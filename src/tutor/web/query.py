"""Lenient query-string parsing: a bad value falls back instead of breaking the page."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

_MAX_DIGITS = 9  # int() refuses very long digit strings; nothing here needs more


def enum_or_none[E: StrEnum](cls: type[E], raw: str | None) -> E | None:
    if not raw:
        return None
    try:
        return cls(raw)
    except ValueError:
        return None


def bounded_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    text = (raw or "").strip()
    if not text.isdecimal() or len(text) > _MAX_DIGITS:
        return default
    return min(max(int(text), minimum), maximum)


def parse_uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None
