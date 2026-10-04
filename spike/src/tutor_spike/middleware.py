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
    ) -> None:
        self.app = app
        self._log = log
        self._now = now
        self._server_sha = server_sha
        self._mcp_path = mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ts_in = self._now()
        log_bodies = scope["path"].rstrip("/") == self._mcp_path
        request_body: list[bytes] = []
        response_body: list[bytes] = []
        response_headers: dict[str, str] = {}
        status = 0

        async def receive_logged() -> Message:
            message = await receive()
            if log_bodies and message["type"] == "http.request":
                request_body.append(message.get("body", b""))
            return message

        async def send_logged(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers.update(_headers(message.get("headers", [])))
            elif log_bodies and message["type"] == "http.response.body":
                response_body.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_logged, send_logged)
        finally:
            ts_out = self._now()
            request_headers = _headers(scope.get("headers", []))
            self._log.write(
                {
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
                    },
                    "rpc": _rpc(b"".join(request_body), b"".join(response_body))
                    if log_bodies
                    else None,
                }
            )


def _headers(raw: Any) -> dict[str, str]:
    """Only the allowlisted headers; Authorization and cookies are never kept."""
    headers: dict[str, str] = {}
    for key, value in raw:
        name = key.decode("latin-1").lower()
        if name in _LOGGED_HEADERS:
            headers[name] = value.decode("latin-1")
    return headers


def _parse_body(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    events: list[Any] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data = line[len("data:") :].strip()
            try:
                events.append(json.loads(data))
            except json.JSONDecodeError:
                events.append({"unparsed": data})
    if len(events) == 1:
        return events[0]
    return events or {"unparsed": text}


def _rpc(request_raw: bytes, response_raw: bytes) -> dict[str, Any]:
    request = _parse_body(request_raw)
    response = _parse_body(response_raw)
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
    if isinstance(request, dict):
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
    if isinstance(response, dict):
        rpc["result_raw"] = response.get("result")
        rpc["error_raw"] = response.get("error")
    else:
        rpc["result_raw"] = response
    return rpc
