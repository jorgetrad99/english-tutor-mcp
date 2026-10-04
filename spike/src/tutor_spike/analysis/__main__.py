"""Print markdown tables for docs/spike/01-04 (spec §6.4). Usage:
(from spike/) uv run python -m tutor_spike.analysis --data data/raw --tz America/Mexico_City
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from tutor_spike.analysis.fidelity import (
    Match,
    cefr_stats,
    errors_fidelity,
    turns_fidelity,
    user_messages,
)
from tutor_spike.analysis.runs import Run, load_runs
from tutor_spike.analysis.scoring import (
    RunOutcome,
    assign,
    end_session_schema_from,
    load_records,
    score_run,
    summarize_exp1,
    summarize_exp2,
    voice_pass,
)

HEAD = "| Run | Date | Client / plan / mode | Outcome | Evidence (file, screenshot, log line) |"
RULE = "| --- | --- | --- | --- | --- |"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _client(run: Run) -> str:
    return f"{'mobile' if run.mode == 'voice' else 'web'} / {run.account} / {run.mode}"


def _exp2_cell(o: RunOutcome) -> str:
    validity = "valid (final)" if o.valid_final else "invalid"
    return f"{validity}, {o.end_calls} call(s), said {o.said_pass}/{o.said_total}"


def render_report(data_dir: Path, tz: ZoneInfo) -> str:
    runs = load_runs(data_dir / "runs.csv", tz)
    records = load_records(data_dir)
    schema = end_session_schema_from(records)
    grouped = assign(runs, records)
    outcomes = {r.run_id: score_run(r, grouped[r.run_id], schema) for r in runs}
    ok_runs = [r for r in runs if r.status == "ok"]
    out: list[str] = []

    exp1 = summarize_exp1(runs, outcomes)
    out += ["## 01 — Voice-mode tool calls", "", HEAD, RULE]
    for r in (r for r in runs if r.mode == "voice"):
        o = outcomes[r.run_id]
        result = "aborted" if r.status != "ok" else ("pass" if voice_pass(r, o) else "fail")
        evidence = f"tools fired: {o.tools_fired}; recording; log {r.date}"
        out.append(f"| {r.run_id} | {r.date} | {_client(r)} | {result} | {evidence} |")
    out += [
        "",
        f"Passes: {exp1.passes}/{exp1.total}; by account: {exp1.by_account}.",
        f"Verdict: {exp1.verdict}",
        "",
    ]

    exp2 = summarize_exp2(runs, outcomes)
    out += ["## 02 — end_session reliability", "", HEAD, RULE]
    for r in runs:
        cell = "aborted" if r.status != "ok" else _exp2_cell(outcomes[r.run_id])
        out.append(f"| {r.run_id} | {r.date} | {_client(r)} | {cell} | log {r.date} |")
    said_rate = exp2.said_pass / exp2.said_total if exp2.said_total else None
    out += [
        "",
        f"Valid by final call: {exp2.valid_final}/{exp2.n}; valid on first call: "
        f"{exp2.valid_first}/{exp2.n}; said passing: {exp2.said_pass}/{exp2.said_total} "
        f"({_pct(said_rate)}).",
        f"Verdict: {exp2.verdict}",
        "",
    ]

    strict, fuzzy, errors = Match(0, 0, 0, 0), Match(0, 0, 0, 0), Match(0, 0, 0, 0)
    out += ["## 03 — Evidence fidelity", "", HEAD, RULE]
    for r in ok_runs:
        o = outcomes[r.run_id]
        transcript = data_dir / r.transcript_file if r.transcript_file else None
        if not o.valid_final or transcript is None or not transcript.exists():
            continue
        messages = user_messages(transcript.read_text(encoding="utf-8"))
        payload = o.final_arguments
        s = turns_fidelity(payload["user_turns"], messages)
        f = turns_fidelity(payload["user_turns"], messages, fuzzy=True)
        strict, fuzzy = strict + s, fuzzy + f
        cell = f"turns R {_pct(s.recall)} / P {_pct(s.precision)}"
        annotation = data_dir / "annotations" / f"{r.run_id}.json"
        if annotation.exists():
            annotated = [e["said"] for e in json.loads(annotation.read_text(encoding="utf-8"))]
            e = errors_fidelity([x["said"] for x in payload["errors"]], annotated)
            errors = errors + e
            cell += f"; errors R {_pct(e.recall)} / P {_pct(e.precision)}"
        out.append(f"| {r.run_id} | {r.date} | {_client(r)} | {cell} | {r.transcript_file} |")
    fabricated = sum(outcomes[r.run_id].chunks_fabricated for r in ok_runs)
    out += [
        "",
        f"user_turns strict: R {_pct(strict.recall)} / P {_pct(strict.precision)} "
        "(thresholds ≥ 90% / ≥ 90%); "
        f"fuzzy: R {_pct(fuzzy.recall)} / P {_pct(fuzzy.precision)}.",
        f"errors: R {_pct(errors.recall)} / P {_pct(errors.precision)} (thresholds ≥ 70% / ≥ 80%).",
        f"chunks_used: not measurable; fabricated chunk IDs across runs: {fabricated}.",
        "",
    ]

    out += [
        "## 04 — Grader reliability",
        "",
        "| Group | n | Mode | Range | SD (half-steps) | Within ±1 of mode | Full-level jumps |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    groups: dict[str, list[str]] = defaultdict(list)
    for r in sorted(ok_runs, key=lambda r: r.start):
        level = outcomes[r.run_id].cefr
        if level is None:
            continue
        for key in ("all", f"account={r.account}", f"mode={r.mode}", f"model={r.model_shown}"):
            groups[key].append(level)
    for key, levels in groups.items():
        stats = cefr_stats(levels)
        if stats is None:
            continue
        out.append(
            f"| {key} | {stats.n} | {stats.mode} | {stats.low}–{stats.high} | "
            f"{stats.sd_half_steps:.2f} | {stats.within_one_of_mode:.0%} | "
            f"{stats.full_level_jumps} |"
        )
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--tz", default="America/Mexico_City")
    args = parser.parse_args()
    print(render_report(args.data, ZoneInfo(args.tz)))


if __name__ == "__main__":
    main()
