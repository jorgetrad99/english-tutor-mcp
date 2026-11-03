"""Retiring a template on Postgres through the app's LOGIN role (slice D ruling 59).

The 0006 grant lets tutor_app update only status, retired_at and updated_at on
exercise_templates; retire_template must fit inside it, and any other column stays refused.
"""

import zoneinfo
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from psycopg.errors import InsufficientPrivilege
from repo_contract import sample_subject
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from template_fixtures import HTML, NUMERIC, spec

from tutor.db.uow import pg_uow_factory, scoped_connection
from tutor.services.context import Services
from tutor.services.templates import retire_template

pytestmark = pytest.mark.integration


def test_retire_template_runs_within_the_app_roles_grant(
    engine: Engine, login_engine: Engine, user_id: UUID, other_user_id: UUID, now: datetime
) -> None:
    uow_factory = pg_uow_factory(login_engine)
    later = now + timedelta(minutes=5)
    svc = Services(
        uow=uow_factory,
        clock=lambda: later,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject(), now).id
        template = uow.templates.create(subject_id, spec(NUMERIC), HTML, 1, now)
    assert retire_template(svc, other_user_id, template.id) is None  # Review Focus 3
    assert retire_template(svc, user_id, template.id) == "retired"
    assert retire_template(svc, user_id, template.id) == "already_retired"
    with uow_factory(user_id) as uow:
        row = uow.templates.get(template.id)
    with engine.connect() as conn:  # the owner reads the audit log
        events = conn.execute(
            text("SELECT meta->>'retire_outcome' FROM audit_log WHERE event = 'template_retired'")
        ).scalars()
        assert sorted(events) == ["already_retired", "retired"]
    assert row is not None
    assert (row.status, row.retired_at, row.updated_at, row.created_at) == (
        "retired",
        later,
        later,
        now,
    )
    # Any other column is still refused to the app role.
    with pytest.raises(DBAPIError) as err, scoped_connection(login_engine, user_id=user_id) as conn:
        conn.execute(
            text("UPDATE exercise_templates SET name = 'Otra' WHERE id = :id"), {"id": template.id}
        )
    assert isinstance(err.value.orig, InsufficientPrivilege)
