import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import read_log_lines
from key_value.aio.stores.memory import MemoryStore

from tutor_spike import server
from tutor_spike.server import Settings, build_app, git_sha, google_auth, run_kwargs

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
    "SPIKE_TESTERS": "me@example.com=author-free",
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
    assert s.testers == {"me@example.com": "author-free"}
    assert s.base_url == "https://tutor-spike.example.com"


async def test_app_serves_json_and_logs_initialize_and_tool_call(tmp_path: Path) -> None:
    app = build_app(
        settings(tmp_path), auth=None, server_sha="test", resolve_tester=lambda: "author-free"
    )
    inner = app.app.app
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
    [call] = [x for x in read_log_lines(tmp_path) if x["kind"] == "call"]
    assert call["tool"] == "get_profile"
    assert call["tester"] == "author-free"
    assert call["mcp_session_id"] == session["mcp-session-id"]
    assert call["rpc_id"] == "2"


async def test_unauthenticated_call_points_to_metadata_whose_resource_is_the_connector_url(
    tmp_path: Path,
) -> None:
    s = settings(tmp_path)
    app = build_app(s, auth=google_auth(s, client_storage=MemoryStore()), server_sha="test")
    inner = app.app.app
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


def test_settings_hide_secrets_from_repr(tmp_path: Path) -> None:
    text = repr(settings(tmp_path))
    assert "not-a-real-secret" not in text
    assert "k" * 64 not in text


def test_settings_reject_a_short_signing_key() -> None:
    with pytest.raises(SystemExit, match="32"):
        Settings.from_env({**ENV, "SPIKE_JWT_SIGNING_KEY": "k" * 31})


def test_uvicorn_runs_without_the_access_log(tmp_path: Path) -> None:
    kwargs = run_kwargs(settings(tmp_path))
    assert kwargs["access_log"] is False
    assert (kwargs["host"], kwargs["port"]) == ("127.0.0.1", 8765)


def _fake_git(outputs: dict[str, str | Exception]) -> Any:
    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        out = outputs[cmd[1]]
        if isinstance(out, Exception):
            raise out
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    return run


@pytest.mark.parametrize(
    ("outputs", "expected"),
    [
        ({"rev-parse": "abc1234\n", "status": ""}, "abc1234"),
        ({"rev-parse": "abc1234\n", "status": " M spike/x.py\n"}, "abc1234-dirty"),
        ({"rev-parse": OSError("no git"), "status": ""}, "unknown"),
        ({"rev-parse": "abc1234\n", "status": subprocess.TimeoutExpired("git", 5)}, "unknown"),
    ],
)
def test_git_sha_marks_a_dirty_tree(
    monkeypatch: pytest.MonkeyPatch, outputs: dict[str, str | Exception], expected: str
) -> None:
    monkeypatch.setattr(server.subprocess, "run", _fake_git(outputs))
    assert git_sha() == expected


async def _post_raw(tmp_path: Path, path: str, size: int) -> int:
    app = build_app(
        settings(tmp_path), auth=None, server_sha="test", resolve_tester=lambda: "author-free"
    )
    inner = app.app.app
    transport = httpx.ASGITransport(app=app)
    async with (
        inner.lifespan(inner),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as c,
    ):
        response = await c.post(path, content=b"x" * size, headers=HEADERS)
    return response.status_code


async def test_oversized_mcp_post_is_rejected_and_logged(tmp_path: Path) -> None:
    assert await _post_raw(tmp_path, "/mcp", 70_000) == 413
    [record] = read_log_lines(tmp_path)
    assert (record["http"]["path"], record["http"]["status"]) == ("/mcp", 413)


async def test_oversized_auth_post_is_rejected(tmp_path: Path) -> None:
    assert await _post_raw(tmp_path, "/register", 2 * 1024 * 1024) == 413
    [record] = read_log_lines(tmp_path)
    assert (record["http"]["path"], record["http"]["status"]) == ("/register", 413)
