"""Security headers and redirect hygiene (spec section 9.6)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tutor.web.assets import AssetManifest

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
    "form-action 'self' https://accounts.google.com; frame-ancestors 'none'; base-uri 'none'; "
    "object-src 'none'"
)
MAX_NEXT_LENGTH = 512  # bounds what an anonymous login session can store
_HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware:
    """Outermost layer: every response, including error pages, gets the same headers."""

    def __init__(self, app: ASGIApp, assets: AssetManifest) -> None:
        self.app = app
        self.assets = assets

    def _is_versioned(self, path: str, query: bytes) -> bool:
        if not path.startswith("/static/"):
            return False
        expected = self.assets.hash_of(path.removeprefix("/static/"))
        given = parse_qs(query.decode("latin-1")).get("v", [])
        return expected is not None and given == [expected]

    def _cache_control(self, scope: Scope, status: int) -> str:
        if not (200 <= status < 300 or status == 304):
            return "no-store"
        path: str = scope["path"]
        if self._is_versioned(path, scope.get("query_string", b"")):
            return "public, max-age=31536000, immutable"
        if path.startswith("/static/") or path == "/sw.js":
            return "no-cache"
        return "no-store"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["Strict-Transport-Security"] = _HSTS
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Cross-Origin-Resource-Policy"] = "same-origin"
                headers["Cache-Control"] = self._cache_control(scope, message["status"])
            await send(message)

        await self.app(scope, receive, send_with_headers)


def safe_next(value: str | None) -> str:
    """Only same-site absolute paths survive; anything else goes to the dashboard."""
    if (
        not value
        or len(value) > MAX_NEXT_LENGTH
        or any(ord(c) < 0x20 or ord(c) == 0x7F or c == "\\" for c in value)
    ):
        return "/app/"
    if value.startswith("//"):
        return "/app/"
    try:
        parts = urlsplit(value)
    except ValueError:  # e.g. an unbalanced "[" in the host part
        return "/app/"
    if parts.scheme or parts.netloc or not parts.path.startswith("/"):
        return "/app/"
    return value
