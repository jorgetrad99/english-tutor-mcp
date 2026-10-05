"""Inicio: the learner's local day, the week trail and the celebration rule."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tutor.domain.dashboard.types import (
    DayState,
    PlannedDay,
    SessionMark,
    SessionStatus,
    TrailNode,
)


def local_today(now: datetime, tz: str) -> date:
    """The learner's calendar day. `now` must be timezone-aware."""
    try:
        zone: ZoneInfo = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return now.astimezone(UTC).date()
    return now.astimezone(zone).date()


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_week_trail(
    start: date,
    planned: Sequence[PlannedDay],
    marks: Sequence[SessionMark],
    today: date,
) -> tuple[TrailNode, ...]:
    titles = {p.day: p.title for p in planned}
    nodes: list[TrailNode] = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        closed = [m for m in marks if m.day == day and m.status is SessionStatus.CLOSED]
        extra = sum(1 for m in closed if not m.on_plan)
        title = titles.get(day)
        if title is None:
            state = DayState.REST
        elif any(m.on_plan for m in closed):
            state = DayState.DONE
        elif day == today:
            state = DayState.TODAY
        elif day > today:
            state = DayState.UPCOMING
        else:
            state = DayState.MISSED
        nodes.append(TrailNode(day, state, day == today, title, extra))
    return tuple(nodes)


def should_celebrate(newest_closed: UUID | None, last_celebrated: UUID | None) -> bool:
    """Celebrate a finished session once: the newest closed session was not celebrated yet."""
    return newest_closed is not None and newest_closed != last_celebrated
