"""Copy redacted spike evidence into docs/spike/NN-data and evals/fixtures (spec §6.4).
Usage (from spike/): uv run python -m tutor_spike.redact --names "Name1,Name2"
"""

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tutor_spike.analysis.runs import load_runs, transcript_path
from tutor_spike.analysis.scoring import (
    assign,
    end_session_schema_from,
    load_records,
    score_run,
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def redact(text: str, names: list[str]) -> str:
    text = _EMAIL.sub("[email]", unicodedata.normalize("NFC", text))
    cleaned = (unicodedata.normalize("NFC", n.strip()) for n in names if n.strip())
    for name in sorted(cleaned, key=len, reverse=True):
        # Not preceded/followed by a letter; digits and underscores do not protect a name.
        pattern = rf"(?<![^\W\d_]){re.escape(name)}(?![^\W\d_])"
        text = re.sub(pattern, "[name]", text, flags=re.IGNORECASE)
    return text


def _redact_json(value: Any, names: list[str]) -> Any:
    """Redact every string value (keys untouched), so escapes cannot hide a name."""
    if isinstance(value, str):
        return redact(value, names)
    if isinstance(value, list):
        return [_redact_json(v, names) for v in value]
    if isinstance(value, dict):
        return {k: _redact_json(v, names) for k, v in value.items()}
    return value


def _redact_json_text(raw: str, names: list[str]) -> str:
    return json.dumps(_redact_json(json.loads(raw), names), indent=2, ensure_ascii=False)


def _write(path: Path, text: str, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    written.append(path)


def export(data_dir: Path, repo_root: Path, names: list[str], tz: ZoneInfo) -> list[Path]:
    runs = load_runs(data_dir / "runs.csv", tz)
    records = load_records(data_dir)
    schema, _ = end_session_schema_from(records)
    grouped = assign(runs, records)
    written: list[Path] = []
    spike_docs = repo_root / "docs" / "spike"
    fixtures = repo_root / "evals" / "fixtures"
    run_sheet = (data_dir / "runs.csv").read_text(encoding="utf-8")
    _write(spike_docs / "02-data" / "runs.csv", redact(run_sheet, names), written)
    for run in (r for r in runs if r.status == "ok"):
        outcome = score_run(run, grouped[run.run_id], schema)
        basename = f"{run.date}-claude-{run.account}-{run.run_id}"
        payload = None
        if outcome.valid_final:
            payload = _redact_json_text(json.dumps(outcome.final_arguments), names)
            _write(spike_docs / "02-data" / "payloads" / f"{run.run_id}.json", payload, written)
        transcript = transcript_path(data_dir, run)
        if run.mode != "text" or transcript is None or not transcript.exists():
            continue
        text = redact(transcript.read_text(encoding="utf-8"), names)
        _write(spike_docs / "03-data" / "transcripts" / f"{run.run_id}.md", text, written)
        _write(fixtures / "transcripts" / f"{basename}.md", text, written)
        if payload is not None:
            _write(fixtures / "transcripts" / f"{basename}.payload.json", payload, written)
        annotation = data_dir / "annotations" / f"{run.run_id}.json"
        if annotation.exists():
            notes = _redact_json_text(annotation.read_text(encoding="utf-8"), names)
            _write(spike_docs / "03-data" / "annotations" / f"{run.run_id}.json", notes, written)
            _write(fixtures / "annotations" / f"{basename}.json", notes, written)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--repo", type=Path, default=Path(".."))
    parser.add_argument("--names", required=True, help="Comma-separated names to redact")
    parser.add_argument("--tz", default="America/Mexico_City")
    args = parser.parse_args()
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if not names:
        raise SystemExit("--names must list at least one name")
    for path in export(args.data, args.repo, names, ZoneInfo(args.tz)):
        print(path)


if __name__ == "__main__":
    main()
