import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from tutor_spike.eventlog import JsonlLog
from tutor_spike.sessions import SessionRegistry
from tutor_spike.tools import build_mcp


def FIXED_NOW() -> datetime:
    return datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


def read_log_lines(directory: Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for path in sorted(directory.glob("calls-*.jsonl")):
        lines += [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    return lines


def make_server(tmp_path: Path, tester: str | None = "author-free") -> FastMCP:
    return build_mcp(
        registry=SessionRegistry(tmp_path / "sessions.jsonl", FIXED_NOW),
        log=JsonlLog(tmp_path, FIXED_NOW),
        resolve_tester=lambda: tester,
        now=FIXED_NOW,
        display_name="Learner",
    )


@pytest.fixture
def end_session_schema(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The end_session inputSchema exactly as the server advertises it."""
    server = make_server(tmp_path_factory.mktemp("schema"))

    async def fetch() -> dict[str, Any]:
        async with Client(server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        return dict(tools["end_session"].input_schema)

    return asyncio.run(fetch())
