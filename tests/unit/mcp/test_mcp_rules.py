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
    found += [rules.end_session_rules(s, already_closed=False) for s in get_args(SessionOutcome)]
    found += [rules.end_session_rules(s, already_closed=True) for s in get_args(SessionOutcome)]
    found += [rules.error_rules(c) for c in (*get_args(ErrorCode), "internal_error")]
    found.append(rules.error_rules("session_not_found", "start_lesson"))
    found.append(rules.error_rules("payload_too_large", "end_session"))
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
    assert "summary_text once, word for word" in rules.end_session_rules(
        "closed", already_closed=False
    )
    assert "Do not read summary_text" in rules.end_session_rules("incomplete", already_closed=False)


def test_a_repeated_end_session_never_reads_the_summary_again() -> None:
    for status in get_args(SessionOutcome):
        text = rules.end_session_rules(status, already_closed=True)
        assert "do not read summary_text again" in text
        assert "word for word" not in text
        assert "end_session again" in text  # no third call either


def test_error_rules_cover_every_code_and_limit_retries() -> None:
    codes = {*get_args(ErrorCode), "internal_error"}
    assert set(rules.ERRORS) == codes
    for code in codes:
        text = rules.error_rules(code)
        assert "one sentence" in text or "Do not mention" in text or "do not mention" in text
        assert "retry" in text or "save_profile" in text


def test_section_12_conversation_rules_are_in_the_instructions() -> None:
    for phrase in (
        "at most 60 words per turn",
        "one question per turn",
        "no bold and no emoji",
        "Never summarize back what the user just said",
        "Spanish only for a meaning check",
        "numbered list",
        "at most 8 lines",
        "unless meaning breaks down",
    ):
        assert phrase.lower() in INSTRUCTIONS.lower(), phrase


def test_scenario_and_close_rules_carry_the_section_12_exceptions() -> None:
    for mode in MODES:
        text = rules.start_lesson_rules(
            mode, has_provisional=False, has_due_reviews=False, plan_exhausted=False
        )
        assert "unless meaning breaks down; then one short recast and continue" in text
        assert "at most 3 hints (a hard limit)" in text
        assert "numbered list of at most 8 lines" in text


def test_save_glossary_rules_name_the_next_action() -> None:
    assert "save the other status if any, then call end_session" in rules.SAVE_GLOSSARY
    assert "warm-up" in rules.SAVE_GLOSSARY


def test_only_the_word_limit_is_conversation_only_and_spanish_is_limited_everywhere() -> None:
    conversation, rest = INSTRUCTIONS.split("In the conversation phase", 1)[1].split(". ", 1)
    assert "at most 60 words per turn in text sessions" in conversation
    assert "Spanish" not in conversation
    assert "In every phase, feedback included, use Spanish only for a meaning check" in rest
    assert "The only exceptions: a warm-up production cue" in rest
    assert "onboarding questions and options are asked in the learner's language" in rest
    assert "exempt" not in INSTRUCTIONS


def test_payload_too_large_rule_names_evidence_only_for_end_session() -> None:
    for tool in (None, "save_glossary", "start_lesson"):
        generic = rules.error_rules("payload_too_large", tool)
        assert "user_turns" not in generic and "cefr" not in generic
        assert "retry" in generic


def test_payload_too_large_never_shortens_user_turns() -> None:
    text = rules.error_rules("payload_too_large", "end_session")
    assert "never shorten or paraphrase user_turns" in text
    assert "cefr evidence" in text


def test_provisional_items_go_back_exactly_as_returned() -> None:
    text = rules.start_lesson_rules(
        "text", has_provisional=True, has_due_reviews=False, plan_exhausted=False
    )
    assert "sending each item back exactly as returned, without item_id" in text
    description = TOOL_DESCRIPTIONS["save_glossary"]
    assert "send each one back exactly as returned, without item_id" in description
    assert "Send only items from this lesson or its provisional_items." in description
