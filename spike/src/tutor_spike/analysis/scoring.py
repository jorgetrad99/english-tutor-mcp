"""Experiment 01 and 02 scoring and verdicts (spec 8.1-8.3, 9.1-9.2)."""

import asyncio
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tutor_spike.analysis.runs import Run
from tutor_spike.normalize import said_in_turns
from tutor_spike.tools import build_mcp

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


def call_key(record: Record) -> tuple[str, str] | None:
    """(MCP session id, JSON-RPC id) joining an HTTP line to its kind:"call"/"tool" records."""
    if record.get("kind") == "http":
        mcp_session_id = (record.get("http") or {}).get("mcp_session_id")
        rpc_id = (record.get("rpc") or {}).get("id")
    else:
        mcp_session_id, rpc_id = record.get("mcp_session_id"), record.get("rpc_id")
    if mcp_session_id is None or rpc_id is None:
        return None
    return str(mcp_session_id), str(rpc_id)


def _window_run(ordered: list[Run], ts: datetime) -> Run | None:
    """Latest-started run whose grace window contains ts."""
    contained = [r for r in ordered if r.start - GRACE_BEFORE <= ts <= r.end + GRACE_AFTER]
    return contained[-1] if contained else None


def _tester_window_run(ordered: list[Run], ts: datetime, tester: Any) -> Run | None:
    """Latest-started run of this tester whose grace window contains ts."""
    return _window_run([r for r in ordered if r.tester == tester], ts)


def assign(runs: list[Run], records: list[Record]) -> dict[str, list[Record]]:
    """Attribute records to runs.

    (i) get_profile tool lines and kind:"call" lines go to the latest-started run of their
    tester whose grace window contains them; (ii) an end_session call whose session_id
    argument was issued by get_profile goes to the run holding that get_profile, window or
    not; HTTP and tool lines joined to a call line follow it; (iii) everything else goes to
    the latest-started run whose grace window contains it.
    """
    ordered = sorted(runs, key=lambda r: r.start)
    issuer: dict[str, Run] = {}
    for record in records:
        if record.get("kind") == "tool" and record.get("tool") == "get_profile":
            owner = _tester_window_run(ordered, _ts(record), record.get("tester"))
            if owner is not None and isinstance(record.get("session_id"), str):
                issuer[record["session_id"]] = owner
    call_runs: dict[tuple[str, str], Run | None] = {}
    by_call: dict[int, Run | None] = {}
    for record in records:
        if record.get("kind") != "call":
            continue
        owner = None
        if record.get("tool") == "end_session":
            owner = issuer.get(record.get("session_id") or "")
        if owner is None:
            owner = _tester_window_run(ordered, _ts(record), record.get("tester"))
        by_call[id(record)] = owner
        key = call_key(record)
        if key is not None:
            call_runs[key] = owner
    grouped: dict[str, list[Record]] = {r.run_id: [] for r in runs}
    for record in sorted(records, key=_ts):
        key = call_key(record)
        if record.get("kind") == "call":
            owner = by_call[id(record)]
        elif key is not None and key in call_runs:
            owner = call_runs[key]
        elif record.get("kind") == "tool" and record.get("tool") == "get_profile":
            owner = _tester_window_run(ordered, _ts(record), record.get("tester"))
        else:
            owner = _window_run(ordered, _ts(record))
        if owner is not None:
            grouped[owner.run_id].append(record)
    return grouped


def _is_tools_call(record: Record) -> bool:
    return record.get("kind") == "http" and (record.get("rpc") or {}).get("method") == "tools/call"


def unassigned_records(records: list[Record], grouped: dict[str, list[Record]]) -> list[Record]:
    """tools/call HTTP lines and kind:"call" lines that no run received, in time order."""
    assigned = {id(r) for run_records in grouped.values() for r in run_records}
    return [
        r
        for r in sorted(records, key=_ts)
        if (_is_tools_call(r) or r.get("kind") == "call") and id(r) not in assigned
    ]


def _advertised_end_session_schema() -> dict[str, Any]:
    """end_session inputSchema exactly as a freshly built spike server advertises it."""
    from fastmcp import Client

    from tutor_spike.eventlog import JsonlLog
    from tutor_spike.sessions import SessionRegistry

    async def fetch(directory: Path) -> dict[str, Any]:
        server = build_mcp(
            registry=SessionRegistry(directory / "sessions.jsonl", datetime.now),
            log=JsonlLog(directory, datetime.now),
            resolve_tester=lambda: None,
            now=datetime.now,
            display_name="schema",
        )
        async with Client(server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        return dict(tools["end_session"].input_schema)

    with tempfile.TemporaryDirectory() as directory:
        return asyncio.run(fetch(Path(directory)))


def end_session_schema_from(records: list[Record]) -> tuple[dict[str, Any], bool]:
    """(end_session inputSchema, True if from the log).

    Uses the last logged tools/list response; without one, the schema build_mcp advertises.
    """
    schema: dict[str, Any] | None = None
    for record in sorted(records, key=_ts):
        rpc = record.get("rpc") or {}
        if record.get("kind") == "http" and rpc.get("method") == "tools/list":
            for tool in (rpc.get("result_raw") or {}).get("tools", []):
                if tool.get("name") == "end_session":
                    schema = tool["inputSchema"]
    if schema is not None:
        return schema, True
    try:
        return _advertised_end_session_schema(), False
    except Exception as exc:
        raise ValueError(
            "No tools/list response with end_session in the log, and the build_mcp "
            f"fallback failed: {type(exc).__name__}"
        ) from exc


def payload_valid(arguments: Any, schema: dict[str, Any], issued: set[str]) -> bool:
    if not isinstance(arguments, dict):
        return False
    if next(Draft202012Validator(schema).iter_errors(arguments), None) is not None:
        return False
    return bool(arguments.get("user_turns")) and arguments.get("session_id") in issued


def _is_too_large_mcp_post(record: Record) -> bool:
    http = record.get("http") or {}
    return (
        record.get("kind") == "http"
        and http.get("status") == 413
        and http.get("method") == "POST"
        and str(http.get("path") or "").rstrip("/") == "/mcp"
    )


def _call_index(records: list[Record]) -> dict[tuple[str, str], Record]:
    return {k: r for r in records if r.get("kind") == "call" and (k := call_key(r)) is not None}


def _counts_for(run: Run, line: Record, calls: dict[tuple[str, str], Record]) -> bool:
    """401 lines never count; joined lines count for the call's tester; others in-window."""
    if (line.get("http") or {}).get("status") == 401:
        return False
    key = call_key(line)
    joined = calls.get(key) if key is not None else None
    return joined is None or joined.get("tester") == run.tester


def _end_attempts(run: Run, records: list[Record]) -> list[Record]:
    """HTTP lines of this run's end_session attempts, oversized (413) POSTs included."""
    calls = _call_index(records)
    attempts: list[Record] = []
    for line in sorted(records, key=_ts):
        if not _counts_for(run, line, calls):
            continue
        rpc = line.get("rpc") or {}
        if _is_tools_call(line) and rpc.get("tool") == "end_session":
            attempts.append(line)
        elif _is_too_large_mcp_post(line):
            key = call_key(line)
            joined = calls.get(key) if key is not None else None
            tool = joined.get("tool") if joined is not None else rpc.get("tool")
            if tool in (None, "end_session"):
                attempts.append(line)
    return attempts


def _fired(run: Run, records: list[Record]) -> bool:
    calls = _call_index(records)
    return any(
        _is_tools_call(c)
        and _counts_for(run, c, calls)
        and c["http"]["status"] == 200
        and (c["rpc"].get("result_raw") is not None or c["rpc"].get("error_raw") is not None)
        for c in records
    )


def _arguments(line: Record) -> Any:
    params = (line.get("rpc") or {}).get("params_raw") or {}
    return params.get("arguments") if isinstance(params, dict) else None


def score_run(run: Run, records: list[Record], schema: dict[str, Any]) -> RunOutcome:
    issued = {
        r["session_id"]
        for r in records
        if r.get("kind") == "tool"
        and r.get("tool") == "get_profile"
        and r.get("tester") == run.tester
    }
    ends = [_arguments(line) for line in _end_attempts(run, records)]
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
    fired = _fired(run, records)
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
