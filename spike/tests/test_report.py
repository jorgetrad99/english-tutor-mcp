import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tutor_spike.analysis.__main__ import render_report

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
