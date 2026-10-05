"""What every use case receives (unit-of-work factory, clock, valid timezones) and local time."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from tutor.domain.metrics import streak_days
from tutor.services.ports import UnitOfWork, UowFactory

STREAK_LOOKBACK = timedelta(days=400)


@dataclass(frozen=True)
class Services:
    uow: UowFactory
    clock: Callable[[], datetime]
    valid_timezones: frozenset[str]


def user_zone(uow: UnitOfWork) -> ZoneInfo:
    """The learner's zone: users.timezone (ruling 4)."""
    return ZoneInfo(uow.users.timezone())


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


def local_midnight(now: datetime, tz: ZoneInfo) -> datetime:
    """UTC instant of 00:00 local time on the local date of `now` (DST-aware)."""
    return datetime.combine(local_date(now, tz), time.min, tzinfo=tz).astimezone(UTC)


def current_streak(
    uow: UnitOfWork, now: datetime, tz: ZoneInfo, *, closing_now: bool = False
) -> int:
    """Streak over closed sessions of the last 400 days; `closing_now` counts one ending now."""
    ended = list(uow.sessions.closed_ended_at(now - STREAK_LOOKBACK))
    if closing_now:
        ended.append(now)
    return streak_days(ended, now, tz)
