"""save_glossary on the in-memory store (spec 10.2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from repo_contract import CONTEXT, MEANING, incoming, present

from tutor.domain.glossary import IncomingItem
from tutor.domain.text import normalize
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.glossary import save_glossary
from tutor.services.lesson import start_lesson
from tutor.services.memory import MemoryStore
from tutor.services.views import GlossarySaveResult, RejectedView, StartLessonRequest

from .conftest import NEW_YORK, NOW, FixedClock, onboard

pytestmark = pytest.mark.unit


def lesson_session(svc: Services, user_id: UUID, **profile_changes: Any) -> UUID:
    onboard(svc, user_id, **profile_changes)
    return start_lesson(svc, user_id, StartLessonRequest(mode="text")).session_id


def test_confirmed_items_are_due_tomorrow_at_four_local(svc: Services, user_id: UUID) -> None:
    session_id = lesson_session(svc, user_id)
    result = save_glossary(
        svc,
        user_id,
        session_id,
        "confirmed",
        [incoming("roll back the deploy", "chunk"), incoming("on-call")],
    )
    assert result == GlossarySaveResult(new=2, reinforced=0, promoted=0, rejected=())
    with svc.uow(user_id) as uow:
        rows = uow.glossary.by_norms(["roll back the deploy", "on-call"])
        assert {row.status for row in rows.values()} == {"confirmed"}
        for row in rows.values():  # 04:00 in Mexico City on Oct 15
            due = present(uow.reviews.state(row.id)).due
            assert due == datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def test_due_time_uses_the_learner_timezone(svc: Services, user_id: UUID) -> None:
    session_id = lesson_session(svc, user_id, timezone=NEW_YORK)
    save_glossary(svc, user_id, session_id, "confirmed", [incoming("ship it")])
    with svc.uow(user_id) as uow:
        row = uow.glossary.by_norms(["ship it"])["ship it"]
        due = present(uow.reviews.state(row.id)).due
    assert due == datetime(2026, 10, 15, 8, 0, tzinfo=UTC)  # 04:00 EDT


def test_provisional_items_expire_in_seven_days(svc: Services, user_id: UUID) -> None:
    session_id = lesson_session(svc, user_id)
    result = save_glossary(svc, user_id, session_id, "provisional", [incoming("circle back")])
    assert result.new == 1
    with svc.uow(user_id) as uow:
        row = uow.glossary.by_norms(["circle back"])["circle back"]
        assert (row.status, row.provisional_expires_at) == ("provisional", NOW + timedelta(days=7))
        assert uow.reviews.state(row.id) is None


def test_declined_items_are_stored_but_never_scheduled(svc: Services, user_id: UUID) -> None:
    session_id = lesson_session(svc, user_id)
    assert save_glossary(svc, user_id, session_id, "declined", [incoming("synergy")]).new == 1
    with svc.uow(user_id) as uow:
        row = uow.glossary.by_norms(["synergy"])["synergy"]
        assert row.status == "declined"
        assert uow.reviews.state(row.id) is None
        assert uow.glossary.count_due(NOW + timedelta(days=30)) == 0


def test_same_text_typed_with_curly_quotes_is_reinforced(svc: Services, user_id: UUID) -> None:
    # Review Focus 1: a curly apostrophe, a double space and "!" still match "I'm on it".
    session_id = lesson_session(svc, user_id)
    save_glossary(
        svc,
        user_id,
        session_id,
        "confirmed",
        [incoming("I\N{RIGHT SINGLE QUOTATION MARK}m  on it!", "chunk")],
    )
    again = save_glossary(
        svc, user_id, session_id, "confirmed", [incoming("I'm on it", "correction")]
    )
    assert (again.new, again.reinforced) == (0, 1)
    with svc.uow(user_id) as uow:
        norm = normalize("I'm on it")
        row = uow.glossary.by_norms([norm])[norm]
    assert (row.seen_count, row.kind, row.text) == (
        2,
        "correction",
        "I\N{RIGHT SINGLE QUOTATION MARK}m  on it!",
    )


def test_provisional_item_confirmed_later_is_promoted(svc: Services, user_id: UUID) -> None:
    session_id = lesson_session(svc, user_id)
    save_glossary(svc, user_id, session_id, "provisional", [incoming("keep you posted")])
    result = save_glossary(svc, user_id, session_id, "confirmed", [incoming("keep you posted")])
    assert (result.new, result.promoted) == (0, 1)
    with svc.uow(user_id) as uow:
        row = uow.glossary.by_norms(["keep you posted"])["keep you posted"]
        assert row.status == "confirmed"
        assert uow.reviews.state(row.id) is not None


def test_confirmed_item_cannot_be_declined(svc: Services, user_id: UUID) -> None:
    session_id = lesson_session(svc, user_id)
    save_glossary(svc, user_id, session_id, "confirmed", [incoming("ship it")])
    result = save_glossary(svc, user_id, session_id, "declined", [incoming("Ship it.")])
    assert result.rejected == (RejectedView(index=0, reason="already_confirmed"),)
    with svc.uow(user_id) as uow:
        assert uow.glossary.by_norms(["ship it"])["ship it"].status == "confirmed"


def test_invalid_items_are_rejected_with_reasons(svc: Services, user_id: UUID) -> None:
    session_id = lesson_session(svc, user_id)
    result = save_glossary(
        svc,
        user_id,
        session_id,
        "confirmed",
        [incoming("  !!  "), incoming("ship it"), incoming("Ship it.")],
    )
    assert result == GlossarySaveResult(
        new=1,
        reinforced=0,
        promoted=0,
        rejected=(
            RejectedView(index=0, reason="empty"),
            RejectedView(index=2, reason="duplicate_in_call"),
        ),
    )


@pytest.mark.parametrize("count", [0, 11])
def test_item_count_is_one_to_ten(svc: Services, user_id: UUID, count: int) -> None:
    session_id = lesson_session(svc, user_id)
    items = [incoming(f"phrase number {n}") for n in range(count)]
    with pytest.raises(ServiceError) as info:
        save_glossary(svc, user_id, session_id, "confirmed", items)
    assert (info.value.code, info.value.fields) == ("validation_failed", ("items",))


def test_session_must_be_the_users(svc: Services, user_id: UUID, other_user_id: UUID) -> None:
    session_id = lesson_session(svc, user_id)
    onboard(svc, other_user_id)
    for who, which in ((other_user_id, session_id), (user_id, uuid4())):
        with pytest.raises(ServiceError) as info:
            save_glossary(svc, who, which, "confirmed", [incoming("ship it")])
        assert info.value.code == "session_not_found"


def test_saves_are_accepted_for_24_hours_after_the_session_ends(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    session_id = lesson_session(svc, user_id)
    with svc.uow(user_id) as uow:
        uow.sessions.mark_incomplete(session_id, clock.now)
    clock.advance(timedelta(hours=23))
    assert save_glossary(svc, user_id, session_id, "provisional", [incoming("follow up")]).new == 1
    clock.advance(timedelta(hours=2))
    with pytest.raises(ServiceError) as info:
        save_glossary(svc, user_id, session_id, "provisional", [incoming("circle back")])
    assert info.value.code == "session_closed"


def test_each_save_is_audited_with_ids_and_enums_only(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    # The gate's confirmation rate replays these rows, so it survives the provisional purge.
    session_id = lesson_session(svc, user_id)
    save_glossary(svc, user_id, session_id, "provisional", [incoming("keep you posted")])
    save_glossary(
        svc,
        user_id,
        session_id,
        "confirmed",
        [incoming("keep you posted"), incoming("ship it"), incoming("  !!  ")],
    )
    with svc.uow(user_id) as uow:
        rows = uow.glossary.by_norms(["keep you posted", "ship it"])
    posted, shipped = str(rows["keep you posted"].id), str(rows["ship it"].id)
    saved = [a.meta for a in store.tables.audit if a.event == "glossary_saved"]
    assert saved == [
        {
            "session_id": str(session_id),
            "status": "provisional",
            "items": [{"id": posted, "action": "insert", "status": "provisional"}],
        },
        {
            "session_id": str(session_id),
            "status": "confirmed",
            "items": [
                {"id": posted, "action": "promote", "status": "confirmed"},
                {"id": shipped, "action": "insert", "status": "confirmed"},
            ],
        },
    ]
    dumped = json.dumps(saved)
    for text in ("keep you posted", "ship it", "!!", MEANING, CONTEXT):
        assert text not in dumped


def test_provisional_items_from_one_lesson_are_decided_in_the_next(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    # Final review I1: start_lesson returns each provisional item with everything save_glossary
    # needs, so sending it back unchanged in the next lesson confirms or declines it.
    first = lesson_session(svc, user_id)
    keep = IncomingItem(
        kind="chunk",
        text="keep you posted",
        meaning="mantenerte al tanto",
        context_sentence="I will keep you posted about the deploy.",
        domain="it",
    )
    drop = IncomingItem(
        kind="term",
        text="synergy",
        meaning="sinergia",
        context_sentence="We need more synergy between teams.",
        domain="it",
    )
    assert save_glossary(svc, user_id, first, "provisional", [keep, drop]).new == 2
    clock.advance(timedelta(days=1))
    lesson = start_lesson(svc, user_id, StartLessonRequest(mode="text"))
    returned = {
        p.text: IncomingItem(
            kind=p.kind,
            text=p.text,
            meaning=p.meaning,
            context_sentence=p.context_sentence,
            domain=p.domain,
        )
        for p in lesson.provisional_items
    }
    assert returned == {keep.text: keep, drop.text: drop}
    kept = save_glossary(svc, user_id, lesson.session_id, "confirmed", [returned[keep.text]])
    dropped = save_glossary(svc, user_id, lesson.session_id, "declined", [returned[drop.text]])
    assert kept == GlossarySaveResult(new=0, reinforced=0, promoted=1, rejected=())
    assert dropped == GlossarySaveResult(new=0, reinforced=0, promoted=0, rejected=())
    with svc.uow(user_id) as uow:
        rows = uow.glossary.by_norms(["keep you posted", "synergy"])
        assert rows["keep you posted"].status == "confirmed"
        assert rows["synergy"].status == "declined"
        assert uow.glossary.count_provisional() == 0
