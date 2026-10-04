import re
from pathlib import Path

import httpx
import pytest
from conftest import read_log_lines
from key_value.aio.stores.memory import MemoryStore

from tutor_spike.server import Settings, build_app, google_auth

HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
PROTOCOL = "2025-06-18"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0"},
    },
}
ENV = {
    "SPIKE_BASE_URL": "https://tutor-spike.example.com",
    "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "not-a-real-secret",
    "SPIKE_JWT_SIGNING_KEY": "k" * 64,
    "SPIKE_TESTERS": "me@gmail.com=author-free",
}


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env({**ENV, "SPIKE_DATA_DIR": str(tmp_path)})


def test_settings_require_every_secret() -> None:
    with pytest.raises(SystemExit, match="GOOGLE_CLIENT_SECRET"):
        Settings.from_env({k: v for k, v in ENV.items() if k != "GOOGLE_CLIENT_SECRET"})


def test_settings_defaults(tmp_path: Path) -> None:
    s = settings(tmp_path)
    assert s.port == 8765
    assert s.display_name == "Learner"
    assert s.testers == {"me@gmail.com": "author-free"}
    assert s.base_url == "https://tutor-spike.example.com"


async def test_app_serves_json_and_logs_initialize_and_tool_call(tmp_path: Path) -> None:
    app = build_app(
        settings(tmp_path), auth=None, server_sha="test", resolve_tester=lambda: "author-free"
    )
    inner = app.app
    transport = httpx.ASGITransport(app=app)
    async with (
        inner.lifespan(inner),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as c,
    ):
        first = await c.post("/mcp", json=INIT, headers=HEADERS)
        assert first.status_code == 200
        assert first.headers["content-type"].startswith("application/json")
        session = {
            **HEADERS,
            "mcp-session-id": first.headers["mcp-session-id"],
            "mcp-protocol-version": PROTOCOL,
        }
        await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=session,
        )
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_profile", "arguments": {}},
        }
        response = await c.post("/mcp", json=call, headers=session)
    assert response.json()["result"]["structuredContent"]["session_id"]
    http = [x for x in read_log_lines(tmp_path) if x["kind"] == "http"]
    assert http[0]["rpc"]["client_info"] == {"name": "test-client", "version": "0"}
    assert any((x["rpc"] or {}).get("tool") == "get_profile" for x in http)
    assert {x["server_sha"] for x in http} == {"test"}


async def test_unauthenticated_call_points_to_metadata_whose_resource_is_the_connector_url(
    tmp_path: Path,
) -> None:
    s = settings(tmp_path)
    app = build_app(s, auth=google_auth(s, client_storage=MemoryStore()), server_sha="test")
    inner = app.app
    transport = httpx.ASGITransport(app=app)
    async with (
        inner.lifespan(inner),
        httpx.AsyncClient(transport=transport, base_url=s.base_url) as c,
    ):
        denied = await c.post("/mcp", json=INIT, headers=HEADERS)
        assert denied.status_code == 401
        match = re.search(r'resource_metadata="([^"]+)"', denied.headers["www-authenticate"])
        assert match
        metadata = (await c.get(match.group(1))).json()
    assert metadata["resource"] == f"{s.base_url}/mcp"
    assert metadata["authorization_servers"]
    assert any(x["http"]["status"] == 401 for x in read_log_lines(tmp_path))
