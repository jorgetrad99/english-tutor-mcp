"""Plan-lite: a deterministic starter plan from the track (spec 7.2)."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from tutor.domain.levels import (
    LEVEL_VALUE,
    CefrLevel,
    hours_between,
    level_after_hours,
)
from tutor.domain.profile import Profile
from tutor.domain.track import TrackItem

Variant = Literal["base", "complication"]
FeasibilityMessage = Literal["reachable", "milestone", "confidence_only"]

DEFAULT_WEEKS = 12
MIN_WEEKS = 4
MAX_WEEKS = 52
ALGORITHM = "plan_lite_v1"
_B2_VALUE = 4.0


@dataclass(frozen=True, slots=True)
class PlannedItem:
    week_no: int
    order_no: int
    track_item_id: str
    variant: Variant


@dataclass(frozen=True, slots=True)
class Feasibility:
    weeks: int
    sessions_planned: int
    hours_available: float
    hours_needed: int
    reachable: bool
    milestone_level: CefrLevel | None
    message: FeasibilityMessage


@dataclass(frozen=True, slots=True)
class PlanLite:
    items: tuple[PlannedItem, ...]
    feasibility: Feasibility


def horizon_weeks(target_date: date | None, today: date) -> int:
    """Weeks until the target date, rounded up and clamped to 4..52; 12 without a date."""
    if target_date is None:
        return DEFAULT_WEEKS
    weeks = math.ceil((target_date - today).days / 7)
    return min(max(weeks, MIN_WEEKS), MAX_WEEKS)


def _shares_use_case(item: TrackItem, profile: Profile) -> bool:
    return any(use_case in profile.use_cases for use_case in item.use_cases)


def order_track(profile: Profile, track: Sequence[TrackItem]) -> tuple[TrackItem, ...]:
    """Candidates (step 1) sorted by (CEFR band, shares a use case first, order_no) (step 2)."""
    advanced = LEVEL_VALUE[profile.self_level] >= _B2_VALUE
    candidates = [
        item
        for item in track
        if item.domain in profile.domains
        and not (advanced and item.cefr == "B1" and not _shares_use_case(item, profile))
    ]
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                LEVEL_VALUE[item.cefr],
                0 if _shares_use_case(item, profile) else 1,
                item.order_no,
            ),
        )
    )


def feasibility(profile: Profile, weeks: int) -> Feasibility:
    """Hours available against guided hours needed, with a milestone when the goal is too far."""
    hours_available = profile.minutes_per_day * profile.days_per_week * weeks / 60
    hours_needed = hours_between(profile.self_level, profile.target_level)
    reachable = hours_available >= hours_needed
    milestone = None if reachable else level_after_hours(profile.self_level, hours_available)
    message: FeasibilityMessage
    if reachable:
        message = "reachable"
    elif milestone is not None:
        message = "milestone"
    else:
        message = "confidence_only"
    return Feasibility(
        weeks=weeks,
        sessions_planned=profile.days_per_week * weeks,
        hours_available=hours_available,
        hours_needed=hours_needed,
        reachable=reachable,
        milestone_level=milestone,
        message=message,
    )


def _fill(
    ordered: Sequence[TrackItem], sessions: int, done_base_ids: frozenset[str]
) -> list[tuple[TrackItem, Variant]]:
    """Step 4: one base pass without already-done base items, then complication passes."""
    filled: list[tuple[TrackItem, Variant]] = [
        (item, "base") for item in ordered if item.id not in done_base_ids
    ][:sessions]
    while len(filled) < sessions:
        for item in ordered[: sessions - len(filled)]:
            filled.append((item, "complication"))
    return filled


def _alternate(filled: list[tuple[TrackItem, Variant]]) -> list[tuple[TrackItem, Variant]]:
    """Step 5: take the first remaining entry whose interaction type differs from the last."""
    remaining = list(filled)
    result: list[tuple[TrackItem, Variant]] = []
    while remaining:
        previous = result[-1][0].interaction_type if result else None
        index = next(
            (i for i, (item, _) in enumerate(remaining) if item.interaction_type != previous),
            0,
        )
        result.append(remaining.pop(index))
    return result


def build_plan_lite(
    profile: Profile,
    track: Sequence[TrackItem],
    today: date,
    done_base_ids: frozenset[str] = frozenset(),
) -> PlanLite:
    """Ordered, scheduled plan items plus the feasibility result (spec 7.2 steps 1-7)."""
    ordered = order_track(profile, track)
    if not ordered:
        raise ValueError("the track has no items for the profile's domains")
    weeks = horizon_weeks(profile.target_date, today)
    result = feasibility(profile, weeks)
    entries = _alternate(_fill(ordered, result.sessions_planned, done_base_ids))
    per_week = profile.days_per_week
    items = tuple(
        PlannedItem(
            week_no=i // per_week + 1,
            order_no=i % per_week + 1,
            track_item_id=item.id,
            variant=variant,
        )
        for i, (item, variant) in enumerate(entries)
    )
    return PlanLite(items=items, feasibility=result)


def rationale(f: Feasibility, profile: Profile) -> dict[str, Any]:
    """JSON-safe record of the inputs and the feasibility result, for plans.rationale."""
    return {
        "algorithm": ALGORITHM,
        "template": f.message,
        "inputs": {
            "self_level": profile.self_level,
            "target_level": profile.target_level,
            "target_date": profile.target_date.isoformat() if profile.target_date else None,
            "minutes_per_day": profile.minutes_per_day,
            "days_per_week": profile.days_per_week,
            "domains": list(profile.domains),
            "use_cases": list(profile.use_cases),
        },
        "weeks": f.weeks,
        "sessions_planned": f.sessions_planned,
        "hours_available": f.hours_available,
        "hours_needed": f.hours_needed,
        "reachable": f.reachable,
        "milestone_level": f.milestone_level,
    }


_TEMPLATES: dict[tuple[FeasibilityMessage, Literal["en", "es"]], str] = {
    ("reachable", "en"): (
        "Your plan has {sessions} sessions over {weeks} weeks (about {available} hours "
        "of practice): enough for your goal."
    ),
    ("milestone", "en"): (
        "Your plan has {sessions} sessions over {weeks} weeks (about {available} hours "
        "of practice). Your goal needs about {needed} hours, so this plan aims for "
        "{milestone} first."
    ),
    ("confidence_only", "en"): (
        "Your plan has {sessions} sessions over {weeks} weeks (about {available} hours "
        "of practice). Moving up a level needs about {needed} hours, so this plan builds "
        "confidence at your current level."
    ),
    ("reachable", "es"): (
        "Tu plan tiene {sessions} sesiones en {weeks} semanas (unas {available} horas "
        "de práctica): suficiente para tu meta."
    ),
    ("milestone", "es"): (
        "Tu plan tiene {sessions} sesiones en {weeks} semanas (unas {available} horas "
        "de práctica). Tu meta necesita unas {needed} horas, así que este plan apunta "
        "primero a {milestone}."
    ),
    ("confidence_only", "es"): (
        "Tu plan tiene {sessions} sesiones en {weeks} semanas (unas {available} horas "
        "de práctica). Subir de nivel necesita unas {needed} horas, así que este plan "
        "te da confianza en tu nivel actual."
    ),
}


def feasibility_text(f: Feasibility, lang: Literal["en", "es"]) -> str:
    """The feasibility sentence for the learner, built only from a fixed template."""
    return _TEMPLATES[(f.message, lang)].format(
        sessions=f.sessions_planned,
        weeks=f.weeks,
        available=f"{f.hours_available:.0f}",
        needed=f.hours_needed,
        milestone=f.milestone_level or "",
    )
