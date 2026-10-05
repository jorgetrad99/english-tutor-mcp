"""The app connects as a non-superuser LOGIN role that reaches data only through tutor_app."""

from datetime import datetime
from uuid import UUID

import pytest
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tutor.db.uow import PgIdentity, PgUnitOfWork, pg_uow_factory

from .conftest import LOGIN_ROLE

pytestmark = pytest.mark.integration


def test_login_role_is_neither_superuser_nor_bypassrls(login_engine: Engine) -> None:
    with login_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert tuple(row) == (False, False)


def test_unit_of_work_works_through_the_login_role(
    login_engine: Engine, user_id: UUID, other_user_id: UUID
) -> None:
    with pg_uow_factory(login_engine)(user_id) as uow:
        assert isinstance(uow, PgUnitOfWork)
        who = uow.conn.execute(text("SELECT session_user, current_user")).one()
        ids = uow.conn.execute(text("SELECT id FROM users")).scalars().all()
    assert tuple(who) == (LOGIN_ROLE, "tutor_app")
    assert ids == [user_id]


def test_identity_works_through_the_login_role(login_engine: Engine, now: datetime) -> None:
    created = PgIdentity(login_engine).resolve("sub-login", "l@example.com", "Lo", now)
    again = PgIdentity(login_engine).resolve("sub-login", None, None, now)
    assert (created.created, again.created, again.id) == (True, False, created.id)


def test_unscoped_query_sees_nothing(login_engine: Engine, user_id: UUID) -> None:
    # No SET ROLE and no app.* settings: tutor_app's privileges apply, but RLS matches nothing
    # (or the statement is denied outright if the grant is made WITHOUT INHERIT).
    try:
        with login_engine.begin() as conn:
            counts = [
                conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
                for table in ("users", "profiles", "sessions", "glossary_items")
            ]
    except DBAPIError as err:
        assert isinstance(err.orig, InsufficientPrivilege)
    else:
        assert counts == [0, 0, 0, 0]


def test_pooled_connection_is_clean_after_a_unit_of_work(
    login_engine: Engine, user_id: UUID
) -> None:
    with pg_uow_factory(login_engine)(user_id) as uow:
        assert isinstance(uow, PgUnitOfWork)
        uow.conn.execute(text("SELECT 1"))
    with login_engine.connect() as conn:
        row = conn.execute(
            text("SELECT current_user, nullif(current_setting('app.user_id', true), '')")
        ).one()
    assert tuple(row) == (LOGIN_ROLE, None)


def test_blank_google_sub_is_rejected_before_the_database(
    login_engine: Engine, now: datetime
) -> None:
    for sub in ("", "   "):
        with pytest.raises(ValueError, match="google_sub"):
            PgIdentity(login_engine).resolve(sub, None, None, now)
