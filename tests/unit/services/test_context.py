"""Local-time helpers and the streak read used by the services (Review Focus 3)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from repo_contract import add_closed_session, first_item

from tutor.services.context import current_streak, local_date, local_midnight, user_zone
from tutor.services.ports import UowFactory

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("moment", "zone", "expected"),
    [
        # Mexico City is UTC-6 all year since 2022.
        (
            datetime(2026, 10, 14, 15, 0, tzinfo=UTC),
            "America/Mexico_City",
            datetime(2026, 10, 14, 6, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 10, 14, 5, 59, tzinfo=UTC),
            "America/Mexico_City",
            datetime(2026, 10, 13, 6, 0, tzinfo=UTC),
        ),
        # New York falls back on Sun 2026-11-01: midnight is still EDT (UTC-4).
        (
            datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
            "America/New_York",
            datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
            "America/New_York",
            datetime(2026, 11, 2, 5, 0, tzinfo=UTC),
        ),
        # New York springs forward on Sun 2026-03-08: midnight is still EST (UTC-5).
        (
            datetime(2026, 3, 8, 12, 0, tzinfo=UTC),
            "America/New_York",
            datetime(2026, 3, 8, 5, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
            "America/New_York",
            datetime(2026, 3, 9, 4, 0, tzinfo=UTC),
        ),
    ],
)
def test_local_midnight_is_the_start_of_the_local_day(
    moment: datetime, zone: str, expected: datetime
) -> None:
    # Review Focus 3
    assert local_midnight(moment, ZoneInfo(zone)) == expected


def test_local_date_uses_the_zone() -> None:
    moment = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)
    assert local_date(moment, ZoneInfo("America/Mexico_City")) == date(2026, 10, 14)
    assert local_date(moment, ZoneInfo("UTC")) == date(2026, 10, 15)


def test_current_streak_counts_closed_sessions_and_the_one_closing_now(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    d = timedelta(days=1)
    m20 = timedelta(minutes=20)
    with uow_factory(user_id) as uow:
        item = first_item(uow)
        add_closed_session(uow, item, now - 2 * d - m20, now - 2 * d, status="incomplete")
        add_closed_session(uow, item, now - d - m20, now - d)
    with uow_factory(user_id) as uow:
        zone = user_zone(uow)
        assert zone == ZoneInfo("America/Mexico_City")
        assert current_streak(uow, now, zone) == 1
        assert current_streak(uow, now, zone, closing_now=True) == 2
