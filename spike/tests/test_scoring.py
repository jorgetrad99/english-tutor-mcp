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
    unassigned_records,
    uncounted_too_large,
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
    mcp: str | None = "s1",
    rpc_id: int | None = None,
) -> dict[str, Any]:
    return {
        "kind": "http",
        "ts_in": at(minutes),
        "http": {"method": "POST", "path": "/mcp", "status": status, "mcp_session_id": mcp},
        "rpc": {
            "id": rpc_id,
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


def test_assign_records_inside_enclosed_run_not_just_started_before() -> None:
    runs = [run("r01", 0, 60), run("r02", 10, 12, status="aborted")]
    record = http_call(30, "end_session")
    grouped = assign(runs, [record])
    assert grouped == {"r01": [record], "r02": []}


def test_exp1_exactly_4_of_5_passing_is_pass() -> None:
    runs = [
        run("v1", 0, 15, "voice", "free", "y", "y"),
        run("v2", 20, 35, "voice", "free", "y", "y"),
        run("v3", 40, 55, "voice", "free", "y", "y"),
        run("v4", 60, 75, "voice", "free", "y", "y"),
        run("v5", 80, 95, "voice", "pro", "y", "y"),
    ]
    outcomes = {
        runs[0].run_id: outcome(runs[0].run_id),
        runs[1].run_id: outcome(runs[1].run_id),
        runs[2].run_id: outcome(runs[2].run_id),
        runs[3].run_id: outcome(runs[3].run_id),
        runs[4].run_id: outcome(runs[4].run_id, fired=False),
    }
    summary = summarize_exp1(runs, outcomes)
    assert (summary.passes, summary.total, summary.verdict) == (4, 5, "PASS")


def test_exp1_tools_fired_false_with_y_y_does_not_pass() -> None:
    runs = [
        run("v1", 0, 15, "voice", "free", "y", "y"),
        run("v2", 20, 35, "voice", "free", "y", "y"),
        run("v3", 40, 55, "voice", "free", "y", "y"),
        run("v4", 60, 75, "voice", "free", "y", "y"),
        run("v5", 80, 95, "voice", "pro", "y", "y"),
    ]
    outcomes = {r.run_id: outcome(r.run_id, fired=i > 0) for i, r in enumerate(runs)}
    summary = summarize_exp1(runs, outcomes)
    assert summary.passes == 4


def test_exp1_sixth_ok_voice_run_is_ignored() -> None:
    runs = [
        run("v1", 0, 15, "voice", "free", "y", "y"),
        run("v2", 20, 35, "voice", "free", "y", "y"),
        run("v3", 40, 55, "voice", "free", "y", "y"),
        run("v4", 60, 75, "voice", "free", "y", "y"),
        run("v5", 80, 95, "voice", "pro", "y", "y"),
        run("v6", 100, 115, "voice", "pro", "y", "y"),
    ]
    outcomes = {r.run_id: outcome(r.run_id) for r in runs}
    summary = summarize_exp1(runs, outcomes)
    assert summary.total == 5


def test_exp2_18_of_25_valid_is_fail_with_fallback() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    outcomes = {r.run_id: outcome(r.run_id, valid_final=i < 18) for i, r in enumerate(runs)}
    summary = summarize_exp2(runs, outcomes)
    assert (summary.valid_final, summary.verdict) == (18, "FAIL_WITH_FALLBACK")


def test_exp2_n_24_ok_runs_is_incomplete() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(24)]
    outcomes = {r.run_id: outcome(r.run_id) for r in runs}
    summary = summarize_exp2(runs, outcomes)
    assert (summary.n, summary.verdict) == (24, "INCOMPLETE")


def test_exp2_aborted_runs_excluded_from_n() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    runs += [run(f"a{i}", 500 + i * 20, 500 + i * 20 + 15, status="aborted") for i in range(2)]
    outcomes = {r.run_id: outcome(r.run_id) for r in runs}
    summary = summarize_exp2(runs, outcomes)
    assert summary.n == 25


def test_exp2_beyond_25th_ok_run_ignored() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(30)]
    outcomes = {r.run_id: outcome(r.run_id) for r in runs}
    summary = summarize_exp2(runs, outcomes)
    assert summary.n == 25


def test_score_run_first_valid_last_invalid(end_session_schema: dict[str, Any]) -> None:
    records = [
        issued(1, "sid-1"),
        http_call(1, "end_session", args(), result={"structuredContent": {}}),
        http_call(14, "end_session", {**args(), "glossary": []}, error={"code": -32602}),
    ]
    result = score_run(run("r01", 0, 15), records, end_session_schema)
    assert (result.valid_first, result.valid_final) == (True, False)


def call(
    minutes: float,
    tool: str,
    tester: str | None,
    mcp: str,
    rpc_id: int,
    session_id: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "call",
        "ts": at(minutes),
        "tool": tool,
        "tester": tester,
        "mcp_session_id": mcp,
        "rpc_id": str(rpc_id),
        "session_id": session_id,
    }


def end_call(
    minutes: float,
    arguments: Any,
    *,
    mcp: str = "m1",
    rpc_id: int = 2,
    tester: str | None = "author-free",
    valid: bool = True,
) -> list[dict[str, Any]]:
    """The kind:"call" record and the HTTP line of one end_session call."""
    session_id = arguments.get("session_id") if isinstance(arguments, dict) else None
    outcome = {"result": {"structuredContent": {}}} if valid else {"error": {"code": -32602}}
    return [
        call(minutes, "end_session", tester, mcp, rpc_id, session_id),
        http_call(minutes, "end_session", arguments, mcp=mcp, rpc_id=rpc_id, **outcome),
    ]


def profile(minutes: float, session_id: str, mcp: str = "m1") -> list[dict[str, Any]]:
    tool_line = issued(minutes, session_id) | {"mcp_session_id": mcp, "rpc_id": "1"}
    return [
        call(minutes, "get_profile", "author-free", mcp, 1),
        tool_line,
        http_call(minutes, "get_profile", {}, result={}, mcp=mcp, rpc_id=1),
    ]


def too_large(minutes: float, mcp: str | None = "m1") -> dict[str, Any]:
    return {
        "kind": "http",
        "ts_in": at(minutes),
        "http": {"method": "POST", "path": "/mcp", "status": 413, "mcp_session_id": mcp},
        "rpc": {"id": None, "method": None, "tool": None, "params_raw": None},
    }


def scored(runs: list[Run], records: list[dict[str, Any]], schema: dict[str, Any]) -> Any:
    grouped = assign(runs, records)
    return {r.run_id: score_run(r, grouped[r.run_id], schema) for r in runs}


def test_outsider_and_unauthenticated_end_session_do_not_affect_the_run(
    end_session_schema: dict[str, Any],
) -> None:
    outsider = end_call(14.2, {**args(), "glossary": []}, mcp="m9", tester=None, valid=False)
    unauthenticated = http_call(14.4, "end_session", {"bogus": 1}, status=401, mcp=None)
    records = [*profile(1, "sid-1"), *end_call(14, args()), *outsider, unauthenticated]
    result = scored([run("r01", 0, 15)], records, end_session_schema)["r01"]
    assert (result.end_calls, result.valid_first, result.valid_final) == (1, True, True)


def test_outsider_get_profile_alone_does_not_make_tools_fire(
    end_session_schema: dict[str, Any],
) -> None:
    records = [
        call(3, "get_profile", None, "m9", 1),
        http_call(3, "get_profile", {}, result={}, mcp="m9", rpc_id=1),
    ]
    grouped = assign([run("r01", 0, 15)], records)
    assert grouped == {"r01": []}
    assert unassigned_records(records, grouped) == records


def test_invalid_final_call_in_a_new_mcp_session_makes_the_run_invalid(
    end_session_schema: dict[str, Any],
) -> None:
    retry = end_call(14.5, args(task_result="done"), mcp="m2", valid=False)
    records = [*profile(1, "sid-1"), *end_call(14, args()), *retry]
    result = scored([run("r01", 0, 15)], records, end_session_schema)["r01"]
    assert (result.end_calls, result.valid_first, result.valid_final) == (2, True, False)


def test_oversized_final_attempt_counts_as_an_invalid_end_session(
    end_session_schema: dict[str, Any],
) -> None:
    records = [*profile(1, "sid-1"), *end_call(14, args()), too_large(14.5)]
    result = scored([run("r01", 0, 15)], records, end_session_schema)["r01"]
    assert (result.end_calls, result.valid_first, result.valid_final) == (2, True, False)


def test_end_session_after_the_window_goes_to_the_run_that_issued_its_session(
    end_session_schema: dict[str, Any],
) -> None:
    late = end_call(27, args(), mcp="m1", rpc_id=5)
    runs = [run("r01", 0, 15), run("r02", 26, 41)]
    records = [*profile(1, "sid-1"), *late]
    outcomes = scored(runs, records, end_session_schema)
    assert (outcomes["r01"].end_calls, outcomes["r01"].valid_final) == (1, True)
    assert outcomes["r02"].end_calls == 0
    assert unassigned_records(records, assign(runs, records)) == []


def test_stranger_oversized_post_without_session_id_does_not_invalidate_the_run(
    end_session_schema: dict[str, Any],
) -> None:
    records = [*profile(1, "sid-1"), *end_call(14, args()), too_large(14.5, mcp=None)]
    result = scored([run("r01", 0, 15)], records, end_session_schema)["r01"]
    assert (result.end_calls, result.valid_final) == (1, True)


def test_oversized_post_with_another_testers_mcp_session_does_not_count(
    end_session_schema: dict[str, Any],
) -> None:
    other = call(5, "get_profile", "author-pro", "m7", 1)
    records = [*profile(1, "sid-1"), *end_call(14, args()), other, too_large(14.5, mcp="m7")]
    result = scored([run("r01", 0, 15)], records, end_session_schema)["r01"]
    assert (result.end_calls, result.valid_final) == (1, True)


def test_oversized_post_with_the_run_testers_mcp_session_is_a_failed_final_attempt(
    end_session_schema: dict[str, Any],
) -> None:
    records = [*profile(1, "sid-1"), *end_call(14, args()), too_large(14.5, mcp="m1")]
    result = scored([run("r01", 0, 15)], records, end_session_schema)["r01"]
    assert (result.end_calls, result.valid_final) == (2, False)


def test_uncounted_too_large_lists_only_oversized_posts_no_run_counted(
    end_session_schema: dict[str, Any],
) -> None:
    stranger, own = too_large(14.5, mcp=None), too_large(14.6, mcp="m1")
    runs = [run("r01", 0, 15)]
    records = [*profile(1, "sid-1"), *end_call(14, args()), stranger, own]
    assert uncounted_too_large(runs, records, assign(runs, records)) == [stranger]
