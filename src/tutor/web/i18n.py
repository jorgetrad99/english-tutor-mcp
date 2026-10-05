"""Language choice, translations and locale-aware formatting (requirements section 14)."""

from __future__ import annotations

import gettext
from datetime import date
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import Literal

from babel.dates import format_date
from babel.messages.extract import extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.numbers import format_currency, format_decimal, format_percent
from babel.support import Translations

from tutor.domain.dashboard.types import Lang, User

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
LOCALE_DIR = WEB_DIR / "locale"

_DATE_PATTERNS: dict[Lang, dict[str, str]] = {
    Lang.ES_MX: {"short": "d MMM", "long": "d 'de' MMMM 'de' y", "weekday": "EEEE d 'de' MMM"},
    Lang.EN: {"short": "MMM d", "long": "MMMM d, y", "weekday": "EEE, MMM d"},
}


def _po_path(lang: Lang) -> Path:
    return LOCALE_DIR / lang.value / "LC_MESSAGES" / "messages.po"


@cache
def load_translations(lang: Lang) -> gettext.NullTranslations:
    """Compile the .po in memory; es-MX is the source language and needs no catalog."""
    po = _po_path(lang)
    if not po.exists():
        return gettext.NullTranslations()
    with po.open("rb") as fh:
        catalog = read_po(fh, locale=lang.value)
    buf = BytesIO()
    write_mo(buf, catalog)
    buf.seek(0)
    return Translations(buf)


def negotiate(query: str | None, accept_language: str | None, user: User | None) -> Lang:
    if user is not None:
        return user.lang
    if query in ("es", "en"):
        return Lang.ES_MX if query == "es" else Lang.EN
    weighted: list[tuple[float, int, str]] = []
    for i, part in enumerate((accept_language or "").split(",")):
        tag, _, params = part.strip().partition(";")
        q = 1.0
        if params.strip().startswith("q="):
            try:
                q = float(params.strip()[2:])
            except ValueError:
                q = 0.0
        weighted.append((-q, i, tag.strip().lower()))
    for _, _, tag in sorted(weighted):
        primary = tag.split("-", 1)[0]
        if primary == "en":
            return Lang.EN
        if primary == "es":
            return Lang.ES_MX
    return Lang.ES_MX


def fmt_decimal(value: float, lang: Lang, digits: int = 1) -> str:
    pattern = "#,##0." + "0" * digits if digits > 0 else "#,##0"
    return format_decimal(value, format=pattern, locale=lang.value)


def fmt_int(value: int, lang: Lang) -> str:
    return format_decimal(value, format="#,##0", locale=lang.value)


def fmt_percent(fraction: float, lang: Lang) -> str:
    return format_percent(fraction, locale=lang.value)


def fmt_money(cents: int, lang: Lang) -> str:
    return format_currency(cents / 100, "USD", locale=lang.value)


def fmt_date(day: date, lang: Lang, style: Literal["short", "long", "weekday"] = "short") -> str:
    return format_date(day, _DATE_PATTERNS[lang][style], locale=lang.value)


def missing_translations() -> list[str]:
    """msgids used in templates that the English catalog does not translate."""
    with _po_path(Lang.EN).open("rb") as fh:
        catalog = read_po(fh, locale=Lang.EN.value)
    method_map = [("**.html", "jinja2.ext:babel_extract")]
    options = {"**.html": {"extensions": "jinja2.ext.i18n"}}
    missing: set[str] = set()
    if not TEMPLATES_DIR.exists():
        return []
    for _file, _line, message, _comments, _ctx in extract_from_dir(
        str(TEMPLATES_DIR), method_map, options
    ):
        ids = message if isinstance(message, tuple) else (message,)
        msgid = ids[0]
        entry = catalog.get(msgid) if msgid else None
        if msgid and not (entry and entry.string):
            missing.add(msgid)
    return sorted(missing)
