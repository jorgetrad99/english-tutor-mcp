"""Glossary editing rules, filters and CSV export (requirements section 10)."""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import Sequence
from datetime import date, timedelta

from tutor.domain.dashboard.types import DueFilter, GlossaryFilter, GlossaryRow

MEANING_MAX = 200
CONTEXT_MAX = 300
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_CSV_HEADER = (
    "text",
    "kind",
    "meaning",
    "context_sentence",
    "domain",
    "status",
    "due_on",
    "leech",
)


class TextError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def clean_user_text(value: str, *, max_len: int, required: bool) -> str:
    """Normalise learner-typed text. It stays data: HTML is kept as literal text."""
    text = unicodedata.normalize("NFC", value)
    text = "".join(" " if ch.isspace() else ch for ch in text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf"))
    text = " ".join(text.split())
    if required and not text:
        raise TextError("empty")
    if len(text) > max_len:
        raise TextError("too_long")
    return text


def fold(text: str) -> str:
    """Case- and accent-insensitive form for search."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def filter_glossary(
    rows: Sequence[GlossaryRow], f: GlossaryFilter, today: date
) -> tuple[GlossaryRow, ...]:
    needle = fold(f.q.strip())
    horizon = today if f.due is DueFilter.TODAY else today + timedelta(days=6)

    def keep(r: GlossaryRow) -> bool:
        if f.kind is not None and r.kind is not f.kind:
            return False
        if f.domain is not None and r.domain != f.domain:
            return False
        if f.status is not None and r.status is not f.status:
            return False
        if f.due is not None and (r.due_on is None or r.due_on > horizon):
            return False
        return not needle or needle in fold(f"{r.text} {r.meaning} {r.context_sentence}")

    def order(r: GlossaryRow) -> tuple[bool, date, str]:
        return (r.due_on is None, r.due_on or date.max, fold(r.text))

    return tuple(sorted(filter(keep, rows), key=order))


def _cell(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def glossary_csv(rows: Sequence[GlossaryRow]) -> str:
    """UTF-8 CSV with a BOM so Excel shows accents; formula-like cells are neutralised."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADER)
    for r in rows:
        writer.writerow(
            [
                _cell(r.text),
                r.kind.value,
                _cell(r.meaning),
                _cell(r.context_sentence),
                r.domain,
                r.status.value,
                r.due_on.isoformat() if r.due_on else "",
                "true" if r.leech else "false",
            ]
        )
    return "﻿" + buf.getvalue()
