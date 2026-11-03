"""Login with Google, logout, language switch, the test-only login and the dev-only login.

Routes that call a data port are plain `def` (FastAPI runs them in a worker thread) or wrap the
call in `run_in_threadpool`: the Postgres adapters block.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from tutor.auth.dev_auth import DEV_IDENTITIES, DevSub
from tutor.domain.dashboard.types import Lang, User
from tutor.web.config import WebConfig
from tutor.web.deps import (
    APP_ROUTER_DEPS,
    current_user,
    get_config,
    get_deps,
    require_csrf_if_session,
)
from tutor.web.ports import GoogleIdentity, LoginFailed
from tutor.web.profile import PROFILE_PATH, optional_profiles
from tutor.web.security import safe_next
from tutor.web.views import render

router = APIRouter()
app_router = APIRouter(dependencies=APP_ROUTER_DEPS)
_NEXT_KEY = "login_next"


@router.get("/auth/google")
async def google_start(request: Request, next: str | None = None) -> Response:
    request.session[_NEXT_KEY] = safe_next(next)  # validated now and again in the callback
    config = get_config(request)
    return await get_deps(request).google.redirect(request, f"{config.base_url}/auth/callback")


@router.get("/auth/callback")
async def google_callback(request: Request) -> Response:
    deps = get_deps(request)
    stored = request.session.pop(_NEXT_KEY, None)
    try:
        identity = await deps.google.identity(request)
    except LoginFailed:
        ctx = {"next": "", "error": True}
        return await run_in_threadpool(render, request, "pages/login.html", ctx, status_code=400)
    return await _sign_in(request, identity, stored if isinstance(stored, str) else None)


async def _sign_in(request: Request, identity: GoogleIdentity, next_path: str | None) -> Response:
    """Find or create the user by sub and open the web session; shared by Google and dev login."""
    deps = get_deps(request)
    # Ports block (Postgres): never call them on the event loop.
    user = await run_in_threadpool(deps.users.sign_in, identity, deps.clock())
    if user.deletion_requested_at is not None:
        request.state.web.logout()  # drop the anonymous login session and its cookie
        return await run_in_threadpool(render, request, "pages/deletion_pending.html")
    target = safe_next(next_path)
    profiles = optional_profiles(request)
    if profiles is not None:
        view = await run_in_threadpool(profiles.view, user.id)
        if view.onboarding_needed:
            target = PROFILE_PATH  # core loop v0 spec 6.3: no profile yet, so Perfil first
    request.state.web.login(user.id)
    return RedirectResponse(target, status_code=303)


# CSRF-checked only when a session exists: logging out a dead session must still clear the cookie.
@router.post("/auth/logout", dependencies=[Depends(require_csrf_if_session)])
async def logout(request: Request) -> Response:
    request.state.web.logout()  # deletes the row and sends the Max-Age=0 cookie
    return RedirectResponse(
        "/login",
        status_code=303,
        headers={"Clear-Site-Data": '"cache", "cookies", "storage"'},
    )


@app_router.post("/app/lang")
def switch_lang(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    lang: Annotated[str, Form()],
    back: Annotated[str, Form()] = "/app/",
) -> Response:
    if lang not in {item.value for item in Lang}:
        raise HTTPException(status_code=422)
    deps = get_deps(request)
    prefs = deps.reader.account(user.id).prefs
    deps.account.set_preferences(user.id, replace(prefs, lang=Lang(lang)))
    return RedirectResponse(safe_next(back), status_code=303)


test_router = APIRouter()


@test_router.get("/auth/test-login", response_class=HTMLResponse)
def test_login_page(request: Request) -> HTMLResponse:
    users = getattr(get_deps(request).users, "users", {})
    return render(request, "pages/test_login.html", {"demo_users": list(users.values())})


@test_router.post("/auth/test-login")
def test_login(request: Request, user_id: Annotated[UUID, Form()]) -> Response:
    if get_deps(request).users.find_user(user_id) is None:
        raise HTTPException(status_code=404)
    request.state.web.login(user_id)
    return RedirectResponse("/app/", status_code=303)


dev_router = APIRouter()


# Dev-environment design D5: one click signs the browser in as a seeded dev identity, through the
# same `sign_in` as Google. `as` is a closed enum (422 otherwise); GET only, so no CSRF exemption.
@dev_router.get("/auth/dev-login")
async def dev_login(request: Request, as_: Annotated[DevSub, Query(alias="as")]) -> Response:
    who = DEV_IDENTITIES[as_]
    identity = GoogleIdentity(sub=who.sub, email=who.email, name=who.name, email_verified=True)
    return await _sign_in(request, identity, None)


def routers(config: WebConfig) -> list[APIRouter]:
    """The test login exists only with TUTOR_ENV=test; the dev login only with TUTOR_DEV_AUTH.
    Neither is registered otherwise, so the CSRF walker and the route sweep never see them."""
    return [
        router,
        app_router,
        *([test_router] if config.test_login else []),
        *([dev_router] if config.dev_auth else []),
    ]
