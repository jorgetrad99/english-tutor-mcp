from pathlib import Path

from conftest import FIXED_NOW, read_log_lines

from tutor_spike.eventlog import JsonlLog


def test_write_appends_one_line_per_record_to_the_daily_file(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path, FIXED_NOW)
    log.write({"kind": "http", "n": 1})
    log.write({"kind": "tool", "n": 2})
    assert (tmp_path / "calls-2026-10-06.jsonl").exists()
    assert [line["n"] for line in read_log_lines(tmp_path)] == [1, 2]


def test_write_keeps_non_ascii_text_readable(tmp_path: Path) -> None:
    JsonlLog(tmp_path, FIXED_NOW).write({"said": "está"})
    assert "está" in (tmp_path / "calls-2026-10-06.jsonl").read_text(encoding="utf-8")


def test_write_creates_the_directory(tmp_path: Path) -> None:
    JsonlLog(tmp_path / "raw", FIXED_NOW).write({"kind": "http"})
    assert read_log_lines(tmp_path / "raw") == [{"kind": "http"}]
