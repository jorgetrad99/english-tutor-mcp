"""Fixed `response_rules` strings (spec 8.4), chosen by tool, mode and state. Never user text.

If ADR 0001 (Oct 11) changes the voice or end_session wording (spec section 16), edit only
the constants here.
"""

from __future__ import annotations

from typing import Literal

from tutor.domain.validation import SessionOutcome
from tutor.services.errors import ErrorCode
from tutor.services.ports import Mode

McpErrorCode = ErrorCode | Literal["internal_error"]

GET_PROFILE_ONBOARDING = (
    "Onboarding comes first: ask the onboarding_questions one at a time in the learner's "
    "preferred language and map each answer to an allowed value; if unsure, offer the options.\n"
    "Read the five answers back in one sentence, then call save_profile."
)
GET_PROFILE_READY = (
    "Greet the learner in one sentence and name the next plan item in a few words.\n"
    "Then call start_lesson with the mode of this conversation. Never read this result aloud."
)
SAVE_PROFILE = (
    "Tell the learner in one or two sentences the plan length and the feasibility message.\n"
    "Offer to start the first lesson and call start_lesson when they agree. Never read the JSON."
)

_PROVISIONAL = (
    "First ask one yes/no question about keeping the provisional_items, then call save_glossary "
    "with status confirmed or declined, sending each item back exactly as returned, without "
    "item_id."
)
_GOAL = "State today's goal in one sentence from item.can_do_en."
_WARMUP = "Warm-up: say each of the 5 chunks once and let the learner repeat it."
_WARMUP_REVIEWS = (
    "Warm-up: say each of the 5 chunks once and let the learner repeat it, then drill up to 4 "
    "due_reviews by production and call record_review with the ratings."
)
_SCENARIO: dict[Mode, str] = {
    "voice": (
        "Scenario: play the character in scenario_brief; turns of at most 40 words, at most 3 "
        "hints (a hard limit), no corrections until the objective is reached or 12 minutes pass, "
        "unless meaning breaks down; then one short recast and continue."
    ),
    "text": (
        "Scenario: play the character in scenario_brief; turns of at most 60 words, at most 3 "
        "hints (a hard limit), no corrections until the objective is reached or 12 minutes pass, "
        "unless meaning breaks down; then one short recast and continue."
    ),
}
_CLOSE = (
    "Feedback: 2-3 corrections and one thing done well, then propose glossary items as a "
    "numbered list of at most 8 lines and save them; finally call end_session with this "
    "session_id."
)
_PLAN_DONE = "The plan is finished: at the end, suggest updating the goal on the website or here."

RECORD_REVIEW = (
    "Do not read dates or ratings aloud.\nFinish the warm-up, then start the scenario in character."
)
SAVE_GLOSSARY = (
    "Confirm in one sentence what was saved; never read the JSON.\n"
    "Do not retry rejected items.\n"
    "If this was the feedback step, save the other status if any, then call end_session; "
    "otherwise continue with today's goal and the warm-up."
)
END_SESSION: dict[SessionOutcome, str] = {
    "closed": (
        "Read summary_text once, word for word, with no lists.\nThen say goodbye in one sentence."
    ),
    "incomplete": (
        "Tell the learner in one sentence that the lesson was too short to count.\n"
        "Do not read summary_text or any numbers aloud."
    ),
}
# A repeated end_session: the learner has already heard the summary, so it is never read again.
END_SESSION_ALREADY_CLOSED = (
    "This lesson had already ended; do not read summary_text again.\n"
    "Say goodbye in one sentence and do not call end_session again."
)
ERRORS: dict[McpErrorCode, str] = {
    "onboarding_needed": (
        "Tell the learner in one sentence that a few setup questions come first.\n"
        "Call get_profile and run its onboarding; do not retry this call before save_profile."
    ),
    "session_not_found": (
        "Tell the learner in one sentence that this lesson could not be saved.\n"
        "Do not start a new lesson and do not retry."
    ),
    "session_closed": (
        "This lesson was closed by a newer one; do not retry.\n"
        "Tell the learner in one sentence that the earlier lesson was already closed."
    ),
    "rate_limited": (
        "Tell the learner in one sentence that the limit is reached and to try again later.\n"
        "Do not retry now."
    ),
    "validation_failed": (
        "Fix the listed fields and call the same tool once more; do not mention this.\n"
        "If it fails again, tell the learner in one sentence and stop retrying."
    ),
    "payload_too_large": (
        "Send fewer or shorter items and retry once.\n"
        "Do not mention this to the learner unless it fails again."
    ),
    "internal_error": (
        "Apologise to the learner in one sentence.\n"
        "Do not retry more than once; if it fails again, continue the lesson without this tool."
    ),
}
# Only end_session sends evidence, so only its payload rule names the evidence fields.
PAYLOAD_TOO_LARGE_END_SESSION = (
    "Drop the cefr evidence and the oldest errors first, then retry once; never shorten "
    "or paraphrase user_turns.\n"
    "Do not mention this to the learner unless it fails again."
)
# Only start_lesson may offer a new session after session_not_found.
SESSION_NOT_FOUND_START = (
    "Do not mention this to the learner.\n"
    "Call start_lesson once for a new session_id; do not retry more than once."
)


def get_profile_rules(*, onboarding_needed: bool) -> str:
    return GET_PROFILE_ONBOARDING if onboarding_needed else GET_PROFILE_READY


def start_lesson_rules(
    mode: Mode, *, has_provisional: bool, has_due_reviews: bool, plan_exhausted: bool
) -> str:
    """Four lines: open, warm-up, scenario, feedback and close."""
    open_line = f"{_PROVISIONAL} {_GOAL}" if has_provisional else _GOAL
    warmup = _WARMUP_REVIEWS if has_due_reviews else _WARMUP
    close = f"{_CLOSE} {_PLAN_DONE}" if plan_exhausted else _CLOSE
    return "\n".join((open_line, warmup, _SCENARIO[mode], close))


def end_session_rules(status: SessionOutcome, *, already_closed: bool) -> str:
    """`already_closed` (a repeated call) never asks for the summary to be read again."""
    if already_closed:
        return END_SESSION_ALREADY_CLOSED
    return END_SESSION[status]


def error_rules(code: McpErrorCode, tool: str | None = None) -> str:
    if code == "session_not_found" and tool == "start_lesson":
        return SESSION_NOT_FOUND_START
    if code == "payload_too_large" and tool == "end_session":
        return PAYLOAD_TOO_LARGE_END_SESSION
    return ERRORS[code]
