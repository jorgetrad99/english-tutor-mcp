"""Materias, Materia and Nota (subjects spec 9; slice D plan rulings 53-56, 61, 62, 72, 73).

Read-only pages over the subjects port: the web computes nothing, it renders the services'
numbers. A foreign, missing or malformed id is 404. Instances appear only as the sandboxed
frame partial, whose src the template origin's signer gives (spec 10).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from tutor.domain.dashboard.types import User
from tutor.web.deps import APP_ROUTER_DEPS, current_user
from tutor.web.query import parse_uuid
from tutor.web.subjects import SUBJECTS_PATH, get_frame_url, get_subjects_port
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
    return render(request, "pages/subject.html", {"page": page, "progress": page.progress})


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
