"""Port calls never run on the event loop, and the user is loaded once per request (core Task 23).

The Postgres adapters block, so every route, dependency, middleware step and error page that
reaches a data port must run it in a worker thread.
"""

import asyncio
import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import User
from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import DemoUsers
from tutor.web.deps import current_user, require_admin
from tutor.web.memory import FakeGoogle, FixedClock, MemoryBackend, memory_deps
from tutor.web.ports import GoogleIdentity
from tutor.web.profile import MemoryProfiles, install_profiles

from .conftest import BASE

pytestmark = pytest.mark.unit

_CSRF = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


class OffLoop:
    """Forwards to the memory backend and records any call made on the event loop thread."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []
        self.on_loop: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                asyncio.get_running_loop()
                self.on_loop.append(name)
            except RuntimeError:
                pass  # a worker thread: no running loop here
            self.calls.append(name)
            return attr(*args, **kwargs)

        return call


@pytest.fixture
def ports(backend: MemoryBackend, demo: DemoUsers) -> OffLoop:
    return OffLoop(backend)


@pytest.fixture
def profile_ports(clock: FixedClock) -> OffLoop:
    return OffLoop(MemoryProfiles(clock))


@pytest.fixture
def threaded_app(
    ports: OffLoop,
    profile_ports: OffLoop,
    clock: FixedClock,
    google: FakeGoogle,
    config: WebConfig,
) -> FastAPI:
    deps = replace(
        memory_deps(ports._inner, clock, google=google),
        users=ports,
        sessions=ports,
        reader=ports,
        glossary=ports,
        account=ports,
        subscriptions=ports,
        settings=ports,
    )
    app = create_app(deps, config)
    install_profiles(app, profile_ports)  # type: ignore[arg-type]

    @app.get("/app/admin-probe", dependencies=[Depends(require_admin)])
    def admin_probe() -> PlainTextResponse:
        return PlainTextResponse("never")

    @app.get("/app/crash-probe", response_class=HTMLResponse)
    def crash_probe(user: Annotated[User, Depends(current_user)]) -> HTMLResponse:
        raise RuntimeError("probe")

    return app


@pytest.fixture
def client(threaded_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(threaded_app, base_url=BASE, follow_redirects=False) as c:
        yield c


def test_every_port_call_runs_off_the_event_loop(
    client: TestClient,
    ports: OffLoop,
    profile_ports: OffLoop,
    demo: DemoUsers,
    google: FakeGoogle,
    clock: FixedClock,
) -> None:
    google.next_identity = GoogleIdentity("g-ana-new", "x@example.com", "X", True)
    start = client.get("/auth/google?next=/app/")
    assert start.status_code == 302
    assert client.get(start.headers["location"].replace(BASE, "")).status_code == 303
    client.cookies.clear()
    assert client.post("/auth/test-login", data={"user_id": str(demo.ana)}).status_code == 303
    page = client.get("/app/connect")
    assert page.status_code == 200
    match = _CSRF.search(page.text)
    assert match
    csrf = match.group(1)
    assert client.post("/app/lang", data={"lang": "en", "csrf_token": csrf}).status_code == 303
    dismissed = client.post("/app/install/dismiss", data={"csrf_token": csrf})
    assert dismissed.status_code == 303
    saved = client.post(
        "/app/profile",
        data={
            "csrf_token": csrf,
            "self_level": "B1",
            "domains": ["it"],
            "use_cases": ["standup"],
            "minutes_per_day": "20",
            "days_per_week": "3",
            "target_level": "B2",
            "timezone": "America/Mexico_City",
        },
    )
    assert saved.status_code == 303
    assert client.get("/app/profile").status_code == 200
    assert client.get("/app/admin-probe").status_code == 404  # error page rendered for a user
    assert client.get("/app/crash-probe").status_code == 500  # ErrorGuard page for a user
    clock.advance(timedelta(minutes=6))
    assert client.get("/app/connect").status_code == 200  # the session is touched
    assert client.post("/auth/logout", data={"csrf_token": csrf}).status_code == 303
    called = set(ports.calls)
    assert {
        "sign_in",
        "find_user",
        "load_session",
        "create_session",
        "delete_session",
        "touch_session",
        "usage",
        "account",
        "set_preferences",
        "dismiss_install_prompt",
        "subscription",
    } <= called
    assert ports.on_loop == []
    assert {"view", "save"} <= set(profile_ports.calls)  # callback, Perfil GET and POST
    assert profile_ports.on_loop == []


def test_an_expired_session_is_deleted_off_the_loop(
    client: TestClient, ports: OffLoop, demo: DemoUsers, clock: FixedClock
) -> None:
    assert client.post("/auth/test-login", data={"user_id": str(demo.ana)}).status_code == 303
    clock.advance(timedelta(days=15))
    assert client.get("/app/connect").status_code == 303
    assert "delete_session" in ports.calls
    assert ports.on_loop == []


def test_the_user_is_loaded_once_per_request(
    client: TestClient, ports: OffLoop, demo: DemoUsers
) -> None:
    assert client.post("/auth/test-login", data={"user_id": str(demo.ana)}).status_code == 303
    page = client.get("/app/connect")
    match = _CSRF.search(page.text)
    assert match
    ports.calls.clear()
    response = client.post("/app/lang", data={"lang": "en", "csrf_token": match.group(1)})
    assert response.status_code == 303
    assert ports.calls.count("find_user") == 1  # require_csrf and current_user share it
