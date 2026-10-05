"""Redact names, emails, URLs, @handles and phone numbers in raw fixture files.

Usage (from evals/):
    uv run python -m fidelity.redact --names "Ana,Beto" --out-dir DIR FILE [FILE ...]
Inputs must live under evals/raw/ (git-ignored); the redacted copies are written to --out-dir
under the same file name and never replace the input. `.json` files are redacted value by value
(keys untouched); other files as text.

Redaction is best effort: a pattern list cannot know every way a person is identifiable. A human
must read each redacted file before it is committed.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

RAW_DIR = Path(__file__).resolve().parents[1] / "raw"
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\(?\d[\d\s().-]{7,}\d")
_HANDLE = re.compile(r"(?<![\w@])@\w{2,}")


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
    text = unicodedata.normalize("NFC", text)
    text = _URL.sub("[url]", text)
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    text = _HANDLE.sub("[handle]", text)
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


def _inside_raw(path: Path) -> bool:
    return path.resolve().is_relative_to(RAW_DIR.resolve())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", required=True, help="Comma-separated names to redact")
    parser.add_argument("--out-dir", required=True, type=Path, help="Where the copies go")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if not names:
        raise SystemExit("list at least one name in --names")
    outside = [str(p) for p in args.files if not _inside_raw(p)]
    if outside:
        raise SystemExit(f"inputs must live under evals/raw/: {', '.join(outside)}")
    if _inside_raw(args.out_dir):
        raise SystemExit("--out-dir must not be under evals/raw/")
    targets = [args.out_dir / path.name for path in args.files]
    taken = [str(t) for t in targets if t.exists()]
    if taken:
        raise SystemExit(f"refusing to overwrite: {', '.join(taken)}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for path, target in zip(args.files, targets, strict=True):
        raw = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            redacted = json.dumps(redact_json(json.loads(raw), names), indent=2, ensure_ascii=False)
        else:
            redacted = redact(raw, names)
        target.write_text(redacted, encoding="utf-8")
    print("Redaction is best effort: read every output file before you commit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
