"""How the web reaches the subjects use cases (subjects spec 9; slice D plan rulings 50, 55, 56,
72).

One port on `app.state.subjects`, as the Perfil port: `ServicesSubjects` over the Postgres
unit of work in production, `MemorySubjects` over the in-memory store for tests and
`just dashboard-demo`. Pages never import a repository; every method takes the session's
user, and a foreign or missing id gives None (the route answers 404). Learner text travels in
the page dataclasses as data. The instance frame's URL comes from `app.state.frame_url`
(spec 10: a separate template origin with a signed token, built elsewhere).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from fastapi import FastAPI, Request

from tutor.domain.notes import SourceKind
from tutor.domain.subject_metrics import SubjectProgress
from tutor.domain.subjects import SubjectStatus
from tutor.domain.templates import GradingKind, PracticeKind, TemplatePurpose
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.memory import MemoryStore, UserRecord, memory_uow
from tutor.services.notes import get_whole_note, list_notes
from tutor.services.ports import NoteListRow
from tutor.services.practice import history
from tutor.services.progress import subject_progress
from tutor.services.subject_views import HistoryRow, NoteView, PlanStatus, SubjectView
from tutor.services.subjects import get_subjects
from tutor.services.templates import instance_counts, list_templates, web_instance_document
from tutor.web.profile import iana_zones

SUBJECTS_PATH = "/app/subjects"

RetireOutcome = Literal["retired", "already_retired"]
FrameUrl = Callable[[UUID, UUID], str]  # (user_id, instance_id) -> the frame's src


@dataclass(frozen=True, slots=True)
class SubjectCard:
    id: UUID
    name: str
    status: SubjectStatus
    sessions_per_week: int
    plan: PlanStatus | None


@dataclass(frozen=True, slots=True)
class SubjectsPage:
    subjects: tuple[SubjectCard, ...]


@dataclass(frozen=True, slots=True)
class SourceGroup:
    """The notes of one source (or of none), each superseded note right after the note that
    replaced it."""

    source_id: UUID | None
    title: str | None
    kind: SourceKind | None
    notes: tuple[NoteListRow, ...]


@dataclass(frozen=True, slots=True)
class TemplateCard:
    id: UUID
    name: str
    description: str | None
    purpose: TemplatePurpose
    grading_kind: GradingKind
    practice_kind: PracticeKind | None
    active_instances: int


@dataclass(frozen=True, slots=True)
class SubjectPage:
    subject: SubjectView
    sources: tuple[SourceGroup, ...]
    templates: tuple[TemplateCard, ...]
    history: tuple[HistoryRow, ...]
    progress: SubjectProgress


@dataclass(frozen=True, slots=True)
class InstanceCard:
    id: UUID
    template_name: str
    purpose: TemplatePurpose
    prompt_text: str


@dataclass(frozen=True, slots=True)
class NotePage:
    note: NoteView
    instances: tuple[InstanceCard, ...]


@dataclass(frozen=True, slots=True)
class WebNoteSaved:
    note_id: UUID
    outcome: Literal["new", "existing", "superseded"]


class SubjectsPort(Protocol):
    def subjects(self, user_id: UUID) -> SubjectsPage: ...

    def subject(self, user_id: UUID, subject_id: UUID) -> SubjectPage | None: ...

    def note(self, user_id: UUID, note_id: UUID) -> NotePage | None: ...

    def instance_document(self, user_id: UUID, instance_id: UUID) -> str | None: ...

    def add_pdf(self, user_id: UUID, subject_id: UUID, title: str, data: bytes) -> WebNoteSaved:
        """Raises ServiceError with field codes."""
        ...

    def add_text(
        self, user_id: UUID, subject_id: UUID, title: str, lesson_ref: str | None, text: str
    ) -> WebNoteSaved:
        """Raises ServiceError with field codes."""
        ...

    def retire_template(self, user_id: UUID, template_id: UUID) -> RetireOutcome | None: ...


def group_by_source(rows: tuple[NoteListRow, ...]) -> tuple[SourceGroup, ...]:
    """Consecutive rows of one source become a group (the repository orders by source); inside
    a group each superseded note follows the note that replaced it."""
    groups: list[tuple[NoteListRow, list[NoteListRow]]] = []
    for row in rows:
        if not groups or groups[-1][0].source_id != row.source_id:
            groups.append((row, []))
        groups[-1][1].append(row)
    return tuple(
        SourceGroup(
            source_id=first.source_id,
            title=first.source_title,
            kind=first.source_kind,
            notes=_chained(members),
        )
        for first, members in groups
    )


def _chained(members: list[NoteListRow]) -> tuple[NoteListRow, ...]:
    by_replacer: dict[UUID, NoteListRow] = {}
    for row in members:
        if row.supersedes_note_id is not None:
            older = next((m for m in members if m.id == row.supersedes_note_id), None)
            if older is not None:
                by_replacer[row.id] = older
    under_replacement = {older.id for older in by_replacer.values()}
    placed: set[UUID] = set()
    ordered: list[NoteListRow] = []
    for row in members:
        if row.id in under_replacement:
            continue  # shown right after the note that replaced it
        current: NoteListRow | None = row
        while current is not None and current.id not in placed:
            ordered.append(current)
            placed.add(current.id)
            current = by_replacer.get(current.id)
    ordered.extend(m for m in members if m.id not in placed)
    return tuple(ordered)


class ServicesSubjects:
    """Production: the subjects use cases over the Postgres unit of work."""

    def __init__(self, svc: Services) -> None:
        self._svc = svc

    def subjects(self, user_id: UUID) -> SubjectsPage:
        view = get_subjects(self._svc, user_id)
        return SubjectsPage(
            subjects=tuple(
                SubjectCard(
                    id=s.id,
                    name=s.subject.name,
                    status=s.subject.status,
                    sessions_per_week=s.subject.sessions_per_week,
                    plan=s.plan_status,
                )
                for s in view.subjects
            )
        )

    def subject(self, user_id: UUID, subject_id: UUID) -> SubjectPage | None:
        found = next(
            (s for s in get_subjects(self._svc, user_id).subjects if s.id == subject_id), None
        )
        if found is None:
            return None
        try:
            notes = list_notes(self._svc, user_id, subject_id)
            templates = list_templates(self._svc, user_id, subject_id).templates
            counts = instance_counts(self._svc, user_id, subject_id)
            rows = history(self._svc, user_id, subject_id)
            progress = subject_progress(self._svc, user_id, subject_id)
        except ServiceError as exc:  # pragma: no cover - the subject was read just above
            if exc.code == "not_found":
                return None
            raise
        return SubjectPage(
            subject=found,
            sources=group_by_source(notes),
            templates=tuple(
                TemplateCard(
                    id=t.template_id,
                    name=t.name,
                    description=t.description,
                    purpose=t.purpose,
                    grading_kind=t.grading_kind,
                    practice_kind=t.practice_kind,
                    active_instances=counts.get(t.template_id, 0),
                )
                for t in templates
            ),
            history=rows,
            progress=progress,
        )

    def note(self, user_id: UUID, note_id: UUID) -> NotePage | None:
        try:
            note = get_whole_note(self._svc, user_id, note_id)
        except ServiceError as exc:
            if exc.code == "not_found":
                return None
            raise
        names: dict[UUID, str] = {}
        for item in note.instances:
            if item.template_id not in names:
                one = list_templates(self._svc, user_id, note.subject_id, item.template_id)
                names[item.template_id] = one.templates[0].name
        return NotePage(
            note=note,
            instances=tuple(
                InstanceCard(
                    id=i.instance_id,
                    template_name=names[i.template_id],
                    purpose=i.purpose,
                    prompt_text=i.prompt_text,
                )
                for i in note.instances
            ),
        )

    def instance_document(self, user_id: UUID, instance_id: UUID) -> str | None:
        return web_instance_document(self._svc, user_id, instance_id)

    def add_pdf(self, user_id: UUID, subject_id: UUID, title: str, data: bytes) -> WebNoteSaved:
        raise NotImplementedError  # slice D Task 4 (save_web_note, pdf_text) fills this

    def add_text(
        self, user_id: UUID, subject_id: UUID, title: str, lesson_ref: str | None, text: str
    ) -> WebNoteSaved:
        raise NotImplementedError  # slice D Task 4 (save_web_note) fills this

    def retire_template(self, user_id: UUID, template_id: UUID) -> RetireOutcome | None:
        raise NotImplementedError  # slice D Task 5 (templates.retire_template) fills this


class MemorySubjects(ServicesSubjects):
    """Tests and `just dashboard-demo`: the same use cases over the in-memory store.

    Dashboard users come from the web memory backend, so a user record is added to the core
    store the first time an id is seen (as MemoryProfiles)."""

    def __init__(self, clock: Callable[[], datetime], store: MemoryStore | None = None) -> None:
        self.store = store if store is not None else MemoryStore()
        self._clock = clock
        self.svc = Services(uow=memory_uow(self.store), clock=clock, valid_timezones=iana_zones())
        super().__init__(self.svc)

    def ensure_user(self, user_id: UUID) -> None:
        if user_id not in self.store.tables.users:
            self.store.tables.users[user_id] = UserRecord(
                id=user_id,
                google_sub=f"web:{user_id}",
                email=None,
                display_name=None,
                created_at=self._clock(),
            )

    def subjects(self, user_id: UUID) -> SubjectsPage:
        self.ensure_user(user_id)
        return super().subjects(user_id)

    def subject(self, user_id: UUID, subject_id: UUID) -> SubjectPage | None:
        self.ensure_user(user_id)
        return super().subject(user_id, subject_id)

    def note(self, user_id: UUID, note_id: UUID) -> NotePage | None:
        self.ensure_user(user_id)
        return super().note(user_id, note_id)

    def instance_document(self, user_id: UUID, instance_id: UUID) -> str | None:
        self.ensure_user(user_id)
        return super().instance_document(user_id, instance_id)

    def add_pdf(self, user_id: UUID, subject_id: UUID, title: str, data: bytes) -> WebNoteSaved:
        raise NotImplementedError  # slice D Task 4 fills this

    def add_text(
        self, user_id: UUID, subject_id: UUID, title: str, lesson_ref: str | None, text: str
    ) -> WebNoteSaved:
        raise NotImplementedError  # slice D Task 4 fills this

    def retire_template(self, user_id: UUID, template_id: UUID) -> RetireOutcome | None:
        raise NotImplementedError  # slice D Task 5 fills this


def install_subjects(app: FastAPI, port: SubjectsPort) -> None:
    app.state.subjects = port


def optional_subjects(request: Request) -> SubjectsPort | None:
    port: SubjectsPort | None = getattr(request.app.state, "subjects", None)
    return port


def get_subjects_port(request: Request) -> SubjectsPort:
    port = optional_subjects(request)
    if port is None:
        raise RuntimeError("no SubjectsPort installed; call install_subjects(app, port)")
    return port


def install_frame_url(app: FastAPI, fn: FrameUrl) -> None:
    app.state.frame_url = fn


def get_frame_url(request: Request) -> FrameUrl:
    fn: FrameUrl | None = getattr(request.app.state, "frame_url", None)
    if fn is None:
        raise RuntimeError("no frame URL signer installed; call install_frame_url(app, fn)")
    return fn
