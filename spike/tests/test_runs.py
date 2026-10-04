from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tutor_spike.analysis.runs import load_runs

HEADER = (
    "run_id,date,account,device,app_version,mode,model_shown,situation_card,start_local,"
    "end_local,voice_stayed_active,continued_with_result,recording_file,transcript_file,"
    "status,abort_reason,notes\n"
)


def test_load_runs_converts_local_times_to_utc(tmp_path: Path) -> None:
    path = tmp_path / "runs.csv"
    path.write_text(
        HEADER + "r01,2026-10-06,free,iphone,1.0,voice,Sonnet,3,09:00,09:17,y,y,"
        "rec.mp4,transcripts/r01.md,ok,,\n",
        encoding="utf-8",
    )
    [run] = load_runs(path, ZoneInfo("America/Mexico_City"))
    assert run.start == datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
    assert run.end == datetime(2026, 10, 6, 15, 17, tzinfo=UTC)
    assert (run.account, run.mode, run.status) == ("free", "voice", "ok")


def test_run_crossing_midnight_ends_next_day(tmp_path: Path) -> None:
    path = tmp_path / "runs.csv"
    path.write_text(
        HEADER + "r02,2026-10-06,pro,laptop,web,text,Opus,1,23:50,00:06,na,na,,,ok,,\n",
        encoding="utf-8",
    )
    [run] = load_runs(path, ZoneInfo("UTC"))
    assert run.end == datetime(2026, 10, 7, 0, 6, tzinfo=UTC)
