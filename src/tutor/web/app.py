"""FastAPI application for the dashboard."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
    # Added first = innermost. Security headers are outermost so even error pages get them.
    app.add_middleware(ErrorGuardMiddleware)
    host = urlsplit(config.base_url).hostname or "localhost"
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host])
    app.add_middleware(SecurityHeadersMiddleware, assets=app.state.assets)
    return app


def _route_label(scope: Scope) -> str:
    """The matched route template, never the decoded path (no log forging)."""
    route = scope.get("route")
    return str(getattr(route, "path", None) or "unmatched")


class ErrorGuardMiddleware:
    """Turns an unhandled exception into the friendly 500 page inside the header layer.

    Starlette would hand an `Exception` handler to its outermost ServerErrorMiddleware,
    which skips our headers and re-raises into the server log with a traceback.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            ref = uuid.uuid4().hex[:8]
            # Never log the traceback, message or args: they can carry learner text or secrets.
            log.error(
                "unhandled error ref=%s route=%s exc=%s",
                ref,
                _route_label(scope),
                type(exc).__name__,
            )
            if started:
                return
            request = Request(scope, receive)
            try:
                template = "partials/error.html" if is_htmx(request) else "pages/error.html"
                response: Response = render(
                    request, template, {"status": 500, "ref": ref}, status_code=500
                )
            except Exception:  # rendering itself failed; never leak details
                response = HTMLResponse(f"Error {ref}", status_code=500)
            await response(scope, receive, send)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        template = "partials/error.html" if is_htmx(request) else "pages/error.html"
        ctx = {"status": exc.status_code, "ref": ""}
        return render(request, template, ctx, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError) -> Response:
        # The detail echoes the offending input; the page deliberately shows none of it.
        template = "partials/error.html" if is_htmx(request) else "pages/error.html"
        return render(request, template, {"status": 422, "ref": ""}, status_code=422)
