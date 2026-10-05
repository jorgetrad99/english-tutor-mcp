"""Pages that need no login."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tutor.web.security import safe_next
from tutor.web.views import render

router = APIRouter()


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/app/", status_code=307)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    raw = request.query_params.get("next", "")
    return render(request, "pages/login.html", {"next": safe_next(raw) if raw else ""})


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return render(request, "pages/privacy.html")


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return render(request, "pages/terms.html")
