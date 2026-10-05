from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang
from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import DemoUsers
from tutor.web.memory import FakeGoogle, FixedClock, MemoryBackend, memory_deps
from tutor.web.ports import GoogleIdentity, WebSession
from tutor.web.sessions import COOKIE, hash_token

from .conftest import BASE, NOW, csrf_of

HOSTILE = ["//evil.example", r"/\evil.example", "/\t/evil.example", "https://evil.example"]


def _callback(start_location: str) -> str:
    return start_location.replace(BASE, "")


def test_google_round_trip_creates_user_and_lands_on_next(
    client: TestClient, google: FakeGoogle, backend: MemoryBackend
) -> None:
    google.next_identity = GoogleIdentity("g-123", "carla@example.com", "Carla", True)
    start = client.get("/auth/google?next=/app/glossary")
    assert start.status_code == 302
    callback = client.get(_callback(start.headers["location"]))
    assert callback.status_code == 303 and callback.headers["location"] == "/app/glossary"
    assert any(u.display_name == "Carla" for u in backend.users.values())
    assert COOKIE in client.cookies


def test_user_is_linked_by_sub_not_email(
    client: TestClient, google: FakeGoogle, backend: MemoryBackend, demo: DemoUsers
) -> None:
    # Same email as Ana, different sub: must be a new account, never Ana's.
    google.next_identity = GoogleIdentity("g-other", "ana@example.com", "Impostor", True)
    start = client.get("/auth/google")
    client.get(_callback(start.headers["location"]))
    session = next(iter(backend.web_sessions.values()))
    assert session.user_id is not None and session.user_id != demo.ana


def test_failed_google_login_shows_error(
    client: TestClient, google: FakeGoogle, backend: MemoryBackend
) -> None:
    google.next_identity = None
    client.get("/auth/google")
    response = client.get("/auth/callback?code=x&state=y")
    assert response.status_code == 400
    assert "No pudimos iniciar sesión con Google" in response.text
    assert all(s.user_id is None for s in backend.web_sessions.values())


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_next_is_replaced_on_start_and_again_on_callback(
    client: TestClient, google: FakeGoogle, backend: MemoryBackend, hostile: str
) -> None:
    google.next_identity = GoogleIdentity("g-9", "x@example.com", "X", True)
    start = client.get("/auth/google", params={"next": hostile})
    stored = next(iter(backend.web_sessions.values())).data
    assert stored["login_next"] == "/app/"
    # Defence in depth: even a poisoned stored value is validated before redirecting.
    stored["login_next"] = hostile
    callback = client.get(_callback(start.headers["location"]))
    assert callback.status_code == 303 and callback.headers["location"] == "/app/"


def test_deleted_user_cannot_sign_in(
    client: TestClient, google: FakeGoogle, backend: MemoryBackend, demo: DemoUsers
) -> None:
    backend.request_deletion(demo.ana, NOW)
    google.next_identity = GoogleIdentity("demo-ana", "ana@example.com", "Ana", True)
    start = client.get("/auth/google")
    response = client.get(_callback(start.headers["location"]))
    assert response.status_code == 200
    assert "Tu cuenta se está eliminando" in response.text
    assert COOKIE not in client.cookies


def test_logout_deletes_session_clears_cookie_and_site_data(
    login: Callable[[UUID], TestClient], demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    assert len(backend.web_sessions) == 1
    response = c.post("/auth/logout", data={"csrf_token": csrf_of(c)})
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert response.headers["clear-site-data"] == '"cache", "cookies", "storage"'
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert backend.web_sessions == {}
    assert c.get("/app/account").status_code == 303


def test_logout_requires_post_and_csrf(
    login: Callable[[UUID], TestClient], demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    assert c.get("/auth/logout").status_code == 405
    assert c.post("/auth/logout").status_code == 403
    assert c.post("/auth/logout", data={"csrf_token": "wrong"}).status_code == 403
    assert len(backend.web_sessions) == 1


def test_language_switch_saves_preference(
    login: Callable[[UUID], TestClient], demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    response = c.post(
        "/app/lang", data={"csrf_token": csrf_of(c), "lang": "en", "back": "/app/glossary"}
    )
    assert response.status_code == 303 and response.headers["location"] == "/app/glossary"
    assert backend.users[demo.ana].lang is Lang.EN


@pytest.mark.parametrize("hostile", HOSTILE)
def test_language_switch_ignores_hostile_back(
    login: Callable[[UUID], TestClient], demo: DemoUsers, hostile: str
) -> None:
    c = login(demo.ana)
    response = c.post("/app/lang", data={"csrf_token": csrf_of(c), "lang": "en", "back": hostile})
    assert response.status_code == 303 and response.headers["location"] == "/app/"


def test_language_switch_rejects_unknown_language_and_missing_csrf(
    login: Callable[[UUID], TestClient], demo: DemoUsers
) -> None:
    c = login(demo.ana)
    assert c.post("/app/lang", data={"csrf_token": csrf_of(c), "lang": "fr"}).status_code == 422
    assert c.post("/app/lang", data={"lang": "en"}).status_code == 403


def test_test_login_page_lists_demo_users(client: TestClient) -> None:
    page = client.get("/auth/test-login")
    assert page.status_code == 200 and "Ana" in page.text


def test_test_login_does_not_exist_outside_test_env(
    backend: MemoryBackend, demo: DemoUsers, clock: FixedClock, config: WebConfig
) -> None:
    prod = create_app(memory_deps(backend, clock), replace(config, env="prod", test_login=False))
    assert isinstance(prod, FastAPI)
    with TestClient(prod, base_url=BASE, follow_redirects=False) as c:
        assert c.get("/auth/test-login").status_code == 404
        assert c.post("/auth/test-login", data={"user_id": str(demo.ana)}).status_code in (403, 404)
        assert COOKIE not in c.cookies


def _assert_logged_out_response(response: Any) -> None:
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert response.headers["clear-site-data"] == '"cache", "cookies", "storage"'
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=;")
    for attr in ("Max-Age=0", "Path=/", "Secure", "HttpOnly", "SameSite=Lax"):
        assert attr in cookie


def test_logout_without_cookie_needs_no_csrf_and_clears_the_cookie(client: TestClient) -> None:
    _assert_logged_out_response(client.post("/auth/logout"))


def test_logout_with_purged_or_expired_session_clears_the_cookie(
    client: TestClient, backend: MemoryBackend, demo: DemoUsers
) -> None:
    client.cookies.set(COOKIE, "never-stored")  # purged: no row
    _assert_logged_out_response(client.post("/auth/logout"))
    token = "e" * 43
    old = NOW - timedelta(days=20)
    backend.create_session(WebSession(hash_token(token), demo.ana, "c", old, old))
    client.cookies.set(COOKIE, token)  # idle for 20 days: expired
    _assert_logged_out_response(client.post("/auth/logout"))
    assert hash_token(token) not in backend.web_sessions


def test_logout_with_valid_session_and_bad_csrf_is_forbidden(
    login: Callable[[UUID], TestClient], demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    assert c.post("/auth/logout", data={"csrf_token": "nope"}).status_code == 403
    assert len(backend.web_sessions) == 1
    _assert_logged_out_response(c.post("/auth/logout", data={"csrf_token": csrf_of(c)}))
    assert backend.web_sessions == {}
