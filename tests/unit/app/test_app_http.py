import asyncio
import json
import logging
import re
import zoneinfo
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy
from cryptography.fernet import Fernet
from key_value.aio.stores.memory import MemoryStore
from mcp_lesson import NOW

from tutor.app import BodySizeGuard, McpRouteLog, Message, PathDispatch, build_app
from tutor.auth.mcp_auth import build_google_provider
from tutor.mcp.server import build_mcp
from tutor.services.context import Services
from tutor.services.memory import MemoryIdentity, memory_uow
from tutor.services.memory import MemoryStore as ServiceStore
from tutor.settings import Settings

pytestmark = pytest.mark.unit

BASE_URL = "https://tutor.example.com"
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0"},
    },
}


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "TUTOR_BASE_URL": BASE_URL,
            "DATABASE_URL": "sqlite://",
            "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
            "TUTOR_JWT_SIGNING_KEY": "j" * 40,
            "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
            "TUTOR_OAUTH_STORAGE_DIR": str(tmp_path / "oauth"),
            "TUTOR_WEB_SESSION_SECRET": "w" * 40,
        }
    )


def memory_app(tmp_path: Path) -> PathDispatch:
    """The production composition with memory services and a memory OAuth store."""
    store = ServiceStore()
    svc = Services(
        uow=memory_uow(store),
        clock=lambda: NOW,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    auth = build_google_provider(settings(tmp_path), client_storage=MemoryStore())
    mcp = build_mcp(svc, MemoryIdentity(store), auth=auth)
    return PathDispatch(McpRouteLog(BodySizeGuard(mcp.http_app(path="/mcp"))), web_app=None)


@asynccontextmanager
async def serving(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """Run the ASGI lifespan through the dispatcher, as uvicorn does, then serve requests."""
    inbox: asyncio.Queue[Message] = asyncio.Queue()
    outbox: asyncio.Queue[Message] = asyncio.Queue()
    task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, inbox.get, outbox.put)
    )
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            yield client
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        assert (await outbox.get())["type"] == "lifespan.shutdown.complete"
        await task


@pytest.mark.asyncio
async def test_mcp_without_token_is_401_pointing_to_the_resource_metadata(
    tmp_path: Path,
) -> None:
    async with serving(memory_app(tmp_path)) as client:
        denied = await client.post("/mcp", json=INIT, headers=HEADERS)
        assert denied.status_code == 401
        match = re.search(r'resource_metadata="([^"]+)"', denied.headers["www-authenticate"])
        assert match
        assert match.group(1) == f"{BASE_URL}/.well-known/oauth-protected-resource/mcp"
        metadata = (await client.get(match.group(1))).json()
    assert metadata["resource"] == f"{BASE_URL}/mcp"
    assert metadata["authorization_servers"]


@pytest.mark.asyncio
async def test_authorization_server_metadata_is_at_the_root(tmp_path: Path) -> None:
    async with serving(memory_app(tmp_path)) as client:
        response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    body = response.json()
    assert body["authorization_endpoint"] == f"{BASE_URL}/authorize"
    assert body["code_challenge_methods_supported"] == ["S256"]


@pytest.mark.asyncio
async def test_proxy_callback_is_routed_to_mcp(tmp_path: Path) -> None:
    async with serving(memory_app(tmp_path)) as client:
        callback = await client.get("/oauth/callback")
        web = await client.get("/auth/callback")
    assert callback.status_code == 400  # the proxy handler, rejecting a missing code
    assert (web.status_code, web.json()) == (404, {"error": "not_found"})


@pytest.mark.asyncio
async def test_web_paths_are_404_until_the_web_app_is_mounted(tmp_path: Path) -> None:
    async with serving(memory_app(tmp_path)) as client:
        response = await client.get("/app/")
    assert (response.status_code, response.json()) == (404, {"error": "not_found"})


@pytest.mark.asyncio
async def test_oversized_mcp_post_is_413(tmp_path: Path) -> None:
    async with serving(memory_app(tmp_path)) as client:
        response = await client.post("/mcp", content=b"x" * 70_000, headers=HEADERS)
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_build_app_wires_auth_and_dispatch(tmp_path: Path) -> None:
    app = build_app(settings(tmp_path), engine=sqlalchemy.create_engine("sqlite://"))
    async with serving(app) as client:
        denied = await client.post("/mcp", json=INIT, headers=HEADERS)
        missing = await client.get("/app/")
    assert denied.status_code == 401
    assert missing.status_code == 404
    assert (tmp_path / "oauth").is_dir()


@pytest.mark.asyncio
async def test_oauth_routes_log_one_line_without_the_query(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="tutor.http"):
        async with serving(memory_app(tmp_path)) as client:
            await client.get("/oauth/callback?code=SECRET-CODE&state=STATE-VALUE")
            await client.post("/mcp", json=INIT, headers=HEADERS)
    [record] = [r for r in caplog.records if r.name == "tutor.http"]
    line = json.loads(record.getMessage())
    assert set(line) == {"event", "route", "method", "status", "latency_ms"}
    assert (line["event"], line["route"], line["method"], line["status"]) == (
        "mcp_http_route",
        "/oauth/callback",
        "GET",
        400,
    )
    assert isinstance(line["latency_ms"], float)
    for secret in ("SECRET-CODE", "STATE-VALUE", "code="):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_a_failing_route_is_logged_as_500_and_re_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    scope = {"type": "http", "method": "GET", "path": "/token", "query_string": b"code=SECRET"}
    with caplog.at_level(logging.INFO, logger="tutor.http"), pytest.raises(RuntimeError):
        await McpRouteLog(broken)(scope, None, None)  # type: ignore[arg-type]
    assert json.loads(caplog.records[0].getMessage())["status"] == 500
    assert "SECRET" not in caplog.text
    assert "boom" not in caplog.text
