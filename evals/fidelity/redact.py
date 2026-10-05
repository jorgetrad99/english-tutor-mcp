"""Redact names and emails in fixture files before they are committed.

Usage (from evals/): uv run python -m fidelity.redact --names "Ana,Beto" FILE [FILE ...]
`.json` files are redacted value by value (keys untouched); other files as text. In place.
Raw sessions (real learner text) stay git-ignored under evals/raw/; commit only redacted copies.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def _fold(text: str) -> str:
    """Accents folded: NFD, combining marks dropped, NFC (accented i becomes plain i)."""
    decomposed = unicodedata.normalize("NFD", text)
    kept = "".join(c for c in decomposed if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", kept)


def _folded_with_origin(text: str) -> tuple[str, list[int]]:
    """Folded text plus, for each folded character, the index of its source character."""
    folded: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        part = _fold(char)
        folded.append(part)
        origin += [index] * len(part)
    return "".join(folded), origin


def redact(text: str, names: Sequence[str]) -> str:
    text = _EMAIL.sub("[email]", unicodedata.normalize("NFC", text))
    folded_names = {_fold(n.strip()) for n in names if n.strip()}
    for name in sorted((n for n in folded_names if n), key=len, reverse=True):
        # Matched on accent-folded text, so accented and plain spellings redact each other. Not
        # preceded or followed by a letter; digits and underscores do not protect a name.
        pattern = rf"(?<![^\W\d_]){re.escape(name)}(?![^\W\d_])"
        folded, origin = _folded_with_origin(text)
        spans = [
            (origin[m.start()], origin[m.end() - 1] + 1)
            for m in re.finditer(pattern, folded, flags=re.IGNORECASE)
        ]
        for start, end in reversed(spans):
            text = text[:start] + "[name]" + text[end:]
    return text


def redact_json(value: Any, names: Sequence[str]) -> Any:
    """Redact every string value (keys untouched), so escapes cannot hide a name."""
    if isinstance(value, str):
        return redact(value, names)
    if isinstance(value, list):
        return [redact_json(v, names) for v in value]
    if isinstance(value, dict):
        return {k: redact_json(v, names) for k, v in value.items()}
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", required=True, help="Comma-separated names to redact")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if not names:
        raise SystemExit("list at least one name in --names")
    for path in args.files:
        raw = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            redacted = json.dumps(redact_json(json.loads(raw), names), indent=2, ensure_ascii=False)
        else:
            redacted = redact(raw, names)
        path.write_text(redacted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
