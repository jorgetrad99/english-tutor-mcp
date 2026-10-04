"""Outer ASGI middleware: logs every HTTP request before the SDK validates it (spec §6.1)."""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tutor_spike.eventlog import JsonlLog

_LOGGED_HEADERS = ("user-agent", "mcp-session-id", "mcp-protocol-version")


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
        log_bodies = is_post and scope["path"].rstrip("/") == self._mcp_path
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
                request_headers = _headers(scope.get("headers", []))
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
        prefix = text[:4096]
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
                events.append({"unparsed": data})
    if len(events) == 1:
        return events[0]
    return events or {"unparsed": text}


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
