"""Experiment 01 and 02 scoring and verdicts (spec 8.1-8.3, 9.1-9.2)."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tutor_spike.analysis.runs import Run
from tutor_spike.normalize import said_in_turns

GRACE_BEFORE = timedelta(minutes=2)
GRACE_AFTER = timedelta(minutes=10)
VOICE_RUNS = 5
TEXT_AND_VOICE_RUNS = 25

Record = dict[str, Any]


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    tools_fired: bool
    end_calls: int
    valid_first: bool
    valid_final: bool
    said_total: int
    said_pass: int
    cefr: str | None
    chunks_fabricated: int
    final_arguments: Any


@dataclass(frozen=True)
class Exp1Summary:
    passes: int
    total: int
    by_account: dict[str, tuple[int, int]]
    verdict: str


@dataclass(frozen=True)
class Exp2Summary:
    n: int
    valid_final: int
    valid_first: int
    said_pass: int
    said_total: int
    verdict: str


def load_records(directory: Path) -> list[Record]:
    records: list[Record] = []
    for path in sorted(directory.glob("calls-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def _ts(record: Record) -> datetime:
    return datetime.fromisoformat(record.get("ts_in") or record["ts"])


def assign(runs: list[Run], records: list[Record]) -> dict[str, list[Record]]:
    """Each record goes to the latest-started run whose grace window contains it."""
    ordered = sorted(runs, key=lambda r: r.start)
    grouped: dict[str, list[Record]] = {r.run_id: [] for r in runs}
    for record in sorted(records, key=_ts):
        ts = _ts(record)
        contained = [r for r in ordered if r.start - GRACE_BEFORE <= ts <= r.end + GRACE_AFTER]
        if contained:
            grouped[contained[-1].run_id].append(record)
    return grouped


def end_session_schema_from(records: list[Record]) -> dict[str, Any]:
    """inputSchema of end_session from the last logged tools/list response."""
    schema: dict[str, Any] | None = None
    for record in sorted(records, key=_ts):
        rpc = record.get("rpc") or {}
        if record.get("kind") == "http" and rpc.get("method") == "tools/list":
            for tool in (rpc.get("result_raw") or {}).get("tools", []):
                if tool.get("name") == "end_session":
                    schema = tool["inputSchema"]
    if schema is None:
        raise SystemExit("No tools/list response with end_session in the log.")
    return schema


def payload_valid(arguments: Any, schema: dict[str, Any], issued: set[str]) -> bool:
    if not isinstance(arguments, dict):
        return False
    if next(Draft202012Validator(schema).iter_errors(arguments), None) is not None:
        return False
    return bool(arguments.get("user_turns")) and arguments.get("session_id") in issued


def _tool_calls(records: list[Record]) -> list[Record]:
    calls = [
        r
        for r in records
        if r.get("kind") == "http" and (r.get("rpc") or {}).get("method") == "tools/call"
    ]
    return sorted(calls, key=_ts)


def _arguments(call: Record) -> Any:
    params = call["rpc"].get("params_raw") or {}
    return params.get("arguments") if isinstance(params, dict) else None


def score_run(run: Run, records: list[Record], schema: dict[str, Any]) -> RunOutcome:
    issued = {
        r["session_id"]
        for r in records
        if r.get("kind") == "tool"
        and r.get("tool") == "get_profile"
        and r.get("tester") == run.tester
    }
    calls = _tool_calls(records)
    ends = [_arguments(c) for c in calls if c["rpc"].get("tool") == "end_session"]
    validity = [payload_valid(a, schema, issued) for a in ends]
    final = ends[-1] if ends else None
    valid_final = bool(validity) and validity[-1]
    said_total = said_pass = chunks = 0
    cefr: str | None = None
    if valid_final:
        if not isinstance(final, dict):
            raise TypeError("final payload is not an object")
        said_total = len(final["errors"])
        said_pass = sum(said_in_turns(e["said"], final["user_turns"]) for e in final["errors"])
        cefr = final["cefr_estimate"]["speaking"]
        chunks = len(final["chunks_used"])
    fired = any(
        c["http"]["status"] == 200
        and (c["rpc"].get("result_raw") is not None or c["rpc"].get("error_raw") is not None)
        for c in calls
    )
    return RunOutcome(
        run_id=run.run_id,
        tools_fired=fired,
        end_calls=len(ends),
        valid_first=bool(validity) and validity[0],
        valid_final=valid_final,
        said_total=said_total,
        said_pass=said_pass,
        cefr=cefr,
        chunks_fabricated=chunks,
        final_arguments=final,
    )


def voice_pass(run: Run, outcome: RunOutcome) -> bool:
    return (
        outcome.tools_fired and run.voice_stayed_active == "y" and run.continued_with_result == "y"
    )


def summarize_exp1(runs: list[Run], outcomes: dict[str, RunOutcome]) -> Exp1Summary:
    voice = sorted(
        (r for r in runs if r.mode == "voice" and r.status == "ok"), key=lambda r: r.start
    )[:VOICE_RUNS]
    by_account: dict[str, tuple[int, int]] = {}
    for r in voice:
        passed, total = by_account.get(r.account, (0, 0))
        by_account[r.account] = (passed + voice_pass(r, outcomes[r.run_id]), total + 1)
    passes = sum(p for p, _ in by_account.values())
    verdict = "INCOMPLETE" if len(voice) < VOICE_RUNS else "PASS" if passes >= 4 else "FAIL"
    return Exp1Summary(passes=passes, total=len(voice), by_account=by_account, verdict=verdict)


def summarize_exp2(runs: list[Run], outcomes: dict[str, RunOutcome]) -> Exp2Summary:
    ok = sorted((r for r in runs if r.status == "ok"), key=lambda r: r.start)
    ok = ok[:TEXT_AND_VOICE_RUNS]
    scored = [outcomes[r.run_id] for r in ok]
    n = len(scored)
    valid_final = sum(o.valid_final for o in scored)
    said_pass = sum(o.said_pass for o in scored)
    said_total = sum(o.said_total for o in scored)
    said_ok = said_total == 0 or said_pass / said_total >= 0.8
    if n < TEXT_AND_VOICE_RUNS:
        verdict = "INCOMPLETE"
    elif valid_final / n < 0.7:
        verdict = "EXTEND"
    elif valid_final / n >= 0.9 and said_ok:
        verdict = "PASS"
    else:
        verdict = "FAIL_WITH_FALLBACK"
    return Exp2Summary(
        n=n,
        valid_final=valid_final,
        valid_first=sum(o.valid_first for o in scored),
        said_pass=said_pass,
        said_total=said_total,
        verdict=verdict,
    )
