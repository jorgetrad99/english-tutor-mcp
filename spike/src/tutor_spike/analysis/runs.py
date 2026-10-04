"""The author's run sheet (spec §6.2 plus continued_with_result)."""

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


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


def load_runs(path: Path, tz: ZoneInfo) -> list[Run]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    runs: list[Run] = []
    for row in rows:
        day = date.fromisoformat(row["date"])
        start = datetime.combine(day, time.fromisoformat(row["start_local"]), tz)
        end = datetime.combine(day, time.fromisoformat(row["end_local"]), tz)
        if end < start:
            end += timedelta(days=1)
        runs.append(
            Run(
                run_id=row["run_id"],
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
