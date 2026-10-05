"""In-memory repositories and unit of work for unit tests and local work.

The store enforces what Postgres enforces: one open session per user, unique (user, text_norm),
one review log per (session, item), JSON-only JSON columns and per-user isolation. A unit of work
deep-copies the tables on enter and restores the copy if the block raises.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

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
    SetStatus,
)
from tutor.domain.lesson import BriefVariant, DueCandidate, RecentResult
from tutor.domain.levels import CefrLevel
from tutor.domain.metrics import SessionMetrics
from tutor.domain.plan_lite import PlannedItem, Variant
from tutor.domain.profile import DEFAULT_TIMEZONE, Domain, Profile
from tutor.domain.track import TrackItem
from tutor.domain.validation import Category, Evidence, SessionOutcome, ValidError
from tutor.services.ports import (
    ActivePlan,
    AuditEvent,
    AuditRepo,
    ClientName,
    GlossaryRepo,
    GlossaryRowData,
    IdentityResolver,
    Mode,
    OpenSessionExists,
    PlanItemRow,
    PlanItemStatus,
    PlanRepo,
    ProfileRepo,
    ResolvedUser,
    ReviewLogRow,
    ReviewRepo,
    SessionRepo,
    SessionRow,
    SessionStatus,
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
