"""end_session on the in-memory store (spec 11.1-11.2, 10.3; Review Focus 2 and 4)."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from repo_contract import (
    DEFAULT_TURNS,
    add_closed_session,
    first_item,
    insert_glossary,
    present,
    sample_evidence,
)

from tutor.domain.fsrs import review
from tutor.domain.text import normalize
from tutor.domain.validation import Evidence, ReportedError
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.lesson import record_review, start_lesson
from tutor.services.memory import MemoryPlanRepo, MemorySessionRepo, MemoryStore
from tutor.services.profile import get_profile
from tutor.services.session_end import end_session
from tutor.services.views import LessonStart, StartLessonRequest

from .conftest import FixedClock, onboard

pytestmark = pytest.mark.unit

SAID = "I am blocked on the API keys"
CORRECT = "I'm blocked on the API keys"
RAW: dict[str, Any] = {"user_turns": list(DEFAULT_TURNS)}


@pytest.fixture
def write_calls(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """Counts the end_session writes that must happen at most once per session."""
    calls: Counter[str] = Counter()
    targets = (
        (MemorySessionRepo, "save_metrics"),
        (MemorySessionRepo, "save_errors"),
        (MemoryPlanRepo, "mark_done"),
    )
    for cls, name in targets:
        original = getattr(cls, name)

        def spy(
            self: Any, *args: Any, _original: Any = original, _name: str = name, **kwargs: Any
        ) -> Any:
            calls[_name] += 1
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, spy)
    return calls


def lesson_evidence(lesson: LessonStart, **changes: Any) -> Evidence:
    data: dict[str, Any] = {
        "errors": (ReportedError(said=SAID, correct=CORRECT, category="grammar"),),
        "chunks_used": (lesson.item.chunks[0].id, "it-99-c9"),
    }
    data.update(changes)
    return sample_evidence(**data)


def voice_lesson(svc: Services, user_id: UUID) -> LessonStart:
    return start_lesson(svc, user_id, StartLessonRequest(mode="voice"))


def run_lesson(svc: Services, clock: FixedClock, user_id: UUID) -> LessonStart:
    onboard(svc, user_id)
    lesson = voice_lesson(svc, user_id)
    clock.advance(timedelta(minutes=20))
    return lesson


def events(store: MemoryStore, user_id: UUID) -> list[str]:
    return [a.event for a in store.tables.audit if a.user_id == user_id]


def plan_item_of(svc: Services, user_id: UUID, session_id: UUID, store: MemoryStore) -> Any:
    with svc.uow(user_id) as uow:
        plan_item_id = present(present(uow.sessions.get(session_id)).plan_item_id)
    return store.tables.plan_items[plan_item_id]


def test_end_session_closes_the_session_and_reports(
    svc: Services, clock: FixedClock, store: MemoryStore, user_id: UUID
) -> None:
    lesson = run_lesson(svc, clock, user_id)
    result = end_session(svc, user_id, lesson.session_id, lesson_evidence(lesson), RAW)
    assert (result.status, result.low_trust, result.already_closed) == ("closed", False, False)
    assert (result.errors_rejected, result.chunks_rejected) == (0, 1)
    assert (result.metrics.errors_total, result.metrics.chunks_used) == (1, 1)
    assert result.metrics.duration_min == 20.0
    assert result.streak == 1
    assert len(result.summary_text.splitlines()) == 4
    with svc.uow(user_id) as uow:
        session = present(uow.sessions.get(lesson.session_id))
        assert (session.status, session.ended_at) == ("closed", clock.now)
        assert session.result == result.to_json()
        norms = uow.sessions.recent_correct_norms(clock.now - timedelta(days=30), uuid4())
        assert norms == frozenset({normalize(CORRECT)})
        assert uow.sessions.previous_cefr() == "B1"
    item = plan_item_of(svc, user_id, lesson.session_id, store)
    assert (item.status, item.done_session_id) == ("done", lesson.session_id)
    assert lesson.session_id in store.tables.metrics
    assert events(store, user_id)[-1] == "session_closed"
    assert store.tables.audit[-1].meta == {
        "session_id": str(lesson.session_id),
        "status": "closed",
        "low_trust": False,
    }


def test_too_few_words_end_incomplete_and_keep_the_plan_item(
    svc: Services, clock: FixedClock, store: MemoryStore, user_id: UUID
) -> None:
    lesson = run_lesson(svc, clock, user_id)
    short = sample_evidence(turns=("Yes.", "Okay, sure."))
    result = end_session(svc, user_id, lesson.session_id, short, {"user_turns": ["Yes."]})
    assert (result.status, result.streak) == ("incomplete", 0)
    assert store.tables.errors == []
    assert plan_item_of(svc, user_id, lesson.session_id, store).status == "pending"
    again = end_session(svc, user_id, lesson.session_id, short, {"user_turns": ["Yes."]})
    assert again == replace(result, already_closed=True)


def test_unknown_or_foreign_session_is_not_found(
    svc: Services, clock: FixedClock, user_id: UUID, other_user_id: UUID
) -> None:
    lesson = run_lesson(svc, clock, user_id)
    onboard(svc, other_user_id)
    for who, which in ((user_id, uuid4()), (other_user_id, lesson.session_id)):
        with pytest.raises(ServiceError) as info:
            end_session(svc, who, which, lesson_evidence(lesson), RAW)
        assert info.value.code == "session_not_found"


def test_oversized_raw_evidence_is_refused_before_any_write(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    lesson = run_lesson(svc, clock, user_id)
    with pytest.raises(ServiceError) as info:
        end_session(
            svc,
            user_id,
            lesson.session_id,
            lesson_evidence(lesson),
            {"user_turns": ["word " * 5000]},
        )
    assert info.value.code == "payload_too_large"
    with svc.uow(user_id) as uow:
        assert present(uow.sessions.get(lesson.session_id)).status == "open"


def test_streak_counts_yesterday_and_this_session(
    svc: Services, clock: FixedClock, user_id: UUID, now: datetime
) -> None:
    onboard(svc, user_id)
    with svc.uow(user_id) as uow:
        yesterday = now - timedelta(days=1)
        add_closed_session(uow, first_item(uow), yesterday, yesterday + timedelta(minutes=20))
    lesson = voice_lesson(svc, user_id)
    clock.advance(timedelta(minutes=20))
    result = end_session(svc, user_id, lesson.session_id, lesson_evidence(lesson), RAW)
    assert result.streak == 2
    assert result.summary_text.splitlines()[3].startswith("Streak: 2")


def test_retry_with_the_same_payload_returns_the_stored_result(
    svc: Services,
    clock: FixedClock,
    store: MemoryStore,
    user_id: UUID,
    write_calls: Counter[str],
) -> None:
    # Review Focus 2
    lesson = run_lesson(svc, clock, user_id)
    evidence = lesson_evidence(lesson)
    first = end_session(svc, user_id, lesson.session_id, evidence, RAW)
    ended_at = clock.now
    clock.advance(timedelta(minutes=3))
    again = end_session(svc, user_id, lesson.session_id, evidence, RAW)
    assert again == replace(first, already_closed=True)
    assert write_calls == Counter({"save_metrics": 1, "save_errors": 1, "mark_done": 1})
    assert events(store, user_id).count("session_closed") == 1
    assert len(store.tables.errors) == 1
    assert get_profile(svc, user_id).streak == 1
    with svc.uow(user_id) as uow:
        assert present(uow.sessions.get(lesson.session_id)).ended_at == ended_at
    item = plan_item_of(svc, user_id, lesson.session_id, store)
    assert (item.status, item.done_session_id) == ("done", lesson.session_id)


def test_retry_with_a_different_payload_returns_the_first_result(
    svc: Services, clock: FixedClock, user_id: UUID, write_calls: Counter[str]
) -> None:
    # Review Focus 2
    lesson = run_lesson(svc, clock, user_id)
    first = end_session(svc, user_id, lesson.session_id, lesson_evidence(lesson), RAW)
    longer = (*DEFAULT_TURNS, "One more turn with plenty of extra words to change every metric.")
    other = lesson_evidence(lesson, turns=longer, errors=(), chunks_used=())
    again = end_session(svc, user_id, lesson.session_id, other, {"user_turns": list(longer)})
    assert again.already_closed
    assert (again.metrics, again.summary_text, again.streak) == (
        first.metrics,
        first.summary_text,
        first.streak,
    )
    assert write_calls["save_metrics"] == 1


def test_end_session_for_a_replaced_session_is_session_closed(
    svc: Services,
    clock: FixedClock,
    store: MemoryStore,
    user_id: UUID,
    write_calls: Counter[str],
) -> None:
    # Review Focus 2
    onboard(svc, user_id)
    replaced = voice_lesson(svc, user_id)
    clock.advance(timedelta(minutes=5))
    current = voice_lesson(svc, user_id)
    clock.advance(timedelta(minutes=20))
    with pytest.raises(ServiceError) as early:
        end_session(svc, user_id, replaced.session_id, lesson_evidence(replaced), RAW)
    assert early.value.code == "session_closed"
    assert write_calls == Counter()
    with svc.uow(user_id) as uow:
        row = present(uow.sessions.get(replaced.session_id))
        assert (row.status, row.result) == ("incomplete", None)
    closed = end_session(svc, user_id, current.session_id, lesson_evidence(current), RAW)
    assert closed.status == "closed"
    with pytest.raises(ServiceError) as late:
        end_session(svc, user_id, replaced.session_id, lesson_evidence(replaced), RAW)
    assert late.value.code == "session_closed"
    assert write_calls == Counter({"save_metrics": 1, "save_errors": 1, "mark_done": 1})
    item = plan_item_of(svc, user_id, current.session_id, store)
    assert (item.status, item.done_session_id) == ("done", current.session_id)


def pending_base_ids(svc: Services, user_id: UUID, track_item_id: str) -> list[UUID]:
    with svc.uow(user_id) as uow:
        plan = present(uow.plans.active())
    return [
        i.id
        for i in plan.items
        if i.track_item_id == track_item_id and i.variant == "base" and i.status == "pending"
    ]


def test_superseded_plan_item_is_still_marked_done(
    svc: Services, clock: FixedClock, store: MemoryStore, user_id: UUID
) -> None:
    # Review Focus 4: the profile changes while the session is open.
    lesson = run_lesson(svc, clock, user_id)
    with svc.uow(user_id) as uow:
        plan_item_id = present(present(uow.sessions.get(lesson.session_id)).plan_item_id)
    changed = onboard(svc, user_id, use_cases=["incident", "demo"])
    assert (changed.plan_changed, changed.plan.version) == (True, 2)
    assert pending_base_ids(svc, user_id, lesson.item.id)  # offered again until the session ends
    result = end_session(svc, user_id, lesson.session_id, lesson_evidence(lesson), RAW)
    assert result.status == "closed"
    old_item = store.tables.plan_items[plan_item_id]
    assert (old_item.status, old_item.done_session_id) == ("done", lesson.session_id)
    with svc.uow(user_id) as uow:
        assert lesson.item.id in uow.plans.done_base_track_ids()
        assert present(uow.plans.active()).version == 2
    # The active plan no longer offers that track item's base variant.
    assert pending_base_ids(svc, user_id, lesson.item.id) == []
    onboard(svc, user_id, use_cases=["incident", "demo"], days_per_week=4)
    with svc.uow(user_id) as uow:
        plan = present(uow.plans.active())
    assert plan.version == 3
    assert (lesson.item.id, "base") not in {(i.track_item_id, i.variant) for i in plan.items}


def test_incomplete_session_with_a_superseded_plan_marks_nothing(
    svc: Services, clock: FixedClock, store: MemoryStore, user_id: UUID, write_calls: Counter[str]
) -> None:
    lesson = run_lesson(svc, clock, user_id)
    onboard(svc, user_id, use_cases=["incident", "demo"])
    short = sample_evidence(turns=("Yes.", "Okay, sure."))
    result = end_session(svc, user_id, lesson.session_id, short, {"user_turns": ["Yes."]})
    assert result.status == "incomplete"
    assert write_calls["mark_done"] == 0
    assert pending_base_ids(svc, user_id, lesson.item.id)
    assert all(i.status != "done" for i in store.tables.plan_items.values())


def test_reviewed_item_used_again_is_upgraded_to_rating_four(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    onboard(svc, user_id)
    lesson = voice_lesson(svc, user_id)
    with svc.uow(user_id) as uow:
        row = insert_glossary(
            uow,
            lesson.session_id,
            "roll back the deploy",
            clock.now,
            kind="chunk",
            first_due=clock.now,
        )
    clock.advance(timedelta(minutes=2))
    record_review(svc, user_id, lesson.session_id, [(row.id, 3)])
    clock.advance(timedelta(minutes=18))
    turns = (
        "First I would roll back the deploy and then check the error rate on the dashboard.",
        "The client asked why the checkout page failed for about ten minutes this morning.",
        "If the error rate grows again we roll back the deploy before we try another fix.",
    )
    end_session(svc, user_id, lesson.session_id, sample_evidence(turns=turns), RAW)
    with svc.uow(user_id) as uow:
        (log,) = uow.reviews.session_logs(lesson.session_id)
        assert log.rating == 4
        assert uow.reviews.state(row.id) == review(log.state_before, 4, log.reviewed_at)


def test_reviewed_item_used_once_keeps_rating_three(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    onboard(svc, user_id)
    lesson = voice_lesson(svc, user_id)
    with svc.uow(user_id) as uow:
        row = insert_glossary(
            uow,
            lesson.session_id,
            "roll back the deploy",
            clock.now,
            kind="chunk",
            first_due=clock.now,
        )
    clock.advance(timedelta(minutes=2))
    record_review(svc, user_id, lesson.session_id, [(row.id, 3)])
    with svc.uow(user_id) as uow:
        after_review = uow.reviews.state(row.id)
    clock.advance(timedelta(minutes=18))
    end_session(svc, user_id, lesson.session_id, sample_evidence(), RAW)
    with svc.uow(user_id) as uow:
        assert [log.rating for log in uow.reviews.session_logs(lesson.session_id)] == [3]
        assert uow.reviews.state(row.id) == after_review
