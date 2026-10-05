"""Conectar: how to add the tutor to Claude, then a short wait for the first session."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.types import User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_config, get_deps
from tutor.web.query import bounded_int
from tutor.web.views import is_htmx, render

router = APIRouter(dependencies=APP_ROUTER_DEPS)
POLL_LIMIT = 60  # one check every 10 s: ten minutes, then a "check again" button


def connect_context(request: Request, user: User, n: int = 0) -> dict[str, Any]:
    connected = get_deps(request).reader.has_any_session(user.id)
    return {
        "mcp_url": get_config(request).mcp_url,
        "connected": connected,
        "n": n,
        "gave_up": not connected and n >= POLL_LIMIT,
    }


@router.get("/app/connect", response_class=HTMLResponse)
def connect_page(request: Request, user: Annotated[User, Depends(current_user)]) -> HTMLResponse:
    return render(request, "pages/connect.html", connect_context(request, user))


@router.get("/app/connect/status")
def connect_status(request: Request, user: Annotated[User, Depends(current_user)]) -> Response:
    if not is_htmx(request):
        return RedirectResponse("/app/connect", status_code=303)
    n = bounded_int(request.query_params.get("n"), default=0, minimum=0, maximum=POLL_LIMIT)
    return render(request, "partials/connect_status.html", connect_context(request, user, n))
