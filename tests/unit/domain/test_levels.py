import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.domain.levels import (
    CEFR_LEVELS,
    LEVEL_VALUE,
    CefrLevel,
    hours_between,
    is_level,
    level_after_hours,
)

pytestmark = pytest.mark.unit


def test_levels_are_ordered_half_steps() -> None:
    assert CEFR_LEVELS == ("B1", "B1+", "B2", "B2+", "C1")
    assert [LEVEL_VALUE[level] for level in CEFR_LEVELS] == [3.0, 3.5, 4.0, 4.5, 5.0]


def test_is_level() -> None:
    assert is_level("B2+")
    assert not is_level("b2")
    assert not is_level("A2")
    assert not is_level(4.0)


@pytest.mark.parametrize(
    ("start", "end", "hours"),
    [
        ("B1", "B1", 0),
        ("B1", "B1+", 90),
        ("B1", "B2", 180),
        ("B1+", "B2", 90),
        ("B2", "B2+", 100),
        ("B2", "C1", 200),
        ("B1+", "B2+", 190),
        ("B1", "C1", 380),
        ("C1", "B1", 0),
        ("B2", "B1", 0),
    ],
)
def test_hours_between(start: CefrLevel, end: CefrLevel, hours: int) -> None:
    assert hours_between(start, end) == hours


@pytest.mark.parametrize(
    ("start", "hours", "expected"),
    [
        ("B1", 0, None),
        ("B1", 89.99, None),
        ("B1", 90, "B1+"),
        ("B1", 179.9, "B1+"),
        ("B1", 180, "B2"),
        ("B1+", 189, "B2"),
        ("B1+", 190, "B2+"),
        ("B2", 99, None),
        ("B2", 199, "B2+"),
        ("B1", 10_000, "C1"),
        ("C1", 10_000, None),
    ],
)
def test_level_after_hours(start: CefrLevel, hours: float, expected: CefrLevel | None) -> None:
    assert level_after_hours(start, hours) == expected


levels = st.sampled_from(CEFR_LEVELS)


@given(levels, st.floats(min_value=0, max_value=1000))
def test_level_after_hours_never_costs_more_than_given(start: CefrLevel, hours: float) -> None:
    reached = level_after_hours(start, hours)
    if reached is not None:
        assert LEVEL_VALUE[reached] > LEVEL_VALUE[start]
        assert hours_between(start, reached) <= hours


@given(levels, levels, levels)
def test_hours_between_is_additive(a: CefrLevel, b: CefrLevel, c: CefrLevel) -> None:
    low, mid, high = sorted((a, b, c), key=LEVEL_VALUE.__getitem__)
    assert hours_between(low, high) == hours_between(low, mid) + hours_between(mid, high)
