"""Practice plans and sessions on the in-memory store (subjects spec 6, 15.4.1, 15.8, 16.4;
slice B plan rulings 28-36 and 63, Review Focus 2-6)."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from template_fixtures import (
    CHOICE,
    FLASHCARD,
    HTML,
    KEY_NAME,
    LEAKS,
    NUMERIC,
    POOL_NAME,
    TABLE,
    choice_params,
    flashcard_params,
    numeric_params,
    param,
    table_params,
)

from tutor.domain.notes import InstanceInput
from tutor.domain.practice import KindResult
from tutor.domain.templates import TEMPLATE_GUIDE_VERSION, ParamPair, TemplateInput
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.memory import MemoryStore, PracticeItemRecord, PracticeSessionRecord
from tutor.services.ports import PracticeSessionRow, UnitOfWork
from tutor.services.practice import (
    counts_toward_plan,
    end_practice,
    history,
    next_exercise,
    record_result,
    show_exercise,
    start_practice,
)
from tutor.services.subject_views import PracticeItemView
from tutor.services.subjects import get_subjects, save_subject
from tutor.services.templates import create_instance, save_template

from .conftest import NOW, FixedClock, create_subject, subject_input

pytestmark = pytest.mark.unit

SELF_REPORT = TemplateInput(
    name="Práctica libre",
    description=None,
    template_purpose="exercise",
    grading_kind="self_report",
    params=(param("task", "text"),),
)
EAR = dataclasses.replace(
    FLASHCARD, name="Solo con vista", needs_view=True, text_match=None, grading_kind="exact_text"
)


def events(store: MemoryStore, user_id: UUID, event: str) -> list[dict[str, Any]]:
    return [a.meta for a in store.tables.audit if a.user_id == user_id and a.event == event]


def template(svc: Services, user_id: UUID, subject_id: UUID, data: TemplateInput) -> UUID:
    return save_template(svc, user_id, subject_id, data, HTML, TEMPLATE_GUIDE_VERSION).template_id


def instance(
    svc: Services,
    user_id: UUID,
    subject_id: UUID,
    template_id: UUID,
    params: tuple[ParamPair, ...],
    prompt: str = "Resuelve.",
) -> UUID:
    data = InstanceInput(template_id=template_id, params=params, prompt_text=prompt)
    return create_instance(svc, user_id, subject_id, data).instance_id


def quiz_subject(svc: Services, user_id: UUID, *, quizzes: int = 3, **changes: Any) -> UUID:
    """A subject with `quizzes` numeric instances (answers 1, 2, ...) and nothing else."""
    subject_id = create_subject(svc, user_id, exercise_kinds=["quiz"], **changes)
    numeric = template(svc, user_id, subject_id, NUMERIC)
    for n in range(1, quizzes + 1):
        instance(svc, user_id, subject_id, numeric, numeric_params(float(n)), f"Pregunta {n}.")
    return subject_id


def stored_item(store: MemoryStore, item_id: UUID) -> PracticeItemRecord:
    return store.tables.practice_items[item_id]


def right_numeric(store: MemoryStore, item: PracticeItemView) -> dict[str, Any]:
    value = store.tables.template_instances[item.instance_id].params[KEY_NAME]
    return {"answer_kind": "number", "value": str(value)}


def fields_of(info: pytest.ExceptionInfo[ServiceError]) -> tuple[str, tuple[str, ...]]:
    return info.value.code, info.value.fields


def no_leaks(*texts: str | None) -> None:
    for text in texts:
        for leak in LEAKS:
            assert leak not in (text or "")


# --- plans (ruling 29) --------------------------------------------------------------------------


def test_review_focus_2_a_plan_per_subject_versioned_on_input_changes(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    target = date(2026, 11, 11)
    created = save_subject(svc, user_id, subject_input(target_date=target))
    assert created.plan_changed is True
    subject_id = created.subject.id
    status = created.subject.plan_status
    assert status is not None
    assert (status.version, status.start_date, status.total_sessions) == (1, date(2026, 10, 14), 12)
    assert (status.expected_sessions, status.missed, status.projected_end) == (0, 0, target)
    clock.advance(timedelta(days=10))
    [view] = get_subjects(svc, user_id).subjects
    assert view.plan_status is not None
    assert (view.plan_status.expected_sessions, view.plan_status.missed) == (3, 3)
    assert view.plan_status.projected_end == target + timedelta(days=7)
    assert view.plan_status.delay_days == 7
    # The same inputs again: no new version.
    same = save_subject(
        svc, user_id, subject_input(target_date=target, goal_text="Tocar"), subject_id
    )
    assert same.plan_changed is False
    assert same.subject.plan_status is not None and same.subject.plan_status.version == 1
    changed = save_subject(
        svc, user_id, subject_input(target_date=target, sessions_per_week=4), subject_id
    )
    assert changed.plan_changed is True
    [view] = get_subjects(svc, user_id).subjects
    assert view.plan_status is not None
    assert (view.plan_status.version, view.plan_status.start_date) == (2, date(2026, 10, 24))
    assert (view.plan_status.missed, view.plan_status.completed_sessions) == (0, 0)
    plans = events(store, user_id, "practice_plan_created")
    assert plans == [
        {
            "subject_id": str(subject_id),
            "version": 1,
            "reason": "created",
            "plan_id": plans[0]["plan_id"],
        },
        {
            "subject_id": str(subject_id),
            "version": 2,
            "reason": "inputs_changed",
            "plan_id": plans[1]["plan_id"],
        },
    ]
    saved = events(store, user_id, "subject_saved")
    assert [m["plan_changed"] for m in saved] == [True, False, True]
    rationale = [p.rationale for p in store.tables.practice_plans.values()]
    assert sorted(rationale, key=str) == sorted(
        [{"reason": "created"}, {"reason": "inputs_changed", "previous_version": 1}], key=str
    )


def test_a_subject_without_a_plan_gets_one_on_its_next_save(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    store.tables.practice_plans.clear()  # a subject saved before slice B
    [view] = get_subjects(svc, user_id).subjects
    assert view.plan_status is None
    result = save_subject(svc, user_id, subject_input(), subject_id)
    assert result.plan_changed is True
    assert result.subject.plan_status is not None and result.subject.plan_status.version == 1


def test_plan_status_counts_closed_sessions_of_the_current_version(
    svc: Services, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = quiz_subject(svc, user_id)
    started = start_practice(svc, user_id, subject_id, "text")
    end_practice(svc, user_id, started.session_id)
    [view] = get_subjects(svc, user_id).subjects
    assert view.plan_status is not None and view.plan_status.completed_sessions == 1
    save_subject(
        svc, user_id, subject_input(exercise_kinds=["quiz"], minutes_per_session=45), subject_id
    )
    [view] = get_subjects(svc, user_id).subjects
    assert view.plan_status is not None
    assert (view.plan_status.version, view.plan_status.completed_sessions) == (2, 0)


# --- start_practice (rulings 30, 31, 34, 35) ----------------------------------------------------


def test_review_focus_3_a_subject_without_instances_cannot_start(
    svc: Services, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["quiz", "exercise"])
    with pytest.raises(ServiceError) as info:
        start_practice(svc, user_id, subject_id, "app")
    assert fields_of(info) == ("validation_failed", ("subject_id",))


def test_review_focus_3_with_drill_enabled_a_pool_alone_starts(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["quiz", "drill"])
    flash = template(svc, user_id, subject_id, FLASHCARD)
    pool_id = instance(svc, user_id, subject_id, flash, flashcard_params(6))
    started = start_practice(svc, user_id, subject_id, "text")
    assert (started.item_count, started.short) == (10, False)
    items = [i for i in store.tables.practice_items.values() if i.session_id == started.session_id]
    assert len(items) == 10 and {i.instance_id for i in items} == {pool_id}
    assert started.first is not None
    assert (started.first.practice_kind, started.first.seq) == ("drill", 1)


def test_instances_of_disabled_kinds_or_displays_do_not_count(svc: Services, user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["quiz"])
    flash = template(svc, user_id, subject_id, FLASHCARD)
    instance(svc, user_id, subject_id, flash, flashcard_params(6))
    table = template(svc, user_id, subject_id, TABLE)
    instance(svc, user_id, subject_id, table, table_params())
    with pytest.raises(ServiceError) as info:
        start_practice(svc, user_id, subject_id, "app")
    assert fields_of(info) == ("validation_failed", ("subject_id",))


def test_ruling_34_text_mode_skips_items_that_need_the_view(svc: Services, user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["drill"])
    ear = template(svc, user_id, subject_id, EAR)
    instance(svc, user_id, subject_id, ear, flashcard_params(6))
    with pytest.raises(ServiceError) as info:
        start_practice(svc, user_id, subject_id, "text")
    assert fields_of(info) == ("validation_failed", ("mode",))
    started = start_practice(svc, user_id, subject_id, "app")
    assert started.item_count == 10


def test_start_validates_the_mode_and_the_subject(
    svc: Services, user_id: UUID, other_user_id: UUID
) -> None:
    subject_id = quiz_subject(svc, user_id)
    with pytest.raises(ServiceError) as info:
        start_practice(svc, user_id, subject_id, "class")  # type: ignore[arg-type]
    assert fields_of(info) == ("validation_failed", ("mode",))
    for who, subject in ((user_id, uuid4()), (other_user_id, subject_id)):
        with pytest.raises(ServiceError) as info:
            start_practice(svc, who, subject, "app")
        assert fields_of(info) == ("not_found", ("subject_id",))


def test_ruling_35_a_short_session_starts_with_what_exists(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = quiz_subject(svc, user_id, quizzes=2)
    started = start_practice(svc, user_id, subject_id, "text")
    assert (started.item_count, started.short) == (2, True)
    assert events(store, user_id, "practice_started") == [
        {
            "session_id": str(started.session_id),
            "subject_id": str(subject_id),
            "practice_mode": "text",
            "item_count": 2,
            "short": True,
        }
    ]


def test_the_mix_follows_preference_and_capacity(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["quiz", "drill", "exercise"])
    numeric = template(svc, user_id, subject_id, NUMERIC)
    for n in range(5):
        instance(svc, user_id, subject_id, numeric, numeric_params(float(n)), f"P{n}.")
    flash = template(svc, user_id, subject_id, FLASHCARD)
    instance(svc, user_id, subject_id, flash, flashcard_params(6))
    practice = template(svc, user_id, subject_id, SELF_REPORT)
    instance(svc, user_id, subject_id, practice, (ParamPair("task", "text", "Toca"),))
    started = start_practice(svc, user_id, subject_id, "text")
    kinds = [
        store.tables.template_instances[i.instance_id].template.practice_kind
        for i in store.tables.practice_items.values()
        if i.session_id == started.session_id
    ]
    # balanced over 10: quiz 4, drill 3, exercise 3; the one exercise instance leaves 2 items,
    # which go to quiz and drill by their shares (40:30, largest remainder first).
    assert sorted(str(k) for k in kinds) == ["drill"] * 4 + ["exercise"] + ["quiz"] * 5


def test_start_returns_the_first_item_shown_with_its_view_and_no_answer_key(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = quiz_subject(svc, user_id)
    started = start_practice(svc, user_id, subject_id, "app")
    first = started.first
    assert first is not None
    assert stored_item(store, first.item_id).shown_at == NOW  # ruling 63
    others = [
        i
        for i in store.tables.practice_items.values()
        if i.session_id == started.session_id and i.id != first.item_id
    ]
    assert others and all(i.shown_at is None for i in others)
    assert first.document is not None and 'id="tutor-params"' in first.document
    assert first.text.startswith("Pregunta ")
    assert (first.grading_kind, first.practice_kind, first.needs_view) == ("numeric", "quiz", False)
    no_leaks(first.document, first.text)
    text = start_practice(svc, user_id, subject_id, "text").first
    assert text is not None and text.document is None  # text mode: no document


def test_ruling_30_a_new_start_marks_the_open_session_incomplete(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = quiz_subject(svc, user_id)
    first = start_practice(svc, user_id, subject_id, "text")
    clock.advance(timedelta(minutes=5))
    second = start_practice(svc, user_id, subject_id, "text")
    old = store.tables.practice_sessions[first.session_id]
    assert (old.status, old.ended_at) == ("incomplete", NOW + timedelta(minutes=5))
    assert store.tables.practice_sessions[second.session_id].status == "open"
    assert events(store, user_id, "practice_incomplete") == [
        {"session_id": str(first.session_id), "subject_id": str(subject_id)}
    ]


# --- next_exercise and record_result (rulings 33, 36, 63) ---------------------------------------


def test_review_focus_6_time_is_the_servers(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = quiz_subject(svc, user_id)
    clock.now = NOW.replace(hour=10, minute=0, second=0)
    started = start_practice(svc, user_id, subject_id, "text")
    first = started.first
    assert first is not None
    clock.advance(timedelta(seconds=30))
    again = next_exercise(svc, user_id, started.session_id)
    assert again.item is not None and again.item.item_id == first.item_id
    assert stored_item(store, first.item_id).shown_at == NOW.replace(hour=10, minute=0, second=0)
    clock.advance(timedelta(seconds=11))
    graded = record_result(
        svc, user_id, started.session_id, first.item_id, right_numeric(store, first)
    )
    assert (graded.outcome, graded.correct, graded.score) == ("new", True, 100)
    item = stored_item(store, first.item_id)
    assert item.answered_at is not None and item.shown_at is not None
    assert (item.answered_at - item.shown_at).total_seconds() == 41
    # An item never handed out cannot be answered.
    unshown = next(
        i
        for i in store.tables.practice_items.values()
        if i.session_id == started.session_id and i.shown_at is None
    )
    with pytest.raises(ServiceError) as info:
        record_result(
            svc, user_id, started.session_id, unshown.id, {"answer_kind": "number", "value": "1"}
        )
    assert fields_of(info) == ("validation_failed", ("item_id",))
    assert stored_item(store, unshown.id).answered_at is None


def test_next_exercise_walks_the_items_then_says_done(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = quiz_subject(svc, user_id, quizzes=2)
    started = start_practice(svc, user_id, subject_id, "text")
    seen = []
    step = next_exercise(svc, user_id, started.session_id)
    while not step.done:
        assert step.item is not None
        seen.append(step.item.seq)
        record_result(
            svc, user_id, started.session_id, step.item.item_id, right_numeric(store, step.item)
        )
        step = next_exercise(svc, user_id, started.session_id)
    assert seen == [1, 2]
    assert (step.item, step.answered, step.remaining) == (None, 2, 0)


def test_record_result_is_idempotent_per_item(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = quiz_subject(svc, user_id)
    started = start_practice(svc, user_id, subject_id, "text")
    assert started.first is not None
    item_id = started.first.item_id
    wrong = {"answer_kind": "number", "value": "999"}
    first = record_result(svc, user_id, started.session_id, item_id, wrong)
    assert (first.outcome, first.correct, first.score) == ("new", False, 0)
    assert dict(first.reveal) == {"zq_reveal_text": "ZQ-HIDDEN-REVEAL"}
    clock.advance(timedelta(seconds=5))
    again = record_result(
        svc, user_id, started.session_id, item_id, right_numeric(store, started.first)
    )
    assert (again.outcome, again.correct, again.score) == ("existing", False, 0)
    assert dict(again.reveal) == dict(first.reveal)
    stored = stored_item(store, item_id)
    assert (stored.answer, stored.answered_at, stored.source) == (
        {"answer_kind": "number", "value": "999"},
        NOW,
        "chat",
    )


def test_review_focus_4_answer_shapes_are_validated_and_nothing_is_stored(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["quiz"])
    choice = template(svc, user_id, subject_id, CHOICE)
    instance(svc, user_id, subject_id, choice, choice_params(key=1))
    started = start_practice(svc, user_id, subject_id, "text")
    assert started.first is not None
    session_id, item_id = started.session_id, started.first.item_id
    cases: list[tuple[dict[str, Any], str]] = [
        ({"answer_kind": "choice_index", "option_number": 7}, "answer.option_number"),
        ({"answer_kind": "choice_index", "option_index": 1}, "answer.option_index"),
        ({"answer_kind": "value", "value": "dos"}, "answer"),
        ({"answer_kind": "self_report", "rating": 5}, "answer"),
    ]
    for answer, field in cases:
        with pytest.raises(ServiceError) as info:
            record_result(svc, user_id, session_id, item_id, answer)
        assert fields_of(info) == ("validation_failed", (field,))
    assert stored_item(store, item_id).answered_at is None
    # The view may send the 0-based index; chat sends the printed number.
    graded = record_result(
        svc, user_id, session_id, item_id, {"answer_kind": "choice_index", "option_number": 2}
    )
    assert (graded.correct, graded.score) == (True, 100)
    assert stored_item(store, item_id).answer == {"answer_kind": "choice_index", "option_index": 1}


def test_an_answer_from_the_view_may_use_the_index(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["quiz"])
    choice = template(svc, user_id, subject_id, CHOICE)
    instance(svc, user_id, subject_id, choice, choice_params(key=2))
    started = start_practice(svc, user_id, subject_id, "app")
    assert started.first is not None
    answer = {"answer_kind": "choice_index", "option_index": 2}
    graded = record_result(
        svc, user_id, started.session_id, started.first.item_id, answer, source="app"
    )
    assert graded.correct is True
    assert stored_item(store, started.first.item_id).source == "app"


def test_a_pool_drill_is_graded_against_its_stored_draw(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["drill"])
    flash = template(svc, user_id, subject_id, FLASHCARD)
    instance(svc, user_id, subject_id, flash, flashcard_params(6))
    started = start_practice(svc, user_id, subject_id, "text")
    first = started.first
    assert first is not None
    no_leaks(first.text)
    stored = stored_item(store, first.item_id)
    entries = store.tables.template_instances[first.instance_id].params[POOL_NAME]
    answer = entries[stored.draw.entries[0]]["answer"]
    graded = record_result(
        svc, user_id, started.session_id, first.item_id, {"answer_kind": "value", "value": answer}
    )
    assert (graded.correct, graded.score) == (True, 100)
    assert "explain" in graded.reveal


def test_review_focus_5_foreign_or_mismatched_ids_are_not_found(
    svc: Services, store: MemoryStore, user_id: UUID, other_user_id: UUID
) -> None:
    mine = quiz_subject(svc, user_id)
    theirs = quiz_subject(svc, other_user_id)
    my_session = start_practice(svc, user_id, mine, "text")
    their_session = start_practice(svc, other_user_id, theirs, "text")
    assert my_session.first is not None and their_session.first is not None
    answer = {"answer_kind": "number", "value": "1"}
    attempts = [
        (my_session.session_id, their_session.first.item_id, "item_id"),
        (their_session.session_id, their_session.first.item_id, "session_id"),
        (uuid4(), my_session.first.item_id, "session_id"),
        (my_session.session_id, uuid4(), "item_id"),
    ]
    for session_id, item_id, field in attempts:
        with pytest.raises(ServiceError) as info:
            record_result(svc, user_id, session_id, item_id, answer)
        assert fields_of(info) == ("not_found", (field,))
    for call in (next_exercise, end_practice):
        with pytest.raises(ServiceError) as info:
            call(svc, user_id, their_session.session_id)
        assert fields_of(info) == ("not_found", ("session_id",))
    assert stored_item(store, their_session.first.item_id).answered_at is None


def test_a_closed_session_takes_no_new_answers(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = quiz_subject(svc, user_id)
    started = start_practice(svc, user_id, subject_id, "text")
    assert started.first is not None
    end_practice(svc, user_id, started.session_id)
    with pytest.raises(ServiceError) as info:
        record_result(
            svc,
            user_id,
            started.session_id,
            started.first.item_id,
            right_numeric(store, started.first),
        )
    assert fields_of(info) == ("session_closed", ("session_id",))
    with pytest.raises(ServiceError) as info:
        next_exercise(svc, user_id, started.session_id)
    assert fields_of(info) == ("session_closed", ("session_id",))


# --- end_practice (ruling 36; spec 16.13 Q2) ----------------------------------------------------


def test_end_practice_summarises_closes_and_is_idempotent(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = quiz_subject(svc, user_id, quizzes=4)
    started = start_practice(svc, user_id, subject_id, "text")
    for score_right in (True, False):
        step = next_exercise(svc, user_id, started.session_id)
        assert step.item is not None
        answer = (
            right_numeric(store, step.item)
            if score_right
            else {"answer_kind": "number", "value": "-1"}
        )
        record_result(svc, user_id, started.session_id, step.item.item_id, answer)
    clock.advance(timedelta(minutes=12, seconds=30))
    summary = end_practice(svc, user_id, started.session_id, self_rating=3)
    assert (summary.items, summary.answered, summary.unanswered, summary.minutes) == (4, 2, 2, 12)
    assert summary.by_kind == (KindResult("quiz", items=4, answered=2, correct=1, mean_score=50),)
    assert (summary.self_rating, summary.short) == (3, False)
    assert summary.streak_days == 1
    assert summary.plan_status is not None and summary.plan_status.completed_sessions == 1
    session = store.tables.practice_sessions[started.session_id]
    assert (session.status, session.self_rating, session.ended_at) == (
        "closed",
        3,
        NOW + timedelta(minutes=12, seconds=30),
    )
    ended = events(store, user_id, "practice_ended")
    assert ended == [
        {
            "session_id": str(started.session_id),
            "subject_id": str(subject_id),
            "items": 4,
            "answered": 2,
            "minutes": 12,
        }
    ]
    clock.advance(timedelta(hours=1))
    again = end_practice(svc, user_id, started.session_id, self_rating=1)
    assert dataclasses.replace(again, plan_status=None) == dataclasses.replace(
        summary, plan_status=None
    )
    assert len(events(store, user_id, "practice_ended")) == 1


def test_end_practice_validates_the_rating_and_refuses_an_incomplete_session(
    svc: Services, user_id: UUID
) -> None:
    subject_id = quiz_subject(svc, user_id)
    started = start_practice(svc, user_id, subject_id, "text")
    for rating in (0, 5):
        with pytest.raises(ServiceError) as info:
            end_practice(svc, user_id, started.session_id, self_rating=rating)
        assert fields_of(info) == ("validation_failed", ("self_rating",))
    start_practice(svc, user_id, subject_id, "text")  # the first one becomes incomplete
    with pytest.raises(ServiceError) as info:
        end_practice(svc, user_id, started.session_id)
    assert fields_of(info) == ("session_closed", ("session_id",))


# --- show_exercise (spec 15.9, 15.13 D5) --------------------------------------------------------


def test_show_exercise_opens_a_class_session_and_appends_showings(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["drill"])
    flash = template(svc, user_id, subject_id, FLASHCARD)
    pool_id = instance(svc, user_id, subject_id, flash, flashcard_params(3))
    shown = [show_exercise(svc, user_id, subject_id, pool_id) for _ in range(3)]
    assert len({s.session_id for s in shown}) == 1
    session = store.tables.practice_sessions[shown[0].session_id]
    assert (session.mode, session.status, session.item_count) == ("class", "open", 0)
    items = [stored_item(store, s.item.item_id) for s in shown]
    assert [i.seq for i in items] == [1, 2, 3]
    assert all(i.shown_at == NOW for i in items)
    assert sorted(i.draw.entries[0] for i in items) == [0, 1, 2]  # no entry asked twice
    assert shown[0].item.document is not None
    no_leaks(shown[0].item.document, shown[0].item.text)
    # A class session counts toward attendance once an item is answered, even while open.
    [view] = get_subjects(svc, user_id).subjects
    assert view.plan_status is not None and view.plan_status.completed_sessions == 0
    entries = store.tables.template_instances[pool_id].params[POOL_NAME]
    answer = entries[items[0].draw.entries[0]]["answer"]
    record_result(
        svc, user_id, shown[0].session_id, items[0].id, {"answer_kind": "value", "value": answer}
    )
    [view] = get_subjects(svc, user_id).subjects
    assert view.plan_status is not None and view.plan_status.completed_sessions == 1


def test_show_exercise_joins_the_subjects_open_session_or_replaces_another(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    first = quiz_subject(svc, user_id)
    second = quiz_subject(svc, user_id, name="Historia")
    quiz_id = next(i.id for i in store.tables.template_instances.values() if i.subject_id == first)
    started = start_practice(svc, user_id, first, "text")
    joined = show_exercise(svc, user_id, first, quiz_id)
    assert joined.session_id == started.session_id
    assert stored_item(store, joined.item.item_id).seq == started.item_count + 1
    other_quiz = next(
        i.id for i in store.tables.template_instances.values() if i.subject_id == second
    )
    moved = show_exercise(svc, user_id, second, other_quiz)
    assert moved.session_id != started.session_id
    assert store.tables.practice_sessions[started.session_id].status == "incomplete"


def test_show_exercise_refuses_displays_and_foreign_instances(
    svc: Services, user_id: UUID, other_user_id: UUID
) -> None:
    subject_id = quiz_subject(svc, user_id)
    other_subject = quiz_subject(svc, user_id, name="Historia")
    table = template(svc, user_id, subject_id, TABLE)
    display = instance(svc, user_id, subject_id, table, table_params())
    with pytest.raises(ServiceError) as info:
        show_exercise(svc, user_id, subject_id, display)
    assert fields_of(info) == ("validation_failed", ("instance_id",))
    theirs = quiz_subject(svc, other_user_id)
    for subject, instance_id, field in (
        (subject_id, uuid4(), "instance_id"),
        (uuid4(), display, "subject_id"),
        (theirs, display, "subject_id"),
    ):
        with pytest.raises(ServiceError) as info:
            show_exercise(svc, user_id, subject, instance_id)
        assert fields_of(info) == ("not_found", (field,))
    with pytest.raises(ServiceError) as info:
        show_exercise(svc, user_id, other_subject, display)  # an instance of another subject
    assert fields_of(info) == ("not_found", ("instance_id",))


# --- a concurrent open session (security review P3) ---------------------------------------------


def racing(svc: Services, store: MemoryStore, *, every_time: bool) -> Services:
    """`svc` whose create_session first meets an open session that another call committed in
    between (once, or on every attempt)."""
    base = svc.uow

    @contextmanager
    def unit_of_work(user_id: UUID) -> Iterator[UnitOfWork]:
        with base(user_id) as uow:
            create = uow.practice.create_session
            calls: list[int] = []

            def competing(session_id: UUID, **kwargs: Any) -> Any:
                calls.append(1)
                if every_time or len(calls) == 1:
                    rival = PracticeSessionRecord(
                        id=uuid4(),
                        user_id=user_id,
                        subject_id=kwargs["subject_id"],
                        plan_id=None,
                        mode="text",
                        started_at=kwargs["now"],
                        item_count=0,
                    )
                    store.tables.practice_sessions[rival.id] = rival
                return create(session_id, **kwargs)

            uow.practice.create_session = competing  # type: ignore[method-assign]
            yield uow

    return dataclasses.replace(svc, uow=unit_of_work)


def test_a_session_opened_concurrently_is_replaced_once(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = quiz_subject(svc, user_id)
    instance_id = next(iter(store.tables.template_instances))
    started = start_practice(racing(svc, store, every_time=False), user_id, subject_id, "text")
    statuses = sorted(s.status for s in store.tables.practice_sessions.values())
    assert statuses == ["incomplete", "open"]
    assert store.tables.practice_sessions[started.session_id].status == "open"
    store.tables.practice_sessions.clear()
    shown = show_exercise(racing(svc, store, every_time=False), user_id, subject_id, instance_id)
    assert store.tables.practice_sessions[shown.session_id].status == "open"


def test_a_session_that_keeps_racing_is_a_practice_conflict(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = quiz_subject(svc, user_id)
    instance_id = next(iter(store.tables.template_instances))
    contested = racing(svc, store, every_time=True)
    with pytest.raises(ServiceError) as info:
        start_practice(contested, user_id, subject_id, "text")
    assert fields_of(info) == ("practice_conflict", ())
    with pytest.raises(ServiceError) as info:
        show_exercise(contested, user_id, subject_id, instance_id)
    assert fields_of(info) == ("practice_conflict", ())


# --- history (slice D ruling 55) ----------------------------------------------------------------


def test_history_lists_sessions_newest_first_with_the_attendance_flag(
    svc: Services, store: MemoryStore, clock: FixedClock, user_id: UUID, other_user_id: UUID
) -> None:
    subject_id = quiz_subject(svc, user_id)
    quiz_id = next(
        i.id for i in store.tables.template_instances.values() if i.subject_id == subject_id
    )
    # A closed text session with one right answer counts.
    closed = start_practice(svc, user_id, subject_id, "text")
    assert closed.first is not None
    right = right_numeric(store, closed.first)
    record_result(svc, user_id, closed.session_id, closed.first.item_id, right)
    clock.advance(timedelta(minutes=7))
    end_practice(svc, user_id, closed.session_id)
    # A text session left open, then replaced: incomplete, does not count.
    clock.advance(timedelta(minutes=1))
    dropped = start_practice(svc, user_id, subject_id, "text")
    clock.advance(timedelta(minutes=1))
    unanswered = start_practice(svc, user_id, subject_id, "text")
    end_practice(svc, user_id, unanswered.session_id)  # closed text: counts without answers
    # A class session without an answer (then replaced) does not count.
    clock.advance(timedelta(minutes=1))
    empty = show_exercise(svc, user_id, subject_id, quiz_id)
    clock.advance(timedelta(minutes=1))
    later = start_practice(svc, user_id, subject_id, "text")  # the class one becomes incomplete
    end_practice(svc, user_id, later.session_id)
    # A class session with a wrong answer counts while it is open.
    clock.advance(timedelta(minutes=1))
    taught = show_exercise(svc, user_id, subject_id, quiz_id)
    wrong = {"answer_kind": "number", "value": "999"}
    record_result(svc, user_id, taught.session_id, taught.item.item_id, wrong)

    rows = history(svc, user_id, subject_id)
    by_id = {r.session_id: r for r in rows}
    assert [r.started_at for r in rows] == sorted((r.started_at for r in rows), reverse=True)
    assert rows[0].session_id == taught.session_id
    first = by_id[closed.session_id]
    assert (first.mode, first.status, first.counted, first.minutes) == ("text", "closed", True, 7)
    assert (first.answered, first.accuracy, first.items) == (1, 1.0, closed.item_count)
    gone = by_id[dropped.session_id]
    assert (gone.status, gone.counted, gone.minutes, gone.accuracy) == (
        "incomplete",
        False,
        None,
        None,
    )
    assert (by_id[empty.session_id].mode, by_id[empty.session_id].counted) == ("class", False)
    assert by_id[empty.session_id].status == "incomplete"
    assert (by_id[unanswered.session_id].counted, by_id[unanswered.session_id].answered) == (
        True,
        0,
    )
    assert len(rows) == 6
    last = by_id[taught.session_id]
    assert (last.status, last.answered, last.accuracy, last.counted) == ("open", 1, 0.0, True)
    assert history(svc, user_id, subject_id, limit=2) == rows[:2]
    with pytest.raises(ServiceError) as info:
        history(svc, other_user_id, subject_id)
    assert fields_of(info) == ("not_found", ("subject_id",))


def test_a_session_without_a_plan_never_counts() -> None:
    row = PracticeSessionRow(
        id=uuid4(),
        subject_id=uuid4(),
        plan_id=None,
        mode="text",
        status="closed",
        started_at=NOW,
        ended_at=NOW,
        self_rating=None,
        summary={},
        item_count=0,
    )
    assert not counts_toward_plan(row, 3)
    assert counts_toward_plan(dataclasses.replace(row, plan_id=uuid4()), 0)
    assert not counts_toward_plan(dataclasses.replace(row, plan_id=uuid4(), status="open"), 0)
    klass = dataclasses.replace(row, plan_id=uuid4(), mode="class", status="incomplete")
    assert counts_toward_plan(klass, 1) and not counts_toward_plan(klass, 0)
