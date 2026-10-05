from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.domain.glossary import GlossaryKind
from tutor.domain.lesson import (
    MAX_DUE_REVIEWS,
    DueCandidate,
    DueReview,
    ItemChoice,
    PendingPlanItem,
    RecentResult,
    TaskResult,
    choose_item,
    choose_variant,
    pick_due_reviews,
    review_format,
)
from tutor.domain.plan_lite import Variant
from tutor.domain.profile import UseCase
from tutor.domain.track import TrackChunk, TrackItem

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)


def uid(n: int) -> UUID:
    return UUID(int=n)


def track_item(item_id: str, order_no: int, *use_cases: UseCase) -> TrackItem:
    return TrackItem(
        id=item_id,
        domain="it",
        order_no=order_no,
        cefr="B1",
        skill="speaking",
        interaction_type="explain",
        use_cases=use_cases,
        can_do_en="Can explain a change.",
        can_do_es="Puede explicar un cambio.",
        character="Priya, tech lead",
        objective="Get approval",
        obstacle="She is busy",
        scenario_hint="Monday standup",
        chunks=tuple(
            TrackChunk(id=f"{item_id}-c{i}", position=i, text=f"chunk {i}", example="Example.")
            for i in range(1, 6)
        ),
    )


TRACK = {
    t.id: t
    for t in (
        track_item("it-01", 1, "standup"),
        track_item("it-02", 2, "code_review", "async_writing"),
        track_item("it-03", 3, "standup", "client_call"),
        track_item("it-04", 4, "interview"),
        track_item("it-05", 5, "client_call"),
    )
}


def pending(
    n: int, week: int, order: int, track_id: str, variant: Variant = "base"
) -> PendingPlanItem:
    return PendingPlanItem(
        plan_item_id=uid(n), week_no=week, order_no=order, track_item_id=track_id, variant=variant
    )


PENDING = [
    pending(3, 2, 1, "it-03"),
    pending(2, 1, 2, "it-02"),
    pending(4, 2, 2, "it-05", "complication"),
]


# --- choose_item -----------------------------------------------------------------


def test_no_prep_takes_first_pending_by_week_then_order() -> None:
    assert choose_item(PENDING, TRACK, None, {}, None) == ItemChoice(
        plan_item_id=uid(2), track_item_id="it-02", variant="base", plan_exhausted=False
    )


def test_prep_takes_first_pending_item_with_the_use_case() -> None:
    choice = choose_item(PENDING, TRACK, "client_call", {}, None)
    assert choice == ItemChoice(
        plan_item_id=uid(3), track_item_id="it-03", variant="base", plan_exhausted=False
    )


def test_prep_keeps_the_plan_items_variant() -> None:
    only_late = [pending(4, 2, 2, "it-05", "complication")]
    choice = choose_item(only_late, TRACK, "client_call", {}, None)
    assert choice.plan_item_id == uid(4)
    assert choice.variant == "complication"


def test_prep_without_pending_match_prefers_a_never_done_track_item_off_plan() -> None:
    last_done = {"it-01": NOW - timedelta(days=9)}
    choice = choose_item(PENDING, TRACK, "standup", last_done, None)
    # it-03 is pending, so it wins before the off-plan search:
    assert choice.plan_item_id == uid(3)
    no_pending_standup = [pending(2, 1, 2, "it-02")]
    choice = choose_item(no_pending_standup, TRACK, "standup", last_done, None)
    assert choice == ItemChoice(
        plan_item_id=None, track_item_id="it-03", variant="base", plan_exhausted=False
    )


def test_prep_off_plan_takes_the_least_recently_done_item() -> None:
    last_done = {"it-01": NOW - timedelta(days=2), "it-03": NOW - timedelta(days=20)}
    choice = choose_item([], TRACK, "standup", last_done, None)
    assert choice == ItemChoice(
        plan_item_id=None, track_item_id="it-03", variant="base", plan_exhausted=False
    )


def test_prep_off_plan_tie_breaks_on_order_no() -> None:
    same = NOW - timedelta(days=5)
    choice = choose_item([], TRACK, "standup", {"it-01": same, "it-03": same}, None)
    assert choice.track_item_id == "it-01"


def test_prep_with_unknown_use_case_falls_back_to_the_plan() -> None:
    choice = choose_item(PENDING, TRACK, "demo", {}, None)
    assert choice.plan_item_id == uid(2)


def test_pending_item_missing_from_track_is_skipped_for_prep() -> None:
    orphan = [pending(9, 1, 1, "it-99"), pending(3, 2, 1, "it-03")]
    assert choose_item(orphan, TRACK, "standup", {}, None).plan_item_id == uid(3)


def test_exhausted_plan_repeats_the_last_item_as_complication_off_plan() -> None:
    last = pending(7, 12, 3, "it-04")
    assert choose_item([], TRACK, None, {}, last) == ItemChoice(
        plan_item_id=None, track_item_id="it-04", variant="complication", plan_exhausted=True
    )
    assert choose_item([], TRACK, "demo", {}, last).plan_exhausted is True


def test_exhausted_plan_with_prep_match_uses_the_off_plan_item() -> None:
    last = pending(7, 12, 3, "it-04")
    choice = choose_item([], TRACK, "interview", {"it-04": NOW}, last)
    assert choice == ItemChoice(
        plan_item_id=None, track_item_id="it-04", variant="base", plan_exhausted=False
    )


def test_no_pending_and_no_last_item_raises() -> None:
    with pytest.raises(ValueError, match="no pending plan item"):
        choose_item([], TRACK, None, {}, None)


# --- choose_variant --------------------------------------------------------------


def results(*rows: tuple[TaskResult, int]) -> list[RecentResult]:
    return [RecentResult(task_result=r, hints_given=h) for r, h in rows]


@pytest.mark.parametrize(
    ("recent", "plan_variant", "expected"),
    [
        ([], "base", "base"),
        ([], "complication", "complication"),
        (results(("not_achieved", 0), ("not_achieved", 0)), "base", "simpler"),
        (
            results(("not_achieved", 0), ("achieved", 0), ("not_achieved", 3)),
            "complication",
            "simpler",
        ),
        (results(("achieved", 1), ("achieved", 0), ("achieved", 1)), "base", "complication"),
        (results(("achieved", 1), ("achieved", 2), ("achieved", 0)), "base", "base"),
        (results(("achieved", 0), ("achieved", 0)), "base", "base"),
        (results(("achieved", 0), ("partial", 0), ("achieved", 0)), "complication", "complication"),
        (results(("not_achieved", 0), ("achieved", 0), ("achieved", 0)), "base", "base"),
        # Only the newest three count: the 4th and 5th are ignored.
        (
            results(
                ("achieved", 0),
                ("achieved", 0),
                ("achieved", 0),
                ("not_achieved", 0),
                ("not_achieved", 0),
            ),
            "base",
            "complication",
        ),
        (
            results(
                ("partial", 0),
                ("achieved", 0),
                ("not_achieved", 0),
                ("not_achieved", 0),
            ),
            "base",
            "base",
        ),
    ],
)
def test_choose_variant(recent: list[RecentResult], plan_variant: Variant, expected: str) -> None:
    assert choose_variant(plan_variant, recent) == expected


# --- review_format and pick_due_reviews ------------------------------------------


@pytest.mark.parametrize(
    ("kind", "leech", "last_ratings", "expected"),
    [
        ("correction", True, (3, 3), "correct"),
        ("correction", False, (3, 3), "use"),
        ("chunk", True, (3, 3), "use"),
        ("term", False, (2, 3, 3), "use"),
        ("term", False, (3,), "recall"),
        ("term", False, (4, 3), "recall"),
        ("correction", False, (), "produce"),
        ("chunk", True, (1, 3), "produce"),
        ("term", True, (), "recall"),
    ],
)
def test_review_format(
    kind: GlossaryKind, leech: bool, last_ratings: tuple[int, ...], expected: str
) -> None:
    assert review_format(kind, leech, last_ratings) == expected


def candidate(
    n: int,
    kind: GlossaryKind,
    overdue_h: float,
    *,
    leech: bool = False,
    ratings: tuple[int, ...] = (),
) -> DueCandidate:
    return DueCandidate(
        item_id=uid(n),
        kind=kind,
        leech=leech,
        due=NOW - timedelta(hours=overdue_h),
        last_ratings=ratings,
    )


def test_pick_due_reviews_orders_by_group_then_most_overdue() -> None:
    candidates = [
        candidate(1, "term", 100),
        candidate(2, "chunk", 1),
        candidate(3, "chunk", 50, ratings=(3, 3)),
        candidate(4, "correction", 2),
        candidate(5, "term", 3, leech=True),
        candidate(6, "correction", 0),  # due exactly now
        candidate(7, "correction", -1),  # not due yet
    ]
    assert pick_due_reviews(candidates, NOW) == (
        DueReview(item_id=uid(5), format="recall"),
        DueReview(item_id=uid(4), format="produce"),
        DueReview(item_id=uid(6), format="produce"),
        DueReview(item_id=uid(3), format="use"),
        DueReview(item_id=uid(2), format="produce"),
        DueReview(item_id=uid(1), format="recall"),
    )


def test_pick_due_reviews_caps_at_eight_and_honours_limit() -> None:
    candidates = [candidate(n, "term", n) for n in range(1, 13)]
    picked = pick_due_reviews(candidates, NOW)
    assert len(picked) == MAX_DUE_REVIEWS == 8
    assert [r.item_id for r in picked] == [uid(n) for n in range(12, 4, -1)]
    assert len(pick_due_reviews(candidates, NOW, limit=4)) == 4
    assert pick_due_reviews(candidates, NOW, limit=0) == ()


@given(
    st.lists(
        st.builds(
            DueCandidate,
            item_id=st.uuids(),
            kind=st.sampled_from(["correction", "chunk", "term"]),
            leech=st.booleans(),
            due=st.datetimes(
                min_value=datetime(2026, 1, 1),
                max_value=datetime(2026, 12, 31),
                timezones=st.just(UTC),
            ),
            last_ratings=st.lists(st.integers(1, 4), max_size=2).map(tuple),
        ),
        max_size=20,
        unique_by=lambda c: c.item_id,
    )
)
def test_pick_due_reviews_properties(candidates: list[DueCandidate]) -> None:
    picked = pick_due_reviews(candidates, NOW)
    by_id = {c.item_id: c for c in candidates}
    assert all(by_id[r.item_id].due <= NOW for r in picked)
    due_count = sum(1 for c in candidates if c.due <= NOW)
    assert len(picked) == min(due_count, MAX_DUE_REVIEWS)
    groups = [
        0
        if by_id[r.item_id].kind == "correction" or by_id[r.item_id].leech
        else 1
        if by_id[r.item_id].kind == "chunk"
        else 2
        for r in picked
    ]
    assert groups == sorted(groups)
