"""Spike-only session_id registry: get_profile issues, end_session checks (spec §3.2)."""

import json
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


class SessionRegistry:
    """Issued and ended session_ids, persisted as JSONL so a restart keeps them."""

    def __init__(self, path: Path, now: Callable[[], datetime]) -> None:
        self._path = path
        self._now = now
        self._lock = threading.Lock()
        self._owners: dict[str, str] = {}
        self._ends: dict[str, int] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._apply(json.loads(line))

    def issue(self, tester: str) -> str:
        session_id = str(uuid.uuid4())
        self._record({"event": "issued", "session_id": session_id, "tester": tester})
        return session_id

    def owner(self, session_id: str) -> str | None:
        return self._owners.get(session_id)

    def record_end(self, session_id: str) -> int:
        """Record an accepted end_session; return how many were recorded before it."""
        previous = self._ends.get(session_id, 0)
        self._record({"event": "ended", "session_id": session_id})
        return previous

    def _record(self, event: dict[str, str]) -> None:
        with self._lock:
            self._apply(event)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": self._now().isoformat(), **event}) + "\n")

    def _apply(self, event: dict[str, str]) -> None:
        session_id = event["session_id"]
        if event["event"] == "issued":
            self._owners[session_id] = event["tester"]
        elif event["event"] == "ended":
            self._ends[session_id] = self._ends.get(session_id, 0) + 1
