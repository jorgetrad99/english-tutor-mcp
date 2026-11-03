"""Every route, checked for login, CSRF and per-user isolation (spec 14.1).

New routes are covered automatically; a route with an unknown path parameter fails
until an isolation case is added below.
"""

import re
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from subject_web_fixtures import SubjectWorld

from tutor.domain.dashboard.types import ConnectedClient
from tutor.services.notes import list_notes
from tutor.services.templates import list_templates
from tutor.web.config import WebConfig
from tutor.web.demo import DemoUsers
from tutor.web.deps import assert_csrf_everywhere, require_csrf
from tutor.web.memory import MemoryBackend
from tutor.web.routing import iter_api_routes
from tutor.web.subjects import MemorySubjects

from .conftest import csrf_of

PUBLIC = {
    "/",
    "/login",
    "/privacy",
    "/terms",
    "/offline",
    "/manifest.webmanifest",
    "/sw.js",
    "/auth/google",
    "/auth/callback",
    "/auth/logout",
    "/auth/test-login",
}
PROTECTED_PREFIXES = ("/app",)


def under_app(path: str) -> bool:
    """Exact /app prefix: "/application" is not a dashboard page."""
    return path == "/app" or path.startswith("/app/")


CSRF_EXEMPT = {("POST", "/auth/test-login")}
# Core loop v0 (plan ruling 11): postponed pages are not registered at all.
POSTPONED_PREFIXES = (
    "/pricing",
    "/webhooks",
    "/billing",
    "/admin",
    "/app/plan",
    "/app/progress",
    "/app/reports",
    "/app/account",
)
NOT_USER_OWNED = {"key"}  # admin setting keys are global and guarded by require_admin
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# The subjects writes (slice D Task 4): the sweeps below must reach each of them.
SUBJECT_WRITES = {
    ("POST", "/app/subjects/{subject_id}/pdf"),
    ("POST", "/app/subjects/{subject_id}/text"),
}
PARAM = re.compile(r"{(\w+)(?::\w+)?}")

Login = Callable[[UUID], TestClient]


def _methods(route: APIRoute) -> set[str]:
    return set(route.methods or ())


def _routes(app: FastAPI) -> list[APIRoute]:
    return iter_api_routes(app)


def _fill(path: str, values: dict[str, str]) -> str:
    return PARAM.sub(lambda m: values.get(m.group(1), str(uuid4())), path)


def _other_users_ids(
    backend: MemoryBackend, demo: DemoUsers, world: SubjectWorld
) -> dict[str, str]:
    """Ids that belong to Beto only; Ana must get 404 for each."""
    backend.clients[demo.beto].append(ConnectedClient("beto-only", "Claude Code", None))
    return {
        "session_id": str(backend.session_rows[demo.beto][0].summary.id),
        "item_id": str(backend.glossaries[demo.beto][0].id),
        "client_id": "beto-only",
        # Subjects (slice D Review Focus 3): Beto's seeded rows.
        "subject_id": str(world.subject_id),
        "note_id": str(world.note_id),
        "instance_id": str(world.instance_id),
        "template_id": str(world.template_id),
    }


def test_every_route_is_classified(app: FastAPI) -> None:
    routes = _routes(app)
    assert len(routes) >= 20 and {r.path for r in routes} >= PUBLIC
    for route in routes:
        public = route.path in PUBLIC
        protected = under_app(route.path)
        assert public != protected, (
            f"classify {route.path}: list it in PUBLIC or move it under /app"
        )


def test_protected_pages_redirect_anonymous_visitors(app: FastAPI, client: TestClient) -> None:
    checked = 0
    for route in _routes(app):
        if "GET" not in _methods(route) or not under_app(route.path):
            continue
        path = _fill(route.path, {})
        page = client.get(path)
        assert page.status_code == 303, f"GET {path} -> {page.status_code}"
        assert page.headers["location"].startswith("/login?next="), path
        partial = client.get(path, headers={"hx-request": "true"})
        assert partial.status_code == 401, f"HTMX GET {path} -> {partial.status_code}"
        assert partial.headers["hx-redirect"].startswith("/login?next="), path
        checked += 1
    assert checked >= 10


def test_unsafe_methods_need_the_csrf_token(app: FastAPI, login: Login, demo: DemoUsers) -> None:
    c = login(demo.ana)
    checked = 0
    swept: set[tuple[str, str]] = set()
    for route in _routes(app):
        for method in sorted(_methods(route) & UNSAFE_METHODS):
            if (method, route.path) in CSRF_EXEMPT:
                continue
            response = c.request(method, _fill(route.path, {}))
            assert response.status_code == 403, (
                f"{method} {route.path} answered {response.status_code} without a CSRF token"
            )
            checked += 1
            swept.add((method, route.path))
    assert checked >= 5
    assert swept >= SUBJECT_WRITES


def test_another_users_ids_are_not_found(
    app: FastAPI,
    login: Login,
    demo: DemoUsers,
    backend: MemoryBackend,
    beto_world: SubjectWorld,
    subjects: MemorySubjects,
) -> None:
    beto = _other_users_ids(backend, demo, beto_world)
    c = login(demo.ana)
    token = csrf_of(c)
    form = {
        "csrf_token": token,
        "meaning": "x",
        "context_sentence": "Una frase.",
        "value": "1",
        "title": "Un título",
        "text": "Un texto.",
    }
    notes_before = list_notes(subjects.svc, demo.beto, beto_world.subject_id)
    checked = 0
    swept: set[tuple[str, str]] = set()
    for route in _routes(app):
        names = PARAM.findall(route.path)
        if not names:
            continue
        unknown = [n for n in names if n not in beto and n not in NOT_USER_OWNED]
        assert not unknown, (
            f"add an isolation case for {route.path} (parameters {unknown}) "
            "in _other_users_ids in test_web_sweep.py"
        )
        if any(n in NOT_USER_OWNED for n in names):
            continue
        path = _fill(route.path, beto)
        for method in sorted(_methods(route) - {"HEAD", "OPTIONS"}):
            data = None if method == "GET" else form
            response = c.request(method, path, data=data)
            assert response.status_code == 404, (
                f"{method} {path} answered {response.status_code} for another learner's id"
            )
            checked += 1
            swept.add((method, route.path))
    assert backend.clients[demo.beto][-1].id == "beto-only"
    assert checked >= 4
    assert swept >= SUBJECT_WRITES
    # Nothing of Beto's changed: no note added, his template still active.
    assert list_notes(subjects.svc, demo.beto, beto_world.subject_id) == notes_before
    active = list_templates(subjects.svc, demo.beto, beto_world.subject_id).templates
    assert beto_world.template_id in [t.template_id for t in active]


def test_own_ids_still_work(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    c = login(demo.ana)
    own_session = backend.session_rows[demo.ana][0].summary.id
    assert c.get(f"/app/sessions/{own_session}").status_code == 200


def test_beto_sees_his_own_subject_rows(
    login: Login, demo: DemoUsers, beto_world: SubjectWorld
) -> None:
    c = login(demo.beto)
    assert c.get(f"/app/subjects/{beto_world.subject_id}").status_code == 200
    assert c.get(f"/app/notes/{beto_world.note_id}").status_code == 200


@pytest.mark.parametrize("path", ["/app/subjects/{}", "/app/notes/{}"])
@pytest.mark.parametrize("raw", ["not-a-uuid", "123", "%27%3B--", "0" * 40])
def test_malformed_subject_ids_are_not_found(
    login: Login, demo: DemoUsers, path: str, raw: str
) -> None:
    # Review Focus 3: a malformed id is 404, never 422.
    assert login(demo.ana).get(path.format(raw)).status_code == 404


def test_postponed_pages_are_not_registered(app: FastAPI) -> None:
    paths = [route.path for route in _routes(app)]
    assert len(paths) >= 20 and set(paths) >= PUBLIC
    assert [p for p in paths if p.startswith(POSTPONED_PREFIXES)] == []


def bare_app() -> FastAPI:
    return FastAPI(openapi_url=None, docs_url=None, redoc_url=None)


def test_csrf_guard_sees_routes_of_included_routers(config: WebConfig) -> None:
    router = APIRouter()

    @router.post("/app/unguarded")
    async def unguarded() -> None:  # pragma: no cover - never called
        return None

    app = bare_app()
    app.include_router(router)
    with pytest.raises(RuntimeError, match="/app/unguarded"):
        assert_csrf_everywhere(app, config)


def test_csrf_guard_accepts_guarded_routes_of_included_routers(config: WebConfig) -> None:
    router = APIRouter()

    @router.post("/app/guarded", dependencies=[Depends(require_csrf)])
    async def guarded() -> None:  # pragma: no cover - never called
        return None

    app = bare_app()
    app.include_router(router)
    assert_csrf_everywhere(app, config)


def test_route_walker_refuses_prefixed_includes() -> None:
    app = bare_app()
    app.include_router(APIRouter(), prefix="/x")
    app.include_router(_router_with_route(), prefix="/x")
    with pytest.raises(RuntimeError, match="prefix"):
        iter_api_routes(app)


def _router_with_route() -> APIRouter:
    router = APIRouter()

    @router.get("/a")
    async def a() -> None:  # pragma: no cover - never called
        return None

    return router


def test_csrf_guard_sees_unguarded_posts_in_nested_includes(config: WebConfig) -> None:
    inner = APIRouter()

    @inner.post("/app/deep")
    async def deep() -> None:  # pragma: no cover - never called
        return None

    outer = APIRouter()
    outer.include_router(inner)
    app = bare_app()
    app.include_router(outer)
    assert [r.path for r in iter_api_routes(app)] == ["/app/deep"]
    with pytest.raises(RuntimeError, match="/app/deep"):
        assert_csrf_everywhere(app, config)


def test_route_walker_refuses_includes_with_dependencies(config: WebConfig) -> None:
    app = bare_app()
    app.include_router(_router_with_route(), dependencies=[Depends(require_csrf)])
    with pytest.raises(RuntimeError, match="dependencies"):
        iter_api_routes(app)
    with pytest.raises(RuntimeError, match="dependencies"):
        assert_csrf_everywhere(app, config)


def test_route_walker_refuses_a_parent_router_with_a_prefix() -> None:
    parent = APIRouter(prefix="/p")
    parent.include_router(_router_with_route())
    app = bare_app()
    app.include_router(parent)
    with pytest.raises(RuntimeError, match="prefix"):
        iter_api_routes(app)


def test_route_walker_refuses_plain_starlette_routes(config: WebConfig) -> None:
    async def handler(request: Request) -> Response:  # pragma: no cover - never called
        return Response()

    app = bare_app()
    app.add_route("/app/raw", handler, methods=["POST"])
    with pytest.raises(RuntimeError, match="unsupported route node"):
        iter_api_routes(app)
    with pytest.raises(RuntimeError, match="unsupported route node"):
        assert_csrf_everywhere(app, config)


def test_route_walker_allows_only_the_static_mount(tmp_path: Path) -> None:
    app = bare_app()
    app.include_router(_router_with_route())
    app.mount("/static", StaticFiles(directory=tmp_path), name="static")
    assert [r.path for r in iter_api_routes(app)] == ["/a"]
    other = bare_app()
    other.mount("/files", StaticFiles(directory=tmp_path), name="files")
    with pytest.raises(RuntimeError, match="unsupported route node"):
        iter_api_routes(other)
    sub = bare_app()
    sub.mount("/static", bare_app())
    with pytest.raises(RuntimeError, match="unsupported route node"):
        iter_api_routes(sub)


def test_route_walker_refuses_low_priority_routes() -> None:
    app = bare_app()
    app.router._low_priority_routes.append(object())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="low-priority"):
        iter_api_routes(app)


def test_app_prefix_is_exact() -> None:
    assert under_app("/app") and under_app("/app/") and under_app("/app/profile")
    assert not under_app("/application") and not under_app("/apple")


def test_csrf_prefix_is_exact_so_application_is_not_an_app_page(config: WebConfig) -> None:
    from tutor.web.deps import APP_ROUTER_DEPS

    app = bare_app()

    @app.post("/application", dependencies=APP_ROUTER_DEPS)
    async def probe() -> None:  # pragma: no cover - never reached
        return None

    # create_app's deps are not installed; require_csrf must reject on CSRF, not on login.
    from fastapi.testclient import TestClient as _Client

    assert _Client(app).post("/application").status_code == 403


def test_walker_notices_missing_fastapi_internals(monkeypatch: pytest.MonkeyPatch) -> None:
    app = bare_app()
    monkeypatch.delattr(type(app.router), "_low_priority_routes", raising=False)
    monkeypatch.delattr(app.router, "_low_priority_routes", raising=False)
    with pytest.raises(RuntimeError, match="internals changed"):
        iter_api_routes(app)
