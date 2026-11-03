"""In-memory repositories and unit of work for unit tests and local work.

The store enforces what Postgres enforces: one open session per user, unique (user, text_norm),
one review log per (session, item), JSON-only JSON columns, one active template per subject and
name, one active instance per content key, one open practice session per user, an answer only
after a showing, and per-user isolation. A unit of work deep-copies the
tables on enter and restores the copy if the block raises.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from tutor.content import load_track
from tutor.domain.fsrs import FsrsState, new_state
from tutor.domain.glossary import (
    DECLINED_RETENTION_DAYS,
    GlossaryAction,
    GlossaryKind,
    GlossaryStatus,
    IncomingItem,
    InsertItem,
    Promote,
    Reinforce,
    Reject,
    SetStatus,
)
from tutor.domain.lesson import BriefVariant, DueCandidate, RecentResult
from tutor.domain.levels import CefrLevel
from tutor.domain.metrics import SessionMetrics
from tutor.domain.notes import (
    CLEAN_VERSION,
    ConceptName,
    LinkKind,
    Origin,
    SourceKind,
    ValidNote,
    ValidSource,
    idempotency_key,
    name_norm,
)
from tutor.domain.plan_lite import PlannedItem, Variant
from tutor.domain.practice import Candidate, DrawnItem, ItemSource, PracticeMode, PracticeStatus
from tutor.domain.practice_plan import PlanInputs, total_sessions
from tutor.domain.profile import DEFAULT_TIMEZONE, Domain, Profile
from tutor.domain.subject_metrics import (
    ActivityFacts,
    ConceptFact,
    SessionFact,
    ShowingFact,
    TemplateFact,
)
from tutor.domain.subjects import Subject
from tutor.domain.templates import Draw, InstanceStatus, TemplateSpec, TemplateStatus, spec_hash
from tutor.domain.templates import Origin as InstanceOrigin
from tutor.domain.track import TrackItem
from tutor.domain.validation import Category, Evidence, SessionOutcome, ValidError
from tutor.services.ports import (
    ActivePlan,
    AuditEvent,
    AuditRepo,
    ClientName,
    ConceptRow,
    GlossaryRepo,
    GlossaryRowData,
    IdentityResolver,
    InstanceRepo,
    InstanceRow,
    Mode,
    NoteListRow,
    NoteRepo,
    NoteRow,
    OpenPracticeExists,
    OpenSessionExists,
    PassageRow,
    PlanItemRow,
    PlanItemStatus,
    PlanRepo,
    PracticeItemRow,
    PracticePlanRow,
    PracticePlanStatus,
    PracticeRepo,
    PracticeSessionRow,
    ProfileRepo,
    ProgressRepo,
    ResolvedUser,
    ReviewLogRow,
    ReviewRepo,
    SearchHit,
    SessionItemCounts,
    SessionRepo,
    SessionRow,
    SessionStatus,
    SourceRow,
    SubjectRepo,
    SubjectRow,
    TemplateRepo,
    TemplateRow,
    TrackRepo,
    UnitOfWork,
    UowFactory,
    UserRepo,
)


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Round-trip through JSON as a JSONB column would; TypeError on non-JSON values."""
    data: dict[str, Any] = json.loads(json.dumps(dict(value)))
    return data


@dataclass(slots=True)
class UserRecord:
    id: UUID
    google_sub: str
    email: str | None
    display_name: str | None
    created_at: datetime
    timezone: str = DEFAULT_TIMEZONE
    mcp_first_seen_at: datetime | None = None


@dataclass(slots=True)
class PlanRecord:
    id: UUID
    user_id: UUID
    version: int
    status: Literal["active", "superseded"]
    generated_at: datetime
    rationale: dict[str, Any]


@dataclass(slots=True)
class PlanItemRecord:
    id: UUID
    plan_id: UUID
    user_id: UUID
    week_no: int
    order_no: int
    track_item_id: str
    variant: Variant
    status: PlanItemStatus = "pending"
    done_session_id: UUID | None = None


@dataclass(slots=True)
class SessionRecord:
    id: UUID
    user_id: UUID
    plan_item_id: UUID | None
    track_item_id: str
    prep_text: str | None
    mode: Mode
    client: ClientName
    brief_variant: BriefVariant
    chunks_offered: tuple[str, ...]
    started_at: datetime
    status: SessionStatus = "open"
    ended_at: datetime | None = None
    low_trust: bool = False
    evidence: Evidence | None = None
    raw_evidence: dict[str, Any] | None = None
    cefr_excluded: bool = False
    result: dict[str, Any] | None = None


@dataclass(slots=True)
class GlossaryRecord:
    id: UUID
    user_id: UUID
    kind: GlossaryKind
    text: str
    text_norm: str
    meaning: str
    context_sentence: str
    domain: Domain
    status: GlossaryStatus
    origin_session_id: UUID
    last_seen_session_id: UUID
    created_at: datetime
    updated_at: datetime
    provisional_expires_at: datetime | None = None
    seen_count: int = 1
    leech: bool = False


@dataclass(slots=True)
class ReviewStateRecord:
    user_id: UUID
    state: FsrsState


@dataclass(slots=True)
class ReviewLogRecord:
    id: UUID
    user_id: UUID
    session_id: UUID
    item_id: UUID
    rating: int
    reviewed_at: datetime
    state_before: FsrsState


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    user_id: UUID
    session_id: UUID
    said: str
    correct: str
    correct_norm: str
    category: Category
    turn_index: int


@dataclass(frozen=True, slots=True)
class MetricsRecord:
    user_id: UUID
    metrics: SessionMetrics


@dataclass(frozen=True, slots=True)
class AuditRecord:
    user_id: UUID
    event: AuditEvent
    meta: dict[str, Any]
    at: datetime


@dataclass(slots=True)
class SubjectRecord:
    id: UUID
    user_id: UUID
    subject: Subject
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class SourceRecord:
    id: UUID
    user_id: UUID
    subject_id: UUID
    kind: SourceKind
    title: str
    url: str | None
    created_at: datetime


@dataclass(slots=True)
class NoteRecord:
    id: UUID
    user_id: UUID
    subject_id: UUID
    source_id: UUID | None
    title: str
    title_norm: str
    lesson_ref: str | None
    position: int | None
    raw_text: str
    raw_had_nul: bool
    raw_sha256: str
    clean_text: str
    clean_version: int
    idempotency_key: str
    origin: Origin
    supersedes_note_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PassageRecord:
    user_id: UUID
    note_id: UUID
    seq: int
    text: str


@dataclass(slots=True)
class ConceptRecord:
    id: UUID
    user_id: UUID
    subject_id: UUID
    name: str
    name_norm: str


@dataclass(frozen=True, slots=True)
class NoteConceptRecord:
    user_id: UUID
    note_id: UUID
    concept_id: UUID


@dataclass(frozen=True, slots=True)
class ConceptLinkRecord:
    user_id: UUID
    from_id: UUID
    to_id: UUID
    kind: LinkKind


@dataclass(slots=True)
class TemplateRecord:
    id: UUID
    user_id: UUID
    subject_id: UUID
    spec: TemplateSpec
    name_norm: str
    html: str
    html_sha256: str
    bytes: int
    guide_version: int
    status: TemplateStatus
    supersedes_template_id: UUID | None
    created_at: datetime
    updated_at: datetime
    retired_at: datetime | None


@dataclass(slots=True)
class InstanceRecord:
    id: UUID
    user_id: UUID
    subject_id: UUID
    note_id: UUID | None
    seq: int | None
    template_id: UUID
    template: TemplateSpec  # the purpose and kinds are copied from it at creation
    params: dict[str, Any]
    prompt_text: str
    concept_id: UUID | None
    origin: InstanceOrigin
    content_key: str
    status: InstanceStatus
    supersedes_instance_id: UUID | None
    created_at: datetime
    retired_at: datetime | None


@dataclass(slots=True)
class PracticePlanRecord:
    id: UUID
    user_id: UUID
    subject_id: UUID
    version: int
    status: PracticePlanStatus
    start_date: date
    inputs: PlanInputs
    total_sessions: int | None
    rationale: dict[str, Any]
    created_at: datetime


@dataclass(slots=True)
class PracticeSessionRecord:
    id: UUID
    user_id: UUID
    subject_id: UUID
    plan_id: UUID | None
    mode: PracticeMode
    started_at: datetime
    item_count: int
    status: PracticeStatus = "open"
    ended_at: datetime | None = None
    self_rating: int | None = None
    summary: dict[str, Any] | None = None


@dataclass(slots=True)
class PracticeItemRecord:
    id: UUID
    user_id: UUID
    session_id: UUID
    seq: int
    instance_id: UUID
    draw_seed: int
    draw: Draw
    shown_at: datetime | None = None
    answer: dict[str, Any] | None = None
    correct: bool | None = None
    score: int | None = None
    answered_at: datetime | None = None
    source: ItemSource | None = None


@dataclass(slots=True)
class Tables:
    users: dict[UUID, UserRecord] = field(default_factory=dict)
    profiles: dict[UUID, Profile] = field(default_factory=dict)
    plans: dict[UUID, PlanRecord] = field(default_factory=dict)
    plan_items: dict[UUID, PlanItemRecord] = field(default_factory=dict)
    sessions: dict[UUID, SessionRecord] = field(default_factory=dict)
    metrics: dict[UUID, MetricsRecord] = field(default_factory=dict)
    errors: list[ErrorRecord] = field(default_factory=list)
    glossary: dict[UUID, GlossaryRecord] = field(default_factory=dict)
    review_states: dict[UUID, ReviewStateRecord] = field(default_factory=dict)
    review_logs: list[ReviewLogRecord] = field(default_factory=list)
    audit: list[AuditRecord] = field(default_factory=list)
    subjects: dict[UUID, SubjectRecord] = field(default_factory=dict)
    sources: dict[UUID, SourceRecord] = field(default_factory=dict)
    notes: dict[UUID, NoteRecord] = field(default_factory=dict)
    note_passages: list[PassageRecord] = field(default_factory=list)
    concepts: dict[UUID, ConceptRecord] = field(default_factory=dict)
    note_concepts: list[NoteConceptRecord] = field(default_factory=list)
    concept_links: list[ConceptLinkRecord] = field(default_factory=list)
    exercise_templates: dict[UUID, TemplateRecord] = field(default_factory=dict)
    template_instances: dict[UUID, InstanceRecord] = field(default_factory=dict)
    practice_plans: dict[UUID, PracticePlanRecord] = field(default_factory=dict)
    practice_sessions: dict[UUID, PracticeSessionRecord] = field(default_factory=dict)
    practice_items: dict[UUID, PracticeItemRecord] = field(default_factory=dict)


class MemoryStore:
    """All tables as dicts; one instance per test. `tables` is replaced on rollback."""

    def __init__(self, track: Sequence[TrackItem] | None = None) -> None:
        self.tables = Tables()
        self.track: tuple[TrackItem, ...] = tuple(track) if track is not None else load_track()
        # Test hook: called at the start of every SessionRepo.create (Review Focus 5).
        self.before_session_create: Callable[[], None] | None = None


class _Repo:
    def __init__(self, store: MemoryStore, user_id: UUID) -> None:
        self._store = store
        self._uid = user_id

    @property
    def _t(self) -> Tables:
        return self._store.tables

    def _owns_session(self, session_id: UUID) -> bool:
        rec = self._t.sessions.get(session_id)
        return rec is not None and rec.user_id == self._uid

    def _owns_item(self, item_id: UUID) -> bool:
        rec = self._t.glossary.get(item_id)
        return rec is not None and rec.user_id == self._uid


class MemoryUserRepo(_Repo):
    def _user(self) -> UserRecord:
        return self._t.users[self._uid]

    def timezone(self) -> str:
        return self._user().timezone

    def set_timezone(self, tz: str) -> None:
        self._user().timezone = tz

    def note_mcp_use(self, now: datetime) -> bool:
        user = self._user()
        if user.mcp_first_seen_at is not None:
            return False
        user.mcp_first_seen_at = now
        return True


class MemoryProfileRepo(_Repo):
    def get(self) -> Profile | None:
        profile = self._t.profiles.get(self._uid)
        if profile is None:
            return None
        return replace(profile, timezone=self._t.users[self._uid].timezone)

    def upsert(self, profile: Profile, now: datetime) -> None:
        self._t.profiles[self._uid] = profile
        self._t.users[self._uid].timezone = profile.timezone


class MemoryTrackRepo(_Repo):
    def items(self, domain: Domain) -> tuple[TrackItem, ...]:
        mine = (t for t in self._store.track if t.domain == domain)
        return tuple(sorted(mine, key=lambda t: t.order_no))


class MemoryPlanRepo(_Repo):
    def active(self) -> ActivePlan | None:
        for plan in self._t.plans.values():
            if plan.user_id == self._uid and plan.status == "active":
                return self._view(plan)
        return None

    def create(
        self, items: Sequence[PlannedItem], rationale: Mapping[str, Any], now: datetime
    ) -> ActivePlan:
        stored = _json_copy(rationale)
        mine = [p for p in self._t.plans.values() if p.user_id == self._uid]
        for old in mine:
            old.status = "superseded"
        plan = PlanRecord(
            id=uuid4(),
            user_id=self._uid,
            version=max((p.version for p in mine), default=0) + 1,
            status="active",
            generated_at=now,
            rationale=stored,
        )
        self._t.plans[plan.id] = plan
        for planned in items:
            rec = PlanItemRecord(
                id=uuid4(),
                plan_id=plan.id,
                user_id=self._uid,
                week_no=planned.week_no,
                order_no=planned.order_no,
                track_item_id=planned.track_item_id,
                variant=planned.variant,
            )
            self._t.plan_items[rec.id] = rec
        return self._view(plan)

    def mark_done(self, plan_item_id: UUID, session_id: UUID) -> bool:
        if not self._owns_session(session_id):
            raise LookupError("session not found")
        item = self._t.plan_items.get(plan_item_id)
        if item is None or item.user_id != self._uid or item.status == "done":
            return False
        item.status = "done"
        item.done_session_id = session_id
        return True

    def done_base_track_ids(self) -> frozenset[str]:
        return frozenset(
            i.track_item_id
            for i in self._t.plan_items.values()
            if i.user_id == self._uid and i.variant == "base" and i.status == "done"
        )

    def _view(self, plan: PlanRecord) -> ActivePlan:
        items = sorted(
            (i for i in self._t.plan_items.values() if i.plan_id == plan.id),
            key=lambda i: (i.week_no, i.order_no),
        )
        return ActivePlan(
            id=plan.id,
            version=plan.version,
            generated_at=plan.generated_at,
            rationale=copy.deepcopy(plan.rationale),
            items=tuple(
                PlanItemRow(
                    id=i.id,
                    week_no=i.week_no,
                    order_no=i.order_no,
                    track_item_id=i.track_item_id,
                    variant=i.variant,
                    status=i.status,
                    done_session_id=i.done_session_id,
                )
                for i in items
            ),
        )


def _session_row(rec: SessionRecord) -> SessionRow:
    return SessionRow(
        id=rec.id,
        plan_item_id=rec.plan_item_id,
        track_item_id=rec.track_item_id,
        prep_text=rec.prep_text,
        mode=rec.mode,
        client=rec.client,
        started_at=rec.started_at,
        ended_at=rec.ended_at,
        status=rec.status,
        low_trust=rec.low_trust,
        brief_variant=rec.brief_variant,
        chunks_offered=rec.chunks_offered,
        result=copy.deepcopy(rec.result),
    )


class MemorySessionRepo(_Repo):
    def _mine(self) -> list[SessionRecord]:
        return [s for s in self._t.sessions.values() if s.user_id == self._uid]

    def _record(self, session_id: UUID) -> SessionRecord | None:
        rec = self._t.sessions.get(session_id)
        return rec if rec is not None and rec.user_id == self._uid else None

    def _closed(self) -> list[tuple[datetime, Evidence, SessionRecord]]:
        """Closed sessions with their evidence, ascending by ended_at."""
        rows: list[tuple[datetime, Evidence, SessionRecord]] = []
        for s in self._mine():
            if s.status == "closed" and s.ended_at is not None and s.evidence is not None:
                rows.append((s.ended_at, s.evidence, s))
        return sorted(rows, key=lambda row: row[0])

    def get(self, session_id: UUID) -> SessionRow | None:
        rec = self._record(session_id)
        return None if rec is None else _session_row(rec)

    def open_session(self) -> SessionRow | None:
        for rec in self._mine():
            if rec.status == "open":
                return _session_row(rec)
        return None

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
        hook = self._store.before_session_create
        if hook is not None:
            hook()
        if plan_item_id is not None:
            plan_item = self._t.plan_items.get(plan_item_id)
            if plan_item is None or plan_item.user_id != self._uid:
                raise LookupError("plan item not found")
        if any(s.status == "open" for s in self._mine()):
            raise OpenSessionExists()
        rec = SessionRecord(
            id=uuid4(),
            user_id=self._uid,
            plan_item_id=plan_item_id,
            track_item_id=track_item_id,
            prep_text=prep_text,
            mode=mode,
            client=client,
            brief_variant=brief_variant,
            chunks_offered=tuple(chunks_offered),
            started_at=now,
        )
        self._t.sessions[rec.id] = rec
        return _session_row(rec)

    def mark_incomplete(self, session_id: UUID, now: datetime) -> None:
        rec = self._record(session_id)
        if rec is not None and rec.status == "open":
            rec.status = "incomplete"
            rec.ended_at = now

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
        rec = self._record(session_id)
        if rec is None:
            return
        stored_raw = _json_copy(raw_evidence)
        stored_result = _json_copy(result)
        rec.status = status
        rec.low_trust = low_trust
        rec.ended_at = ended_at
        rec.evidence = evidence
        rec.raw_evidence = stored_raw
        rec.cefr_excluded = cefr_excluded
        rec.result = stored_result

    def count_started_since(self, since: datetime) -> int:
        return sum(1 for s in self._mine() if s.started_at >= since)

    def recent_results(self, limit: int) -> tuple[RecentResult, ...]:
        newest = list(reversed(self._closed()))[:limit]
        return tuple(
            RecentResult(task_result=ev.task_result, hints_given=ev.hints_given)
            for _, ev, _ in newest
        )

    def closed_ended_at(self, since: datetime) -> tuple[datetime, ...]:
        return tuple(ended for ended, _, _ in self._closed() if ended >= since)

    def previous_cefr(self) -> CefrLevel | None:
        for _, ev, rec in reversed(self._closed()):
            if not rec.cefr_excluded:
                return ev.cefr_level
        return None

    def last_done_by_track(self) -> Mapping[str, datetime]:
        return {rec.track_item_id: ended for ended, _, rec in self._closed()}

    def save_metrics(self, session_id: UUID, metrics: SessionMetrics) -> None:
        if not self._owns_session(session_id):
            raise LookupError("session not found")
        plain = replace(metrics, errors_by_category=dict(metrics.errors_by_category))
        self._t.metrics[session_id] = MetricsRecord(user_id=self._uid, metrics=plain)

    def save_errors(self, session_id: UUID, errors: Sequence[ValidError]) -> None:
        if not self._owns_session(session_id):
            raise LookupError("session not found")
        kept = [e for e in self._t.errors if e.session_id != session_id]
        added = [
            ErrorRecord(
                user_id=self._uid,
                session_id=session_id,
                said=e.said,
                correct=e.correct,
                correct_norm=e.correct_norm,
                category=e.category,
                turn_index=e.turn_index,
            )
            for e in errors
        ]
        self._t.errors = kept + added

    def recent_correct_norms(self, since: datetime, exclude: UUID) -> frozenset[str]:
        ids = {rec.id for ended, _, rec in self._closed() if ended >= since and rec.id != exclude}
        return frozenset(e.correct_norm for e in self._t.errors if e.session_id in ids)


def _glossary_row(rec: GlossaryRecord) -> GlossaryRowData:
    return GlossaryRowData(
        id=rec.id,
        kind=rec.kind,
        text=rec.text,
        text_norm=rec.text_norm,
        meaning=rec.meaning,
        context_sentence=rec.context_sentence,
        domain=rec.domain,
        status=rec.status,
        seen_count=rec.seen_count,
        leech=rec.leech,
        created_at=rec.created_at,
        provisional_expires_at=rec.provisional_expires_at,
    )


class MemoryGlossaryRepo(_Repo):
    def _mine(self) -> list[GlossaryRecord]:
        return [g for g in self._t.glossary.values() if g.user_id == self._uid]

    def _own(self, item_id: UUID) -> GlossaryRecord:
        rec = self._t.glossary.get(item_id)
        if rec is None or rec.user_id != self._uid:
            raise LookupError("glossary item not found")
        return rec

    def _schedule(self, item_id: UUID, due: datetime) -> None:
        holder = self._t.review_states.get(item_id)
        state = new_state(due) if holder is None else replace(holder.state, due=due)
        self._t.review_states[item_id] = ReviewStateRecord(user_id=self._uid, state=state)

    def by_norms(self, norms: Collection[str]) -> Mapping[str, GlossaryRowData]:
        wanted = set(norms)
        return {g.text_norm: _glossary_row(g) for g in self._mine() if g.text_norm in wanted}

    def get_many(self, ids: Collection[UUID]) -> Mapping[UUID, GlossaryRowData]:
        wanted = set(ids)
        return {g.id: _glossary_row(g) for g in self._mine() if g.id in wanted}

    def apply(
        self,
        actions: Sequence[GlossaryAction],
        items: Sequence[IncomingItem],
        *,
        session_id: UUID,
        now: datetime,
    ) -> None:
        if any(not isinstance(a, Reject) for a in actions) and not self._owns_session(session_id):
            raise LookupError("session not found")
        for action in actions:
            match action:
                case InsertItem():
                    self._insert(action, session_id, now)
                case Reinforce():
                    rec = self._own(action.item_id)
                    rec.kind = action.kind
                    rec.seen_count = action.seen_count
                    rec.leech = action.leech
                    rec.last_seen_session_id = session_id
                    rec.updated_at = now
                    self._schedule(rec.id, action.due)
                case Promote():
                    rec = self._own(action.item_id)
                    rec.status = "confirmed"
                    rec.provisional_expires_at = None
                    rec.last_seen_session_id = session_id
                    rec.updated_at = now
                    self._t.review_states[rec.id] = ReviewStateRecord(
                        user_id=self._uid, state=new_state(action.first_due)
                    )
                case SetStatus():
                    rec = self._own(action.item_id)
                    rec.status = action.status
                    rec.provisional_expires_at = action.provisional_expires_at
                    rec.last_seen_session_id = session_id
                    rec.updated_at = now
                case _:
                    pass  # Reject: nothing to write.

    def _insert(self, action: InsertItem, session_id: UUID, now: datetime) -> None:
        if any(g.text_norm == action.text_norm for g in self._mine()):
            raise ValueError("duplicate glossary text for this user")
        rec = GlossaryRecord(
            id=uuid4(),
            user_id=self._uid,
            kind=action.item.kind,
            text=action.item.text,
            text_norm=action.text_norm,
            meaning=action.item.meaning,
            context_sentence=action.item.context_sentence,
            domain=action.item.domain,
            status=action.status,
            origin_session_id=session_id,
            last_seen_session_id=session_id,
            created_at=now,
            updated_at=now,
            provisional_expires_at=action.provisional_expires_at,
        )
        self._t.glossary[rec.id] = rec
        if action.first_due is not None:
            self._t.review_states[rec.id] = ReviewStateRecord(
                user_id=self._uid, state=new_state(action.first_due)
            )

    def _last_ratings(self, item_id: UUID) -> tuple[int, ...]:
        logs = sorted(
            (e for e in self._t.review_logs if e.item_id == item_id),
            key=lambda e: e.reviewed_at,
        )
        return tuple(e.rating for e in logs[-2:])

    def due_candidates(self, now: datetime) -> tuple[DueCandidate, ...]:
        found: list[DueCandidate] = []
        for rec in self._mine():
            holder = self._t.review_states.get(rec.id)
            if rec.status != "confirmed" or holder is None or holder.state.due > now:
                continue
            found.append(
                DueCandidate(
                    item_id=rec.id,
                    kind=rec.kind,
                    leech=rec.leech,
                    due=holder.state.due,
                    last_ratings=self._last_ratings(rec.id),
                )
            )
        return tuple(sorted(found, key=lambda c: (c.due, str(c.item_id))))

    def provisional(self, limit: int) -> tuple[GlossaryRowData, ...]:
        rows = sorted(
            (g for g in self._mine() if g.status == "provisional"),
            key=lambda g: (g.created_at, str(g.id)),
        )
        return tuple(_glossary_row(g) for g in rows[:limit])

    def count_provisional(self) -> int:
        return sum(1 for g in self._mine() if g.status == "provisional")

    def count_due(self, now: datetime) -> int:
        return len(self.due_candidates(now))

    def purge(self, now: datetime) -> int:
        cutoff = now - timedelta(days=DECLINED_RETENTION_DAYS)
        doomed = {
            g.id
            for g in self._mine()
            if (
                g.status == "provisional"
                and g.provisional_expires_at is not None
                and g.provisional_expires_at < now
            )
            or (g.status == "declined" and g.created_at < cutoff)
        }
        for item_id in doomed:
            del self._t.glossary[item_id]
            self._t.review_states.pop(item_id, None)
        self._t.review_logs = [e for e in self._t.review_logs if e.item_id not in doomed]
        return len(doomed)


class MemoryReviewRepo(_Repo):
    def state(self, item_id: UUID) -> FsrsState | None:
        holder = self._t.review_states.get(item_id)
        return holder.state if holder is not None and holder.user_id == self._uid else None

    def save_state(self, item_id: UUID, state: FsrsState) -> None:
        if not self._owns_item(item_id):
            raise LookupError("glossary item not found")
        self._t.review_states[item_id] = ReviewStateRecord(user_id=self._uid, state=state)

    def log(
        self, session_id: UUID, item_id: UUID, rating: int, now: datetime, state_before: FsrsState
    ) -> bool:
        if not (self._owns_session(session_id) and self._owns_item(item_id)):
            raise LookupError("session or glossary item not found")
        if any(e.session_id == session_id and e.item_id == item_id for e in self._t.review_logs):
            return False
        self._t.review_logs.append(
            ReviewLogRecord(
                id=uuid4(),
                user_id=self._uid,
                session_id=session_id,
                item_id=item_id,
                rating=rating,
                reviewed_at=now,
                state_before=state_before,
            )
        )
        return True

    def session_logs(self, session_id: UUID) -> tuple[ReviewLogRow, ...]:
        logs = sorted(
            (
                e
                for e in self._t.review_logs
                if e.user_id == self._uid and e.session_id == session_id
            ),
            key=lambda e: (e.reviewed_at, str(e.id)),
        )
        return tuple(
            ReviewLogRow(
                item_id=e.item_id,
                rating=e.rating,
                reviewed_at=e.reviewed_at,
                state_before=e.state_before,
            )
            for e in logs
        )

    def set_log_rating(self, session_id: UUID, item_id: UUID, rating: int) -> None:
        for entry in self._t.review_logs:
            mine = entry.user_id == self._uid
            if mine and entry.session_id == session_id and entry.item_id == item_id:
                entry.rating = rating


# --- subjects and notes --------------------------------------------------------------------
#
# The search is a model of the Postgres statement, not a copy (plan rulings 2 and 3): text and
# query are normalised with `name_norm` and split into whole `\w+` tokens; a passage matches when
# every query token is one of its tokens (no stemming, no stop words, no trigrams); concept seeds
# are exact `name_norm` matches; the title fallback needs every token in the title.

_WORD = re.compile(r"\w+")
_MARK = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)
EXCERPT_WORDS = 25
EXCERPT_LEAD = 8  # words kept before the first match, so the markers are always in the window
MAX_SEEDS = 5
MAX_DEPTH = 2
FUZZY_MAX_TOKENS = 3


def search_tokens(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(name_norm(text)))


def excerpt(text: str, wanted: Collection[str]) -> str:
    """25 words of the passage around the first matched word, that word inside `[[` `]]`
    (punctuation stays outside the markers); plain text."""
    words = text.split()
    tokens = frozenset(wanted)
    index = next((i for i, w in enumerate(words) if tokens & frozenset(search_tokens(w))), None)
    if index is None:
        return " ".join(words[:EXCERPT_WORDS])
    start = max(0, index - EXCERPT_LEAD)
    window = words[start : start + EXCERPT_WORDS]
    match = _MARK.match(window[index - start])
    if match is not None:
        pre, core, post = match.groups()
        window[index - start] = f"{pre}[[{core}]]{post}"
    return " ".join(window)


@dataclass(slots=True)
class _Found:
    passages: int = 0
    excerpts: tuple[str, ...] = ()
    concepts: list[tuple[int, str, str]] = field(default_factory=list)  # (depth, norm, name)


def _subject_row(rec: SubjectRecord) -> SubjectRow:
    return SubjectRow(
        id=rec.id, subject=rec.subject, created_at=rec.created_at, updated_at=rec.updated_at
    )


def _source_row(rec: SourceRecord) -> SourceRow:
    return SourceRow(
        id=rec.id,
        subject_id=rec.subject_id,
        kind=rec.kind,
        title=rec.title,
        url=rec.url,
        created_at=rec.created_at,
    )


def _note_row(rec: NoteRecord) -> NoteRow:
    return NoteRow(
        id=rec.id,
        subject_id=rec.subject_id,
        source_id=rec.source_id,
        title=rec.title,
        lesson_ref=rec.lesson_ref,
        position=rec.position,
        raw_text=rec.raw_text,
        raw_had_nul=rec.raw_had_nul,
        clean_text=rec.clean_text,
        clean_version=rec.clean_version,
        idempotency_key=rec.idempotency_key,
        origin=rec.origin,
        supersedes_note_id=rec.supersedes_note_id,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


def _upsert_concept(tables: Tables, user_id: UUID, subject_id: UUID, concept: ConceptName) -> UUID:
    """The subject's concept with this name_norm, created with this spelling if new."""
    for rec in tables.concepts.values():
        if (
            rec.user_id == user_id
            and rec.subject_id == subject_id
            and rec.name_norm == concept.name_norm
        ):
            return rec.id
    new = ConceptRecord(
        id=uuid4(),
        user_id=user_id,
        subject_id=subject_id,
        name=concept.name,
        name_norm=concept.name_norm,
    )
    tables.concepts[new.id] = new
    return new.id


def _template_row(rec: TemplateRecord, *, with_html: bool) -> TemplateRow:
    return TemplateRow(
        id=rec.id,
        subject_id=rec.subject_id,
        spec=rec.spec,
        html=rec.html if with_html else None,
        html_sha256=rec.html_sha256,
        bytes=rec.bytes,
        guide_version=rec.guide_version,
        status=rec.status,
        supersedes_template_id=rec.supersedes_template_id,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
        retired_at=rec.retired_at,
    )


class MemorySubjectRepo(_Repo):
    def _mine(self) -> list[SubjectRecord]:
        return [s for s in self._t.subjects.values() if s.user_id == self._uid]

    def _record(self, subject_id: UUID) -> SubjectRecord | None:
        rec = self._t.subjects.get(subject_id)
        return rec if rec is not None and rec.user_id == self._uid else None

    def list(self) -> tuple[SubjectRow, ...]:
        rows = sorted(
            self._mine(), key=lambda s: (s.subject.status != "active", s.subject.name, s.id)
        )
        return tuple(_subject_row(s) for s in rows)

    def get(self, subject_id: UUID) -> SubjectRow | None:
        rec = self._record(subject_id)
        return None if rec is None else _subject_row(rec)

    def get_for_update(self, subject_id: UUID) -> SubjectRow | None:
        return self.get(subject_id)  # one writer at a time in memory: a plain read

    def create(self, subject: Subject, now: datetime) -> SubjectRow:
        rec = SubjectRecord(
            id=uuid4(), user_id=self._uid, subject=subject, created_at=now, updated_at=now
        )
        self._t.subjects[rec.id] = rec
        return _subject_row(rec)

    def update(self, subject_id: UUID, subject: Subject, now: datetime) -> bool:
        rec = self._record(subject_id)
        if rec is None:
            return False
        rec.subject = subject
        rec.updated_at = now
        return True


class MemoryNoteRepo(_Repo):
    # --- ownership ---------------------------------------------------------------------------

    def _mine(self) -> list[NoteRecord]:
        return [n for n in self._t.notes.values() if n.user_id == self._uid]

    def _subject(self, subject_id: UUID) -> SubjectRecord:
        rec = self._t.subjects.get(subject_id)
        if rec is None or rec.user_id != self._uid:
            raise LookupError("subject not found")
        return rec

    def _source(self, source_id: UUID) -> SourceRecord | None:
        rec = self._t.sources.get(source_id)
        return rec if rec is not None and rec.user_id == self._uid else None

    def _note(self, note_id: UUID) -> NoteRecord | None:
        rec = self._t.notes.get(note_id)
        return rec if rec is not None and rec.user_id == self._uid else None

    def _concept_id(self, subject_id: UUID, concept: ConceptName) -> UUID:
        return _upsert_concept(self._t, self._uid, subject_id, concept)

    # --- sources and notes -------------------------------------------------------------------

    def upsert_source(self, subject_id: UUID, source: ValidSource, now: datetime) -> SourceRow:
        self._subject(subject_id)
        for rec in self._t.sources.values():
            if (
                rec.user_id == self._uid
                and rec.subject_id == subject_id
                and rec.kind == source.kind
                and rec.title == source.title
            ):
                if source.url is not None:
                    rec.url = source.url
                return _source_row(rec)
        rec = SourceRecord(
            id=uuid4(),
            user_id=self._uid,
            subject_id=subject_id,
            kind=source.kind,
            title=source.title,
            url=source.url,
            created_at=now,
        )
        self._t.sources[rec.id] = rec
        return _source_row(rec)

    def source(self, source_id: UUID) -> SourceRow | None:
        rec = self._source(source_id)
        return None if rec is None else _source_row(rec)

    def by_idempotency_key(self, key: str) -> NoteRow | None:
        return next((_note_row(n) for n in self._mine() if n.idempotency_key == key), None)

    def latest_by_lesson(
        self, subject_id: UUID, source_id: UUID | None, lesson_ref: str
    ) -> NoteRow | None:
        found = [
            n
            for n in self._mine()
            if n.subject_id == subject_id
            and n.source_id == source_id
            and n.lesson_ref == lesson_ref
        ]
        if not found:
            return None
        return _note_row(max(found, key=lambda n: (n.created_at, n.id)))

    def insert(
        self,
        note: ValidNote,
        *,
        subject_id: UUID,
        source_id: UUID | None,
        origin: Origin,
        supersedes: UUID | None,
        now: datetime,
    ) -> NoteRow:
        self._subject(subject_id)
        if source_id is not None:
            source = self._source(source_id)
            if source is None or source.subject_id != subject_id:
                raise LookupError("source not found")
        if supersedes is not None and self._note(supersedes) is None:
            raise LookupError("note not found")
        key = idempotency_key(subject_id, source_id, note.lesson_ref, note.raw_text)
        if any(n.idempotency_key == key for n in self._mine()):
            raise ValueError("duplicate idempotency key for this user")
        rec = NoteRecord(
            id=uuid4(),
            user_id=self._uid,
            subject_id=subject_id,
            source_id=source_id,
            title=note.title,
            title_norm=name_norm(note.title),
            lesson_ref=note.lesson_ref,
            position=note.position,
            raw_text=note.raw_text,
            raw_had_nul=note.raw_had_nul,
            raw_sha256=note.raw_sha256,
            clean_text=note.clean_text,
            clean_version=CLEAN_VERSION,
            idempotency_key=key,
            origin=origin,
            supersedes_note_id=supersedes,
            created_at=now,
            updated_at=now,
        )
        self._t.notes[rec.id] = rec
        for seq, text in enumerate(note.passages, start=1):
            self._t.note_passages.append(PassageRecord(self._uid, rec.id, seq, text))
        for concept in note.concepts:
            self._t.note_concepts.append(
                NoteConceptRecord(self._uid, rec.id, self._concept_id(subject_id, concept))
            )
        for link in note.links:
            edge = ConceptLinkRecord(
                self._uid,
                self._concept_id(subject_id, link.from_name),
                self._concept_id(subject_id, link.to_name),
                link.kind,
            )
            if edge not in self._t.concept_links:
                self._t.concept_links.append(edge)
        return _note_row(rec)

    def get(self, note_id: UUID) -> NoteRow | None:
        rec = self._note(note_id)
        return None if rec is None else _note_row(rec)

    def list_for_subject(self, subject_id: UUID) -> tuple[NoteListRow, ...]:
        mine = self._mine()
        replaced = {n.supersedes_note_id for n in mine if n.supersedes_note_id is not None}
        rows: list[NoteListRow] = []
        for n in mine:
            if n.subject_id != subject_id:
                continue
            src = self._source(n.source_id) if n.source_id is not None else None
            rows.append(
                NoteListRow(
                    id=n.id,
                    source_id=n.source_id,
                    source_title=src.title if src is not None else None,
                    source_kind=src.kind if src is not None else None,
                    title=n.title,
                    lesson_ref=n.lesson_ref,
                    position=n.position,
                    origin=n.origin,
                    created_at=n.created_at,
                    supersedes_note_id=n.supersedes_note_id,
                    superseded=n.id in replaced,
                )
            )
        return tuple(
            sorted(
                rows,
                key=lambda r: (
                    r.source_title is None,
                    r.source_title or "",
                    r.position is None,
                    r.position or 0,
                    r.created_at,
                    r.id,
                ),
            )
        )

    def _passages_of(self, note_id: UUID) -> list[PassageRecord]:
        rows = [p for p in self._t.note_passages if p.user_id == self._uid and p.note_id == note_id]
        return sorted(rows, key=lambda p: p.seq)

    def passages(self, note_id: UUID, start: int, stop: int) -> tuple[PassageRow, ...]:
        return tuple(
            PassageRow(seq=p.seq, text=p.text)
            for p in self._passages_of(note_id)
            if start <= p.seq <= stop
        )

    def passage_count(self, note_id: UUID) -> int:
        return len(self._passages_of(note_id))

    def concept_by_norm(self, subject_id: UUID, name_norm: str) -> ConceptRow | None:
        for c in self._t.concepts.values():
            if c.user_id == self._uid and c.subject_id == subject_id and c.name_norm == name_norm:
                return ConceptRow(
                    id=c.id, subject_id=c.subject_id, name=c.name, name_norm=c.name_norm
                )
        return None

    def concepts(self, note_id: UUID) -> tuple[ConceptRow, ...]:
        rows = sorted(
            (
                self._t.concepts[nc.concept_id]
                for nc in self._t.note_concepts
                if nc.user_id == self._uid and nc.note_id == note_id
            ),
            key=lambda c: (c.name_norm, c.id),
        )
        return tuple(
            ConceptRow(id=c.id, subject_id=c.subject_id, name=c.name, name_norm=c.name_norm)
            for c in rows
        )

    # --- search ------------------------------------------------------------------------------

    def _hops(self, query_norm: str, subject_id: UUID | None) -> dict[UUID, int]:
        """Concept id -> depth: exact-name seeds at 0, then `related` both ways and
        `prerequisite` outgoing, two hops at most, each concept at its first depth."""
        seeds = sorted(
            (
                c
                for c in self._t.concepts.values()
                if c.user_id == self._uid
                and (subject_id is None or c.subject_id == subject_id)
                and c.name_norm == query_norm
            ),
            key=lambda c: c.id,
        )[:MAX_SEEDS]
        depth = {c.id: 0 for c in seeds}
        frontier = [c.id for c in seeds]
        links = [edge for edge in self._t.concept_links if edge.user_id == self._uid]
        for hop in range(1, MAX_DEPTH + 1):
            reached: list[UUID] = []
            for concept_id in frontier:
                for edge in links:
                    for other in _neighbours(edge, concept_id):
                        if other not in depth:
                            depth[other] = hop
                            reached.append(other)
            frontier = reached
        return depth

    def search(self, query: str, subject_id: UUID | None, limit: int) -> tuple[SearchHit, ...]:
        tokens = search_tokens(query)
        if not tokens or limit < 1:
            return ()
        wanted = frozenset(tokens)
        notes = {n.id: n for n in self._mine() if subject_id is None or n.subject_id == subject_id}
        found: dict[UUID, _Found] = {}
        for note in notes.values():
            matched = [
                p.text
                for p in self._passages_of(note.id)
                if wanted <= frozenset(search_tokens(p.text))
            ]
            if matched:
                found[note.id] = _Found(len(matched), (excerpt(matched[0], wanted),))
        hops = self._hops(name_norm(query), subject_id)
        for attached in self._t.note_concepts:
            if (
                attached.user_id == self._uid
                and attached.note_id in notes
                and attached.concept_id in hops
            ):
                concept = self._t.concepts[attached.concept_id]
                entry = found.setdefault(attached.note_id, _Found())
                entry.concepts.append((hops[concept.id], concept.name_norm, concept.name))
        fuzzy = False
        if not found and len(tokens) <= FUZZY_MAX_TOKENS:
            fuzzy = True
            for note in notes.values():
                if wanted <= frozenset(search_tokens(note.title)):
                    found[note.id] = _Found()
        ordered = sorted(
            found.items(),
            key=lambda item: (
                -(item[1].passages + len(item[1].concepts)),
                min((d for d, _, _ in item[1].concepts), default=MAX_DEPTH + 1),
                -notes[item[0]].updated_at.timestamp(),
                item[0],
            ),
        )
        return tuple(
            SearchHit(
                note_id=note_id,
                passages=entry.passages,
                concepts=tuple(name for _, _, name in sorted(entry.concepts)),
                depth=min((d for d, _, _ in entry.concepts), default=None),
                fuzzy=fuzzy,
                excerpts=entry.excerpts,
            )
            for note_id, entry in ordered[:limit]
        )


def _neighbours(edge: ConceptLinkRecord, concept_id: UUID) -> tuple[UUID, ...]:
    if edge.kind == "related":
        if edge.from_id == concept_id:
            return (edge.to_id,)
        if edge.to_id == concept_id:
            return (edge.from_id,)
    elif edge.from_id == concept_id:  # prerequisite: from the required concept onwards
        return (edge.to_id,)
    return ()


class MemoryTemplateRepo(_Repo):
    def _mine(self, subject_id: UUID) -> list[TemplateRecord]:
        return [
            t
            for t in self._t.exercise_templates.values()
            if t.user_id == self._uid and t.subject_id == subject_id
        ]

    def list_active(self, subject_id: UUID) -> tuple[TemplateRow, ...]:
        rows = sorted(
            (t for t in self._mine(subject_id) if t.status == "active"),
            key=lambda t: (t.name_norm, t.id),
        )
        return tuple(_template_row(t, with_html=False) for t in rows)

    def get(self, template_id: UUID) -> TemplateRow | None:
        rec = self._t.exercise_templates.get(template_id)
        if rec is None or rec.user_id != self._uid:
            return None
        return _template_row(rec, with_html=True)

    def find_same(self, subject_id: UUID, spec_sha256: str, html_sha256: str) -> TemplateRow | None:
        for rec in sorted(self._mine(subject_id), key=lambda t: (t.created_at, t.id)):
            if (
                rec.status == "active"
                and rec.html_sha256 == html_sha256
                and spec_hash(rec.spec) == spec_sha256
            ):
                return _template_row(rec, with_html=True)
        return None

    def active_by_name(self, subject_id: UUID, name_norm: str) -> TemplateRow | None:
        for rec in self._mine(subject_id):
            if rec.status == "active" and rec.name_norm == name_norm:
                return _template_row(rec, with_html=False)
        return None

    def create(
        self,
        subject_id: UUID,
        spec: TemplateSpec,
        html: str,
        guide_version: int,
        now: datetime,
        supersedes: UUID | None = None,
    ) -> TemplateRow:
        subject = self._t.subjects.get(subject_id)
        if subject is None or subject.user_id != self._uid:
            raise LookupError("subject not found")
        old = None
        if supersedes is not None:
            old = self._t.exercise_templates.get(supersedes)
            if old is None or old.user_id != self._uid or old.subject_id != subject_id:
                raise LookupError("template not found")
            if old.status == "active":
                old.status, old.retired_at, old.updated_at = "retired", now, now
        norm = name_norm(spec.name)
        if self.active_by_name(subject_id, norm) is not None:
            raise ValueError("an active template of this subject has this name")
        encoded = html.encode("utf-8")
        rec = TemplateRecord(
            id=uuid4(),
            user_id=self._uid,
            subject_id=subject_id,
            spec=spec,
            name_norm=norm,
            html=html,
            html_sha256=hashlib.sha256(encoded).hexdigest(),
            bytes=len(encoded),
            guide_version=guide_version,
            status="active",
            supersedes_template_id=supersedes,
            created_at=now,
            updated_at=now,
            retired_at=None,
        )
        self._t.exercise_templates[rec.id] = rec
        return _template_row(rec, with_html=True)

    def retire(self, template_id: UUID, now: datetime) -> bool:
        rec = self._t.exercise_templates.get(template_id)
        if rec is None or rec.user_id != self._uid or rec.status != "active":
            return False
        rec.status, rec.retired_at, rec.updated_at = "retired", now, now
        return True


class MemoryInstanceRepo(_Repo):
    def _row(self, rec: InstanceRecord) -> InstanceRow:
        concept = self._t.concepts.get(rec.concept_id) if rec.concept_id is not None else None
        return InstanceRow(
            id=rec.id,
            subject_id=rec.subject_id,
            note_id=rec.note_id,
            seq=rec.seq,
            template_id=rec.template_id,
            purpose=rec.template.purpose,
            practice_kind=rec.template.practice_kind,
            grading_kind=rec.template.grading_kind,
            params=_json_copy(rec.params),
            prompt_text=rec.prompt_text,
            concept_id=rec.concept_id,
            concept=concept.name if concept is not None else None,
            origin=rec.origin,
            content_key=rec.content_key,
            status=rec.status,
            supersedes_instance_id=rec.supersedes_instance_id,
            created_at=rec.created_at,
            retired_at=rec.retired_at,
        )

    def _mine(self) -> list[InstanceRecord]:
        return [i for i in self._t.template_instances.values() if i.user_id == self._uid]

    def insert(
        self,
        *,
        subject_id: UUID,
        note_id: UUID | None,
        template: TemplateRow,
        params: Mapping[str, Any],
        prompt_text: str,
        concept_id: UUID | None,
        origin: InstanceOrigin,
        key: str,
        now: datetime,
        replaces: UUID | None = None,
    ) -> tuple[InstanceRow, bool]:
        subject = self._t.subjects.get(subject_id)
        if subject is None or subject.user_id != self._uid:
            raise LookupError("subject not found")
        if note_id is not None:
            note = self._t.notes.get(note_id)
            if note is None or note.user_id != self._uid or note.subject_id != subject_id:
                raise LookupError("note not found")
        stored = self._t.exercise_templates.get(template.id)
        if (
            stored is None
            or stored.user_id != self._uid
            or stored.subject_id != subject_id
            or stored.status != "active"
        ):
            raise LookupError("template not found")
        if concept_id is not None:
            found = self._t.concepts.get(concept_id)
            if found is None or found.user_id != self._uid or found.subject_id != subject_id:
                raise LookupError("concept not found")
        old = None
        if replaces is not None:
            old = self._t.template_instances.get(replaces)
            if old is None or old.user_id != self._uid or old.subject_id != subject_id:
                raise LookupError("instance not found")
        existing = next(
            (i for i in self._mine() if i.status == "active" and i.content_key == key), None
        )
        if old is not None and old.status == "active" and old is not existing:
            old.status, old.retired_at = "retired", now
        if existing is not None:
            return self._row(existing), False
        seq = None
        if note_id is not None:
            seq = 1 + max((i.seq or 0 for i in self._mine() if i.note_id == note_id), default=0)
        rec = InstanceRecord(
            id=uuid4(),
            user_id=self._uid,
            subject_id=subject_id,
            note_id=note_id,
            seq=seq,
            template_id=template.id,
            template=stored.spec,
            params=_json_copy(params),
            prompt_text=prompt_text,
            concept_id=concept_id,
            origin=origin,
            content_key=key,
            status="active",
            supersedes_instance_id=replaces,
            created_at=now,
            retired_at=None,
        )
        self._t.template_instances[rec.id] = rec
        return self._row(rec), True

    def get(self, instance_id: UUID) -> InstanceRow | None:
        rec = self._t.template_instances.get(instance_id)
        if rec is None or rec.user_id != self._uid:
            return None
        return self._row(rec)

    def for_note(self, note_id: UUID) -> tuple[InstanceRow, ...]:
        rows = sorted(
            (i for i in self._mine() if i.note_id == note_id and i.status == "active"),
            key=lambda i: (i.seq or 0, i.id),
        )
        return tuple(self._row(i) for i in rows)

    def active_counts(self, subject_id: UUID) -> Mapping[UUID, int]:
        counts: dict[UUID, int] = {}
        for i in self._mine():
            if i.subject_id == subject_id and i.status == "active":
                counts[i.template_id] = counts.get(i.template_id, 0) + 1
        return counts


def _plan_row(rec: PracticePlanRecord) -> PracticePlanRow:
    return PracticePlanRow(
        id=rec.id,
        subject_id=rec.subject_id,
        version=rec.version,
        status=rec.status,
        start_date=rec.start_date,
        inputs=rec.inputs,
        total_sessions=rec.total_sessions,
        rationale=_json_copy(rec.rationale),
        created_at=rec.created_at,
    )


def _practice_session_row(rec: PracticeSessionRecord) -> PracticeSessionRow:
    return PracticeSessionRow(
        id=rec.id,
        subject_id=rec.subject_id,
        plan_id=rec.plan_id,
        mode=rec.mode,
        status=rec.status,
        started_at=rec.started_at,
        ended_at=rec.ended_at,
        self_rating=rec.self_rating,
        summary=_json_copy(rec.summary) if rec.summary is not None else None,
        item_count=rec.item_count,
    )


def _practice_item_row(rec: PracticeItemRecord) -> PracticeItemRow:
    return PracticeItemRow(
        id=rec.id,
        session_id=rec.session_id,
        seq=rec.seq,
        instance_id=rec.instance_id,
        draw_seed=rec.draw_seed,
        draw=rec.draw,
        shown_at=rec.shown_at,
        answer=_json_copy(rec.answer) if rec.answer is not None else None,
        correct=rec.correct,
        score=rec.score,
        answered_at=rec.answered_at,
        source=rec.source,
    )


class MemoryPracticeRepo(_Repo):
    """Practice plans, sessions and items; one open practice session per user (ruling 30)."""

    def _subject(self, subject_id: UUID) -> SubjectRecord:
        rec = self._t.subjects.get(subject_id)
        if rec is None or rec.user_id != self._uid:
            raise LookupError("subject not found")
        return rec

    def _plans(self, subject_id: UUID) -> list[PracticePlanRecord]:
        return [
            p
            for p in self._t.practice_plans.values()
            if p.user_id == self._uid and p.subject_id == subject_id
        ]

    def _session(self, session_id: UUID) -> PracticeSessionRecord | None:
        rec = self._t.practice_sessions.get(session_id)
        return rec if rec is not None and rec.user_id == self._uid else None

    def _items_of(self, session_id: UUID) -> list[PracticeItemRecord]:
        return sorted(
            (
                i
                for i in self._t.practice_items.values()
                if i.user_id == self._uid and i.session_id == session_id
            ),
            key=lambda i: i.seq,
        )

    def _instance_of(self, subject_id: UUID, instance_id: UUID) -> InstanceRecord:
        rec = self._t.template_instances.get(instance_id)
        if rec is None or rec.user_id != self._uid or rec.subject_id != subject_id:
            raise LookupError("instance not found")
        return rec

    def active_plan(self, subject_id: UUID) -> PracticePlanRow | None:
        active = [p for p in self._plans(subject_id) if p.status == "active"]
        return _plan_row(active[0]) if active else None

    def create_plan(
        self,
        subject_id: UUID,
        inputs: PlanInputs,
        start_date: date,
        now: datetime,
        rationale: Mapping[str, Any],
    ) -> PracticePlanRow:
        self._subject(subject_id)
        stored = _json_copy(rationale)
        plans = self._plans(subject_id)
        for plan in plans:
            plan.status = "superseded"
        rec = PracticePlanRecord(
            id=uuid4(),
            user_id=self._uid,
            subject_id=subject_id,
            version=1 + max((p.version for p in plans), default=0),
            status="active",
            start_date=start_date,
            inputs=inputs,
            total_sessions=total_sessions(inputs, start_date),
            rationale=stored,
            created_at=now,
        )
        self._t.practice_plans[rec.id] = rec
        return _plan_row(rec)

    def completed_count(self, plan_id: UUID) -> int:
        count = 0
        for rec in self._t.practice_sessions.values():
            if rec.user_id != self._uid or rec.plan_id != plan_id:
                continue
            if rec.mode == "class":
                count += any(i.answered_at is not None for i in self._items_of(rec.id))
            else:
                count += rec.status == "closed"
        return count

    def open_session(self) -> PracticeSessionRow | None:
        for rec in self._t.practice_sessions.values():
            if rec.user_id == self._uid and rec.status == "open":
                return _practice_session_row(rec)
        return None

    def create_session(
        self,
        session_id: UUID,
        *,
        subject_id: UUID,
        plan_id: UUID | None,
        mode: PracticeMode,
        items: Sequence[DrawnItem],
        now: datetime,
    ) -> PracticeSessionRow:
        self._subject(subject_id)
        if plan_id is not None:
            plan = self._t.practice_plans.get(plan_id)
            if plan is None or plan.user_id != self._uid or plan.subject_id != subject_id:
                raise LookupError("plan not found")
        for item in items:
            self._instance_of(subject_id, item.instance_id)
        if len({item.seq for item in items}) != len(items):
            raise ValueError("duplicate seq in the session")
        if self.open_session() is not None:
            raise OpenPracticeExists
        rec = PracticeSessionRecord(
            id=session_id,
            user_id=self._uid,
            subject_id=subject_id,
            plan_id=plan_id,
            mode=mode,
            started_at=now,
            item_count=len(items),
        )
        self._t.practice_sessions[rec.id] = rec
        for item in items:
            self._insert_item(session_id, item, shown_at=None)
        return _practice_session_row(rec)

    def _insert_item(
        self, session_id: UUID, item: DrawnItem, *, shown_at: datetime | None
    ) -> PracticeItemRecord:
        rec = PracticeItemRecord(
            id=item.item_id,
            user_id=self._uid,
            session_id=session_id,
            seq=item.seq,
            instance_id=item.instance_id,
            draw_seed=item.draw_seed,
            draw=item.draw,
            shown_at=shown_at,
        )
        self._t.practice_items[rec.id] = rec
        return rec

    def session(self, session_id: UUID) -> PracticeSessionRow | None:
        rec = self._session(session_id)
        return _practice_session_row(rec) if rec is not None else None

    def sessions_for_subject(
        self, subject_id: UUID, limit: int = 50
    ) -> tuple[PracticeSessionRow, ...]:
        mine = [
            r
            for r in self._t.practice_sessions.values()
            if r.user_id == self._uid and r.subject_id == subject_id
        ]
        mine.sort(key=lambda r: (r.started_at, r.id), reverse=True)
        return tuple(_practice_session_row(r) for r in mine[:limit])

    def items(self, session_id: UUID) -> tuple[PracticeItemRow, ...]:
        return tuple(_practice_item_row(i) for i in self._items_of(session_id))

    def item_counts(self, session_ids: Sequence[UUID]) -> Mapping[UUID, SessionItemCounts]:
        counts: dict[UUID, SessionItemCounts] = {}
        for session_id in dict.fromkeys(session_ids):
            rows = self._items_of(session_id)
            if rows:
                counts[session_id] = SessionItemCounts(
                    items=len(rows),
                    answered=sum(1 for r in rows if r.answered_at is not None),
                    correct=sum(1 for r in rows if r.correct is True),
                )
        return counts

    def item(self, item_id: UUID) -> PracticeItemRow | None:
        rec = self._t.practice_items.get(item_id)
        return _practice_item_row(rec) if rec is not None and rec.user_id == self._uid else None

    def add_item(self, session_id: UUID, item: DrawnItem, now: datetime) -> PracticeItemRow:
        session = self._session(session_id)
        if session is None:
            raise LookupError("session not found")
        self._instance_of(session.subject_id, item.instance_id)
        if session.status != "open":
            raise ValueError("the session is not open")
        if any(i.seq == item.seq for i in self._items_of(session_id)):
            raise ValueError("duplicate seq in the session")
        return _practice_item_row(self._insert_item(session_id, item, shown_at=now))

    def mark_shown(self, item_id: UUID, now: datetime) -> datetime | None:
        rec = self._t.practice_items.get(item_id)
        if rec is None or rec.user_id != self._uid:
            return None
        if rec.shown_at is None:
            rec.shown_at = now
        return rec.shown_at

    def record(
        self,
        item_id: UUID,
        *,
        answer: Mapping[str, Any],
        correct: bool | None,
        score: int,
        source: ItemSource,
        now: datetime,
    ) -> PracticeItemRow | None:
        rec = self._t.practice_items.get(item_id)
        if rec is None or rec.user_id != self._uid:
            raise LookupError("item not found")
        if rec.answered_at is not None:
            return None
        if rec.shown_at is None or now < rec.shown_at:
            raise ValueError("an item is answered only after it was shown")
        rec.answer = _json_copy(answer)
        rec.correct, rec.score, rec.answered_at, rec.source = correct, score, now, source
        return _practice_item_row(rec)

    def mark_incomplete(self, session_id: UUID, now: datetime) -> None:
        rec = self._session(session_id)
        if rec is not None and rec.status == "open":
            rec.status, rec.ended_at = "incomplete", now

    def close(
        self,
        session_id: UUID,
        *,
        summary: Mapping[str, Any],
        self_rating: int | None,
        now: datetime,
    ) -> None:
        rec = self._session(session_id)
        if rec is not None and rec.status == "open":
            rec.summary = _json_copy(summary)
            rec.status, rec.ended_at, rec.self_rating = "closed", now, self_rating

    def candidates(self, subject_id: UUID) -> tuple[Candidate, ...]:
        last: dict[UUID, datetime] = {}
        for item in self._t.practice_items.values():
            if item.user_id == self._uid and item.shown_at is not None:
                seen = last.get(item.instance_id)
                last[item.instance_id] = max(seen, item.shown_at) if seen else item.shown_at
        found = []
        for rec in self._t.template_instances.values():
            if (
                rec.user_id == self._uid
                and rec.subject_id == subject_id
                and rec.status == "active"
                and rec.template.purpose == "exercise"
            ):
                template = self._t.exercise_templates[rec.template_id]
                found.append(
                    Candidate(
                        instance_id=rec.id,
                        spec=template.spec,
                        params=_json_copy(rec.params),
                        last_shown_at=last.get(rec.id),
                    )
                )
        return tuple(sorted(found, key=lambda c: c.instance_id))


class MemoryProgressRepo(_Repo):
    """The metric facts over the memory tables, as PgProgressRepo reads them (ruling 70)."""

    def _subject_ok(self, subject_id: UUID) -> bool:
        rec = self._t.subjects.get(subject_id)
        return rec is not None and rec.user_id == self._uid

    def _sessions(self, subject_id: UUID) -> list[PracticeSessionRecord]:
        return [
            s
            for s in self._t.practice_sessions.values()
            if s.user_id == self._uid and s.subject_id == subject_id
        ]

    def _answered(self, subject_id: UUID) -> list[PracticeItemRecord]:
        sessions = {s.id for s in self._sessions(subject_id)}
        found = [
            (i.answered_at, i.id, i)
            for i in self._t.practice_items.values()
            if i.user_id == self._uid and i.session_id in sessions and i.answered_at is not None
        ]
        return [i for _, _, i in sorted(found, key=lambda e: (e[0], e[1]))]

    def showings(self, subject_id: UUID) -> tuple[ShowingFact, ...]:
        facts: list[ShowingFact] = []
        for item in self._answered(subject_id):
            instance = self._t.template_instances[item.instance_id]
            if item.shown_at is None or item.answered_at is None or item.score is None:
                continue  # pragma: no cover - record() needs a showing and stores a score
            facts.append(
                ShowingFact(
                    instance_id=instance.id,
                    template_id=instance.template_id,
                    concept_id=instance.concept_id,
                    grading_kind=instance.template.grading_kind,
                    correct=item.correct,
                    score=item.score,
                    shown_at=item.shown_at,
                    answered_at=item.answered_at,
                )
            )
        return tuple(facts)

    def practice_days(self, subject_id: UUID, tz_name: str) -> tuple[datetime, ...]:
        zone = ZoneInfo(tz_name)
        first: dict[date, datetime] = {}
        for item in self._answered(subject_id):
            if item.answered_at is None:
                continue  # pragma: no cover - _answered keeps answered items only
            day = item.answered_at.astimezone(zone).date()
            if day not in first or item.answered_at < first[day]:
                first[day] = item.answered_at
        return tuple(sorted(first.values()))

    def sessions(self, subject_id: UUID) -> tuple[SessionFact, ...]:
        facts: list[tuple[datetime, UUID, SessionFact]] = []
        for rec in self._sessions(subject_id):
            moments = [rec.started_at]
            for item in self._t.practice_items.values():
                if item.user_id == self._uid and item.session_id == rec.id:
                    moments += [m for m in (item.shown_at, item.answered_at) if m is not None]
            fact = SessionFact(
                started_at=rec.started_at, status=rec.status, last_activity_at=max(moments)
            )
            facts.append((rec.started_at, rec.id, fact))
        return tuple(f for _, _, f in sorted(facts, key=lambda e: (e[0], e[1])))

    def activity(self, subject_id: UUID) -> ActivityFacts:
        if not self._subject_ok(subject_id):
            return ActivityFacts((), (), (), ())

        def mine(rows: Iterable[Any]) -> tuple[datetime, ...]:
            return tuple(
                sorted(
                    r.created_at
                    for r in rows
                    if r.user_id == self._uid and r.subject_id == subject_id
                )
            )

        searches = tuple(
            sorted(
                a.at
                for a in self._t.audit
                if a.user_id == self._uid
                and a.event == "notes_searched"
                and a.meta.get("subject_id") == str(subject_id)
            )
        )
        return ActivityFacts(
            notes=mine(self._t.notes.values()),
            templates=mine(self._t.exercise_templates.values()),
            instances=mine(self._t.template_instances.values()),
            searches=searches,
        )

    def concepts(self, subject_id: UUID) -> tuple[ConceptFact, ...]:
        rows = sorted(
            (
                c
                for c in self._t.concepts.values()
                if c.user_id == self._uid and c.subject_id == subject_id
            ),
            key=lambda c: (c.name_norm, c.id),
        )
        return tuple(ConceptFact(c.id, c.name, c.name_norm) for c in rows)

    def templates(self, subject_id: UUID) -> tuple[TemplateFact, ...]:
        rows = sorted(
            (
                t
                for t in self._t.exercise_templates.values()
                if t.user_id == self._uid and t.subject_id == subject_id
            ),
            key=lambda t: (t.name_norm, t.id),
        )
        return tuple(TemplateFact(t.id, t.spec.name, t.spec.grading_kind, t.status) for t in rows)

    def unscoped_search_count(self) -> int:
        return sum(
            1
            for a in self._t.audit
            if a.user_id == self._uid
            and a.event == "notes_searched"
            and a.meta.get("subject_id") is None
        )


class MemoryAuditRepo(_Repo):
    def record(self, event: AuditEvent, meta: Mapping[str, Any], now: datetime) -> None:
        self._t.audit.append(
            AuditRecord(user_id=self._uid, event=event, meta=_json_copy(meta), at=now)
        )


class MemoryUnitOfWork:
    def __init__(self, store: MemoryStore, user_id: UUID) -> None:
        self.user_id: UUID = user_id
        self.users: UserRepo = MemoryUserRepo(store, user_id)
        self.profiles: ProfileRepo = MemoryProfileRepo(store, user_id)
        self.track: TrackRepo = MemoryTrackRepo(store, user_id)
        self.plans: PlanRepo = MemoryPlanRepo(store, user_id)
        self.sessions: SessionRepo = MemorySessionRepo(store, user_id)
        self.glossary: GlossaryRepo = MemoryGlossaryRepo(store, user_id)
        self.reviews: ReviewRepo = MemoryReviewRepo(store, user_id)
        self.audit: AuditRepo = MemoryAuditRepo(store, user_id)
        self.subjects: SubjectRepo = MemorySubjectRepo(store, user_id)
        self.notes: NoteRepo = MemoryNoteRepo(store, user_id)
        self.templates: TemplateRepo = MemoryTemplateRepo(store, user_id)
        self.instances: InstanceRepo = MemoryInstanceRepo(store, user_id)
        self.practice: PracticeRepo = MemoryPracticeRepo(store, user_id)
        self.progress: ProgressRepo = MemoryProgressRepo(store, user_id)


def memory_uow(store: MemoryStore) -> UowFactory:
    """A unit-of-work factory over `store`: snapshot on enter, restore if the block raises."""

    @contextmanager
    def factory(user_id: UUID) -> Iterator[UnitOfWork]:
        snapshot = copy.deepcopy(store.tables)
        try:
            yield MemoryUnitOfWork(store, user_id)
        except BaseException:
            store.tables = snapshot
            raise

    return factory


class MemoryIdentity(IdentityResolver):
    """Find-or-create users by Google sub in a MemoryStore."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def resolve(
        self, google_sub: str, email: str | None, display_name: str | None, now: datetime
    ) -> ResolvedUser:
        for user in self._store.tables.users.values():
            if user.google_sub == google_sub:
                return ResolvedUser(id=user.id, created=False)
        user = UserRecord(
            id=uuid4(),
            google_sub=google_sub,
            email=email,
            display_name=display_name,
            created_at=now,
        )
        self._store.tables.users[user.id] = user
        return ResolvedUser(id=user.id, created=True)
