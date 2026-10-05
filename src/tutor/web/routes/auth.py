"""Login with Google, logout, language switch and the test-only login."""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.types import Lang, User
from tutor.web.config import WebConfig
from tutor.web.deps import (
    APP_ROUTER_DEPS,
    current_user,
    get_config,
    get_deps,
    require_csrf_if_session,
)
from tutor.web.ports import LoginFailed
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
        return render(request, "pages/login.html", {"next": "", "error": True}, status_code=400)
    user = deps.users.sign_in(identity, deps.clock())
    if user.deletion_requested_at is not None:
        request.state.web.logout()  # drop the anonymous login session and its cookie
        return render(request, "pages/deletion_pending.html")
    target = safe_next(stored if isinstance(stored, str) else None)
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
async def switch_lang(
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


@app_router.get("/app/account", response_class=HTMLResponse)
async def account_stub(
    request: Request, user: Annotated[User, Depends(current_user)]
) -> HTMLResponse:
    # Temporary (V9): deleted by core Task 25; `csrf_of` loads it to read the CSRF meta tag.
    return render(request, "layouts/app.html", {"active_nav": "account"})


test_router = APIRouter()


@test_router.get("/auth/test-login", response_class=HTMLResponse)
async def test_login_page(request: Request) -> HTMLResponse:
    users = getattr(get_deps(request).users, "users", {})
    return render(request, "pages/test_login.html", {"demo_users": list(users.values())})


@test_router.post("/auth/test-login")
async def test_login(request: Request, user_id: Annotated[UUID, Form()]) -> Response:
    if get_deps(request).users.find_user(user_id) is None:
        raise HTTPException(status_code=404)
    request.state.web.login(user_id)
    return RedirectResponse("/app/", status_code=303)


def routers(config: WebConfig) -> list[APIRouter]:
    """The test login exists only when the config allows it (TUTOR_ENV=test)."""
    return [router, app_router, *([test_router] if config.test_login else [])]
