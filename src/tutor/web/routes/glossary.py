"""Glosario: filters, inline edit of meaning and context, CSV export (section 10)."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import QueryParams

from tutor.domain.dashboard.glossary import (
    CONTEXT_MAX,
    MEANING_MAX,
    TextError,
    clean_user_text,
    glossary_csv,
)
from tutor.domain.dashboard.types import (
    DueFilter,
    GlossaryFilter,
    GlossaryKind,
    GlossaryRow,
    GlossaryStatus,
    User,
)
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.query import enum_or_none, parse_uuid
from tutor.web.views import is_htmx, render, today_for

router = APIRouter(dependencies=APP_ROUTER_DEPS)
Q_MAX = 100


def glossary_filter(params: QueryParams) -> GlossaryFilter:
    return GlossaryFilter(
        kind=enum_or_none(GlossaryKind, params.get("kind")),
        domain=params.get("domain") or None,
        status=enum_or_none(GlossaryStatus, params.get("status")),
        due=enum_or_none(DueFilter, params.get("due")),
        q=(params.get("q") or "")[:Q_MAX],
    )


def filter_query(f: GlossaryFilter) -> str:
    pairs = (
        ("kind", f.kind),
        ("domain", f.domain),
        ("status", f.status),
        ("due", f.due),
        ("q", f.q),
    )
    return urlencode([(k, str(v)) for k, v in pairs if v])


def _find(request: Request, user: User, item_id: str) -> GlossaryRow:
    uid = parse_uuid(item_id)
    if uid is not None:
        rows = get_deps(request).reader.glossary(user.id, GlossaryFilter(), today_for(request))
        for row in rows:
            if row.id == uid:
                return row
    raise HTTPException(status_code=404)


def _edit_ctx(
    row: GlossaryRow, meaning: str, context: str, errors: dict[str, str]
) -> dict[str, Any]:
    return {
        "row": row,
        "values": {"meaning": meaning, "context_sentence": context},
        "errors": errors,
        "meaning_max": MEANING_MAX,
        "context_max": CONTEXT_MAX,
    }


@router.get("/app/glossary", response_class=HTMLResponse)
def glossary_page(request: Request, user: Annotated[User, Depends(current_user)]) -> HTMLResponse:
    deps = get_deps(request)
    f = glossary_filter(request.query_params)
    query = filter_query(f)
    ctx = {
        "rows": deps.reader.glossary(user.id, f, today_for(request)),
        "f": f,
        "domains": deps.reader.glossary_domains(user.id),
        "csv_url": "/app/glossary.csv" + (f"?{query}" if query else ""),
    }
    template = "partials/glossary_table.html" if is_htmx(request) else "pages/glossary.html"
    return render(request, template, ctx)


@router.get("/app/glossary.csv")
def glossary_export(request: Request, user: Annotated[User, Depends(current_user)]) -> Response:
    rows = get_deps(request).reader.glossary(
        user.id, glossary_filter(request.query_params), today_for(request)
    )
    return Response(
        glossary_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="glosario.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/app/glossary/{item_id}/edit", response_class=HTMLResponse)
def glossary_edit_form(
    item_id: str, request: Request, user: Annotated[User, Depends(current_user)]
) -> HTMLResponse:
    row = _find(request, user, item_id)
    template = "partials/glossary_edit.html" if is_htmx(request) else "pages/glossary_edit.html"
    return render(request, template, _edit_ctx(row, row.meaning, row.context_sentence, {}))


@router.get("/app/glossary/{item_id}/row")
def glossary_row(
    item_id: str, request: Request, user: Annotated[User, Depends(current_user)]
) -> Response:
    row = _find(request, user, item_id)
    if not is_htmx(request):
        return RedirectResponse("/app/glossary", status_code=303)
    return render(request, "partials/glossary_row.html", {"row": row})


@router.post("/app/glossary/{item_id}")
def glossary_save(
    item_id: str,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    meaning: Annotated[str, Form()] = "",
    context_sentence: Annotated[str, Form()] = "",
) -> Response:
    row = _find(request, user, item_id)  # ownership first: 404 before any validation detail
    errors: dict[str, str] = {}
    clean_meaning = clean_context = ""
    try:
        clean_meaning = clean_user_text(meaning, max_len=MEANING_MAX, required=False)
    except TextError as exc:
        errors["meaning"] = exc.code
    try:
        clean_context = clean_user_text(context_sentence, max_len=CONTEXT_MAX, required=True)
    except TextError as exc:
        errors["context_sentence"] = exc.code
    if errors:
        template = "partials/glossary_edit.html" if is_htmx(request) else "pages/glossary_edit.html"
        ctx = _edit_ctx(row, meaning, context_sentence, errors)
        return render(request, template, ctx, status_code=422)
    updated = get_deps(request).glossary.update_glossary_text(
        user.id, row.id, clean_meaning, clean_context
    )
    if updated is None:
        raise HTTPException(status_code=404)
    if not is_htmx(request):
        return RedirectResponse("/app/glossary", status_code=303)
    return render(request, "partials/glossary_saved.html", {"row": updated})
