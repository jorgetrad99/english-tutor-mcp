"""Build-time check (Dockerfile): the installed `tutor` package carries its non-Python data.

Run with the venv's python after `uv sync --no-editable`; exits 1 naming what is missing.
"""

import sys
from pathlib import Path

import tutor

ROOT = Path(tutor.__file__).resolve().parent
# glob (relative to the package) -> minimum number of files
EXPECTED = {
    "web/locale/*/LC_MESSAGES/*.po": 1,
    "web/templates/**/*.html": 10,
    "web/static/**/*.*": 5,
    "content/*.yaml": 1,
}


def main() -> int:
    missing = [
        f"{pattern} (need {need})"
        for pattern, need in EXPECTED.items()
        if sum(p.is_file() for p in ROOT.glob(pattern)) < need
    ]
    if missing:
        print("package data missing: " + "; ".join(missing), file=sys.stderr)
        return 1
    print("package data ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
