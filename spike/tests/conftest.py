import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def FIXED_NOW() -> datetime:
    return datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


def read_log_lines(directory: Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for path in sorted(directory.glob("calls-*.jsonl")):
        lines += [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    return lines
