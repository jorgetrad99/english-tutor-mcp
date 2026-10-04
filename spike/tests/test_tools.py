import uuid
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXED_NOW, make_server, read_log_lines
from fastmcp import Client

from tutor_spike.contract import INSTRUCTIONS
from tutor_spike.eventlog import JsonlLog
from tutor_spike.sessions import SessionRegistry
from tutor_spike.testers import parse_testers
from tutor_spike.tools import build_mcp


def valid_args(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "user_turns": [
            "Yesterday I go to the office and the deploy was broken.",
            "We need more time for the release.",
        ],
        "errors": [
            {
                "said": "Yesterday I go to the office",
                "correct": "Yesterday I went to the office",
                "category": "grammar",
            },
            {"said": "I have 30 years", "correct": "I am 30 years old", "category": "lexis"},
        ],
        "chunks_used": [],
        "task_result": "achieved",
        "hints_given": 1,
        "cefr_estimate": {
            "speaking": "B1+",
            "confidence": "medium",
            "evidence": ["Past tense errors under pressure."],
        },
        "confidence_1_5": 3,
    }


def text_of(result: Any) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


def assert_unknown_session_logged(directory: Path) -> None:
    [line] = [x for x in read_log_lines(directory) if x.get("tool") == "end_session"]
    assert line["accepted"] is False
    assert line["reason"] == "unknown_session"


async def start(client: Client) -> str:
    result = await client.call_tool("get_profile", {}, raise_on_error=False)
    return str(result.structured_content["session_id"])


async def test_get_profile_issues_a_session_id_and_logs_it(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool("get_profile", {}, raise_on_error=False)
    content = result.structured_content
    uuid.UUID(content["session_id"])
    assert content["display_name"] == "Learner"
    assert content["level"] == "B1+"
    assert content["response_rules"]
    [line] = [x for x in read_log_lines(tmp_path) if x["kind"] == "tool"]
    assert line["tool"] == "get_profile"
    assert line["tester"] == "author-free"
    assert line["session_id"] == content["session_id"]


async def test_valid_end_session_is_accepted_and_said_rule_applied(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        session_id = await start(client)
        result = await client.call_tool("end_session", valid_args(session_id), raise_on_error=False)
    assert not result.is_error
    assert result.structured_content["accepted"] is True
    assert result.structured_content["errors_kept"] == 1
    assert result.structured_content["errors_rejected"] == 1
    assert result.structured_content["response_rules"]


async def test_unknown_session_id_is_rejected(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        result = await client.call_tool("end_session", valid_args("nope"), raise_on_error=False)
    assert result.is_error
    assert "Call get_profile" in text_of(result)
    assert_unknown_session_logged(tmp_path)


async def test_session_id_of_another_tester_is_rejected(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path, tester="author-pro")) as client:
        session_id = await start(client)
    async with Client(make_server(tmp_path, tester="author-free")) as client:
        result = await client.call_tool("end_session", valid_args(session_id), raise_on_error=False)
    assert result.is_error
    assert "Call get_profile" in text_of(result)
    assert_unknown_session_logged(tmp_path)


async def test_unknown_field_is_rejected_with_retry_rules(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        session_id = await start(client)
        args = {**valid_args(session_id), "glossary": ["deploy"]}
        result = await client.call_tool("end_session", args, raise_on_error=False)
    assert result.is_error
    assert "same session_id" in text_of(result)


async def test_unknown_field_inside_nested_object_is_rejected(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        session_id = await start(client)
        args = valid_args(session_id)
        args["errors"][0]["foo"] = 1
        result = await client.call_tool("end_session", args, raise_on_error=False)
    assert result.is_error


async def test_server_fault_is_not_reported_as_a_schema_error(tmp_path: Path) -> None:
    class FailingRegistry(SessionRegistry):
        def record_end(self, session_id: str) -> int:
            raise OSError("disk full")

    server = build_mcp(
        registry=FailingRegistry(tmp_path / "sessions.jsonl", FIXED_NOW),
        log=JsonlLog(tmp_path, FIXED_NOW),
        resolve_tester=lambda: "author-free",
        now=FIXED_NOW,
        display_name="Learner",
    )
    async with Client(server) as client:
        session_id = await start(client)
        result = await client.call_tool("end_session", valid_args(session_id), raise_on_error=False)
    assert result.is_error
    assert "same session_id" not in text_of(result)


async def test_out_of_enum_category_is_rejected(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        session_id = await start(client)
        args = valid_args(session_id)
        args["errors"][0]["category"] = "spelling"
        result = await client.call_tool("end_session", args, raise_on_error=False)
    assert result.is_error


async def test_empty_user_turns_is_rejected(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        session_id = await start(client)
        args = {**valid_args(session_id), "user_turns": []}
        result = await client.call_tool("end_session", args, raise_on_error=False)
    assert result.is_error


async def test_repeat_end_session_is_accepted_and_flagged(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        session_id = await start(client)
        await client.call_tool("end_session", valid_args(session_id), raise_on_error=False)
        await client.call_tool("end_session", valid_args(session_id), raise_on_error=False)
    ends = [x for x in read_log_lines(tmp_path) if x.get("tool") == "end_session"]
    assert [x["repeat"] for x in ends] == [0, 1]


async def test_caller_not_on_allowlist_is_refused(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path, tester=None)) as client:
        result = await client.call_tool("get_profile", {}, raise_on_error=False)
    assert result.is_error
    assert "not enabled" in text_of(result)


def _object_schemas(node: Any) -> list[dict[str, Any]]:
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


async def test_tool_schemas_follow_the_mcp_contract(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        tools = await client.list_tools()
    assert {tool.name for tool in tools} == {"get_profile", "end_session"}
    for tool in tools:
        assert tool.description
        assert len(tool.description.split()) <= 120
        for obj in _object_schemas(tool.input_schema):
            assert obj.get("additionalProperties") is False, (tool.name, obj)
            for name, prop in obj["properties"].items():
                assert prop.get("title") and prop.get("description"), (tool.name, name)


def test_enums_are_closed(end_session_schema: dict[str, Any]) -> None:
    text = str(end_session_schema)
    for value in ("word_order", "not_achieved", "B2+", "medium"):
        assert value in text


def test_instructions_are_frozen_v1() -> None:
    assert len(INSTRUCTIONS.split()) <= 400
    assert "call `get_profile` and keep its `session_id`" in INSTRUCTIONS
    assert "`end_session` with the `session_id` from `get_profile`" in INSTRUCTIONS


def test_server_sends_the_instructions(tmp_path: Path) -> None:
    assert make_server(tmp_path).instructions == INSTRUCTIONS


def test_parse_testers_maps_lowercased_emails_to_labels() -> None:
    raw = "Me@Gmail.com=author-free, other@gmail.com=author-pro,"
    assert parse_testers(raw) == {"me@gmail.com": "author-free", "other@gmail.com": "author-pro"}


def test_parse_testers_rejects_malformed_entries() -> None:
    with pytest.raises(ValueError, match="SPIKE_TESTERS"):
        parse_testers("me@gmail.com")
