from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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


def test_load_runs_raises_on_unparsable_start_local(tmp_path: Path) -> None:
    path = tmp_path / "runs.csv"
    path.write_text(
        HEADER + "r03,2026-10-06,free,iphone,1.0,voice,Sonnet,3,not-a-time,09:17,y,y,"
        "rec.mp4,transcripts/r03.md,ok,,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run r03"):
        load_runs(path, ZoneInfo("UTC"))


def test_load_runs_raises_on_blank_end_local(tmp_path: Path) -> None:
    path = tmp_path / "runs.csv"
    path.write_text(
        HEADER + "r04,2026-10-06,free,iphone,1.0,voice,Sonnet,3,09:00,,y,y,"
        "rec.mp4,transcripts/r04.md,ok,,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run r04"):
        load_runs(path, ZoneInfo("UTC"))


GOOD = "r05,2026-10-06,free,iphone,1.0,voice,Sonnet,3,09:00,09:17,y,n,rec.mp4,,ok,,\n"


def write(tmp_path: Path, *rows: str, bom: bool = False) -> Path:
    path = tmp_path / "runs.csv"
    path.write_text(("\ufeff" if bom else "") + HEADER + "".join(rows), encoding="utf-8")
    return path


def test_load_runs_accepts_a_bom_and_normalizes_case_and_spaces(tmp_path: Path) -> None:
    row = "r05,2026-10-06, Free ,iphone,1.0,VOICE,Sonnet,3,09:00,09:17, Y ,n,rec.mp4,,OK ,,\n"
    [run] = load_runs(write(tmp_path, row, bom=True), ZoneInfo("UTC"))
    assert run.run_id == "r05"
    assert (run.account, run.mode, run.status) == ("free", "voice", "ok")
    assert (run.voice_stayed_active, run.continued_with_result) == ("y", "n")


@pytest.mark.parametrize(
    ("row", "column"),
    [
        (GOOD.replace(",free,", ",team,"), "account"),
        (GOOD.replace(",voice,", ",video,"), "mode"),
        (GOOD.replace(",ok,", ",done,"), "status"),
        (GOOD.replace(",y,n,", ",yes,n,"), "voice_stayed_active"),
        (GOOD.replace(",y,n,", ",y,maybe,"), "continued_with_result"),
        (GOOD.replace(",y,n,", ",na,n,"), "voice_stayed_active"),
        (GOOD.replace(",y,n,", ",y,na,"), "continued_with_result"),
    ],
)
def test_load_runs_rejects_values_outside_the_closed_sets(
    tmp_path: Path, row: str, column: str
) -> None:
    with pytest.raises(ValueError, match=rf"run r05: .*{column}"):
        load_runs(write(tmp_path, row), ZoneInfo("UTC"))


def test_aborted_voice_run_may_leave_the_voice_columns_na(tmp_path: Path) -> None:
    row = GOOD.replace(",y,n,", ",na,na,").replace(",ok,", ",aborted,")
    [run] = load_runs(write(tmp_path, row), ZoneInfo("UTC"))
    assert run.status == "aborted"


@pytest.mark.parametrize("run_id", ["../r1", "r 1", "", "r" * 33, "run/1"])
def test_load_runs_rejects_unsafe_run_ids(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError, match="run_id"):
        load_runs(write(tmp_path, GOOD.replace("r05,", f"{run_id},", 1)), ZoneInfo("UTC"))


def test_load_runs_rejects_duplicate_run_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="run r05: duplicate run_id"):
        load_runs(write(tmp_path, GOOD, GOOD), ZoneInfo("UTC"))
