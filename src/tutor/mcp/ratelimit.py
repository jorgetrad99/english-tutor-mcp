"""In-memory sliding-window limiter: 60 MCP calls per minute per user (spec section 13)."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(
        self, limit: int = 60, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Count the call and return True, or return False (not counted) when over the limit."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self._window_s:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True
