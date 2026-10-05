"""FastAPI dependencies shared by every page."""

from __future__ import annotations

import secrets
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from tutor.domain.dashboard.types import Role, User
from tutor.web.config import WebConfig
from tutor.web.ports import WebDeps
from tutor.web.security import safe_next

CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class NotAuthenticated(Exception):
    def __init__(self, next_path: str) -> None:
        super().__init__(next_path)
        self.next_path = next_path


def get_deps(request: Request) -> WebDeps:
    deps: WebDeps = request.app.state.deps
    return deps


def get_config(request: Request) -> WebConfig:
    config: WebConfig = request.app.state.config
    return config


def optional_user(request: Request) -> User | None:
    holder = getattr(request.state, "web", None)
    session = getattr(holder, "session", None)
    if session is None or session.user_id is None:
        return None
    user = get_deps(request).users.find_user(session.user_id)
    if user is None or user.deletion_requested_at is not None:
        return None
    request.state.user = user
    return user


def current_user(request: Request) -> User:
    user = optional_user(request)
    if user is None:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise NotAuthenticated(target)
    return user


def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role is not Role.ADMIN:
        raise HTTPException(status_code=404)
    return user


async def require_csrf(request: Request) -> None:
    if request.method in _SAFE_METHODS:
        return
    if request.url.path.startswith("/app") and optional_user(request) is None:
        raise NotAuthenticated(request.url.path)  # nothing to forge without a session
    holder = getattr(request.state, "web", None)
    session = getattr(holder, "session", None)
    expected = session.csrf_token if session is not None else None
    sent = request.headers.get(CSRF_HEADER)
    if sent is None and request.headers.get("content-type", "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    ):
        field = (await request.form()).get(CSRF_FIELD)
        sent = field if isinstance(field, str) else None
    if not expected or not sent or not secrets.compare_digest(sent.encode(), expected.encode()):
        raise HTTPException(status_code=403)


async def require_csrf_if_session(request: Request) -> None:
    """For logout: with no valid session there is nothing to forge, so no token is needed."""
    holder = getattr(request.state, "web", None)
    if getattr(holder, "session", None) is None:
        return
    await require_csrf(request)


APP_ROUTER_DEPS = [Depends(require_csrf)]
LOGOUT_PATH = "/auth/logout"


def _depends_on(dependant: Dependant, call: object) -> bool:
    return dependant.call is call or any(_depends_on(d, call) for d in dependant.dependencies)


def csrf_exempt_paths(config: WebConfig) -> frozenset[str]:
    """Only the test login may skip CSRF, and only where it exists (TUTOR_ENV=test)."""
    return frozenset({"/auth/test-login"}) if config.test_login else frozenset()


def assert_csrf_everywhere(app: FastAPI, config: WebConfig) -> None:
    """Fail at startup if an unsafe-method route forgot `require_csrf`.

    `require_csrf_if_session` counts only on `/auth/logout`."""
    exempt = csrf_exempt_paths(config)
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in exempt:
            continue
        if not ((route.methods or set()) - _SAFE_METHODS):
            continue
        if _depends_on(route.dependant, require_csrf):
            continue
        if route.path == LOGOUT_PATH and _depends_on(route.dependant, require_csrf_if_session):
            continue  # the only route where "no session, nothing to forge" is accepted
        raise RuntimeError(f"route {route.path} accepts unsafe methods without require_csrf")


def login_redirect_target(next_path: str) -> str:
    """The login URL for a return path. The path is validated here, not by callers."""
    return f"/login?next={quote(safe_next(next_path), safe='')}"
