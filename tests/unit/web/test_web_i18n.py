import re
from datetime import date
from uuid import uuid4

import pytest

from tutor.domain.dashboard.types import Lang, Role, User
from tutor.web.i18n import (
    fmt_date,
    fmt_decimal,
    fmt_int,
    fmt_money,
    fmt_percent,
    load_translations,
    missing_translations,
    negotiate,
)


def test_decimals_use_point_in_mexico_and_english() -> None:
    assert fmt_decimal(3.14, Lang.ES_MX) == "3.1"
    assert fmt_decimal(1234.5, Lang.ES_MX) == "1,234.5"
    assert fmt_decimal(3.14, Lang.EN) == "3.1"
    assert fmt_int(12345, Lang.ES_MX) == "12,345"


def test_percent_and_money() -> None:
    assert re.sub(r"\s", "", fmt_percent(0.42, Lang.ES_MX)) == "42%"
    money = fmt_money(2900, Lang.ES_MX)
    assert "29.00" in money and ("USD" in money or "US$" in money)


def test_dates_are_localised() -> None:
    assert fmt_date(date(2027, 1, 8), Lang.ES_MX, "weekday").startswith("viernes 8 de ene")
    assert fmt_date(date(2027, 1, 8), Lang.EN, "weekday") == "Fri, Jan 8"
    assert fmt_date(date(2027, 1, 8), Lang.ES_MX, "long") == "8 de enero de 2027"


@pytest.mark.parametrize(
    ("query", "header", "expected"),
    [
        ("en", None, Lang.EN),
        ("es", "en-US", Lang.ES_MX),
        (None, "en-US,en;q=0.9,es;q=0.8", Lang.EN),
        (None, "es-MX,es;q=0.9,en;q=0.8", Lang.ES_MX),
        (None, "fr-FR,en;q=0.5", Lang.EN),
        (None, "de-DE", Lang.ES_MX),
        (None, None, Lang.ES_MX),
        ("xx", "garbage;;;q=abc", Lang.ES_MX),
    ],
)
def test_negotiate_for_anonymous_visitors(
    query: str | None, header: str | None, expected: Lang
) -> None:
    assert negotiate(query, header, None) is expected


def test_logged_in_user_language_wins() -> None:
    user = User(uuid4(), "Ana", Role.LEARNER, Lang.EN, "UTC", False)
    assert negotiate("es", "es-MX", user) is Lang.EN


def test_spanish_needs_no_catalog_and_english_translates() -> None:
    assert load_translations(Lang.ES_MX).gettext("Inicio") == "Inicio"
    assert load_translations(Lang.EN).gettext("Entrar con Google") == "Sign in with Google"


def test_every_template_string_has_an_english_translation() -> None:
    assert missing_translations() == []
