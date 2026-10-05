from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, login_redirect_target
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
    backend.save_session(WebSession(hash_token(token), ana.id, "csrf", clock(), clock()))
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
    backend.save_session(WebSession(hash_token(token), ana.id, "csrf", clock(), clock()))
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
    backend.save_session(WebSession(hash_token(token), ana.id, "right", clock(), clock()))
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
