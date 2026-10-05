import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.domain.text import count_words, find_turn, normalize

pytestmark = pytest.mark.unit

RSQ = "\N{RIGHT SINGLE QUOTATION MARK}"
LSQ = "\N{LEFT SINGLE QUOTATION MARK}"
LDQ = "\N{LEFT DOUBLE QUOTATION MARK}"
RDQ = "\N{RIGHT DOUBLE QUOTATION MARK}"
MLA = "\N{MODIFIER LETTER APOSTROPHE}"
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
ROCKET = "\N{ROCKET}"
SMILE = "\N{SLIGHTLY SMILING FACE}"


def test_lowercases_strips_punctuation_and_collapses_spaces() -> None:
    assert normalize("  Hello,   WORLD! ") == "hello world"


def test_applies_nfkc() -> None:
    assert normalize("\N{FULLWIDTH LATIN CAPITAL LETTER H}ello \N{LATIN SMALL LIGATURE FI}x") == (
        "hello fix"
    )


def test_keeps_inner_apostrophes_and_hyphens() -> None:
    assert normalize("I don't know the follow-up plan.") == "i don't know the follow-up plan"


def test_drops_apostrophes_and_hyphens_at_word_edges() -> None:
    assert normalize("'quoted' -- the users' data - today -") == "quoted the users data today"


@pytest.mark.parametrize("mark", [RSQ, LSQ, MLA, "`"])
def test_typographic_apostrophes_become_straight(mark: str) -> None:
    assert normalize(f"I{mark}m blocked") == "i'm blocked"


def test_curly_double_quotes_are_stripped() -> None:
    assert normalize(f"He said {LDQ}ship it{RDQ}.") == "he said ship it"


# Review Focus 1
def test_curly_apostrophe_matches_plain_typing() -> None:
    assert normalize(f"I{RSQ}m blocked on the API") == normalize("i'm blocked on the API")
    assert find_turn("I'm blocked on", [f"Yesterday, I{RSQ}m blocked on the API keys."]) == 0


# Review Focus 1
def test_accents_are_kept() -> None:
    assert normalize("Está bien, ¿sí?") == "está bien sí"


# Review Focus 1
def test_emoji_are_removed() -> None:
    assert normalize(f"Ship it {ROCKET}{ROCKET}! {SMILE}") == "ship it"


# Review Focus 1
def test_double_spaces_tabs_and_newlines_collapse() -> None:
    assert normalize("we  need\t\tmore\n\ntime") == "we need more time"


# Review Focus 1
def test_spanish_text_and_marks() -> None:
    assert normalize("¡Qué onda! ¿Cómo estás… güey?") == "qué onda cómo estás güey"


# Review Focus 1
def test_mixed_typography_said_matches_plain_turn() -> None:
    said = f"  I{RSQ}ve  been stuck {ELLIPSIS} on this {SMILE} "
    turns = ["Hi Tom.", "Sorry, I've been stuck on this for two hours"]
    assert find_turn(said, turns) == 1


def test_count_words_uses_the_metrics_regex() -> None:
    assert count_words("Yesterday I worked on the login bug.") == 7
    assert count_words(f"I{RSQ}m done, don't worry") == 4
    assert count_words("") == 0
    assert count_words(f"{ROCKET} ... !!!") == 0


def test_count_words_counts_ascii_tokens_only() -> None:
    # Spec 11.3: tokens are [A-Za-z0-9]+; an accented letter splits or ends a token.
    assert count_words("Está bien") == 2
    assert count_words("v2 API 3 times") == 4


def test_find_turn_respects_after() -> None:
    turns = ["I go to the office", "then I go to the office again"]
    assert find_turn("go to the office", turns) == 0
    assert find_turn("go to the office", turns, after=0) == 1
    assert find_turn("go to the office", turns, after=1) is None


def test_find_turn_empty_needle_or_no_match() -> None:
    assert find_turn("", ["anything"]) is None
    assert find_turn("?!", ["anything ?!"]) is None
    assert find_turn("I have 30 years", ["I am thirty", "We ship on Friday"]) is None
    assert find_turn("anything", []) is None


def test_find_turn_never_spans_two_turns() -> None:
    assert find_turn("broken we need", ["It was broken.", "We need time."]) is None


LEARNER_ALPHABET = st.sampled_from(
    list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    + list("áéíóúñüÁÉÍÓÚÑÜ¿¡")
    + list(" \t\n.,;:!?-'\"()/")
    + [RSQ, LSQ, LDQ, RDQ, MLA, "`", ELLIPSIS, ROCKET, SMILE]
)
learner_text = st.text(alphabet=LEARNER_ALPHABET, max_size=80)


@given(st.text(max_size=80))
def test_normalize_is_idempotent(text: str) -> None:
    once = normalize(text)
    assert normalize(once) == once


@given(learner_text)
def test_normalize_is_idempotent_on_learner_text(text: str) -> None:
    once = normalize(text)
    assert normalize(once) == once


@given(learner_text)
def test_normalize_never_longer_than_input(text: str) -> None:
    # Learner-typed characters; NFKC can expand compatibility characters such as ligatures.
    assert len(normalize(text)) <= len(text)


@given(learner_text)
def test_normalized_output_has_no_edge_spaces_or_loose_marks(text: str) -> None:
    out = normalize(text)
    assert out == out.strip()
    assert "  " not in out
    for word in out.split(" "):
        assert not word.startswith(("'", "-"))
        assert not word.endswith(("'", "-"))


@given(st.lists(learner_text, max_size=6), learner_text, st.integers(min_value=-1, max_value=6))
def test_find_turn_is_consistent_with_normalize(turns: list[str], needle: str, after: int) -> None:
    found = find_turn(needle, turns, after=after)
    target = normalize(needle)
    matches = [i for i in range(after + 1, len(turns)) if target and target in normalize(turns[i])]
    assert found == (matches[0] if matches else None)


@given(st.lists(learner_text, min_size=1, max_size=6), st.data())
def test_every_turn_finds_itself_or_an_earlier_turn(turns: list[str], data: st.DataObject) -> None:
    k = data.draw(st.integers(min_value=0, max_value=len(turns) - 1))
    found = find_turn(turns[k], turns)
    if normalize(turns[k]):
        assert found is not None
        assert found <= k
    else:
        assert found is None
