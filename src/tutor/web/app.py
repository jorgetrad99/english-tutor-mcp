"""FastAPI application for the dashboard."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from tutor.web.assets import AssetManifest
from tutor.web.config import WebConfig
from tutor.web.ports import WebDeps
from tutor.web.routes import all_routers
from tutor.web.security import SecurityHeadersMiddleware
from tutor.web.views import Views, is_htmx, render

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("tutor.web")


def create_app(deps: WebDeps, config: WebConfig) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps
    app.state.config = config
    app.state.assets = AssetManifest(STATIC_DIR)
    app.state.views = Views(app.state.assets)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in all_routers(config):
        app.include_router(router)
    _install_error_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        template = "partials/error.html" if is_htmx(request) else "pages/error.html"
        ctx = {"status": exc.status_code, "ref": ""}
        return render(request, template, ctx, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> Response:
        ref = uuid.uuid4().hex[:8]
        # Never log the traceback, message or args: they can carry learner text or secrets.
        log.error(
            "unhandled error ref=%s path=%s exc=%s", ref, request.url.path, type(exc).__name__
        )
        try:
            template = "partials/error.html" if is_htmx(request) else "pages/error.html"
            return render(request, template, {"status": 500, "ref": ref}, status_code=500)
        except Exception:  # rendering itself failed; never leak details
            return HTMLResponse(f"Error {ref}", status_code=500)
