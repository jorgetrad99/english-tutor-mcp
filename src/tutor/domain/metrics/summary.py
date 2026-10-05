"""The 4-line end_session summary and the streak (spec 11.4, 11.5)."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tutor.domain.metrics.session import SessionMetrics


def _num(value: float) -> str:
    """12.0 -> "12", 12.5 -> "12.5"."""
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _plural(count: float, word: str) -> str:
    return word if count == 1 else f"{word}s"


def summary_text(m: SessionMetrics, streak: int) -> str:
    """Spec 11.4 templates, English, 4 lines joined by a newline."""
    spoke = (
        f"You spoke {m.user_words} {_plural(m.user_words, 'word')} in {_num(m.duration_min)} "
        f"{_plural(m.duration_min, 'minute')} ({_num(m.user_words_per_min)} per minute)."
    )
    if m.errors_total == 0:
        fixed = "No mistakes were recorded."
    else:
        fixed = (
            f"You fixed {m.uptake_count} of your {m.errors_total} "
            f"{_plural(m.errors_total, 'mistake')} during the session."
        )
    used = (
        f"You used {m.chunks_used} of today's {m.chunks_offered} "
        f"{_plural(m.chunks_offered, 'phrase')}."
    )
    days = f"Streak: {streak} {_plural(streak, 'day')}."
    return "\n".join((spoke, fixed, used, days))


def streak_days(closed_ended_at: Sequence[datetime], now: datetime, tz: ZoneInfo) -> int:
    """Consecutive local days with a closed session, ending today or yesterday (local)."""
    days = {ended.astimezone(tz).date() for ended in closed_ended_at}
    today = now.astimezone(tz).date()
    day = today if today in days else today - timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return count
