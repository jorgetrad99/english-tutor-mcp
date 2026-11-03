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
import re
import time
import zoneinfo
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import anyio.to_thread
from fastmcp.server.auth import AuthProvider
from sqlalchemy import Engine
from starlette.middleware.trustedhost import TrustedHostMiddleware

from tutor.auth.dev_auth import build_dev_verifier
from tutor.auth.mcp_auth import MCP_CALLBACK_PATH, build_google_provider
from tutor.db.engine import check_app_role, make_engine
from tutor.db.uow import PgIdentity, pg_uow_factory
from tutor.mcp.observe import user_hash
from tutor.mcp.server import build_mcp
from tutor.services.context import Services
from tutor.settings import Settings, is_loopback
from tutor.web.app import create_app
from tutor.web.config import MCP_PATH, WebConfig
from tutor.web.frames import FrameTokens, FrameUrl, make_frame_url
from tutor.web.pg import (
    PURGE_ABSOLUTE,
    PURGE_ANONYMOUS,
    PURGE_IDLE,
    PgWebBackend,
    pg_web_deps,
)
from tutor.web.profile import ServicesProfiles, install_profiles
from tutor.web.sessions import ANONYMOUS_LIFETIME, COOKIE
from tutor.web.template_origin import InstanceDocument, TemplateHeaders, TemplateOriginApp

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
# Under dev auth only loopback names reach the MCP side (Starlette keeps IPv6 brackets), so a
# host-side tunnel forwarding a public Host to localhost:8000 gets 400, as on the website.
DEV_MCP_HOSTS = ("localhost", "127.0.0.1", "[::1]")

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


class RejectProxyHeaders:
    """400 for any request carrying `Forwarded` or an `X-Forwarded-*` header (dev auth only).

    A tunnel that rewrites Host to localhost (ngrok --host-header) passes the loopback Host
    check, but proxies add these headers; loopback dev traffic never carries them.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and any(
            name.lower() == b"forwarded" or name.lower().startswith(b"x-forwarded-")
            for name, _ in scope.get("headers", [])
        ):
            await send_json(send, 400, {"error": "proxied_request"})
            return
        await self.app(scope, receive, send)


# Starlette's UUIDConvertor form: either case, hyphens optional. Any other spelling a UUID
# parser might accept (braces, `urn:uuid:`) does not match and keeps 64 KB: it fails closed.
_UUID_PART = "[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
# Fixed per-path body limits (slice D controller decision Q5): a POST whose whole path matches
# gets that limit; every other request keeps MAX_BODY_BYTES. Nothing in a request changes it.
UPLOAD_BODY_LIMITS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(rf"/app/subjects/{_UUID_PART}/pdf"), 20 * 1024 * 1024 + MAX_BODY_BYTES),
    (re.compile(rf"/app/subjects/{_UUID_PART}/text"), 2 * 1024 * 1024),
)
MAX_CONCURRENT_UPLOADS = 4  # large bodies in flight at once per process; one more gets 503
_SESSION_COOKIE = COOKIE.encode("ascii") + b"="


def has_session_cookie(cookie_header: bytes | None) -> bool:
    """Whether the request carries a non-empty session cookie. Presence only: the session
    itself is checked by the website after the body is read."""
    if not cookie_header:
        return False
    return any(
        part.strip().startswith(_SESSION_COOKIE) and len(part.strip()) > len(_SESSION_COOKIE)
        for part in cookie_header.split(b";")
    )


def body_limit(
    method: str | None, path: str, *, session: bool, default: int = MAX_BODY_BYTES
) -> int:
    """The body limit for one request: an upload route's from the table when the request
    carries a session cookie, else `default`."""
    if method == "POST" and session:
        for pattern, limit in UPLOAD_BODY_LIMITS:
            if pattern.fullmatch(path):
                return limit
    return default


class BodySizeGuard:
    """Rejects bodies over the limit (POST, PUT, PATCH, DELETE) with 413: 64 KB, except the
    two upload routes of UPLOAD_BODY_LIMITS for a request with a session cookie.

    A 64 KB body is read whole before the app runs, so the app never sees a partial one. An
    upload body is not buffered: it streams to the app (Starlette spools file parts to disk)
    through a counter that, once the limit passes, tells the app the client left and answers
    413 itself; at most MAX_CONCURRENT_UPLOADS stream at once, the next gets 503.

    Spec section 5. It wraps both the MCP app and the website.
    """

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self._limit = limit
        self._uploads = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in BODY_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v for k, v in scope.get("headers", [])}
        session = has_session_cookie(headers.get("cookie"))
        path = scope.get("path", "")
        limit = body_limit(scope.get("method"), path, session=session, default=self._limit)
        declared = headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            await send_json(send, 413, {"error": "payload_too_large"})
            return
        if limit > self._limit:
            await self._stream(scope, receive, send, limit)
            return
        await self._buffered(scope, receive, send, limit)

    async def _stream(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        if self._uploads >= MAX_CONCURRENT_UPLOADS:
            await send_json(send, 503, {"error": "busy"})
            return
        self._uploads += 1
        size = 0
        over = False
        started = False

        async def counted() -> Message:
            nonlocal size, over
            if over:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    over = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded(message: Message) -> None:
            nonlocal started
            if over and not started:
                return  # the app's answer to the cut body is dropped; the 413 is ours
            started = True
            await send(message)

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            if not over:
                raise
        finally:
            self._uploads -= 1
        if over and not started:
            await send_json(send, 413, {"error": "payload_too_large"})

    async def _buffered(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
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
            if size > limit:
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


# The image HEALTHCHECK (deploy/Dockerfile) GETs this from loopback every 30 s.
HEALTHCHECK_PATH = "/.well-known/oauth-authorization-server"


def _is_health_probe(scope: Scope) -> bool:
    """The container's own probe; the same path from any other client is OAuth discovery."""
    client = scope.get("client")
    return (
        scope.get("path") == HEALTHCHECK_PATH
        and scope.get("method") == "GET"
        and client is not None
        and is_loopback(str(client[0]))
    )


class RequestLog:
    """One JSON line on `tutor.http` per HTTP request of the website and of the OAuth and
    well-known routes (uvicorn's access log is off; spec 13), except the container health
    probe.

    A line carries the route label, the method, the status, the latency and the learner's
    hash when the web session knows the user; never the query string (authorization codes
    travel in it), headers, body or exception text. /mcp is left to the MCP call log.
    """

    def __init__(self, app: ASGIApp, clock: Callable[[], float] = time.perf_counter) -> None:
        self.app = app
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == MCP_PATH or _is_health_probe(scope):
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
    """Lifespan goes to the MCP app; a request on the template host goes to the template
    origin whatever its path (spec 10, binding (d)); other requests go by path; no web app
    means 404 JSON."""

    def __init__(
        self,
        mcp_app: ASGIApp,
        web_app: ASGIApp | None,
        *,
        template_app: ASGIApp | None = None,
        template_host: str | None = None,
    ) -> None:
        if (template_app is None) != (template_host is None):
            raise ValueError("template_app and template_host go together")
        self.mcp_app = mcp_app
        self.web_app = web_app
        self.template_app = template_app
        self.template_host = template_host.lower() if template_host else None
        # The website's frame src factory (tutor.web.frames.make_frame_url); integration
        # installs it on the web app. None when no template origin is configured.
        self.frame_url: FrameUrl | None = None

    def route(self, path: str, host: str | None = None) -> ASGIApp | None:
        if self.template_host is not None and host == self.template_host:
            return self.template_app
        if path in MCP_PATHS or path.startswith(WELL_KNOWN_PREFIX):
            return self.mcp_app
        return self.web_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.mcp_app(scope, receive, send)
            return
        app = self.route(scope.get("path", ""), request_host(scope))
        if app is not None:
            await app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1000})
        else:
            await send_json(send, 404, {"error": "not_found"})


def request_host(scope: Scope) -> str | None:
    """The Host header's name, lower case, without the port or a trailing dot (`host.` is the
    same name in DNS, so it must reach the same app); None when absent."""
    for name, value in scope.get("headers", []):
        if name.lower() == b"host":
            host = value.decode("latin-1").strip().lower()
            if host.startswith("["):  # an IPv6 literal keeps its brackets
                return host.split("]", 1)[0] + "]"
            return (host.rsplit(":", 1)[0] if ":" in host else host).rstrip(".")
    return None


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


def dev_mcp_hosts(settings: Settings) -> list[str]:
    """The loopback names, plus the base URL's host (loopback too: the dev auth guard)."""
    host = urlsplit(settings.base_url).hostname or "localhost"
    host = f"[{host}]" if ":" in host else host
    return list(dict.fromkeys((*DEV_MCP_HOSTS, host)))


def build_app(
    settings: Settings,
    *,
    engine: Engine | None = None,
    web_config: WebConfig | None = None,
    instance_document: InstanceDocument | None = None,
) -> PathDispatch:
    """MCP + OAuth proxy and the website, both behind the body-size guard and the request log.

    On Postgres the login role is checked first (RLS must bind it), and the server lifespan
    purges expired web sessions at startup and then hourly. With TUTOR_DEV_AUTH (loopback dev
    only) the OAuth proxy and its discovery routes are replaced by the dev token verifier, and
    the MCP side answers only loopback Host names (TrustedHost, as the website does), and both
    sides refuse requests carrying Forwarded or X-Forwarded-* headers (RejectProxyHeaders).
    With a template origin, requests on its host reach only `TemplateOriginApp`, which serves
    `instance_document` (404 for everything while it is None)."""
    engine = engine if engine is not None else make_engine(settings.database_url)
    on_postgres = engine.dialect.name == "postgresql"
    if on_postgres:
        check_app_role(engine)  # RLS must bind this login role; SystemExit otherwise
    config = web_config if web_config is not None else web_config_from_env(os.environ)
    if config.template_origin != settings.template_origin:
        raise SystemExit("TUTOR_TEMPLATE_ORIGIN must be the same for the website and the server")
    svc = Services(
        uow=pg_uow_factory(engine),
        clock=_utc_now,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    auth: AuthProvider = (
        build_dev_verifier(settings) if settings.dev_auth else build_google_provider(settings)
    )
    mcp = build_mcp(svc, PgIdentity(engine), auth=auth)
    deps = pg_web_deps(engine, settings, config)
    mcp_app: ASGIApp = BodySizeGuard(mcp.http_app(path=MCP_PATH))
    if settings.dev_auth:
        mcp_app = TrustedHostMiddleware(mcp_app, allowed_hosts=dev_mcp_hosts(settings))
        mcp_app = RejectProxyHeaders(mcp_app)
    mcp_app = RequestLog(mcp_app)
    if on_postgres and isinstance(deps.sessions, PgWebBackend):
        mcp_app = PeriodicPurge(mcp_app, _session_purge(deps.sessions, config, deps.clock))
    web = create_app(deps, config)
    install_profiles(web, ServicesProfiles(svc))  # Perfil saves through the same Services
    web_app: ASGIApp = BodySizeGuard(web)
    if settings.dev_auth:
        web_app = RejectProxyHeaders(web_app)  # covers /auth/dev-login
    web_app = RequestLog(web_app)
    if settings.template_origin is None or settings.frame_token_key is None:
        return PathDispatch(mcp_app, web_app=web_app)
    tokens = FrameTokens(settings.frame_token_key, settings.template_origin)
    template_app: ASGIApp = BodySizeGuard(
        TemplateOriginApp(tokens, instance_document, settings.base_url)
    )
    if settings.dev_auth:
        template_app = RejectProxyHeaders(template_app)
    # Outside the guards: their 413 and 400 answers carry the template header set too.
    template_app = TemplateHeaders(template_app, settings.base_url)
    dispatch = PathDispatch(
        mcp_app,
        web_app=web_app,
        template_app=RequestLog(template_app),
        template_host=urlsplit(settings.template_origin).hostname,
    )
    dispatch.frame_url = make_frame_url(tokens, settings.template_origin)
    return dispatch


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
