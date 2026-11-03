"""save_notes, save_artifact, get_artifact, get_note and search_notes (subjects spec 4, 5).

One unit of work per call: a payload is stored whole or not at all. Every write records an
audit event with ids, enums and counts only. Learner text is returned as data.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeGuard
from uuid import UUID

from tutor.domain.html_guard import ARTIFACT_KINDS, ARTIFACT_TITLE_MAX, ArtifactKind, check_html
from tutor.domain.notes import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    QUERY_MAX,
    NoteInput,
    NotesErrors,
    Origin,
    SourceInput,
    idempotency_key,
    validate_notes,
)
from tutor.domain.subjects import collapse_name
from tutor.services.context import Services
from tutor.services.errors import ServiceError, field_error
from tutor.services.ports import NoteRow, UnitOfWork
from tutor.services.subject_views import (
    ArtifactRef,
    ArtifactSaved,
    ArtifactView,
    ExerciseView,
    NoteOutcome,
    NoteView,
    Outcome,
    PassageView,
    QuizItemView,
    SaveNotesResult,
    SearchHitView,
    SearchResult,
    SearchWhy,
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
    """Store 1-4 notes of one subject (spec 4.1, 4.2): `existing` when the idempotency key is
    known, `superseded` when the same source and lesson_ref hold another text, else `new`."""
    valid = validate_notes(source, notes, origin=origin)
    if isinstance(valid, NotesErrors):
        raise field_error(valid.code, valid.errors)
    now = svc.clock()
    with svc.uow(user_id) as uow:
        if uow.subjects.get(subject_id) is None:
            raise ServiceError("not_found", ("subject_id",))
        source_id = None
        if valid.source is not None:
            source_id = uow.notes.upsert_source(subject_id, valid.source, now).id
        outcomes: list[NoteOutcome] = []
        for note in valid.notes:
            key = idempotency_key(subject_id, source_id, note.lesson_ref, note.raw_text)
            existing = uow.notes.by_idempotency_key(key)
            if existing is not None:
                outcomes.append(NoteOutcome(existing.id, "existing"))
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
            outcome: Outcome = "new" if previous is None else "superseded"
            outcomes.append(NoteOutcome(row.id, outcome))
        meta: dict[str, Any] = {
            "subject_id": str(subject_id),
            "source_id": str(source_id) if source_id is not None else None,
            "origin": origin,
        }
        for kind in _OUTCOMES:
            meta[kind] = sum(1 for item in outcomes if item.outcome == kind)
        uow.audit.record("notes_saved", meta, now)
        return SaveNotesResult(source_id=source_id, notes=tuple(outcomes))


_OUTCOMES: tuple[Outcome, ...] = ("new", "existing", "superseded")


def _is_artifact_kind(value: str) -> TypeGuard[ArtifactKind]:
    return value in ARTIFACT_KINDS


def artifact_web_path(note_id: UUID, artifact_id: UUID) -> str:
    """The website route that serves the stored document (spec 9)."""
    return f"/app/notas/{note_id}/artefactos/{artifact_id}/raw"


def save_artifact(
    svc: Services, user_id: UUID, note_id: UUID, kind: str, title: str, html: str
) -> ArtifactSaved:
    """Store one HTML document for a note (spec 4.4). `check_html` decides; a rejected document
    is `validation_failed` on `html` and its first problem code goes to the audit log."""
    now = svc.clock()
    fields: list[str] = []
    if not _is_artifact_kind(kind):
        fields.append("artifact_kind")
    name = collapse_name(title)
    if not 1 <= len(name) <= ARTIFACT_TITLE_MAX:
        fields.append("title")
    problems = check_html(html)
    if problems:
        fields.append("html")
    with svc.uow(user_id) as uow:
        if uow.notes.get(note_id) is None:
            raise ServiceError("not_found", ("note_id",))
        if not fields and _is_artifact_kind(kind):
            row = uow.notes.add_artifact(note_id, kind, name, html, now)
            uow.audit.record(
                "artifact_saved",
                {
                    "artifact_id": str(row.id),
                    "note_id": str(note_id),
                    "artifact_kind": kind,
                    "bytes": row.bytes,
                },
                now,
            )
            return ArtifactSaved(artifact_id=row.id, web_path=artifact_web_path(note_id, row.id))
        if problems:
            uow.audit.record(
                "artifact_rejected",
                {"note_id": str(note_id), "problem": problems[0].code},
                now,
            )
    raise ServiceError("validation_failed", tuple(fields))


def get_artifact(svc: Services, user_id: UUID, artifact_id: UUID) -> ArtifactView:
    """The stored document as data (spec 4.4)."""
    with svc.uow(user_id) as uow:
        row = uow.notes.artifact(artifact_id)
    if row is None or row.html is None:
        raise ServiceError("not_found", ("artifact_id",))
    return ArtifactView(
        artifact_id=row.id, note_id=row.note_id, kind=row.kind, title=row.title, html=row.html
    )


def get_note(
    svc: Services,
    user_id: UUID,
    note_id: UUID,
    passage_from: int | None = None,
    passage_to: int | None = None,
) -> NoteView:
    """The note with its clean text (whole up to 12,000 characters, else `truncated`), its
    exercises, quiz items without answers, concepts and artifact references (spec 5.2). A
    passage window (1-based, inclusive, at most 10) reads part of a long note."""
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
        exercises = tuple(
            ExerciseView(
                exercise_id=e.id,
                name=e.name,
                instructions=e.instructions,
                kind=e.kind,
                tempo_bpm=e.tempo_bpm,
                duration_min=e.duration_min,
            )
            for e in uow.notes.exercises(note_id)
        )
        quiz_items = tuple(
            QuizItemView(
                question=q.question,
                options=q.options,
                explanation=q.explanation,
                concept=q.concept,
            )
            for q in uow.notes.quiz_items(note_id)
        )
        concepts = tuple(c.name for c in uow.notes.concepts(note_id))
        artifacts = tuple(
            ArtifactRef(artifact_id=a.id, kind=a.kind, title=a.title)
            for a in uow.notes.artifacts(note_id)
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
        clean_text=note.clean_text[:MAX_NOTE_TEXT_CHARS],
        truncated=len(note.clean_text) > MAX_NOTE_TEXT_CHARS,
        passage_count=count,
        passages=passages,
        exercises=exercises,
        quiz_items=quiz_items,
        concepts=concepts,
        artifacts=artifacts,
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
    """Deterministic search over the learner's notes (spec 5.1); the repository ranks."""
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
    return SearchResult(hits=tuple(hits))


def _source_title(uow: UnitOfWork, note: NoteRow) -> str | None:
    if note.source_id is None:
        return None
    source = uow.notes.source(note.source_id)
    return source.title if source is not None else None
