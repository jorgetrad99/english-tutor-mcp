"""Spike MCP contract: section 7 end_session schema, descriptions, response_rules (spec §3)."""

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["grammar", "lexis", "word_order", "register", "other"]
TaskResult = Literal["achieved", "partial", "not_achieved"]
Speaking = Literal["B1", "B1+", "B2", "B2+", "C1"]
Confidence = Literal["low", "medium", "high"]

# Allowed values quoted in retry errors, keyed by the field name that ends the error location.
ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "category": get_args(Category),
    "task_result": get_args(TaskResult),
    "speaking": get_args(Speaking),
    "confidence": get_args(Confidence),
}


class ErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    said: str = Field(
        title="Said",
        description="The learner's words exactly as they said them, copied from user_turns.",
    )
    correct: str = Field(
        title="Correct", description="A natural, correct version of what the learner meant."
    )
    category: Category = Field(title="Category", description="The main kind of error.")


class CefrEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaking: Speaking = Field(
        title="Speaking level", description="Your estimate of the learner's speaking level today."
    )
    confidence: Confidence = Field(title="Confidence", description="How sure you are.")
    evidence: list[str] = Field(
        title="Evidence",
        description="Short quotes or observations from this conversation supporting the level.",
    )


SessionId = Annotated[
    str,
    Field(
        title="Session ID",
        description="The session_id returned by get_profile at the start of this session.",
    ),
]
UserTurns = Annotated[
    list[str],
    Field(
        title="User turns",
        description="Everything the learner said, one entry per turn, in their exact words.",
        min_length=1,
    ),
]
Errors = Annotated[
    list[ErrorItem],
    Field(
        title="Errors",
        description="The learner's errors, each quoted exactly from user_turns. Empty if none.",
    ),
]
ChunksUsed = Annotated[
    list[str],
    Field(
        title="Chunks used",
        description="IDs of the chunks of the day the learner used. Empty if none were given.",
    ),
]
TaskResultField = Annotated[
    TaskResult,
    Field(title="Task result", description="Whether the learner reached the scenario objective."),
]
HintsGiven = Annotated[
    int,
    Field(title="Hints given", description="How many hints you gave in the scenario.", ge=0, le=3),
]
CefrField = Annotated[
    CefrEstimate,
    Field(
        title="CEFR estimate", description="Your level estimate for this session, with evidence."
    ),
]
Confidence15 = Annotated[
    int,
    Field(
        title="Learner confidence 1-5",
        description=(
            "The learner's own rating of how confident they felt today, 1 (very unsure) to 5 "
            "(very confident). Ask them during feedback if they have not said."
        ),
        ge=1,
        le=5,
    ),
]
AssistantWords = Annotated[
    int | None,
    Field(
        title="Assistant words estimate",
        description="Rough number of words you spoke in this session, if you can estimate it.",
        ge=0,
    ),
]

GET_PROFILE_DESCRIPTION = (
    "Call once at the start of every English practice session, before greeting the learner. "
    "Returns the learner's profile and a session_id. Keep the session_id: end_session needs "
    "it at the end of the session."
)
END_SESSION_DESCRIPTION = (
    "Call once at the end of every practice session, including when the learner says they "
    "have to go. Send the session_id from get_profile and evidence from this conversation "
    "only: the learner's turns in their exact words, their errors quoted exactly from those "
    "turns, the scenario result, hints given, and your level estimate with evidence. Never "
    "invent turns, errors, counts or levels."
)

GET_PROFILE_RULES = (
    "Greet the learner by name in one sentence and ask which work situation they want to "
    "practise. Do not read this profile aloud."
)
END_SESSION_RULES = (
    "Tell the learner in one sentence that the session is saved. Do not read numbers aloud."
)
RETRY_RULES = (
    "Fix the listed fields and call end_session again with the same session_id. "
    "Do not mention this to the learner."
)
UNKNOWN_SESSION = (
    "Unknown session_id. Call get_profile, then call end_session again with the session_id "
    "it returns. Do not mention this to the learner."
)
NOT_ALLOWED = "This account is not enabled for the spike."

INSTRUCTIONS = (
    "You are an English speaking tutor for a Spanish-speaking software professional. "
    "Speak English unless the learner asks otherwise. In voice sessions answer in 1–3 "
    "sentences.\n"
    "At the start of every practice session call `get_profile` and keep its `session_id`.\n"
    "Run the session in this order. Open: greet the learner and ask which work situation they "
    "want to practise. Scenario, about 10–12 minutes: play the other person, stay in "
    "character, keep your turns short, give at most 3 hints, and do not correct the learner "
    "during the scenario. Feedback: step out of character and give the 2–3 most useful "
    "corrections and one thing done well.\n"
    "At the end of the session, including when the learner says they have to go, call "
    "`end_session` with the `session_id` from `get_profile` and the evidence from this "
    "conversation: the learner's turns as they said them, their errors quoted exactly, and "
    "your level estimate with evidence. Never invent turns, errors, counts or levels that are "
    "not in the conversation.\n"
    "No markdown, lists or headings while in conversation."
)
