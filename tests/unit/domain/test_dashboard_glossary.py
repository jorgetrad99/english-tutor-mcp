import csv
import io
from datetime import date
from uuid import uuid4

import pytest

from tutor.domain.dashboard.glossary import (
    CONTEXT_MAX,
    TextError,
    clean_user_text,
    filter_glossary,
    glossary_csv,
)
from tutor.domain.dashboard.types import (
    DueFilter,
    GlossaryFilter,
    GlossaryKind,
    GlossaryRow,
    GlossaryStatus,
)

pytestmark = pytest.mark.unit

TODAY = date(2027, 1, 12)


def row(text: str, **kw: object) -> GlossaryRow:
    base: dict[str, object] = {
        "id": uuid4(),
        "kind": GlossaryKind.CHUNK,
        "text": text,
        "meaning": "",
        "context_sentence": f"I said {text}.",
        "domain": "it",
        "status": GlossaryStatus.CONFIRMED,
        "due_on": None,
        "leech": False,
        "expires_on": None,
    }
    base.update(kw)
    return GlossaryRow(**base)  # type: ignore[arg-type]


def test_clean_collapses_whitespace_and_strips_control_characters() -> None:
    assert clean_user_text("  a\tb​  c\x00 ", max_len=50, required=True) == "a b c"


def test_clean_rejects_empty_when_required_and_too_long() -> None:
    with pytest.raises(TextError) as empty:
        clean_user_text("   ", max_len=10, required=True)
    assert empty.value.code == "empty"
    assert clean_user_text("   ", max_len=10, required=False) == ""
    with pytest.raises(TextError) as long:
        clean_user_text("x" * (CONTEXT_MAX + 1), max_len=CONTEXT_MAX, required=True)
    assert long.value.code == "too_long"


def test_clean_keeps_html_as_plain_text() -> None:
    assert clean_user_text("<b>trade-off</b>", max_len=50, required=True) == "<b>trade-off</b>"


def test_filters_combine() -> None:
    rows = [
        row("trade-off", due_on=TODAY),
        row("ship it", kind=GlossaryKind.TERM, due_on=date(2027, 1, 18)),
        row("rollback", domain="business", due_on=date(2027, 1, 30)),
        row("blocker", status=GlossaryStatus.PROVISIONAL),
    ]
    by_due = filter_glossary(rows, GlossaryFilter(due=DueFilter.TODAY), TODAY)
    assert [r.text for r in by_due] == ["trade-off"]
    week = filter_glossary(rows, GlossaryFilter(due=DueFilter.WEEK), TODAY)
    assert [r.text for r in week] == ["trade-off", "ship it"]
    assert [r.text for r in filter_glossary(rows, GlossaryFilter(domain="business"), TODAY)] == [
        "rollback"
    ]
    provisional = GlossaryFilter(status=GlossaryStatus.PROVISIONAL)
    assert [r.text for r in filter_glossary(rows, provisional, TODAY)] == ["blocker"]


def test_search_ignores_case_and_accents_across_fields() -> None:
    rows = [row("follow up", meaning="dar seguimiento"), row("deadline", meaning="fecha límite")]
    assert [r.text for r in filter_glossary(rows, GlossaryFilter(q="LIMITE"), TODAY)] == [
        "deadline"
    ]


def test_sort_is_due_first_then_text() -> None:
    rows = [row("b"), row("a"), row("z", due_on=TODAY)]
    assert [r.text for r in filter_glossary(rows, GlossaryFilter(), TODAY)] == ["z", "a", "b"]


def test_csv_has_bom_header_and_neutralises_formulas() -> None:
    out = glossary_csv([row('=HYPERLINK("http://x")', meaning="@cmd", due_on=TODAY)])
    assert out.startswith("﻿")
    parsed = list(csv.reader(io.StringIO(out.lstrip("﻿"))))
    assert parsed[0] == [
        "text",
        "kind",
        "meaning",
        "context_sentence",
        "domain",
        "status",
        "due_on",
        "leech",
    ]
    assert parsed[1][0] == '\'=HYPERLINK("http://x")'
    assert parsed[1][2] == "'@cmd"
    assert parsed[1][6] == "2027-01-12"
    assert parsed[1][7] == "false"
