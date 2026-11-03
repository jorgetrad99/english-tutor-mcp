"""subject_progress and the notes_searched event on the in-memory store (subjects spec 16;
slice B plan rulings 63-71, Review Focus 6, 8 and 9)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from template_fixtures import HTML, KEY_NAME, NUMERIC, numeric_params

from tutor.domain.notes import InstanceInput, NoteInput
from tutor.domain.subject_metrics import ActivityCounts, TimeStats
from tutor.domain.templates import TEMPLATE_GUIDE_VERSION
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.memory import MemoryStore
from tutor.services.notes import save_notes, search_notes
from tutor.services.practice import end_practice, next_exercise, record_result, start_practice
from tutor.services.progress import subject_progress
from tutor.services.templates import create_instance, save_template

from .conftest import NOW, FixedClock, create_subject

pytestmark = pytest.mark.unit


def events(store: MemoryStore, user_id: UUID, event: str) -> list[dict[str, Any]]:
    return [a.meta for a in store.tables.audit if a.user_id == user_id and a.event == event]


def lesson(svc: Services, user_id: UUID, subject_id: UUID, text: str) -> None:
    note = NoteInput(
        title="Clase", lesson_ref=None, position=None, raw_text=text, concepts=["Suma"]
    )
    save_notes(svc, user_id, subject_id, None, [note])


def practised_subject(svc: Services, user_id: UUID, quizzes: int = 3) -> UUID:
    subject_id = create_subject(svc, user_id, exercise_kinds=["quiz"])
    lesson(svc, user_id, subject_id, "Dos más dos son cuatro.")
    template_id = save_template(
        svc, user_id, subject_id, NUMERIC, HTML, TEMPLATE_GUIDE_VERSION
    ).template_id
    for n in range(1, quizzes + 1):
        data = InstanceInput(template_id, numeric_params(float(n)), f"Pregunta {n}.", "Suma")
        create_instance(svc, user_id, subject_id, data)
    return subject_id


def answer_all(
    svc: Services, store: MemoryStore, user_id: UUID, session_id: UUID, clock: FixedClock
) -> None:
    step = next_exercise(svc, user_id, session_id)
    while step.item is not None:
        clock.advance(timedelta(seconds=20))
        value = store.tables.template_instances[step.item.instance_id].params[KEY_NAME]
        answer = {"answer_kind": "number", "value": str(value)}
        record_result(svc, user_id, session_id, step.item.item_id, answer)
        step = next_exercise(svc, user_id, session_id)


def test_subject_progress_reads_the_learners_practice(
    svc: Services, store: MemoryStore, user_id: UUID, other_user_id: UUID, clock: FixedClock
) -> None:
    subject_id = practised_subject(svc, user_id)
    started = start_practice(svc, user_id, subject_id, "text")
    answer_all(svc, store, user_id, started.session_id, clock)
    end_practice(svc, user_id, started.session_id)
    search_notes(svc, user_id, "cuatro", subject_id)
    search_notes(svc, user_id, "cuatro")
    # Beto's practice never reaches Ana's metrics.
    theirs = practised_subject(svc, other_user_id)
    their_session = start_practice(svc, other_user_id, theirs, "text")
    answer_all(svc, store, other_user_id, their_session.session_id, clock)
    progress = subject_progress(svc, user_id, subject_id)
    assert progress.as_of == date(2026, 10, 14)
    assert progress.streak.current_days == 1
    assert progress.overall.all_time.attempts == 3
    assert progress.overall.all_time.correct == 3
    [suma] = progress.concepts
    assert (suma.name, suma.concept_rank, suma.all_time.mean_score) == ("Suma", "ranked", 100)
    assert suma.timing == TimeStats(3, 0, 20, 20)
    [numeric] = progress.templates
    assert numeric.timing.timed == 3
    assert progress.totals == ActivityCounts(
        notes_ingested=1,
        templates_created=1,
        instances_created=3,
        searches=1,
        practice_started=1,
        practice_completed=1,
        practice_abandoned=0,
    )
    assert progress.unscoped_searches == 1
    assert "Suma" not in progress.summary_text and "Respuesta" not in progress.summary_text
    assert progress.summary_text.startswith("Racha: 1 día (máxima: 1).")


def test_subject_progress_is_not_found_for_a_foreign_or_missing_subject(
    svc: Services, user_id: UUID, other_user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    for who, subject in ((other_user_id, subject_id), (user_id, uuid4())):
        with pytest.raises(ServiceError) as info:
            subject_progress(svc, who, subject)
        assert (info.value.code, info.value.fields) == ("not_found", ("subject_id",))


def test_review_focus_8_the_streak_follows_the_learners_day(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = practised_subject(svc, user_id, quizzes=4)
    # 23:50 and 00:10 in Mexico City (UTC-6) fall on one UTC date.
    clock.now = NOW.replace(day=15, hour=5, minute=49, second=0)
    started = start_practice(svc, user_id, subject_id, "text")
    assert started.first is not None
    clock.advance(timedelta(minutes=1))
    first_value = store.tables.template_instances[started.first.instance_id].params[KEY_NAME]
    record_result(
        svc,
        user_id,
        started.session_id,
        started.first.item_id,
        {"answer_kind": "number", "value": str(first_value)},
    )
    clock.now = NOW.replace(day=15, hour=6, minute=10, second=0)
    step = next_exercise(svc, user_id, started.session_id)
    assert step.item is not None
    record_result(
        svc, user_id, started.session_id, step.item.item_id, {"answer_kind": "number", "value": "0"}
    )
    progress = subject_progress(svc, user_id, subject_id)
    assert (progress.streak.current_days, progress.streak.longest_days) == (2, 2)
    assert progress.streak.last_practice_day == date(2026, 10, 15)


def test_review_focus_9_search_records_no_query(
    svc: Services, store: MemoryStore, user_id: UUID, caplog: pytest.LogCaptureFixture
) -> None:
    subject_id = create_subject(svc, user_id)
    lesson(svc, user_id, subject_id, "Dos más dos son cuatro.")
    query = "<script>secret</script>"
    with caplog.at_level(logging.DEBUG):
        search_notes(svc, user_id, query, subject_id)
        search_notes(svc, user_id, "cuatro")
    assert events(store, user_id, "notes_searched") == [
        {"subject_id": str(subject_id), "hits": 0, "fuzzy": False},
        {"subject_id": None, "hits": 1, "fuzzy": False},
    ]
    assert "secret" not in repr(store.tables.audit)
    assert "secret" not in caplog.text


def test_a_stale_open_session_counts_as_abandoned(
    svc: Services, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = practised_subject(svc, user_id)
    start_practice(svc, user_id, subject_id, "text")
    clock.advance(timedelta(hours=24))
    assert subject_progress(svc, user_id, subject_id).totals.practice_abandoned == 0
    clock.advance(timedelta(seconds=1))
    assert subject_progress(svc, user_id, subject_id).totals.practice_abandoned == 1
