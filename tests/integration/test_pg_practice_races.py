"""Concurrent practice calls on Postgres (security review P3 of slice B; rulings 29, 30, 86)."""

import threading
import zoneinfo
from datetime import date, datetime
from uuid import UUID

import pytest
from psycopg.errors import LockNotAvailable
from repo_contract import PLAN, practice_world, sample_subject
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tutor.domain.subjects import SubjectInput
from tutor.services.context import Services
from tutor.services.ports import UowFactory
from tutor.services.practice import start_practice
from tutor.services.subjects import save_subject

pytestmark = pytest.mark.integration


def services(uow_factory: UowFactory, now: datetime) -> Services:
    return Services(
        uow=uow_factory,
        clock=lambda: now,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )


def subject_input(sessions_per_week: int) -> SubjectInput:
    return SubjectInput(
        name="Historia",
        goal_text=None,
        context_text=None,
        preference="balanced",
        exercise_kinds=["quiz", "drill", "exercise"],
        minutes_per_session=30,
        sessions_per_week=sessions_per_week,
        target_date=None,
        status=None,
    )


def run_together(*calls: object) -> list[object]:
    """Run each callable in its own thread at once; their results or exceptions, in order."""
    outcomes: list[object] = [None] * len(calls)
    start = threading.Barrier(len(calls))

    def worker(index: int, call: object) -> None:
        start.wait()
        try:
            outcomes[index] = call()  # type: ignore[operator]
        except Exception as exc:  # recorded and asserted by the test
            outcomes[index] = exc

    threads = [threading.Thread(target=worker, args=(i, c)) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    return outcomes


def test_create_plan_locks_the_subject_row(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject(), now).id
    with uow_factory(user_id) as uow:
        uow.practice.create_plan(subject_id, PLAN, date(2026, 10, 14), now, {})
        # The foreign key's own FOR KEY SHARE does not conflict with this; the version lock does.
        with pytest.raises(DBAPIError) as err, engine.begin() as other:
            other.execute(
                text("SELECT id FROM subjects WHERE id = :id FOR NO KEY UPDATE NOWAIT"),
                {"id": subject_id},
            )
        assert isinstance(err.value.orig, LockNotAvailable)


def test_concurrent_plan_versions_never_collide(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    svc = services(uow_factory, now)
    subject_id = save_subject(svc, user_id, subject_input(3)).subject.id
    outcomes = run_together(
        *((lambda n=n: save_subject(svc, user_id, subject_input(n), subject_id)) for n in (4, 5, 6))
    )
    assert not [o for o in outcomes if isinstance(o, Exception)], outcomes
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT version, status FROM practice_plans WHERE subject_id = :s ORDER BY 1"),
            {"s": subject_id},
        ).all()
    assert [r.version for r in rows] == [1, 2, 3, 4]
    assert [r.status for r in rows].count("active") == 1


def test_two_start_practice_calls_racing_leave_one_open_session(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        world = practice_world(uow, now)
    svc = services(uow_factory, now)
    outcomes = run_together(
        lambda: start_practice(svc, user_id, world.subject_id, "text"),
        lambda: start_practice(svc, user_id, world.subject_id, "text"),
    )
    assert not [o for o in outcomes if isinstance(o, Exception)], outcomes
    with engine.connect() as conn:
        statuses = conn.execute(
            text("SELECT status FROM practice_sessions WHERE user_id = :u ORDER BY status"),
            {"u": user_id},
        ).scalars()
        assert list(statuses) == ["incomplete", "open"]
