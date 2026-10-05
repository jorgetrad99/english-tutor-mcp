from dataclasses import replace
from datetime import timedelta
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Role, User
from tutor.web.config import WebConfig
from tutor.web.deps import (
    APP_ROUTER_DEPS,
    assert_csrf_everywhere,
    current_user,
    login_redirect_target,
    require_admin,
)
from tutor.web.memory import FixedClock, MemoryBackend
from tutor.web.ports import WebSession
from tutor.web.sessions import COOKIE, hash_token

from .conftest import BASE


def _probe_router() -> APIRouter:
    router = APIRouter(dependencies=APP_ROUTER_DEPS)

    @router.get("/app/probe")
    async def probe(user: Annotated[User, Depends(current_user)]) -> PlainTextResponse:
        return PlainTextResponse(user.display_name)

    @router.post("/app/probe")
    async def probe_post(user: Annotated[User, Depends(current_user)]) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @router.post("/probe/login/{user_id}")
    async def probe_login(user_id: UUID, request: Request) -> PlainTextResponse:
        request.state.web.login(user_id)
        return PlainTextResponse("in")

    @router.get("/probe/anon-write")
    async def anon_write(request: Request) -> PlainTextResponse:
        request.session["_state_google_x"] = {"n": 1}
        return PlainTextResponse("stored")

    return router


def _client(app: FastAPI) -> TestClient:
    app.include_router(_probe_router())
    return TestClient(app, base_url=BASE, follow_redirects=False)


def test_anonymous_request_sets_no_cookie(app: FastAPI) -> None:
    c = _client(app)
    response = c.get("/login")
    assert COOKIE not in response.cookies


def test_anonymous_session_data_creates_one_cookie(app: FastAPI) -> None:
    c = _client(app)
    response = c.get("/probe/anon-write")
    header = response.headers["set-cookie"]
    assert header.startswith(f"{COOKIE}=")
    for attr in ("Path=/", "Secure", "HttpOnly", "SameSite=lax"):
        assert attr.lower() in header.lower()
    assert "Max-Age=600" in header  # pre-login sessions are short-lived


def test_unauthenticated_page_redirects_and_htmx_gets_401(app: FastAPI) -> None:
    c = _client(app)
    page = c.get("/app/probe")
    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=%2Fapp%2Fprobe"
    partial = c.get("/app/probe", headers={"hx-request": "true"})
    assert partial.status_code == 401
    assert partial.headers["hx-redirect"] == "/login?next=%2Fapp%2Fprobe"


def test_login_redirect_target_is_always_same_site() -> None:
    for hostile in ("//evil.example", "https://evil.example", "//[x", "/a\b", "/a\x7fb"):
        assert login_redirect_target(hostile) == "/login?next=%2Fapp%2F"
    assert login_redirect_target("/app/glossary?q=x") == "/login?next=%2Fapp%2Fglossary%3Fq%3Dx"


def test_session_expires_after_idle_limit(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    c = _client(app)
    token = "t" * 43
    ana = next(u for u in backend.users.values() if u.display_name == "Ana")
    backend.create_session(WebSession(hash_token(token), ana.id, "csrf", clock(), clock()))
    c.cookies.set(COOKIE, token)
    assert c.get("/app/probe").text == "Ana"
    clock.advance(timedelta(days=15))
    assert c.get("/app/probe").status_code == 303
    assert backend.load_session(hash_token(token)) is None


def test_absolute_lifetime_even_when_active(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    c = _client(app)
    token = "u" * 43
    ana = next(u for u in backend.users.values() if u.display_name == "Ana")
    backend.create_session(WebSession(hash_token(token), ana.id, "csrf", clock(), clock()))
    c.cookies.set(COOKIE, token)
    for _ in range(4):
        clock.advance(timedelta(days=8))
        response = c.get("/app/probe")
    assert response.status_code == 303  # 32 days after creation


def test_post_without_or_with_wrong_csrf_is_403(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    c = _client(app)
    token = "v" * 43
    ana = next(u for u in backend.users.values() if u.display_name == "Ana")
    backend.create_session(WebSession(hash_token(token), ana.id, "right", clock(), clock()))
    c.cookies.set(COOKIE, token)
    assert c.post("/app/probe").status_code == 403
    assert c.post("/app/probe", headers={"x-csrf-token": "wrong"}).status_code == 403
    assert c.post("/app/probe", headers={"x-csrf-token": "right"}).text == "ok"
    assert c.post("/app/probe", data={"csrf_token": "right"}).text == "ok"


def test_login_rotates_the_session_token(
    app: FastAPI, backend: MemoryBackend, demo: object
) -> None:
    c = _client(app)
    c.get("/probe/anon-write")
    before = c.cookies.get(COOKIE)
    assert before is not None
    ana = next(u for u in backend.users.values() if u.display_name == "Ana")
    anon = backend.load_session(hash_token(before))
    assert anon is not None
    response = c.post(f"/probe/login/{ana.id}", headers={"x-csrf-token": anon.csrf_token})
    assert response.status_code == 200
    after = c.cookies.get(COOKIE)
    assert after and after != before
    assert backend.load_session(hash_token(before)) is None
    new = backend.load_session(hash_token(after))
    assert new is not None and new.user_id == ana.id
    assert new.csrf_token != anon.csrf_token
    assert not any(k.startswith("_state_") for k in new.data)
    assert "Max-Age=2592000" in response.headers["set-cookie"]  # full 30-day window after login


def test_anonymous_session_dies_after_ten_minutes_and_is_never_extended(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock
) -> None:
    c = _client(app)
    c.get("/probe/anon-write")
    old = c.cookies.get(COOKIE)
    assert old is not None
    clock.advance(timedelta(minutes=9))
    c.get("/probe/anon-write")  # activity does not extend a pre-login session
    assert backend.load_session(hash_token(old)) is not None
    clock.advance(timedelta(minutes=2))
    c.get("/probe/anon-write")
    assert backend.load_session(hash_token(old)) is None
    fresh = c.cookies.get(COOKIE)
    assert fresh and fresh != old


def _ana(backend: MemoryBackend) -> User:
    return next(u for u in backend.users.values() if u.display_name == "Ana")


def _seed(
    backend: MemoryBackend, clock: FixedClock, user: User, token: str, csrf: str = "csrf"
) -> None:
    backend.create_session(WebSession(hash_token(token), user.id, csrf, clock(), clock()))


def test_session_deleted_in_flight_is_not_revived(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    router = APIRouter()

    @router.get("/probe/slow-logout")
    async def slow_logout(request: Request) -> PlainTextResponse:
        request.session["n"] = 1  # a write forces a touch at response time
        backend.delete_session(request.state.web.session.token_hash)  # logout elsewhere
        return PlainTextResponse("done")

    app.include_router(router)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    token = "w" * 43
    _seed(backend, clock, _ana(backend), token)
    c.cookies.set(COOKIE, token)
    response = c.get("/probe/slow-logout")
    assert response.status_code == 200
    assert backend.load_session(hash_token(token)) is None
    cookie = response.headers["set-cookie"]  # the browser is told to drop the dead token
    assert cookie.startswith(f"{COOKIE}=;") and "Max-Age=0" in cookie


def test_create_never_replaces_and_touch_never_inserts(
    backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    ana = _ana(backend)
    _seed(backend, clock, ana, "x" * 43)
    with pytest.raises(ValueError):
        _seed(backend, clock, ana, "x" * 43)
    assert backend.touch_session("missing", clock(), {}) is False
    assert backend.load_session("missing") is None


def test_non_ascii_csrf_is_403_not_500(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    c = _client(app)
    token = "y" * 43
    _seed(backend, clock, _ana(backend), token, "right")
    c.cookies.set(COOKIE, token)
    accented = {b"x-csrf-token": "r\N{LATIN SMALL LETTER I WITH ACUTE}ght".encode()}
    assert c.post("/app/probe", headers=accented).status_code == 403
    assert c.post("/app/probe", data={"csrf_token": "\N{SNOWMAN}"}).status_code == 403


def test_login_never_carries_another_users_data(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    c = _client(app)
    ana = _ana(backend)
    beto = next(u for u in backend.users.values() if u.display_name == "Beto")
    token = "z" * 43
    backend.create_session(
        WebSession(hash_token(token), ana.id, "a-csrf", clock(), clock(), {"login_next": "/x"})
    )
    c.cookies.set(COOKIE, token)
    response = c.post(f"/probe/login/{beto.id}", headers={"x-csrf-token": "a-csrf"})
    assert response.status_code == 200
    new_token = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    new = backend.load_session(hash_token(new_token))
    assert new is not None and new.user_id == beto.id and new.data == {}
    assert backend.load_session(hash_token(token)) is None


def test_logout_deletes_the_row_and_expires_the_cookie(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    router = APIRouter()

    @router.get("/probe/logout")
    async def logout(request: Request) -> PlainTextResponse:
        request.state.web.logout()
        return PlainTextResponse("bye")

    app.include_router(router)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    token = "l" * 43
    _seed(backend, clock, _ana(backend), token)
    c.cookies.set(COOKIE, token)
    header = c.get("/probe/logout").headers["set-cookie"]
    assert backend.load_session(hash_token(token)) is None
    for attr in ("Max-Age=0", "Path=/", "Secure", "HttpOnly", "SameSite=lax"):
        assert attr.lower() in header.lower()


def test_require_admin_hides_the_page_from_learners(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    router = APIRouter()

    @router.get("/probe/admin")
    async def admin(user: Annotated[User, Depends(require_admin)]) -> PlainTextResponse:
        return PlainTextResponse("admin")

    app.include_router(router)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    boss = next(u for u in backend.users.values() if u.role is Role.ADMIN)
    _seed(backend, clock, _ana(backend), "1" * 43)
    _seed(backend, clock, boss, "2" * 43)
    c.cookies.set(COOKIE, "1" * 43)
    assert c.get("/probe/admin").status_code == 404
    c.cookies.set(COOKIE, "2" * 43)
    assert c.get("/probe/admin").text == "admin"


def test_user_pending_deletion_is_anonymous(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    c = _client(app)
    ana = _ana(backend)
    backend.users[ana.id] = replace(ana, deletion_requested_at=clock())
    _seed(backend, clock, ana, "d" * 43)
    c.cookies.set(COOKIE, "d" * 43)
    assert c.get("/app/probe").status_code == 303


def test_head_and_options_skip_csrf(app: FastAPI) -> None:
    router = APIRouter(dependencies=APP_ROUTER_DEPS)

    @router.api_route("/probe/any", methods=["GET", "HEAD", "OPTIONS"])
    async def anything() -> PlainTextResponse:
        return PlainTextResponse("ok")

    app.include_router(router)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    assert c.head("/probe/any").status_code == 200
    assert c.options("/probe/any").status_code == 200


def test_htmx_post_with_expired_session_gets_login_redirect_not_403(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    c = _client(app)
    _seed(backend, clock, _ana(backend), "e" * 43)
    c.cookies.set(COOKIE, "e" * 43)
    clock.advance(timedelta(days=15))
    response = c.post("/app/probe", headers={"hx-request": "true", "x-csrf-token": "csrf"})
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login?next=%2Fapp%2Fprobe"
    assert c.post("/app/probe").status_code == 303


def test_static_paths_never_touch_sessions(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    c = _client(app)
    _seed(backend, clock, _ana(backend), "s" * 43)
    c.cookies.set(COOKIE, "s" * 43)
    created = clock()
    clock.advance(timedelta(hours=1))
    c.get("/static/css/app.css")
    row = backend.load_session(hash_token("s" * 43))
    assert row is not None and row.last_seen_at == created


def test_purge_expired_removes_idle_absolute_and_anonymous_rows(
    backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    ana = _ana(backend)
    day = timedelta(days=1)
    now = clock()
    rows = [
        WebSession("fresh", ana.id, "c", now, now),
        WebSession("idle", ana.id, "c", now - 20 * day, now - 15 * day),
        WebSession("old", ana.id, "c", now - 31 * day, now),
        WebSession("anon-old", None, "c", now - timedelta(minutes=11), now),
        WebSession("anon-new", None, "c", now - timedelta(minutes=5), now),
    ]
    for row in rows:
        backend.create_session(row)
    removed = backend.purge_expired(
        now, idle=14 * day, absolute=30 * day, anonymous=timedelta(minutes=10)
    )
    assert removed == 3
    assert set(backend.web_sessions) == {"fresh", "anon-new"}


def test_unprotected_unsafe_route_is_rejected_at_startup(config: WebConfig) -> None:
    bare = FastAPI()

    @bare.post("/oops")
    async def oops() -> PlainTextResponse:
        return PlainTextResponse("x")

    with pytest.raises(RuntimeError, match="/oops"):
        assert_csrf_everywhere(bare, config)
    guarded = FastAPI()
    guarded.include_router(_probe_router())
    assert_csrf_everywhere(guarded, config)  # protected routes pass


def test_csrf_exemption_exists_only_with_test_login(config: WebConfig) -> None:
    def build(test_login: bool) -> FastAPI:
        bare = FastAPI()

        @bare.post("/auth/test-login")
        async def login_route() -> PlainTextResponse:
            return PlainTextResponse("x")

        assert_csrf_everywhere(bare, replace(config, test_login=test_login))
        return bare

    build(True)
    with pytest.raises(RuntimeError, match="/auth/test-login"):
        build(False)


def test_stripe_webhook_path_is_not_exempt(config: WebConfig) -> None:
    bare = FastAPI()

    @bare.post("/webhooks/stripe")
    async def hook() -> PlainTextResponse:
        return PlainTextResponse("x")

    with pytest.raises(RuntimeError, match="/webhooks/stripe"):
        assert_csrf_everywhere(bare, config)
