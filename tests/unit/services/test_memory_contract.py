"""The repository contract on MemoryStore, plus the store's own guarantees."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from repo_contract import (
    RepoContract,
    add_closed_session,
    add_note,
    first_item,
    incoming,
    insert_glossary,
    long_paragraph,
    planned_items,
    present,
    sample_metrics,
    sample_profile,
    sample_subject,
    start_session,
    valid_note,
    valid_source,
)
from template_fixtures import HTML, KEY_NAME, NUMERIC, REVEAL_NAME, spec

from tutor.domain.glossary import InsertItem
from tutor.domain.notes import ConceptLinkInput
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.memory import MemoryStore, excerpt
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
        subject_id = uow.subjects.create(sample_subject(), now).id
        source_id = uow.notes.upsert_source(subject_id, valid_source(), now).id
        link = ConceptLinkInput("Ritmo", "Compás", "related")
        note = valid_note("Clase", long_paragraph("texto."), concepts=["Ritmo"], links=[link])
        row = add_note(uow, subject_id, note, now, source_id=source_id)
        template = uow.templates.create(subject_id, spec(NUMERIC), HTML, 1, now)
        uow.instances.insert(
            subject_id=subject_id,
            note_id=row.id,
            template=template,
            params={"problem": "2 + 2", KEY_NAME: 4.0, REVEAL_NAME: "r"},
            prompt_text="Suma.",
            concept_id=present(uow.notes.concept_by_norm(subject_id, "ritmo")).id,
            origin="ingest",
            key="0" * 64,
            now=now,
        )
        raise RuntimeError("abort")
    assert store.tables == before


def test_duplicate_idempotency_key_for_one_user_raises(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    note = valid_note("A", long_paragraph("uno."))
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject(), now).id
        add_note(uow, subject_id, note, now)
    with pytest.raises(ValueError, match="duplicate"), uow_factory(user_id) as uow:
        add_note(uow, subject_id, note, now)


def test_search_excerpt_marks_the_first_match_in_a_25_word_window() -> None:
    # Plan ruling 3: the memory model of ts_headline.
    text = (
        " ".join(f"w{i}" for i in range(40))
        + " Séptima, dominante. "
        + " ".join(f"v{i}" for i in range(30))
    )
    words = excerpt(text, {"septima"}).split()
    assert len(words) == 25
    assert words[0] == "w32"
    assert words[8] == "[[Séptima]],"
    assert excerpt("La séptima suena. Y la séptima otra vez.", {"septima", "suena"}) == (
        "La [[séptima]] suena. Y la séptima otra vez."
    )
    assert excerpt("sin nada", {"x"}) == "sin nada"


def test_search_falls_back_to_the_title_for_short_queries(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    # The memory model of the trigram fallback on notes.title_norm (plan ruling 2).
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject(), now).id
        titled = add_note(
            uow, subject_id, valid_note("Afinación con afinador", long_paragraph("texto uno.")), now
        )
        add_note(uow, subject_id, valid_note("Otra", long_paragraph("texto dos.")), now)
    with uow_factory(user_id) as uow:
        [hit] = uow.notes.search("afinacion", None, 10)
        assert (hit.note_id, hit.passages, hit.concepts, hit.depth, hit.fuzzy, hit.excerpts) == (
            titled.id,
            0,
            (),
            None,
            True,
            (),
        )
        assert uow.notes.search("afinación con afinador perfecto", None, 10) == ()  # 4 tokens
        assert uow.notes.search("texto", None, 10)[0].fuzzy is False


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
