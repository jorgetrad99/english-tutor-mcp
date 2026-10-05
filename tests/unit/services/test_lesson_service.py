"""start_lesson and record_review on the in-memory store (spec 8.1, 9 and 10.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from repo_contract import add_closed_session, first_item, insert_glossary, present, start_session

from tutor.domain.fsrs import review
from tutor.domain.glossary import GlossaryKind
from tutor.domain.lesson import MAX_DUE_REVIEWS, STARTS_PER_DAY, TaskResult, pick_due_reviews
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.lesson import record_review, start_lesson
from tutor.services.memory import MemoryStore
from tutor.services.ports import OpenSessionExists, ReviewLogRow
from tutor.services.views import LessonStart, ReviewResultView, StartLessonRequest

from .conftest import MEXICO_CITY, NEW_YORK, NOW, FixedClock, onboard

pytestmark = pytest.mark.unit


def text_lesson(svc: Services, user_id: UUID, **changes: Any) -> LessonStart:
    return start_lesson(svc, user_id, StartLessonRequest(mode="text", **changes))


def error_code(info: pytest.ExceptionInfo[ServiceError]) -> str:
    return info.value.code


# start_lesson


def test_start_lesson_needs_onboarding(svc: Services, user_id: UUID) -> None:
    with pytest.raises(ServiceError) as info:
        text_lesson(svc, user_id)
    assert error_code(info) == "onboarding_needed"


def test_start_lesson_opens_a_session_for_the_first_pending_item(
    svc: Services, user_id: UUID, now: datetime
) -> None:
    first = present(onboard(svc, user_id).plan.next_item)
    lesson = text_lesson(svc, user_id)
    assert lesson.item.id == first.track_item_id
    assert (lesson.mode, lesson.variant, lesson.prep_text) == ("text", "base", None)
    assert (lesson.due_reviews, lesson.provisional_items) == ((), ())
    assert not lesson.plan_exhausted
    assert not lesson.replaced_session
    with svc.uow(user_id) as uow:
        session = present(uow.sessions.get(lesson.session_id))
    assert (session.status, session.plan_item_id, session.started_at) == (
        "open",
        first.plan_item_id,
        now,
    )
    assert (session.mode, session.client, session.brief_variant) == ("text", "claude", "base")
    assert session.chunks_offered == tuple(c.id for c in lesson.item.chunks)
    assert len(session.chunks_offered) == 5


def test_second_start_closes_the_open_session_as_incomplete(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    onboard(svc, user_id)
    first = text_lesson(svc, user_id)
    clock.advance(timedelta(minutes=5))
    second = text_lesson(svc, user_id)
    assert second.replaced_session
    with svc.uow(user_id) as uow:
        old = present(uow.sessions.get(first.session_id))
        assert (old.status, old.ended_at, old.result) == ("incomplete", clock.now, None)
        assert present(uow.sessions.open_session()).id == second.session_id


def test_prep_picks_an_item_for_the_use_case(svc: Services, user_id: UUID) -> None:
    onboard(svc, user_id)
    lesson = text_lesson(
        svc,
        user_id,
        prep="  Tomorrow I explain the outage to the client.  ",
        prep_use_case="incident",
    )
    assert "incident" in lesson.item.use_cases
    assert lesson.prep_text == "Tomorrow I explain the outage to the client."
    with svc.uow(user_id) as uow:
        session = present(uow.sessions.get(lesson.session_id))
    assert session.prep_text == lesson.prep_text


@pytest.mark.parametrize(
    ("changes", "fields"),
    [
        ({"prep": "Client call at nine"}, ("prep_use_case",)),
        ({"prep_use_case": "demo"}, ("prep",)),
        ({"prep": "   ", "prep_use_case": "demo"}, ("prep",)),
        ({"prep": "x" * 301, "prep_use_case": "demo"}, ("prep",)),
        ({"minutes": 9}, ("minutes",)),
        ({"minutes": 31}, ("minutes",)),
    ],
)
def test_start_lesson_validates_its_request(
    svc: Services, user_id: UUID, changes: dict[str, Any], fields: tuple[str, ...]
) -> None:
    onboard(svc, user_id)
    with pytest.raises(ServiceError) as info:
        text_lesson(svc, user_id, **changes)
    assert (info.value.code, info.value.fields) == ("validation_failed", fields)
    with svc.uow(user_id) as uow:
        assert uow.sessions.open_session() is None


def test_start_purges_expired_provisional_items(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    onboard(svc, user_id)
    first = text_lesson(svc, user_id)
    with svc.uow(user_id) as uow:
        insert_glossary(
            uow,
            first.session_id,
            "on call rotation",
            clock.now,
            status="provisional",
            expires=clock.now + timedelta(minutes=30),
        )
        insert_glossary(
            uow, first.session_id, "blameless postmortem", clock.now, status="provisional"
        )
    clock.advance(timedelta(hours=1))
    second = text_lesson(svc, user_id)
    assert [p.text for p in second.provisional_items] == ["blameless postmortem"]
    with svc.uow(user_id) as uow:
        assert uow.glossary.count_provisional() == 1


def test_due_reviews_follow_the_composer_order_and_cap(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    onboard(svc, user_id)
    first = text_lesson(svc, user_id)
    kinds: tuple[GlossaryKind, ...] = ("term", "chunk", "correction")
    with svc.uow(user_id) as uow:
        for n in range(10):
            insert_glossary(
                uow,
                first.session_id,
                f"due phrase {n}",
                clock.now,
                kind=kinds[n % 3],
                first_due=clock.now - timedelta(hours=n + 1),
            )
    clock.advance(timedelta(minutes=30))
    with svc.uow(user_id) as uow:
        expected = pick_due_reviews(uow.glossary.due_candidates(clock.now), clock.now)
        rows = uow.glossary.get_many([e.item_id for e in expected])
    second = text_lesson(svc, user_id)
    assert len(second.due_reviews) == MAX_DUE_REVIEWS
    assert [(d.item_id, d.format) for d in second.due_reviews] == [
        (e.item_id, e.format) for e in expected
    ]
    assert [(d.text, d.kind, d.meaning) for d in second.due_reviews] == [
        (rows[e.item_id].text, rows[e.item_id].kind, rows[e.item_id].meaning) for e in expected
    ]


def test_provisional_items_are_capped_oldest_first(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    onboard(svc, user_id)
    first = text_lesson(svc, user_id)
    with svc.uow(user_id) as uow:
        for n in range(10):
            insert_glossary(
                uow,
                first.session_id,
                f"provisional phrase {n}",
                clock.now - timedelta(minutes=10 - n),
                status="provisional",
                expires=clock.now + timedelta(days=7),
            )
    second = text_lesson(svc, user_id)
    assert [p.text for p in second.provisional_items] == [
        f"provisional phrase {n}" for n in range(8)
    ]


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ((("achieved", 0), ("achieved", 1), ("achieved", 0)), "complication"),
        ((("not_achieved", 2), ("achieved", 0), ("not_achieved", 3)), "simpler"),
        ((("achieved", 0), ("partial", 1), ("achieved", 0)), "base"),
    ],
)
def test_variant_follows_the_last_three_closed_sessions(
    svc: Services,
    user_id: UUID,
    now: datetime,
    results: tuple[tuple[TaskResult, int], ...],
    expected: str,
) -> None:
    onboard(svc, user_id)
    with svc.uow(user_id) as uow:
        item = first_item(uow)
        for n, (task_result, hints) in enumerate(results):
            started = now - timedelta(days=3 - n)
            add_closed_session(
                uow,
                item,
                started,
                started + timedelta(minutes=20),
                task_result=task_result,
                hints_given=hints,
            )
    assert text_lesson(svc, user_id).variant == expected


def test_exhausted_plan_returns_the_last_item(svc: Services, user_id: UUID, now: datetime) -> None:
    onboard(svc, user_id)
    with svc.uow(user_id) as uow:
        plan = present(uow.plans.active())
        session = start_session(uow, first_item(uow), now - timedelta(hours=1))
        for item in plan.items:
            uow.plans.mark_done(item.id, session.id)
    lesson = text_lesson(svc, user_id)
    last = max(plan.items, key=lambda i: (i.week_no, i.order_no))
    assert lesson.plan_exhausted
    assert lesson.item.id == last.track_item_id


def test_ten_starts_per_local_day_in_mexico_city(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    onboard(svc, user_id)
    clock.now = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)  # 00:00 local
    last: LessonStart | None = None
    for _ in range(STARTS_PER_DAY):
        last = text_lesson(svc, user_id)
        clock.advance(timedelta(minutes=30))
    clock.now = datetime(2026, 10, 15, 5, 59, tzinfo=UTC)  # 23:59 local, same day
    with pytest.raises(ServiceError) as info:
        text_lesson(svc, user_id)
    assert error_code(info) == "rate_limited"
    with svc.uow(user_id) as uow:  # the refused start rolled back its housekeeping too
        assert present(uow.sessions.open_session()).id == present(last).session_id
    clock.now = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # 00:00 local, next day
    assert text_lesson(svc, user_id).replaced_session


@pytest.mark.parametrize(
    ("day_start", "last_minute", "next_day"),
    [
        # Fall back on Sun 2026-11-01: the day starts in EDT (04:00 UTC), ends in EST.
        (
            datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
            datetime(2026, 11, 2, 4, 59, tzinfo=UTC),
            datetime(2026, 11, 2, 5, 0, tzinfo=UTC),
        ),
        # Spring forward on Sun 2026-03-08: the day starts in EST (05:00 UTC), ends in EDT.
        (
            datetime(2026, 3, 8, 5, 0, tzinfo=UTC),
            datetime(2026, 3, 9, 3, 59, tzinfo=UTC),
            datetime(2026, 3, 9, 4, 0, tzinfo=UTC),
        ),
    ],
)
def test_ten_starts_per_local_day_in_new_york_across_dst(
    svc: Services,
    clock: FixedClock,
    user_id: UUID,
    day_start: datetime,
    last_minute: datetime,
    next_day: datetime,
) -> None:
    # Review Focus 3
    onboard(svc, user_id, timezone=NEW_YORK)
    clock.now = day_start - timedelta(minutes=1)  # 23:59 the evening before: not today's
    text_lesson(svc, user_id)
    clock.now = day_start
    for _ in range(STARTS_PER_DAY):  # every 40 minutes, crossing the 02:00 change
        text_lesson(svc, user_id)
        clock.advance(timedelta(minutes=40))
    clock.now = last_minute
    with pytest.raises(ServiceError) as info:
        text_lesson(svc, user_id)
    assert error_code(info) == "rate_limited"
    clock.now = next_day
    assert text_lesson(svc, user_id).replaced_session


def test_start_retries_once_when_a_concurrent_start_wins(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    # Review Focus 5
    onboard(svc, user_id)
    attempts: list[int] = []

    def lose_the_first_race() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise OpenSessionExists()

    store.before_session_create = lose_the_first_race
    lesson = text_lesson(svc, user_id)
    assert len(attempts) == 2
    with svc.uow(user_id) as uow:
        assert present(uow.sessions.open_session()).id == lesson.session_id
        assert uow.sessions.count_started_since(NOW - timedelta(days=1)) == 1


def test_start_gives_up_after_a_second_conflict(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    # Review Focus 5
    onboard(svc, user_id)
    earlier = text_lesson(svc, user_id)

    def always_lose() -> None:
        raise OpenSessionExists()

    store.before_session_create = always_lose
    with pytest.raises(ServiceError) as info:
        text_lesson(svc, user_id)
    assert error_code(info) == "rate_limited"
    with svc.uow(user_id) as uow:
        assert present(uow.sessions.open_session()).id == earlier.session_id
        assert uow.sessions.count_started_since(NOW - timedelta(days=1)) == 1


# record_review


def lesson_with_due_item(
    svc: Services, clock: FixedClock, user_id: UUID
) -> tuple[LessonStart, UUID]:
    onboard(svc, user_id)
    lesson = text_lesson(svc, user_id)
    with svc.uow(user_id) as uow:
        row = insert_glossary(
            uow,
            lesson.session_id,
            "roll back the deploy",
            clock.now,
            kind="chunk",
            first_due=clock.now,
        )
    return lesson, row.id


def test_record_review_updates_the_schedule_and_logs_the_state_before(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    lesson, item_id = lesson_with_due_item(svc, clock, user_id)
    with svc.uow(user_id) as uow:
        before = present(uow.reviews.state(item_id))
    clock.advance(timedelta(minutes=5))
    views = record_review(svc, user_id, lesson.session_id, [(item_id, 3)])
    expected = review(before, 3, clock.now)
    assert views == (
        ReviewResultView(
            item_id=item_id,
            next_due=expected.due.astimezone(ZoneInfo(MEXICO_CITY)).date(),
            outcome="recorded",
        ),
    )
    with svc.uow(user_id) as uow:
        assert uow.reviews.state(item_id) == expected
        assert uow.reviews.session_logs(lesson.session_id) == (
            ReviewLogRow(item_id=item_id, rating=3, reviewed_at=clock.now, state_before=before),
        )


def test_record_review_is_idempotent_per_session_and_item(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    lesson, item_id = lesson_with_due_item(svc, clock, user_id)
    (first,) = record_review(svc, user_id, lesson.session_id, [(item_id, 3)])
    with svc.uow(user_id) as uow:
        after = uow.reviews.state(item_id)
    clock.advance(timedelta(minutes=1))
    again = record_review(svc, user_id, lesson.session_id, [(item_id, 1)])
    assert again == (
        ReviewResultView(item_id=item_id, next_due=first.next_due, outcome="already_recorded"),
    )
    with svc.uow(user_id) as uow:
        assert uow.reviews.state(item_id) == after
        assert [log.rating for log in uow.reviews.session_logs(lesson.session_id)] == [3]


def test_record_review_needs_an_open_session_of_the_user(
    svc: Services, clock: FixedClock, user_id: UUID, other_user_id: UUID
) -> None:
    lesson, item_id = lesson_with_due_item(svc, clock, user_id)
    with pytest.raises(ServiceError) as missing:
        record_review(svc, user_id, uuid4(), [(item_id, 3)])
    assert error_code(missing) == "session_not_found"
    onboard(svc, other_user_id)
    with pytest.raises(ServiceError) as foreign:
        record_review(svc, other_user_id, lesson.session_id, [(item_id, 3)])
    assert error_code(foreign) == "session_not_found"
    text_lesson(svc, user_id)  # replaces the first session
    with pytest.raises(ServiceError) as closed:
        record_review(svc, user_id, lesson.session_id, [(item_id, 3)])
    assert error_code(closed) == "session_closed"


def test_record_review_accepts_only_the_users_confirmed_items(
    svc: Services, clock: FixedClock, user_id: UUID, other_user_id: UUID
) -> None:
    lesson, item_id = lesson_with_due_item(svc, clock, user_id)
    with svc.uow(user_id) as uow:
        maybe = insert_glossary(
            uow, lesson.session_id, "maybe later", clock.now, status="provisional"
        )
    onboard(svc, other_user_id)
    their_lesson = text_lesson(svc, other_user_id)
    with svc.uow(other_user_id) as uow:
        foreign = insert_glossary(uow, their_lesson.session_id, "not yours", clock.now)
    with pytest.raises(ServiceError) as info:
        record_review(
            svc,
            user_id,
            lesson.session_id,
            [(item_id, 3), (maybe.id, 3), (foreign.id, 2), (uuid4(), 4)],
        )
    assert (info.value.code, info.value.fields) == (
        "validation_failed",
        ("results[1].item_id", "results[2].item_id", "results[3].item_id"),
    )
    with svc.uow(user_id) as uow:
        assert uow.reviews.session_logs(lesson.session_id) == ()


def test_record_review_validates_the_results_list(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    lesson, item_id = lesson_with_due_item(svc, clock, user_id)
    cases: list[tuple[list[tuple[UUID, Any]], tuple[str, ...]]] = [
        ([], ("results",)),
        ([(uuid4(), 3) for _ in range(9)], ("results",)),
        ([(item_id, 3), (item_id, 2)], ("results[1].item_id",)),
        ([(item_id, 5)], ("results[0].rating",)),
    ]
    for results, fields in cases:
        with pytest.raises(ServiceError) as info:
            record_review(svc, user_id, lesson.session_id, results)
        assert (info.value.code, info.value.fields) == ("validation_failed", fields)
