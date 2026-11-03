"""save_notes, get_note and search_notes (subjects spec 4, 5, 15.9).

One unit of work per call: a payload is stored whole or not at all, its instances included.
Every write records an audit event with ids, enums and counts only. Learner text is returned
as data; an instance shows its prompt parameters only (plan ruling 25).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from tutor.domain.errors import FieldError
from tutor.domain.notes import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    QUERY_MAX,
    NoteInput,
    NotesErrors,
    Origin,
    SourceInput,
    ValidNote,
    idempotency_key,
    validate_notes,
)
from tutor.domain.templates import pool_size, public_params
from tutor.services.context import Services
from tutor.services.errors import ServiceError, field_error
from tutor.services.ports import NoteListRow, NoteRow, UnitOfWork
from tutor.services.subject_views import (
    NoteInstanceView,
    NoteOutcome,
    NoteView,
    Outcome,
    PassageView,
    SaveNotesResult,
    SearchHitView,
    SearchResult,
    SearchWhy,
)
from tutor.services.templates import (
    ResolvedInstance,
    raise_collected,
    resolve_instance,
    store_instance,
)

MAX_NOTE_TEXT_CHARS = 12_000  # get_note returns clean_text whole up to this (plan ruling 11)
MAX_PASSAGES_PER_CALL = 10


def save_notes(
    svc: Services,
    user_id: UUID,
    subject_id: UUID,
    source: SourceInput | None,
    notes: Sequence[NoteInput],
    *,
    origin: Origin = "mcp",
) -> SaveNotesResult:
    """Store 1-4 notes of one subject (spec 4.1, 4.2, 15.4): `existing` when the idempotency key
    is known, `superseded` when the same source and lesson_ref hold another text, else `new`.

    Every instance must use an active template of the subject (else `not_found` on its
    `template_id`) and parameters that fit it (else `validation_failed` on their paths); all
    are checked before anything is written (plan ruling 23). An `existing` note keeps the
    instances it has; the result lists every note's active instance ids."""
    valid = validate_notes(source, notes, origin=origin)
    if isinstance(valid, NotesErrors):
        raise field_error(valid.code, valid.errors)
    now = svc.clock()
    with svc.uow(user_id) as uow:
        if uow.subjects.get(subject_id) is None:
            raise ServiceError("not_found", ("subject_id",))
        resolved = _resolve_instances(uow, subject_id, notes, valid.notes)
        source_id = None
        if valid.source is not None:
            source_id = uow.notes.upsert_source(subject_id, valid.source, now).id
        outcomes: list[NoteOutcome] = []
        created = 0
        for index, note in enumerate(valid.notes):
            key = idempotency_key(subject_id, source_id, note.lesson_ref, note.raw_text)
            existing = uow.notes.by_idempotency_key(key)
            if existing is not None:
                kept = tuple(i.id for i in uow.instances.for_note(existing.id))
                outcomes.append(NoteOutcome(existing.id, "existing", kept))
                continue
            previous = None
            if note.lesson_ref is not None:
                previous = uow.notes.latest_by_lesson(subject_id, source_id, note.lesson_ref)
            row = uow.notes.insert(
                note,
                subject_id=subject_id,
                source_id=source_id,
                origin=origin,
                supersedes=previous.id if previous is not None else None,
                now=now,
            )
            for item in resolved[index]:
                _, instance_outcome = store_instance(uow, subject_id, row.id, item, "ingest", now)
                created += instance_outcome != "existing"
            ids = tuple(i.id for i in uow.instances.for_note(row.id))
            outcome: Outcome = "new" if previous is None else "superseded"
            outcomes.append(NoteOutcome(row.id, outcome, ids))
        meta: dict[str, Any] = {
            "subject_id": str(subject_id),
            "source_id": str(source_id) if source_id is not None else None,
            "origin": origin,
        }
        for kind in _OUTCOMES:
            meta[kind] = sum(1 for item in outcomes if item.outcome == kind)
        meta["instances"] = created
        uow.audit.record("notes_saved", meta, now)
        return SaveNotesResult(source_id=source_id, notes=tuple(outcomes))


_OUTCOMES: tuple[Outcome, ...] = ("new", "existing", "superseded")


def _resolve_instances(
    uow: UnitOfWork,
    subject_id: UUID,
    notes: Sequence[NoteInput],
    valid_notes: Sequence[ValidNote],
) -> list[list[ResolvedInstance]]:
    """Every note's instances checked against their templates, or the collected errors."""
    not_found: list[str] = []
    errors: list[FieldError] = []
    resolved: list[list[ResolvedInstance]] = []
    for n, note in enumerate(notes):
        items: list[ResolvedInstance] = []
        for i, data in enumerate(note.instances):
            path = f"notes[{n}].instances[{i}]"
            item = resolve_instance(
                uow, subject_id, data, path, not_found, errors, valid_notes[n].concepts
            )
            if item is not None:
                items.append(item)
        resolved.append(items)
    raise_collected(not_found, errors)
    return resolved


def get_note(
    svc: Services,
    user_id: UUID,
    note_id: UUID,
    passage_from: int | None = None,
    passage_to: int | None = None,
) -> NoteView:
    """The note with its clean text (whole up to 12,000 characters, else `truncated`), its
    concepts and its active instances with their prompt parameters only (spec 5.2, 15.9). A
    passage window (1-based, inclusive, at most 10) reads part of a long note."""
    return _note_view(svc, user_id, note_id, passage_from, passage_to, whole_text=False)


def get_whole_note(svc: Services, user_id: UUID, note_id: UUID) -> NoteView:
    """get_note with the clean text uncut, for the website's note page (slice D ruling 50)."""
    return _note_view(svc, user_id, note_id, None, None, whole_text=True)


def _note_view(
    svc: Services,
    user_id: UUID,
    note_id: UUID,
    passage_from: int | None,
    passage_to: int | None,
    *,
    whole_text: bool,
) -> NoteView:
    with svc.uow(user_id) as uow:
        note = uow.notes.get(note_id)
        if note is None:
            raise ServiceError("not_found", ("note_id",))
        count = uow.notes.passage_count(note_id)
        passages: tuple[PassageView, ...] = ()
        if passage_from is not None or passage_to is not None:
            start, stop = _window(passage_from, passage_to, count)
            passages = tuple(
                PassageView(seq=p.seq, text=p.text)
                for p in uow.notes.passages(note_id, start, stop)
            )
        source = uow.notes.source(note.source_id) if note.source_id is not None else None
        concepts = tuple(c.name for c in uow.notes.concepts(note_id))
        instances: list[NoteInstanceView] = []
        for row in uow.instances.for_note(note_id):
            template = uow.templates.get(row.template_id)
            if template is None:  # pragma: no cover - an instance's template always exists
                continue
            instances.append(
                NoteInstanceView(
                    instance_id=row.id,
                    seq=row.seq,
                    template_id=row.template_id,
                    purpose=row.purpose,
                    practice_kind=row.practice_kind,
                    grading_kind=row.grading_kind,
                    needs_view=template.spec.needs_view,
                    prompt_text=row.prompt_text,
                    concept=row.concept,
                    params=public_params(template.spec, row.params),
                    pool_entries=pool_size(template.spec, row.params),
                )
            )
    return NoteView(
        note_id=note.id,
        subject_id=note.subject_id,
        source_id=note.source_id,
        source_title=source.title if source is not None else None,
        title=note.title,
        lesson_ref=note.lesson_ref,
        position=note.position,
        origin=note.origin,
        supersedes_note_id=note.supersedes_note_id,
        created_at=note.created_at,
        updated_at=note.updated_at,
        clean_text=note.clean_text if whole_text else note.clean_text[:MAX_NOTE_TEXT_CHARS],
        truncated=not whole_text and len(note.clean_text) > MAX_NOTE_TEXT_CHARS,
        passage_count=count,
        passages=passages,
        concepts=concepts,
        instances=tuple(instances),
    )


def _window(passage_from: int | None, passage_to: int | None, count: int) -> tuple[int, int]:
    """A 1-based inclusive window of at most MAX_PASSAGES_PER_CALL passages; `passage_to`
    past the end is clamped, `passage_from` past the end is an error."""
    start = 1 if passage_from is None else passage_from
    if start < 1 or start > count:
        raise ServiceError("validation_failed", ("passage_from",))
    stop = start + MAX_PASSAGES_PER_CALL - 1 if passage_to is None else passage_to
    if stop < start or stop - start + 1 > MAX_PASSAGES_PER_CALL:
        raise ServiceError("validation_failed", ("passage_to",))
    return start, min(stop, count)


def search_notes(
    svc: Services,
    user_id: UUID,
    query: str,
    subject_id: UUID | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> SearchResult:
    """Deterministic search over the learner's notes (spec 5.1); the repository ranks. Each call
    records `notes_searched` with the subject filter, the hit count and whether a fuzzy path
    found any hit, never the query (spec 16.6, slice B ruling 69)."""
    text = query.strip()
    if not 1 <= len(text) <= QUERY_MAX:
        raise ServiceError("validation_failed", ("query",))
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ServiceError("validation_failed", ("limit",))
    with svc.uow(user_id) as uow:
        if subject_id is not None and uow.subjects.get(subject_id) is None:
            raise ServiceError("not_found", ("subject_id",))
        hits: list[SearchHitView] = []
        for hit in uow.notes.search(text, subject_id, limit):
            note = uow.notes.get(hit.note_id)
            if note is None:  # pragma: no cover - the repository only returns the user's notes
                continue
            hits.append(
                SearchHitView(
                    note_id=note.id,
                    subject_id=note.subject_id,
                    title=note.title,
                    lesson_ref=note.lesson_ref,
                    source_title=_source_title(uow, note),
                    excerpts=hit.excerpts,
                    why=SearchWhy(
                        passages=hit.passages,
                        concepts=hit.concepts,
                        depth=hit.depth,
                        fuzzy=hit.fuzzy,
                    ),
                )
            )
        uow.audit.record(
            "notes_searched",
            {
                "subject_id": str(subject_id) if subject_id is not None else None,
                "hits": len(hits),
                "fuzzy": any(h.why.fuzzy for h in hits),
            },
            svc.clock(),
        )
    return SearchResult(hits=tuple(hits))


def _source_title(uow: UnitOfWork, note: NoteRow) -> str | None:
    if note.source_id is None:
        return None
    source = uow.notes.source(note.source_id)
    return source.title if source is not None else None


def list_notes(svc: Services, user_id: UUID, subject_id: UUID) -> tuple[NoteListRow, ...]:
    """Every note of the subject, superseded ones included and flagged, in the repository's
    order (slice D ruling 56): metadata only, no text. A foreign or missing subject is
    `not_found`."""
    with svc.uow(user_id) as uow:
        if uow.subjects.get(subject_id) is None:
            raise ServiceError("not_found", ("subject_id",))
        return uow.notes.list_for_subject(subject_id)


WEB_SOURCE_KINDS = frozenset({"pdf", "text"})
_WEB_NOTE_PATH = "notes[0]."


def save_web_note(
    svc: Services,
    user_id: UUID,
    subject_id: UUID,
    source: SourceInput,
    title: str,
    lesson_ref: str | None,
    raw_text: str,
) -> NoteOutcome:
    """One note added on the website from an uploaded PDF or a pasted text (slice D ruling 57):
    `save_notes` with `origin = web`, so up to MAX_WEB_NOTE_CHARS, the same idempotency and
    supersede rules and one `notes_saved` event with counts only. No concepts, links or
    instances. The source is `{kind: pdf | text, title}`; another kind fails on `source.kind`.
    Field paths name the form's fields: `title`, `lesson_ref`, `raw_text`, `source.title`."""
    if source.kind not in WEB_SOURCE_KINDS:
        raise ServiceError("validation_failed", ("source.kind",))
    note = NoteInput(title=title, lesson_ref=lesson_ref, position=None, raw_text=raw_text)
    try:
        result = save_notes(svc, user_id, subject_id, source, [note], origin="web")
    except ServiceError as error:
        fields = tuple(field.removeprefix(_WEB_NOTE_PATH) for field in error.fields)
        raise ServiceError(error.code, fields) from None
    return result.notes[0]
