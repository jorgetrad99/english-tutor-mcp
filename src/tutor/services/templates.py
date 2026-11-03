"""get_template_guide, list_templates, save_template, create_instance and get_instance_view
(subjects spec 15; plan rulings 21-29).

One unit of work per call. The server validates everything (the template's contract and HTML,
an instance's parameters against its template), derives the practice kind, and sends a client
only the public payload: prompt parameters and a preview draw, never an answer key, a reveal or
a pool (ruling 25). Audit events carry ids and enums only. Template and instance text is data.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from tutor.domain.errors import FieldError
from tutor.domain.html_guard import check_template_html
from tutor.domain.notes import ConceptName, InstanceInput, name_norm, validate_instance
from tutor.domain.templates import (
    TEMPLATE_GUIDE_VERSION,
    GuideSection,
    Origin,
    TemplateInput,
    compose_document,
    content_key,
    draw,
    encode_payload,
    public_payload,
    render_text,
    seed_for,
    spec_hash,
    validate_params,
    validate_template,
)
from tutor.services.context import Services
from tutor.services.errors import ServiceError, field_error
from tutor.services.ports import InstanceRow, TemplateRow, UnitOfWork
from tutor.services.subject_views import (
    GuideView,
    InstanceOutcome,
    InstanceSaved,
    InstanceView,
    TemplateOutcome,
    TemplateSaved,
    TemplatesView,
    TemplateView,
)
from tutor.services.template_files import bridge, guide_section, inline_guide


def get_template_guide(section: GuideSection = "contract") -> GuideView:
    """One section of the server's guide (spec 15.6): server text, no learner data."""
    return GuideView(
        guide_version=TEMPLATE_GUIDE_VERSION, section=section, text=guide_section(section)
    )


def _template_view(row: TemplateRow, *, with_html: bool) -> TemplateView:
    spec = row.spec
    return TemplateView(
        template_id=row.id,
        name=spec.name,
        description=spec.description,
        purpose=spec.purpose,
        practice_kind=spec.practice_kind,
        grading_kind=spec.grading_kind,
        text_match=spec.text_match,
        numeric_tolerance=spec.numeric_tolerance,
        pool_draw=spec.pool_draw,
        pool_count=spec.pool_count,
        needs_view=spec.needs_view,
        params=spec.params,
        options_param=spec.options_param,
        status=row.status,
        guide_version=row.guide_version,
        supersedes_template_id=row.supersedes_template_id,
        html=row.html if with_html else None,
    )


def list_templates(
    svc: Services,
    user_id: UUID,
    subject_id: UUID,
    template_id: UUID | None = None,
    include_html: bool = False,
) -> TemplatesView:
    """The subject's active templates (spec 15.9), or one template of the subject in any status
    (with its HTML when asked). A subject without an active template also gets the inline guide
    (decision path and contract)."""
    if include_html and template_id is None:
        raise ServiceError("validation_failed", ("include_html",))
    with svc.uow(user_id) as uow:
        if uow.subjects.get(subject_id) is None:
            raise ServiceError("not_found", ("subject_id",))
        active = uow.templates.list_active(subject_id)
        if template_id is None:
            views = tuple(_template_view(row, with_html=False) for row in active)
        else:
            row = uow.templates.get(template_id)
            if row is None or row.subject_id != subject_id:
                raise ServiceError("not_found", ("template_id",))
            views = (_template_view(row, with_html=include_html),)
    return TemplatesView(
        guide_version=TEMPLATE_GUIDE_VERSION,
        templates=views,
        guide=None if active else inline_guide(),
    )


def save_template(
    svc: Services,
    user_id: UUID,
    subject_id: UUID,
    data: TemplateInput,
    html: str,
    guide_version: int,
    replaces_template_id: UUID | None = None,
) -> TemplateSaved:
    """Validate and store a template the learner approved (spec 15.3): `existing` when the
    subject already has this exact spec and HTML active, `superseded` when it replaces
    `replaces_template_id` (which is retired), else `new`. Every bad field, the HTML included,
    is reported at once as `validation_failed`; a name taken by another active template of the
    subject is `validation_failed` on `name`."""
    checked = validate_template(data, guide_version)
    errors: list[FieldError] = list(checked) if isinstance(checked, tuple) else []
    if check_template_html(html):
        errors.append(FieldError("html", "invalid"))
    if errors or isinstance(checked, tuple):
        raise field_error("validation_failed", errors)
    spec = checked
    html_sha = _sha256(html)
    now = svc.clock()
    with svc.uow(user_id) as uow:
        if uow.subjects.get(subject_id) is None:
            raise ServiceError("not_found", ("subject_id",))
        replaced = None
        if replaces_template_id is not None:
            replaced = uow.templates.get(replaces_template_id)
            if replaced is None or replaced.subject_id != subject_id:
                raise ServiceError("not_found", ("replaces_template_id",))
        same = uow.templates.find_same(subject_id, spec_hash(spec), html_sha)
        outcome: TemplateOutcome
        if same is not None:
            row, outcome = same, "existing"
        else:
            holder = uow.templates.active_by_name(subject_id, name_norm(spec.name))
            if holder is not None and holder.id != replaces_template_id:
                raise ServiceError("validation_failed", ("name",))
            row = uow.templates.create(
                subject_id, spec, html, guide_version, now, supersedes=replaces_template_id
            )
            outcome = "new" if replaced is None else "superseded"
        _audit_template(uow, row, outcome, now=now)
        return TemplateSaved(
            template_id=row.id, practice_kind=row.spec.practice_kind, outcome=outcome
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit_template(
    uow: UnitOfWork, row: TemplateRow, outcome: TemplateOutcome, *, now: datetime
) -> None:
    uow.audit.record(
        "template_saved",
        {
            "template_id": str(row.id),
            "subject_id": str(row.subject_id),
            "template_outcome": outcome,
            "grading_kind": row.spec.grading_kind,
            "pool_draw": row.spec.pool_draw,
            "bytes": row.bytes,
            "guide_version": row.guide_version,
        },
        now,
    )


@dataclass(frozen=True, slots=True)
class ResolvedInstance:
    """An instance checked against its template, ready for InstanceRepo.insert."""

    template: TemplateRow
    params: dict[str, object]
    prompt_text: str
    concept_norm: str | None  # an existing concept, or one of the note's (plan ruling 30)
    replaces: UUID | None


def resolve_instance(
    uow: UnitOfWork,
    subject_id: UUID,
    data: InstanceInput,
    path: str,
    not_found: list[str],
    errors: list[FieldError],
    note_concepts: Sequence[ConceptName] | None = None,
) -> ResolvedInstance | None:
    """Resolve the template (active, of the subject), the replaced instance (of the subject)
    and the concept, then check the parameters (ruling 23). Unknown ids and concepts go to
    `not_found`, bad fields to `errors`, both with paths under `path`.

    The concept must be an existing concept of the subject or, from save_notes, one of the
    note's `concepts` sent in the same call; an instance without one, on a note with exactly
    one concept, gets that concept. An instance never creates a concept (plan ruling 30)."""
    at = f"{path}." if path else ""
    shape: list[FieldError] = []
    valid = validate_instance(data, path, shape)
    errors.extend(shape)
    if valid is None or shape:
        return None
    template = uow.templates.get(valid.template_id)
    if template is None or template.subject_id != subject_id or template.status != "active":
        not_found.append(f"{at}template_id")
        return None
    if valid.replaces_instance_id is not None:
        replaced = uow.instances.get(valid.replaces_instance_id)
        if replaced is None or replaced.subject_id != subject_id:
            not_found.append(f"{at}replaces_instance_id")
            return None
    own = {c.name_norm for c in note_concepts or ()}
    concept_norm = None
    if valid.concept is not None:
        concept_norm = valid.concept.name_norm
        known = concept_norm in own or uow.notes.concept_by_norm(subject_id, concept_norm)
        if not known:
            not_found.append(f"{at}concept")
            return None
    elif note_concepts is not None and len(note_concepts) == 1:
        concept_norm = note_concepts[0].name_norm
    params = validate_params(template.spec, valid.params, path=path)
    if isinstance(params, tuple):
        errors.extend(params)
        return None
    return ResolvedInstance(
        template, params, valid.prompt_text, concept_norm, valid.replaces_instance_id
    )


def store_instance(
    uow: UnitOfWork,
    subject_id: UUID,
    note_id: UUID | None,
    resolved: ResolvedInstance,
    origin: Origin,
    now: datetime,
) -> tuple[InstanceRow, InstanceOutcome]:
    """Insert (or find) the instance and audit it; `superseded` when it replaced another."""
    key = content_key(
        subject_id, resolved.template.id, resolved.params, resolved.prompt_text, note_id
    )
    concept_id = None
    if resolved.concept_norm is not None:  # stored by now: checked, or the note's own concept
        concept = uow.notes.concept_by_norm(subject_id, resolved.concept_norm)
        concept_id = concept.id if concept is not None else None
    row, created = uow.instances.insert(
        subject_id=subject_id,
        note_id=note_id,
        template=resolved.template,
        params=resolved.params,
        prompt_text=resolved.prompt_text,
        concept_id=concept_id,
        origin=origin,
        key=key,
        now=now,
        replaces=resolved.replaces,
    )
    outcome: InstanceOutcome = (
        "existing" if not created else ("superseded" if resolved.replaces else "new")
    )
    uow.audit.record(
        "instance_created",
        {
            "instance_id": str(row.id),
            "subject_id": str(subject_id),
            "note_id": str(note_id) if note_id is not None else None,
            "origin": origin,
            "grading_kind": row.grading_kind,
            "instance_outcome": outcome,
        },
        now,
    )
    return row, outcome


def preview(template: TemplateRow, instance: InstanceRow) -> tuple[str, str]:
    """The composed document (artifact bridge) and the text view of an instance, with the
    preview draw seeded by the instance id (ruling 26): never stored, never graded."""
    if template.html is None:  # pragma: no cover - get() always returns the html
        raise RuntimeError("template html missing")
    spec = template.spec
    shown = draw(spec, instance.params, seed_for(instance.id))
    payload = public_payload(instance.id, spec, instance.params, shown)
    document = compose_document(template.html, encode_payload(payload), bridge("artifact"))
    return document, render_text(spec, instance.params, shown, instance.prompt_text)


def raise_collected(not_found: Sequence[str], errors: Sequence[FieldError]) -> None:
    """`not_found` first (an id the learner does not have), then `validation_failed`."""
    if not_found:
        raise ServiceError("not_found", tuple(dict.fromkeys(not_found)))
    if errors:
        raise field_error("validation_failed", errors)


def create_instance(
    svc: Services,
    user_id: UUID,
    subject_id: UUID,
    data: InstanceInput,
    note_id: UUID | None = None,
) -> InstanceSaved:
    """Store one instance of a subject's template outside ingest (spec 15.9), optionally on a
    note of the same subject, and return its view. Not graded."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        if uow.subjects.get(subject_id) is None:
            raise ServiceError("not_found", ("subject_id",))
        if note_id is not None:
            note = uow.notes.get(note_id)
            if note is None or note.subject_id != subject_id:
                raise ServiceError("not_found", ("note_id",))
        not_found: list[str] = []
        errors: list[FieldError] = []
        resolved = resolve_instance(uow, subject_id, data, "", not_found, errors)
        raise_collected(not_found, errors)
        if resolved is None:  # pragma: no cover - raise_collected raised
            raise ServiceError("validation_failed")
        row, outcome = store_instance(uow, subject_id, note_id, resolved, "class", now)
        document, text = preview(resolved.template, row)
        return InstanceSaved(
            instance_id=row.id, outcome=outcome, purpose=row.purpose, document=document, text=text
        )


def get_instance_view(svc: Services, user_id: UUID, instance_id: UUID) -> InstanceView:
    """The instance's composed document and text view, with a preview draw (spec 15.9)."""
    with svc.uow(user_id) as uow:
        row = uow.instances.get(instance_id)
        template = uow.templates.get(row.template_id) if row is not None else None
    if row is None or template is None:
        raise ServiceError("not_found", ("instance_id",))
    document, text = preview(template, row)
    return InstanceView(
        instance_id=row.id, purpose=row.purpose, status=row.status, document=document, text=text
    )
