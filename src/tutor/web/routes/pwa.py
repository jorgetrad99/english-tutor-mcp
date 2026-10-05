"""Installable shell: web app manifest, service worker, offline page (spec section 8)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.types import Lang, User
from tutor.web.assets import AssetManifest
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.security import safe_next
from tutor.web.views import Views, is_htmx, render

router = APIRouter()
app_router = APIRouter(dependencies=APP_ROUTER_DEPS)

_PRECACHE_SUFFIXES = (".css", ".js", ".woff2", ".svg")
_PRECACHE_ICONS = frozenset({"icons/icon-192.png", "icons/icon-512.png"})


def precache_list(assets: AssetManifest) -> list[str]:
    files = sorted(
        f for f in assets.files() if f.endswith(_PRECACHE_SUFFIXES) or f in _PRECACHE_ICONS
    )
    return [assets.url(f) for f in files] + ["/offline"]


@router.get("/manifest.webmanifest")
async def manifest(request: Request) -> Response:
    assets: AssetManifest = request.app.state.assets
    body = {
        "id": "/app/",
        "name": "English Tutor",
        "short_name": "Tutor",
        "description": "Tu tutor de inglés para el trabajo: plan, progreso y glosario.",
        "lang": "es-MX",
        "dir": "ltr",
        "start_url": "/app/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "theme_color": "#11402c",
        "background_color": "#e3f4ea",
        "categories": ["education", "productivity"],
        "icons": [
            {
                "src": assets.url("icons/icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": assets.url("icons/icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": assets.url("icons/icon-maskable-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    return Response(json.dumps(body, ensure_ascii=False), media_type="application/manifest+json")


@router.get("/sw.js")
async def service_worker(request: Request) -> Response:
    assets: AssetManifest = request.app.state.assets
    views: Views = request.app.state.views
    script = (
        views.envs[Lang.ES_MX]
        .get_template("pwa/sw.js")
        .render(version=assets.version, precache=precache_list(assets))
    )
    return Response(script, media_type="text/javascript")


@router.get("/offline", response_class=HTMLResponse)
async def offline(request: Request) -> HTMLResponse:
    # No user dependency and an empty CSRF token: this page is cached on the device.
    return render(request, "pages/offline.html", {"csrf_token": ""})


@app_router.post("/app/install/dismiss")
def dismiss_install(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    back: Annotated[str, Form()] = "/app/",
) -> Response:
    deps = get_deps(request)
    deps.account.dismiss_install_prompt(user.id, deps.clock())
    if is_htmx(request):
        return HTMLResponse("")  # 200 so htmx swaps the card away; a 204 is not swapped
    return RedirectResponse(safe_next(back), status_code=303)
