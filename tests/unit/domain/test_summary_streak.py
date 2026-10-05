from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.domain.metrics import SessionMetrics, streak_days, summary_text

pytestmark = pytest.mark.unit

MX = ZoneInfo("America/Mexico_City")
NY = ZoneInfo("America/New_York")


def m(
    *,
    words: int = 412,
    duration: float = 15.0,
    wpm: float = 27.5,
    errors: int = 3,
    uptake: int = 1,
    used: int = 2,
    offered: int = 5,
) -> SessionMetrics:
    return SessionMetrics(
        user_words=words,
        assistant_words_estimate=None,
        user_ratio=None,
        turns=10,
        words_per_turn=41.2,
        duration_min=duration,
        user_words_per_min=wpm,
        errors_total=errors,
        errors_rejected=0,
        errors_by_category={},
        errors_per_100w=0.7,
        recurring_errors=0,
        uptake_count=uptake,
        chunks_offered=offered,
        chunks_used=used,
        chunks_rejected=0,
        activation_rate=0.4,
    )


# --- summary_text ----------------------------------------------------------------


def test_summary_text_four_template_lines() -> None:
    assert summary_text(m(), 4) == (
        "You spoke 412 words in 15 minutes (27.5 per minute).\n"
        "You fixed 1 of your 3 mistakes during the session.\n"
        "You used 2 of today's 5 phrases.\n"
        "Streak: 4 days."
    )


def test_summary_text_without_mistakes_and_with_decimals() -> None:
    text = summary_text(m(words=95, duration=12.3, wpm=7.7, errors=0, uptake=0, used=0), 0)
    assert text.splitlines() == [
        "You spoke 95 words in 12.3 minutes (7.7 per minute).",
        "No mistakes were recorded.",
        "You used 0 of today's 5 phrases.",
        "Streak: 0 days.",
    ]


def test_summary_text_singular_forms() -> None:
    text = summary_text(m(words=1, duration=1.0, wpm=1.0, errors=1, uptake=1, used=1), 1)
    assert text.splitlines() == [
        "You spoke 1 word in 1 minute (1 per minute).",
        "You fixed 1 of your 1 mistake during the session.",
        "You used 1 of today's 5 phrases.",
        "Streak: 1 day.",
    ]


def test_summary_text_has_no_learner_text() -> None:
    lines = summary_text(m(), 2).splitlines()
    assert len(lines) == 4
    assert all(line.isascii() for line in lines)


# --- streak_days -----------------------------------------------------------------


def at(tz: ZoneInfo, y: int, mo: int, d: int, h: int, mi: int = 0, fold: int = 0) -> datetime:
    """A local wall-clock time converted to UTC, as the database returns it."""
    return datetime(y, mo, d, h, mi, tzinfo=tz, fold=fold).astimezone(UTC)


def test_no_sessions_is_zero() -> None:
    assert streak_days([], at(MX, 2026, 10, 14, 9), MX) == 0


def test_streak_counts_consecutive_local_days_ending_today() -> None:
    ended = [at(MX, 2026, 10, d, 19) for d in (10, 12, 13, 14)] + [at(MX, 2026, 10, 14, 8)]
    assert streak_days(ended, at(MX, 2026, 10, 14, 21), MX) == 3


def test_streak_may_end_yesterday_but_not_earlier() -> None:
    ended = [at(MX, 2026, 10, d, 19) for d in (12, 13)]
    assert streak_days(ended, at(MX, 2026, 10, 14, 9), MX) == 2
    assert streak_days(ended, at(MX, 2026, 10, 15, 9), MX) == 0


def test_streak_across_local_midnight_mexico_city() -> None:
    # Review Focus 3: 23:59 and 00:00 local are different days even though both are
    # the same UTC date (05:59 and 06:00 UTC on Oct 14).
    ended = [at(MX, 2026, 10, 13, 23, 59), at(MX, 2026, 10, 14, 0, 0)]
    assert [e.date() for e in ended] == [date(2026, 10, 14), date(2026, 10, 14)]
    assert streak_days(ended, at(MX, 2026, 10, 14, 1), MX) == 2
    assert streak_days(ended, at(MX, 2026, 10, 15, 23, 59), MX) == 2
    assert streak_days(ended, at(MX, 2026, 10, 16, 0, 0), MX) == 0


def test_streak_mexico_city_has_no_dst_in_april() -> None:
    # Review Focus 3: Mexico dropped DST in 2022; 23:30 on Apr 4 stays Apr 4 (UTC-6).
    ended = [datetime(2026, 4, 4, 5, 30, tzinfo=UTC), datetime(2026, 4, 5, 5, 30, tzinfo=UTC)]
    assert [e.astimezone(MX).date() for e in ended] == [date(2026, 4, 3), date(2026, 4, 4)]
    assert streak_days(ended, datetime(2026, 4, 5, 5, 59, tzinfo=UTC), MX) == 2
    assert streak_days(ended, datetime(2026, 4, 6, 6, 0, tzinfo=UTC), MX) == 0


def test_streak_across_new_york_spring_forward() -> None:
    # Review Focus 3: 2026-03-08 is 23 hours long in New York.
    ended = [
        at(NY, 2026, 3, 6, 23, 30),  # EST, 04:30 UTC Mar 7
        at(NY, 2026, 3, 7, 23, 30),  # EST, 04:30 UTC Mar 8
        at(NY, 2026, 3, 8, 0, 30),  # EST, before the 02:00 jump
        at(NY, 2026, 3, 8, 23, 30),  # EDT, 03:30 UTC Mar 9
    ]
    assert ended[3] == datetime(2026, 3, 9, 3, 30, tzinfo=UTC)
    assert streak_days(ended, at(NY, 2026, 3, 9, 10), NY) == 3
    assert streak_days(ended, at(NY, 2026, 3, 8, 23, 59), NY) == 3
    assert streak_days(ended[:2], at(NY, 2026, 3, 8, 23, 59), NY) == 2
    assert streak_days(ended[:2], at(NY, 2026, 3, 9, 0, 0), NY) == 0


def test_streak_across_new_york_fall_back() -> None:
    # Review Focus 3: 2026-11-01 is 25 hours long; 01:30 happens twice.
    first_0130 = at(NY, 2026, 11, 1, 1, 30, fold=0)  # EDT, 05:30 UTC
    second_0130 = at(NY, 2026, 11, 1, 1, 30, fold=1)  # EST, 06:30 UTC
    assert second_0130 - first_0130 == timedelta(hours=1)
    ended = [
        at(NY, 2026, 10, 30, 23, 59),  # EDT, 03:59 UTC Oct 31
        at(NY, 2026, 10, 31, 23, 30),  # EDT, 03:30 UTC Nov 1
        first_0130,
        second_0130,
        at(NY, 2026, 11, 1, 23, 30),  # EST, 04:30 UTC Nov 2
    ]
    assert streak_days(ended, at(NY, 2026, 11, 1, 23, 59), NY) == 3
    assert streak_days(ended, at(NY, 2026, 11, 2, 23, 59), NY) == 3
    assert streak_days(ended, at(NY, 2026, 11, 3, 0, 0), NY) == 0


def test_streak_depends_on_the_learner_timezone() -> None:
    # 18:00 UTC Oct 14 is Oct 14 in both zones; 05:30 UTC Oct 15 is Oct 15 01:30 in
    # New York but Oct 14 23:30 in Mexico City.
    ended = [datetime(2026, 10, 14, 18, 0, tzinfo=UTC), datetime(2026, 10, 15, 5, 30, tzinfo=UTC)]
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    assert streak_days(ended, now, NY) == 2
    assert streak_days(ended, now, MX) == 1


@given(
    st.integers(1, 60),
    st.integers(0, 23),
    st.sampled_from([MX, NY]),
    st.dates(min_value=date(2026, 1, 1), max_value=date(2027, 12, 31)),
)
def test_n_daily_sessions_give_streak_n(n: int, hour: int, tz: ZoneInfo, last: date) -> None:
    days = [last - timedelta(days=i) for i in range(n)]
    ended = [datetime(d.year, d.month, d.day, hour, tzinfo=tz).astimezone(UTC) for d in days]
    nxt, after = last + timedelta(days=1), last + timedelta(days=2)
    now = datetime(last.year, last.month, last.day, 23, 59, tzinfo=tz).astimezone(UTC)
    end_of_next = datetime(nxt.year, nxt.month, nxt.day, 23, 59, tzinfo=tz).astimezone(UTC)
    start_of_after = datetime(after.year, after.month, after.day, tzinfo=tz).astimezone(UTC)
    assert streak_days(ended, now, tz) == n
    assert streak_days(ended, end_of_next, tz) == n
    assert streak_days(ended, start_of_after, tz) == 0
