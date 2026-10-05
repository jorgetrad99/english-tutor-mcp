from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from tutor.domain.dashboard.home import (
    build_week_trail,
    local_today,
    should_celebrate,
    week_start,
)
from tutor.domain.dashboard.types import DayState, PlannedDay, SessionMark, SessionStatus

pytestmark = pytest.mark.unit

MON = date(2027, 1, 11)


def test_local_today_uses_learner_timezone_not_utc() -> None:
    late_evening_cdmx = datetime(2027, 1, 12, 3, 0, tzinfo=UTC)  # 21:00 on Jan 11 in CDMX
    assert local_today(late_evening_cdmx, "America/Mexico_City") == date(2027, 1, 11)
    assert local_today(late_evening_cdmx, "UTC") == date(2027, 1, 12)


def test_local_today_falls_back_to_utc_for_unknown_zone() -> None:
    assert local_today(datetime(2027, 1, 12, 3, tzinfo=UTC), "Mars/Base") == date(2027, 1, 12)


def test_week_start_is_monday() -> None:
    assert week_start(date(2027, 1, 17)) == MON  # Sunday
    assert week_start(MON) == MON


def test_trail_states_across_the_week() -> None:
    planned = [
        PlannedDay(date(2027, 1, 11), "Standup update"),
        PlannedDay(date(2027, 1, 12), "Code review"),
        PlannedDay(date(2027, 1, 13), "Demo"),
        PlannedDay(date(2027, 1, 15), "Interview"),
    ]
    marks = [
        SessionMark(date(2027, 1, 11), True, SessionStatus.CLOSED),
        SessionMark(date(2027, 1, 12), True, SessionStatus.INCOMPLETE),
        SessionMark(date(2027, 1, 12), False, SessionStatus.CLOSED),
        SessionMark(date(2027, 1, 12), False, SessionStatus.CLOSED),
    ]
    trail = build_week_trail(MON, planned, marks, today=date(2027, 1, 13))
    assert [n.state for n in trail] == [
        DayState.DONE,
        DayState.MISSED,  # an incomplete session does not count
        DayState.TODAY,
        DayState.REST,
        DayState.UPCOMING,
        DayState.REST,
        DayState.REST,
    ]
    assert trail[1].extra_sessions == 2
    assert trail[2].is_today and not trail[3].is_today
    assert trail[0].title == "Standup update" and trail[3].title is None


def test_off_plan_closed_session_on_rest_day_is_extra_not_done() -> None:
    marks = [SessionMark(date(2027, 1, 16), False, SessionStatus.CLOSED)]
    trail = build_week_trail(MON, [], marks, today=date(2027, 1, 17))
    assert trail[5].state is DayState.REST and trail[5].extra_sessions == 1
    assert trail[6].is_today and trail[6].state is DayState.REST


def test_planned_today_with_closed_session_is_done() -> None:
    planned = [PlannedDay(MON, "Standup")]
    marks = [SessionMark(MON, True, SessionStatus.CLOSED)]
    node = build_week_trail(MON, planned, marks, today=MON)[0]
    assert node.state is DayState.DONE and node.is_today


def test_should_celebrate_only_new_closed_sessions() -> None:
    a, b = uuid4(), uuid4()
    assert should_celebrate(a, None)
    assert should_celebrate(b, a)
    assert not should_celebrate(a, a)
    assert not should_celebrate(None, a)
