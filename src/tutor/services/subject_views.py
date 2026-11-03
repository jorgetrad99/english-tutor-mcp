"""Result types of the subjects, notes and templates use cases (subjects spec 3-5, 7, 15).

Frozen dataclasses the MCP tools and the web pages render; learner text travels in them as data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from tutor.domain.notes import Origin
from tutor.domain.practice import KindResult, PracticeMode, PracticeStatus
from tutor.domain.subjects import Subject, SubjectQuestion
from tutor.domain.templates import (
    GradingKind,
    GuideSection,
    InstanceStatus,
    ParamSpec,
    PoolDraw,
    PracticeKind,
    TemplatePurpose,
    TemplateStatus,
    TextMatch,
)

Outcome = Literal["new", "existing", "superseded"]
OUTCOMES: tuple[Outcome, ...] = ("new", "existing", "superseded")
# `superseded` when the call replaced an older template or instance (rulings 28, spec 15.9).
TemplateOutcome = Literal["new", "existing", "superseded"]
InstanceOutcome = Literal["new", "existing", "superseded"]


@dataclass(frozen=True, slots=True)
class PlanStatus:
    """Spec 6.1: the subject's active plan at the learner's local date (slice B rulings 28 and
    29); None for a subject saved before slice B until its next save."""

    version: int
    start_date: date
    total_sessions: int | None
    expected_sessions: int
    completed_sessions: int
    missed: int
    projected_end: date | None
    delay_days: int | None
    adherence: float | None


@dataclass(frozen=True, slots=True)
class SubjectView:
    id: UUID
    subject: Subject
    plan_status: PlanStatus | None


@dataclass(frozen=True, slots=True)
class SubjectsView:
    subjects: tuple[SubjectView, ...]
    questions: tuple[SubjectQuestion, ...]


@dataclass(frozen=True, slots=True)
class SaveSubjectResult:
    subject: SubjectView
    created: bool
    plan_changed: bool


@dataclass(frozen=True, slots=True)
class NoteOutcome:
    note_id: UUID
    outcome: Outcome
    instance_ids: tuple[UUID, ...] = ()  # the note's active instances, in seq order


@dataclass(frozen=True, slots=True)
class SaveNotesResult:
    source_id: UUID | None
    notes: tuple[NoteOutcome, ...]


@dataclass(frozen=True, slots=True)
class NoteInstanceView:
    """An instance as get_note shows it: prompt parameters only, a pool as its entry count
    (plan ruling 25)."""

    instance_id: UUID
    seq: int | None
    template_id: UUID
    purpose: TemplatePurpose
    practice_kind: PracticeKind | None
    grading_kind: GradingKind
    needs_view: bool
    prompt_text: str
    concept: str | None
    params: Mapping[str, object]
    pool_entries: int | None


@dataclass(frozen=True, slots=True)
class PassageView:
    seq: int
    text: str


@dataclass(frozen=True, slots=True)
class NoteView:
    note_id: UUID
    subject_id: UUID
    source_id: UUID | None
    source_title: str | None
    title: str
    lesson_ref: str | None
    position: int | None
    origin: Origin
    supersedes_note_id: UUID | None
    created_at: datetime
    updated_at: datetime
    clean_text: str
    truncated: bool
    passage_count: int
    passages: tuple[PassageView, ...]
    concepts: tuple[str, ...]
    instances: tuple[NoteInstanceView, ...]


@dataclass(frozen=True, slots=True)
class SearchWhy:
    passages: int
    concepts: tuple[str, ...]
    depth: int | None
    fuzzy: bool


@dataclass(frozen=True, slots=True)
class SearchHitView:
    note_id: UUID
    subject_id: UUID
    title: str
    lesson_ref: str | None
    source_title: str | None
    excerpts: tuple[str, ...]
    why: SearchWhy


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: tuple[SearchHitView, ...]


@dataclass(frozen=True, slots=True)
class GuideView:
    guide_version: int
    section: GuideSection
    text: str


@dataclass(frozen=True, slots=True)
class TemplateView:
    template_id: UUID
    name: str
    description: str | None
    purpose: TemplatePurpose
    practice_kind: PracticeKind | None
    grading_kind: GradingKind
    text_match: TextMatch | None
    numeric_tolerance: Decimal | None
    pool_draw: PoolDraw
    pool_count: int | None
    needs_view: bool
    params: tuple[ParamSpec, ...]
    options_param: str | None
    status: TemplateStatus
    guide_version: int
    supersedes_template_id: UUID | None
    html: str | None  # only when asked for one template


@dataclass(frozen=True, slots=True)
class TemplatesView:
    guide_version: int
    templates: tuple[TemplateView, ...]
    guide: str | None  # the inline guide when the subject has no active template


@dataclass(frozen=True, slots=True)
class TemplateSaved:
    template_id: UUID
    practice_kind: PracticeKind | None
    outcome: TemplateOutcome


@dataclass(frozen=True, slots=True)
class InstanceSaved:
    instance_id: UUID
    outcome: InstanceOutcome
    purpose: TemplatePurpose
    document: str | None  # only when asked (include_document); get_instance_view shows one
    text: str


@dataclass(frozen=True, slots=True)
class InstanceView:
    instance_id: UUID
    purpose: TemplatePurpose
    status: InstanceStatus
    document: str
    text: str


# --- practice (spec 6.2 amended by 15; slice B) -------------------------------------------------

ResultOutcome = Literal["new", "existing"]


@dataclass(frozen=True, slots=True)
class PracticeItemView:
    """One showing as a client sees it: prompt parameters and the stored draw only, never an
    answer key, a reveal or a pool. `document` is the composed template (artifact bridge until
    slice C gives the app its own), None in text mode; `text` always states the item."""

    item_id: UUID
    session_id: UUID
    seq: int
    instance_id: UUID
    practice_kind: PracticeKind | None
    grading_kind: GradingKind
    needs_view: bool
    document: str | None
    text: str


@dataclass(frozen=True, slots=True)
class PracticeStart:
    session_id: UUID
    mode: PracticeMode
    item_count: int
    short: bool  # fewer than four items could be drawn (ruling 35)
    first: PracticeItemView | None


@dataclass(frozen=True, slots=True)
class NextItem:
    session_id: UUID
    done: bool
    item: PracticeItemView | None
    answered: int
    remaining: int


@dataclass(frozen=True, slots=True)
class ShownExercise:
    session_id: UUID
    mode: PracticeMode
    item: PracticeItemView


@dataclass(frozen=True, slots=True)
class GradedResult:
    """The server's grade of one showing; `reveal` holds the instance's reveal parameters and
    the drawn entries' explanations (learner data)."""

    session_id: UUID
    item_id: UUID
    outcome: ResultOutcome
    correct: bool | None
    score: int
    reveal: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PracticeSummary:
    session_id: UUID
    items: int
    answered: int
    unanswered: int
    by_kind: tuple[KindResult, ...]
    minutes: int
    short: bool
    self_rating: int | None
    plan_status: PlanStatus | None
    streak_days: int  # the subject's current streak (spec 16.13 Q2), never the English one


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """One practice session as the subject page lists it (slice D ruling 55). `items` and
    `answered` count the session's showings; `accuracy` is the share of answered showings graded
    correct (None without an answer); `minutes` is the stored summary's, None until the session
    is closed; `counted` is the attendance rule (spec 15.13 D5), decided here."""

    session_id: UUID
    started_at: datetime
    mode: PracticeMode
    status: PracticeStatus
    items: int
    answered: int
    accuracy: float | None
    minutes: int | None
    counted: bool
