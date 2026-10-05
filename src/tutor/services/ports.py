"""Repository ports and row types for the use cases.

Every method acts on the unit of work's user only; another user's rows are invisible. Writes to
them are no-ops (close, mark_incomplete, set_log_rating), except save_metrics, save_errors,
save_state, log and apply, which raise LookupError when the referenced session or glossary item
is not visible to the user; mark_done and create raise it for a foreign session or plan item.
Implementations: tutor.services.memory (tests) and tutor.db (Postgres, RLS).
These docstrings are the semantics; tests/repo_contract.py pins them on both backends.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from tutor.domain.fsrs import FsrsState
from tutor.domain.glossary import GlossaryAction, GlossaryKind, GlossaryStatus, IncomingItem
from tutor.domain.lesson import BriefVariant, DueCandidate, RecentResult
from tutor.domain.levels import CefrLevel
from tutor.domain.metrics import SessionMetrics
from tutor.domain.plan_lite import PlannedItem, Variant
from tutor.domain.profile import Domain, Profile
from tutor.domain.track import TrackItem
from tutor.domain.validation import Evidence, SessionOutcome, ValidError

PlanItemStatus = Literal["pending", "done", "skipped"]
SessionStatus = Literal["open", "closed", "incomplete"]
Mode = Literal["voice", "text"]
ClientName = Literal["claude", "chatgpt", "code", "unknown"]
AuditEvent = Literal[
    "user_created",
    "web_login",
    "mcp_first_use",
    "profile_saved",
    "plan_generated",
    "session_closed",
    "glossary_saved",
]


@dataclass(frozen=True, slots=True)
class PlanItemRow:
    id: UUID
    week_no: int
    order_no: int
    track_item_id: str
    variant: Variant
    status: PlanItemStatus
    done_session_id: UUID | None


@dataclass(frozen=True, slots=True)
class ActivePlan:
    id: UUID
    version: int
    generated_at: datetime
    rationale: Mapping[str, Any]
    items: tuple[PlanItemRow, ...]  # ordered by (week_no, order_no)


@dataclass(frozen=True, slots=True)
class SessionRow:
    id: UUID
    plan_item_id: UUID | None
    track_item_id: str
    prep_text: str | None
    mode: Mode
    client: ClientName
    started_at: datetime
    ended_at: datetime | None
    status: SessionStatus
    low_trust: bool
    brief_variant: BriefVariant
    chunks_offered: tuple[str, ...]
    result: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class GlossaryRowData:
    id: UUID
    kind: GlossaryKind
    text: str
    text_norm: str
    meaning: str
    context_sentence: str
    domain: Domain
    status: GlossaryStatus
    seen_count: int
    leech: bool
    created_at: datetime
    provisional_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReviewLogRow:
    item_id: UUID
    rating: int
    reviewed_at: datetime
    state_before: FsrsState


class OpenSessionExists(Exception):
    """Raised by SessionRepo.create when the user already has an open session."""


class UserRepo(Protocol):
    def timezone(self) -> str:
        """users.timezone (IANA); new users have DEFAULT_TIMEZONE."""

    def set_timezone(self, tz: str) -> None:
        """Write users.timezone."""

    def note_mcp_use(self, now: datetime) -> bool:
        """Set users.mcp_first_seen_at if empty; True only when this call set it."""


class ProfileRepo(Protocol):
    def get(self) -> Profile | None:
        """The profile, or None before onboarding; Profile.timezone is users.timezone."""

    def upsert(self, profile: Profile, now: datetime) -> None:
        """Insert or replace the profile and write profile.timezone to users.timezone."""


class TrackRepo(Protocol):
    def items(self, domain: Domain) -> tuple[TrackItem, ...]:
        """Seeded track items of the domain ordered by order_no, chunks by position."""


class PlanRepo(Protocol):
    def active(self) -> ActivePlan | None:
        """The user's active plan with its items ordered by (week_no, order_no)."""

    def create(
        self, items: Sequence[PlannedItem], rationale: Mapping[str, Any], now: datetime
    ) -> ActivePlan:
        """New active plan, version = the user's max + 1, items pending; others superseded.
        `rationale` must be JSON-serializable (TypeError otherwise)."""

    def mark_done(self, plan_item_id: UUID, session_id: UUID) -> bool:
        """Mark one of the user's plan items done in any plan version. False for another
        user's, a missing or an already-done plan item; LookupError when session_id is not a
        session of the current user."""

    def done_base_track_ids(self) -> frozenset[str]:
        """Track ids of done plan items with variant base, across every version."""


class SessionRepo(Protocol):
    def get(self, session_id: UUID) -> SessionRow | None:
        """The user's session, or None."""

    def open_session(self) -> SessionRow | None:
        """The user's open session, or None."""

    def create(
        self,
        *,
        plan_item_id: UUID | None,
        track_item_id: str,
        prep_text: str | None,
        mode: Mode,
        client: ClientName,
        brief_variant: BriefVariant,
        chunks_offered: Sequence[str],
        now: datetime,
    ) -> SessionRow:
        """Insert an open session started at `now`. Raises OpenSessionExists if the user
        already has one (unique index); the caller's unit of work then rolls back.
        LookupError when plan_item_id is not a plan item of the current user."""

    def mark_incomplete(self, session_id: UUID, now: datetime) -> None:
        """Open session -> incomplete with ended_at = now and no result; no-op otherwise."""

    def close(
        self,
        session_id: UUID,
        *,
        status: SessionOutcome,
        low_trust: bool,
        ended_at: datetime,
        evidence: Evidence,
        raw_evidence: Mapping[str, Any],
        cefr_excluded: bool,
        result: Mapping[str, Any],
    ) -> None:
        """Store the end_session outcome: status, low_trust, ended_at, task_result,
        hints_given, CEFR estimate and confidence, cefr_excluded, confidence_1_5,
        raw_evidence and result (JSON). No-op for another user's session."""

    def count_started_since(self, since: datetime) -> int:
        """Sessions of any status with started_at >= since."""

    def recent_results(self, limit: int) -> tuple[RecentResult, ...]:
        """Closed sessions, newest ended_at first."""

    def closed_ended_at(self, since: datetime) -> tuple[datetime, ...]:
        """ended_at of closed sessions with ended_at >= since, ascending."""

    def previous_cefr(self) -> CefrLevel | None:
        """CEFR estimate of the newest closed session whose estimate is not excluded."""

    def last_done_by_track(self) -> Mapping[str, datetime]:
        """track_item_id -> newest ended_at among closed sessions."""

    def save_metrics(self, session_id: UUID, metrics: SessionMetrics) -> None:
        """Insert or replace the session's metrics row. Raises LookupError if not the user's."""

    def save_errors(self, session_id: UUID, errors: Sequence[ValidError]) -> None:
        """Replace the session's validated errors. Raises LookupError if not the user's."""

    def recent_correct_norms(self, since: datetime, exclude: UUID) -> frozenset[str]:
        """correct_norm of errors saved for closed sessions with ended_at >= since,
        excluding the session `exclude`."""


class GlossaryRepo(Protocol):
    def by_norms(self, norms: Collection[str]) -> Mapping[str, GlossaryRowData]:
        """text_norm -> row for the requested norms that exist."""

    def get_many(self, ids: Collection[UUID]) -> Mapping[UUID, GlossaryRowData]:
        """id -> row for the requested ids that are the user's."""

    def apply(
        self,
        actions: Sequence[GlossaryAction],
        items: Sequence[IncomingItem],
        *,
        session_id: UUID,
        now: datetime,
    ) -> None:
        """Write plan_glossary_save's actions. InsertItem: new row (seen_count 1, created_at =
        updated_at = now, origin/last_seen session) plus new_state(first_due) when first_due
        is set; unique (user, text_norm). Reinforce: kind, seen_count, leech, last_seen,
        updated_at, and the review state's due (new_state(due) if none). Promote: confirmed,
        no expiry, review state new_state(first_due). SetStatus: status and expiry. Reject is
        ignored; `items` is informational and meaning/context never change after insert.
        Raises LookupError if an action targets an item that is not the user's."""

    def due_candidates(self, now: datetime) -> tuple[DueCandidate, ...]:
        """Confirmed items whose review state due <= now, ordered by (due, id); last_ratings
        are the ratings of the item's two newest review logs, oldest first."""

    def provisional(self, limit: int) -> tuple[GlossaryRowData, ...]:
        """Provisional items ordered by (created_at, id), at most `limit`."""

    def count_provisional(self) -> int:
        """Number of provisional items."""

    def count_due(self, now: datetime) -> int:
        """len(due_candidates(now))."""

    def purge(self, now: datetime) -> int:
        """Delete provisional items with provisional_expires_at < now and declined items with
        created_at < now - DECLINED_RETENTION_DAYS; return how many."""


class ReviewRepo(Protocol):
    def state(self, item_id: UUID) -> FsrsState | None:
        """The item's FSRS state, or None."""

    def save_state(self, item_id: UUID, state: FsrsState) -> None:
        """Insert or replace the item's FSRS state. Raises LookupError if not the user's."""

    def log(
        self, session_id: UUID, item_id: UUID, rating: int, now: datetime, state_before: FsrsState
    ) -> bool:
        """Append a review log; False (and nothing written) if (session, item) is logged.
        Raises LookupError if the session or item is not the user's."""

    def session_logs(self, session_id: UUID) -> tuple[ReviewLogRow, ...]:
        """The session's logs ordered by (reviewed_at, id)."""

    def set_log_rating(self, session_id: UUID, item_id: UUID, rating: int) -> None:
        """Change one log's rating (rating-4 upgrade); no-op if absent."""


class AuditRepo(Protocol):
    def record(self, event: AuditEvent, meta: Mapping[str, Any], now: datetime) -> None:
        """Append an audit entry; meta is JSON and never holds learner text."""


class UnitOfWork(Protocol):
    user_id: UUID
    users: UserRepo
    profiles: ProfileRepo
    track: TrackRepo
    plans: PlanRepo
    sessions: SessionRepo
    glossary: GlossaryRepo
    reviews: ReviewRepo
    audit: AuditRepo


# Commits when the block exits cleanly; rolls everything back when it raises.
UowFactory = Callable[[UUID], AbstractContextManager[UnitOfWork]]


@dataclass(frozen=True, slots=True)
class ResolvedUser:
    id: UUID
    created: bool


class IdentityResolver(Protocol):
    def resolve(
        self, google_sub: str, email: str | None, display_name: str | None, now: datetime
    ) -> ResolvedUser:
        """Find-or-create the user by Google sub, never by email."""
