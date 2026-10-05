"""Tool inputs and outputs (spec 8.1). Inputs: extra="forbid", closed enums, title + description.

Each input field is an Annotated alias used twice: by the input model and by the tool
function's signature in `tutor.mcp.server`, which checks at startup that both schemas agree.
Every input model, top level and nested, forbids unknown keys: FastMCP rejects unknown nested
keys only when the nested model says so. Every output field carries a title and a description.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tutor.domain.glossary import (
    CONTEXT_MAX,
    MEANING_MAX,
    TEXT_MAX,
    GlossaryKind,
    IncomingItem,
    RejectReason,
    SaveStatus,
)
from tutor.domain.lesson import BriefVariant, ReviewFormat, TaskResult
from tutor.domain.levels import CefrLevel
from tutor.domain.metrics import metrics_to_json
from tutor.domain.plan_lite import Feasibility, Variant, feasibility_text
from tutor.domain.profile import (
    GOAL_TEXT_MAX,
    MAX_DAYS_PER_WEEK,
    MAX_USE_CASES,
    MIN_DAYS_PER_WEEK,
    MIN_USE_CASES,
    ONBOARDING_QUESTIONS,
    Domain,
    Profile,
    ProfileInput,
    UseCase,
)
from tutor.domain.track import InteractionType, Skill, TrackLevel
from tutor.domain.validation import Category, Confidence, Evidence, ReportedError, SessionOutcome
from tutor.services.errors import ServiceError
from tutor.services.glossary import MAX_GLOSSARY_ITEMS
from tutor.services.lesson import MAX_MINUTES, MAX_PREP_CHARS, MAX_REVIEW_RESULTS, MIN_MINUTES
from tutor.services.ports import Mode, PlanItemStatus
from tutor.services.session_end import MAX_RAW_EVIDENCE_BYTES
from tutor.services.views import (
    EndSessionResult,
    GlossarySaveResult,
    LessonStart,
    PlanItemView,
    PlanSummary,
    ProfileView,
    ReviewResultView,
    SaveProfileResult,
    StartLessonRequest,
)

# Caps with no constant in the domain or the services (spec sections 5 and 7).
MAX_USER_TURNS = 200
MAX_USER_TURN_CHARS = 2000
MAX_REPORTED_ERRORS = 50
MAX_ERROR_TEXT_CHARS = 300
MAX_CHUNK_IDS = 10
MAX_CHUNK_ID_CHARS = 40
MAX_CEFR_EVIDENCE = 5
MAX_CEFR_EVIDENCE_CHARS = 200
RAW_EVIDENCE_MAX_BYTES = MAX_RAW_EVIDENCE_BYTES


class StrictInput(BaseModel):
    """Base of every input model, nested ones included: unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")


# ---------- shared fields

SessionIdField = Annotated[
    UUID,
    Field(
        title="Session ID",
        description="The session_id returned by start_lesson for this lesson.",
    ),
]
DomainField = Annotated[
    Domain, Field(title="Field", description="The learner's work field. Only 'it' for now.")
]

# ---------- get_profile


class GetProfileInput(StrictInput):
    """get_profile takes no arguments."""


# ---------- save_profile

SelfLevel = Annotated[
    CefrLevel,
    Field(
        title="Current level",
        description="The learner's own rating of their English today, from the onboarding.",
    ),
]
Domains = Annotated[
    list[Domain],
    Field(
        title="Fields",
        description="The learner's work field. Only 'it' for now.",
        min_length=1,
        max_length=1,
    ),
]
UseCases = Annotated[
    list[UseCase],
    Field(
        title="Use cases",
        description=(
            f"{MIN_USE_CASES} to {MAX_USE_CASES} work situations where the learner needs "
            "English, without repeats."
        ),
        min_length=MIN_USE_CASES,
        max_length=MAX_USE_CASES,
    ),
]
MinutesPerDay = Annotated[
    Literal[15, 20, 30],
    Field(title="Minutes per day", description="Practice minutes per day the learner chose."),
]
DaysPerWeek = Annotated[
    int,
    Field(
        title="Days per week",
        description=f"Practice days per week, from {MIN_DAYS_PER_WEEK} to {MAX_DAYS_PER_WEEK}.",
        ge=MIN_DAYS_PER_WEEK,
        le=MAX_DAYS_PER_WEEK,
    ),
]
TargetLevel = Annotated[
    CefrLevel,
    Field(
        title="Target level",
        description="The level the learner wants to reach; not below the current level.",
    ),
]
TargetDate = Annotated[
    date | None,
    Field(
        title="Target date",
        description="Optional date to reach the target, YYYY-MM-DD, 28 to 364 days from today.",
    ),
]
GoalText = Annotated[
    str | None,
    Field(
        title="Goal",
        description=(
            f"Optional goal in the learner's own words, at most {GOAL_TEXT_MAX} characters."
        ),
        max_length=GOAL_TEXT_MAX,
    ),
]


class SaveProfileInput(StrictInput):
    self_level: SelfLevel
    domains: Domains
    use_cases: UseCases
    minutes_per_day: MinutesPerDay
    days_per_week: DaysPerWeek
    target_level: TargetLevel
    target_date: TargetDate = None
    goal_text: GoalText = None

    def to_profile_input(self) -> ProfileInput:
        return ProfileInput(
            self_level=self.self_level,
            domains=tuple(self.domains),
            use_cases=tuple(self.use_cases),
            minutes_per_day=self.minutes_per_day,
            days_per_week=self.days_per_week,
            target_level=self.target_level,
            target_date=self.target_date,
            goal_text=self.goal_text,
            timezone=None,
        )


# ---------- start_lesson

LessonMode = Annotated[
    Mode,
    Field(
        title="Mode",
        description="'voice' when the learner is speaking in a voice conversation, else 'text'.",
    ),
]
Prep = Annotated[
    str | None,
    Field(
        title="Prep event",
        description=(
            "Optional real event to prepare for, in the learner's words, at most "
            f"{MAX_PREP_CHARS} characters. Send it together with prep_use_case."
        ),
        max_length=MAX_PREP_CHARS,
    ),
]
PrepUseCase = Annotated[
    UseCase | None,
    Field(
        title="Prep use case",
        description="The work situation that best matches prep. Required when prep is sent.",
    ),
]
Minutes = Annotated[
    int | None,
    Field(
        title="Minutes",
        description=f"Optional lesson length in minutes, from {MIN_MINUTES} to {MAX_MINUTES}.",
        ge=MIN_MINUTES,
        le=MAX_MINUTES,
    ),
]


class StartLessonInput(StrictInput):
    mode: LessonMode
    prep: Prep = None
    prep_use_case: PrepUseCase = None
    minutes: Minutes = None
    domain: DomainField = "it"

    def to_request(self) -> StartLessonRequest:
        prep = (self.prep or "").strip() or None
        if (prep is None) != (self.prep_use_case is None):
            raise ServiceError("validation_failed", ("prep", "prep_use_case"))
        return StartLessonRequest(
            mode=self.mode,
            prep=prep,
            prep_use_case=self.prep_use_case,
            minutes=self.minutes,
            domain=self.domain,
        )


# ---------- record_review


class ReviewResultInput(StrictInput):
    item_id: UUID = Field(
        title="Item ID", description="The item_id of a due review from start_lesson."
    )
    rating: Literal[1, 2, 3, 4] = Field(
        title="Rating",
        description=(
            "1 = could not produce it, 2 = produced it with help or errors, "
            "3 = produced it correctly, 4 = produced it at once and naturally."
        ),
    )


ReviewResults = Annotated[
    list[ReviewResultInput],
    Field(
        title="Results",
        description=f"One rating per drilled review item, at most {MAX_REVIEW_RESULTS}.",
        min_length=1,
        max_length=MAX_REVIEW_RESULTS,
    ),
]


class RecordReviewInput(StrictInput):
    session_id: SessionIdField
    results: ReviewResults


# ---------- save_glossary


class GlossaryItemInput(StrictInput):
    kind: GlossaryKind = Field(
        title="Kind",
        description="'correction' for a fixed error, 'chunk' for a phrase, 'term' for a word.",
    )
    text: str = Field(
        title="Text",
        description=f"The correct English to learn, at most {TEXT_MAX} characters.",
        max_length=TEXT_MAX,
    )
    meaning: str = Field(
        title="Meaning",
        description=f"A short meaning or Spanish translation, at most {MEANING_MAX} characters.",
        max_length=MEANING_MAX,
    )
    context_sentence: str = Field(
        title="Context sentence",
        description=(
            f"A sentence from this lesson that uses the text, at most {CONTEXT_MAX} characters."
        ),
        max_length=CONTEXT_MAX,
    )
    domain: DomainField

    def to_incoming(self) -> IncomingItem:
        return IncomingItem(
            kind=self.kind,
            text=self.text,
            meaning=self.meaning,
            context_sentence=self.context_sentence,
            domain=self.domain,
        )


GlossaryStatusField = Annotated[
    SaveStatus,
    Field(
        title="Status",
        description=(
            "'confirmed' for items the learner kept, 'declined' for items they dropped, "
            "'provisional' only if the lesson ended before they answered."
        ),
    ),
]
GlossaryItems = Annotated[
    list[GlossaryItemInput],
    Field(
        title="Items",
        description=f"Items that share this status, at most {MAX_GLOSSARY_ITEMS} per call.",
        min_length=1,
        max_length=MAX_GLOSSARY_ITEMS,
    ),
]


class SaveGlossaryInput(StrictInput):
    session_id: SessionIdField
    status: GlossaryStatusField
    items: GlossaryItems


# ---------- end_session (requirements section 7 schema, as in the spike contract)


class ErrorInput(StrictInput):
    said: str = Field(
        title="Said",
        description="The learner's words exactly as they said them, copied from user_turns.",
        max_length=MAX_ERROR_TEXT_CHARS,
    )
    correct: str = Field(
        title="Correct",
        description="A natural, correct version of what the learner meant.",
        max_length=MAX_ERROR_TEXT_CHARS,
    )
    category: Category = Field(title="Category", description="The main kind of error.")


class CefrEstimateInput(StrictInput):
    speaking: CefrLevel = Field(
        title="Speaking level", description="Your estimate of the learner's speaking level today."
    )
    confidence: Confidence = Field(title="Confidence", description="How sure you are.")
    evidence: list[Annotated[str, Field(max_length=MAX_CEFR_EVIDENCE_CHARS)]] = Field(
        title="Evidence",
        description=(
            f"Up to {MAX_CEFR_EVIDENCE} short quotes or observations from this lesson "
            "supporting the level."
        ),
        max_length=MAX_CEFR_EVIDENCE,
    )


UserTurns = Annotated[
    list[Annotated[str, Field(max_length=MAX_USER_TURN_CHARS)]],
    Field(
        title="User turns",
        description="Everything the learner said, one entry per turn, in their exact words.",
        min_length=1,
        max_length=MAX_USER_TURNS,
    ),
]
ReportedErrors = Annotated[
    list[ErrorInput],
    Field(
        title="Errors",
        description="The learner's errors, each quoted exactly from user_turns. Empty if none.",
        max_length=MAX_REPORTED_ERRORS,
    ),
]
ChunksUsed = Annotated[
    list[Annotated[str, Field(max_length=MAX_CHUNK_ID_CHARS)]],
    Field(
        title="Chunks used",
        description="IDs of the lesson's chunks the learner used. Empty if none.",
        max_length=MAX_CHUNK_IDS,
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
    CefrEstimateInput,
    Field(title="CEFR estimate", description="Your level estimate for this lesson, with evidence."),
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
        description="Rough number of words you spoke in this lesson, if you can estimate it.",
        ge=0,
        le=100_000,
    ),
]


class EndSessionInput(StrictInput):
    session_id: SessionIdField
    user_turns: UserTurns
    errors: ReportedErrors
    chunks_used: ChunksUsed
    task_result: TaskResultField
    hints_given: HintsGiven
    cefr_estimate: CefrField
    confidence_1_5: Confidence15
    assistant_words_estimate: AssistantWords = None

    def to_evidence(self) -> Evidence:
        return Evidence(
            user_turns=tuple(self.user_turns),
            errors=tuple(
                ReportedError(said=e.said, correct=e.correct, category=e.category)
                for e in self.errors
            ),
            chunks_used=tuple(self.chunks_used),
            task_result=self.task_result,
            hints_given=self.hints_given,
            cefr_level=self.cefr_estimate.speaking,
            cefr_confidence=self.cefr_estimate.confidence,
            cefr_evidence=tuple(self.cefr_estimate.evidence),
            confidence_1_5=self.confidence_1_5,
            assistant_words_estimate=self.assistant_words_estimate,
        )

    def raw_evidence(self) -> dict[str, Any]:
        """The payload as stored in sessions.raw_evidence; over 20 KB is payload_too_large.

        Measured like the service does (UTF-8 JSON without ASCII escaping).
        """
        raw = self.model_dump(mode="json", exclude={"session_id"})
        if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > RAW_EVIDENCE_MAX_BYTES:
            raise ServiceError("payload_too_large")
        return raw


# ---------- outputs (structured content; user text appears only as data fields)


def out(title: str, description: str) -> Any:
    """An output field with its title and description."""
    return Field(title=title, description=description)


class OptionOut(BaseModel):
    value: str = out("Value", "The value to send back for this option.")
    label_en: str = out("Label (English)", "Short option label in English.")
    label_es: str = out("Label (Spanish)", "Short option label in Spanish.")
    description_en: str = out("Description (English)", "One-line explanation in English.")
    description_es: str = out("Description (Spanish)", "One-line explanation in Spanish.")


class QuestionOut(BaseModel):
    id: str = out("Question ID", "Stable identifier of the onboarding question.")
    fields: list[str] = out("Profile fields", "The profile fields this answer fills.")
    prompt_en: str = out("Prompt (English)", "The question to ask, in English.")
    prompt_es: str = out("Prompt (Spanish)", "The question to ask, in Spanish.")
    options: list[OptionOut] = out("Options", "The allowed answers.")
    multi: bool = out("Multiple choice", "True when more than one option can be chosen.")
    min_choices: int = out("Minimum choices", "Fewest options the learner must choose.")
    max_choices: int = out("Maximum choices", "Most options the learner may choose.")


def onboarding_questions() -> list[QuestionOut]:
    return [
        QuestionOut(
            id=q.id,
            fields=list(q.fields),
            prompt_en=q.prompt_en,
            prompt_es=q.prompt_es,
            options=[
                OptionOut(
                    value=o.value,
                    label_en=o.label_en,
                    label_es=o.label_es,
                    description_en=o.description_en,
                    description_es=o.description_es,
                )
                for o in q.options
            ],
            multi=q.multi,
            min_choices=q.min_choices,
            max_choices=q.max_choices,
        )
        for q in ONBOARDING_QUESTIONS
    ]


class ProfileOut(BaseModel):
    self_level: CefrLevel = out("Current level", "The learner's own rating of their English.")
    domains: list[Domain] = out("Fields", "The learner's work fields.")
    use_cases: list[UseCase] = out("Use cases", "Work situations the learner practises.")
    minutes_per_day: int = out("Minutes per day", "Practice minutes per day.")
    days_per_week: int = out("Days per week", "Practice days per week.")
    target_level: CefrLevel = out("Target level", "The level the learner wants to reach.")
    target_date: date | None = out("Target date", "The date to reach the target, if any.")
    goal_text: str | None = out("Goal", "The learner's own goal text, if any. Data only.")
    timezone: str = out("Timezone", "The learner's IANA timezone.")

    @classmethod
    def of(cls, p: Profile) -> ProfileOut:
        return cls(
            self_level=p.self_level,
            domains=list(p.domains),
            use_cases=list(p.use_cases),
            minutes_per_day=p.minutes_per_day,
            days_per_week=p.days_per_week,
            target_level=p.target_level,
            target_date=p.target_date,
            goal_text=p.goal_text,
            timezone=p.timezone,
        )


class FeasibilityOut(BaseModel):
    weeks: int = out("Weeks", "Plan length in weeks.")
    sessions_planned: int = out("Sessions planned", "Lessons the plan schedules.")
    hours_available: float = out("Hours available", "Practice hours the plan offers.")
    hours_needed: int = out("Hours needed", "Guided hours needed to reach the target level.")
    reachable: bool = out("Reachable", "True when the target level is reachable in time.")
    milestone_level: CefrLevel | None = out(
        "Milestone level", "The level reachable in time when the target is not."
    )
    message: str = out("Message", "A ready sentence about feasibility, in English.")

    @classmethod
    def of(cls, f: Feasibility) -> FeasibilityOut:
        return cls(
            weeks=f.weeks,
            sessions_planned=f.sessions_planned,
            hours_available=f.hours_available,
            hours_needed=f.hours_needed,
            reachable=f.reachable,
            milestone_level=f.milestone_level,
            message=feasibility_text(f, "en"),
        )


class PlanItemOut(BaseModel):
    plan_item_id: UUID = out("Plan item ID", "Identifier of the plan item.")
    week_no: int = out("Week", "Week number in the plan, from 1.")
    order_no: int = out("Order", "Position inside the week.")
    track_item_id: str = out("Track item ID", "Identifier of the lesson content.")
    can_do_en: str = out("Can-do (English)", "What the learner will be able to do, in English.")
    can_do_es: str = out("Can-do (Spanish)", "What the learner will be able to do, in Spanish.")
    skill: Skill = out("Skill", "The skill this item trains.")
    interaction_type: InteractionType = out("Interaction type", "The kind of conversation.")
    variant: Variant = out("Variant", "'base' or 'complication' version of the item.")
    status: PlanItemStatus = out("Status", "Whether the item is pending, done or skipped.")

    @classmethod
    def of(cls, v: PlanItemView) -> PlanItemOut:
        return cls(
            plan_item_id=v.plan_item_id,
            week_no=v.week_no,
            order_no=v.order_no,
            track_item_id=v.track_item_id,
            can_do_en=v.can_do_en,
            can_do_es=v.can_do_es,
            skill=v.skill,
            interaction_type=v.interaction_type,
            variant=v.variant,
            status=v.status,
        )


class PlanOut(BaseModel):
    version: int = out("Version", "Plan version; it grows when the profile changes the plan.")
    current_week_no: int = out("Current week", "The week the learner is in.")
    weeks: int = out("Weeks", "Plan length in weeks.")
    sessions_planned: int = out("Sessions planned", "Lessons the plan schedules.")
    sessions_done: int = out("Sessions done", "Plan lessons already completed.")
    week_items: list[PlanItemOut] = out("Week items", "The plan items of the current week.")
    next_item: PlanItemOut | None = out("Next item", "The next pending plan item, if any.")
    feasibility: FeasibilityOut = out("Feasibility", "Whether the target is reachable in time.")

    @classmethod
    def of(cls, s: PlanSummary) -> PlanOut:
        return cls(
            version=s.version,
            current_week_no=s.current_week_no,
            weeks=s.weeks,
            sessions_planned=s.sessions_planned,
            sessions_done=s.sessions_done,
            week_items=[PlanItemOut.of(v) for v in s.week_items],
            next_item=PlanItemOut.of(s.next_item) if s.next_item else None,
            feasibility=FeasibilityOut.of(s.feasibility),
        )


class GetProfileOutput(BaseModel):
    onboarding_needed: bool = out("Onboarding needed", "True until the learner has a profile.")
    onboarding_questions: list[QuestionOut] = out(
        "Onboarding questions", "The questions to ask, empty when onboarding is done."
    )
    profile: ProfileOut | None = out("Profile", "The learner's profile, if saved.")
    plan: PlanOut | None = out("Plan", "The active plan summary, if any.")
    streak: int = out("Streak", "Consecutive practice days.")
    open_session_id: UUID | None = out("Open session", "An unfinished lesson, if any.")
    provisional_count: int = out("Provisional count", "Glossary items waiting for a decision.")
    due_reviews_count: int = out("Due reviews", "Glossary items due for review.")
    response_rules: str = out("Response rules", "What to do next. Follow these.")

    @classmethod
    def of(cls, v: ProfileView, rules: str) -> GetProfileOutput:
        return cls(
            onboarding_needed=v.onboarding_needed,
            onboarding_questions=onboarding_questions() if v.onboarding_needed else [],
            profile=ProfileOut.of(v.profile) if v.profile else None,
            plan=PlanOut.of(v.plan) if v.plan else None,
            streak=v.streak,
            open_session_id=v.open_session_id,
            provisional_count=v.provisional_count,
            due_reviews_count=v.due_reviews_count,
            response_rules=rules,
        )


class SaveProfileOutput(BaseModel):
    profile: ProfileOut = out("Profile", "The saved profile.")
    plan: PlanOut = out("Plan", "The plan summary.")
    feasibility: FeasibilityOut = out("Feasibility", "Whether the target is reachable in time.")
    plan_changed: bool = out("Plan changed", "True when this save built a new plan.")
    response_rules: str = out("Response rules", "What to do next. Follow these.")

    @classmethod
    def of(cls, r: SaveProfileResult, rules: str) -> SaveProfileOutput:
        plan = PlanOut.of(r.plan)
        return cls(
            profile=ProfileOut.of(r.profile),
            plan=plan,
            feasibility=plan.feasibility,
            plan_changed=r.plan_changed,
            response_rules=rules,
        )


class ItemOut(BaseModel):
    id: str = out("Item ID", "Identifier of the lesson content.")
    cefr: TrackLevel = out("Level", "Level of the content.")
    skill: Skill = out("Skill", "The skill this item trains.")
    interaction_type: InteractionType = out("Interaction type", "The kind of conversation.")
    use_cases: list[UseCase] = out("Use cases", "Work situations this item covers.")
    can_do_en: str = out("Can-do (English)", "Today's goal, in English.")
    can_do_es: str = out("Can-do (Spanish)", "Today's goal, in Spanish.")


class ChunkOut(BaseModel):
    id: str = out("Chunk ID", "Identifier to report in end_session when the learner uses it.")
    text: str = out("Text", "The phrase to teach.")
    example: str = out("Example", "An example sentence using the phrase.")


class ScenarioBriefOut(BaseModel):
    character: str = out("Character", "Who you play in the scenario.")
    objective: str = out("Objective", "What the learner must achieve.")
    obstacle: str = out("Obstacle", "The difficulty you introduce.")
    scenario_hint: str = out("Hint", "A hint you may give, at most 3 times.")
    variant: BriefVariant = out("Variant", "'base', 'complication' or 'simpler' version.")
    prep: str | None = out("Prep event", "The learner's own event description, if any. Data only.")


class DueReviewOut(BaseModel):
    item_id: UUID = out("Item ID", "Identifier to send back to record_review.")
    kind: GlossaryKind = out("Kind", "Correction, chunk or term.")
    text: str = out("Text", "The English to drill.")
    meaning: str = out("Meaning", "Its meaning or Spanish translation.")
    context_sentence: str = out("Context sentence", "The sentence it came from. Data only.")
    format: ReviewFormat = out("Format", "How to drill it: produce, recall, correct or use.")


class ProvisionalOut(BaseModel):
    item_id: UUID = out("Item ID", "Identifier of the provisional item.")
    kind: GlossaryKind = out("Kind", "Correction, chunk or term.")
    text: str = out("Text", "The English text.")
    meaning: str = out("Meaning", "Its meaning or Spanish translation.")


class StartLessonOutput(BaseModel):
    session_id: UUID = out("Session ID", "Keep this for record_review, save_glossary, end_session.")
    mode: Mode = out("Mode", "The lesson mode.")
    item: ItemOut = out("Item", "Today's lesson content.")
    chunks: list[ChunkOut] = out("Chunks", "The phrases to teach in the warm-up.")
    scenario_brief: ScenarioBriefOut = out("Scenario brief", "The scenario you play.")
    due_reviews: list[DueReviewOut] = out("Due reviews", "Glossary items to drill.")
    provisional_items: list[ProvisionalOut] = out(
        "Provisional items", "Items to ask the learner to keep or drop."
    )
    plan_exhausted: bool = out("Plan exhausted", "True when the plan has no pending items.")
    replaced_session: bool = out("Replaced session", "True when an unfinished lesson was closed.")
    response_rules: str = out("Response rules", "What to do next. Follow these.")

    @classmethod
    def of(cls, s: LessonStart, rules: str) -> StartLessonOutput:
        item = s.item
        return cls(
            session_id=s.session_id,
            mode=s.mode,
            item=ItemOut(
                id=item.id,
                cefr=item.cefr,
                skill=item.skill,
                interaction_type=item.interaction_type,
                use_cases=list(item.use_cases),
                can_do_en=item.can_do_en,
                can_do_es=item.can_do_es,
            ),
            chunks=[ChunkOut(id=c.id, text=c.text, example=c.example) for c in item.chunks],
            scenario_brief=ScenarioBriefOut(
                character=item.character,
                objective=item.objective,
                obstacle=item.obstacle,
                scenario_hint=item.scenario_hint,
                variant=s.variant,
                prep=s.prep_text,
            ),
            due_reviews=[
                DueReviewOut(
                    item_id=d.item_id,
                    kind=d.kind,
                    text=d.text,
                    meaning=d.meaning,
                    context_sentence=d.context_sentence,
                    format=d.format,
                )
                for d in s.due_reviews
            ],
            provisional_items=[
                ProvisionalOut(item_id=p.item_id, kind=p.kind, text=p.text, meaning=p.meaning)
                for p in s.provisional_items
            ],
            plan_exhausted=s.plan_exhausted,
            replaced_session=s.replaced_session,
            response_rules=rules,
        )


class ReviewOut(BaseModel):
    item_id: UUID = out("Item ID", "The reviewed item.")
    next_due: date = out("Next due", "When the item is due again. Never read aloud.")
    outcome: Literal["recorded", "already_recorded"] = out(
        "Outcome", "'recorded', or 'already_recorded' when the item was already graded today."
    )


class RecordReviewOutput(BaseModel):
    results: list[ReviewOut] = out("Results", "One entry per rated item.")
    response_rules: str = out("Response rules", "What to do next. Follow these.")

    @classmethod
    def of(cls, views: tuple[ReviewResultView, ...], rules: str) -> RecordReviewOutput:
        return cls(
            results=[
                ReviewOut(item_id=v.item_id, next_due=v.next_due, outcome=v.outcome) for v in views
            ],
            response_rules=rules,
        )


class RejectedOut(BaseModel):
    index: int = out("Index", "Position of the rejected item in the call, from 0.")
    reason: RejectReason = out("Reason", "Why the item was not saved.")


class SaveGlossaryOutput(BaseModel):
    new: int = out("New", "Items saved for the first time.")
    reinforced: int = out("Reinforced", "Items that already existed and were reinforced.")
    promoted: int = out("Promoted", "Provisional or declined items now confirmed.")
    rejected: list[RejectedOut] = out("Rejected", "Items that were not saved, with the reason.")
    response_rules: str = out("Response rules", "What to do next. Follow these.")

    @classmethod
    def of(cls, r: GlossarySaveResult, rules: str) -> SaveGlossaryOutput:
        return cls(
            new=r.new,
            reinforced=r.reinforced,
            promoted=r.promoted,
            rejected=[RejectedOut(index=x.index, reason=x.reason) for x in r.rejected],
            response_rules=rules,
        )


class EndSessionOutput(BaseModel):
    status: SessionOutcome = out("Status", "'closed' for a full lesson, 'incomplete' if too short.")
    low_trust: bool = out("Low trust", "True when the evidence did not match the conversation.")
    already_closed: bool = out("Already closed", "True when this lesson had already ended.")
    summary_text: str = out("Summary", "The summary to read once when status is closed.")
    streak: int = out("Streak", "Consecutive practice days.")
    errors_rejected: int = out("Errors rejected", "Reported errors the server could not verify.")
    chunks_rejected: int = out("Chunks rejected", "Reported chunks the server could not verify.")
    metrics: dict[str, Any] = out("Metrics", "Server-computed lesson metrics. Never read aloud.")
    response_rules: str = out("Response rules", "What to do next. Follow these.")

    @classmethod
    def of(cls, r: EndSessionResult, rules: str) -> EndSessionOutput:
        return cls(
            status=r.status,
            low_trust=r.low_trust,
            already_closed=r.already_closed,
            summary_text=r.summary_text,
            streak=r.streak,
            errors_rejected=r.errors_rejected,
            chunks_rejected=r.chunks_rejected,
            metrics=metrics_to_json(r.metrics),
            response_rules=rules,
        )


INPUT_MODELS: dict[str, type[StrictInput]] = {
    "get_profile": GetProfileInput,
    "save_profile": SaveProfileInput,
    "start_lesson": StartLessonInput,
    "record_review": RecordReviewInput,
    "save_glossary": SaveGlossaryInput,
    "end_session": EndSessionInput,
}
OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "get_profile": GetProfileOutput,
    "save_profile": SaveProfileOutput,
    "start_lesson": StartLessonOutput,
    "record_review": RecordReviewOutput,
    "save_glossary": SaveGlossaryOutput,
    "end_session": EndSessionOutput,
}
