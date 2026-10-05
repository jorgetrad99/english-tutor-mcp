import dataclasses
import json
from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.content import load_track
from tutor.domain.levels import CEFR_LEVELS, LEVEL_VALUE, CefrLevel
from tutor.domain.plan_lite import (
    DEFAULT_WEEKS,
    Feasibility,
    PlanLite,
    PlannedItem,
    build_plan_lite,
    feasibility,
    feasibility_text,
    horizon_weeks,
    order_track,
    rationale,
)
from tutor.domain.profile import USE_CASES, Profile, UseCase
from tutor.domain.track import INTERACTION_TYPES, InteractionType, TrackChunk, TrackItem

pytestmark = pytest.mark.unit

TODAY = date(2026, 10, 14)


def profile(**changes: Any) -> Profile:
    base = Profile(
        self_level="B1",
        domains=("it",),
        use_cases=("incident",),
        minutes_per_day=20,
        days_per_week=3,
        target_level="B2",
        target_date=None,
        goal_text=None,
        timezone="America/Mexico_City",
    )
    return dataclasses.replace(base, **changes)


def item(
    item_id: str,
    order_no: int,
    interaction: InteractionType,
    *,
    cefr: str = "B1",
    use_cases: tuple[UseCase, ...] = ("standup",),
) -> TrackItem:
    return TrackItem(
        id=item_id,
        domain="it",
        order_no=order_no,
        cefr=cefr,  # type: ignore[arg-type]
        skill="speaking",
        interaction_type=interaction,
        use_cases=use_cases,
        can_do_en="Can do it.",
        can_do_es="Puedo hacerlo.",
        character="Sam",
        objective="Do it.",
        obstacle="It is hard.",
        scenario_hint="A call.",
        chunks=tuple(
            TrackChunk(f"{item_id}-c{p}", p, f"chunk {p} here", f"A chunk {p} here.")
            for p in range(1, 6)
        ),
    )


def ids(items: Sequence[PlannedItem]) -> list[str]:
    return [i.track_item_id for i in items]


@pytest.mark.parametrize(
    ("offset", "weeks"),
    [(None, DEFAULT_WEEKS), (-30, 4), (1, 4), (28, 4), (29, 5), (210, 30), (364, 52), (500, 52)],
)
def test_horizon_weeks(offset: int | None, weeks: int) -> None:
    target = None if offset is None else TODAY + timedelta(days=offset)
    assert horizon_weeks(target, TODAY) == weeks


def test_order_track_b1_puts_matching_use_cases_first_within_each_band() -> None:
    ordered = [i.id for i in order_track(profile(), load_track())]
    assert ordered == [
        "it-03", "it-08", "it-01", "it-02", "it-04", "it-05", "it-06", "it-07",
        "it-09", "it-10", "it-11", "it-12", "it-13", "it-20", "it-14", "it-15",
        "it-16", "it-17", "it-18", "it-19", "it-21", "it-22", "it-23", "it-24",
    ]  # fmt: skip


def test_order_track_b2_drops_b1_items_without_a_shared_use_case() -> None:
    ordered = [
        i.id for i in order_track(profile(self_level="B2", use_cases=("interview",)), load_track())
    ]
    assert ordered == [
        "it-09", "it-17", "it-19", "it-13", "it-14", "it-15", "it-16",
        "it-18", "it-20", "it-21", "it-22", "it-23", "it-24",
    ]  # fmt: skip


def test_order_track_ignores_other_domains() -> None:
    other = dataclasses.replace(item("biz-01", 2, "negotiate"), domain="biz")  # type: ignore[arg-type]
    track = [item("it-01", 1, "explain"), other]
    assert [i.id for i in order_track(profile(), track)] == ["it-01"]


def test_build_plan_lite_default_horizon() -> None:
    plan = build_plan_lite(profile(), load_track(), TODAY)
    assert isinstance(plan, PlanLite)
    assert len(plan.items) == 36 == plan.feasibility.sessions_planned
    assert ids(plan.items)[:6] == ["it-03", "it-01", "it-08", "it-02", "it-04", "it-05"]
    assert Counter(i.variant for i in plan.items) == {"base": 24, "complication": 12}
    assert plan.items[0] == PlannedItem(1, 1, "it-03", "base")
    assert (plan.items[2].week_no, plan.items[2].order_no) == (1, 3)
    assert (plan.items[3].week_no, plan.items[3].order_no) == (2, 1)
    assert (plan.items[-1].week_no, plan.items[-1].order_no) == (12, 3)


def test_complication_pass_repeats_the_sorted_list_from_the_start() -> None:
    plan = build_plan_lite(profile(), load_track(), TODAY)
    ordered = [i.id for i in order_track(profile(), load_track())]
    complications = sorted(i.track_item_id for i in plan.items if i.variant == "complication")
    assert complications == sorted(ordered[:12])


def test_interaction_type_pass_on_a_small_track() -> None:
    track = [item("a", 1, "explain"), item("b", 2, "explain"), item("c", 3, "negotiate")]
    plan = build_plan_lite(profile(target_date=TODAY + timedelta(days=28)), track, TODAY)
    assert [(i.track_item_id, i.variant) for i in plan.items] == [
        ("a", "base"),
        ("c", "base"),
        ("b", "base"),
        ("c", "complication"),
        ("a", "complication"),
        ("c", "complication"),
        ("b", "complication"),
        ("c", "complication"),
        ("a", "complication"),
        ("b", "complication"),  # only explain items remain from here on
        ("a", "complication"),
        ("b", "complication"),
    ]


# Review Focus 4
def test_regeneration_skips_base_items_already_done() -> None:
    done = frozenset({"it-03", "it-01"})
    plan = build_plan_lite(profile(), load_track(), TODAY, done_base_ids=done)
    base_ids = [i.track_item_id for i in plan.items if i.variant == "base"]
    assert len(base_ids) == 22
    assert done.isdisjoint(base_ids)
    assert len(plan.items) == 36
    assert ("it-03", "complication") in [(i.track_item_id, i.variant) for i in plan.items]


def test_regeneration_with_every_base_done_plans_only_complications() -> None:
    done = frozenset(i.id for i in load_track())
    plan = build_plan_lite(profile(), load_track(), TODAY, done_base_ids=done)
    assert {i.variant for i in plan.items} == {"complication"}
    assert len(plan.items) == 36


def test_build_plan_lite_needs_candidates() -> None:
    with pytest.raises(ValueError, match="no items"):
        build_plan_lite(profile(), [], TODAY)


def test_target_date_sets_the_horizon() -> None:
    plan = build_plan_lite(profile(target_date=TODAY + timedelta(days=210)), load_track(), TODAY)
    assert plan.feasibility.weeks == 30
    assert len(plan.items) == 90


def test_feasibility_same_level_is_reachable_with_zero_hours() -> None:
    f = feasibility(profile(self_level="B2", target_level="B2"), 12)
    assert (f.hours_needed, f.reachable, f.milestone_level, f.message) == (
        0,
        True,
        None,
        "reachable",
    )


def test_feasibility_exact_hours_are_enough() -> None:
    f = feasibility(profile(target_level="B1+", minutes_per_day=30, days_per_week=6), 30)
    assert f == Feasibility(
        weeks=30,
        sessions_planned=180,
        hours_available=90.0,
        hours_needed=90,
        reachable=True,
        milestone_level=None,
        message="reachable",
    )


def test_feasibility_one_week_short_builds_confidence_only() -> None:
    f = feasibility(profile(target_level="B1+", minutes_per_day=30, days_per_week=6), 29)
    assert (f.hours_available, f.reachable, f.milestone_level) == (87.0, False, None)
    assert f.message == "confidence_only"


def test_feasibility_milestone_when_the_target_is_too_far() -> None:
    f = feasibility(profile(target_level="B2", minutes_per_day=30, days_per_week=6), 30)
    assert (f.hours_needed, f.reachable, f.milestone_level, f.message) == (
        180,
        False,
        "B1+",
        "milestone",
    )


def test_rationale_is_json_safe_and_complete() -> None:
    p = profile(target_date=date(2027, 1, 13), use_cases=("standup", "incident"))
    f = feasibility(p, 13)
    data = rationale(f, p)
    assert json.loads(json.dumps(data)) == data
    assert data == {
        "algorithm": "plan_lite_v1",
        "template": "confidence_only",
        "inputs": {
            "self_level": "B1",
            "target_level": "B2",
            "target_date": "2027-01-13",
            "minutes_per_day": 20,
            "days_per_week": 3,
            "domains": ["it"],
            "use_cases": ["standup", "incident"],
        },
        "weeks": 13,
        "sessions_planned": 39,
        "hours_available": 13.0,
        "hours_needed": 180,
        "reachable": False,
        "milestone_level": None,
    }


@pytest.mark.parametrize(
    ("f", "lang", "text"),
    [
        (
            Feasibility(30, 180, 90.0, 90, True, None, "reachable"),
            "en",
            "Your plan has 180 sessions over 30 weeks (about 90 hours of practice): "
            "enough for your goal.",
        ),
        (
            Feasibility(30, 180, 90.0, 180, False, "B1+", "milestone"),
            "en",
            "Your plan has 180 sessions over 30 weeks (about 90 hours of practice). Your goal "
            "needs about 180 hours, so this plan aims for B1+ first.",
        ),
        (
            Feasibility(12, 36, 12.0, 180, False, None, "confidence_only"),
            "en",
            "Your plan has 36 sessions over 12 weeks (about 12 hours of practice). Moving up a "
            "level needs about 180 hours, so this plan builds confidence at your current level.",
        ),
        (
            Feasibility(30, 180, 90.0, 90, True, None, "reachable"),
            "es",
            "Tu plan tiene 180 sesiones en 30 semanas (unas 90 horas de práctica): "
            "suficiente para tu meta.",
        ),
        (
            Feasibility(30, 180, 90.0, 180, False, "B1+", "milestone"),
            "es",
            "Tu plan tiene 180 sesiones en 30 semanas (unas 90 horas de práctica). Tu meta "
            "necesita unas 180 horas, así que este plan apunta primero a B1+.",
        ),
        (
            Feasibility(4, 8, 2.6666666666666665, 90, False, None, "confidence_only"),
            "es",
            "Tu plan tiene 8 sesiones en 4 semanas (unas 3 horas de práctica). Subir de nivel "
            "necesita unas 90 horas, así que este plan te da confianza en tu nivel actual.",
        ),
    ],
)
def test_feasibility_text_templates(f: Feasibility, lang: Any, text: str) -> None:
    assert feasibility_text(f, lang) == text


def _forced_or_alternating(types: Sequence[str]) -> bool:
    """Equal neighbours only when every remaining item has that same type."""
    return all(
        types[i] != types[i - 1] or all(t == types[i] for t in types[i:])
        for i in range(1, len(types))
    )


@st.composite
def profiles(draw: st.DrawFn) -> Profile:
    self_level: CefrLevel = draw(st.sampled_from(CEFR_LEVELS))
    targets = [lvl for lvl in CEFR_LEVELS if LEVEL_VALUE[lvl] >= LEVEL_VALUE[self_level]]
    offset = draw(st.none() | st.integers(min_value=28, max_value=364))
    return profile(
        self_level=self_level,
        target_level=draw(st.sampled_from(targets)),
        use_cases=tuple(
            draw(st.lists(st.sampled_from(USE_CASES), min_size=1, max_size=4, unique=True))
        ),
        minutes_per_day=draw(st.sampled_from((15, 20, 30))),
        days_per_week=draw(st.integers(min_value=2, max_value=7)),
        target_date=None if offset is None else TODAY + timedelta(days=offset),
    )


TRACK_IDS = [i.id for i in load_track()]


@given(profiles(), st.frozensets(st.sampled_from(TRACK_IDS)))
def test_plan_properties_on_the_real_track(p: Profile, done: frozenset[str]) -> None:
    track = load_track()
    plan = build_plan_lite(p, track, TODAY, done_base_ids=done)
    by_id = {i.id: i for i in track}
    assert len(plan.items) == plan.feasibility.sessions_planned
    assert plan.feasibility.sessions_planned == p.days_per_week * plan.feasibility.weeks
    for index, planned in enumerate(plan.items):
        assert planned.week_no == index // p.days_per_week + 1
        assert planned.order_no == index % p.days_per_week + 1
    assert _forced_or_alternating([by_id[i.track_item_id].interaction_type for i in plan.items])
    base = [i.track_item_id for i in plan.items if i.variant == "base"]
    eligible = [i.id for i in order_track(p, track) if i.id not in done]
    assert len(base) == len(set(base)) == min(len(eligible), len(plan.items))
    assert set(base) <= set(eligible)


@given(
    st.lists(st.sampled_from(INTERACTION_TYPES), min_size=1, max_size=10),
    st.integers(min_value=2, max_value=7),
)
def test_interaction_pass_on_synthetic_tracks(types: list[InteractionType], days: int) -> None:
    track = [item(f"x-{n}", n, t) for n, t in enumerate(types, start=1)]
    plan = build_plan_lite(profile(days_per_week=days), track, TODAY)
    by_id = {i.id: i for i in track}
    assert len(plan.items) == days * DEFAULT_WEEKS
    assert _forced_or_alternating([by_id[i.track_item_id].interaction_type for i in plan.items])
