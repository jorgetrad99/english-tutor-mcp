from datetime import UTC, datetime, timedelta
from typing import Any

from tutor_spike.analysis.runs import Run
from tutor_spike.analysis.scoring import (
    RunOutcome,
    assign,
    payload_valid,
    score_run,
    summarize_exp1,
    summarize_exp2,
)

T0 = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


def at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def run(
    run_id: str,
    start: float,
    end: float,
    mode: str = "text",
    account: str = "free",
    stayed: str = "na",
    continued: str = "na",
    status: str = "ok",
) -> Run:
    return Run(
        run_id=run_id,
        date="2026-10-06",
        account=account,
        mode=mode,
        model_shown="Sonnet",
        start=T0 + timedelta(minutes=start),
        end=T0 + timedelta(minutes=end),
        voice_stayed_active=stayed,
        continued_with_result=continued,
        status=status,
        transcript_file="",
    )


def http_call(
    minutes: float,
    tool: str,
    arguments: Any = None,
    result: Any = None,
    error: Any = None,
    status: int = 200,
) -> dict[str, Any]:
    return {
        "kind": "http",
        "ts_in": at(minutes),
        "http": {"status": status, "mcp_session_id": "s1"},
        "rpc": {
            "method": "tools/call",
            "tool": tool,
            "params_raw": {"name": tool, "arguments": arguments},
            "result_raw": result,
            "error_raw": error,
        },
    }


def issued(minutes: float, session_id: str, tester: str = "author-free") -> dict[str, Any]:
    return {
        "kind": "tool",
        "ts": at(minutes),
        "tool": "get_profile",
        "tester": tester,
        "session_id": session_id,
    }


def args(session_id: str = "sid-1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": session_id,
        "user_turns": ["Yesterday I go to the office."],
        "errors": [
            {
                "said": "I go to the office",
                "correct": "I went to the office",
                "category": "grammar",
            },
            {"said": "invented error", "correct": "x", "category": "other"},
        ],
        "chunks_used": [],
        "task_result": "achieved",
        "hints_given": 0,
        "cefr_estimate": {"speaking": "B1+", "confidence": "low", "evidence": ["e"]},
        "confidence_1_5": 3,
    }
    return {**base, **overrides}


def outcome(
    run_id: str,
    *,
    fired: bool = True,
    valid_final: bool = True,
    valid_first: bool = True,
    said: tuple[int, int] = (1, 1),
) -> RunOutcome:
    return RunOutcome(
        run_id=run_id,
        tools_fired=fired,
        end_calls=1,
        valid_first=valid_first,
        valid_final=valid_final,
        said_pass=said[0],
        said_total=said[1],
        cefr="B1+",
        chunks_fabricated=0,
        final_arguments=None,
    )


def test_assign_gives_overlapping_records_to_the_later_started_run() -> None:
    runs = [run("r01", 0, 15), run("r02", 18, 33)]
    early, late = http_call(14, "end_session"), http_call(19, "get_profile")
    grouped = assign(runs, [early, late])
    assert grouped == {"r01": [early], "r02": [late]}


def test_assign_drops_records_outside_every_window() -> None:
    grouped = assign([run("r01", 0, 15)], [http_call(60, "get_profile")])
    assert grouped == {"r01": []}


def test_payload_valid_requires_schema_turns_and_issued_session(
    end_session_schema: dict[str, Any],
) -> None:
    assert payload_valid(args(), end_session_schema, {"sid-1"})
    assert not payload_valid(args(), end_session_schema, set())
    assert not payload_valid(args(user_turns=[]), end_session_schema, {"sid-1"})
    assert not payload_valid({**args(), "glossary": []}, end_session_schema, {"sid-1"})
    assert not payload_valid(args(task_result="done"), end_session_schema, {"sid-1"})
    assert not payload_valid(None, end_session_schema, {"sid-1"})


def test_score_run_valid_by_final_call_after_a_rejected_first_call(
    end_session_schema: dict[str, Any],
) -> None:
    records = [
        issued(1, "sid-1"),
        http_call(1, "get_profile", {}, result={"structuredContent": {}}),
        http_call(14, "end_session", {**args(), "glossary": []}, error={"code": -32602}),
        http_call(14.5, "end_session", args(), result={"structuredContent": {}}),
    ]
    result = score_run(run("r01", 0, 15), records, end_session_schema)
    assert (result.end_calls, result.valid_first, result.valid_final) == (2, False, True)
    assert (result.said_pass, result.said_total) == (1, 2)
    assert result.cefr == "B1+"
    assert result.tools_fired


def test_score_run_without_end_session_is_invalid(end_session_schema: dict[str, Any]) -> None:
    records = [issued(1, "sid-1"), http_call(1, "get_profile", {}, result={})]
    result = score_run(run("r01", 0, 15), records, end_session_schema)
    assert (result.end_calls, result.valid_final, result.said_total) == (0, False, 0)


def test_session_issued_to_the_other_account_does_not_count(
    end_session_schema: dict[str, Any],
) -> None:
    records = [issued(1, "sid-1", tester="author-pro"), http_call(14, "end_session", args())]
    result = score_run(run("r01", 0, 15, account="free"), records, end_session_schema)
    assert not result.valid_final


def test_non_empty_chunks_used_counts_as_fabrication(end_session_schema: dict[str, Any]) -> None:
    records = [issued(1, "sid-1"), http_call(14, "end_session", args(chunks_used=["c1", "c2"]))]
    result = score_run(run("r01", 0, 15), records, end_session_schema)
    assert result.chunks_fabricated == 2


def test_exp1_counts_voice_runs_meeting_all_three_conditions() -> None:
    runs = [
        run("v1", 0, 15, "voice", "free", "y", "y"),
        run("v2", 20, 35, "voice", "free", "y", "y"),
        run("v3", 40, 55, "voice", "free", "n", "y"),
        run("v4", 60, 75, "voice", "pro", "y", "y"),
        run("v5", 80, 95, "voice", "pro", "y", "n"),
        run("t1", 100, 115),
    ]
    outcomes = {r.run_id: outcome(r.run_id) for r in runs}
    summary = summarize_exp1(runs, outcomes)
    assert (summary.passes, summary.total, summary.verdict) == (3, 5, "FAIL")
    assert summary.by_account == {"free": (2, 3), "pro": (1, 2)}


def test_exp1_ignores_aborted_runs_and_waits_for_five() -> None:
    runs = [run("v1", 0, 15, "voice", stayed="y", continued="y", status="aborted")]
    summary = summarize_exp1(runs, {"v1": outcome("v1")})
    assert (summary.total, summary.verdict) == (0, "INCOMPLETE")


def test_exp2_pass_at_23_of_25() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    outcomes = {r.run_id: outcome(r.run_id, valid_final=i < 23) for i, r in enumerate(runs)}
    summary = summarize_exp2(runs, outcomes)
    assert (summary.n, summary.valid_final, summary.verdict) == (25, 23, "PASS")


def test_exp2_fallback_at_22_of_25_and_extension_below_70_percent() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    at_22 = {r.run_id: outcome(r.run_id, valid_final=i < 22) for i, r in enumerate(runs)}
    at_17 = {r.run_id: outcome(r.run_id, valid_final=i < 17) for i, r in enumerate(runs)}
    assert summarize_exp2(runs, at_22).verdict == "FAIL_WITH_FALLBACK"
    assert summarize_exp2(runs, at_17).verdict == "EXTEND"


def test_exp2_said_below_80_percent_fails_with_fallback() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    outcomes = {r.run_id: outcome(r.run_id, said=(3, 4)) for r in runs}
    assert summarize_exp2(runs, outcomes).verdict == "FAIL_WITH_FALLBACK"


def test_exp2_with_no_errors_reported_treats_said_as_not_applicable() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    outcomes = {r.run_id: outcome(r.run_id, said=(0, 0)) for r in runs}
    summary = summarize_exp2(runs, outcomes)
    assert (summary.said_total, summary.verdict) == (0, "PASS")
