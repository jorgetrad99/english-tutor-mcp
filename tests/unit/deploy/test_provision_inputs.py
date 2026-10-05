"""Input checks of the role-provisioning step (core Task 27): names keys, never values."""

import pytest

from tutor.ops.provision_roles import check_inputs, main

pytestmark = pytest.mark.unit

GOOD = {
    "MIGRATION_DATABASE_URL": "postgresql://owner:x@db:5432/tutor",
    "APP_DB_USER": "tutor_login",
    "APP_DB_PASSWORD": "a" * 40,
    "REPORT_DB_PASSWORD": "b" * 40,
}


def test_good_inputs_pass() -> None:
    assert check_inputs(GOOD) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("APP_DB_USER", "tutor_app"),
        ("APP_DB_USER", "tutor_report"),
        ("APP_DB_USER", "postgres"),
        ("APP_DB_USER", "pg_monitor"),
        ("APP_DB_USER", 'x"; DROP ROLE y; --'),
        ("APP_DB_PASSWORD", "short"),
        ("REPORT_DB_PASSWORD", "a" * 40),
        ("MIGRATION_DATABASE_URL", "sqlite:///x.db"),
    ],
)
def test_bad_inputs_are_named(key: str, value: str) -> None:
    problems = check_inputs({**GOOD, key: value})
    assert problems
    assert not any(value in p for p in problems if len(value) > 8)


def test_missing_keys_are_named(capsys: pytest.CaptureFixture[str]) -> None:
    assert main({}) == 2
    err = capsys.readouterr().err
    assert "APP_DB_PASSWORD is missing" in err
