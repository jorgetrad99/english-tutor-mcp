"""Plan arithmetic (subjects spec 6.1; slice B plan ruling 28, Review Focus 1)."""

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.domain.practice_plan import (
    PlanInputs,
    PlanStatus,
    plan_inputs_changed,
    plan_status,
    total_sessions,
)

pytestmark = pytest.mark.unit

START = date(2026, 11, 2)


def inputs(spw: int = 3, minutes: int = 30, target: date | None = None) -> PlanInputs:
    return PlanInputs(minutes_per_session=minutes, sessions_per_week=spw, target_date=target)


def test_total_sessions_is_sessions_per_week_times_started_weeks() -> None:
    assert total_sessions(inputs(target=START + timedelta(days=28)), START) == 12
    assert total_sessions(inputs(target=START + timedelta(days=29)), START) == 15
    assert total_sessions(inputs(target=START + timedelta(days=1)), START) == 3
    assert total_sessions(inputs(target=START), START) == 0
    assert total_sessions(inputs(target=START - timedelta(days=3)), START) == 0
    assert total_sessions(inputs(), START) is None


def test_review_focus_1_missed_sessions_and_projected_end() -> None:
    today = START + timedelta(days=10)
    target = START + timedelta(days=28)
    status = plan_status(
        inputs(target=target), version=1, start_date=START, completed=2, today=today
    )
    assert status == PlanStatus(
        version=1,
        start_date=START,
        sessions_per_week=3,
        minutes_per_session=30,
        target_date=target,
        total_sessions=12,
        completed=2,
        expected=3,
        missed=1,
        projected_end=target + timedelta(days=7),
        delay_days=7,
        adherence=2 / 3,
    )
    on_track = plan_status(
        inputs(target=target), version=1, start_date=START, completed=3, today=today
    )
    assert (on_track.missed, on_track.projected_end, on_track.delay_days) == (0, target, 0)


def test_review_focus_1_open_ended_plan_has_no_projection() -> None:
    status = plan_status(
        inputs(), version=2, start_date=START, completed=2, today=START + timedelta(days=10)
    )
    assert (status.total_sessions, status.projected_end, status.delay_days) == (None, None, None)
    assert (status.expected, status.missed) == (3, 1)


def test_before_the_first_week_ends_nothing_is_expected() -> None:
    status = plan_status(
        inputs(target=START + timedelta(days=28)),
        version=1,
        start_date=START,
        completed=0,
        today=START + timedelta(days=6),
    )
    assert (status.expected, status.missed, status.adherence) == (0, 0, None)
    assert status.projected_end == START + timedelta(days=28)


def test_ahead_of_plan_is_never_negative_delay() -> None:
    target = START + timedelta(days=28)
    status = plan_status(
        inputs(target=target), version=1, start_date=START, completed=9, today=START + timedelta(7)
    )
    assert status.missed == 0
    assert status.projected_end == START + timedelta(days=7 + 7)
    assert status.delay_days == 0
    assert status.adherence == pytest.approx(3.0)


def test_a_finished_plan_projects_the_current_week() -> None:
    target = START + timedelta(days=14)
    status = plan_status(
        inputs(target=target), version=1, start_date=START, completed=6, today=START + timedelta(20)
    )
    assert status.projected_end == START + timedelta(days=14)
    assert status.delay_days == 0


def test_today_before_the_start_counts_as_day_zero() -> None:
    status = plan_status(
        inputs(), version=1, start_date=START, completed=0, today=START - timedelta(1)
    )
    assert (status.expected, status.missed) == (0, 0)


@given(
    spw=st.integers(1, 7),
    days=st.integers(0, 400),
    completed=st.integers(0, 200),
    target_days=st.one_of(st.none(), st.integers(0, 400)),
)
def test_status_invariants(spw: int, days: int, completed: int, target_days: int | None) -> None:
    target = START + timedelta(days=target_days) if target_days is not None else None
    status = plan_status(
        inputs(spw=spw, target=target),
        version=1,
        start_date=START,
        completed=completed,
        today=START + timedelta(days=days),
    )
    assert status.expected == spw * (days // 7)
    assert status.missed == max(0, status.expected - completed)
    if target is None:
        assert status.projected_end is None
    else:
        assert status.projected_end is not None and status.delay_days is not None
        assert status.delay_days >= 0
        assert (status.projected_end - START).days % 7 == 0


def test_plan_inputs_changed() -> None:
    base = inputs(target=START)
    assert not plan_inputs_changed(base, inputs(target=START))
    assert plan_inputs_changed(base, inputs(spw=4, target=START))
    assert plan_inputs_changed(base, inputs(minutes=45, target=START))
    assert plan_inputs_changed(base, inputs(target=None))
