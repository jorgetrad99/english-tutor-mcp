"""Per-session metrics computed from validated evidence (spec 11.3)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_args

from tutor.domain.text import find_turn
from tutor.domain.validation import Category, Evidence, ValidatedEvidence, ValidError

MAX_DURATION_MIN = 45.0


@dataclass(frozen=True, slots=True)
class SessionMetrics:
    user_words: int
    assistant_words_estimate: int | None
    user_ratio: float | None
    turns: int
    words_per_turn: float
    duration_min: float
    user_words_per_min: float
    errors_total: int
    errors_rejected: int
    errors_by_category: Mapping[str, int]
    errors_per_100w: float
    recurring_errors: int
    uptake_count: int
    chunks_offered: int
    chunks_used: int
    chunks_rejected: int
    activation_rate: float


def uptake_count(errors: Sequence[ValidError], turns: Sequence[str]) -> int:
    """Valid errors whose `correct` form appears in a user turn after the one with `said`."""
    return sum(1 for e in errors if find_turn(e.correct, turns, after=e.turn_index) is not None)


def _duration_min(started_at: datetime, ended_at: datetime) -> float:
    seconds = max((ended_at - started_at).total_seconds(), 0.0)
    return round(min(seconds / 60, MAX_DURATION_MIN), 1)


def compute_metrics(
    ev: Evidence,
    v: ValidatedEvidence,
    *,
    chunks_offered: int,
    started_at: datetime,
    ended_at: datetime,
    recent_correct_norms: frozenset[str],
) -> SessionMetrics:
    """`recent_correct_norms`: correct_norm values of the user's other closed sessions (30 d)."""
    duration = _duration_min(started_at, ended_at)
    estimate = ev.assistant_words_estimate
    ratio: float | None = None
    if estimate is not None:
        total = v.user_words + estimate
        ratio = round(v.user_words / total, 2) if total else 0.0
    by_category = {category: 0 for category in get_args(Category)}
    for e in v.errors:
        by_category[e.category] += 1
    errors_total = len(v.errors)
    used = len(v.chunks_used)
    return SessionMetrics(
        user_words=v.user_words,
        assistant_words_estimate=estimate,
        user_ratio=ratio,
        turns=v.turns,
        words_per_turn=round(v.user_words / v.turns, 1) if v.turns else 0.0,
        duration_min=duration,
        user_words_per_min=round(v.user_words / max(duration, 1.0), 1),
        errors_total=errors_total,
        errors_rejected=v.errors_rejected,
        errors_by_category=by_category,
        errors_per_100w=round(errors_total * 100 / v.user_words, 1) if v.user_words else 0.0,
        recurring_errors=sum(
            1 for e in v.errors if e.correct_norm and e.correct_norm in recent_correct_norms
        ),
        uptake_count=uptake_count(v.errors, ev.user_turns),
        chunks_offered=chunks_offered,
        chunks_used=used,
        chunks_rejected=v.chunks_rejected,
        activation_rate=round(used / chunks_offered, 2) if chunks_offered else 0.0,
    )


def metrics_to_json(m: SessionMetrics) -> dict[str, Any]:
    """JSON-safe dict with every field; `errors_by_category` becomes a plain dict."""
    return {
        "user_words": m.user_words,
        "assistant_words_estimate": m.assistant_words_estimate,
        "user_ratio": m.user_ratio,
        "turns": m.turns,
        "words_per_turn": m.words_per_turn,
        "duration_min": m.duration_min,
        "user_words_per_min": m.user_words_per_min,
        "errors_total": m.errors_total,
        "errors_rejected": m.errors_rejected,
        "errors_by_category": dict(m.errors_by_category),
        "errors_per_100w": m.errors_per_100w,
        "recurring_errors": m.recurring_errors,
        "uptake_count": m.uptake_count,
        "chunks_offered": m.chunks_offered,
        "chunks_used": m.chunks_used,
        "chunks_rejected": m.chunks_rejected,
        "activation_rate": m.activation_rate,
    }
