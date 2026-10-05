"""Every route, checked for login, CSRF and per-user isolation (spec 14.1).

New routes are covered automatically; a route with an unknown path parameter fails
until an isolation case is added below.
"""

import re
from collections.abc import Callable, Iterable
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute

from tutor.domain.dashboard.types import ConnectedClient
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

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
)
NOT_USER_OWNED = {"key"}  # admin setting keys are global and guarded by require_admin
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PARAM = re.compile(r"{(\w+)(?::\w+)?}")

Login = Callable[[UUID], TestClient]


def _flatten(routes: Iterable[BaseRoute]) -> list[APIRoute]:
    """FastAPI 0.142 keeps included routers as `_IncludedRouter` nodes, not APIRoutes."""
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif (included := getattr(route, "original_router", None)) is not None:
            assert not route.include_context.prefix, "prefixed routers: extend _flatten"  # type: ignore[attr-defined]
            found.extend(_flatten(included.routes))
    return found


def _methods(route: APIRoute) -> set[str]:
    return set(route.methods or ())


def _routes(app: FastAPI) -> list[APIRoute]:
    return _flatten(app.routes)


def _fill(path: str, values: dict[str, str]) -> str:
    return PARAM.sub(lambda m: values.get(m.group(1), str(uuid4())), path)


def _other_users_ids(backend: MemoryBackend, demo: DemoUsers) -> dict[str, str]:
    """Ids that belong to Beto only; Ana must get 404 for each."""
    backend.clients[demo.beto].append(ConnectedClient("beto-only", "Claude Code", None))
    return {
        "session_id": str(backend.session_rows[demo.beto][0].summary.id),
        "item_id": str(backend.glossaries[demo.beto][0].id),
        "client_id": "beto-only",
    }


def test_every_route_is_classified(app: FastAPI) -> None:
    for route in _routes(app):
        public = route.path in PUBLIC
        protected = route.path.startswith(PROTECTED_PREFIXES)
        assert public != protected, (
            f"classify {route.path}: list it in PUBLIC or move it under /app"
        )


def test_protected_pages_redirect_anonymous_visitors(app: FastAPI, client: TestClient) -> None:
    checked = 0
    for route in _routes(app):
        if "GET" not in _methods(route) or not route.path.startswith(PROTECTED_PREFIXES):
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
    for route in _routes(app):
        for method in sorted(_methods(route) & UNSAFE_METHODS):
            if (method, route.path) in CSRF_EXEMPT:
                continue
            response = c.request(method, _fill(route.path, {}))
            assert response.status_code == 403, (
                f"{method} {route.path} answered {response.status_code} without a CSRF token"
            )
            checked += 1
    assert checked >= 4


def test_another_users_ids_are_not_found(
    app: FastAPI, login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    beto = _other_users_ids(backend, demo)
    c = login(demo.ana)
    token = csrf_of(c)
    form = {"csrf_token": token, "meaning": "x", "context_sentence": "Una frase.", "value": "1"}
    checked = 0
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
    assert backend.clients[demo.beto][-1].id == "beto-only"
    assert checked >= 4


def test_own_ids_still_work(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    c = login(demo.ana)
    own_session = backend.session_rows[demo.ana][0].summary.id
    assert c.get(f"/app/sessions/{own_session}").status_code == 200


def test_postponed_pages_are_not_registered(app: FastAPI) -> None:
    paths = [route.path for route in _routes(app)]
    assert [p for p in paths if p.startswith(POSTPONED_PREFIXES)] == []
