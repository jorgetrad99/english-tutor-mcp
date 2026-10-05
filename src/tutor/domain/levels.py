"""CEFR levels on the half-step scale and guided-hours estimates (spec 7.2 step 7)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, TypeGuard

CefrLevel = Literal["B1", "B1+", "B2", "B2+", "C1"]

CEFR_LEVELS: tuple[CefrLevel, ...] = ("B1", "B1+", "B2", "B2+", "C1")
LEVEL_VALUE: Mapping[CefrLevel, float] = MappingProxyType(
    {"B1": 3.0, "B1+": 3.5, "B2": 4.0, "B2+": 4.5, "C1": 5.0}
)

_B2_VALUE = 4.0
_HOURS_BELOW_B2 = 90
_HOURS_FROM_B2 = 100


def is_level(value: object) -> TypeGuard[CefrLevel]:
    """True when `value` is one of the five CEFR levels used by the product."""
    return isinstance(value, str) and value in CEFR_LEVELS


def _step_cost(from_level: CefrLevel) -> int:
    """Hours of guided practice for the half-step that starts at `from_level`."""
    return _HOURS_BELOW_B2 if LEVEL_VALUE[from_level] < _B2_VALUE else _HOURS_FROM_B2


def hours_between(start: CefrLevel, end: CefrLevel) -> int:
    """Guided hours from `start` up to `end`; 0 when `end` is not above `start`."""
    first, last = CEFR_LEVELS.index(start), CEFR_LEVELS.index(end)
    return sum(_step_cost(CEFR_LEVELS[i]) for i in range(first, last))


def level_after_hours(start: CefrLevel, hours: float) -> CefrLevel | None:
    """Highest level above `start` whose cumulative cost fits in `hours`; None if none fits."""
    reached: CefrLevel | None = None
    for level in CEFR_LEVELS[CEFR_LEVELS.index(start) + 1 :]:
        if hours_between(start, level) > hours:
            break
        reached = level
    return reached
