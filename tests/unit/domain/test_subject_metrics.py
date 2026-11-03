"""Subject metrics (subjects spec 16.3-16.7, 16.12; slice B plan rulings 64-68)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from tutor.domain.metrics import streak_days
from tutor.domain.subject_metrics import (
    ITEM_TIME_CAP_SECONDS,
    MAX_LISTED,
    RANK_MIN_ATTEMPTS,
    RECENT_DAYS,
    STALE_OPEN_SESSION,
    WEEKS_SHOWN,
    Accuracy,
    ActivityCounts,
    ActivityFacts,
    ConceptFact,
    SessionFact,
    ShowingFact,
    Streak,
    TemplateFact,
    TimeStats,
    WeekCounts,
    accuracy_of,
    activity_totals,
    concept_accuracy,
    duration_seconds,
    is_abandoned,
    longest_streak_days,
    percentile_nearest_rank,
    recent_from,
    round_half_up,
    subject_progress,
    subject_streak,
    summary_text,
    template_timing,
    time_stats,
    weekly_counts,
)
from tutor.domain.templates import GradingKind

pytestmark = pytest.mark.unit

MX = ZoneInfo("America/Mexico_City")
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)  # Tuesday 12:00 in Mexico City


def uid(n: int) -> UUID:
    return UUID(int=n)


def showing(
    score: int,
    *,
    concept: int | None = 1,
    template: int = 100,
    at: datetime = NOW - timedelta(hours=1),
    seconds: int = 30,
    kind: GradingKind = "exact_sequence",
    instance: int = 50,
) -> ShowingFact:
    correct = None if kind == "self_report" else score == 100
    return ShowingFact(
        instance_id=uid(instance),
        template_id=uid(template),
        concept_id=uid(concept) if concept is not None else None,
        grading_kind=kind,
        correct=correct,
        score=score,
        shown_at=at - timedelta(seconds=seconds),
        answered_at=at,
    )


def concept(n: int, name: str) -> ConceptFact:
    return ConceptFact(id=uid(n), name=name, name_norm=name.casefold())


# --- arithmetic ---------------------------------------------------------------------------------


def test_constants() -> None:
    assert (ITEM_TIME_CAP_SECONDS, RECENT_DAYS, WEEKS_SHOWN, RANK_MIN_ATTEMPTS, MAX_LISTED) == (
        600,
        14,
        8,
        3,
        20,
    )
    assert timedelta(hours=24) == STALE_OPEN_SESSION


def test_round_half_up_uses_integers() -> None:
    assert round_half_up(50 + 51, 2) == 51
    assert round_half_up(67 + 67 + 100, 3) == 78
    assert round_half_up(1, 3) == 0
    assert round_half_up(2, 4) == 1
    assert round_half_up(0, 1) == 0


def test_nearest_rank_percentiles() -> None:
    assert percentile_nearest_rank([7], 50) == 7
    assert percentile_nearest_rank([7], 90) == 7
    assert percentile_nearest_rank([3, 9], 50) == 3  # the lower middle value
    assert percentile_nearest_rank([3, 9], 90) == 9
    assert percentile_nearest_rank([1, 2, 3], 50) == 2
    assert percentile_nearest_rank([1, 2, 3, 4], 50) == 2
    assert percentile_nearest_rank([1, 2, 3, 4], 90) == 4
    ten = list(range(10, 110, 10))
    assert (percentile_nearest_rank(ten, 50), percentile_nearest_rank(ten, 90)) == (50, 90)
    with pytest.raises(ValueError):
        percentile_nearest_rank([], 50)


def test_time_stats_are_null_below_three_and_cap_at_600() -> None:
    assert time_stats([]) == TimeStats(0, 0, None, None)
    assert time_stats([5, 9]) == TimeStats(2, 0, None, None)
    assert time_stats([5, 9, 7]) == TimeStats(3, 0, 7, 9)
    assert time_stats([600, 601, 5, 9]) == TimeStats(3, 1, 9, 600)  # 600 kept, 601 counted
    assert time_stats([601, 700, 5, 9]) == TimeStats(2, 2, None, None)


def test_review_focus_6_durations_are_whole_server_seconds() -> None:
    shown = datetime(2026, 11, 3, 10, 0, 0, tzinfo=UTC)
    fact = ShowingFact(
        uid(1), uid(2), None, "exact_text", True, 100, shown, shown + timedelta(seconds=41.9)
    )
    assert duration_seconds(fact) == 41


def test_accuracy_counts_partial_credit_and_rounds_half_up() -> None:
    assert accuracy_of(()) == Accuracy(0, 0, 0, None, None)
    facts = (showing(67), showing(67), showing(100), showing(50, kind="exact_set"))
    result = accuracy_of(facts)
    assert result == Accuracy(attempts=4, correct=1, partial=3, mean_score=71, accuracy=0.25)
    # Self-reports are not graded showings.
    assert accuracy_of((showing(75, kind="self_report"),)) == Accuracy(0, 0, 0, None, None)
    assert accuracy_of((showing(0, kind="numeric"),)).partial == 0


# --- concepts -----------------------------------------------------------------------------------


def test_review_focus_7_weakest_first_with_partial_credit() -> None:
    concepts = (
        concept(1, "Escalas"),
        concept(2, "Acordes"),
        concept(3, "Ritmo"),
        concept(4, "Teoría"),
    )
    facts = (
        showing(67, concept=1),
        showing(67, concept=1),
        showing(100, concept=1),
        *(showing(100, concept=2) for _ in range(3)),
        showing(0, concept=3, kind="numeric"),
    )
    result = concept_accuracy(facts, concepts, recent_from=NOW - timedelta(days=13))
    ranked = [(c.name, c.concept_rank) for c in result.concepts]
    assert ranked == [
        ("Escalas", "ranked"),
        ("Acordes", "ranked"),
        ("Ritmo", "few_attempts"),
        ("Teoría", "not_practised"),
    ]
    escalas = result.concepts[0]
    assert (escalas.recent.mean_score, escalas.recent.partial) == (78, 2)
    assert escalas.all_time == escalas.recent
    assert result.concepts[3].all_time == Accuracy(0, 0, 0, None, None)
    assert result.concepts[3].timing == TimeStats(0, 0, None, None)


def test_ranked_ties_break_by_attempts_then_name_then_id() -> None:
    concepts = (concept(1, "b"), concept(2, "a"), concept(3, "c"), concept(4, "a"))
    facts = (
        *(showing(50, concept=1) for _ in range(4)),
        *(showing(50, concept=2) for _ in range(3)),
        *(showing(50, concept=3) for _ in range(3)),
        *(showing(50, concept=4) for _ in range(3)),
    )
    result = concept_accuracy(facts, concepts, recent_from=NOW - timedelta(days=13))
    assert [c.concept_id for c in result.concepts] == [uid(1), uid(2), uid(4), uid(3)]


def test_the_rank_score_is_recent_with_three_recent_attempts_else_all_time() -> None:
    old = NOW - timedelta(days=30)
    concepts = (concept(1, "Viejo"), concept(2, "Nuevo"))
    facts = (
        # Viejo: three old 100s and two recent 0s -> all-time mean 60 ranks it.
        *(showing(100, concept=1, at=old) for _ in range(3)),
        *(showing(0, concept=1, kind="numeric") for _ in range(2)),
        # Nuevo: three recent 70s rank it by the recent mean, ahead of its old zeros.
        *(showing(0, concept=2, at=old, kind="numeric") for _ in range(3)),
        *(showing(70, concept=2, kind="exact_set") for _ in range(3)),
    )
    result = concept_accuracy(facts, concepts, recent_from=NOW - timedelta(days=13))
    assert [c.name for c in result.concepts] == ["Viejo", "Nuevo"]
    viejo, nuevo = result.concepts
    assert (viejo.all_time.mean_score, viejo.recent.attempts) == (60, 2)
    assert (nuevo.recent.mean_score, nuevo.all_time.mean_score) == (70, 35)


def test_few_attempts_order_by_attempts_then_name_and_unpractised_by_name() -> None:
    concepts = (concept(1, "z"), concept(2, "m"), concept(3, "a"), concept(4, "y"), concept(5, "b"))
    twice = (showing(0, concept=2, kind="numeric"),) * 2
    facts = (showing(0, concept=1, kind="numeric"), *twice, showing(100, concept=3))
    result = concept_accuracy(facts, concepts, recent_from=NOW - timedelta(days=13))
    assert [c.name for c in result.concepts] == ["m", "a", "z", "b", "y"]


def test_unassigned_showings_are_reported_together() -> None:
    facts = (
        showing(100, concept=None),
        showing(0, concept=None, kind="numeric", seconds=700),
        showing(100, concept=9),  # a concept that is not the subject's
    )
    result = concept_accuracy(facts, (concept(1, "Escalas"),), recent_from=NOW - timedelta(1))
    assert result.unassigned.all_time.attempts == 3
    assert result.unassigned.timing == TimeStats(2, 1, None, None)
    assert result.concepts[0].concept_rank == "not_practised"


def test_the_recent_window_starts_at_local_midnight_across_a_dst_change() -> None:
    # New York leaves summer time on 2026-11-01; the window of 2026-11-10 starts on 10-28,
    # still at UTC-4: local midnight is 04:00 UTC, not 05:00.
    today = date(2026, 11, 10)
    start = recent_from(today, NY)
    assert start == datetime(2026, 10, 28, 4, 0, tzinfo=UTC)
    inside = showing(100, at=start)
    outside = showing(100, at=start - timedelta(seconds=1))
    result = concept_accuracy((inside, outside), (concept(1, "Escalas"),), recent_from=start)
    assert (result.concepts[0].recent.attempts, result.concepts[0].all_time.attempts) == (1, 2)


# --- time per item ------------------------------------------------------------------------------


def test_self_reports_are_never_timed() -> None:
    facts = (showing(75, kind="self_report", seconds=40),) * 3
    result = concept_accuracy(facts, (concept(1, "Escalas"),), recent_from=NOW)
    assert result.concepts[0].timing == TimeStats(0, 0, None, None)
    assert (
        template_timing(facts, (TemplateFact(uid(100), "Práctica", "self_report", "active"),)) == ()
    )


def test_template_timing_includes_retired_templates_by_timed_then_name() -> None:
    templates = (
        TemplateFact(uid(100), "Bravo", "exact_text", "retired"),
        TemplateFact(uid(101), "Alfa", "numeric", "active"),
        TemplateFact(uid(102), "Charlie", "numeric", "active"),
        TemplateFact(uid(103), "Sin uso", "numeric", "active"),
    )
    facts = (
        *(showing(100, template=100, seconds=s) for s in (10, 20, 30)),
        *(showing(100, template=101, seconds=s) for s in (5, 6, 601)),
        *(showing(100, template=102, seconds=s) for s in (1, 2)),
    )
    result = template_timing(facts, templates)
    assert [(t.name, t.template_status, t.timing) for t in result] == [
        ("Bravo", "retired", TimeStats(3, 0, 20, 30)),
        ("Alfa", "active", TimeStats(2, 1, None, None)),
        ("Charlie", "active", TimeStats(2, 0, None, None)),
    ]


# --- streak -------------------------------------------------------------------------------------


def test_review_focus_8_streak_across_midnight_in_the_learners_zone() -> None:
    # 23:50 and 00:10 in Mexico City (UTC-6) are both on 2026-11-03 in UTC.
    late = datetime(2026, 11, 3, 5, 50, tzinfo=UTC)
    early = datetime(2026, 11, 3, 6, 10, tzinfo=UTC)
    now = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
    assert streak_days([late, early], now, MX) == 2
    assert subject_streak([late, early], now, MX) == Streak(2, 2, date(2026, 11, 3))
    assert streak_days([late, early], now, ZoneInfo("UTC")) == 1


def test_longest_streak_survives_a_later_gap() -> None:
    def day(d: int) -> datetime:
        return datetime(2026, 10, d, 18, 0, tzinfo=UTC)

    moments = [day(1), day(2), day(3), day(4), day(10), day(11)]
    assert longest_streak_days(moments, MX) == 4
    assert longest_streak_days([day(5)], MX) == 1
    assert longest_streak_days([day(5), day(5)], MX) == 1
    assert longest_streak_days([], MX) == 0
    now = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    assert subject_streak(moments, now, MX) == Streak(0, 4, date(2026, 10, 11))
    assert subject_streak([], now, MX) == Streak(0, 0, None)


# --- behaviour counts ---------------------------------------------------------------------------


def session(started: datetime, status: str = "closed", last: datetime | None = None) -> SessionFact:
    return SessionFact(started_at=started, status=status, last_activity_at=last or started)  # type: ignore[arg-type]


def test_practice_abandoned_for_incomplete_and_stale_open_sessions() -> None:
    started = NOW - timedelta(days=2)
    assert is_abandoned(session(started, "incomplete"), NOW)
    assert not is_abandoned(session(started, "closed"), NOW)
    edge = NOW - STALE_OPEN_SESSION
    assert not is_abandoned(session(started, "open", edge), NOW)
    assert not is_abandoned(session(started, "open", edge + timedelta(seconds=1)), NOW)
    assert is_abandoned(session(started, "open", edge - timedelta(seconds=1)), NOW)


def test_monday_weeks_in_the_learners_zone_with_empty_weeks() -> None:
    today = date(2026, 11, 3)  # a Tuesday; its week starts on Monday 11-02
    monday_local = datetime(2026, 11, 2, 6, 30, tzinfo=UTC)  # 00:30 Monday in Mexico City
    sunday_local = datetime(2026, 11, 2, 5, 30, tzinfo=UTC)  # 23:30 Sunday in Mexico City
    long_ago = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    activity = ActivityFacts(
        notes=(monday_local, sunday_local, long_ago),
        templates=(sunday_local,),
        instances=(monday_local, monday_local),
        searches=(monday_local,),
    )
    sessions = (
        session(monday_local, "closed"),
        session(sunday_local, "incomplete"),
        session(monday_local, "open", NOW - timedelta(hours=1)),
    )
    weeks = weekly_counts(activity, sessions, today=today, tz=MX, now=NOW)
    assert len(weeks) == WEEKS_SHOWN
    assert [w.week_start for w in weeks] == [
        date(2026, 11, 2) - timedelta(weeks=k) for k in range(7, -1, -1)
    ]
    assert weeks[-1] == WeekCounts(
        week_start=date(2026, 11, 2),
        notes_ingested=1,
        templates_created=0,
        instances_created=2,
        searches=1,
        practice_started=2,
        practice_completed=1,
        practice_abandoned=0,
    )
    assert weeks[-2] == WeekCounts(date(2026, 10, 26), 1, 1, 0, 0, 1, 0, 1)
    assert all(w == WeekCounts(w.week_start, 0, 0, 0, 0, 0, 0, 0) for w in weeks[:-2])
    assert activity_totals(activity, sessions, now=NOW) == ActivityCounts(3, 1, 2, 1, 3, 1, 1)


# --- the whole read -----------------------------------------------------------------------------


def test_subject_progress_puts_it_together() -> None:
    concepts = (concept(1, "Escalas"), concept(2, "Acordes"))
    templates = (TemplateFact(uid(100), "Orden", "exact_sequence", "active"),)
    facts = (
        showing(67, seconds=41),
        showing(67, seconds=20),
        showing(100, seconds=700),
        showing(75, kind="self_report", concept=None),
    )
    days = (NOW - timedelta(days=1), NOW - timedelta(hours=1))
    activity = ActivityFacts(notes=(NOW,), templates=(NOW,), instances=(NOW,), searches=())
    progress = subject_progress(
        facts, days, (session(NOW),), activity, concepts, templates, 2, now=NOW, tz=MX
    )
    assert progress.as_of == date(2026, 11, 3)
    assert progress.streak == Streak(2, 2, date(2026, 11, 3))
    assert progress.overall.all_time == Accuracy(3, 1, 2, 78, 1 / 3)
    assert progress.overall.recent == progress.overall.all_time
    assert progress.window_days == 14
    assert [c.name for c in progress.concepts] == ["Escalas", "Acordes"]
    assert progress.concepts_total == 2
    assert progress.unassigned.all_time.attempts == 0
    assert [t.timing for t in progress.templates] == [TimeStats(2, 1, None, None)]
    assert progress.time_cap_seconds == 600
    assert len(progress.weeks) == 8 and progress.weeks[-1].practice_started == 1
    assert progress.totals == ActivityCounts(1, 1, 1, 0, 1, 1, 0)
    assert progress.unscoped_searches == 2
    assert progress.summary_text == (
        "Racha: 2 días (máxima: 2). Últimos 14 días: 3 respuestas, 78 de 100 en promedio. "
        "1 ejercicio de más de 10 minutos no cuenta en los tiempos."
    )


def test_summary_text_wording() -> None:
    empty = Accuracy(0, 0, 0, None, None)
    assert summary_text(Streak(1, 9, None), empty, 0) == (
        "Racha: 1 día (máxima: 9). Últimos 14 días: sin respuestas."
    )
    one = Accuracy(1, 1, 0, 100, 1.0)
    assert summary_text(Streak(4, 9, None), one, 2) == (
        "Racha: 4 días (máxima: 9). Últimos 14 días: 1 respuesta, 100 de 100 en promedio. "
        "2 ejercicios de más de 10 minutos no cuentan en los tiempos."
    )
