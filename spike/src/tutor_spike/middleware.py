"""Outer ASGI middleware: logs every HTTP request before the SDK validates it (spec §6.1)."""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tutor_spike.eventlog import JsonlLog

_LOGGED_HEADERS = ("user-agent", "mcp-session-id", "mcp-protocol-version")
UNPARSED_CHARS = 4096
MCP_BODY_LIMIT = 65_536
OTHER_BODY_LIMIT = 1_048_576


class BodySizeGuard:
    """Rejects oversized POST bodies with 413 before they reach the app.

    Sits inside RawLogMiddleware, so every rejection is still logged. The body is read
    (up to the limit) before the app runs, then replayed to it unchanged.
    """

    def __init__(
        self,
        app: ASGIApp,
        mcp_path: str = "/mcp",
        mcp_limit: int = MCP_BODY_LIMIT,
        other_limit: int = OTHER_BODY_LIMIT,
    ) -> None:
        self.app = app
        self._mcp_path = mcp_path
        self._mcp_limit = mcp_limit
        self._other_limit = other_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        is_mcp = scope["path"].rstrip("/") == self._mcp_path
        limit = self._mcp_limit if is_mcp else self._other_limit
        declared = _headers_raw(scope.get("headers", [])).get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await _too_large(send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await _too_large(send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


async def _too_large(send: Send) -> None:
    body = b'{"error": "request body too large"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _headers_raw(raw: Any) -> dict[str, str]:
    return {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in raw}


class RawLogMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        log: JsonlLog,
        now: Callable[[], datetime],
        server_sha: str,
        mcp_path: str = "/mcp",
        max_capture_bytes: int = 262_144,
    ) -> None:
        self.app = app
        self._log = log
        self._now = now
        self._server_sha = server_sha
        self._mcp_path = mcp_path
        self._max_capture_bytes = max_capture_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ts_in = self._now()
        is_post = scope.get("method") == "POST"
        is_mcp = scope["path"].rstrip("/") == self._mcp_path
        log_bodies = is_post and is_mcp
        request_body: list[bytes] = []
        response_body: list[bytes] = []
        response_headers: dict[str, str] = {}
        status = 0
        request_bytes_total = 0
        response_bytes_total = 0
        request_captured = 0
        response_captured = 0
        request_truncated = False
        response_truncated = False

        async def receive_logged() -> Message:
            nonlocal request_bytes_total, request_captured, request_truncated
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                request_bytes_total += len(chunk)
                if log_bodies and not request_truncated:
                    space_left = self._max_capture_bytes - request_captured
                    if space_left > 0:
                        to_capture = min(len(chunk), space_left)
                        request_body.append(chunk[:to_capture])
                        request_captured += to_capture
                        if to_capture < len(chunk):
                            request_truncated = True
                    else:
                        request_truncated = True
            return message

        async def send_logged(message: Message) -> None:
            nonlocal status, response_bytes_total, response_captured, response_truncated
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers.update(_headers(message.get("headers", [])))
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                response_bytes_total += len(chunk)
                if log_bodies and not response_truncated:
                    space_left = self._max_capture_bytes - response_captured
                    if space_left > 0:
                        to_capture = min(len(chunk), space_left)
                        response_body.append(chunk[:to_capture])
                        response_captured += to_capture
                        if to_capture < len(chunk):
                            response_truncated = True
                    else:
                        response_truncated = True
            await send(message)

        try:
            await self.app(scope, receive_logged, send_logged)
        finally:
            try:
                ts_out = self._now()
                # Auth routes keep only method, path, status, latency and sizes.
                request_headers = _headers(scope.get("headers", [])) if is_mcp else {}
                if not is_mcp:
                    response_headers.clear()
                record = {
                    "kind": "http",
                    "ts_in": ts_in.isoformat(),
                    "ts_out": ts_out.isoformat(),
                    "latency_ms": int((ts_out - ts_in).total_seconds() * 1000),
                    "server_sha": self._server_sha,
                    "http": {
                        "method": scope.get("method"),
                        "path": scope["path"],
                        "status": status if status else 500,
                        "user_agent": request_headers.get("user-agent"),
                        "mcp_session_id": request_headers.get("mcp-session-id")
                        or response_headers.get("mcp-session-id"),
                        "mcp_protocol_version": request_headers.get("mcp-protocol-version"),
                        "request_bytes": request_bytes_total,
                        "response_bytes": response_bytes_total,
                        "request_truncated": request_truncated,
                        "response_truncated": response_truncated,
                    },
                    "rpc": _rpc(
                        b"".join(request_body),
                        b"".join(response_body),
                        request_truncated,
                        response_truncated,
                    )
                    if log_bodies
                    else None,
                }
                self._log.write(record)
            except Exception as exc:
                try:
                    minimal_record = {
                        "kind": "http",
                        "ts_in": ts_in.isoformat(),
                        "ts_out": ts_out.isoformat(),
                        "server_sha": self._server_sha,
                        "http": {
                            "method": scope.get("method"),
                            "path": scope["path"],
                            "status": status if status else 500,
                        },
                        "log_error": type(exc).__name__,
                    }
                    self._log.write(minimal_record)
                except Exception:  # noqa: S110
                    pass


def _headers(raw: Any) -> dict[str, str]:
    """Only the allowlisted headers; Authorization and cookies are never kept."""
    headers: dict[str, str] = {}
    for key, value in raw:
        name = key.decode("latin-1").lower()
        if name in _LOGGED_HEADERS:
            headers[name] = value.decode("latin-1")
    return headers


def _parse_body(raw: bytes, truncated: bool = False) -> Any:
    if truncated:
        text = raw.decode("utf-8", errors="replace")
        prefix = text[:UNPARSED_CHARS]
        return {"truncated": True, "prefix": prefix}
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        pass
    events: list[Any] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data = line[len("data:") :].strip()
            try:
                events.append(json.loads(data))
            except (ValueError, RecursionError):
                events.append({"unparsed": data[:UNPARSED_CHARS]})
    if len(events) == 1:
        return events[0]
    return events or {"unparsed": text[:UNPARSED_CHARS]}


def _rpc(
    request_raw: bytes,
    response_raw: bytes,
    request_truncated: bool = False,
    response_truncated: bool = False,
) -> dict[str, Any]:
    request = _parse_body(request_raw, request_truncated)
    response = _parse_body(response_raw, response_truncated)
    rpc: dict[str, Any] = {
        "id": None,
        "method": None,
        "tool": None,
        "client_info": None,
        "params_raw": None,
        "result_raw": None,
        "error_raw": None,
        "request_raw": None,
    }
    is_request_valid = (
        isinstance(request, dict) and not request.get("truncated") and not request.get("unparsed")
    )
    if is_request_valid:
        params = request.get("params")
        rpc["id"] = request.get("id")
        rpc["method"] = request.get("method")
        rpc["params_raw"] = params
        if isinstance(params, dict):
            if rpc["method"] == "tools/call":
                rpc["tool"] = params.get("name")
            if rpc["method"] == "initialize":
                rpc["client_info"] = params.get("clientInfo")
    else:
        rpc["request_raw"] = request
    is_response_valid = (
        isinstance(response, dict)
        and not response.get("truncated")
        and not response.get("unparsed")
    )
    if is_response_valid:
        rpc["result_raw"] = response.get("result")
        rpc["error_raw"] = response.get("error")
    else:
        rpc["result_raw"] = response
    return rpc
