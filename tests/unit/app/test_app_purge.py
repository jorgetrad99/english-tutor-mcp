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
