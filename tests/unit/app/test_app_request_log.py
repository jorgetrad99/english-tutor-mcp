"""One JSON line per HTTP request on `tutor.http`, web and OAuth alike (spec 13; core Task 23).

The line names the matched route template (or a fixed label), never the raw path, query
string, headers or body; /mcp is left to the MCP call log.
"""

import json
import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tutor.app import Message, RequestLog, Scope, route_label
from tutor.mcp.observe import user_hash

pytestmark = pytest.mark.unit

FIELDS = {"event", "route", "method", "status", "latency_ms", "user_hash"}


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _drop(message: Message) -> None:
    return None


def _ticks(*values: float) -> Any:
    it = iter(values)
    return lambda: next(it)


def _records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "tutor.http"]


def test_route_label_is_the_template_or_a_fixed_label() -> None:
    sid = uuid4()
    routed: Scope = {
        "path": f"/app/sessions/{sid}",
        "route": SimpleNamespace(path="/app/sessions/{id}"),
    }
    assert route_label(routed) == "/app/sessions/{id}"
    assert route_label({"path": "/oauth/callback"}) == "/oauth/callback"  # a fixed MCP path
    assert route_label({"path": f"/.well-known/{'x' * 300}"}) == "unmatched"
    assert route_label({"path": "/app/<script>"}) == "unmatched"


@pytest.mark.asyncio
async def test_one_line_with_status_latency_and_user_hash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    uid = uuid4()

    async def inner(scope: Scope, receive: Any, send: Any) -> None:
        scope["route"] = SimpleNamespace(path="/app/sessions/{session_id}")
        scope.setdefault("state", {})["user"] = SimpleNamespace(id=uid)
        await send({"type": "http.response.start", "status": 303, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    caplog.set_level(logging.INFO, logger="tutor.http")
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": f"/app/sessions/{uuid4()}",
        "query_string": b"code=secret-code&state=s",
        "headers": [],
    }
    await RequestLog(inner, clock=_ticks(1.0, 1.25))(scope, _receive, _drop)
    assert _records(caplog) == [
        {
            "event": "http_request",
            "route": "/app/sessions/{session_id}",
            "method": "GET",
            "status": 303,
            "latency_ms": 250.0,
            "user_hash": user_hash(uid),
        }
    ]
    assert "secret-code" not in caplog.text


@pytest.mark.asyncio
async def test_a_crash_is_logged_as_500_and_still_raised(caplog: pytest.LogCaptureFixture) -> None:
    async def inner(scope: Scope, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    caplog.set_level(logging.INFO, logger="tutor.http")
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/token",
        "query_string": b"code=SECRET",
        "headers": [],
    }
    with pytest.raises(RuntimeError):
        await RequestLog(inner, clock=_ticks(0.0, 0.002))(scope, _receive, _drop)
    [line] = _records(caplog)
    assert set(line) == FIELDS
    assert (line["status"], line["user_hash"], line["route"]) == (500, None, "/token")
    assert "SECRET" not in caplog.text
    assert "boom" not in caplog.text


@pytest.mark.asyncio
async def test_mcp_and_lifespan_pass_through_unlogged(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    caplog.set_level(logging.INFO, logger="tutor.http")
    await RequestLog(inner)({"type": "lifespan"}, _receive, _drop)
    await RequestLog(inner)({"type": "http", "method": "POST", "path": "/mcp"}, _receive, _drop)
    assert (seen, _records(caplog)) == (["lifespan", "http"], [])
