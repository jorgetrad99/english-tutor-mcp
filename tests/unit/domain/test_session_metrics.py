from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.domain.metrics import (
    SessionMetrics,
    compute_metrics,
    metrics_to_json,
    uptake_count,
)
from tutor.domain.validation import (
    Evidence,
    ReportedError,
    ValidatedEvidence,
    ValidError,
    validate_evidence,
)

pytestmark = pytest.mark.unit

START = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
OFFERED = ("it-07-c1", "it-07-c2", "it-07-c3", "it-07-c4", "it-07-c5")
TURNS = (
    "Yesterday I have went to the client meeting and we talk about the release plan.",
    "They asked many questions about the new deploy and I explain the risks clearly.",
    "After that I went back and we talked about the dates for the next release.",
)  # 15 + 14 + 15 = 44 words


def evidence(
    turns: tuple[str, ...] = TURNS,
    errors: tuple[ReportedError, ...] = (),
    chunks: tuple[str, ...] = (),
    estimate: int | None = None,
) -> Evidence:
    return Evidence(
        user_turns=turns,
        errors=errors,
        chunks_used=chunks,
        task_result="achieved",
        hints_given=1,
        cefr_level="B1+",
        cefr_confidence="medium",
        cefr_evidence=(),
        confidence_1_5=4,
        assistant_words_estimate=estimate,
    )


def metrics(
    ev: Evidence,
    *,
    minutes: float = 10,
    recent: frozenset[str] = frozenset(),
    offered: int = 5,
) -> tuple[ValidatedEvidence, SessionMetrics]:
    v = validate_evidence(ev, OFFERED, previous_cefr=None, self_level="B1")
    m = compute_metrics(
        ev,
        v,
        chunks_offered=offered,
        started_at=START,
        ended_at=START + timedelta(minutes=minutes),
        recent_correct_norms=recent,
    )
    return v, m


ERRORS = (
    ReportedError(said="I have went", correct="I went", category="grammar"),
    ReportedError(said="we talk about", correct="we talked about", category="grammar"),
    ReportedError(said="I explain the risks", correct="I explained the risks", category="grammar"),
    ReportedError(said="many questions", correct="a lot of questions", category="lexis"),
)


def test_full_metrics_example() -> None:
    ev = evidence(
        errors=ERRORS,
        chunks=("it-07-c2", "it-07-c4", "bogus"),
        estimate=66,
    )
    v, m = metrics(ev, minutes=12.5, recent=frozenset({"a lot of questions", "unrelated"}))
    assert v.user_words == 44
    assert m == SessionMetrics(
        user_words=44,
        assistant_words_estimate=66,
        user_ratio=0.4,
        turns=3,
        words_per_turn=14.7,
        duration_min=12.5,
        user_words_per_min=3.5,
        errors_total=4,
        errors_rejected=0,
        errors_by_category={
            "grammar": 3,
            "lexis": 1,
            "word_order": 0,
            "register": 0,
            "other": 0,
        },
        errors_per_100w=9.1,
        recurring_errors=1,
        uptake_count=2,
        chunks_offered=5,
        chunks_used=2,
        chunks_rejected=1,
        activation_rate=0.4,
    )


def test_uptake_needs_the_correct_form_in_a_later_turn() -> None:
    turns = ("I have went there.", "Sorry, I went there.", "We talk about it, then I went home.")
    errors = (
        ValidError("I have went", "I went", "i went", "grammar", 0),
        ValidError("We talk about", "we talked about", "we talked about", "grammar", 2),
        ValidError("I went", "I went", "i went", "grammar", 2),
    )
    assert uptake_count(errors, turns) == 1


def test_uptake_matches_typographic_variants() -> None:
    # Review Focus 1
    turns = ("I dont know.", "Ok, I don\N{RIGHT SINGLE QUOTATION MARK}t know yet.")
    errors = (ValidError("I dont know", "I don't know", "i don't know", "grammar", 0),)
    assert uptake_count(errors, turns) == 1


def test_duration_is_capped_at_45_and_never_negative() -> None:
    assert metrics(evidence(), minutes=90)[1].duration_min == 45.0
    assert metrics(evidence(), minutes=-5)[1].duration_min == 0.0
    _, m = metrics(evidence(), minutes=10 + 4 / 60)  # 10 min 4 s
    assert m.duration_min == 10.1


def test_words_per_minute_uses_at_least_one_minute() -> None:
    _, m = metrics(evidence(), minutes=0.25)
    assert m.duration_min == 0.2
    assert m.user_words_per_min == 44.0


def test_user_ratio_only_with_an_estimate() -> None:
    assert metrics(evidence())[1].user_ratio is None
    assert metrics(evidence(estimate=0))[1].user_ratio == 1.0
    assert metrics(evidence(estimate=176))[1].user_ratio == 0.2
    assert metrics(evidence(turns=(), estimate=0))[1].user_ratio == 0.0


def test_empty_session_has_zero_rates() -> None:
    _, m = metrics(evidence(turns=()), offered=0)
    assert m.words_per_turn == 0.0
    assert m.errors_per_100w == 0.0
    assert m.activation_rate == 0.0
    assert m.user_words_per_min == 0.0


def test_low_trust_session_reports_no_errors_but_keeps_words() -> None:
    bad = (
        ReportedError(said="never said this", correct="x", category="grammar"),
        ReportedError(said="nor this", correct="y", category="lexis"),
        ReportedError(said="I have went", correct="I went", category="grammar"),
    )
    v, m = metrics(evidence(errors=bad))
    assert v.low_trust is True
    assert m.errors_total == 0
    assert m.errors_rejected == 2
    assert m.user_words == 44
    assert m.uptake_count == 0


def test_recurring_ignores_empty_correct_norm() -> None:
    ev = evidence(errors=(ReportedError(said="I have went", correct="!!", category="other"),))
    _, m = metrics(ev, recent=frozenset({""}))
    assert m.errors_total == 1
    assert m.recurring_errors == 0


def test_metrics_to_json_round_trips_through_the_constructor() -> None:
    _, m = metrics(evidence(errors=ERRORS, estimate=10))
    data = metrics_to_json(m)
    assert list(data) == list(SessionMetrics.__dataclass_fields__)
    assert isinstance(data["errors_by_category"], dict)
    assert SessionMetrics(**data) == m


@given(
    turns=st.lists(st.text(alphabet="abc XYZ 019'.", max_size=50), max_size=6),
    minutes=st.floats(min_value=-10, max_value=200),
    estimate=st.none() | st.integers(0, 5000),
    chunks=st.lists(st.sampled_from([*OFFERED, "bogus"]), max_size=8),
)
def test_metric_ranges(
    turns: list[str], minutes: float, estimate: int | None, chunks: list[str]
) -> None:
    ev = evidence(tuple(turns), chunks=tuple(chunks), estimate=estimate)
    _, m = metrics(ev, minutes=minutes)
    assert 0.0 <= m.duration_min <= 45.0
    assert m.user_words_per_min <= m.user_words
    assert 0.0 <= m.activation_rate <= 1.0
    assert m.user_ratio is None or 0.0 <= m.user_ratio <= 1.0
    assert m.uptake_count <= m.errors_total
    assert sum(m.errors_by_category.values()) == m.errors_total
