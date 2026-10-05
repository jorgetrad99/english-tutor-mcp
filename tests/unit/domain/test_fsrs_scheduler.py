"""FSRS-4.5 against the official py-fsrs v2.5.1 vector and the research notes' DERIVED vectors."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.domain.fsrs import (
    DEFAULT_WEIGHTS,
    TARGET_RETENTION,
    FsrsState,
    Rating,
    new_state,
    review,
    state_from_json,
    state_to_json,
)
from tutor.domain.fsrs.scheduler import (
    FACTOR,
    MAX_INTERVAL_DAYS,
    init_difficulty,
    init_stability,
    next_difficulty,
    next_forget_stability,
    next_interval,
    next_recall_stability,
    retrievability,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
W = DEFAULT_WEIGHTS
# py-fsrs v2.5.1 tests/test_fsrs.py::test_review_card (non-default test weights, r = 0.9)
OFFICIAL_W = (
    1.14, 1.01, 5.44, 14.67, 5.3024, 1.5662, 1.2503, 0.0028, 1.5489, 0.1763,
    0.9953, 2.7473, 0.0179, 0.3105, 0.3976, 0.0, 2.0902,
)  # fmt: skip
OFFICIAL_RATINGS: tuple[Rating, ...] = (3, 3, 3, 3, 3, 3, 1, 1, 3, 3, 3, 3, 3)


def test_defaults_are_the_fsrs_4_5_weights() -> None:
    assert DEFAULT_WEIGHTS == (
        0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975, 0.031, 1.6474,
        0.1367, 1.0461, 2.1072, 0.0793, 0.3246, 1.587, 0.2272, 2.8755,
    )  # fmt: skip
    assert TARGET_RETENTION == 0.85
    assert FACTOR == 0.23456790123456783


# --- 6d: unit values per formula, default weights ----------------------------------------


@pytest.mark.parametrize(
    ("rating", "s0", "d0"),
    [
        (1, 0.4872, 7.6214),
        (2, 1.4003, 6.3916),
        (3, 3.7145, 5.1618),
        (4, 13.8206, 3.9320000000000004),
    ],
)
def test_initial_stability_and_difficulty(rating: Rating, s0: float, d0: float) -> None:
    assert init_stability(W, rating) == s0
    assert init_difficulty(W, rating) == pytest.approx(d0, rel=1e-15)


def test_initial_values_are_clamped() -> None:
    tiny = (0.01, *W[1:])
    assert init_stability(tiny, 1) == 0.1
    steep = (*W[:5], 10.0, *W[6:])
    assert init_difficulty(steep, 1) == 10.0
    assert init_difficulty(steep, 4) == 1.0


@pytest.mark.parametrize(
    ("t", "s", "r"),
    [
        (1, 1, 0.9),
        (0, 1, 1.0),
        (3, 3.7145, 0.9169112760382789),
        (5, 10, 0.946058996209746),
        (20, 10, 0.8250286473253902),
        (100, 100, 0.9),
    ],
)
def test_retrievability(t: float, s: float, r: float) -> None:
    assert retrievability(t, s) == pytest.approx(r, rel=1e-15)


@pytest.mark.parametrize(
    ("s", "at_090", "at_085"),
    [
        (0.4872, 1, 1),
        (1.4003, 1, 2),
        (2.5, 2, 4),
        (3.7145, 4, 6),
        (13.8206, 14, 23),
        (100, 100, 164),
        (1e6, MAX_INTERVAL_DAYS, MAX_INTERVAL_DAYS),
    ],
)
def test_next_interval(s: float, at_090: int, at_085: int) -> None:
    assert next_interval(s, 0.9) == at_090
    assert next_interval(s, 0.85) == at_085


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (1.0, (2.8683708, 1.9986933, 1.1290158, 1.0)),
        (5.1618, (6.901155, 6.0314775, 5.1618, 4.2921225000000005)),
        (10.0, (10.0, 10.0, 9.8500158, 8.9803383)),
    ],
)
def test_next_difficulty(d: float, expected: tuple[float, float, float, float]) -> None:
    got = tuple(next_difficulty(W, d, g) for g in (1, 2, 3, 4))
    # Compare element by element, each float with rel=1e-12
    assert len(got) == len(expected)
    for i, (g_val, e_val) in enumerate(zip(got, expected, strict=True)):
        assert g_val == pytest.approx(e_val, rel=1e-12), f"Element {i} mismatch"


def test_next_stability_after_recall_and_forgetting() -> None:
    d, s, r = 5.1618, 3.7145, 0.85
    assert next_recall_stability(W, d, s, r, 2) == pytest.approx(7.347978485427174, rel=1e-12)
    assert next_recall_stability(W, d, s, r, 3) == pytest.approx(19.706922911211155, rel=1e-12)
    assert next_recall_stability(W, d, s, r, 4) == pytest.approx(49.70071208118768, rel=1e-12)
    assert next_forget_stability(W, d, s, r) == pytest.approx(1.535671091366268, rel=1e-12)


@pytest.mark.parametrize(
    ("d", "s", "t", "r", "raw", "raw_ivl", "capped_ivl"),
    [
        (7.6214, 0.4872, 30, 0.25446168896872823, 0.8052013034063482, 1, 1),
        (5.1618, 1.4003, 60, 0.30081812419654663, 1.8446055904214993, 3, 2),
    ],
)
def test_post_lapse_cap(
    d: float, s: float, t: int, r: float, raw: float, raw_ivl: int, capped_ivl: int
) -> None:
    assert retrievability(t, s) == pytest.approx(r, rel=1e-12)
    assert next_forget_stability(W, d, s, r) == pytest.approx(raw, rel=1e-12)
    assert next_interval(raw, 0.85) == raw_ivl
    before = FsrsState(s, d, 3, 0, T0 - timedelta(days=t), T0)
    after = review(before, 1, T0)
    assert after.stability == s
    assert after.due == T0 + timedelta(days=capped_ivl)
    assert after.lapses == 1


# --- 6a-6c: py-fsrs v2.5.1 wrapper flow (learning steps), re-implemented for parity --------


def pyfsrs_v251_run(
    w: Sequence[float], retention: float, ratings: Sequence[Rating]
) -> list[tuple[int, float, float]]:
    """py-fsrs v2.5.1 `review_card` states and steps over our formulas; (ivl, S, D) per step."""
    state, s, d, last, now = "new", 0.0, 0.0, T0, T0
    out: list[tuple[int, float, float]] = []
    for g in ratings:
        if state == "new":
            s, d = init_stability(w, g), init_difficulty(w, g)
            if g == 4:
                state, ivl = "review", next_interval(s, retention)
                due = now + timedelta(days=ivl)
            else:
                state, ivl = "learning", 0
                due = now + timedelta(minutes={1: 1, 2: 5, 3: 10}[g])
        elif state in ("learning", "relearning"):
            good = next_interval(s, retention)
            easy = max(next_interval(s, retention), good + 1)
            if g in (1, 2):
                ivl, due = 0, now + timedelta(minutes=5 if g == 1 else 10)
            else:
                state, ivl = "review", good if g == 3 else easy
                due = now + timedelta(days=ivl)
        else:
            r = retrievability((now - last).days, s)
            s_by = {g2: next_recall_stability(w, d, s, r, g2) for g2 in (2, 3, 4)}
            s_by[1] = next_forget_stability(w, d, s, r)
            hard = next_interval(s_by[2], retention)
            good = next_interval(s_by[3], retention)
            hard = min(hard, good)
            good = max(good, hard + 1)
            easy = max(next_interval(s_by[4], retention), good + 1)
            d, s = next_difficulty(w, d, g), s_by[g]
            if g == 1:
                state, ivl, due = "relearning", 0, now + timedelta(minutes=5)
            else:
                ivl = {2: hard, 3: good, 4: easy}[g]
                due = now + timedelta(days=ivl)
        out.append((ivl, s, d))
        last, now = now, due
    return out


def test_official_py_fsrs_v251_vector() -> None:
    run = pyfsrs_v251_run(OFFICIAL_W, 0.9, OFFICIAL_RATINGS)
    assert [ivl for ivl, _, _ in run] == [0, 5, 16, 43, 106, 236, 0, 0, 12, 25, 47, 85, 147]


def test_official_vector_per_step_state() -> None:
    run = pyfsrs_v251_run(OFFICIAL_W, 0.9, OFFICIAL_RATINGS)
    # Compare element by element for tuples
    s_val, d_val = run[2][1:]
    assert s_val == pytest.approx(15.935277453230313, rel=1e-12)
    assert d_val == pytest.approx(5.3024, rel=1e-12)

    s_val, d_val = run[6][1:]
    assert s_val == pytest.approx(12.387294593404093, rel=1e-12)
    assert d_val == pytest.approx(7.795998319999999, rel=1e-12)

    s_val, d_val = run[12][1:]
    assert s_val == pytest.approx(146.50234062675943, rel=1e-12)
    assert d_val == pytest.approx(7.768187098876362, rel=1e-12)


@pytest.mark.parametrize(
    ("retention", "ivls", "final_s"),
    [
        (0.9, [0, 4, 15, 49, 146, 393, 0, 0, 13, 34, 84, 195, 426], 425.8667458680844),
        (0.85, [0, 6, 32, 142, 539, 1796, 0, 0, 33, 113, 345, 961, 2470], 1508.6795325590094),
    ],
)
def test_derived_wrapper_vectors_with_default_weights(
    retention: float, ivls: list[int], final_s: float
) -> None:
    run = pyfsrs_v251_run(W, retention, OFFICIAL_RATINGS)
    assert [ivl for ivl, _, _ in run] == ivls
    s_val, d_val = run[-1][1:]
    assert s_val == pytest.approx(final_s, rel=1e-12)
    assert d_val == pytest.approx(6.6952984387616485, rel=1e-12)


# --- 6e: this project's scheduler (no learning steps), each review on its due day ----------


def run_reviews(ratings: Sequence[Rating], retention: float = TARGET_RETENTION) -> list[FsrsState]:
    state = new_state(T0)
    states = []
    for g in ratings:
        state = review(state, g, state.due, retention=retention)
        states.append(state)
    return states


def intervals(states: Sequence[FsrsState]) -> list[int]:
    assert all(s.last_review is not None for s in states)
    return [(s.due - s.last_review).days for s in states if s.last_review is not None]


def test_derived_sequence_r085_with_a_lapse() -> None:
    states = run_reviews((3, 3, 3, 3, 1, 3, 3, 2, 4, 3))
    assert intervals(states) == [6, 32, 142, 539, 21, 75, 238, 340, 1748, 4323]
    expected = [
        (3.7145, 5.1618),
        (19.52305823000846, 5.1618),
        (86.57753093448697, 5.1618),
        (329.29352361107567, 5.1618),
        (13.075955914038445, 6.901155),
        (45.809890283516395, 6.847234995),
        (145.31318371715903, 6.794986510155),
        (207.34749718133145, 7.614035228340194),
        (1067.4929330606449, 6.6683384362616485),
        (2640.3860812003486, 6.621635744737537),
    ]
    # Compare element by element for list of tuples
    assert len([(s.stability, s.difficulty) for s in states]) == len(expected)
    for i, (state, exp) in enumerate(zip(states, expected, strict=True)):
        s_val = pytest.approx(exp[0], rel=1e-12)
        d_val = pytest.approx(exp[1], rel=1e-12)
        assert state.stability == s_val, f"Stability mismatch at index {i}"
        assert state.difficulty == d_val, f"Difficulty mismatch at index {i}"
    assert [s.reps for s in states] == list(range(1, 11))
    assert states[-1].lapses == 1


def test_derived_sequence_r085_hits_the_difficulty_clamp() -> None:
    states = run_reviews((1, 3, 3, 2, 2, 1, 3, 4))
    assert intervals(states) == [1, 4, 15, 23, 31, 6, 10, 33]
    assert states[5].difficulty == 10
    assert states[5].stability == pytest.approx(3.6986927970125167, rel=1e-12)
    s_val = pytest.approx(20.327964912951543, rel=1e-12)
    d_val = pytest.approx(8.835003610200001, rel=1e-12)
    assert states[-1].stability == s_val
    assert states[-1].difficulty == d_val
    assert states[-1].lapses == 1  # the first-ever Again is not a lapse


def test_derived_sequence_r090() -> None:
    states = run_reviews((3, 3, 3, 3, 1, 3, 3, 2, 4, 3), retention=0.9)
    assert intervals(states) == [4, 15, 49, 146, 9, 24, 61, 80, 325, 691]
    s_val = pytest.approx(691.0901359273582, rel=1e-12)
    d_val = pytest.approx(6.621635744737537, rel=1e-12)
    assert states[-1].stability == s_val
    assert states[-1].difficulty == d_val


# --- scheduler behaviour ------------------------------------------------------------------


def test_new_state_has_no_memory_yet() -> None:
    assert new_state(T0) == FsrsState(None, None, 0, 0, None, T0)


@pytest.mark.parametrize(("rating", "days"), [(1, 1), (2, 2), (3, 6), (4, 23)])
def test_first_review_uses_initial_values(rating: Rating, days: int) -> None:
    state = review(new_state(T0), rating, T0)
    assert state.stability == init_stability(W, rating)
    assert state.difficulty == init_difficulty(W, rating)
    assert (state.reps, state.lapses, state.last_review) == (1, 0, T0)
    assert state.due == T0 + timedelta(days=days)


def test_clock_going_backwards_counts_as_zero_elapsed_days() -> None:
    first = review(new_state(T0), 3, T0)
    again = review(first, 3, T0 - timedelta(days=1))
    assert again.stability == first.stability


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rating": 5}, "rating"),
        ({"now": datetime(2026, 10, 14, 15, 0)}, "timezone-aware"),
        ({"weights": W[:16]}, "17 weights"),
        ({"retention": 1.0}, "retention"),
    ],
)
def test_review_rejects_bad_input(kwargs: dict[str, Any], message: str) -> None:
    args: dict[str, Any] = {"rating": 3, "now": T0, **kwargs}
    extra = {k: v for k, v in args.items() if k in ("weights", "retention")}
    with pytest.raises(ValueError, match=message):
        review(new_state(T0), args["rating"], args["now"], **extra)


def test_json_round_trip() -> None:
    for state in (new_state(T0), *run_reviews((3, 1, 4))):
        data = json.loads(json.dumps(state_to_json(state)))
        assert state_from_json(data) == state


def test_json_shape() -> None:
    state = review(new_state(T0), 3, T0)
    assert state_to_json(state) == {
        "stability": 3.7145,
        "difficulty": 5.1618,
        "reps": 1,
        "lapses": 0,
        "last_review": "2026-10-14T15:00:00+00:00",
        "due": "2026-10-20T15:00:00+00:00",
    }


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"reps": -1}, "non-negative"),
        ({"lapses": True}, "non-negative"),
        ({"stability": "high"}, "numbers"),
        ({"due": "2026-10-14T15:00:00"}, "UTC offset"),
        ({"last_review": 5}, "ISO 8601"),
    ],
)
def test_state_from_json_rejects_malformed_data(changes: dict[str, Any], message: str) -> None:
    data = {**state_to_json(review(new_state(T0), 3, T0)), **changes}
    with pytest.raises(ValueError, match=message):
        state_from_json(data)


@given(
    st.lists(
        st.tuples(st.sampled_from((1, 2, 3, 4)), st.integers(min_value=1, max_value=400)),
        min_size=1,
        max_size=25,
    )
)
def test_scheduler_invariants(steps: list[tuple[Rating, int]]) -> None:
    state = new_state(T0)
    now = T0
    lapses = 0
    for index, (rating, gap_days) in enumerate(steps):
        state = review(state, rating, now)
        lapses += 1 if rating == 1 and index > 0 else 0
        assert state.stability is not None and state.stability > 0
        assert state.difficulty is not None and 1 <= state.difficulty <= 10
        assert state.due > now
        assert state.due - now <= timedelta(days=MAX_INTERVAL_DAYS)
        assert (state.reps, state.lapses, state.last_review) == (index + 1, lapses, now)
        now += timedelta(days=gap_days)
