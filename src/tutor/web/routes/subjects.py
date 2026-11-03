"""Materias, Materia and Nota (subjects spec 9; slice D plan rulings 53-62, 72, 73).

Pages over the subjects port: the web computes nothing, it renders the services' numbers. A
foreign, missing or malformed id is 404, checked before any form field. Instances appear only
as the sandboxed frame partial, whose src the template origin's signer gives (spec 10).

The writes (a PDF upload, a pasted text, retiring a template) are sync routes, so FastAPI runs
them in a worker thread and the PDF parse never blocks the event loop. Their forms are read by
`upload_form` with the part size a 400,000-character paste needs. Nothing here logs a PDF's
text, a file name or any learner text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile

from tutor.domain.dashboard.types import User
from tutor.domain.notes import LESSON_REF_MAX, MAX_WEB_NOTE_CHARS, TITLE_MAX
from tutor.domain.subjects import collapse_name
from tutor.services.errors import ServiceError
from tutor.services.pdf_text import MAX_PDF_BYTES
from tutor.web.deps import APP_ROUTER_DEPS, FORM_PART_MAX_BYTES, current_user
from tutor.web.query import parse_uuid
from tutor.web.subjects import (
    SUBJECTS_PATH,
    PdfRefused,
    SubjectPage,
    SubjectsPort,
    WebNoteSaved,
    get_frame_url,
    get_subjects_port,
)
from tutor.web.views import is_htmx, render

router = APIRouter(dependencies=APP_ROUTER_DEPS)


@router.get(SUBJECTS_PATH, response_class=HTMLResponse)
def subjects_page(request: Request, user: Annotated[User, Depends(current_user)]) -> HTMLResponse:
    page = get_subjects_port(request).subjects(user.id)
    template = "partials/subjects_list.html" if is_htmx(request) else "pages/subjects.html"
    return render(request, template, {"page": page})


@router.get(SUBJECTS_PATH + "/{subject_id}", response_class=HTMLResponse)
def subject_page(
    subject_id: str, request: Request, user: Annotated[User, Depends(current_user)]
) -> HTMLResponse:
    sid = parse_uuid(subject_id)
    page = get_subjects_port(request).subject(user.id, sid) if sid else None
    if page is None:
        raise HTTPException(status_code=404)
    return render(request, "pages/subject.html", subject_ctx(page, None))


@router.get("/app/notes/{note_id}", response_class=HTMLResponse)
def note_page(
    note_id: str, request: Request, user: Annotated[User, Depends(current_user)]
) -> HTMLResponse:
    nid = parse_uuid(note_id)
    page = get_subjects_port(request).note(user.id, nid) if nid else None
    if page is None:
        raise HTTPException(status_code=404)
    frame_url = get_frame_url(request)
    frames = [(card, frame_url(user.id, card.id)) for card in page.instances]
    return render(request, "pages/note.html", {"page": page, "frames": frames})


# --- writes (slice D Tasks 4 and 5) --------------------------------------------------------------

UploadKind = Literal["pdf", "text"]


async def upload_form(request: Request) -> AsyncIterator[FormData]:
    """The request's form (already parsed by `require_csrf` when the token came as a field),
    closed after the response so a spooled file part never outlives the request."""
    form = await request.form(max_part_size=FORM_PART_MAX_BYTES)
    try:
        yield form
    finally:
        await form.close()


Form = Annotated[FormData, Depends(upload_form)]


# The caps the upload forms state (subjects spec 4.1); the services enforce them.
FORM_LIMITS = {
    "max_title": TITLE_MAX,
    "max_lesson_ref": LESSON_REF_MAX,
    "max_text": MAX_WEB_NOTE_CHARS,
}


def subject_ctx(page: SubjectPage, upload: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "page": page,
        "progress": page.progress,
        "subject_id": page.subject.id,
        "upload": upload,
        **FORM_LIMITS,
    }


async def owned_subject(
    subject_id: str, request: Request, user: Annotated[User, Depends(current_user)]
) -> tuple[SubjectsPort, SubjectPage]:
    """Ownership first: a foreign, missing or malformed subject is 404 before the route reads
    its form (a form `require_csrf` already parsed is closed before the 404)."""
    port = get_subjects_port(request)
    sid = parse_uuid(subject_id)
    page = await run_in_threadpool(port.subject, user.id, sid) if sid else None
    if page is None:
        await request.close()
        raise HTTPException(status_code=404)
    return port, page


# Declared before the form in each upload route, so FastAPI resolves it first.
Owned = Annotated[tuple[SubjectsPort, SubjectPage], Depends(owned_subject)]


def _field(form: FormData, name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


def _title_problem(title: str) -> str | None:
    """`required` or `too_long`, decided from the input (a ServiceError names fields only)."""
    text = collapse_name(title)
    if not text:
        return "required"
    return "too_long" if len(text) > TITLE_MAX else None


def default_title(filename: str | None) -> str:
    """The file name without its folder and extension, cut to TITLE_MAX characters."""
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return collapse_name(stem)[:TITLE_MAX]


def _field_errors(error: ServiceError, kind: UploadKind, values: dict[str, str]) -> dict[str, str]:
    """The service's field paths as the form's fields, each with its reason."""
    if error.code != "validation_failed":
        raise error
    errors: dict[str, str] = {}
    for field in error.fields:
        if field in ("title", "source.title"):
            errors["title"] = _title_problem(values["title"]) or "too_long"
        elif field == "lesson_ref":
            errors["lesson_ref"] = "too_long"
        elif field == "raw_text" and kind == "text":
            errors["text"] = "required" if not values["text"].strip() else "too_long"
        elif field == "raw_text":
            errors["file"] = "too_long"
        else:  # pragma: no cover - save_web_note names no other field
            raise error
    return errors


def _upload_answer(
    request: Request,
    page: SubjectPage,
    kind: UploadKind,
    values: dict[str, str],
    errors: dict[str, str],
    saved: WebNoteSaved | None,
) -> Response:
    """Saved: 303 to the note, or the result fragment for HTMX. Refused: 422 with the form
    redisplayed (the whole subject page, or the forms section for HTMX)."""
    if saved is not None and not is_htmx(request):
        return RedirectResponse(f"/app/notes/{saved.note_id}", status_code=303)
    upload = {"form": kind, "values": values, "errors": errors, "saved": saved}
    ctx = {"subject_id": page.subject.id, "upload": upload, **FORM_LIMITS}
    if saved is not None:
        return render(request, "pages/subject_upload_result.html", ctx)
    if is_htmx(request):
        return render(request, "partials/upload_forms.html", ctx, status_code=422)
    return render(request, "pages/subject.html", subject_ctx(page, upload), status_code=422)


def _not_found_or_fields(
    error: ServiceError, kind: UploadKind, values: dict[str, str]
) -> dict[str, str]:
    if error.code == "not_found":  # the subject went away between the two reads
        raise HTTPException(status_code=404) from None
    return _field_errors(error, kind, values)


@router.post(SUBJECTS_PATH + "/{subject_id}/pdf")
def subject_add_pdf(
    request: Request, user: Annotated[User, Depends(current_user)], owned: Owned, form: Form
) -> Response:
    port, page = owned
    upload = form.get("file")
    file = upload if isinstance(upload, UploadFile) and upload.filename else None
    title = collapse_name(_field(form, "title")) or default_title(file.filename if file else None)
    values = {"title": title}
    errors: dict[str, str] = {}
    problem = _title_problem(title)
    if problem:
        errors["title"] = problem
    # At most one byte over the cap is read: extract() refuses it before any parse.
    data = file.file.read(MAX_PDF_BYTES + 1) if file is not None else b""
    if not data:
        errors["file"] = "required"
    saved = None
    if not errors:
        try:
            saved = port.add_pdf(user.id, page.subject.id, title, data)
        except PdfRefused as refused:
            errors["file"] = refused.problem.value
        except ServiceError as error:
            errors = _not_found_or_fields(error, "pdf", values)
    return _upload_answer(request, page, "pdf", values, errors, saved)


@router.post(SUBJECTS_PATH + "/{subject_id}/text")
def subject_add_text(
    request: Request, user: Annotated[User, Depends(current_user)], owned: Owned, form: Form
) -> Response:
    port, page = owned
    values = {
        "title": _field(form, "title"),
        "lesson_ref": _field(form, "lesson_ref"),
        "text": _field(form, "text"),
    }
    saved = None
    errors: dict[str, str] = {}
    try:
        saved = port.add_text(
            user.id, page.subject.id, values["title"], values["lesson_ref"] or None, values["text"]
        )
    except ServiceError as error:
        errors = _not_found_or_fields(error, "text", values)
    return _upload_answer(request, page, "text", values, errors, saved)


@router.post("/app/templates/{template_id}/retire")
def template_retire(
    template_id: str, request: Request, user: Annotated[User, Depends(current_user)]
) -> Response:
    """Retire one template (spec 15.13 D9, ruling 59); its instances are untouched (amendment
    A56). A second POST is `already_retired` and answered as the first. HTMX gets the row
    without its button, no-JS a 303 back to the subject page."""
    port = get_subjects_port(request)
    tid = parse_uuid(template_id)
    ref = port.template(user.id, tid) if tid else None
    if ref is None or tid is None or port.retire_template(user.id, tid) is None:
        raise HTTPException(status_code=404)
    if not is_htmx(request):
        return RedirectResponse(f"{SUBJECTS_PATH}/{ref.subject_id}", status_code=303)
    return render(request, "partials/template_row.html", {"t": ref.card, "retired": True})
