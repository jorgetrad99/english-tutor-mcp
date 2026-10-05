"""The schema aliases and models through a real FastMCP server (what Task 20 will register)."""

import json
from typing import Any
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP

from tutor.mcp.errors import UNKNOWN_FIELD, ValidationErrorMiddleware
from tutor.mcp.schemas import (
    INPUT_MODELS,
    OUTPUT_MODELS,
    CefrField,
    ChunksUsed,
    Confidence15,
    EndSessionOutput,
    GlossaryItems,
    GlossaryStatusField,
    HintsGiven,
    RecordReviewOutput,
    ReportedErrors,
    ReviewResults,
    SaveGlossaryOutput,
    SessionIdField,
    TaskResultField,
    UserTurns,
)
from tutor.mcp.titles import TitleRestoreMiddleware

pytestmark = pytest.mark.unit


def wiring_server() -> FastMCP:
    mcp = FastMCP("wiring")
    mcp.add_middleware(ValidationErrorMiddleware())
    mcp.add_middleware(TitleRestoreMiddleware(INPUT_MODELS, OUTPUT_MODELS))

    def record_review(session_id: SessionIdField, results: ReviewResults) -> RecordReviewOutput:
        raise NotImplementedError

    def save_glossary(
        session_id: SessionIdField, status: GlossaryStatusField, items: GlossaryItems
    ) -> SaveGlossaryOutput:
        raise NotImplementedError

    def end_session(
        session_id: SessionIdField,
        user_turns: UserTurns,
        errors: ReportedErrors,
        chunks_used: ChunksUsed,
        task_result: TaskResultField,
        hints_given: HintsGiven,
        cefr_estimate: CefrField,
        confidence_1_5: Confidence15,
    ) -> EndSessionOutput:
        raise NotImplementedError

    for fn in (record_review, save_glossary, end_session):
        mcp.tool(fn)
    return mcp


def property_nodes(node: Any) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for name, prop in current.get("properties", {}).items():
                found.append((name, prop))
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


@pytest.mark.asyncio
async def test_published_schemas_have_titles_on_nested_properties_too() -> None:
    async with Client(wiring_server()) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {"record_review", "save_glossary", "end_session"}
    for name, tool in tools.items():
        for schema in (tool.input_schema, tool.output_schema):
            assert schema
            props = property_nodes(schema)
            assert props
            for prop_name, prop in props:
                assert prop.get("title"), (name, prop_name)
                assert prop.get("description"), (name, prop_name)
    # a nested property: the plain name-derived title would be "Said"; "Item ID" is not derivable
    nested = property_nodes(tools["end_session"].input_schema)
    assert ("said", "Said") in {(n, p["title"]) for n, p in nested if n == "said"}
    results = property_nodes(tools["record_review"].input_schema)
    assert {p["title"] for n, p in results if n == "item_id"} == {"Item ID"}
    assert {p["title"] for n, p in results if n == "rating"} == {"Rating"}


@pytest.mark.asyncio
async def test_published_input_schemas_forbid_unknown_keys_at_every_level() -> None:
    async with Client(wiring_server()) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for tool in tools.values():
        objects = [p for p in [tool.input_schema] if p]
        stack: list[Any] = list(objects)
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if "properties" in current:
                    assert current.get("additionalProperties") is False, tool.name
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)


def glossary_item() -> dict[str, Any]:
    return {
        "kind": "chunk",
        "text": "push back",
        "meaning": "negociar",
        "context_sentence": "Can we push back?",
        "domain": "it",
    }


NESTED_UNKNOWN_CALLS: list[tuple[str, dict[str, Any], str]] = [
    (
        "record_review",
        {"session_id": str(uuid4()), "results": [{"item_id": str(uuid4()), "rating": 3, "x": 1}]},
        f"results.0.{UNKNOWN_FIELD}",
    ),
    (
        "save_glossary",
        {
            "session_id": str(uuid4()),
            "status": "confirmed",
            "items": [{**glossary_item(), "x": 1}],
        },
        f"items.0.{UNKNOWN_FIELD}",
    ),
    (
        "end_session",
        {
            "session_id": str(uuid4()),
            "user_turns": ["hello"],
            "errors": [{"said": "a", "correct": "b", "category": "grammar", "x": 1}],
            "chunks_used": [],
            "task_result": "achieved",
            "hints_given": 0,
            "cefr_estimate": {"speaking": "B1", "confidence": "low", "evidence": []},
            "confidence_1_5": 3,
        },
        f"errors.0.{UNKNOWN_FIELD}",
    ),
    (
        "end_session",
        {
            "session_id": str(uuid4()),
            "user_turns": ["hello"],
            "errors": [],
            "chunks_used": [],
            "task_result": "achieved",
            "hints_given": 0,
            "cefr_estimate": {"speaking": "B1", "confidence": "low", "evidence": [], "x": 1},
            "confidence_1_5": 3,
        },
        f"cefr_estimate.{UNKNOWN_FIELD}",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "field"), NESTED_UNKNOWN_CALLS)
async def test_an_unknown_key_inside_a_nested_object_is_a_validation_error(
    tool: str, args: dict[str, Any], field: str
) -> None:
    async with Client(wiring_server()) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    assert result.is_error
    body = json.loads(" ".join(getattr(b, "text", "") for b in result.content))
    assert body["code"] == "validation_failed"
    assert body["fields"] == [field]
