"""Practice plan arithmetic (subjects spec 6.1; slice B plan rulings 28 and 29).

Weeks are calendar-free: week `w` covers days `[7w, 7w + 7)` from `start_date`, the plan's
creation date in the learner's timezone, and the completed weeks at `today` are
`floor(days / 7)`. Pure functions over dates and counts; the caller supplies `today`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

WEEK_DAYS = 7


@dataclass(frozen=True, slots=True)
class PlanInputs:
    """The three subject answers a plan is built from; a change to any of them makes a new
    plan version (ruling 29)."""

    minutes_per_session: int
    sessions_per_week: int
    target_date: date | None


@dataclass(frozen=True, slots=True)
class PlanStatus:
    version: int
    start_date: date
    sessions_per_week: int
    minutes_per_session: int
    target_date: date | None
    total_sessions: int | None
    completed: int
    expected: int
    missed: int
    projected_end: date | None
    delay_days: int | None
    adherence: float | None


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def total_sessions(inputs: PlanInputs, start_date: date) -> int | None:
    """`sessions_per_week * ceil(days(start_date -> target_date) / 7)`; None without a target.
    A target on or before the start schedules nothing."""
    if inputs.target_date is None:
        return None
    days = max(0, (inputs.target_date - start_date).days)
    return inputs.sessions_per_week * _ceil_div(days, WEEK_DAYS)


def completed_weeks(start_date: date, today: date) -> int:
    return max(0, (today - start_date).days) // WEEK_DAYS


def plan_status(
    inputs: PlanInputs, *, version: int, start_date: date, completed: int, today: date
) -> PlanStatus:
    """Spec 6.1. `expected` counts completed weeks only, so nothing is missed before the first
    week ends. `projected_end` starts from the current plan week (`start_date + 7 * weeks`) and
    adds the weeks the remaining sessions need at `sessions_per_week`, so a missed session
    pushes the end out and an on-track plan ends on its target; `delay_days` is how far past
    the target that falls (never negative). Both are None for an open-ended plan."""
    weeks = completed_weeks(start_date, today)
    expected = inputs.sessions_per_week * weeks
    total = total_sessions(inputs, start_date)
    projected_end: date | None = None
    delay_days: int | None = None
    if total is not None and inputs.target_date is not None:
        remaining = max(0, total - completed)
        weeks_left = _ceil_div(remaining, inputs.sessions_per_week)
        projected_end = start_date + timedelta(days=WEEK_DAYS * (weeks + weeks_left))
        delay_days = max(0, (projected_end - inputs.target_date).days)
    return PlanStatus(
        version=version,
        start_date=start_date,
        sessions_per_week=inputs.sessions_per_week,
        minutes_per_session=inputs.minutes_per_session,
        target_date=inputs.target_date,
        total_sessions=total,
        completed=completed,
        expected=expected,
        missed=max(0, expected - completed),
        projected_end=projected_end,
        delay_days=delay_days,
        adherence=completed / expected if expected else None,
    )


def plan_inputs_changed(old: PlanInputs, new: PlanInputs) -> bool:
    return old != new
