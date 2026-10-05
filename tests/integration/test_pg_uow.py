"""PgUnitOfWork and PgIdentity on db-test."""

from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from tutor.db.uow import PgUnitOfWork, scoped_connection
from tutor.services.ports import IdentityResolver, UowFactory

pytestmark = pytest.mark.integration

_INSERT_EVENT = text("INSERT INTO audit_log (user_id, event, meta, at) VALUES (:u, :e, '{}', :at)")


def test_unit_of_work_runs_as_tutor_app_for_its_user(
    uow_factory: UowFactory, user_id: UUID
) -> None:
    with uow_factory(user_id) as uow:
        assert isinstance(uow, PgUnitOfWork)
        assert uow.user_id == user_id
        row = uow.conn.execute(text("SELECT current_user, current_setting('app.user_id')")).one()
    assert tuple(row) == ("tutor_app", str(user_id))


def test_clean_exit_commits_and_an_exception_rolls_back(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        assert isinstance(uow, PgUnitOfWork)
        uow.conn.execute(_INSERT_EVENT, {"u": user_id, "e": "kept", "at": now})
    with pytest.raises(RuntimeError), uow_factory(user_id) as uow:
        assert isinstance(uow, PgUnitOfWork)
        uow.conn.execute(_INSERT_EVENT, {"u": user_id, "e": "dropped", "at": now})
        raise RuntimeError("boom")
    with engine.connect() as conn:
        events = conn.execute(
            text("SELECT event FROM audit_log WHERE user_id = :u"), {"u": user_id}
        ).scalars()
        assert list(events) == ["kept"]


def test_repositories_not_built_yet_raise(uow_factory: UowFactory, user_id: UUID) -> None:
    # Removed in Task 17, when the last placeholder repository is replaced.
    with uow_factory(user_id) as uow, pytest.raises(NotImplementedError):
        uow.glossary.count_provisional()


def test_identity_finds_or_creates_by_sub_never_by_email(
    engine: Engine, identity: IdentityResolver, now: datetime
) -> None:
    first = identity.resolve("sub-new", "same@example.com", "Ana", now)
    again = identity.resolve("sub-new", "changed@example.com", None, now)
    other = identity.resolve("sub-other", "same@example.com", "Ana", now)
    assert (first.created, again.created, other.created) == (True, False, True)
    assert again.id == first.id
    assert other.id != first.id
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT display_name, email, created_at, timezone, role FROM users WHERE id = :i"),
            {"i": first.id},
        ).one()
    assert tuple(row) == ("Ana", "same@example.com", now, "America/Mexico_City", "learner")


def test_identity_falls_back_to_the_email_local_part(
    engine: Engine, identity: IdentityResolver, now: datetime
) -> None:
    created = identity.resolve("sub-lu", "lu@example.com", None, now)
    with engine.connect() as conn:
        name = conn.execute(
            text("SELECT display_name FROM users WHERE id = :i"), {"i": created.id}
        ).scalar_one()
    assert name == "lu"


def test_google_sub_scope_sees_only_that_user(
    engine: Engine, user_id: UUID, other_user_id: UUID
) -> None:
    with scoped_connection(engine, google_sub="sub-user-a") as conn:
        ids = conn.execute(text("SELECT id FROM users")).scalars().all()
    assert ids == [user_id]
