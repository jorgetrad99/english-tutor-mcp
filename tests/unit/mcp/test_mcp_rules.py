import itertools
from typing import get_args

import pytest

from tutor.domain.validation import SessionOutcome
from tutor.mcp import rules
from tutor.mcp.instructions import (
    INSTRUCTIONS,
    START_LESSON_PROMPT,
    TOOL_DESCRIPTIONS,
)
from tutor.services.errors import ErrorCode
from tutor.services.ports import Mode

pytestmark = pytest.mark.unit

MODES: tuple[Mode, ...] = ("voice", "text")
TOOLS = {"get_profile", "save_profile", "start_lesson", "record_review", "save_glossary"}


def words(text: str) -> int:
    return len(text.split())


def all_rules() -> list[str]:
    found = [
        rules.get_profile_rules(onboarding_needed=True),
        rules.get_profile_rules(onboarding_needed=False),
        rules.SAVE_PROFILE,
        rules.RECORD_REVIEW,
        rules.SAVE_GLOSSARY,
    ]
    for mode, prov, due, done in itertools.product(
        MODES, (True, False), (True, False), (True, False)
    ):
        found.append(
            rules.start_lesson_rules(
                mode, has_provisional=prov, has_due_reviews=due, plan_exhausted=done
            )
        )
    found += [rules.end_session_rules(s) for s in get_args(SessionOutcome)]
    found += [rules.end_session_rules(s, already_closed=True) for s in get_args(SessionOutcome)]
    found += [rules.error_rules(c) for c in get_args(ErrorCode)]
    return found


def test_instructions_fit_400_words_and_carry_the_spec_sentences() -> None:
    assert words(INSTRUCTIONS) <= 400
    for sentence in (
        "Always call `start_lesson` at the beginning of a lesson and `end_session` at the end",
        "On first use, call `get_profile`; if it says `onboarding_needed`, run the onboarding "
        "before any lesson.",
        "save the ones the user keeps as `confirmed` and the ones they drop as `declined`",
        "No markdown, lists or headings while in conversation.",
        "never mid-sentence",
        "In voice sessions answer in 1-3 sentences.",
    ):
        assert sentence in INSTRUCTIONS


def test_six_tool_descriptions_fit_120_words() -> None:
    assert set(TOOL_DESCRIPTIONS) == {*TOOLS, "end_session"}
    for name, text in TOOL_DESCRIPTIONS.items():
        assert 0 < words(text) <= 120, name


def test_record_review_says_an_item_is_graded_at_most_once_a_day() -> None:
    text = TOOL_DESCRIPTIONS["record_review"]
    assert "at most once a day" in text
    assert "once per lesson" not in text


def test_end_session_description_never_asks_for_a_second_summary() -> None:
    text = TOOL_DESCRIPTIONS["end_session"]
    assert "already_closed" in text
    assert "do not read the summary again" in text


def test_prompt_starts_with_get_profile_then_start_lesson() -> None:
    assert START_LESSON_PROMPT.index("get_profile") < START_LESSON_PROMPT.index("start_lesson")
    assert "save_profile" in START_LESSON_PROMPT


def test_every_rules_string_has_two_to_four_lines() -> None:
    for text in all_rules():
        assert 2 <= len(text.splitlines()) <= 4, text
        assert "{" not in text and "}" not in text, text


def test_start_lesson_rules_follow_mode_and_state() -> None:
    voice = rules.start_lesson_rules(
        "voice", has_provisional=True, has_due_reviews=True, plan_exhausted=False
    )
    text = rules.start_lesson_rules(
        "text", has_provisional=False, has_due_reviews=False, plan_exhausted=True
    )
    assert "at most 40 words" in voice and "at most 60 words" in text
    assert "provisional_items" in voice and "provisional_items" not in text
    assert "record_review" in voice and "record_review" not in text
    assert "plan is finished" in text and "plan is finished" not in voice
    assert "no corrections until the objective is reached or 12 minutes pass" in voice
    assert "end_session" in voice and "end_session" in text


def test_end_session_rules_read_the_summary_only_when_closed() -> None:
    assert "summary_text once, word for word" in rules.end_session_rules("closed")
    assert "Do not read summary_text" in rules.end_session_rules("incomplete")


def test_a_repeated_end_session_never_reads_the_summary_again() -> None:
    for status in get_args(SessionOutcome):
        text = rules.end_session_rules(status, already_closed=True)
        assert "do not read it again" in text
        assert "word for word" not in text
        assert "end_session again" in text  # no third call either


def test_error_rules_cover_every_code_and_limit_retries() -> None:
    assert set(rules.ERRORS) == set(get_args(ErrorCode))
    for code in get_args(ErrorCode):
        text = rules.error_rules(code)
        assert "one sentence" in text or "Do not mention" in text or "do not mention" in text
        assert "retry" in text or "save_profile" in text
