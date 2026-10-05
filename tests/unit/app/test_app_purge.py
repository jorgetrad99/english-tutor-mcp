"""The web session purge runs in the server lifespan: at startup, then periodically (Task 23)."""

import asyncio
import json
import logging
import threading
from typing import Any

import pytest

from tutor.app import Message, PeriodicPurge, Scope

pytestmark = pytest.mark.unit


async def lifespan_app(scope: Scope, receive: Any, send: Any) -> None:
    if scope["type"] != "lifespan":
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        return
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def run_lifespan(app: Any, until: threading.Event) -> None:
    inbox: asyncio.Queue[Message] = asyncio.Queue()
    outbox: asyncio.Queue[Message] = asyncio.Queue()
    task = asyncio.create_task(app({"type": "lifespan"}, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    for _ in range(500):
        if until.is_set():
            break
        await asyncio.sleep(0.01)
    await inbox.put({"type": "lifespan.shutdown"})
    assert (await outbox.get())["type"] == "lifespan.shutdown.complete"
    await task


@pytest.mark.asyncio
async def test_purge_runs_at_startup_and_again_off_the_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runs: list[bool] = []
    twice = threading.Event()

    def job() -> int:
        try:
            asyncio.get_running_loop()
            runs.append(False)
        except RuntimeError:
            runs.append(True)  # a worker thread
        if len(runs) >= 2:
            twice.set()
        return 3

    caplog.set_level(logging.INFO, logger="tutor.web")
    await run_lifespan(PeriodicPurge(lifespan_app, job, every_s=0.01), twice)
    assert twice.is_set()
    assert all(runs)
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "tutor.web"]
    assert {"event": "web_sessions_purged", "deleted": 3} in lines


@pytest.mark.asyncio
async def test_a_failing_purge_logs_the_class_only_and_keeps_going(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []
    twice = threading.Event()

    def job() -> int:
        calls.append(1)
        if len(calls) >= 2:
            twice.set()
        raise RuntimeError("password=hunter2 at db")

    caplog.set_level(logging.INFO, logger="tutor.web")
    await run_lifespan(PeriodicPurge(lifespan_app, job, every_s=0.01), twice)
    assert len(calls) >= 2
    assert "hunter2" not in caplog.text
    assert "web session purge failed exc=RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_http_passes_through() -> None:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    app = PeriodicPurge(lifespan_app, lambda: 0)
    await app({"type": "http", "method": "GET", "path": "/"}, receive, send)
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_shutdown_is_not_blocked_by_a_running_purge() -> None:
    started, release = threading.Event(), threading.Event()
    seen_by_inner: list[bool] = []

    def job() -> int:
        started.set()
        release.wait(10)  # a stuck purge
        return 0

    async def inner(scope: Scope, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await asyncio.sleep(0.05)  # the inner shutdown takes a moment
                seen_by_inner.append(True)
                await send({"type": "lifespan.shutdown.complete"})
                return

    try:
        await asyncio.wait_for(run_lifespan(PeriodicPurge(inner, job, every_s=0.01), started), 3)
    finally:
        release.set()
    assert started.is_set()
    assert seen_by_inner == [True]


@pytest.mark.asyncio
async def test_the_periodic_task_is_cancelled_as_soon_as_shutdown_is_received() -> None:
    calls: list[int] = []
    ready = threading.Event()
    after_shutdown: list[int] = []
    shutdown_seen = asyncio.Event()

    def job() -> int:
        calls.append(1)
        ready.set()
        if shutdown_seen.is_set():
            after_shutdown.append(1)
        return 0

    async def inner(scope: Scope, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                shutdown_seen.set()
                await asyncio.sleep(0.2)  # slow inner shutdown; the loop must not tick now
                await send({"type": "lifespan.shutdown.complete"})
                return

    await run_lifespan(PeriodicPurge(inner, job, every_s=0.01), ready)
    assert after_shutdown == []


def test_build_time_check_rejects_lifetimes_the_database_function_does_not_have() -> None:
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from tutor.app import _session_purge
    from tutor.web.config import WebConfig

    config = WebConfig(
        env="dev",
        base_url="http://localhost:8000",
        mcp_url="http://localhost:8000/mcp",
        support_email="a@example.com",
        session_idle_days=7,
    )
    with pytest.raises(SystemExit, match="14 days idle"):
        _session_purge(MagicMock(), config, lambda: datetime.now(UTC))
