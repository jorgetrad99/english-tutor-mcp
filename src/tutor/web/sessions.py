"""Server-side sessions behind one cookie (spec 9.3).

The cookie holds a random token; the store keeps only its SHA-256. The session's
`data` dict is exposed as `request.session` so Authlib can keep OAuth state in it.

Anonymous sessions exist only to hold the OAuth state of a login in flight, so they
live for ten minutes from creation. Login replaces the session with a new token (and a
new CSRF token), which also defeats session fixation.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tutor.web.config import WebConfig
from tutor.web.ports import WebDeps, WebSession

COOKIE = "__Host-tutor_session"
_TOUCH_EVERY = timedelta(minutes=5)
ANONYMOUS_LIFETIME = timedelta(minutes=10)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionHolder:
    def __init__(self, session: WebSession | None) -> None:
        self.session = session
        self.login_as: UUID | None = None
        self.logged_out = False

    def login(self, user_id: UUID) -> None:
        self.login_as = user_id

    def logout(self) -> None:
        self.logged_out = True


class ServerSessionMiddleware:
    def __init__(self, app: ASGIApp, deps: WebDeps, config: WebConfig) -> None:
        self.app = app
        self.deps = deps
        self.idle = timedelta(days=config.session_idle_days)
        self.absolute = timedelta(days=config.session_max_days)

    def _expired(self, session: WebSession, now: datetime) -> bool:
        if session.user_id is None:
            return now - session.created_at > ANONYMOUS_LIFETIME
        return now - session.last_seen_at > self.idle or now - session.created_at > self.absolute

    def _load(self, token: str | None, now: datetime) -> WebSession | None:
        if not token:
            return None
        session = self.deps.sessions.load_session(hash_token(token))
        if session is None:
            return None
        if self._expired(session, now):
            self.deps.sessions.delete_session(session.token_hash)
            return None
        return session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        now = self.deps.clock()
        session = self._load(HTTPConnection(scope).cookies.get(COOKIE), now)
        holder = SessionHolder(session)
        scope.setdefault("state", {})["web"] = holder
        data: dict[str, Any] = dict(session.data) if session else {}
        scope["session"] = data
        original = dict(data)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                cookie = self._commit(holder, data, original, now)
                if cookie is not None:
                    MutableHeaders(scope=message).append("set-cookie", cookie)
            await send(message)

        await self.app(scope, receive, send_wrapper)

    def _cookie(self, token: str, max_age: int) -> str:
        return f"{COOKIE}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"

    def _new(self, user_id: UUID | None, data: dict[str, Any], now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        self.deps.sessions.save_session(
            WebSession(hash_token(token), user_id, secrets.token_urlsafe(32), now, now, data)
        )
        lifetime = ANONYMOUS_LIFETIME if user_id is None else self.absolute
        return self._cookie(token, int(lifetime.total_seconds()))

    def _commit(
        self,
        holder: SessionHolder,
        data: dict[str, Any],
        original: dict[str, Any],
        now: datetime,
    ) -> str | None:
        session = holder.session
        if holder.logged_out:
            if session is not None:
                self.deps.sessions.delete_session(session.token_hash)
            return self._cookie("", 0)
        if holder.login_as is not None:
            if session is not None:
                self.deps.sessions.delete_session(session.token_hash)
            kept = {k: v for k, v in data.items() if not k.startswith("_state_")}
            return self._new(holder.login_as, kept, now)
        if session is not None:
            if data != original or now - session.last_seen_at > _TOUCH_EVERY:
                session.data = data
                session.last_seen_at = now
                self.deps.sessions.save_session(session)
            return None
        if data:
            return self._new(None, data, now)
        return None
