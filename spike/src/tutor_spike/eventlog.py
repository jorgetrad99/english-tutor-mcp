"""Append-only daily JSONL log for HTTP and tool records (spec §6.1)."""

import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonlLog:
    def __init__(self, directory: Path, now: Callable[[], datetime]) -> None:
        self._directory = directory
        self._now = now
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        path = self._directory / f"calls-{self._now():%Y-%m-%d}.jsonl"
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._directory.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
