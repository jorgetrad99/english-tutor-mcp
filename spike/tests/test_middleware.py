import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from conftest import FIXED_NOW, read_log_lines
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tutor_spike.eventlog import JsonlLog
from tutor_spike.middleware import RawLogMiddleware

SSE_BODY = 'event: message\ndata: {"jsonrpc": "2.0", "id": 3, "result": {"ok": true}}\n\n'


async def mcp_endpoint(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        # Invalid JSON should not crash the endpoint
        return JSONResponse({"error": "Invalid JSON"}, status_code=200)
    if body.get("id") == 3:
        return Response(SSE_BODY, media_type="text/event-stream")
    params = body.get("params", {})
    if body.get("method") == "tools/call" and params.get("arguments", {}).get("bad"):
        error = {"code": -32602, "message": "Unexpected field 'bad'"}
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "error": error})
    result = {"structuredContent": {"ok": True}}
    return JSONResponse(
        {"jsonrpc": "2.0", "id": body.get("id"), "result": result},
        headers={"mcp-session-id": "sess-1"},
    )


async def token_endpoint(request: Request) -> Response:
    await request.body()
    return JSONResponse({"access_token": "secret-access-token"})


async def crash(request: Request) -> Response:
    raise RuntimeError("boom")


async def mcp_get_endpoint(request: Request) -> Response:
    return Response("data: x\n\n", media_type="text/event-stream")


def make_app(tmp_path: Path, max_capture_bytes: int = 262_144) -> RawLogMiddleware:
    inner = Starlette(
        routes=[
            Route("/mcp", mcp_endpoint, methods=["POST"]),
            Route("/mcp", mcp_get_endpoint, methods=["GET"]),
            Route("/token", token_endpoint, methods=["POST"]),
            Route("/crash", crash, methods=["POST"]),
        ]
    )
    ticks = itertools.count()

    def clock() -> datetime:
        return FIXED_NOW() + timedelta(milliseconds=10 * next(ticks))

    return RawLogMiddleware(
        inner,
        log=JsonlLog(tmp_path, FIXED_NOW),
        now=clock,
        server_sha="abc1234",
        max_capture_bytes=max_capture_bytes,
    )


def client(app: RawLogMiddleware) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_tool_call_is_logged_with_raw_arguments_and_result(tmp_path: Path) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "end_session", "arguments": {"session_id": "s", "user_turns": ["hi"]}},
    }
    headers = {"user-agent": "Claude-User", "mcp-protocol-version": "2025-06-18"}
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json=payload, headers=headers)
    [record] = read_log_lines(tmp_path)
    assert record["kind"] == "http"
    assert record["server_sha"] == "abc1234"
    assert record["latency_ms"] >= 0
    assert record["http"]["method"] == "POST"
    assert record["http"]["path"] == "/mcp"
    assert record["http"]["status"] == 200
    assert record["http"]["user_agent"] == "Claude-User"
    assert record["http"]["mcp_session_id"] == "sess-1"
    assert record["http"]["mcp_protocol_version"] == "2025-06-18"
    assert record["http"]["request_truncated"] is False
    assert record["http"]["response_truncated"] is False
    assert record["rpc"]["id"] == 7
    assert record["rpc"]["tool"] == "end_session"
    assert record["rpc"]["params_raw"]["arguments"] == {"session_id": "s", "user_turns": ["hi"]}
    assert record["rpc"]["result_raw"] == {"structuredContent": {"ok": True}}


async def test_rejected_arguments_are_still_logged_verbatim(tmp_path: Path) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "end_session", "arguments": {"bad": "glossary"}},
    }
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json=payload)
    [record] = read_log_lines(tmp_path)
    assert record["rpc"]["params_raw"]["arguments"] == {"bad": "glossary"}
    assert record["rpc"]["error_raw"]["code"] == -32602


async def test_initialize_client_info_is_extracted(tmp_path: Path) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "claude-ai", "version": "0.1.0"}},
    }
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json=payload)
    [record] = read_log_lines(tmp_path)
    assert record["rpc"]["client_info"] == {"name": "claude-ai", "version": "0.1.0"}


async def test_sse_response_body_is_parsed(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"})
    [record] = read_log_lines(tmp_path)
    assert record["rpc"]["result_raw"] == {"ok": True}


async def test_auth_routes_never_log_bodies_or_secrets(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        await c.post(
            "/token",
            content=b"grant_type=authorization_code&code=secret-code",
            headers={"authorization": "Bearer secret-bearer"},
        )
    [record] = read_log_lines(tmp_path)
    assert record["rpc"] is None
    assert record["http"]["path"] == "/token"
    raw = (tmp_path / "calls-2026-10-06.jsonl").read_text(encoding="utf-8")
    for secret in ("secret-code", "secret-bearer", "secret-access-token"):
        assert secret not in raw


async def test_authorization_header_is_never_logged_on_mcp_path(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
            headers={"authorization": "Bearer secret-bearer"},
        )
    raw = (tmp_path / "calls-2026-10-06.jsonl").read_text(encoding="utf-8")
    assert "secret-bearer" not in raw


async def test_crashing_request_is_still_logged(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        response = await c.post("/crash", json={})
    assert response.status_code == 500
    [record] = read_log_lines(tmp_path)
    assert record["http"]["status"] == 500


async def test_timestamps_are_utc_iso(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "ping"})
    [record] = read_log_lines(tmp_path)
    assert datetime.fromisoformat(record["ts_in"]).tzinfo == UTC
    assert record["ts_out"] >= record["ts_in"]


async def test_request_body_over_cap_is_truncated(tmp_path: Path) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "long_tool", "arguments": {"data": "x" * 200}},
    }
    async with client(make_app(tmp_path, max_capture_bytes=64)) as c:
        response = await c.post("/mcp", json=payload)
    assert response.status_code == 200
    [record] = read_log_lines(tmp_path)
    assert record["http"]["request_truncated"] is True
    assert record["http"]["request_bytes"] > 64
    assert isinstance(record["rpc"]["request_raw"], dict)
    assert record["rpc"]["request_raw"]["truncated"] is True
    assert "prefix" in record["rpc"]["request_raw"]


async def test_response_body_over_cap_is_truncated(tmp_path: Path) -> None:
    async with client(make_app(tmp_path, max_capture_bytes=64)) as c:
        response = await c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "test", "arguments": {}},
            },
        )
    assert response.status_code == 200
    [record] = read_log_lines(tmp_path)
    assert record["http"]["response_truncated"] is True
    assert record["http"]["response_bytes"] > 0
    assert isinstance(record["rpc"]["result_raw"], dict)
    assert record["rpc"]["result_raw"]["truncated"] is True


async def test_get_mcp_does_not_capture_response_body(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        response = await c.get("/mcp")
    assert response.status_code == 200
    [record] = read_log_lines(tmp_path)
    assert record["http"]["method"] == "GET"
    assert record["http"]["path"] == "/mcp"
    assert record["http"]["response_bytes"] > 0
    assert record["http"]["response_truncated"] is False
    assert record["rpc"] is None


async def test_large_body_is_handled_without_exception(tmp_path: Path) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {"name": "large", "arguments": {"data": "y" * 5000}},
    }
    async with client(make_app(tmp_path, max_capture_bytes=1_000_000)) as c:
        response = await c.post("/mcp", json=payload)
    assert response.status_code == 200
    [record] = read_log_lines(tmp_path)
    assert record["kind"] == "http"
    assert record["http"]["status"] == 200
    assert "log_error" not in record


async def test_raw_binary_body_with_default_cap(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        response = await c.post(
            "/mcp", content=b"1" * 5000, headers={"content-type": "application/json"}
        )
    assert response.status_code == 200
    [record] = read_log_lines(tmp_path)
    assert record["kind"] == "http"
    assert record["http"]["status"] == 200
    assert record["http"]["request_truncated"] is False
    assert record["http"]["request_bytes"] == 5000
    assert isinstance(record["rpc"]["request_raw"], dict)
    assert record["rpc"]["request_raw"]["unparsed"]  # ValueError path: int digits not valid JSON


async def test_deeply_nested_json_with_large_cap(tmp_path: Path) -> None:
    body = b"[" * 100_000 + b"]" * 100_000
    async with client(make_app(tmp_path, max_capture_bytes=300_000)) as c:
        response = await c.post("/mcp", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 200
    [record] = read_log_lines(tmp_path)
    assert record["kind"] == "http"
    assert record["http"]["status"] == 200
    assert "log_error" not in record


async def test_logging_failure_falls_back_to_minimal_record(tmp_path: Path) -> None:
    class FailingJsonlLog:
        def __init__(self, real_log: JsonlLog) -> None:
            self.real_log = real_log
            self.write_count = 0

        def write(self, record: dict) -> None:
            self.write_count += 1
            if self.write_count == 1:
                raise ValueError("Log write failed")
            self.real_log.write(record)

    failing_log = FailingJsonlLog(JsonlLog(tmp_path, FIXED_NOW))
    ticks = itertools.count()

    def clock() -> datetime:
        return FIXED_NOW() + timedelta(milliseconds=10 * next(ticks))

    app = RawLogMiddleware(
        Starlette(
            routes=[
                Route(
                    "/mcp",
                    lambda req: JSONResponse({"ok": True}),
                    methods=["POST"],
                )
            ]
        ),
        log=failing_log,
        now=clock,
        server_sha="abc1234",
    )

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.post("/mcp", json={"test": "data"})
    assert response.status_code == 200
    [record] = read_log_lines(tmp_path)
    assert record["kind"] == "http"
    assert record["http"]["status"] == 200
    assert record["log_error"] == "ValueError"


async def test_many_small_chunks_with_small_cap(tmp_path: Path) -> None:
    chunk_size = 200
    num_chunks = 2000

    async def stream_body():
        for _ in range(num_chunks):
            yield b"x" * chunk_size

    async with client(make_app(tmp_path, max_capture_bytes=64_000)) as c:
        response = await c.post(
            "/mcp",
            content=stream_body(),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 200
    [record] = read_log_lines(tmp_path)
    assert record["http"]["request_truncated"] is True
    assert record["http"]["request_bytes"] == chunk_size * num_chunks
    assert record["kind"] == "http"
