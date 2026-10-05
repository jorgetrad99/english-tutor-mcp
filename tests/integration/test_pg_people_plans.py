"""Postgres-specific behaviour of the people and plan repositories."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from tutor.domain.plan_lite import PlannedItem
from tutor.domain.profile import Profile
from tutor.services.ports import UowFactory

pytestmark = pytest.mark.integration

PROFILE = Profile(
    self_level="B1",
    domains=("it",),
    use_cases=("standup", "code_review"),
    minutes_per_day=20,
    days_per_week=3,
    target_level="B2",
    target_date=date(2027, 3, 1),
    goal_text="Lead the standup in English",
    timezone="America/New_York",
)


def test_new_user_has_the_default_timezone(uow_factory: UowFactory, user_id: UUID) -> None:
    with uow_factory(user_id) as uow:
        assert uow.users.timezone() == "America/Mexico_City"
        assert uow.profiles.get() is None


def test_profile_upsert_writes_users_timezone_and_keeps_onboarded_at(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    later = now + timedelta(days=2)
    with uow_factory(user_id) as uow:
        uow.profiles.upsert(PROFILE, now)
    with uow_factory(user_id) as uow:
        uow.profiles.upsert(replace(PROFILE, days_per_week=5, goal_text=None), later)
        assert uow.profiles.get() == replace(PROFILE, days_per_week=5, goal_text=None)
        assert uow.users.timezone() == "America/New_York"
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT onboarded_at, updated_at FROM profiles WHERE user_id = :u"),
            {"u": user_id},
        ).one()
    assert (row.onboarded_at, row.updated_at) == (now, later)


def test_note_mcp_use_is_true_only_the_first_time(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        assert uow.users.note_mcp_use(now) is True
    with uow_factory(user_id) as uow:
        assert uow.users.note_mcp_use(now + timedelta(hours=1)) is False


def test_new_plan_supersedes_the_old_one_and_old_items_can_still_be_done(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    # Review Focus 4 (repository level): a superseded version's item is still marked done.
    with uow_factory(user_id) as uow:
        track = uow.track.items("it")
        v1 = uow.plans.create(
            [
                PlannedItem(week_no=1, order_no=1, track_item_id=track[0].id, variant="base"),
                PlannedItem(week_no=1, order_no=2, track_item_id=track[1].id, variant="base"),
            ],
            {"reachable": True},
            now,
        )
        v2 = uow.plans.create(
            [PlannedItem(week_no=1, order_no=1, track_item_id=track[1].id, variant="base")],
            {"reachable": False},
            now + timedelta(hours=1),
        )
        assert (v1.version, v2.version) == (1, 2)
        assert uow.plans.active() == v2
        assert [i.status for i in v1.items] == ["pending", "pending"]
        session_id = uow.sessions.create(
            plan_item_id=None,
            track_item_id=track[0].id,
            prep_text=None,
            mode="voice",
            client="claude",
            brief_variant="base",
            chunks_offered=[],
            now=now,
        ).id
        assert uow.plans.mark_done(v1.items[0].id, session_id) is True
        assert uow.plans.mark_done(v1.items[0].id, session_id) is False
        assert uow.plans.done_base_track_ids() == frozenset({track[0].id})
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT version, status FROM plans WHERE user_id = :u"), {"u": user_id}
        ).all()
    assert {r.version: r.status for r in rows} == {1: "superseded", 2: "active"}


def test_the_database_allows_one_active_plan_per_user(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        uow.plans.create([], {}, now)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO plans (user_id, version, status, generated_at, rationale)"
                " VALUES (:u, 9, 'active', now(), '{}')"
            ),
            {"u": user_id},
        )


def test_audit_record_stores_event_and_meta(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        uow.audit.record("profile_saved", {"plan_changed": True}, now)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT event, meta, at FROM audit_log WHERE user_id = :u"), {"u": user_id}
        ).one()
    assert tuple(row) == ("profile_saved", {"plan_changed": True}, now)


def test_non_json_values_raise_type_error_like_the_memory_store(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with pytest.raises(TypeError), uow_factory(user_id) as uow:
        uow.plans.create([], {"bad": object()}, now)
    with pytest.raises(TypeError), uow_factory(user_id) as uow:
        uow.audit.record("profile_saved", {"bad": object()}, now)
    with uow_factory(user_id) as uow:
        assert uow.plans.active() is None
