from uuid import uuid4

from tutor.domain.dashboard.types import Mode
from tutor.web.query import bounded_int, enum_or_none, parse_uuid


def test_enum_or_none_accepts_values_and_ignores_the_rest() -> None:
    assert enum_or_none(Mode, "voice") is Mode.VOICE
    assert enum_or_none(Mode, "fax") is None
    assert enum_or_none(Mode, "") is None
    assert enum_or_none(Mode, None) is None


def test_bounded_int_clamps_and_falls_back() -> None:
    assert bounded_int("5", default=0, minimum=1, maximum=10) == 5
    assert bounded_int(" 7 ", default=0, minimum=1, maximum=10) == 7
    assert bounded_int("50", default=0, minimum=1, maximum=10) == 10
    assert bounded_int("0", default=0, minimum=1, maximum=10) == 1
    assert bounded_int("-3", default=4, minimum=1, maximum=10) == 4
    assert bounded_int("abc", default=4, minimum=1, maximum=10) == 4
    assert bounded_int(None, default=4, minimum=1, maximum=10) == 4
    assert bounded_int("9" * 5000, default=4, minimum=1, maximum=10) == 4


def test_parse_uuid() -> None:
    value = uuid4()
    assert parse_uuid(str(value)) == value
    assert parse_uuid("not-a-uuid") is None
