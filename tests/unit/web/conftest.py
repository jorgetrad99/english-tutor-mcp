import re
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import DemoUsers, seed_demo
from tutor.web.memory import FakeBilling, FakeGoogle, FixedClock, MemoryBackend, memory_deps

NOW = datetime(2027, 1, 12, 18, 0, tzinfo=UTC)  # 12:00 in Mexico City, a Tuesday
TODAY = date(2027, 1, 12)
BASE = "https://localhost"


@pytest.fixture
def backend() -> MemoryBackend:
    return MemoryBackend()


@pytest.fixture
def demo(backend: MemoryBackend) -> DemoUsers:
    return seed_demo(backend, TODAY)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def billing() -> FakeBilling:
    return FakeBilling()


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def config() -> WebConfig:
    return WebConfig(
        env="test",
        base_url=BASE,
        mcp_url=f"{BASE}/mcp",
        support_email="soporte@example.test",
        test_login=True,
    )


@pytest.fixture
def app(
    backend: MemoryBackend,
    demo: DemoUsers,
    clock: FixedClock,
    billing: FakeBilling,
    google: FakeGoogle,
    config: WebConfig,
) -> FastAPI:
    return create_app(memory_deps(backend, clock, billing=billing, google=google), config)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        yield c


@pytest.fixture
def login(app: FastAPI) -> Iterator[Callable[[UUID], TestClient]]:
    clients: list[TestClient] = []

    def _login(user_id: UUID) -> TestClient:
        c = TestClient(app, base_url=BASE, follow_redirects=False)
        response = c.post("/auth/test-login", data={"user_id": str(user_id)})
        assert response.status_code == 303, response.text
        clients.append(c)
        return c

    yield _login
    for c in clients:
        c.close()


_CSRF = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


def csrf_of(client: TestClient) -> str:
    match = _CSRF.search(client.get("/app/account").text)
    assert match, "csrf meta tag missing"
    return match.group(1)
