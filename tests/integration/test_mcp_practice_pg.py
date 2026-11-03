"""The practice tools and get_subject_progress through the real server on Postgres, as the login
role (only tutor_app's grants), with the caller identified by an access token in context
(subjects spec 6.2, 15.9, 16.8; slice B Tasks 3 and 4; Review Focus 5, 6 and 8)."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastmcp import Client
from mcp_lesson import Clock, call, random_sub, text_of
from sqlalchemy import Engine, text

from tutor.domain.templates import TEMPLATE_GUIDE_VERSION
from tutor.mcp import rules
from tutor.services.template_files import guide_examples

from .test_mcp_lesson_pg import server, signed_in

pytestmark = pytest.mark.integration

SUBJECT_ARGS = {
    "name": "Matemáticas",
    "preference": "balanced",
    "exercise_kinds": ["quiz", "drill", "exercise"],
    "minutes_per_session": 12,
    "sessions_per_week": 3,
}


def example(name: str) -> dict[str, Any]:
    found = next(e for e in guide_examples() if e.name == name)
    return {"html": found.html, **json.loads(json.dumps(dict(found.wire)))}


async def add_example(client: Client, subject_id: str, name: str) -> str:
    data = example(name)
    template = await call(
        client,
        "save_template",
        {
            "subject_id": subject_id,
            **data["spec"],
            "html": data["html"],
            "guide_version": TEMPLATE_GUIDE_VERSION,
            "learner_confirmed": True,
        },
    )
    made = await call(
        client,
        "create_instance",
        {
            "subject_id": subject_id,
            "template_id": template["template_id"],
            "params": data["instance"]["params"],
            "prompt_text": data["instance"]["prompt_text"],
        },
    )
    return str(made["instance_id"])


@pytest.mark.asyncio
async def test_a_practice_session_on_postgres_with_the_login_role(
    login_engine: Engine, engine: Engine
) -> None:
    # 23:50 in Mexico City (UTC-6) on 10-13, then 00:10 on 10-14: two local days, one UTC date.
    clock = Clock()
    clock.now = datetime(2026, 10, 14, 5, 49, tzinfo=UTC)
    mcp = server(login_engine, clock)
    with signed_in(random_sub()):
        async with Client(mcp) as client:
            subject = (await call(client, "save_subject", SUBJECT_ARGS))["subject"]
            subject_id = subject["subject_id"]
            assert subject["plan_status"]["version"] == 1
            await add_example(client, subject_id, "numeric")
            await add_example(client, subject_id, "pool_recall")
            started = await call(
                client, "start_practice", {"subject_id": subject_id, "practice_mode": "text"}
            )
            assert started["item_count"] == 4 and started["short"] is False
            session_id = started["session_id"]
            item = started["item"]
            answered = 0
            while item is not None:
                clock.now = (
                    datetime(2026, 10, 14, 6, 10, tzinfo=UTC)
                    if answered == 1
                    else clock.now + timedelta(minutes=1)
                )
                answer: dict[str, Any] = (
                    {"answer_kind": "number", "value": "28,27"}
                    if item["grading_kind"] == "numeric"
                    else {"answer_kind": "value", "value": "no lo sé"}
                )
                graded = await call(
                    client,
                    "record_result",
                    {"session_id": session_id, "item_id": item["item_id"], "answer": answer},
                )
                assert graded["result_outcome"] == "new"
                repeat = await call(
                    client,
                    "record_result",
                    {"session_id": session_id, "item_id": item["item_id"], "answer": answer},
                )
                assert repeat["result_outcome"] == "existing"
                answered += 1
                step = await call(client, "next_exercise", {"session_id": session_id})
                item = step["item"]
            assert step["done"] is True and step["response_rules"] == rules.NEXT_EXERCISE_DONE
            ended = await call(client, "end_practice", {"session_id": session_id})
            progress = await call(client, "get_subject_progress", {"subject_id": subject_id})
            listed = await call(client, "get_subjects", {})
    assert (ended["answered"], ended["unanswered"]) == (4, 0)
    assert ended["plan_status"]["completed_sessions"] == 1
    assert listed["subjects"][0]["plan_status"]["completed_sessions"] == 1
    assert progress["streak"]["current_days"] == 2  # Review Focus 8, on Postgres
    assert progress["overall"]["all_time"]["attempts"] == 4
    assert progress["totals"]["practice_completed"] == 1
    with engine.connect() as conn:
        shown_first = conn.execute(
            text(
                "SELECT count(*) FROM practice_items"
                " WHERE answered_at IS NOT NULL AND shown_at <= answered_at"
            )
        ).scalar_one()
    assert shown_first == 4


@pytest.mark.asyncio
async def test_another_learner_never_reaches_the_session(login_engine: Engine) -> None:
    mcp = server(login_engine, Clock())
    with signed_in(random_sub()):
        async with Client(mcp) as client:
            subject_id = (await call(client, "save_subject", SUBJECT_ARGS))["subject"]["subject_id"]
            await add_example(client, subject_id, "numeric")
            started = await call(client, "start_practice", {"subject_id": subject_id})
    with signed_in(random_sub()):
        async with Client(mcp) as client:
            payload = {
                "session_id": started["session_id"],
                "item_id": started["item"]["item_id"],
                "answer": {"answer_kind": "number", "value": "1"},
            }
            result = await client.call_tool("record_result", payload, raise_on_error=False)
            body = json.loads(text_of(result))
            progress = await client.call_tool(
                "get_subject_progress", {"subject_id": subject_id}, raise_on_error=False
            )
    assert result.is_error and (body["code"], body["fields"]) == ("not_found", ["session_id"])
    assert progress.is_error and json.loads(text_of(progress))["code"] == "not_found"
