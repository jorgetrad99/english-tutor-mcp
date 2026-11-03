"""Subject metrics: accuracy per concept, time per item, the subject streak and behaviour counts
(subjects spec 16.3-16.7; slice B plan rulings 64-68).

Pure functions over facts the `ProgressRepo` reads; no learner text except concept and
template names, which are carried as data. Integer arithmetic for every rounding; the only
floats are the `accuracy` fractions.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from tutor.domain.dashboard.home import week_start
from tutor.domain.metrics import streak_days
from tutor.domain.templates import GradingKind, TemplateStatus

if TYPE_CHECKING:
    from tutor.domain.practice import PracticeStatus

ITEM_TIME_CAP_SECONDS = 600
RECENT_DAYS = 14
WEEKS_SHOWN = 8
RANK_MIN_ATTEMPTS = 3
STALE_OPEN_SESSION = timedelta(hours=24)
MAX_LISTED = 20  # the MCP output's cap on concepts and templates (ruling 71)
MEDIAN = 50
P90 = 90

ConceptRank = Literal["ranked", "few_attempts", "not_practised"]
CONCEPT_RANKS: tuple[ConceptRank, ...] = ("ranked", "few_attempts", "not_practised")


# --- facts --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShowingFact:
    """An answered showing of the subject; `correct` None for a self-report."""

    instance_id: UUID
    template_id: UUID
    concept_id: UUID | None
    grading_kind: GradingKind
    correct: bool | None
    score: int
    shown_at: datetime
    answered_at: datetime


@dataclass(frozen=True, slots=True)
class SessionFact:
    started_at: datetime
    status: PracticeStatus
    last_activity_at: datetime  # started_at or the latest shown_at / answered_at of its items


@dataclass(frozen=True, slots=True)
class ConceptFact:
    id: UUID
    name: str
    name_norm: str


@dataclass(frozen=True, slots=True)
class TemplateFact:
    id: UUID
    name: str
    grading_kind: GradingKind
    status: TemplateStatus


@dataclass(frozen=True, slots=True)
class ActivityFacts:
    """`created_at` of the subject's notes, templates and instances, and the moments of its
    `notes_searched` events."""

    notes: tuple[datetime, ...]
    templates: tuple[datetime, ...]
    instances: tuple[datetime, ...]
    searches: tuple[datetime, ...]


# --- arithmetic ---------------------------------------------------------------------------------


def round_half_up(total: int, n: int) -> int:
    """`total / n` rounded half up, in integers: `(2 * total + n) // (2 * n)`."""
    return (2 * total + n) // (2 * n)


def percentile_nearest_rank(sorted_values: Sequence[int], p: int) -> int:
    """The value at 1-based rank `ceil(p * n / 100)` of ascending values (the median of an
    even count is the lower middle value). ValueError on no values."""
    if not sorted_values:
        raise ValueError("no values")
    rank = -(-p * len(sorted_values) // 100)
    return sorted_values[max(rank, 1) - 1]


@dataclass(frozen=True, slots=True)
class TimeStats:
    timed: int
    over_cap: int
    median_seconds: int | None
    p90_seconds: int | None


def time_stats(durations: Iterable[int], cap: int = ITEM_TIME_CAP_SECONDS) -> TimeStats:
    """Durations above `cap` are counted, not timed; percentiles need 3 timed showings."""
    values = list(durations)
    timed = sorted(d for d in values if d <= cap)
    over = len(values) - len(timed)
    if len(timed) < RANK_MIN_ATTEMPTS:
        return TimeStats(len(timed), over, None, None)
    return TimeStats(
        len(timed),
        over,
        percentile_nearest_rank(timed, MEDIAN),
        percentile_nearest_rank(timed, P90),
    )


def duration_seconds(fact: ShowingFact) -> int:
    """`answered_at - shown_at` in whole seconds, truncated (ruling 64)."""
    return (fact.answered_at - fact.shown_at) // timedelta(seconds=1)


def _graded(showings: Iterable[ShowingFact]) -> list[ShowingFact]:
    return [s for s in showings if s.correct is not None]


def _timing(showings: Iterable[ShowingFact]) -> TimeStats:
    return time_stats(duration_seconds(s) for s in _graded(showings))


@dataclass(frozen=True, slots=True)
class Accuracy:
    attempts: int
    correct: int
    partial: int
    mean_score: int | None
    accuracy: float | None


def accuracy_of(showings: Iterable[ShowingFact]) -> Accuracy:
    """Spec 16.3 over the graded showings (self-reports are not graded)."""
    graded = _graded(showings)
    if not graded:
        return Accuracy(0, 0, 0, None, None)
    n = len(graded)
    correct = sum(1 for s in graded if s.correct)
    return Accuracy(
        attempts=n,
        correct=correct,
        partial=sum(1 for s in graded if 0 < s.score < 100),
        mean_score=round_half_up(sum(s.score for s in graded), n),
        accuracy=correct / n,
    )


def recent_from(today: date, tz: ZoneInfo) -> datetime:
    """Local midnight of `today - 13` days, as an aware instant (DST-aware)."""
    return datetime.combine(today - timedelta(days=RECENT_DAYS - 1), time.min, tzinfo=tz)


# --- concepts and templates ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConceptProgress:
    concept_id: UUID
    name: str
    name_norm: str
    concept_rank: ConceptRank
    all_time: Accuracy
    recent: Accuracy
    timing: TimeStats


@dataclass(frozen=True, slots=True)
class UnassignedProgress:
    all_time: Accuracy
    recent: Accuracy
    timing: TimeStats


@dataclass(frozen=True, slots=True)
class ConceptAccuracy:
    concepts: tuple[ConceptProgress, ...]  # every concept, weakest first
    unassigned: UnassignedProgress


def _rank(all_time: Accuracy, recent: Accuracy) -> tuple[ConceptRank, int]:
    if recent.attempts >= RANK_MIN_ATTEMPTS and recent.mean_score is not None:
        return "ranked", recent.mean_score
    if all_time.attempts >= RANK_MIN_ATTEMPTS and all_time.mean_score is not None:
        return "ranked", all_time.mean_score
    return ("few_attempts" if all_time.attempts else "not_practised"), 0


def concept_accuracy(
    showings: Sequence[ShowingFact], concepts: Sequence[ConceptFact], *, recent_from: datetime
) -> ConceptAccuracy:
    """Every concept of the subject, weakest first (spec 16.3): ranked concepts by rank score,
    then all-time attempts descending, name_norm and id; then concepts with 1-2 attempts by
    attempts descending and name_norm; then concepts never practised by name_norm. Showings
    of an instance without one of these concepts are `unassigned`."""
    known = {c.id for c in concepts}
    by_concept: dict[UUID | None, list[ShowingFact]] = {}
    for s in showings:
        key = s.concept_id if s.concept_id in known else None
        by_concept.setdefault(key, []).append(s)

    def recent(facts: Sequence[ShowingFact]) -> list[ShowingFact]:
        return [s for s in facts if s.answered_at >= recent_from]

    ranked: list[tuple[tuple[int, int, str, UUID], ConceptProgress]] = []
    few: list[tuple[tuple[int, str, UUID], ConceptProgress]] = []
    unpractised: list[tuple[tuple[str, UUID], ConceptProgress]] = []
    for c in concepts:
        facts = by_concept.get(c.id, [])
        all_time, last = accuracy_of(facts), accuracy_of(recent(facts))
        rank, score = _rank(all_time, last)
        item = ConceptProgress(c.id, c.name, c.name_norm, rank, all_time, last, _timing(facts))
        if rank == "ranked":
            ranked.append(((score, -all_time.attempts, c.name_norm, c.id), item))
        elif rank == "few_attempts":
            few.append(((-all_time.attempts, c.name_norm, c.id), item))
        else:
            unpractised.append(((c.name_norm, c.id), item))
    loose = by_concept.get(None, [])
    ordered = (
        [p for _, p in sorted(ranked, key=lambda e: e[0])]
        + [p for _, p in sorted(few, key=lambda e: e[0])]
        + [p for _, p in sorted(unpractised, key=lambda e: e[0])]
    )
    return ConceptAccuracy(
        concepts=tuple(ordered),
        unassigned=UnassignedProgress(
            all_time=accuracy_of(loose), recent=accuracy_of(recent(loose)), timing=_timing(loose)
        ),
    )


@dataclass(frozen=True, slots=True)
class TemplateTiming:
    template_id: UUID
    name: str
    grading_kind: GradingKind
    template_status: TemplateStatus
    timing: TimeStats


def template_timing(
    showings: Sequence[ShowingFact], templates: Sequence[TemplateFact]
) -> tuple[TemplateTiming, ...]:
    """Time per item for every template with graded showings, retired ones included, by
    `timed` descending, then name, then id (spec 16.4)."""
    by_template: dict[UUID, list[ShowingFact]] = {}
    for s in _graded(showings):
        by_template.setdefault(s.template_id, []).append(s)
    rows = [
        TemplateTiming(t.id, t.name, t.grading_kind, t.status, _timing(by_template[t.id]))
        for t in templates
        if t.id in by_template
    ]
    return tuple(sorted(rows, key=lambda r: (-r.timing.timed, r.name, r.template_id)))


# --- streak -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Streak:
    current_days: int
    longest_days: int
    last_practice_day: date | None


def _local_days(moments: Iterable[datetime], tz: ZoneInfo) -> list[date]:
    return sorted({m.astimezone(tz).date() for m in moments})


def longest_streak_days(moments: Iterable[datetime], tz: ZoneInfo) -> int:
    """The longest run of consecutive local days with a moment, ever."""
    longest = run = 0
    previous: date | None = None
    for day in _local_days(moments, tz):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    return longest


def subject_streak(practice_days: Sequence[datetime], now: datetime, tz: ZoneInfo) -> Streak:
    """The current streak reuses the English `streak_days` (spec 16.5); the longest run and
    the last local day are new."""
    days = _local_days(practice_days, tz)
    return Streak(
        current_days=streak_days(practice_days, now, tz),
        longest_days=longest_streak_days(practice_days, tz),
        last_practice_day=days[-1] if days else None,
    )


# --- behaviour counts ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActivityCounts:
    notes_ingested: int
    templates_created: int
    instances_created: int
    searches: int
    practice_started: int
    practice_completed: int
    practice_abandoned: int


@dataclass(frozen=True, slots=True)
class WeekCounts:
    week_start: date
    notes_ingested: int
    templates_created: int
    instances_created: int
    searches: int
    practice_started: int
    practice_completed: int
    practice_abandoned: int


def is_abandoned(session: SessionFact, now: datetime) -> bool:
    """`incomplete`, or `open` with no activity for more than 24 hours (computed, never
    written; spec 16.6)."""
    if session.status == "incomplete":
        return True
    return session.status == "open" and now - session.last_activity_at > STALE_OPEN_SESSION


def _counts(
    activity: ActivityFacts, sessions: Sequence[SessionFact], now: datetime
) -> ActivityCounts:
    return ActivityCounts(
        notes_ingested=len(activity.notes),
        templates_created=len(activity.templates),
        instances_created=len(activity.instances),
        searches=len(activity.searches),
        practice_started=len(sessions),
        practice_completed=sum(1 for s in sessions if s.status == "closed"),
        practice_abandoned=sum(1 for s in sessions if is_abandoned(s, now)),
    )


def activity_totals(
    activity: ActivityFacts, sessions: Sequence[SessionFact], *, now: datetime
) -> ActivityCounts:
    return _counts(activity, sessions, now)


def weekly_counts(
    activity: ActivityFacts,
    sessions: Sequence[SessionFact],
    *,
    today: date,
    tz: ZoneInfo,
    now: datetime,
) -> tuple[WeekCounts, ...]:
    """The last 8 Monday weeks in the learner's zone, oldest first, the current one included;
    a session counts in the week it started."""
    current = week_start(today)
    starts = [current - timedelta(weeks=k) for k in range(WEEKS_SHOWN - 1, -1, -1)]

    def week(moment: datetime) -> date:
        return week_start(moment.astimezone(tz).date())

    def within(moments: Iterable[datetime], start: date) -> tuple[datetime, ...]:
        return tuple(m for m in moments if week(m) == start)

    result: list[WeekCounts] = []
    for start in starts:
        counts = _counts(
            ActivityFacts(
                notes=within(activity.notes, start),
                templates=within(activity.templates, start),
                instances=within(activity.instances, start),
                searches=within(activity.searches, start),
            ),
            [s for s in sessions if week(s.started_at) == start],
            now,
        )
        result.append(
            WeekCounts(
                start,
                counts.notes_ingested,
                counts.templates_created,
                counts.instances_created,
                counts.searches,
                counts.practice_started,
                counts.practice_completed,
                counts.practice_abandoned,
            )
        )
    return tuple(result)


# --- the whole read -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Overall:
    all_time: Accuracy
    recent: Accuracy


@dataclass(frozen=True, slots=True)
class SubjectProgress:
    """Every concept and template in order (the website shows them all; the MCP output keeps
    the first MAX_LISTED of each)."""

    as_of: date
    streak: Streak
    overall: Overall
    window_days: int
    concepts: tuple[ConceptProgress, ...]
    concepts_total: int
    unassigned: UnassignedProgress
    templates: tuple[TemplateTiming, ...]
    time_cap_seconds: int
    weeks: tuple[WeekCounts, ...]
    totals: ActivityCounts
    unscoped_searches: int
    summary_text: str


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summary_text(streak: Streak, recent: Accuracy, over_cap: int) -> str:
    """Spanish (the v0 interface language), from numbers only: never a concept or template
    name (spec 16.8)."""
    parts = [
        f"Racha: {_plural(streak.current_days, 'día', 'días')} (máxima: {streak.longest_days})."
    ]
    if recent.attempts and recent.mean_score is not None:
        parts.append(
            f"Últimos {RECENT_DAYS} días: {_plural(recent.attempts, 'respuesta', 'respuestas')}, "
            f"{recent.mean_score} de 100 en promedio."
        )
    else:
        parts.append(f"Últimos {RECENT_DAYS} días: sin respuestas.")
    if over_cap:
        verb = "cuenta" if over_cap == 1 else "cuentan"
        parts.append(
            f"{_plural(over_cap, 'ejercicio', 'ejercicios')} de más de "
            f"{ITEM_TIME_CAP_SECONDS // 60} minutos no {verb} en los tiempos."
        )
    return " ".join(parts)


def subject_progress(
    showings: Sequence[ShowingFact],
    practice_days: Sequence[datetime],
    sessions: Sequence[SessionFact],
    activity: ActivityFacts,
    concepts: Sequence[ConceptFact],
    templates: Sequence[TemplateFact],
    unscoped_searches: int,
    *,
    now: datetime,
    tz: ZoneInfo,
) -> SubjectProgress:
    today = now.astimezone(tz).date()
    start = recent_from(today, tz)
    breakdown = concept_accuracy(showings, concepts, recent_from=start)
    streak = subject_streak(practice_days, now, tz)
    recent = accuracy_of(s for s in showings if s.answered_at >= start)
    return SubjectProgress(
        as_of=today,
        streak=streak,
        overall=Overall(all_time=accuracy_of(showings), recent=recent),
        window_days=RECENT_DAYS,
        concepts=breakdown.concepts,
        concepts_total=len(breakdown.concepts),
        unassigned=breakdown.unassigned,
        templates=template_timing(showings, templates),
        time_cap_seconds=ITEM_TIME_CAP_SECONDS,
        weeks=weekly_counts(activity, sessions, today=today, tz=tz, now=now),
        totals=activity_totals(activity, sessions, now=now),
        unscoped_searches=unscoped_searches,
        summary_text=summary_text(streak, recent, _timing(showings).over_cap),
    )
