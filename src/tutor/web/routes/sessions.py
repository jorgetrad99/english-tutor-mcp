"""Sesiones: the filtered list and the detail of one session (spec 6.6)."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from tutor.domain.dashboard.types import Mode, SessionFilter, SessionStatus, User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.query import bounded_int, enum_or_none, parse_uuid
from tutor.web.views import is_htmx, render

router = APIRouter(dependencies=APP_ROUTER_DEPS)
PER_PAGE = 20
MAX_PAGE = 10_000
LISTED_STATUSES = frozenset({SessionStatus.CLOSED, SessionStatus.INCOMPLETE})


def page_url(f: SessionFilter, page: int) -> str:
    pairs = [(k, str(v)) for k, v in (("mode", f.mode), ("status", f.status)) if v is not None]
    return "/app/sessions?" + urlencode([*pairs, ("page", str(page))])


@router.get("/app/sessions", response_class=HTMLResponse)
def sessions_page(request: Request, user: Annotated[User, Depends(current_user)]) -> HTMLResponse:
    params = request.query_params
    status = enum_or_none(SessionStatus, params.get("status"))
    f = SessionFilter(
        mode=enum_or_none(Mode, params.get("mode")),
        status=status if status in LISTED_STATUSES else None,
    )
    page = bounded_int(params.get("page"), default=1, minimum=1, maximum=MAX_PAGE)
    result = get_deps(request).reader.sessions(user.id, f, page, PER_PAGE)
    ctx = {
        "result": result,
        "f": f,
        "first_url": page_url(f, 1),
        "prev_url": page_url(f, page - 1) if page > 1 else None,
        "next_url": page_url(f, page + 1) if result.has_next else None,
    }
    template = "partials/sessions_table.html" if is_htmx(request) else "pages/sessions.html"
    return render(request, template, ctx)


@router.get("/app/sessions/{session_id}", response_class=HTMLResponse)
def session_detail(
    session_id: str, request: Request, user: Annotated[User, Depends(current_user)]
) -> HTMLResponse:
    sid = parse_uuid(session_id)
    detail = get_deps(request).reader.session_detail(user.id, sid) if sid else None
    if detail is None:
        raise HTTPException(status_code=404)
    return render(request, "pages/session_detail.html", {"d": detail})
