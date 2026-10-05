"""FSRS-4.5 scheduler (spec 10.3).

Formulas and default weights are those of py-fsrs v2.5.1 (the last FSRS-4.5 release), verified in
the planning research: full-float stability and difficulty, no learning steps, no fuzz. One graded
attempt per item per day; the post-lapse cap S'_f <= S follows fsrs4anki 4.5 and the optimizer.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal

Rating = Literal[1, 2, 3, 4]

TARGET_RETENTION = 0.85
DEFAULT_WEIGHTS: tuple[float, ...] = (
    0.4872,
    1.4003,
    3.7145,
    13.8206,
    5.1618,
    1.2298,
    0.8975,
    0.031,
    1.6474,
    0.1367,
    1.0461,
    2.1072,
    0.0793,
    0.3246,
    1.587,
    0.2272,
    2.8755,
)
DECAY = -0.5
FACTOR: float = 0.9 ** (1 / DECAY) - 1  # 19/81
MAX_INTERVAL_DAYS = 36500
MIN_INITIAL_STABILITY = 0.1
MIN_DIFFICULTY = 1.0
MAX_DIFFICULTY = 10.0
_AGAIN, _HARD, _GOOD, _EASY = 1, 2, 3, 4


@dataclass(frozen=True, slots=True)
class FsrsState:
    stability: float | None
    difficulty: float | None
    reps: int
    lapses: int
    last_review: datetime | None
    due: datetime


def retrievability(elapsed_days: float, stability: float) -> float:
    """R(t, S) = (1 + FACTOR * t / S) ** DECAY; R(S, S) = 0.9."""
    r: float = (1 + FACTOR * elapsed_days / stability) ** DECAY
    return r


def next_interval(
    stability: float, retention: float, maximum_interval: int = MAX_INTERVAL_DAYS
) -> int:
    """Days until R falls to `retention`: round(S / FACTOR * (r ** (1 / DECAY) - 1)), 1..max."""
    interval: float = stability / FACTOR * (retention ** (1 / DECAY) - 1)
    return min(max(round(interval), 1), maximum_interval)


def init_stability(w: Sequence[float], rating: Rating) -> float:
    """S0(G) = w[G - 1], floored at 0.1."""
    return max(w[rating - 1], MIN_INITIAL_STABILITY)


def init_difficulty(w: Sequence[float], rating: Rating) -> float:
    """D0(G) = w4 - (G - 3) * w5, clamped to [1, 10] (the linear FSRS-4.5 form)."""
    return min(max(w[4] - w[5] * (rating - 3), MIN_DIFFICULTY), MAX_DIFFICULTY)


def next_difficulty(w: Sequence[float], difficulty: float, rating: Rating) -> float:
    """D' = w7 * D0(3) + (1 - w7) * (D - w6 * (G - 3)), clamped to [1, 10]."""
    shifted = difficulty - w[6] * (rating - 3)
    reverted = w[7] * w[4] + (1 - w[7]) * shifted
    return min(max(reverted, MIN_DIFFICULTY), MAX_DIFFICULTY)


def next_recall_stability(
    w: Sequence[float], difficulty: float, stability: float, r: float, rating: Rating
) -> float:
    """Stability after a successful recall (G = 2, 3, 4); D is the pre-update difficulty."""
    hard_penalty = w[15] if rating == _HARD else 1.0
    easy_bonus = w[16] if rating == _EASY else 1.0
    return stability * (
        1
        + math.exp(w[8])
        * (11 - difficulty)
        * math.pow(stability, -w[9])
        * (math.exp((1 - r) * w[10]) - 1)
        * hard_penalty
        * easy_bonus
    )


def next_forget_stability(
    w: Sequence[float], difficulty: float, stability: float, r: float
) -> float:
    """Stability after a lapse (G = 1), before the post-lapse cap; D is the pre-update value."""
    return (
        w[11]
        * math.pow(difficulty, -w[12])
        * (math.pow(stability + 1, w[13]) - 1)
        * math.exp((1 - r) * w[14])
    )


def new_state(first_due: datetime) -> FsrsState:
    """A never-reviewed item, first due at `first_due`."""
    return FsrsState(None, None, 0, 0, None, first_due)


def _check(rating: int, now: datetime, weights: Sequence[float], retention: float) -> None:
    if rating not in (_AGAIN, _HARD, _GOOD, _EASY):
        raise ValueError("rating must be 1, 2, 3 or 4")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if len(weights) != len(DEFAULT_WEIGHTS):
        raise ValueError("FSRS-4.5 needs 17 weights")
    if not 0 < retention < 1:
        raise ValueError("retention must be between 0 and 1")


def review(
    state: FsrsState,
    rating: Rating,
    now: datetime,
    *,
    weights: Sequence[float] = DEFAULT_WEIGHTS,
    retention: float = TARGET_RETENTION,
) -> FsrsState:
    """Apply one graded attempt at `now` and schedule the next one."""
    _check(rating, now, weights, retention)
    lapsed = False
    if state.stability is None or state.difficulty is None or state.last_review is None:
        stability = init_stability(weights, rating)
        difficulty = init_difficulty(weights, rating)
    else:
        elapsed = max((now - state.last_review).days, 0)
        r = retrievability(elapsed, state.stability)
        difficulty = next_difficulty(weights, state.difficulty, rating)
        if rating == _AGAIN:
            lapsed = True
            forget = next_forget_stability(weights, state.difficulty, state.stability, r)
            stability = min(forget, state.stability)
        else:
            stability = next_recall_stability(weights, state.difficulty, state.stability, r, rating)
    days = next_interval(stability, retention)
    return replace(
        state,
        stability=stability,
        difficulty=difficulty,
        reps=state.reps + 1,
        lapses=state.lapses + (1 if lapsed else 0),
        last_review=now,
        due=now + timedelta(days=days),
    )


def state_to_json(state: FsrsState) -> dict[str, Any]:
    """JSON-safe dict; datetimes as ISO 8601 with offset."""
    return {
        "stability": state.stability,
        "difficulty": state.difficulty,
        "reps": state.reps,
        "lapses": state.lapses,
        "last_review": state.last_review.isoformat() if state.last_review else None,
        "due": state.due.isoformat(),
    }


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("stability and difficulty must be numbers or null")
    return float(value)


def _aware(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetimes must be ISO 8601 strings")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("datetimes must carry a UTC offset")
    return parsed


def state_from_json(data: Mapping[str, Any]) -> FsrsState:
    """Inverse of state_to_json; raises ValueError on malformed input."""
    reps, lapses = data["reps"], data["lapses"]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (reps, lapses)):
        raise ValueError("reps and lapses must be non-negative integers")
    last_review = data["last_review"]
    return FsrsState(
        stability=_optional_float(data["stability"]),
        difficulty=_optional_float(data["difficulty"]),
        reps=reps,
        lapses=lapses,
        last_review=None if last_review is None else _aware(last_review),
        due=_aware(data["due"]),
    )
