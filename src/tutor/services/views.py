"""Result types the use cases return to the MCP tools and the web pages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

from tutor.domain.glossary import GlossaryKind, RejectReason
from tutor.domain.lesson import BriefVariant, ReviewFormat
from tutor.domain.metrics import SessionMetrics, metrics_to_json
from tutor.domain.plan_lite import Feasibility, Variant
from tutor.domain.profile import Domain, Profile, UseCase
from tutor.domain.track import InteractionType, Skill, TrackItem
from tutor.domain.validation import SessionOutcome
from tutor.services.ports import ClientName, Mode, PlanItemStatus


@dataclass(frozen=True, slots=True)
class PlanItemView:
    plan_item_id: UUID
    week_no: int
    order_no: int
    track_item_id: str
    can_do_en: str
    can_do_es: str
    skill: Skill
    interaction_type: InteractionType
    variant: Variant
    status: PlanItemStatus


@dataclass(frozen=True, slots=True)
class PlanSummary:
    version: int
    current_week_no: int
    week_items: tuple[PlanItemView, ...]
    next_item: PlanItemView | None
    feasibility: Feasibility
    weeks: int
    sessions_planned: int
    sessions_done: int


@dataclass(frozen=True, slots=True)
class ProfileView:
    onboarding_needed: bool
    profile: Profile | None
    plan: PlanSummary | None
    streak: int
    open_session_id: UUID | None
    provisional_count: int
    due_reviews_count: int


@dataclass(frozen=True, slots=True)
class SaveProfileResult:
    profile: Profile
    plan: PlanSummary
    plan_changed: bool


@dataclass(frozen=True, slots=True)
class StartLessonRequest:
    mode: Mode
    prep: str | None = None
    prep_use_case: UseCase | None = None
    minutes: int | None = None
    domain: Domain = "it"
    client: ClientName = "claude"


@dataclass(frozen=True, slots=True)
class DueReviewView:
    item_id: UUID
    kind: GlossaryKind
    text: str
    meaning: str
    context_sentence: str
    format: ReviewFormat


@dataclass(frozen=True, slots=True)
class ProvisionalView:
    """Everything save_glossary needs, so the item can be sent back unchanged."""

    item_id: UUID
    kind: GlossaryKind
    text: str
    meaning: str
    context_sentence: str
    domain: Domain


@dataclass(frozen=True, slots=True)
class LessonStart:
    session_id: UUID
    mode: Mode
    item: TrackItem
    variant: BriefVariant
    prep_text: str | None
    due_reviews: tuple[DueReviewView, ...]
    provisional_items: tuple[ProvisionalView, ...]
    plan_exhausted: bool
    replaced_session: bool


@dataclass(frozen=True, slots=True)
class ReviewResultView:
    item_id: UUID
    next_due: date
    outcome: Literal["recorded", "already_recorded"]


@dataclass(frozen=True, slots=True)
class RejectedView:
    index: int
    reason: RejectReason


@dataclass(frozen=True, slots=True)
class GlossarySaveResult:
    new: int
    reinforced: int
    promoted: int
    rejected: tuple[RejectedView, ...]


@dataclass(frozen=True, slots=True)
class EndSessionResult:
    status: SessionOutcome
    low_trust: bool
    metrics: SessionMetrics
    summary_text: str
    streak: int
    already_closed: bool
    errors_rejected: int
    chunks_rejected: int

    def to_json(self) -> dict[str, Any]:
        """Stored in sessions.result for idempotent repeats; `already_closed` is not stored."""
        return {
            "status": self.status,
            "low_trust": self.low_trust,
            "metrics": metrics_to_json(self.metrics),
            "summary_text": self.summary_text,
            "streak": self.streak,
            "errors_rejected": self.errors_rejected,
            "chunks_rejected": self.chunks_rejected,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any], *, already_closed: bool) -> EndSessionResult:
        metrics = dict(data["metrics"])
        metrics["errors_by_category"] = dict(metrics["errors_by_category"])
        return cls(
            status=data["status"],
            low_trust=bool(data["low_trust"]),
            metrics=SessionMetrics(**metrics),
            summary_text=str(data["summary_text"]),
            streak=int(data["streak"]),
            already_closed=already_closed,
            errors_rejected=int(data["errors_rejected"]),
            chunks_rejected=int(data["chunks_rejected"]),
        )
