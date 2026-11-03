"""Migration 0007 on db-test: round trip and its guard, one open practice session per user, one
active plan per subject, the shown-before-answer check, the answer CHECKs and the narrow grants
(subjects spec 8.2, 16.10; slice B plan rulings 30, 38, 63; Review Focus 6 and 8)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from alembic.config import Config
from psycopg.errors import CheckViolation, InsufficientPrivilege, UniqueViolation
from repo_contract import PLAN, drawn, open_practice, practice_world
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from tutor.db.uow import scoped_connection
from tutor.services.ports import UowFactory

from .conftest import ROOT, run_alembic

pytestmark = pytest.mark.integration

NEW_TABLES = ("practice_plans", "practice_sessions", "practice_items")
DESTRUCTIVE_FLAG = "TUTOR_ALLOW_DESTRUCTIVE_DOWNGRADE"
# ended_at must not precede started_at = now() (the clock of db-test, not the fixture's).
FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
FROZEN_ITEM_COLUMNS = ("instance_id", "draw_seed", "draw", "seq", "session_id", "user_id")


def _tables_present(engine: Engine) -> bool:
    return set(NEW_TABLES) <= set(inspect(engine).get_table_names())


def _fails_with(engine: Engine, user_id: UUID, sql: str, params: dict[str, Any]) -> Any:
    with pytest.raises(DBAPIError) as err, scoped_connection(engine, user_id=user_id) as conn:
        conn.execute(text(sql), params)
    return err.value.orig


@pytest.fixture
def item(uow_factory: UowFactory, user_id: UUID, now: datetime) -> Iterator[UUID]:
    """One unanswered, unshown showing in an open session of `user_id`."""
    with uow_factory(user_id) as uow:
        world = practice_world(uow, now)
        shown = drawn(world.quiz, 1)
        open_practice(uow, world, now, items=(shown,))
    yield shown.item_id


def test_round_trip_leaves_0006_alone(engine: Engine) -> None:
    run_alembic(engine, "downgrade", "0006")
    tables = set(inspect(engine).get_table_names())
    assert tables.isdisjoint(NEW_TABLES)
    assert {"subjects", "template_instances", "exercise_templates"} <= tables
    run_alembic(engine, "upgrade", "head")
    assert _tables_present(engine)


def test_every_new_table_has_forced_rls_and_only_the_app_grant(engine: Engine) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class"
                " WHERE relname = ANY(:t)"
            ),
            {"t": list(NEW_TABLES)},
        ).all()
        grantees = conn.execute(
            text(
                "SELECT DISTINCT grantee FROM information_schema.role_table_grants"
                " WHERE table_schema = 'public' AND table_name = ANY(:t)"
            ),
            {"t": list(NEW_TABLES)},
        ).scalars()
        owner = conn.execute(text("SELECT current_user")).scalar_one()
    assert {r.relname: (r.relrowsecurity, r.relforcerowsecurity) for r in rows} == {
        t: (True, True) for t in NEW_TABLES
    }
    assert set(grantees) <= {owner, "tutor_app"}  # nothing for tutor_report


def test_ruling_30_the_open_index_allows_one_open_session_per_user(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime, item: UUID
) -> None:
    with engine.connect() as conn:
        subject_id = conn.execute(
            text("SELECT subject_id FROM practice_sessions WHERE user_id = :u"), {"u": user_id}
        ).scalar_one()
    insert = (
        "INSERT INTO practice_sessions (user_id, subject_id, mode, status, started_at,"
        " item_count, ended_at) VALUES (:u, :s, 'text', :status, now(), 0, :ended)"
    )
    orig = _fails_with(
        engine, user_id, insert, {"u": user_id, "s": subject_id, "status": "open", "ended": None}
    )
    assert isinstance(orig, UniqueViolation)
    with scoped_connection(engine, user_id=user_id) as conn:  # other statuses are free
        conn.execute(
            text(insert), {"u": user_id, "s": subject_id, "status": "incomplete", "ended": FUTURE}
        )


def test_one_active_plan_per_subject(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        world = practice_world(uow, now)
        uow.practice.create_plan(world.subject_id, PLAN, date(2026, 10, 14), now, {})
    orig = _fails_with(
        engine,
        user_id,
        "INSERT INTO practice_plans (user_id, subject_id, version, status, start_date,"
        " minutes_per_session, sessions_per_week, rationale, created_at)"
        " VALUES (:u, :s, 9, 'active', current_date, 30, 3, '{}', now())",
        {"u": user_id, "s": world.subject_id},
    )
    assert isinstance(orig, UniqueViolation)


def test_review_focus_6_an_answer_needs_a_showing_before_it(
    engine: Engine, user_id: UUID, now: datetime, item: UUID
) -> None:
    answer = (
        "UPDATE practice_items SET answer = '{}', score = 100, correct = true, source = 'chat',"
        " answered_at = :at WHERE id = :id"
    )
    orig = _fails_with(engine, user_id, answer, {"id": item, "at": now})
    assert isinstance(orig, CheckViolation)
    assert orig.diag.constraint_name == "ck_practice_items_shown_before_answer"
    with scoped_connection(engine, user_id=user_id) as conn:
        conn.execute(
            text("UPDATE practice_items SET shown_at = :at WHERE id = :id"), {"id": item, "at": now}
        )
    orig = _fails_with(engine, user_id, answer, {"id": item, "at": now - timedelta(seconds=1)})
    assert isinstance(orig, CheckViolation)
    with scoped_connection(engine, user_id=user_id) as conn:
        conn.execute(text(answer), {"id": item, "at": now + timedelta(seconds=41)})


@pytest.mark.parametrize(
    ("assignments", "constraint"),
    [
        ("score = 100, source = 'chat', answered_at = :at", "ck_practice_items_answer_complete"),
        (
            "answer = '{}', score = 101, source = 'chat', answered_at = :at",
            "ck_practice_items_score",
        ),
        (
            "answer = '{}', score = 100, source = 'web', answered_at = :at",
            "ck_practice_items_source",
        ),
        ("answer = '[]', score = 0, source = 'app', answered_at = :at", "ck_practice_items_answer"),
        ("correct = true", "ck_practice_items_answer_complete"),
    ],
)
def test_answer_checks(
    engine: Engine, user_id: UUID, now: datetime, item: UUID, assignments: str, constraint: str
) -> None:
    with scoped_connection(engine, user_id=user_id) as conn:
        conn.execute(
            text("UPDATE practice_items SET shown_at = :at WHERE id = :id"), {"id": item, "at": now}
        )
    orig = _fails_with(
        engine,
        user_id,
        f"UPDATE practice_items SET {assignments} WHERE id = :id",  # noqa: S608 - constants
        {"id": item, "at": now + timedelta(seconds=5)},
    )
    assert isinstance(orig, CheckViolation)
    assert orig.diag.constraint_name == constraint


@pytest.mark.parametrize("column", FROZEN_ITEM_COLUMNS)
def test_the_app_cannot_change_what_a_showing_drew(
    engine: Engine, user_id: UUID, item: UUID, column: str
) -> None:
    orig = _fails_with(
        engine,
        user_id,
        f"UPDATE practice_items SET {column} = {column} WHERE id = :id",  # noqa: S608
        {"id": item},
    )
    assert isinstance(orig, InsufficientPrivilege)


@pytest.mark.parametrize("table", NEW_TABLES)
def test_the_app_deletes_nothing(engine: Engine, user_id: UUID, item: UUID, table: str) -> None:
    orig = _fails_with(engine, user_id, f"DELETE FROM {table}", {})  # noqa: S608 - constants
    assert isinstance(orig, InsufficientPrivilege)


def test_session_and_plan_checks(engine: Engine, user_id: UUID, now: datetime, item: UUID) -> None:
    with engine.connect() as conn:
        session_id = conn.execute(
            text("SELECT id FROM practice_sessions WHERE user_id = :u"), {"u": user_id}
        ).scalar_one()
    cases = {
        "ck_practice_sessions_summary": "UPDATE practice_sessions SET status = 'closed',"
        " ended_at = now() WHERE id = :id",
        "ck_practice_sessions_ended_at": "UPDATE practice_sessions SET status = 'incomplete'"
        " WHERE id = :id",
        "ck_practice_sessions_self_rating": "UPDATE practice_sessions SET self_rating = 3"
        " WHERE id = :id",
    }
    for constraint, sql in cases.items():
        orig = _fails_with(engine, user_id, sql, {"id": session_id})
        assert isinstance(orig, CheckViolation)
        assert orig.diag.constraint_name == constraint
    with scoped_connection(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "UPDATE practice_sessions SET status = 'closed', ended_at = now(), summary = '{}',"
                " self_rating = 4 WHERE id = :id"
            ),
            {"id": session_id},
        )


def test_deleting_a_subject_removes_its_practice(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime, item: UUID
) -> None:
    with engine.begin() as conn:  # as the owner: the app never deletes a subject in v0
        conn.execute(text("DELETE FROM subjects WHERE user_id = :u"), {"u": user_id})
        left = [
            conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
            for t in NEW_TABLES
        ]
    assert left == [0, 0, 0]


def test_downgrade_refuses_while_practice_exists(
    engine: Engine, user_id: UUID, item: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DESTRUCTIVE_FLAG, raising=False)
    with pytest.raises(RuntimeError, match=DESTRUCTIVE_FLAG):
        run_alembic(engine, "downgrade", "0006")
    assert _tables_present(engine)
    monkeypatch.setenv(DESTRUCTIVE_FLAG, "1")
    run_alembic(engine, "downgrade", "0006")
    assert set(inspect(engine).get_table_names()).isdisjoint(NEW_TABLES)
    run_alembic(engine, "upgrade", "head")
    assert _tables_present(engine)


def test_downgrade_refuses_when_row_level_security_hides_the_rows(
    engine: Engine, user_id: UUID, item: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A role that does not bypass RLS sees empty tables whatever they hold, so without the
    flag the downgrade refuses rather than trust an empty count (the 0006 pattern)."""
    monkeypatch.delenv(DESTRUCTIVE_FLAG, raising=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    with pytest.raises(RuntimeError, match=DESTRUCTIVE_FLAG), engine.begin() as conn:
        # Alembic must read its version row as that role; the refusal rolls the grant back.
        conn.execute(text("GRANT SELECT ON alembic_version TO tutor_app"))
        conn.execute(text("SET LOCAL ROLE tutor_app"))
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0006")
    assert _tables_present(engine)
