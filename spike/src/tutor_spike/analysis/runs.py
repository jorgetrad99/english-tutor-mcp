"""The author's run sheet (spec §6.2 plus continued_with_result)."""

import csv
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
CLOSED_COLUMNS = {
    "account": ("free", "pro"),
    "mode": ("voice", "text"),
    "status": ("ok", "aborted"),
    "voice_stayed_active": ("y", "n", "na"),
    "continued_with_result": ("y", "n", "na"),
}
VOICE_COLUMNS = ("voice_stayed_active", "continued_with_result")


@dataclass(frozen=True)
class Run:
    run_id: str
    date: str
    account: str
    mode: str
    model_shown: str
    start: datetime
    end: datetime
    voice_stayed_active: str
    continued_with_result: str
    status: str
    transcript_file: str

    @property
    def tester(self) -> str:
        return f"author-{self.account}"


def _validated(row: dict[str, str], seen: set[str]) -> dict[str, str]:
    """Stripped, lowercased closed columns; ValueError naming the run and column otherwise."""
    run_id = (row.get("run_id") or "").strip()
    if not RUN_ID.match(run_id):
        raise ValueError(f"run {run_id!r}: run_id must match {RUN_ID.pattern}")
    if run_id in seen:
        raise ValueError(f"run {run_id}: duplicate run_id")
    seen.add(run_id)
    clean = {**row, "run_id": run_id}
    for column, allowed in CLOSED_COLUMNS.items():
        value = (row.get(column) or "").strip().lower()
        if value not in allowed:
            raise ValueError(f"run {run_id}: {column} must be one of {', '.join(allowed)}")
        clean[column] = value
    if clean["mode"] == "voice" and clean["status"] == "ok":
        for column in VOICE_COLUMNS:
            if clean[column] == "na":
                raise ValueError(f"run {run_id}: {column} must be y or n for an ok voice run")
    return clean


def load_runs(path: Path, tz: ZoneInfo) -> list[Run]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    runs: list[Run] = []
    seen: set[str] = set()
    for raw in rows:
        row = _validated(raw, seen)
        run_id = row["run_id"]
        try:
            day = date.fromisoformat(row["date"])
            start = datetime.combine(day, time.fromisoformat(row["start_local"]), tz)
            end = datetime.combine(day, time.fromisoformat(row["end_local"]), tz)
        except ValueError as e:
            raise ValueError(f"run {run_id}: bad start_local/end_local") from e
        if end < start:
            end += timedelta(days=1)
        runs.append(
            Run(
                run_id=run_id,
                date=row["date"],
                account=row["account"],
                mode=row["mode"],
                model_shown=row["model_shown"],
                start=start.astimezone(UTC),
                end=end.astimezone(UTC),
                voice_stayed_active=row["voice_stayed_active"],
                continued_with_result=row["continued_with_result"],
                status=row["status"],
                transcript_file=row["transcript_file"],
            )
        )
    return runs


def transcript_path(data_dir: Path, run: Run) -> Path | None:
    """The run's transcript inside data_dir, or None when the sheet names none."""
    if not run.transcript_file:
        return None
    path = (data_dir / run.transcript_file).resolve()
    if not path.is_relative_to(data_dir.resolve()):
        raise ValueError(f"run {run.run_id}: transcript_file escapes the data directory")
    return path
