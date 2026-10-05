"""Print a transcript's learner turns, numbered, for blind error annotation.

Usage (from evals/): uv run python -m fidelity.annotate raw/<name>.md
Only the learner's messages are shown, so the tutor's feedback cannot steer the annotation.
"""

from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Sequence
from pathlib import Path

from fidelity.metrics import user_messages


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path)
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper) and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    text = args.transcript.read_text(encoding="utf-8")
    for number, message in enumerate(user_messages(text), 1):
        print(f"{number}. {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
