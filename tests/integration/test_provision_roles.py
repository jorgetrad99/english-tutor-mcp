"""The deployment role step on db-test: idempotent, least privilege, passes the app's own check."""

import os
import secrets
from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from tutor.db.engine import check_app_role, make_engine
from tutor.ops.provision_roles import main
from tutor.ops.report_role import REPORT_ROLE

pytestmark = pytest.mark.integration

LOGIN = "tutor_provision_test"
DEFAULT_URL = "postgresql://tutor:tutor@localhost:5433/tutor_test"


@pytest.fixture
def owner_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_URL)


@pytest.fixture(autouse=True)
def drop_login(engine: Engine) -> Iterator[None]:
    yield
    with engine.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {LOGIN}"))


def _env(owner_url: str, app_pw: str, report_pw: str) -> dict[str, str]:
    return {
        "MIGRATION_DATABASE_URL": owner_url,
        "APP_DB_USER": LOGIN,
        "APP_DB_PASSWORD": app_pw,
        "REPORT_DB_PASSWORD": report_pw,
    }


def test_provision_is_idempotent_and_the_login_passes_the_app_check(
    engine: Engine, owner_url: str
) -> None:
    app_pw, report_pw = secrets.token_hex(24), secrets.token_hex(24)
    assert main(_env(owner_url, app_pw, report_pw)) == 0
    new_app_pw = secrets.token_hex(24)
    assert main(_env(owner_url, new_app_pw, report_pw)) == 0  # rerun: password rotates
    login_url = make_url(owner_url).set(username=LOGIN, password=new_app_pw)
    login = make_engine(login_url.render_as_string(hide_password=False))
    try:
        check_app_role(login)  # not superuser, no BYPASSRLS, member of tutor_app, owns nothing
    finally:
        login.dispose()
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            make_url(owner_url).set(username=LOGIN, password=app_pw).render_as_string(False)
        )
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT r.rolcanlogin, r.rolsuper, r.rolbypassrls, r.rolcreatedb, r.rolcreaterole, "
                "m.inherit_option, m.set_option FROM pg_roles r "
                "JOIN pg_auth_members m ON m.member = r.oid "
                "JOIN pg_roles g ON g.oid = m.roleid AND g.rolname = 'tutor_app' "
                "WHERE r.rolname = :n"
            ),
            {"n": LOGIN},
        ).one()
        report = conn.execute(
            text("SELECT rolcanlogin, rolbypassrls, rolsuper FROM pg_roles WHERE rolname = :n"),
            {"n": REPORT_ROLE},
        ).one()
    assert tuple(row) == (True, False, False, False, False, False, True)
    assert tuple(report) == (True, True, False)


def test_owner_cannot_be_the_app_login(owner_url: str) -> None:
    env = _env(owner_url, secrets.token_hex(24), secrets.token_hex(24))
    env["APP_DB_USER"] = make_url(owner_url).username or "tutor"
    with pytest.raises(SystemExit):
        main(env)
