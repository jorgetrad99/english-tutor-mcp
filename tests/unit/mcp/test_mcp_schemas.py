from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from tutor.domain.glossary import CONTEXT_MAX, MEANING_MAX, TEXT_MAX, IncomingItem
from tutor.domain.profile import GOAL_TEXT_MAX, MINUTES_CHOICES
from tutor.domain.validation import ReportedError
from tutor.mcp import schemas
from tutor.mcp.schemas import (
    INPUT_MODELS,
    OUTPUT_MODELS,
    RAW_EVIDENCE_MAX_BYTES,
    EndSessionInput,
    GlossaryItemInput,
    SaveProfileInput,
    StartLessonInput,
)
from tutor.services.errors import ServiceError
from tutor.services.lesson import MAX_PREP_CHARS, MAX_REVIEW_RESULTS
from tutor.services.session_end import MAX_RAW_EVIDENCE_BYTES

pytestmark = pytest.mark.unit

CURLY_TURN = "I" + chr(0x2019) + "m fixing the deploy"  # typographic apostrophe


def object_schemas(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if "properties" in current:
                found.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def enum_values(schema: dict[str, Any]) -> dict[str, set[Any]]:
    """Every enum or const keyed by property name (anyOf branches and list items included)."""
    found: dict[str, set[Any]] = {}
    defs = schema.get("$defs", {})
    for obj in [*object_schemas(schema), *defs.values()]:
        for name, prop in obj.get("properties", {}).items():
            branches = [prop, *prop.get("anyOf", []), prop.get("items", {})]
            for branch in branches:
                if "$ref" in branch:
                    branch = defs[branch["$ref"].rsplit("/", 1)[-1]]
                if "enum" in branch:
                    found.setdefault(name, set()).update(branch["enum"])
                if "const" in branch:
                    found.setdefault(name, set()).add(branch["const"])
    return found


def schema_models() -> list[type[BaseModel]]:
    return [
        v
        for v in vars(schemas).values()
        if isinstance(v, type) and issubclass(v, BaseModel) and v.__module__ == schemas.__name__
    ]


@pytest.mark.parametrize("tool", sorted(INPUT_MODELS))
def test_every_field_has_title_and_description(tool: str) -> None:
    schema = INPUT_MODELS[tool].model_json_schema()
    for obj in object_schemas(schema):
        assert obj.get("additionalProperties") is False, tool
        for name, prop in obj["properties"].items():
            assert prop.get("title"), (tool, name)
            assert prop.get("description"), (tool, name)


@pytest.mark.parametrize("model", schema_models(), ids=lambda m: m.__name__)
def test_every_model_field_declares_title_and_description(model: type[BaseModel]) -> None:
    """Pydantic derives a title from the name, so check what the author declared."""
    for name, field in model.model_fields.items():
        assert field.title, (model.__name__, name)
        assert field.description, (model.__name__, name)


@pytest.mark.parametrize("tool", sorted(OUTPUT_MODELS))
def test_every_output_property_has_title_and_description(tool: str) -> None:
    schema = OUTPUT_MODELS[tool].model_json_schema(mode="serialization")
    assert "response_rules" in schema["properties"]
    objects = object_schemas(schema)
    assert objects
    for obj in objects:
        for name, prop in obj["properties"].items():
            assert prop.get("title"), (tool, name)
            assert prop.get("description"), (tool, name)


def test_output_closed_sets_are_enums_not_open_strings() -> None:
    enums: dict[str, set[Any]] = {}
    for model in OUTPUT_MODELS.values():
        for name, values in enum_values(model.model_json_schema(mode="serialization")).items():
            enums.setdefault(name, set()).update(values)
    assert enums["self_level"] == {"B1", "B1+", "B2", "B2+", "C1"}
    assert enums["target_level"] == enums["self_level"]
    assert enums["milestone_level"] == enums["self_level"]
    assert enums["skill"] == {"speaking", "writing"}
    assert enums["cefr"] == {"B1", "B2"}
    assert enums["status"] == {"pending", "done", "skipped", "closed", "incomplete"}
    assert enums["outcome"] == {"recorded", "already_recorded"}
    assert enums["mode"] == {"voice", "text"}
    assert enums["variant"] == {"base", "complication", "simpler"}
    assert enums["kind"] == {"correction", "chunk", "term"}
    assert enums["format"] == {"produce", "recall", "correct", "use"}
    assert enums["reason"] == {
        "empty",
        "too_long",
        "missing_context",
        "duplicate_in_call",
        "already_confirmed",
    }
    assert enums["domains"] == {"it"}


def test_enums_are_closed_and_match_the_contract() -> None:
    enums: dict[str, set[Any]] = {}
    for model in INPUT_MODELS.values():
        for name, values in enum_values(model.model_json_schema()).items():
            enums.setdefault(name, set()).update(values)
    use_cases = {
        "standup",
        "code_review",
        "interview",
        "client_call",
        "demo",
        "incident",
        "one_on_one",
        "async_writing",
    }
    levels = {"B1", "B1+", "B2", "B2+", "C1"}
    assert enums == {
        "self_level": levels,
        "target_level": levels,
        "speaking": levels,
        "domains": {"it"},
        "domain": {"it"},
        "use_cases": use_cases,
        "prep_use_case": use_cases,
        "minutes_per_day": {15, 20, 30},
        "mode": {"voice", "text"},
        "rating": {1, 2, 3, 4},
        "status": {"confirmed", "provisional", "declined"},
        "kind": {"correction", "chunk", "term"},
        "category": {"grammar", "lexis", "word_order", "register", "other"},
        "task_result": {"achieved", "partial", "not_achieved"},
        "confidence": {"low", "medium", "high"},
    }
    assert enums["minutes_per_day"] == set(MINUTES_CHOICES)


def test_end_session_keeps_the_section_7_shape() -> None:
    schema = EndSessionInput.model_json_schema()
    assert set(schema["properties"]) == {
        "session_id",
        "user_turns",
        "errors",
        "chunks_used",
        "task_result",
        "hints_given",
        "cefr_estimate",
        "confidence_1_5",
        "assistant_words_estimate",
    }
    assert set(schema["required"]) == set(schema["properties"]) - {"assistant_words_estimate"}
    cefr = schema["$defs"]["CefrEstimateInput"]
    assert set(cefr["properties"]) == {"speaking", "confidence", "evidence"}
    assert set(schema["$defs"]["ErrorInput"]["properties"]) == {"said", "correct", "category"}
    props = schema["properties"]
    assert (props["hints_given"]["minimum"], props["hints_given"]["maximum"]) == (0, 3)
    assert (props["confidence_1_5"]["minimum"], props["confidence_1_5"]["maximum"]) == (1, 5)
    assert props["user_turns"]["minItems"] == 1
    assert props["user_turns"]["items"]["maxLength"] == 2000


@pytest.mark.parametrize(
    ("model", "path", "cap"),
    [
        (SaveProfileInput, ("goal_text",), 300),
        (StartLessonInput, ("prep",), 300),
        (GlossaryItemInput, ("text",), 120),
        (GlossaryItemInput, ("meaning",), 200),
        (GlossaryItemInput, ("context_sentence",), 300),
    ],
)
def test_length_caps_follow_the_global_constraints(
    model: type[Any], path: tuple[str, ...], cap: int
) -> None:
    prop = model.model_json_schema()["properties"][path[0]]
    branches = [prop, *prop.get("anyOf", [])]
    assert cap in {b.get("maxLength") for b in branches}


def test_caps_come_from_the_domain_and_services_constants() -> None:
    assert (GOAL_TEXT_MAX, MAX_PREP_CHARS) == (300, 300)
    assert (TEXT_MAX, MEANING_MAX, CONTEXT_MAX) == (120, 200, 300)
    assert RAW_EVIDENCE_MAX_BYTES == MAX_RAW_EVIDENCE_BYTES == 20 * 1024
    review = schemas.RecordReviewInput.model_json_schema()["properties"]["results"]
    assert review["maxItems"] == MAX_REVIEW_RESULTS == 8
    lesson = StartLessonInput.model_json_schema()["properties"]["minutes"]
    assert {b.get("minimum") for b in lesson["anyOf"]} >= {10}
    assert {b.get("maximum") for b in lesson["anyOf"]} >= {30}


def end_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "session_id": str(uuid4()),
        "user_turns": [CURLY_TURN, "We need more time"],
        "errors": [{"said": "I fixing", "correct": "I'm fixing", "category": "grammar"}],
        "chunks_used": ["it-01-c1"],
        "task_result": "achieved",
        "hints_given": 1,
        "cefr_estimate": {"speaking": "B1+", "confidence": "medium", "evidence": ["past tense"]},
        "confidence_1_5": 3,
    }
    return {**args, **overrides}


def test_end_session_input_converts_to_evidence() -> None:
    ev = EndSessionInput.model_validate(end_args()).to_evidence()
    assert ev.user_turns == (CURLY_TURN, "We need more time")
    assert ev.errors == (ReportedError(said="I fixing", correct="I'm fixing", category="grammar"),)
    assert ev.chunks_used == ("it-01-c1",)
    assert (ev.cefr_level, ev.cefr_confidence, ev.cefr_evidence) == (
        "B1+",
        "medium",
        ("past tense",),
    )
    assert (ev.task_result, ev.hints_given, ev.confidence_1_5) == ("achieved", 1, 3)
    assert ev.assistant_words_estimate is None


def test_raw_evidence_excludes_the_session_id_and_is_capped() -> None:
    raw = EndSessionInput.model_validate(end_args()).raw_evidence()
    assert "session_id" not in raw
    assert raw["user_turns"][0] == CURLY_TURN
    big = EndSessionInput.model_validate(end_args(user_turns=["word " * 399] * 11))
    assert len(big.model_dump_json().encode()) > RAW_EVIDENCE_MAX_BYTES
    with pytest.raises(ServiceError) as info:
        big.raw_evidence()
    assert info.value.code == "payload_too_large"


@pytest.mark.parametrize(
    "overrides",
    [
        {"user_turns": []},
        {"hints_given": 4},
        {"confidence_1_5": 0},
        {"task_result": "done"},
        {"notes": "extra"},
        {"user_turns": ["x" * 2001]},
        {"cefr_estimate": {"speaking": "A2", "confidence": "low", "evidence": []}},
    ],
)
def test_end_session_input_rejects_out_of_contract_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EndSessionInput.model_validate(end_args(**overrides))


def profile_args() -> dict[str, Any]:
    return {
        "self_level": "B1+",
        "domains": ["it"],
        "use_cases": ["standup"],
        "minutes_per_day": 20,
        "days_per_week": 3,
        "target_level": "B2",
    }


def glossary_item() -> dict[str, Any]:
    return {
        "kind": "chunk",
        "text": "push back on the date",
        "meaning": "negociar la fecha",
        "context_sentence": "Can we push back on the date?",
        "domain": "it",
    }


def review_args() -> dict[str, Any]:
    return {"session_id": str(uuid4()), "results": [{"item_id": str(uuid4()), "rating": 3}]}


def glossary_args() -> dict[str, Any]:
    return {"session_id": str(uuid4()), "status": "confirmed", "items": [glossary_item()]}


# (tool, valid minimal payload, where the unknown key goes; "" means the top level)
UNKNOWN_KEY_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("get_profile", {}, ""),
    ("save_profile", profile_args(), ""),
    ("start_lesson", {"mode": "text"}, ""),
    ("record_review", review_args(), ""),
    ("record_review", review_args(), "results.0"),
    ("save_glossary", glossary_args(), ""),
    ("save_glossary", glossary_args(), "items.0"),
    ("end_session", end_args(), ""),
    ("end_session", end_args(), "errors.0"),
    ("end_session", end_args(), "cefr_estimate"),
]


@pytest.mark.parametrize(
    ("tool", "payload", "where"),
    UNKNOWN_KEY_CASES,
    ids=[f"{t}:{w or 'top'}" for t, _, w in UNKNOWN_KEY_CASES],
)
def test_unknown_keys_are_rejected_at_every_level(
    tool: str, payload: dict[str, Any], where: str
) -> None:
    model = INPUT_MODELS[tool]
    model.model_validate(payload)  # the payload itself is valid
    broken: dict[str, Any] = {**payload}
    node: Any = broken
    for step in filter(None, where.split(".")):
        node = node[int(step)] if step.isdigit() else node[step]
    node["surprise"] = 1
    with pytest.raises(ValidationError) as info:
        model.model_validate(broken)
    assert {e["type"] for e in info.value.errors()} == {"extra_forbidden"}


def test_save_profile_input_converts_without_timezone() -> None:
    raw = SaveProfileInput.model_validate(
        {
            "self_level": "B1+",
            "domains": ["it"],
            "use_cases": ["standup", "incident"],
            "minutes_per_day": 20,
            "days_per_week": 3,
            "target_level": "B2",
            "target_date": "2027-03-01",
        }
    ).to_profile_input()
    assert (raw.self_level, raw.target_level) == ("B1+", "B2")
    assert tuple(raw.use_cases) == ("standup", "incident")
    assert raw.target_date == date(2027, 3, 1)
    assert raw.goal_text is None
    assert raw.timezone is None


def test_start_lesson_prep_needs_its_use_case() -> None:
    with pytest.raises(ServiceError) as info:
        StartLessonInput(mode="voice", prep="standup tomorrow").to_request()
    assert (info.value.code, info.value.fields) == ("validation_failed", ("prep", "prep_use_case"))
    with pytest.raises(ServiceError):
        StartLessonInput(mode="voice", prep_use_case="standup").to_request()
    req = StartLessonInput(mode="text", prep="  standup tomorrow ", prep_use_case="standup")
    assert req.to_request().prep == "standup tomorrow"
    assert StartLessonInput(mode="text", prep="   ").to_request().prep is None


def test_glossary_item_converts_to_incoming() -> None:
    item = GlossaryItemInput(
        kind="chunk",
        text="push back on the date",
        meaning="negociar la fecha",
        context_sentence="Can we push back on the date?",
        domain="it",
    )
    assert item.to_incoming() == IncomingItem(
        kind="chunk",
        text="push back on the date",
        meaning="negociar la fecha",
        context_sentence="Can we push back on the date?",
        domain="it",
    )
