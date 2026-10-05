"""Validation for the admin settings table (requirements section 14)."""

from __future__ import annotations

import math
import re

from tutor.domain.dashboard.glossary import TextError, clean_user_text
from tutor.domain.dashboard.types import SettingType

_INT = re.compile(r"-?\d+")
_FLOAT = re.compile(r"-?\d+(\.\d+)?")
_TRUE = {"true", "1", "si", "sí", "yes"}
_FALSE = {"false", "0", "no"}
_MAX_RAW_LEN = 30
STR_MAX = 200


class SettingError(ValueError):
    pass


def parse_setting(kind: SettingType, raw: str) -> str:
    value = raw.strip()
    match kind:
        case SettingType.INT:
            if len(value) > _MAX_RAW_LEN or not _INT.fullmatch(value):
                raise SettingError(kind)
            try:
                return str(int(value))
            except ValueError as exc:
                raise SettingError(kind) from exc
        case SettingType.FLOAT:
            if len(value) > _MAX_RAW_LEN or not _FLOAT.fullmatch(value):
                raise SettingError(kind)
            try:
                parsed = float(value)
                if not math.isfinite(parsed):
                    raise SettingError(kind)
                return str(parsed)
            except ValueError as exc:
                raise SettingError(kind) from exc
        case SettingType.BOOL:
            lowered = value.casefold()
            if lowered in _TRUE:
                return "true"
            if lowered in _FALSE:
                return "false"
            raise SettingError(kind)
        case SettingType.STR:
            try:
                return clean_user_text(raw, max_len=STR_MAX, required=False)
            except TextError as exc:
                raise SettingError(kind) from exc
