from tutor_spike.normalize import normalize, said_in_turns


def test_normalize_lowercases_strips_punctuation_and_collapses_spaces() -> None:
    assert normalize("  Hello,   WORLD! ") == "hello world"


def test_normalize_applies_nfkc() -> None:
    assert normalize("Ｈｅｌｌｏ ﬁx") == "hello fix"


def test_normalize_treats_curly_and_straight_apostrophes_alike() -> None:
    assert normalize("I don't knew") == normalize("I don't knew") == "i dont knew"


def test_normalize_keeps_accented_letters() -> None:
    assert normalize("Está bien") == "está bien"


def test_said_found_inside_a_turn_despite_punctuation() -> None:
    turns = ["Yesterday, I go to the office… and the deploy was broken."]
    assert said_in_turns("yesterday I go to the office", turns)


def test_said_from_speech_to_text_with_curly_quote_matches() -> None:
    assert said_in_turns("I don't knew,", ["Sorry, I don't knew the answer"])


def test_said_absent_from_every_turn_fails() -> None:
    assert not said_in_turns("I have 30 years", ["I am thirty", "We ship on Friday"])


def test_said_spanning_two_turns_fails() -> None:
    assert not said_in_turns("broken we need", ["It was broken.", "We need time."])


def test_empty_or_punctuation_only_said_fails() -> None:
    assert not said_in_turns("", ["anything"])
    assert not said_in_turns("?!", ["anything ?!"])
