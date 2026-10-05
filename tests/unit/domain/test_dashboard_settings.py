import pytest

from tutor.domain.dashboard.settings import SettingError, parse_setting
from tutor.domain.dashboard.types import SettingType

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("kind", "raw", "canonical"),
    [
        (SettingType.INT, " 12 ", "12"),
        (SettingType.INT, "-3", "-3"),
        (SettingType.FLOAT, "0.85", "0.85"),
        (SettingType.FLOAT, "1", "1.0"),
        (SettingType.BOOL, "Sí", "true"),
        (SettingType.BOOL, "0", "false"),
        (SettingType.STR, "  hola   mundo ", "hola mundo"),
    ],
)
def test_valid_values_are_canonicalised(kind: SettingType, raw: str, canonical: str) -> None:
    assert parse_setting(kind, raw) == canonical


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        (SettingType.INT, "1.5"),
        (SettingType.INT, ""),
        (SettingType.FLOAT, "0,85"),
        (SettingType.FLOAT, "nan"),
        (SettingType.BOOL, "maybe"),
        (SettingType.STR, "x" * 201),
    ],
)
def test_invalid_values_raise(kind: SettingType, raw: str) -> None:
    with pytest.raises(SettingError):
        parse_setting(kind, raw)


def test_very_long_int_is_rejected() -> None:
    with pytest.raises(SettingError):
        parse_setting(SettingType.INT, "1" * 5000)


def test_very_long_float_is_rejected() -> None:
    with pytest.raises(SettingError):
        parse_setting(SettingType.FLOAT, "1." + "1" * 400)


def test_inf_and_nan_strings_raise() -> None:
    with pytest.raises(SettingError):
        parse_setting(SettingType.FLOAT, "inf")
    with pytest.raises(SettingError):
        parse_setting(SettingType.FLOAT, "nan")


def test_whitespace_only_str_is_empty() -> None:
    assert parse_setting(SettingType.STR, "   \t\n  ") == ""


def test_inf_string_float_raises() -> None:
    with pytest.raises(SettingError):
        parse_setting(SettingType.FLOAT, "inf")


def test_negative_inf_string_float_raises() -> None:
    with pytest.raises(SettingError):
        parse_setting(SettingType.FLOAT, "-inf")


def test_overflow_float_raises() -> None:
    # 1e309 overflows to infinity
    with pytest.raises(SettingError):
        parse_setting(SettingType.FLOAT, "1e309")
