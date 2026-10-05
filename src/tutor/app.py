"""Product ASGI app: MCP + OAuth proxy and the website in one process.

Both apps own root paths, so a path dispatcher picks one per request (plan ruling 2). Each
keeps its own middleware: the web CSP and no-store headers never touch MCP responses.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import zoneinfo
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import anyio.to_thread
from sqlalchemy import Engine

from tutor.auth.mcp_auth import MCP_CALLBACK_PATH, build_google_provider
from tutor.db.engine import check_app_role, make_engine
from tutor.db.uow import PgIdentity, pg_uow_factory
from tutor.mcp.observe import user_hash
from tutor.mcp.server import build_mcp
from tutor.services.context import Services
from tutor.settings import Settings
from tutor.web.app import create_app
from tutor.web.config import MCP_PATH, WebConfig
from tutor.web.pg import (
    PURGE_ABSOLUTE,
    PURGE_ANONYMOUS,
    PURGE_IDLE,
    PgWebBackend,
    pg_web_deps,
)
from tutor.web.profile import ServicesProfiles, install_profiles
from tutor.web.sessions import ANONYMOUS_LIFETIME

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MCP_PATHS: frozenset[str] = frozenset(
    {MCP_PATH, "/authorize", "/token", "/register", "/consent", MCP_CALLBACK_PATH}
)
WELL_KNOWN_PREFIX = "/.well-known/"
MAX_BODY_BYTES = 65_536
BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MAX_LOGGED_PATH = 200
PURGE_EVERY_S = 3600.0

_routes = logging.getLogger("tutor.http")
_web_log = logging.getLogger("tutor.web")


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
    """Rejects bodies over 64 KB (POST, PUT, PATCH, DELETE) with 413 before the app reads them.

    Spec section 5. It wraps both the MCP app and the website.
    """

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self._limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in BODY_METHODS:
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
            if message["type"] == "http.disconnect":
                return  # the client left: nothing to answer, nothing for the app to do
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


def route_label(scope: Scope) -> str:
    """The matched route template, else the fixed MCP path, else "unmatched".

    Never the raw path: it can carry ids and whatever a client types (/.well-known/...)."""
    template = getattr(scope.get("route"), "path", None)
    if isinstance(template, str) and template:
        return template[:MAX_LOGGED_PATH]
    path = scope.get("path", "")
    return path if path in MCP_PATHS else "unmatched"


class RequestLog:
    """One JSON line on `tutor.http` per HTTP request of the website and of the OAuth and
    well-known routes (uvicorn's access log is off; spec 13).

    A line carries the route label, the method, the status, the latency and the learner's
    hash when the web session knows the user; never the query string (authorization codes
    travel in it), headers, body or exception text. /mcp is left to the MCP call log.
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
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            uid = getattr(scope.get("state", {}).get("user"), "id", None)
            _routes.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "route": route_label(scope),
                        "method": str(scope.get("method", ""))[:16],
                        "status": status,
                        "latency_ms": round((self._clock() - started) * 1000, 1),
                        "user_hash": user_hash(uid) if isinstance(uid, UUID) else None,
                    }
                )
            )


class PeriodicPurge:
    """Runs `job` (blocking) in a worker thread once the server has started, then every
    `every_s` seconds until shutdown. It wraps the MCP app, which receives the lifespan
    (PathDispatch). Used for the web-session purge; a failure logs its class only and the
    loop goes on."""

    def __init__(
        self, app: ASGIApp, job: Callable[[], int], every_s: float = PURGE_EVERY_S
    ) -> None:
        self.app = app
        self.job = job
        self._every_s = every_s

    async def _loop(self) -> None:
        while True:
            try:
                deleted = await anyio.to_thread.run_sync(self.job, abandon_on_cancel=True)
                _web_log.info(json.dumps({"event": "web_sessions_purged", "deleted": deleted}))
            except Exception as exc:  # never the message: it can carry driver details
                _web_log.error("web session purge failed exc=%s", type(exc).__name__)
            await asyncio.sleep(self._every_s)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self.app(scope, receive, send)
            return
        task: asyncio.Task[None] | None = None

        async def watch(message: Message) -> None:
            nonlocal task
            if message["type"] == "lifespan.startup.complete" and task is None:
                task = asyncio.create_task(self._loop())
            await send(message)

        async def listen() -> Message:
            message = await receive()
            if message["type"] == "lifespan.shutdown" and task is not None:
                task.cancel()  # before the inner app starts its own shutdown
            return message

        try:
            await self.app(scope, listen, watch)
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


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


def web_config_from_env(environ: Mapping[str, str]) -> WebConfig:
    """WebConfig.from_env; a missing or invalid key exits naming the key (never a value)."""
    try:
        return WebConfig.from_env(environ)
    except KeyError as missing:
        raise SystemExit(f"Missing settings: {missing.args[0]}") from None
    except ValueError as invalid:  # the messages name keys and allowed forms, not values
        raise SystemExit(str(invalid)) from None


def build_app(
    settings: Settings, *, engine: Engine | None = None, web_config: WebConfig | None = None
) -> PathDispatch:
    """MCP + OAuth proxy and the website, both behind the 64 KB guard and the request log.

    On Postgres the login role is checked first (RLS must bind it), and the server lifespan
    purges expired web sessions at startup and then hourly."""
    engine = engine if engine is not None else make_engine(settings.database_url)
    on_postgres = engine.dialect.name == "postgresql"
    if on_postgres:
        check_app_role(engine)  # RLS must bind this login role; SystemExit otherwise
    config = web_config if web_config is not None else web_config_from_env(os.environ)
    svc = Services(
        uow=pg_uow_factory(engine),
        clock=_utc_now,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    mcp = build_mcp(svc, PgIdentity(engine), auth=build_google_provider(settings))
    deps = pg_web_deps(engine, settings, config)
    mcp_app: ASGIApp = RequestLog(BodySizeGuard(mcp.http_app(path=MCP_PATH)))
    if on_postgres and isinstance(deps.sessions, PgWebBackend):
        mcp_app = PeriodicPurge(mcp_app, _session_purge(deps.sessions, config, deps.clock))
    web = create_app(deps, config)
    install_profiles(web, ServicesProfiles(svc))  # Perfil saves through the same Services
    web_app = RequestLog(BodySizeGuard(web))
    return PathDispatch(mcp_app, web_app=web_app)


def _session_purge(
    store: PgWebBackend, config: WebConfig, clock: Callable[[], datetime]
) -> Callable[[], int]:
    idle = timedelta(days=config.session_idle_days)
    absolute = timedelta(days=config.session_max_days)
    if (idle, absolute, ANONYMOUS_LIFETIME) != (PURGE_IDLE, PURGE_ABSOLUTE, PURGE_ANONYMOUS):
        # The database function (migration 0005) has these lifetimes built in.
        raise SystemExit("web session lifetimes must be 14 days idle, 30 days absolute")

    def purge() -> int:
        return store.purge_expired(
            clock(), idle=idle, absolute=absolute, anonymous=ANONYMOUS_LIFETIME
        )

    return purge
