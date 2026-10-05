"""Evidence fidelity over the annotated v0 text sessions in evals/fixtures.

Layout: transcripts/<name>.md (redacted "U:"/"A:" transcript), transcripts/<name>.payload.json
(the session's sessions.raw_evidence), annotations/<name>.json (either the spike's list of
{"said": ...} or {"errors": [{"said": ...}], "chunks_used": ["it-07-c3", ...]}).
Usage (from evals/): uv run python -m fidelity.report [--fixtures fixtures]
Exit code 1 when a section 11 threshold is missed.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fidelity.metrics import (
    Match,
    chunks_fidelity,
    errors_fidelity,
    turns_fidelity,
    user_messages,
)

# Requirements section 11: (recall, precision); None means not required.
THRESHOLDS: dict[str, tuple[float | None, float | None]] = {
    "user_turns": (0.9, 0.9),
    "errors": (0.7, 0.8),
    "chunks_used": (0.8, None),
}


@dataclass(frozen=True)
class SessionFidelity:
    name: str
    turns: Match
    turns_fuzzy: Match
    errors: Match
    chunks: Match | None


def measure(
    name: str, transcript: str, payload: Mapping[str, Any], annotation: Any
) -> SessionFidelity:
    turns = user_messages(transcript)
    payload_turns = [str(t) for t in payload.get("user_turns", [])]
    said = [str(e["said"]) for e in payload.get("errors", [])]
    if isinstance(annotation, list):
        annotated_errors, annotated_chunks = annotation, None
    else:
        annotated_errors = annotation.get("errors", [])
        annotated_chunks = annotation.get("chunks_used")
    chunks = None
    if annotated_chunks is not None:
        chunks = chunks_fidelity(
            [str(c) for c in payload.get("chunks_used", [])], [str(c) for c in annotated_chunks]
        )
    return SessionFidelity(
        name=name,
        turns=turns_fidelity(payload_turns, turns),
        turns_fuzzy=turns_fidelity(payload_turns, turns, fuzzy=True),
        errors=errors_fidelity(said, [str(e["said"]) for e in annotated_errors]),
        chunks=chunks,
    )


def load_sessions(fixtures: Path) -> list[SessionFidelity]:
    """Every transcript that has both a payload and an annotation."""
    out: list[SessionFidelity] = []
    for transcript in sorted((fixtures / "transcripts").glob("*.md")):
        payload = transcript.with_name(f"{transcript.stem}.payload.json")
        annotation = fixtures / "annotations" / f"{transcript.stem}.json"
        if not payload.exists() or not annotation.exists():
            continue
        out.append(
            measure(
                transcript.stem,
                transcript.read_text(encoding="utf-8"),
                json.loads(payload.read_text(encoding="utf-8")),
                json.loads(annotation.read_text(encoding="utf-8")),
            )
        )
    return out


def totals(sessions: Sequence[SessionFidelity]) -> dict[str, Match]:
    """Micro-averages; chunks only over sessions whose annotation lists chunks."""
    empty = Match(0, 0, 0, 0)
    result = {"user_turns": empty, "errors": empty, "chunks_used": empty}
    for s in sessions:
        result["user_turns"] = result["user_turns"] + s.turns
        result["errors"] = result["errors"] + s.errors
        if s.chunks is not None:
            result["chunks_used"] = result["chunks_used"] + s.chunks
    return result


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def failures(total: Mapping[str, Match]) -> list[str]:
    problems: list[str] = []
    for field, (min_recall, min_precision) in THRESHOLDS.items():
        match = total[field]
        for label, value, floor in (
            ("recall", match.recall, min_recall),
            ("precision", match.precision, min_precision),
        ):
            if floor is not None and value is not None and value < floor:
                problems.append(f"{field} {label} {_pct(value)} < {_pct(floor)}")
    return problems


def render(sessions: Sequence[SessionFidelity]) -> str:
    lines = [
        "| Session | Turns R / P | Turns fuzzy R / P | Errors R / P | Chunks R / P |",
        "| --- | --- | --- | --- | --- |",
    ]

    def rp(m: Match | None) -> str:
        return "n/a" if m is None else f"{_pct(m.recall)} / {_pct(m.precision)}"

    for s in sessions:
        lines.append(
            f"| {s.name} | {rp(s.turns)} | {rp(s.turns_fuzzy)} | {rp(s.errors)} | {rp(s.chunks)} |"
        )
    total = totals(sessions)
    fuzzy = Match(0, 0, 0, 0)
    for s in sessions:
        fuzzy = fuzzy + s.turns_fuzzy
    lines.append(
        f"| All | {rp(total['user_turns'])} | {rp(fuzzy)} | {rp(total['errors'])} "
        f"| {rp(total['chunks_used'])} |"
    )
    problems = failures(total)
    lines += ["", "Thresholds met." if not problems else "Below threshold: " + "; ".join(problems)]
    lines.append("Voice sessions have no transcript; their fidelity is assumed equal to text.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("fixtures"))
    args = parser.parse_args(argv)
    sessions = load_sessions(args.fixtures)
    if not sessions:
        print(f"No annotated sessions in {args.fixtures}.")
        return 0
    print(render(sessions))
    return 1 if failures(totals(sessions)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
