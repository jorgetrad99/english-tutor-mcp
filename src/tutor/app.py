"""Product ASGI app: MCP + OAuth proxy and (from Task 23) the website in one process.

Both apps own root paths, so a path dispatcher picks one per request (plan ruling 2). Each
keeps its own middleware: the web CSP and no-store headers never touch MCP responses.
"""

from __future__ import annotations

import json
import logging
import time
import zoneinfo
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from tutor.auth.mcp_auth import MCP_CALLBACK_PATH, build_google_provider
from tutor.db.engine import check_app_role, make_engine
from tutor.db.uow import PgIdentity, pg_uow_factory
from tutor.mcp.server import build_mcp
from tutor.services.context import Services
from tutor.settings import Settings

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MCP_PATH = "/mcp"
MCP_PATHS: frozenset[str] = frozenset(
    {MCP_PATH, "/authorize", "/token", "/register", "/consent", MCP_CALLBACK_PATH}
)
WELL_KNOWN_PREFIX = "/.well-known/"
MAX_BODY_BYTES = 65_536
MAX_LOGGED_PATH = 200

_routes = logging.getLogger("tutor.http")


async def send_json(send: Send, status: int, body: dict[str, Any]) -> None:
    data = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(data)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": data})


class BodySizeGuard:
    """Rejects POST bodies over 64 KB with 413 before the MCP app reads them (spec section 5)."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self._limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v for k, v in scope.get("headers", [])}
        declared = headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self._limit):
            await send_json(send, 413, {"error": "payload_too_large"})
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self._limit:
                await send_json(send, 413, {"error": "payload_too_large"})
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


class McpRouteLog:
    """One log line per OAuth and well-known request (uvicorn's access log is off).

    /mcp is skipped: the call log covers it. A line carries the route path WITHOUT the query
    string (authorization codes travel in it), the method, the status and the latency; never
    headers, query, body or exception text.
    """

    def __init__(self, app: ASGIApp, clock: Callable[[], float] = time.perf_counter) -> None:
        self.app = app
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == MCP_PATH:
            await self.app(scope, receive, send)
            return
        status = 500
        started = self._clock()

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            _routes.info(
                json.dumps(
                    {
                        "event": "mcp_http_route",
                        "route": str(scope.get("path", ""))[:MAX_LOGGED_PATH],
                        "method": scope.get("method"),
                        "status": status,
                        "latency_ms": round((self._clock() - started) * 1000, 1),
                    }
                )
            )


class PathDispatch:
    """Lifespan goes to the MCP app; requests go by path; no web app means 404 JSON."""

    def __init__(self, mcp_app: ASGIApp, web_app: ASGIApp | None) -> None:
        self.mcp_app = mcp_app
        self.web_app = web_app

    def route(self, path: str) -> ASGIApp | None:
        if path in MCP_PATHS or path.startswith(WELL_KNOWN_PREFIX):
            return self.mcp_app
        return self.web_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.mcp_app(scope, receive, send)
            return
        app = self.route(scope.get("path", ""))
        if app is not None:
            await app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1000})
        else:
            await send_json(send, 404, {"error": "not_found"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_app(settings: Settings, *, engine: Engine | None = None) -> PathDispatch:
    """MCP only for now (web_app None); Task 23 passes the dashboard app as web_app."""
    engine = engine if engine is not None else make_engine(settings.database_url)
    if engine.dialect.name == "postgresql":
        check_app_role(engine)  # RLS must bind this login role; SystemExit otherwise
    svc = Services(
        uow=pg_uow_factory(engine),
        clock=_utc_now,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    mcp = build_mcp(svc, PgIdentity(engine), auth=build_google_provider(settings))
    return PathDispatch(McpRouteLog(BodySizeGuard(mcp.http_app(path=MCP_PATH))), web_app=None)
