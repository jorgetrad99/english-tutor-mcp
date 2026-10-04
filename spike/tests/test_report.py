import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tutor_spike.analysis import __main__ as report_cli
from tutor_spike.analysis import scoring
from tutor_spike.analysis.__main__ import render_report
from tutor_spike.analysis.scoring import end_session_schema_from

HEADER = (
    "run_id,date,account,device,app_version,mode,model_shown,situation_card,start_local,"
    "end_local,voice_stayed_active,continued_with_result,recording_file,transcript_file,"
    "status,abort_reason,notes\n"
)


def write_sample_data(tmp_path: Path, end_session_schema: dict[str, Any]) -> None:
    """One text run r01 (free) with transcript, annotation, schema and a valid payload."""
    (tmp_path / "runs.csv").write_text(
        HEADER + "r01,2026-10-06,free,laptop,web,text,Sonnet,3,15:00,15:15,na,na,,"
        "transcripts/r01.md,ok,,\n",
        encoding="utf-8",
    )
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "r01.md").write_text(
        "U: Yesterday I go to the office.\nA: Oh no.\n", encoding="utf-8"
    )
    (tmp_path / "annotations").mkdir()
    (tmp_path / "annotations" / "r01.json").write_text(
        json.dumps([{"said": "I go to the office", "correct": "I went to the office"}]),
        encoding="utf-8",
    )
    t = datetime(2026, 10, 6, 15, 1, tzinfo=UTC)
    arguments = {
        "session_id": "sid-1",
        "user_turns": ["Yesterday I go to the office."],
        "errors": [{"said": "I go to the office", "correct": "I went", "category": "grammar"}],
        "chunks_used": [],
        "task_result": "achieved",
        "hints_given": 0,
        "cefr_estimate": {"speaking": "B1+", "confidence": "low", "evidence": ["e"]},
        "confidence_1_5": 3,
    }
    tools_list = {"tools": [{"name": "end_session", "inputSchema": end_session_schema}]}
    lines = [
        {
            "kind": "http",
            "ts_in": t.isoformat(),
            "http": {"status": 200},
            "rpc": {"method": "tools/list", "result_raw": tools_list},
        },
        {
            "kind": "tool",
            "ts": t.isoformat(),
            "tool": "get_profile",
            "tester": "author-free",
            "session_id": "sid-1",
        },
        {
            "kind": "http",
            "ts_in": (t + timedelta(minutes=13)).isoformat(),
            "http": {"status": 200},
            "rpc": {
                "method": "tools/call",
                "tool": "end_session",
                "params_raw": {"name": "end_session", "arguments": arguments},
                "result_raw": {"structuredContent": {"accepted": True}},
                "error_raw": None,
            },
        },
    ]
    (tmp_path / "calls-2026-10-06.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )


def test_report_renders_every_section(tmp_path: Path, end_session_schema: dict[str, Any]) -> None:
    write_sample_data(tmp_path, end_session_schema)
    report = render_report(tmp_path, ZoneInfo("UTC"))
    for heading in ("## 01", "## 02", "## 03", "## 04"):
        assert heading in report
    assert "| r01 | 2026-10-06 | web / free / text | valid (final), 1 call(s), said 1/1 |" in report
    assert "INCOMPLETE" in report


def _valid_args(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "user_turns": ["Yesterday I go to the office."],
        "errors": [{"said": "I go to the office", "correct": "I went", "category": "grammar"}],
        "chunks_used": [],
        "task_result": "achieved",
        "hints_given": 0,
        "cefr_estimate": {"speaking": "B1+", "confidence": "low", "evidence": ["e"]},
        "confidence_1_5": 3,
    }


def _extra_run(
    tmp_path: Path, run_id: str, start: datetime, arguments: dict[str, Any], annotate: bool
) -> str:
    """Write transcript, optional annotation and log lines; return the runs.csv row."""
    (tmp_path / "transcripts" / f"{run_id}.md").write_text(
        "U: Yesterday I go to the office.\nA: Oh no.\n", encoding="utf-8"
    )
    if annotate:
        (tmp_path / "annotations" / f"{run_id}.json").write_text(
            json.dumps([{"said": "I go to the office", "correct": "x"}]), encoding="utf-8"
        )
    lines = [
        {
            "kind": "tool",
            "ts": (start + timedelta(minutes=1)).isoformat(),
            "tool": "get_profile",
            "tester": "author-free",
            "session_id": arguments["session_id"],
        },
        {
            "kind": "http",
            "ts_in": (start + timedelta(minutes=13)).isoformat(),
            "http": {"status": 200},
            "rpc": {
                "method": "tools/call",
                "tool": "end_session",
                "params_raw": {"name": "end_session", "arguments": arguments},
                "result_raw": {"structuredContent": {"accepted": True}},
                "error_raw": None,
            },
        },
    ]
    with (tmp_path / "calls-2026-10-06.jsonl").open("a", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(x) for x in lines) + "\n")
    end = (start + timedelta(minutes=15)).strftime("%H:%M")
    return (
        f"{run_id},2026-10-06,free,laptop,web,text,Sonnet,3,{start.strftime('%H:%M')},{end},"
        f"na,na,,transcripts/{run_id}.md,ok,,\n"
    )


def test_report_sections_03_and_04_content(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    report = render_report(tmp_path, ZoneInfo("UTC"))
    row = next(x for x in report.splitlines() if x.startswith("| r01 |") and "turns R" in x)
    assert "turns R 100% / P 100%" in row
    assert "errors R 100% / P 100%" in row
    assert "user_turns strict: R 100% / P 100%" in report
    assert "fuzzy: R 100% / P 100%." in report
    assert "chunks_used: not measurable" in report
    for group in ("all", "account=free", "mode=text", "model=Sonnet"):
        assert f"| {group} | 1 | B1+ |" in report


def test_report_excludes_invalid_payload_and_gates_errors_on_annotation(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    invalid = {**_valid_args("sid-2"), "unexpected": "field"}
    row2 = _extra_run(
        tmp_path, "r02", datetime(2026, 10, 6, 16, 0, tzinfo=UTC), invalid, annotate=True
    )
    row3 = _extra_run(
        tmp_path,
        "r03",
        datetime(2026, 10, 6, 17, 0, tzinfo=UTC),
        _valid_args("sid-3"),
        annotate=False,
    )
    with (tmp_path / "runs.csv").open("a", encoding="utf-8") as f:
        f.write(row2 + row3)
    lines = render_report(tmp_path, ZoneInfo("UTC")).splitlines()
    section3 = [x for x in lines if "turns R" in x]
    assert not any(x.startswith("| r02 |") for x in section3)
    assert any(x.startswith("| r02 |") and "invalid" in x for x in lines)
    r03 = next(x for x in section3 if x.startswith("| r03 |"))
    assert "turns R 100% / P 100%" in r03
    assert "errors" not in r03
    r01 = next(x for x in section3 if x.startswith("| r01 |"))
    assert "errors R 100% / P 100%" in r01
    assert any(x.startswith("| all | 2 | B1+ |") for x in lines)


def _append_log(tmp_path: Path, *lines: dict[str, Any]) -> None:
    with (tmp_path / "calls-2026-10-06.jsonl").open("a", encoding="utf-8") as f:
        f.write("".join(json.dumps(x) + "\n" for x in lines))


def test_report_lists_unassigned_calls_and_fidelity_exclusions(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    stray = datetime(2026, 10, 6, 20, 0, tzinfo=UTC).isoformat()
    _append_log(
        tmp_path,
        {"kind": "call", "ts": stray, "tool": "get_profile", "tester": None},
        {
            "kind": "http",
            "ts_in": stray,
            "http": {"method": "POST", "path": "/mcp", "status": 200},
            "rpc": {"method": "tools/call", "tool": "get_profile", "params_raw": {}},
        },
    )
    rows = (
        "r02,2026-10-06,pro,iphone,1.0,voice,Sonnet,3,16:00,16:15,y,y,,,ok,,\n"
        "r03,2026-10-06,free,laptop,web,text,Sonnet,3,17:00,17:15,na,na,,,aborted,crash,\n"
        "r04,2026-10-06,free,laptop,web,text,Sonnet,3,18:00,18:15,na,na,,,ok,,\n"
    )
    with (tmp_path / "runs.csv").open("a", encoding="utf-8") as f:
        f.write(rows)
    report = render_report(tmp_path, ZoneInfo("UTC"))
    assert "## Unassigned records" in report
    assert "tools/call HTTP lines: 1" in report
    assert 'kind:"call" records: 1' in report
    assert stray in report
    for line in ("- r02: invalid final", "- r03: aborted", "- r04: invalid final"):
        assert line in report


def test_report_excludes_a_valid_run_without_transcript(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    (tmp_path / "transcripts" / "r01.md").unlink()
    assert "- r01: no transcript" in render_report(tmp_path, ZoneInfo("UTC"))


def _drop_tools_list(tmp_path: Path) -> None:
    log = tmp_path / "calls-2026-10-06.jsonl"
    kept = [x for x in log.read_text(encoding="utf-8").splitlines() if "tools/list" not in x]
    log.write_text("\n".join(kept) + "\n", encoding="utf-8")


def test_schema_falls_back_to_build_mcp_without_tools_list(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    _drop_tools_list(tmp_path)
    schema, from_log = end_session_schema_from(scoring.load_records(tmp_path))
    assert (schema, from_log) == (end_session_schema, False)
    report = render_report(tmp_path, ZoneInfo("UTC"))
    assert report.startswith("end_session schema: no tools/list response in the log")
    assert "valid (final)" in report


def test_schema_from_the_log_is_named_in_the_header(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    report = render_report(tmp_path, ZoneInfo("UTC"))
    assert report.startswith("end_session schema: from the logged tools/list response")


def test_schema_error_when_log_and_fallback_both_fail(
    tmp_path: Path, end_session_schema: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(**_: Any) -> Any:
        raise RuntimeError("no server")

    monkeypatch.setattr(scoring, "build_mcp", broken)
    with pytest.raises(ValueError, match="tools/list"):
        end_session_schema_from([])


def test_cli_writes_the_report_to_out_as_utf8(
    tmp_path: Path, end_session_schema: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    out = tmp_path / "report.md"
    argv = ["analysis", "--data", str(tmp_path), "--tz", "UTC", "--out", str(out)]
    monkeypatch.setattr("sys.argv", argv)
    report_cli.main()
    assert out.read_text(encoding="utf-8") == render_report(tmp_path, ZoneInfo("UTC"))
    assert "≥ 90%" in out.read_text(encoding="utf-8")


def test_cli_prints_the_report_without_out(
    tmp_path: Path,
    end_session_schema: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    monkeypatch.setattr("sys.argv", ["analysis", "--data", str(tmp_path), "--tz", "UTC"])
    report_cli.main()
    assert "## 02 — end_session reliability" in capsys.readouterr().out


def _voice_row(run_id: str, start: datetime, transcript: bool) -> str:
    end = (start + timedelta(minutes=15)).strftime("%H:%M")
    name = f"transcripts/{run_id}.md" if transcript else ""
    return (
        f"{run_id},2026-10-06,free,iphone,1.0,voice,Sonnet,3,{start.strftime('%H:%M')},{end},"
        f"y,y,,{name},ok,,\n"
    )


def test_voice_run_with_transcript_is_measured_in_section_03(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    start = datetime(2026, 10, 6, 16, 0, tzinfo=UTC)
    _extra_run(tmp_path, "v01", start, _valid_args("sid-v1"), annotate=False)
    with (tmp_path / "runs.csv").open("a", encoding="utf-8") as f:
        f.write(_voice_row("v01", start, transcript=True))
    report = render_report(tmp_path, ZoneInfo("UTC"))
    row = next(x for x in report.splitlines() if x.startswith("| v01 |") and "turns R" in x)
    assert "turns R 100% / P 100%" in row
    assert "- v01:" not in report
    assert "user_turns strict: R 100% / P 100%" in report


def test_voice_run_without_transcript_is_excluded_with_that_reason(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    start = datetime(2026, 10, 6, 16, 0, tzinfo=UTC)
    _extra_run(tmp_path, "v01", start, _valid_args("sid-v1"), annotate=False)
    (tmp_path / "transcripts" / "v01.md").unlink()
    with (tmp_path / "runs.csv").open("a", encoding="utf-8") as f:
        f.write(_voice_row("v01", start, transcript=False))
    assert "- v01: no transcript" in render_report(tmp_path, ZoneInfo("UTC"))


def test_report_lists_uncounted_413_requests_by_timestamp_only(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    stamp = datetime(2026, 10, 6, 15, 14, tzinfo=UTC).isoformat()
    _append_log(
        tmp_path,
        {
            "kind": "http",
            "ts_in": stamp,
            "http": {"method": "POST", "path": "/mcp", "status": 413, "mcp_session_id": None},
            "rpc": {"id": None, "method": None, "tool": None, "params_raw": "SECRET-BODY"},
        },
    )
    report = render_report(tmp_path, ZoneInfo("UTC"))
    assert f"- Uncounted 413 requests: 1 ({stamp})" in report
    assert "SECRET-BODY" not in report
    assert "| r01 | 2026-10-06 | web / free / text | valid (final), 1 call(s)" in report
