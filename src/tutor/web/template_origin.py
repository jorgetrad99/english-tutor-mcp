"""The template origin: a separate ASGI app that serves only the raw instance document.

Spec 9 (`/raw` bullet) and 10, binding (b) to (d); ADR 0005 decision 8. It answers on its own
host (the dispatcher in `tutor.app` routes by Host), sets and reads no cookies, accepts only a
signed frame token (`tutor.web.frames`), and is never wrapped by `SecurityHeadersMiddleware`:
its responses carry the template policy instead of the dashboard's. Every refusal is the same
404; there are no redirects and no other routes.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs
from uuid import UUID

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tutor.web.frames import RAW_PATH, FrameTokens
from tutor.web.security import template_frame_csp

RAW_ROUTE = re.compile(
    r"/app/instances/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/raw"
)
HTML_UTF8 = "text/html; charset=utf-8"  # ruling 74: the charset is named, never guessed
_HSTS = "max-age=31536000; includeSubDomains"

# (user_id, instance_id) -> the composed document, or None for a foreign or missing instance.
# It runs as that user under RLS (the subjects port's `instance_document`) in a worker thread.
InstanceDocument = Callable[[UUID, UUID], str | None]

log = logging.getLogger("tutor.web")


@dataclass(frozen=True)
class _Route:
    path: str  # what RequestLog logs: the template, never the path or the query


_RAW = _Route(RAW_PATH)


def template_headers(dashboard_origin: str) -> dict[str, str]:
    """The header set of every template-origin response (spec 9)."""
    return {
        "Content-Security-Policy": template_frame_csp(dashboard_origin),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "private, no-store",
        "Strict-Transport-Security": _HSTS,
    }


def raw_html_response(document: str, dashboard_origin: str) -> Response:
    """The 200 of the raw route: the document as UTF-8 HTML under the template policy."""
    return Response(
        content=document, media_type=HTML_UTF8, headers=template_headers(dashboard_origin)
    )


class TemplateHeaders:
    """Outermost layer of the template stack: every response on the template host, including
    the guards' 413 and 400, carries `template_headers` and never a cookie."""

    def __init__(self, app: ASGIApp, dashboard_origin: str) -> None:
        self.app = app
        self._headers = template_headers(dashboard_origin)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                del headers["set-cookie"]
                for name, value in self._headers.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class TemplateOriginApp:
    """`GET /app/instances/{uuid}/raw?t=<frame token>` and nothing else."""

    def __init__(
        self, tokens: FrameTokens, document: InstanceDocument | None, dashboard_origin: str
    ) -> None:
        self.tokens = tokens
        self.document = document
        self.dashboard_origin = dashboard_origin
        self._headers = template_headers(dashboard_origin)  # fails at startup on a bad origin

    def _answer(self, status: int, text: str) -> Response:
        return Response(
            content=text,
            status_code=status,
            media_type="text/plain; charset=utf-8",
            headers=self._headers,
        )

    async def _document(self, scope: Scope) -> str | None:
        if scope.get("method") != "GET" or self.document is None:
            return None
        match = RAW_ROUTE.fullmatch(scope.get("path", ""))
        if match is None:
            return None
        instance_id = UUID(match.group(1))
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        tokens = query.pop("t", [])
        if query or len(tokens) != 1:
            return None
        user_id = self.tokens.verify(tokens[0], instance_id)
        if user_id is None:
            return None
        scope["route"] = _RAW
        return await run_in_threadpool(self.document, user_id, instance_id)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1000})
            return
        if scope["type"] != "http":
            return
        try:
            document = await self._document(scope)
        except Exception as exc:  # never the message: it can carry learner text
            log.error("template document failed exc=%s", type(exc).__name__)
            response = self._answer(500, "Error")
        else:
            response = (
                self._answer(404, "Not found")
                if document is None
                else raw_html_response(document, self.dashboard_origin)
            )
        await response(scope, receive, send)
