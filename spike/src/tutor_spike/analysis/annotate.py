"""Print one run's learner turns, numbered, for blind error annotation (spec §8.4).
Usage (from spike/): uv run python -m tutor_spike.analysis.annotate <run_id> [--data data/raw]
Only the learner's messages are shown, so Claude's feedback cannot steer the annotation.
"""

import argparse
import io
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from tutor_spike.analysis.fidelity import user_messages
from tutor_spike.analysis.runs import load_runs, transcript_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    runs = {r.run_id: r for r in load_runs(args.data / "runs.csv", ZoneInfo("UTC"))}
    run = runs.get(args.run_id)
    if run is None:
        raise SystemExit(f"run {args.run_id} is not in {args.data / 'runs.csv'}")
    transcript = transcript_path(args.data, run)
    if transcript is None or not transcript.exists():
        raise SystemExit(f"run {run.run_id} has no transcript file")
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    for number, message in enumerate(user_messages(transcript.read_text(encoding="utf-8")), 1):
        print(f"{number}. {message}")


if __name__ == "__main__":
    main()
