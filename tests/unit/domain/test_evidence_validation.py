import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from tutor.domain.levels import CefrLevel
from tutor.domain.text import normalize
from tutor.domain.validation import (
    Category,
    Evidence,
    ReportedError,
    ValidatedEvidence,
    ValidError,
    validate_evidence,
)

pytestmark = pytest.mark.unit

OFFERED = ("it-07-c1", "it-07-c2", "it-07-c3", "it-07-c4", "it-07-c5")
FILLER = " ".join(["word"] * 30)  # exactly 30 words
TEXT = st.text(alphabet="abcXYZ019 áñ'\N{RIGHT SINGLE QUOTATION MARK}-.,!?", max_size=60)


def evidence(
    turns: tuple[str, ...] = (FILLER,),
    errors: tuple[ReportedError, ...] = (),
    chunks: tuple[str, ...] = (),
    cefr: CefrLevel = "B1",
) -> Evidence:
    return Evidence(
        user_turns=turns,
        errors=errors,
        chunks_used=chunks,
        task_result="achieved",
        hints_given=0,
        cefr_level=cefr,
        cefr_confidence="medium",
        cefr_evidence=("used past tense",),
        confidence_1_5=3,
        assistant_words_estimate=None,
    )


def err(said: str, correct: str = "fixed", category: Category = "grammar") -> ReportedError:
    return ReportedError(said=said, correct=correct, category=category)


def run(
    ev: Evidence, previous: CefrLevel | None = None, self_level: CefrLevel = "B1"
) -> ValidatedEvidence:
    return validate_evidence(ev, OFFERED, previous_cefr=previous, self_level=self_level)


# --- errors ----------------------------------------------------------------------


def test_errors_must_quote_a_user_turn_and_keep_the_turn_index() -> None:
    turns = ("I have went to the office yesterday.", f"Then he don't answer. {FILLER}")
    ev = evidence(
        turns,
        errors=(
            err("he don't answer", "he didn't answer"),
            err("I have went", "I went", "grammar"),
            err("something never said", "x"),
        ),
    )
    v = run(ev)
    assert v.errors == (
        ValidError(
            said="he don't answer",
            correct="he didn't answer",
            correct_norm="he didn't answer",
            category="grammar",
            turn_index=1,
        ),
        ValidError(
            said="I have went",
            correct="I went",
            correct_norm="i went",
            category="grammar",
            turn_index=0,
        ),
    )
    assert v.errors_reported == 3
    assert v.errors_rejected == 1
    assert v.low_trust is False


@pytest.mark.parametrize(
    ("said", "turn"),
    [
        # Review Focus 1: typographic text matches the same text typed plainly.
        ("I\N{RIGHT SINGLE QUOTATION MARK}m agree", "Yes, I'm agree with that plan."),
        ("I'm agree", "Yes, I\N{RIGHT SINGLE QUOTATION MARK}m agree with that plan."),
        (
            "\N{LEFT DOUBLE QUOTATION MARK}deploy\N{RIGHT DOUBLE QUOTATION MARK} it",
            'We should "deploy" it today.',
        ),
        ("I  have   went", "I have went to the standup."),
        ("I HAVE WENT", "i have went to the standup"),
        ("el código", "I checked el código yesterday."),
        ("deployed it yesterday", "I deployed it 🚀 yesterday!"),
        ("we can\N{RIGHT SINGLE QUOTATION MARK}t merge", "Honestly, we can`t merge this."),
    ],
)
def test_said_matching_ignores_typography(said: str, turn: str) -> None:
    # Review Focus 1
    v = run(evidence((turn, FILLER), errors=(err(said),)))
    assert v.errors_rejected == 0
    assert len(v.errors) == 1
    assert v.errors[0].turn_index == 0


def test_empty_or_punctuation_only_said_is_rejected() -> None:
    v = run(evidence(errors=(err("  "), err("?!"), err("word"))))
    assert v.errors_rejected == 2
    assert v.errors_reported == 3
    assert v.low_trust is True


def test_correct_norm_uses_normalize() -> None:
    v = run(evidence(errors=(err("word", "I\N{RIGHT SINGLE QUOTATION MARK}m  Done!"),)))
    assert v.errors[0].correct_norm == "i'm done"


# --- low_trust -------------------------------------------------------------------


def test_exactly_half_rejected_is_not_low_trust() -> None:
    v = run(evidence(errors=(err("word"), err("nope"))))
    assert v.low_trust is False
    assert len(v.errors) == 1


def test_more_than_half_rejected_is_low_trust_and_discards_all_errors() -> None:
    v = run(evidence(errors=(err("word"), err("nope"), err("nah"))))
    assert v.low_trust is True
    assert v.errors == ()
    assert v.errors_reported == 3
    assert v.errors_rejected == 2
    assert v.user_words == 30
    assert v.turns == 1
    assert v.status == "closed"


def test_no_errors_reported_is_not_low_trust() -> None:
    v = run(evidence())
    assert v.low_trust is False
    assert v.errors_reported == 0


# --- chunks ----------------------------------------------------------------------


def test_chunks_are_deduplicated_in_order_and_unknown_ids_rejected() -> None:
    chunks = ("it-07-c3", "it-07-c1", "it-07-c3", "it-99-c1", "it-99-c1", "it-07-c5")
    v = run(evidence(chunks=chunks))
    assert v.chunks_used == ("it-07-c3", "it-07-c1", "it-07-c5")
    assert v.chunks_rejected == 1


# --- status ----------------------------------------------------------------------


def test_no_turns_is_incomplete() -> None:
    v = run(evidence(turns=()))
    assert v.status == "incomplete"
    assert v.turns == 0
    assert v.user_words == 0


def test_fewer_than_30_words_is_incomplete_and_30_is_closed() -> None:
    assert run(evidence(turns=(" ".join(["word"] * 29),))).status == "incomplete"
    assert run(evidence(turns=("word " * 15, "word " * 15))).status == "closed"


def test_words_with_curly_apostrophes_count_once() -> None:
    # Review Focus 1
    rsq = "\N{RIGHT SINGLE QUOTATION MARK}"
    v = run(evidence(turns=(f"I{rsq}m sure it{rsq}s fine", "I'm sure it's fine")))
    assert v.user_words == 8


# --- CEFR ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reported", "previous", "self_level", "excluded"),
    [
        ("B1", None, "B1", False),
        ("B2", None, "B1", True),
        ("B1+", None, "B1", False),
        ("B2+", None, "B2", False),
        ("C1", None, "B2", True),
        ("B1", "B2", "C1", True),
        ("B2+", "B2", "B1", False),
        ("B2", "B2", "B1", False),
        ("B1+", "B2+", "B1", True),
    ],
)
def test_cefr_excluded_on_a_full_level_jump(
    reported: CefrLevel, previous: CefrLevel | None, self_level: CefrLevel, excluded: bool
) -> None:
    v = run(evidence(cefr=reported), previous=previous, self_level=self_level)
    assert v.cefr_excluded is excluded


# --- properties ------------------------------------------------------------------


@given(
    turns=st.lists(TEXT, max_size=6),
    saids=st.lists(TEXT, max_size=6),
    chunks=st.lists(st.sampled_from([*OFFERED, "it-01-c1", "bogus"]), max_size=10),
)
def test_validation_invariants(turns: list[str], saids: list[str], chunks: list[str]) -> None:
    ev = evidence(tuple(turns), tuple(err(s) for s in saids), tuple(chunks))
    v = run(ev)
    assert v.errors_reported == len(saids)
    assert v.errors_rejected + (len(v.errors) if not v.low_trust else 0) <= len(saids)
    if not v.low_trust:
        assert len(v.errors) + v.errors_rejected == len(saids)
    assert set(v.chunks_used) <= set(OFFERED)
    assert len(v.chunks_used) == len(set(v.chunks_used))
    assert len(v.chunks_used) + v.chunks_rejected == len(set(chunks))
    for e in v.errors:
        assert 0 <= e.turn_index < len(turns)


@given(st.lists(TEXT, min_size=1, max_size=5), st.data())
def test_any_slice_of_a_turn_validates(turns: list[str], data: st.DataObject) -> None:
    index = data.draw(st.integers(0, len(turns) - 1))
    words = turns[index].split()
    assume(words)
    start = data.draw(st.integers(0, len(words) - 1))
    end = data.draw(st.integers(start + 1, len(words)))
    said = " ".join(words[start:end])
    v = run(evidence(tuple(turns), (err(said),)))
    assume(normalize(said))
    assert v.errors_rejected == 0
    assert v.errors[0].turn_index <= index
