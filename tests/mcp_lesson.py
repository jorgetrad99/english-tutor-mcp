"""Scripted MCP lesson shared by the unit (memory) and integration (Postgres) tests."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastmcp import Client, FastMCP

from tutor.mcp import rules

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # Wednesday, 09:00 in America/Mexico_City

PROFILE_ARGS: dict[str, Any] = {
    "self_level": "B1+",
    "domains": ["it"],
    "use_cases": ["standup", "incident"],
    "minutes_per_day": 20,
    "days_per_week": 3,
    "target_level": "B2",
}
GLOSSARY_ITEM: dict[str, Any] = {
    "kind": "chunk",
    "text": "push back on the date",
    "meaning": "pedir mover la fecha",
    "context_sentence": "Can we push back on the date of the release?",
    "domain": "it",
}
TURNS = [
    "Hi Ana, I want to talk about the release date for the payments service.",
    "Yesterday we find a bug in the deploy pipeline and the tests are failing.",
    "Can we push back on the date until Friday so the team can fix it safely?",
    "I can send you a short update every morning with the progress.",
]


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def end_args(session_id: str, chunk_ids: list[str], **overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "session_id": session_id,
        "user_turns": TURNS,
        "errors": [
            {
                "said": "Yesterday we find a bug",
                "correct": "Yesterday we found a bug",
                "category": "grammar",
            }
        ],
        "chunks_used": chunk_ids,
        "task_result": "achieved",
        "hints_given": 1,
        "cefr_estimate": {
            "speaking": "B1+",
            "confidence": "medium",
            "evidence": ["Past tense errors under pressure"],
        },
        "confidence_1_5": 3,
        "assistant_words_estimate": 180,
    }
    return {**args, **overrides}


def text_of(result: Any) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


async def call(client: Client, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call through the real client, which also checks the result against the published
    output schema."""
    result = await client.call_tool(tool, args, raise_on_error=False)
    assert not result.is_error, text_of(result)
    content = result.structured_content
    assert content is not None
    assert content["response_rules"]
    return dict(content)


def random_sub() -> str:
    return f"google-sub-{uuid4()}"


async def run_scripted_lesson(mcp: FastMCP, clock: Clock) -> None:
    """get_profile -> save_profile -> start_lesson -> save_glossary -> end_session, then
    the next day the confirmed item is due and record_review schedules it."""
    async with Client(mcp) as c:
        first = await call(c, "get_profile", {})
        assert first["onboarding_needed"] is True
        assert first["onboarding_questions"]
        assert first["profile"] is None

        saved = await call(c, "save_profile", PROFILE_ARGS)
        assert saved["profile"]["use_cases"] == ["standup", "incident"]
        assert saved["plan"]["sessions_planned"] > 0
        assert saved["feasibility"]["message"]

        lesson = await call(c, "start_lesson", {"mode": "text"})
        session_id = lesson["session_id"]
        chunk_ids = [chunk["id"] for chunk in lesson["chunks"]]
        assert len(chunk_ids) == 5
        assert lesson["due_reviews"] == []
        assert lesson["scenario_brief"]["objective"]

        glossary = await call(
            c,
            "save_glossary",
            {"session_id": session_id, "status": "confirmed", "items": [GLOSSARY_ITEM]},
        )
        assert (glossary["new"], glossary["rejected"]) == (1, [])

        clock.advance(minutes=15)
        end = await call(c, "end_session", end_args(session_id, chunk_ids[:2]))
        assert (end["status"], end["streak"], end["already_closed"]) == ("closed", 1, False)
        assert (end["errors_rejected"], end["chunks_rejected"]) == (0, 0)
        assert len(end["summary_text"].splitlines()) == 4
        assert end["response_rules"] == rules.end_session_rules("closed", already_closed=False)
        assert end["metrics"]["errors_by_category"]["grammar"] == 1
        assert (end["metrics"]["errors_total"], end["metrics"]["chunks_used"]) == (1, 2)
        again = await call(c, "end_session", end_args(session_id, chunk_ids[:2]))
        assert (again["already_closed"], again["streak"]) == (True, 1)
        assert again["summary_text"] == end["summary_text"]
        assert again["response_rules"] == rules.END_SESSION_ALREADY_CLOSED

        same_day = await call(c, "get_profile", {})
        assert (same_day["streak"], same_day["due_reviews_count"]) == (1, 0)
        assert same_day["open_session_id"] is None
        assert same_day["onboarding_questions"] == []

        clock.advance(days=1)
        next_day = await call(c, "get_profile", {})
        assert (next_day["streak"], next_day["due_reviews_count"]) == (1, 1)

        lesson2 = await call(c, "start_lesson", {"mode": "voice"})
        [due] = lesson2["due_reviews"]
        assert due["text"] == GLOSSARY_ITEM["text"]
        review = {
            "session_id": lesson2["session_id"],
            "results": [{"item_id": due["item_id"], "rating": 3}],
        }
        [recorded] = (await call(c, "record_review", review))["results"]
        assert recorded["outcome"] == "recorded"
        assert date.fromisoformat(recorded["next_due"]) > date(2026, 10, 15)
        [repeat] = (await call(c, "record_review", review))["results"]
        assert repeat["outcome"] == "already_recorded"
