"""The repository contract on MemoryStore, plus the store's own guarantees."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from repo_contract import (
    RepoContract,
    add_closed_session,
    first_item,
    incoming,
    insert_glossary,
    planned_items,
    sample_metrics,
    sample_profile,
    start_session,
)

from tutor.domain.glossary import InsertItem
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.memory import MemoryStore
from tutor.services.ports import OpenSessionExists, UowFactory

from .conftest import FixedClock

pytestmark = pytest.mark.unit


class TestMemoryRepos(RepoContract):
    """Every RepoContract test on the in-memory store."""


def test_duplicate_text_norm_for_one_user_raises(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        session = start_session(uow, first_item(uow), now)
        insert_glossary(uow, session.id, "Ship it", now)
    duplicate = InsertItem(
        index=0,
        item=incoming("ship it!"),
        text_norm="ship it",
        status="provisional",
        provisional_expires_at=now + timedelta(days=7),
        first_due=None,
    )
    with pytest.raises(ValueError, match="duplicate"), uow_factory(user_id) as uow:
        uow.glossary.apply([duplicate], [duplicate.item], session_id=session.id, now=now)


def test_session_create_hook_runs_before_the_insert(
    store: MemoryStore, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    def lose_the_race() -> None:
        raise OpenSessionExists()

    store.before_session_create = lose_the_race
    with pytest.raises(OpenSessionExists), uow_factory(user_id) as uow:
        start_session(uow, first_item(uow), now)
    assert store.tables.sessions == {}


def test_json_columns_reject_values_jsonb_cannot_store(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with pytest.raises(TypeError), uow_factory(user_id) as uow:
        uow.plans.create(planned_items(uow.track.items("it")), {"generated": now}, now)
    with uow_factory(user_id) as uow:
        assert uow.plans.active() is None


def test_rollback_restores_every_table(
    store: MemoryStore, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    before = copy.deepcopy(store.tables)
    with pytest.raises(RuntimeError), uow_factory(user_id) as uow:
        uow.profiles.upsert(sample_profile(), now)
        uow.plans.create(planned_items(uow.track.items("it")), {"weeks": 12}, now)
        session = start_session(uow, first_item(uow), now)
        insert_glossary(uow, session.id, "ship it", now)
        uow.users.note_mcp_use(now)
        uow.audit.record("profile_saved", {}, now)
        raise RuntimeError("abort")
    assert store.tables == before


def test_service_error_carries_only_code_and_fields() -> None:
    error = ServiceError("validation_failed", ("prep", "minutes"))
    assert (error.code, error.fields, str(error)) == (
        "validation_failed",
        ("prep", "minutes"),
        "validation_failed",
    )
    assert ServiceError("rate_limited").fields == ()


def test_services_reads_the_injected_clock(svc: Services, clock: FixedClock, now: datetime) -> None:
    assert svc.clock() == now
    clock.advance(timedelta(minutes=5))
    assert svc.clock() == now + timedelta(minutes=5)


def test_audit_record_is_stored_with_event_meta_and_timestamp(
    store: MemoryStore, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    meta = {"session_id": "abc", "status": "closed", "low_trust": False, "n": 3}
    with uow_factory(user_id) as uow:
        uow.audit.record("session_closed", meta, now)
    [row] = store.tables.audit
    assert (row.user_id, row.event, row.meta, row.at) == (user_id, "session_closed", meta, now)


def test_save_metrics_twice_keeps_one_stored_record(
    store: MemoryStore, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        session = add_closed_session(uow, first_item(uow), now - timedelta(minutes=20), now)
        uow.sessions.save_metrics(session.id, sample_metrics())
    with uow_factory(user_id) as uow:
        uow.sessions.save_metrics(session.id, sample_metrics())
    assert list(store.tables.metrics) == [session.id]
    record = store.tables.metrics[session.id]
    assert (record.user_id, record.metrics) == (user_id, sample_metrics())
