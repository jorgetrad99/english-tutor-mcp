"""Result types of the subjects and notes use cases (subjects spec 3-5, 7.1).

Frozen dataclasses the MCP tools and the web pages render; learner text travels in them as data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from tutor.domain.html_guard import ArtifactKind
from tutor.domain.notes import NoteExerciseKind, Origin
from tutor.domain.subjects import Subject, SubjectQuestion

Outcome = Literal["new", "existing", "superseded"]
OUTCOMES: tuple[Outcome, ...] = ("new", "existing", "superseded")


@dataclass(frozen=True, slots=True)
class PlanStatus:
    """Spec 6.1 shape, filled by slice B; this slice returns None for every subject (ruling 5)."""

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


@dataclass(frozen=True, slots=True)
class SaveNotesResult:
    source_id: UUID | None
    notes: tuple[NoteOutcome, ...]


@dataclass(frozen=True, slots=True)
class ArtifactSaved:
    artifact_id: UUID
    web_path: str


@dataclass(frozen=True, slots=True)
class ArtifactView:
    artifact_id: UUID
    note_id: UUID
    kind: ArtifactKind
    title: str
    html: str


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: UUID
    kind: ArtifactKind
    title: str


@dataclass(frozen=True, slots=True)
class ExerciseView:
    exercise_id: UUID
    name: str
    instructions: str
    kind: NoteExerciseKind
    tempo_bpm: int | None
    duration_min: int | None


@dataclass(frozen=True, slots=True)
class QuizItemView:
    """A quiz item as the learner may see it: never the answer index (spec 5.2)."""

    question: str
    options: tuple[str, ...]
    explanation: str | None
    concept: str | None


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
    exercises: tuple[ExerciseView, ...]
    quiz_items: tuple[QuizItemView, ...]
    concepts: tuple[str, ...]
    artifacts: tuple[ArtifactRef, ...]


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
