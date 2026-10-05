"""Security headers and redirect hygiene (spec section 9.6)."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
    "form-action 'self' https://checkout.stripe.com https://billing.stripe.com "
    "https://accounts.google.com; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
_HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        versioned = path.startswith("/static/") and b"v=" in scope.get("query_string", b"")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["Strict-Transport-Security"] = _HSTS
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                if versioned:
                    headers["Cache-Control"] = "public, max-age=31536000, immutable"
                elif path.startswith("/static/") or path == "/sw.js":
                    headers["Cache-Control"] = "no-cache"
                else:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def safe_next(value: str | None) -> str:
    """Only same-site absolute paths survive; anything else goes to the dashboard."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")):
        return "/app/"
    return value
