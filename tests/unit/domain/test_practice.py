"""Practice sessions in the domain: item count, mix, deterministic drawing, grading glue and the
session summary (subjects spec 6.2, 15.4.1, 15.8; slice B plan rulings 31-36, amended)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from template_fixtures import (
    CHOICE,
    FLASHCARD,
    KEY_NAME,
    NUMERIC,
    POOL_NAME,
    TABLE,
    choice_params,
    flashcard_params,
    numeric_params,
    param,
    spec,
    table_params,
)

from tutor.domain.errors import FieldError
from tutor.domain.practice import (
    ITEM_COUNT_MAX,
    ITEM_COUNT_MIN,
    MIX,
    Candidate,
    DrawnItem,
    KindResult,
    Mix,
    SessionSummary,
    SummaryItem,
    answer_to_json,
    avoid_for,
    capacities,
    draw_items,
    draw_showing,
    grade_showing,
    item_count,
    parse_showing_answer,
    plan_mix,
    schedulable,
    summarise,
    used_entries,
)
from tutor.domain.subjects import ExerciseKind, Preference
from tutor.domain.templates import (
    ChoiceAnswer,
    Draw,
    Graded,
    NumberAnswer,
    ParamPair,
    SelfReportAnswer,
    TemplateInput,
    TemplateSpec,
    ValueAnswer,
    draw,
    seed_for,
    validate_params,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
SESSION = UUID("00000000-0000-0000-0000-00000000aaaa")

SELF_REPORT = TemplateInput(
    name="Práctica",
    description=None,
    template_purpose="exercise",
    grading_kind="self_report",
    params=(param("task", "text"),),
)
CHOICE_POOL = TemplateInput(
    name="Elige",
    description=None,
    template_purpose="exercise",
    grading_kind="choice_index",
    params=(param(POOL_NAME, "pool", "pool"),),
    pool_draw="one_of_choices",
    pool_count=4,
)
VIEW_ONLY = dataclasses.replace(CHOICE_POOL, name="Oído", needs_view=True)


def uid(n: int) -> UUID:
    return UUID(int=n)


def params_of(template: TemplateSpec, pairs: Sequence[ParamPair]) -> dict[str, object]:
    result = validate_params(template, pairs, path="params")
    assert isinstance(result, dict), result
    return result


def cand(
    n: int,
    data: TemplateInput,
    pairs: Sequence[ParamPair],
    last: datetime | None = None,
) -> Candidate:
    template = spec(data)
    return Candidate(
        instance_id=uid(n),
        spec=template,
        params=params_of(template, pairs),
        last_shown_at=last,
    )


def quiz(n: int, last: datetime | None = None) -> Candidate:
    return cand(n, NUMERIC, numeric_params(float(n)), last)


def exercise(n: int, last: datetime | None = None) -> Candidate:
    return cand(n, SELF_REPORT, (ParamPair("task", "text", f"Toca {n}"),), last)


def drill(n: int, last: datetime | None = None, entries: int = 6) -> Candidate:
    return cand(n, FLASHCARD, flashcard_params(entries), last)


def ids(count: int, start: int = 1000) -> tuple[UUID, ...]:
    return tuple(uid(start + k) for k in range(count))


# --- item count ---------------------------------------------------------------------------------


def test_item_count_is_a_third_of_the_minutes_clamped() -> None:
    assert [item_count(m) for m in (10, 12, 13, 14, 30, 36, 37, 38, 120)] == [
        4,
        4,
        4,
        5,
        10,
        12,
        12,
        12,
        12,
    ]
    assert (ITEM_COUNT_MIN, ITEM_COUNT_MAX) == (4, 12)


# --- mix ----------------------------------------------------------------------------------------


def test_mix_shares_follow_the_spec_with_drill_for_ear() -> None:
    assert MIX == {
        "practice_first": (("exercise", 60), ("quiz", 20), ("drill", 20)),
        "theory_first": (("quiz", 50), ("drill", 30), ("exercise", 20)),
        "balanced": (("quiz", 40), ("drill", 30), ("exercise", 30)),
    }


def test_plan_mix_splits_by_share_with_largest_remainders() -> None:
    every: dict[ExerciseKind, int | None] = {"quiz": 50, "drill": None, "exercise": 50}
    assert plan_mix("balanced", every, 10) == Mix({"quiz": 4, "drill": 3, "exercise": 3}, False)
    assert plan_mix("theory_first", every, 12) == Mix({"quiz": 6, "drill": 4, "exercise": 2}, False)
    # 4 x (60, 20, 20) = 2.4, 0.8, 0.8: floors 2, 0, 0; remainders go to quiz then drill.
    assert plan_mix("practice_first", every, 4) == Mix(
        {"exercise": 2, "quiz": 1, "drill": 1}, False
    )


def test_review_focus_3_a_missing_kind_gives_its_share_to_the_others() -> None:
    assert plan_mix("balanced", {"quiz": 50, "exercise": 50}, 10) == Mix(
        {"quiz": 6, "exercise": 4}, False
    )
    assert plan_mix("balanced", {"drill": None}, 7) == Mix({"drill": 7}, False)


def test_ruling_35_a_shortfall_moves_to_the_other_kinds() -> None:
    assert plan_mix("balanced", {"quiz": 1, "drill": None, "exercise": 2}, 10) == Mix(
        {"quiz": 1, "drill": 7, "exercise": 2}, False
    )
    # Fewer than four in all: start with what exists, and say it is short.
    assert plan_mix("balanced", {"quiz": 2, "exercise": 1}, 10) == Mix(
        {"quiz": 2, "exercise": 1}, True
    )
    assert plan_mix("balanced", {"quiz": 4}, 10) == Mix({"quiz": 4}, False)
    assert plan_mix("balanced", {}, 10) == Mix({}, True)
    assert plan_mix("balanced", {"quiz": 0, "drill": 0}, 10) == Mix({}, True)


@given(
    count=st.integers(4, 12),
    quiz_cap=st.integers(0, 15),
    exercise_cap=st.integers(0, 15),
    with_drill=st.booleans(),
    preference=st.sampled_from(["practice_first", "theory_first", "balanced"]),
)
def test_plan_mix_invariants(
    count: int, quiz_cap: int, exercise_cap: int, with_drill: bool, preference: str
) -> None:
    capacity: dict[ExerciseKind, int | None] = {"quiz": quiz_cap, "exercise": exercise_cap}
    if with_drill:
        capacity["drill"] = None
    mix = plan_mix(cast(Preference, preference), capacity, count)
    available = quiz_cap + exercise_cap + (count if with_drill else 0)
    assert mix.total == min(count, available)
    assert mix.counts.get("quiz", 0) <= quiz_cap
    assert mix.counts.get("exercise", 0) <= exercise_cap
    assert all(v > 0 for v in mix.counts.values())
    assert mix.short == (mix.total < ITEM_COUNT_MIN)


def test_capacities_count_authored_instances_and_free_pools() -> None:
    candidates = (quiz(1), quiz(2), exercise(3), drill(4))
    assert capacities(candidates) == {"quiz": 2, "exercise": 1, "drill": None}
    assert capacities((quiz(1),)) == {"quiz": 1}
    display = cand(7, TABLE, table_params())
    assert capacities((display,)) == {}
    assert schedulable((display,), ("quiz", "drill", "exercise"), text_mode=False) == ()


def test_schedulable_keeps_enabled_kinds_and_skips_views_in_text_mode() -> None:
    ear = cand(5, VIEW_ONLY, flashcard_params(6))
    pool_choice = cand(6, CHOICE_POOL, flashcard_params(6))
    candidates = (quiz(1), exercise(2), drill(3), ear, pool_choice)
    assert [c.instance_id for c in schedulable(candidates, ("quiz", "drill"), text_mode=False)] == [
        uid(1),
        uid(3),
        uid(5),
        uid(6),
    ]
    assert [c.instance_id for c in schedulable(candidates, ("quiz", "drill"), text_mode=True)] == [
        uid(1),
        uid(3),
        uid(6),
    ]


# --- drawing ------------------------------------------------------------------------------------


def test_authored_items_are_least_recently_shown_first_and_never_repeated() -> None:
    old = T0 - timedelta(days=3)
    recent = T0 - timedelta(days=1)
    candidates = (quiz(1, recent), quiz(2, None), quiz(3, old), quiz(4, None), exercise(9, old))
    items = draw_items(SESSION, Mix({"quiz": 3}, False), candidates, ids(3))
    assert sorted(i.instance_id for i in items) == [uid(2), uid(3), uid(4)]
    assert [i.seq for i in items] == [1, 2, 3]
    assert [i.item_id for i in items] == list(ids(3))
    assert all(i.practice_kind == "quiz" for i in items)
    assert all(i.draw_seed == seed_for(i.item_id) for i in items)


def test_drill_items_rotate_over_pools_and_avoid_repeating_entries() -> None:
    candidates = (drill(1, T0, entries=3), drill(2, None, entries=3))
    items = draw_items(SESSION, Mix({"drill": 6}, False), candidates, ids(6))
    by_instance: dict[UUID, list[DrawnItem]] = {}
    for item in items:
        by_instance.setdefault(item.instance_id, []).append(item)
    assert {k: len(v) for k, v in by_instance.items()} == {uid(1): 3, uid(2): 3}
    for shown in by_instance.values():
        # Three entries, three showings in seq order: each entry asked once.
        assert sorted(s.draw.entries[0] for s in shown) == [0, 1, 2]
    pool = drill(2, None, entries=3)
    for item in by_instance[uid(2)]:
        assert item.draw.entries[0] < pool_size(pool)


def pool_size(candidate: Candidate) -> int:
    value = candidate.params[POOL_NAME]
    assert isinstance(value, list)
    return len(value)


def test_a_drill_repeats_entries_only_once_every_entry_was_asked() -> None:
    items = draw_items(SESSION, Mix({"drill": 5}, False), (drill(1, entries=2),), ids(5))
    firsts = [i.draw.entries[0] for i in items]
    assert sorted(firsts[:2]) == [0, 1]
    assert set(firsts) <= {0, 1}


def test_review_focus_determinism_same_seed_and_candidates_same_items() -> None:
    candidates = (quiz(1), quiz(2), exercise(3), exercise(4), drill(5), drill(6))
    mix = Mix({"quiz": 2, "exercise": 2, "drill": 4}, False)
    first = draw_items(SESSION, mix, candidates, ids(8))
    again = draw_items(SESSION, mix, tuple(reversed(candidates)), ids(8))
    assert first == again
    other = draw_items(uid(77), mix, candidates, ids(8))
    assert sorted(i.instance_id for i in other) == sorted(i.instance_id for i in first)


@given(st.integers(0, 2**64 - 1))
def test_drawing_is_deterministic_for_any_session(seed: int) -> None:
    session = UUID(int=seed << 64)
    candidates = (quiz(1), exercise(2), drill(3, entries=4))
    mix = Mix({"quiz": 1, "exercise": 1, "drill": 3}, False)
    items = draw_items(session, mix, candidates, ids(5))
    assert items == draw_items(session, mix, candidates, ids(5))
    assert [i.seq for i in items] == [1, 2, 3, 4, 5]
    assert sorted(str(i.practice_kind) for i in items) == [
        "drill",
        "drill",
        "drill",
        "exercise",
        "quiz",
    ]
    drills = [i.draw.entries[0] for i in items if i.practice_kind == "drill"]
    assert len(set(drills)) == 3


def test_draw_items_needs_one_id_per_item() -> None:
    with pytest.raises(ValueError, match="item id"):
        draw_items(SESSION, Mix({"quiz": 2}, False), (quiz(1), quiz(2)), ids(1))


def test_draw_items_refuses_a_mix_the_candidates_cannot_fill() -> None:
    with pytest.raises(ValueError, match="candidates"):
        draw_items(SESSION, Mix({"quiz": 2}, False), (quiz(1),), ids(2))


def test_draw_showing_avoids_the_sessions_entries_and_stores_the_draw() -> None:
    candidate = drill(1, entries=3)
    item = draw_showing(candidate, uid(500), seq=4, avoid=frozenset({0, 2}))
    assert item == DrawnItem(
        item_id=uid(500),
        seq=4,
        instance_id=uid(1),
        practice_kind="drill",
        grading_kind="exact_text",
        draw_seed=seed_for(uid(500)),
        draw=Draw(entries=(1,)),
    )
    plain = draw_showing(quiz(2), uid(501), seq=1)
    assert plain.draw == Draw() and plain.practice_kind == "quiz"


def test_used_entries_are_the_asked_ones() -> None:
    assert used_entries(spec(FLASHCARD), Draw(entries=(3,))) == frozenset({3})
    assert used_entries(spec(CHOICE_POOL), Draw(entries=(2, 0, 5, 1), order=(1, 0, 3, 2))) == (
        frozenset({2})
    )
    assert used_entries(spec(NUMERIC), Draw()) == frozenset()
    ordered = TemplateInput(
        name="Orden",
        description=None,
        template_purpose="exercise",
        grading_kind="exact_sequence",
        params=(param(POOL_NAME, "pool", "pool"),),
        pool_draw="ordered",
        pool_count=3,
    )
    assert used_entries(spec(ordered), Draw(entries=(4, 1, 2), order=(2, 0, 1))) == (
        frozenset({1, 2, 4})
    )


def test_avoid_for_cycles_through_the_pool() -> None:
    template = spec(FLASHCARD)
    params = params_of(template, flashcard_params(3))
    one = Draw(entries=(1,))
    assert avoid_for(template, params, ()) == frozenset()
    assert avoid_for(template, params, (one,)) == frozenset({1})
    assert avoid_for(template, params, (one, Draw(entries=(0,)))) == frozenset({0, 1})
    full = (one, Draw(entries=(0,)), Draw(entries=(2,)))
    assert avoid_for(template, params, full) == frozenset()  # every entry asked: a new cycle
    assert avoid_for(template, params, (*full, Draw(entries=(2,)))) == frozenset({2})
    assert avoid_for(spec(NUMERIC), {}, (Draw(),)) == frozenset()


# --- grading glue -------------------------------------------------------------------------------


def choice_item() -> tuple[TemplateSpec, Mapping[str, object], Draw]:
    template = spec(CHOICE)
    return template, params_of(template, choice_params(key=1)), Draw()


def test_review_focus_4_a_text_choice_is_answered_by_its_printed_number() -> None:
    template, params, shown = choice_item()
    data = {"answer_kind": "choice_index", "option_number": 2}
    assert parse_showing_answer(template, params, shown, data, from_view=False) == ChoiceAnswer(1)
    assert parse_showing_answer(template, params, shown, data, from_view=True) == ChoiceAnswer(1)
    view = {"answer_kind": "choice_index", "option_index": 2}
    assert parse_showing_answer(template, params, shown, view, from_view=True) == ChoiceAnswer(2)


def test_review_focus_4_choice_answers_out_of_range_or_from_the_wrong_channel() -> None:
    template, params, shown = choice_item()

    def errors(data: dict[str, Any], *, from_view: bool = False) -> object:
        return parse_showing_answer(template, params, shown, data, from_view=from_view)

    number = "answer.option_number"
    index = "answer.option_index"
    assert errors({"answer_kind": "choice_index", "option_number": 4}) == (
        FieldError(number, "out_of_range"),
    )
    assert errors({"answer_kind": "choice_index", "option_number": 0}) == (
        FieldError(number, "out_of_range"),
    )
    assert errors({"answer_kind": "choice_index", "option_number": "2"}) == (
        FieldError(number, "invalid"),
    )
    assert errors({"answer_kind": "choice_index", "option_number": True}) == (
        FieldError(number, "invalid"),
    )
    # A 0-based index comes only from the view (slice A plan ruling 36).
    assert errors({"answer_kind": "choice_index", "option_index": 1}) == (
        FieldError(index, "invalid"),
    )
    assert errors({"answer_kind": "choice_index", "option_index": 7}, from_view=True) == (
        FieldError(index, "out_of_range"),
    )
    assert errors({"answer_kind": "choice_index", "option_index": 1, "option_number": 2}) == (
        FieldError("answer", "invalid"),
    )
    assert errors({"answer_kind": "choice_index"}) == (FieldError("answer", "invalid"),)


def test_review_focus_4_an_answer_of_the_wrong_kind_is_invalid_on_answer() -> None:
    template, params, shown = choice_item()
    data = {"answer_kind": "value", "value": "dos"}
    assert parse_showing_answer(template, params, shown, data, from_view=False) == (
        FieldError("answer", "invalid"),
    )


def test_pool_choice_options_are_counted_from_the_draw() -> None:
    template = spec(CHOICE_POOL)
    params = params_of(template, flashcard_params(6))
    shown = draw(template, params, 42)
    data = {"answer_kind": "choice_index", "option_number": 4}
    assert parse_showing_answer(template, params, shown, data, from_view=False) == ChoiceAnswer(3)
    data = {"answer_kind": "choice_index", "option_number": 5}
    assert parse_showing_answer(template, params, shown, data, from_view=False) == (
        FieldError("answer.option_number", "out_of_range"),
    )


def test_other_kinds_pass_through_to_the_template_parser() -> None:
    template = spec(NUMERIC)
    params = params_of(template, numeric_params(3.14))
    data = {"answer_kind": "number", "value": "3,14"}
    assert parse_showing_answer(template, params, Draw(), data, from_view=False) == (
        NumberAnswer(Decimal("3.14"))
    )
    bad = {"answer_kind": "number", "value": "tres"}
    assert parse_showing_answer(template, params, Draw(), bad, from_view=False) == (
        FieldError("answer.value", "invalid"),
    )


def test_grade_showing_uses_the_stored_draw() -> None:
    template = spec(FLASHCARD)
    params = params_of(template, flashcard_params(4))
    entries = params[POOL_NAME]
    assert isinstance(entries, list)
    shown = Draw(entries=(2,))
    right = grade_showing(template, params, shown, ValueAnswer(str(entries[2]["answer"])))
    wrong = grade_showing(template, params, shown, ValueAnswer(str(entries[1]["answer"])))
    assert isinstance(right, Graded) and isinstance(wrong, Graded)
    assert (right.correct, right.score, wrong.correct, wrong.score) == (True, 100, False, 0)
    exercise_spec = spec(SELF_REPORT)
    report = grade_showing(exercise_spec, {"task": "x"}, Draw(), SelfReportAnswer(3, None, 10))
    assert isinstance(report, Graded) and (report.correct, report.score) == (None, 75)


def test_choice_grading_refuses_an_index_outside_the_options() -> None:
    template, params, shown = choice_item()
    assert grade_showing(template, params, shown, ChoiceAnswer(9)) == (
        FieldError("answer", "invalid"),
    )
    assert KEY_NAME in params


def test_answers_are_stored_as_plain_json() -> None:
    assert answer_to_json(ChoiceAnswer(2)) == {"answer_kind": "choice_index", "option_index": 2}
    assert answer_to_json(ValueAnswer("la cuenta")) == {
        "answer_kind": "value",
        "value": "la cuenta",
    }
    assert answer_to_json(NumberAnswer(Decimal("3.140"))) == {
        "answer_kind": "number",
        "value": "3.14",
    }
    assert answer_to_json(SelfReportAnswer(3, 80, None)) == {
        "answer_kind": "self_report",
        "rating": 3,
        "tempo_bpm": 80,
        "minutes": None,
    }
    from tutor.domain.templates import SequenceAnswer, ValuesAnswer

    assert answer_to_json(ValuesAnswer(("C", "E"))) == {
        "answer_kind": "values",
        "values": ["C", "E"],
    }
    assert answer_to_json(SequenceAnswer(("a", "b"))) == {
        "answer_kind": "sequence",
        "values": ["a", "b"],
    }


# --- summary ------------------------------------------------------------------------------------


def test_summary_counts_answers_by_kind_and_minutes() -> None:
    items = (
        SummaryItem("quiz", correct=True, score=100),
        SummaryItem("quiz", correct=False, score=0),
        SummaryItem("quiz", correct=None, score=None),  # unanswered
        SummaryItem("drill", correct=False, score=67),
        SummaryItem("exercise", correct=None, score=75),
    )
    summary = summarise(items, T0, T0 + timedelta(minutes=17, seconds=59))
    assert summary == SessionSummary(
        items=5,
        answered=4,
        unanswered=1,
        by_kind=(
            KindResult("quiz", items=3, answered=2, correct=1, mean_score=50),
            KindResult("drill", items=1, answered=1, correct=0, mean_score=67),
            KindResult("exercise", items=1, answered=1, correct=0, mean_score=75),
        ),
        minutes=17,
    )


def test_summary_of_an_empty_session() -> None:
    assert summarise((), T0, T0) == SessionSummary(0, 0, 0, (), 0)
    unanswered = summarise((SummaryItem("quiz", None, None),), T0, T0 + timedelta(minutes=3))
    assert unanswered.by_kind == (KindResult("quiz", 1, 0, 0, None),)
