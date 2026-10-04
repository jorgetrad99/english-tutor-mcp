# Spike Server and Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the throwaway spike MCP server (`get_profile`, `end_session`, Google OAuth, raw JSONL logging) and the offline analysis that turns 25 runs into the numbers for `docs/spike/01–04`.

**Architecture:** A separate uv project in `spike/` (package `tutor_spike`). FastMCP 4 serves Streamable HTTP in JSON-response mode behind a pure-ASGI middleware that logs every HTTP request before SDK validation. Tools write their own `kind: "tool"` lines (tester label, issued `session_id`). An offline CLI joins those logs with the author's `runs.csv`, transcripts and annotations and prints markdown tables. No LLM calls anywhere.

**Tech Stack:** Python 3.12, `fastmcp` 4.x (`GoogleProvider`), `uvicorn`, `jsonschema`, `pytest` + `pytest-asyncio`, `httpx` (ships with fastmcp).

**Spec:** `docs/superpowers/specs/2026-10-03-spike-design.md` (approved 2026-10-03). Experiment files: `docs/spike/01-voice-mode-tool-calls.md` … `04-grader-reliability.md`.

## Global Constraints

- All code lives in `spike/` as its own uv project (`spike/pyproject.toml`). Nothing in `src/tutor`, root `pyproject.toml` dependencies or `justfile` changes.
- `fastmcp>=4.0.10,<5`; Python `>=3.12`.
- Root `uv run just check` must still pass after every task (root `ruff check .` and `ruff format --check .` reach `spike/`; mypy and root pytest do not).
- Spike tests run with `uv run --directory spike pytest -q`.
- CLAUDE.md: use Context7 (`/websites/gofastmcp`) before using a FastMCP API not already shown in this plan. Where a task's first step says "verify", do it before writing code.
- MCP rules: closed enums (`Literal`), unknown fields rejected (`additionalProperties: false` at every object level), `title` and `description` on every field, tool descriptions ≤ 120 words, every successful result has `response_rules`.
- `end_session` input is the requirements section 7 schema verbatim: `session_id`, `user_turns` (≥ 1), `errors[]` (`said`, `correct`, `category` ∈ grammar|lexis|word_order|register|other), `chunks_used[]`, `task_result` ∈ achieved|partial|not_achieved, `hints_given`, `cefr_estimate` (`speaking` ∈ B1|B1+|B2|B2+|C1, `confidence` ∈ low|medium|high, `evidence[]`), `confidence_1_5`, `assistant_words_estimate?`.
- Server instructions = spec §3.3 text, frozen before run 1 (git tag `spike-instructions-v1`).
- Normalization (spec §8): Unicode NFKC, lowercase, punctuation stripped, whitespace collapsed. One implementation (`tutor_spike.normalize`) used by the server and the analysis.
- Never logged anywhere: the `Authorization` header, tokens, authorization codes, client secrets, emails. Auth routes are logged as method, path, status, latency only.
- Raw data lives only in `spike/data/raw/` (gitignored). Only redacted copies go to `docs/spike/NN-data/` and `evals/fixtures/`.
- OAuth: scopes `openid` + `https://www.googleapis.com/auth/userinfo.email`; allowed client redirect `https://claude.ai/api/mcp/auth_callback`; MCP path `/mcp`; connector URL `https://<spike-host>/mcp`.
- `.env*` files are hook-protected: Claude never writes them. The template is `spike/env.example`; the author copies it to `spike/.env`.
- Log join (refines spec §6.1): HTTP lines and tool lines are written separately and joined offline by time window (runs are sequential, one author). Tool lines carry the tester label.
- Run sheet adds one column to spec §6.2: `continued_with_result (y|n|na)`, the third condition of spec §8.1.

## Review Focus

1. Voice speech-to-text produces curly apostrophes and stray punctuation (`I don’t knew,`) while `user_turns` has straight ones: the `said` rule must still match. Pinned in Task 1.
2. The laptop or server restarts in the middle of a run: a `session_id` issued before the restart must still be accepted by `end_session`. Pinned in Task 2.
3. Claude sends an unknown field or an out-of-enum value: the call must fail with retry `response_rules`, and the raw arguments must still be in the log. Pinned in Tasks 3 and 4.
4. Two runs start within the grace window of each other: each log line must be counted for exactly one run (the later one once it has started). Pinned in Task 6.
5. No run reports any `errors`: the `said` criterion must report "n/a" and not crash or divide by zero. Pinned in Task 6.

---

## File Structure

```
spike/
  pyproject.toml            uv project, ruff config extending the root
  env.example               settings template (author copies to spike/.env)
  .gitignore                data/raw/
  README.md                 runbook: setup, start, pre-run checklist, after-run, analysis
  deck.md                   the 12 situation cards
  runs.template.csv         run sheet header
  src/tutor_spike/
    __init__.py
    normalize.py            normalize(), said_in_turns()
    sessions.py             SessionRegistry (issued/ended session_ids, persisted JSONL)
    eventlog.py             JsonlLog (daily calls-YYYY-MM-DD.jsonl)
    middleware.py           RawLogMiddleware (pure ASGI, logs before validation)
    contract.py             enums, nested models, field annotations, descriptions, response_rules, INSTRUCTIONS
    tools.py                build_mcp(): get_profile, end_session, retry-rules middleware
    testers.py              parse_testers(), token_tester()
    server.py               Settings, google_auth(), build_app(), main()
    redact.py               redact(), export CLI
    analysis/
      __init__.py
      runs.py               Run, load_runs()
      scoring.py            load_records(), assign(), score_run(), summaries, verdicts
      fidelity.py           transcripts, user_turns/errors fidelity, CEFR stats
      __main__.py           report CLI
  tests/
    conftest.py             FIXED_NOW, read_log_lines(), end_session_schema fixture
    test_normalize.py
    test_sessions.py
    test_eventlog.py
    test_middleware.py
    test_tools.py
    test_server.py
    test_runs.py
    test_scoring.py
    test_fidelity.py
    test_report.py
    test_redact.py
```

---

### Task 1: Scaffold the spike project and the normalization rule

**Files:**
- Create: `spike/pyproject.toml`, `spike/.gitignore`, `spike/src/tutor_spike/__init__.py`, `spike/src/tutor_spike/normalize.py`
- Test: `spike/tests/test_normalize.py`

**Interfaces:**
- Produces: `normalize(text: str) -> str`; `said_in_turns(said: str, user_turns: list[str]) -> bool`

- [ ] **Step 1: Create the project files**

`spike/pyproject.toml`:

```toml
[project]
name = "tutor-spike"
version = "0.0.0"
description = "Throwaway week-0 spike (docs/superpowers/specs/2026-10-03-spike-design.md)."
requires-python = ">=3.12"
dependencies = [
    "fastmcp>=4.0.10,<5",
    "jsonschema>=4.23",
    "uvicorn>=0.30",
]

[dependency-groups]
dev = [
    "pytest>=9.1.1",
    "pytest-asyncio>=1.4.0",
]

[build-system]
requires = ["uv_build>=0.12.22,<0.13.0"]
build-backend = "uv_build"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"

[tool.ruff]
extend = "../pyproject.toml"

[tool.ruff.lint]
# The frozen instructions use the spec's en dashes; tests use curly quotes and full-width text.
ignore = ["RUF001"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]
```

`spike/.gitignore`:

```
data/raw/
```

`spike/src/tutor_spike/__init__.py`:

```python
"""Throwaway week-0 spike. Deleted at the go/no-go (ADR 0001)."""
```

Run: `uv sync --directory spike`
Expected: creates `spike/.venv` and `spike/uv.lock`, no errors.

- [ ] **Step 2: Write the failing tests**

`spike/tests/test_normalize.py`:

```python
from tutor_spike.normalize import normalize, said_in_turns


def test_normalize_lowercases_strips_punctuation_and_collapses_spaces() -> None:
    assert normalize("  Hello,   WORLD! ") == "hello world"


def test_normalize_applies_nfkc() -> None:
    assert normalize("Ｈｅｌｌｏ ﬁx") == "hello fix"


def test_normalize_treats_curly_and_straight_apostrophes_alike() -> None:
    assert normalize("I don’t knew") == normalize("I don't knew") == "i dont knew"


def test_normalize_keeps_accented_letters() -> None:
    assert normalize("Está bien") == "está bien"


def test_said_found_inside_a_turn_despite_punctuation() -> None:
    turns = ["Yesterday, I go to the office… and the deploy was broken."]
    assert said_in_turns("yesterday I go to the office", turns)


def test_said_from_speech_to_text_with_curly_quote_matches() -> None:
    assert said_in_turns("I don’t knew,", ["Sorry, I don't knew the answer"])


def test_said_absent_from_every_turn_fails() -> None:
    assert not said_in_turns("I have 30 years", ["I am thirty", "We ship on Friday"])


def test_said_spanning_two_turns_fails() -> None:
    assert not said_in_turns("broken we need", ["It was broken.", "We need time."])


def test_empty_or_punctuation_only_said_fails() -> None:
    assert not said_in_turns("", ["anything"])
    assert not said_in_turns("?!", ["anything ?!"])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --directory spike pytest tests/test_normalize.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor_spike.normalize'`

- [ ] **Step 4: Implement**

`spike/src/tutor_spike/normalize.py`:

```python
"""Text normalization shared by the server's said rule and the offline analysis (spec §8)."""

import re
import unicodedata

_PUNCTUATION = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Unicode NFKC, lowercase, punctuation stripped, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _PUNCTUATION.sub("", text)
    return _SPACES.sub(" ", text).strip()


def said_in_turns(said: str, user_turns: list[str]) -> bool:
    """True when the normalized `said` is a non-empty substring of one normalized user turn."""
    needle = normalize(said)
    return bool(needle) and any(needle in normalize(turn) for turn in user_turns)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --directory spike pytest tests/test_normalize.py -q`
Expected: 9 passed

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff format spike` then `uv run ruff check spike` then `uv run just check`
Expected: all clean; root check passes unchanged.

```bash
git add spike/pyproject.toml spike/uv.lock spike/.gitignore spike/src spike/tests
git commit -m "feat(spike): scaffold spike project and said normalization rule"
```

---

### Task 2: Session registry and JSONL log

**Files:**
- Create: `spike/src/tutor_spike/sessions.py`, `spike/src/tutor_spike/eventlog.py`, `spike/tests/conftest.py`
- Test: `spike/tests/test_sessions.py`, `spike/tests/test_eventlog.py`

**Interfaces:**
- Produces:
  - `SessionRegistry(path: Path, now: Callable[[], datetime])` with `issue(tester: str) -> str`, `owner(session_id: str) -> str | None`, `record_end(session_id: str) -> int` (number of earlier accepted ends; 0 = first)
  - `JsonlLog(directory: Path, now: Callable[[], datetime])` with `write(record: dict[str, Any]) -> None`, appending to `directory / f"calls-{now():%Y-%m-%d}.jsonl"`
  - `tests/conftest.py`: `FIXED_NOW: Callable[[], datetime]` (2026-10-06 15:00 UTC) and `read_log_lines(directory: Path) -> list[dict[str, Any]]`

- [ ] **Step 1: Write conftest helpers**

`spike/tests/conftest.py`:

```python
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def FIXED_NOW() -> datetime:
    return datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


def read_log_lines(directory: Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for path in sorted(directory.glob("calls-*.jsonl")):
        lines += [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    return lines
```

- [ ] **Step 2: Write the failing tests**

`spike/tests/test_sessions.py`:

```python
import uuid
from pathlib import Path

from conftest import FIXED_NOW
from tutor_spike.sessions import SessionRegistry


def test_issue_returns_uuid_owned_by_tester(tmp_path: Path) -> None:
    registry = SessionRegistry(tmp_path / "sessions.jsonl", FIXED_NOW)
    session_id = registry.issue("author-free")
    uuid.UUID(session_id)
    assert registry.owner(session_id) == "author-free"


def test_unknown_session_has_no_owner(tmp_path: Path) -> None:
    registry = SessionRegistry(tmp_path / "sessions.jsonl", FIXED_NOW)
    assert registry.owner("not-issued") is None


def test_record_end_counts_earlier_ends(tmp_path: Path) -> None:
    registry = SessionRegistry(tmp_path / "sessions.jsonl", FIXED_NOW)
    session_id = registry.issue("author-pro")
    assert registry.record_end(session_id) == 0
    assert registry.record_end(session_id) == 1


def test_sessions_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "sessions.jsonl"
    before = SessionRegistry(path, FIXED_NOW)
    session_id = before.issue("author-free")
    before.record_end(session_id)
    after = SessionRegistry(path, FIXED_NOW)
    assert after.owner(session_id) == "author-free"
    assert after.record_end(session_id) == 1


def test_blank_lines_in_the_file_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "sessions.jsonl"
    registry = SessionRegistry(path, FIXED_NOW)
    session_id = registry.issue("author-free")
    path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert SessionRegistry(path, FIXED_NOW).owner(session_id) == "author-free"
```

`spike/tests/test_eventlog.py`:

```python
from pathlib import Path

from conftest import FIXED_NOW, read_log_lines
from tutor_spike.eventlog import JsonlLog


def test_write_appends_one_line_per_record_to_the_daily_file(tmp_path: Path) -> None:
    log = JsonlLog(tmp_path, FIXED_NOW)
    log.write({"kind": "http", "n": 1})
    log.write({"kind": "tool", "n": 2})
    assert (tmp_path / "calls-2026-10-06.jsonl").exists()
    assert [line["n"] for line in read_log_lines(tmp_path)] == [1, 2]


def test_write_keeps_non_ascii_text_readable(tmp_path: Path) -> None:
    JsonlLog(tmp_path, FIXED_NOW).write({"said": "está"})
    assert "está" in (tmp_path / "calls-2026-10-06.jsonl").read_text(encoding="utf-8")


def test_write_creates_the_directory(tmp_path: Path) -> None:
    JsonlLog(tmp_path / "raw", FIXED_NOW).write({"kind": "http"})
    assert read_log_lines(tmp_path / "raw") == [{"kind": "http"}]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --directory spike pytest tests/test_sessions.py tests/test_eventlog.py -q`
Expected: FAIL with `ModuleNotFoundError` for `tutor_spike.sessions` and `tutor_spike.eventlog`

- [ ] **Step 4: Implement**

`spike/src/tutor_spike/sessions.py`:

```python
"""Spike-only session_id registry: get_profile issues, end_session checks (spec §3.2)."""

import json
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


class SessionRegistry:
    """Issued and ended session_ids, persisted as JSONL so a restart keeps them."""

    def __init__(self, path: Path, now: Callable[[], datetime]) -> None:
        self._path = path
        self._now = now
        self._lock = threading.Lock()
        self._owners: dict[str, str] = {}
        self._ends: dict[str, int] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._apply(json.loads(line))

    def issue(self, tester: str) -> str:
        session_id = str(uuid.uuid4())
        self._record({"event": "issued", "session_id": session_id, "tester": tester})
        return session_id

    def owner(self, session_id: str) -> str | None:
        return self._owners.get(session_id)

    def record_end(self, session_id: str) -> int:
        """Record an accepted end_session; return how many were recorded before it."""
        previous = self._ends.get(session_id, 0)
        self._record({"event": "ended", "session_id": session_id})
        return previous

    def _record(self, event: dict[str, str]) -> None:
        with self._lock:
            self._apply(event)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": self._now().isoformat(), **event}) + "\n")

    def _apply(self, event: dict[str, str]) -> None:
        session_id = event["session_id"]
        if event["event"] == "issued":
            self._owners[session_id] = event["tester"]
        elif event["event"] == "ended":
            self._ends[session_id] = self._ends.get(session_id, 0) + 1
```

`spike/src/tutor_spike/eventlog.py`:

```python
"""Append-only daily JSONL log for HTTP and tool records (spec §6.1)."""

import json
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonlLog:
    def __init__(self, directory: Path, now: Callable[[], datetime]) -> None:
        self._directory = directory
        self._now = now
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        path = self._directory / f"calls-{self._now():%Y-%m-%d}.jsonl"
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._directory.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --directory spike pytest -q`
Expected: 17 passed

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff format spike`, `uv run ruff check spike`, `uv run just check`
Expected: clean.

```bash
git add spike/src/tutor_spike/sessions.py spike/src/tutor_spike/eventlog.py spike/tests
git commit -m "feat(spike): persisted session registry and daily JSONL log"
```

---

### Task 3: Raw logging middleware

**Files:**
- Create: `spike/src/tutor_spike/middleware.py`
- Test: `spike/tests/test_middleware.py`

**Interfaces:**
- Consumes: `JsonlLog` (Task 2)
- Produces: `RawLogMiddleware(app: ASGIApp, log: JsonlLog, now: Callable[[], datetime], server_sha: str, mcp_path: str = "/mcp")`, a pure ASGI app; attribute `.app` is the wrapped app. Writes one record per HTTP request:
  `{"kind": "http", "ts_in", "ts_out", "latency_ms", "server_sha", "http": {"method", "path", "status", "user_agent", "mcp_session_id", "mcp_protocol_version"}, "rpc": {"id", "method", "tool", "client_info", "params_raw", "result_raw", "error_raw", "request_raw"} | None}`. `rpc` is `None` for every path other than `mcp_path`.

- [ ] **Step 1: Write the failing tests**

`spike/tests/test_middleware.py`:

```python
import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from conftest import FIXED_NOW, read_log_lines
from tutor_spike.eventlog import JsonlLog
from tutor_spike.middleware import RawLogMiddleware

SSE_BODY = 'event: message\ndata: {"jsonrpc": "2.0", "id": 3, "result": {"ok": true}}\n\n'


async def mcp_endpoint(request: Request) -> Response:
    body = await request.json()
    if body.get("id") == 3:
        return Response(SSE_BODY, media_type="text/event-stream")
    if body.get("method") == "tools/call" and body["params"]["arguments"].get("bad"):
        error = {"code": -32602, "message": "Unexpected field 'bad'"}
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "error": error})
    result = {"structuredContent": {"ok": True}}
    return JSONResponse(
        {"jsonrpc": "2.0", "id": body.get("id"), "result": result},
        headers={"mcp-session-id": "sess-1"},
    )


async def token_endpoint(request: Request) -> Response:
    await request.body()
    return JSONResponse({"access_token": "secret-access-token"})


async def crash(request: Request) -> Response:
    raise RuntimeError("boom")


def make_app(tmp_path: Path) -> RawLogMiddleware:
    inner = Starlette(
        routes=[
            Route("/mcp", mcp_endpoint, methods=["POST"]),
            Route("/token", token_endpoint, methods=["POST"]),
            Route("/crash", crash, methods=["POST"]),
        ]
    )
    ticks = itertools.count()

    def clock() -> datetime:
        return FIXED_NOW() + timedelta(milliseconds=10 * next(ticks))

    return RawLogMiddleware(
        inner, log=JsonlLog(tmp_path, FIXED_NOW), now=clock, server_sha="abc1234"
    )


def client(app: RawLogMiddleware) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_tool_call_is_logged_with_raw_arguments_and_result(tmp_path: Path) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "end_session", "arguments": {"session_id": "s", "user_turns": ["hi"]}},
    }
    headers = {"user-agent": "Claude-User", "mcp-protocol-version": "2025-06-18"}
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json=payload, headers=headers)
    [record] = read_log_lines(tmp_path)
    assert record["kind"] == "http"
    assert record["server_sha"] == "abc1234"
    assert record["latency_ms"] >= 0
    assert record["http"] == {
        "method": "POST",
        "path": "/mcp",
        "status": 200,
        "user_agent": "Claude-User",
        "mcp_session_id": "sess-1",
        "mcp_protocol_version": "2025-06-18",
    }
    assert record["rpc"]["id"] == 7
    assert record["rpc"]["tool"] == "end_session"
    assert record["rpc"]["params_raw"]["arguments"] == {"session_id": "s", "user_turns": ["hi"]}
    assert record["rpc"]["result_raw"] == {"structuredContent": {"ok": True}}


async def test_rejected_arguments_are_still_logged_verbatim(tmp_path: Path) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "end_session", "arguments": {"bad": "glossary"}},
    }
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json=payload)
    [record] = read_log_lines(tmp_path)
    assert record["rpc"]["params_raw"]["arguments"] == {"bad": "glossary"}
    assert record["rpc"]["error_raw"]["code"] == -32602


async def test_initialize_client_info_is_extracted(tmp_path: Path) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "claude-ai", "version": "0.1.0"}},
    }
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json=payload)
    [record] = read_log_lines(tmp_path)
    assert record["rpc"]["client_info"] == {"name": "claude-ai", "version": "0.1.0"}


async def test_sse_response_body_is_parsed(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"})
    [record] = read_log_lines(tmp_path)
    assert record["rpc"]["result_raw"] == {"ok": True}


async def test_auth_routes_never_log_bodies_or_secrets(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        await c.post(
            "/token",
            content=b"grant_type=authorization_code&code=secret-code",
            headers={"authorization": "Bearer secret-bearer"},
        )
    [record] = read_log_lines(tmp_path)
    assert record["rpc"] is None
    assert record["http"]["path"] == "/token"
    raw = (tmp_path / "calls-2026-10-06.jsonl").read_text(encoding="utf-8")
    for secret in ("secret-code", "secret-bearer", "secret-access-token"):
        assert secret not in raw


async def test_authorization_header_is_never_logged_on_mcp_path(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
            headers={"authorization": "Bearer secret-bearer"},
        )
    raw = (tmp_path / "calls-2026-10-06.jsonl").read_text(encoding="utf-8")
    assert "secret-bearer" not in raw


async def test_crashing_request_is_still_logged(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        response = await c.post("/crash", json={})
    assert response.status_code == 500
    [record] = read_log_lines(tmp_path)
    assert record["http"]["status"] == 500


async def test_timestamps_are_utc_iso(tmp_path: Path) -> None:
    async with client(make_app(tmp_path)) as c:
        await c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "ping"})
    [record] = read_log_lines(tmp_path)
    assert datetime.fromisoformat(record["ts_in"]).tzinfo == UTC
    assert record["ts_out"] >= record["ts_in"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --directory spike pytest tests/test_middleware.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor_spike.middleware'`

- [ ] **Step 3: Implement**

`spike/src/tutor_spike/middleware.py`:

```python
"""Outer ASGI middleware: logs every HTTP request before the SDK validates it (spec §6.1)."""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tutor_spike.eventlog import JsonlLog

_LOGGED_HEADERS = ("user-agent", "mcp-session-id", "mcp-protocol-version")


class RawLogMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        log: JsonlLog,
        now: Callable[[], datetime],
        server_sha: str,
        mcp_path: str = "/mcp",
    ) -> None:
        self.app = app
        self._log = log
        self._now = now
        self._server_sha = server_sha
        self._mcp_path = mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ts_in = self._now()
        log_bodies = scope["path"].rstrip("/") == self._mcp_path
        request_body: list[bytes] = []
        response_body: list[bytes] = []
        response_headers: dict[str, str] = {}
        status = 0

        async def receive_logged() -> Message:
            message = await receive()
            if log_bodies and message["type"] == "http.request":
                request_body.append(message.get("body", b""))
            return message

        async def send_logged(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers.update(_headers(message.get("headers", [])))
            elif log_bodies and message["type"] == "http.response.body":
                response_body.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_logged, send_logged)
        finally:
            ts_out = self._now()
            request_headers = _headers(scope.get("headers", []))
            self._log.write(
                {
                    "kind": "http",
                    "ts_in": ts_in.isoformat(),
                    "ts_out": ts_out.isoformat(),
                    "latency_ms": int((ts_out - ts_in).total_seconds() * 1000),
                    "server_sha": self._server_sha,
                    "http": {
                        "method": scope.get("method"),
                        "path": scope["path"],
                        "status": status if status else 500,
                        "user_agent": request_headers.get("user-agent"),
                        "mcp_session_id": request_headers.get("mcp-session-id")
                        or response_headers.get("mcp-session-id"),
                        "mcp_protocol_version": request_headers.get("mcp-protocol-version"),
                    },
                    "rpc": _rpc(b"".join(request_body), b"".join(response_body))
                    if log_bodies
                    else None,
                }
            )


def _headers(raw: Any) -> dict[str, str]:
    """Only the allowlisted headers; Authorization and cookies are never kept."""
    headers: dict[str, str] = {}
    for key, value in raw:
        name = key.decode("latin-1").lower()
        if name in _LOGGED_HEADERS:
            headers[name] = value.decode("latin-1")
    return headers


def _parse_body(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    events: list[Any] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data = line[len("data:") :].strip()
            try:
                events.append(json.loads(data))
            except json.JSONDecodeError:
                events.append({"unparsed": data})
    if len(events) == 1:
        return events[0]
    return events or {"unparsed": text}


def _rpc(request_raw: bytes, response_raw: bytes) -> dict[str, Any]:
    request = _parse_body(request_raw)
    response = _parse_body(response_raw)
    rpc: dict[str, Any] = {
        "id": None,
        "method": None,
        "tool": None,
        "client_info": None,
        "params_raw": None,
        "result_raw": None,
        "error_raw": None,
        "request_raw": None,
    }
    if isinstance(request, dict):
        params = request.get("params")
        rpc["id"] = request.get("id")
        rpc["method"] = request.get("method")
        rpc["params_raw"] = params
        if isinstance(params, dict):
            if rpc["method"] == "tools/call":
                rpc["tool"] = params.get("name")
            if rpc["method"] == "initialize":
                rpc["client_info"] = params.get("clientInfo")
    else:
        rpc["request_raw"] = request
    if isinstance(response, dict):
        rpc["result_raw"] = response.get("result")
        rpc["error_raw"] = response.get("error")
    else:
        rpc["result_raw"] = response
    return rpc
```

Note: the SSE test expects `result_raw == {"ok": True}`: `_parse_body` returns the single event dict, and `_rpc` takes its `result`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --directory spike pytest -q`
Expected: 25 passed

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff format spike`, `uv run ruff check spike`, `uv run just check`

```bash
git add spike/src/tutor_spike/middleware.py spike/tests/test_middleware.py
git commit -m "feat(spike): raw request logging middleware that never logs secrets"
```

---

### Task 4: MCP contract and tools

**Files:**
- Create: `spike/src/tutor_spike/contract.py`, `spike/src/tutor_spike/testers.py`, `spike/src/tutor_spike/tools.py`
- Test: `spike/tests/test_tools.py`; modify `spike/tests/conftest.py` (add `end_session_schema` fixture)

**Interfaces:**
- Consumes: `SessionRegistry`, `JsonlLog` (Task 2); `said_in_turns` (Task 1)
- Produces:
  - `contract.INSTRUCTIONS: str`, `contract.ErrorItem`, `contract.CefrEstimate`, response-rule constants
  - `testers.parse_testers(raw: str) -> dict[str, str]`; `testers.token_tester(testers: dict[str, str]) -> Callable[[], str | None]`
  - `tools.build_mcp(*, registry: SessionRegistry, log: JsonlLog, resolve_tester: Callable[[], str | None], now: Callable[[], datetime], display_name: str, auth: AuthProvider | None = None) -> FastMCP`
  - Tool lines written to the log: `{"kind": "tool", "ts", "tool", "tester", "mcp_session_id", "rpc_id", ...}`; `get_profile` adds `session_id`; `end_session` adds `session_id`, `accepted`, and either `reason` or `errors_kept`, `errors_rejected`, `repeat`.

- [ ] **Step 1: Verify FastMCP APIs in Context7**

Query `/websites/gofastmcp` for: `FastMCP(..., instructions=, auth=, strict_input_validation=)`, the `AuthProvider` import path (`fastmcp.server.auth`), `mcp.add_middleware`, `Middleware.on_call_tool` / `MiddlewareContext.message.name`, `fastmcp.exceptions.ToolError`, `fastmcp.Context` (`request_id`, `session_id`), `fastmcp.Client(server).call_tool(name, args, raise_on_error=False)` and its result fields (`is_error`, `structured_content`, `content`), `client.list_tools()` (`inputSchema`). Adjust names in the code below only if the docs differ, and note any difference in the commit message.

- [ ] **Step 2: Write the contract module**

`spike/src/tutor_spike/contract.py`:

```python
"""Spike MCP contract: section 7 end_session schema, descriptions, response_rules (spec §3)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["grammar", "lexis", "word_order", "register", "other"]
TaskResult = Literal["achieved", "partial", "not_achieved"]
Speaking = Literal["B1", "B1+", "B2", "B2+", "C1"]
Confidence = Literal["low", "medium", "high"]


class ErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    said: str = Field(
        title="Said",
        description="The learner's words exactly as they said them, copied from user_turns.",
    )
    correct: str = Field(
        title="Correct", description="A natural, correct version of what the learner meant."
    )
    category: Category = Field(title="Category", description="The main kind of error.")


class CefrEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaking: Speaking = Field(
        title="Speaking level", description="Your estimate of the learner's speaking level today."
    )
    confidence: Confidence = Field(title="Confidence", description="How sure you are.")
    evidence: list[str] = Field(
        title="Evidence",
        description="Short quotes or observations from this conversation supporting the level.",
    )


SessionId = Annotated[
    str,
    Field(
        title="Session ID",
        description="The session_id returned by get_profile at the start of this session.",
    ),
]
UserTurns = Annotated[
    list[str],
    Field(
        title="User turns",
        description="Everything the learner said, one entry per turn, in their exact words.",
        min_length=1,
    ),
]
Errors = Annotated[
    list[ErrorItem],
    Field(
        title="Errors",
        description="The learner's errors, each quoted exactly from user_turns. Empty if none.",
    ),
]
ChunksUsed = Annotated[
    list[str],
    Field(
        title="Chunks used",
        description="IDs of the chunks of the day the learner used. Empty if none were given.",
    ),
]
TaskResultField = Annotated[
    TaskResult,
    Field(title="Task result", description="Whether the learner reached the scenario objective."),
]
HintsGiven = Annotated[
    int,
    Field(title="Hints given", description="How many hints you gave in the scenario.", ge=0),
]
CefrField = Annotated[
    CefrEstimate,
    Field(
        title="CEFR estimate", description="Your level estimate for this session, with evidence."
    ),
]
Confidence15 = Annotated[
    int,
    Field(
        title="Learner confidence 1-5",
        description="How confident the learner sounded: 1 very unsure, 5 very confident.",
        ge=1,
        le=5,
    ),
]
AssistantWords = Annotated[
    int | None,
    Field(
        title="Assistant words estimate",
        description="Rough number of words you spoke in this session, if you can estimate it.",
        ge=0,
    ),
]

GET_PROFILE_DESCRIPTION = (
    "Call once at the start of every English practice session, before greeting the learner. "
    "Returns the learner's profile and a session_id. Keep the session_id: end_session needs "
    "it at the end of the session."
)
END_SESSION_DESCRIPTION = (
    "Call once at the end of every practice session, including when the learner says they "
    "have to go. Send the session_id from get_profile and evidence from this conversation "
    "only: the learner's turns in their exact words, their errors quoted exactly from those "
    "turns, the scenario result, hints given, and your level estimate with evidence. Never "
    "invent turns, errors, counts or levels."
)

GET_PROFILE_RULES = (
    "Greet the learner by name in one sentence and ask which work situation they want to "
    "practise. Do not read this profile aloud."
)
END_SESSION_RULES = (
    "Tell the learner in one sentence that the session is saved. Do not read numbers aloud."
)
RETRY_RULES = (
    "Fix the listed fields and call end_session again with the same session_id. "
    "Do not mention this to the learner."
)
UNKNOWN_SESSION = (
    "Unknown session_id. Call get_profile, then call end_session again with the session_id "
    "it returns. Do not mention this to the learner."
)
NOT_ALLOWED = "This account is not enabled for the spike."

INSTRUCTIONS = (
    "You are an English speaking tutor for a Spanish-speaking software professional. "
    "Speak English unless the learner asks otherwise. In voice sessions answer in 1–3 "
    "sentences.\n"
    "At the start of every practice session call `get_profile` and keep its `session_id`.\n"
    "Run the session in this order. Open: greet the learner and ask which work situation they "
    "want to practise. Scenario, about 10–12 minutes: play the other person, stay in "
    "character, keep your turns short, give at most 3 hints, and do not correct the learner "
    "during the scenario. Feedback: step out of character and give the 2–3 most useful "
    "corrections and one thing done well.\n"
    "At the end of the session, including when the learner says they have to go, call "
    "`end_session` with the `session_id` from `get_profile` and the evidence from this "
    "conversation: the learner's turns as they said them, their errors quoted exactly, and "
    "your level estimate with evidence. Never invent turns, errors, counts or levels that are "
    "not in the conversation.\n"
    "No markdown, lists or headings while in conversation."
)
```

- [ ] **Step 3: Write the failing tests**

Replace `spike/tests/conftest.py` with:

```python
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
        return dict(tools["end_session"].inputSchema)

    return asyncio.run(fetch())
```

`spike/tests/test_tools.py`:

```python
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from conftest import make_server, read_log_lines
from tutor_spike.contract import INSTRUCTIONS
from tutor_spike.testers import parse_testers


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


async def test_session_id_of_another_tester_is_rejected(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path, tester="author-pro")) as client:
        session_id = await start(client)
    async with Client(make_server(tmp_path, tester="author-free")) as client:
        result = await client.call_tool("end_session", valid_args(session_id), raise_on_error=False)
    assert result.is_error


async def test_unknown_field_is_rejected_with_retry_rules(tmp_path: Path) -> None:
    async with Client(make_server(tmp_path)) as client:
        session_id = await start(client)
        args = {**valid_args(session_id), "glossary": ["deploy"]}
        result = await client.call_tool("end_session", args, raise_on_error=False)
    assert result.is_error
    assert "same session_id" in text_of(result)


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
        for obj in _object_schemas(tool.inputSchema):
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run --directory spike pytest tests/test_tools.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor_spike.tools'` (collection error from `conftest.py`)

- [ ] **Step 5: Implement testers and tools**

`spike/src/tutor_spike/testers.py`:

```python
"""Map the OAuth identity (Google email) to a tester label; logs keep only the label."""

from collections.abc import Callable


def parse_testers(raw: str) -> dict[str, str]:
    """'a@x.com=author-free,b@y.com=author-pro' -> {email (lowercased): label}."""
    testers: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        email, sep, label = entry.partition("=")
        if not sep or not email.strip() or not label.strip():
            raise ValueError(f"Bad SPIKE_TESTERS entry: {entry!r} (want email=label)")
        testers[email.strip().lower()] = label.strip()
    return testers


def token_tester(testers: dict[str, str]) -> Callable[[], str | None]:
    """Resolver for tools: the caller's tester label, or None if not allowlisted."""

    def resolve() -> str | None:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
        if token is None:
            return None
        return testers.get(str(token.claims.get("email", "")).lower())

    return resolve
```

`spike/src/tutor_spike/tools.py`:

```python
"""get_profile and end_session (spec §3.2). Tool lines carry the tester label."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import Middleware, MiddlewareContext

from tutor_spike import contract as c
from tutor_spike.eventlog import JsonlLog
from tutor_spike.normalize import said_in_turns
from tutor_spike.sessions import SessionRegistry


class RetryRulesMiddleware(Middleware):
    """Schema errors on end_session come back with the retry response_rules."""

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        try:
            return await call_next(context)
        except ToolError:
            raise
        except Exception as exc:
            if getattr(context.message, "name", None) != "end_session":
                raise
            raise ToolError(f"Invalid end_session arguments: {exc}. {c.RETRY_RULES}") from exc


def build_mcp(
    *,
    registry: SessionRegistry,
    log: JsonlLog,
    resolve_tester: Callable[[], str | None],
    now: Callable[[], datetime],
    display_name: str,
    auth: AuthProvider | None = None,
) -> FastMCP:
    mcp = FastMCP(
        name="english-tutor-spike",
        instructions=c.INSTRUCTIONS,
        auth=auth,
        strict_input_validation=True,
    )
    mcp.add_middleware(RetryRulesMiddleware())

    def tester_or_refuse() -> str:
        tester = resolve_tester()
        if tester is None:
            raise ToolError(c.NOT_ALLOWED)
        return tester

    def log_tool(ctx: Context, tool: str, tester: str, **fields: Any) -> None:
        try:
            mcp_session_id: str | None = ctx.session_id
        except RuntimeError:
            mcp_session_id = None
        log.write(
            {
                "kind": "tool",
                "ts": now().isoformat(),
                "tool": tool,
                "tester": tester,
                "mcp_session_id": mcp_session_id,
                "rpc_id": ctx.request_id,
                **fields,
            }
        )

    @mcp.tool(name="get_profile", description=c.GET_PROFILE_DESCRIPTION)
    def get_profile(ctx: Context) -> dict[str, Any]:
        tester = tester_or_refuse()
        session_id = registry.issue(tester)
        log_tool(ctx, "get_profile", tester, session_id=session_id)
        return {
            "display_name": display_name,
            "level": "B1+",
            "domain": "software",
            "native_language": "es",
            "plan_summary": "spike: free practice",
            "streak": 0,
            "open_session": False,
            "provisional_items": 0,
            "session_id": session_id,
            "response_rules": c.GET_PROFILE_RULES,
        }

    @mcp.tool(name="end_session", description=c.END_SESSION_DESCRIPTION)
    def end_session(
        session_id: c.SessionId,
        user_turns: c.UserTurns,
        errors: c.Errors,
        chunks_used: c.ChunksUsed,
        task_result: c.TaskResultField,
        hints_given: c.HintsGiven,
        cefr_estimate: c.CefrField,
        confidence_1_5: c.Confidence15,
        ctx: Context,
        assistant_words_estimate: c.AssistantWords = None,
    ) -> dict[str, Any]:
        tester = tester_or_refuse()
        if registry.owner(session_id) != tester:
            log_tool(
                ctx,
                "end_session",
                tester,
                session_id=session_id,
                accepted=False,
                reason="unknown_session",
            )
            raise ToolError(c.UNKNOWN_SESSION)
        kept = sum(said_in_turns(error.said, user_turns) for error in errors)
        rejected = len(errors) - kept
        repeat = registry.record_end(session_id)
        log_tool(
            ctx,
            "end_session",
            tester,
            session_id=session_id,
            accepted=True,
            errors_kept=kept,
            errors_rejected=rejected,
            repeat=repeat,
        )
        return {
            "accepted": True,
            "errors_kept": kept,
            "errors_rejected": rejected,
            "response_rules": c.END_SESSION_RULES,
        }

    return mcp
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --directory spike pytest -q`
Expected: all passed (25 from Tasks 1–3 plus the 15 in `test_tools.py`).

Contingencies, each to be recorded in the commit message if used:
- If `test_unknown_field_is_rejected_with_retry_rules` fails only on the `"same session_id"` assertion (FastMCP validates before tool middleware runs), check in Context7 for a hook that runs around argument validation and use it. If none exists, delete that one assertion, keep `assert result.is_error`, and add a line to spec §3.2 that schema errors carry FastMCP's own message.
- If `test_tool_schemas_follow_the_mcp_contract` fails on a missing top-level `additionalProperties`, set it after registration (`tool.parameters["additionalProperties"] = False` via `await mcp.get_tool(name)`, verified in Context7) so Claude sees the closed schema.

- [ ] **Step 7: Lint and commit**

Run: `uv run ruff format spike`, `uv run ruff check spike`, `uv run just check`

```bash
git add spike/src/tutor_spike/contract.py spike/src/tutor_spike/testers.py spike/src/tutor_spike/tools.py spike/tests
git commit -m "feat(spike): get_profile and end_session with section 7 schema"
```

---

### Task 5: HTTP app, Google OAuth and entry point

**Files:**
- Create: `spike/src/tutor_spike/server.py`, `spike/env.example`
- Test: `spike/tests/test_server.py`

**Interfaces:**
- Consumes: `build_mcp` (Task 4), `RawLogMiddleware` (Task 3), `JsonlLog`, `SessionRegistry` (Task 2), `parse_testers`, `token_tester` (Task 4)
- Produces:
  - `Settings` (frozen dataclass: `base_url`, `google_client_id`, `google_client_secret`, `jwt_signing_key`, `testers`, `data_dir`, `display_name`, `port`) with `Settings.from_env(env: Mapping[str, str]) -> Settings`
  - `google_auth(settings: Settings, client_storage: Any | None = None) -> GoogleProvider`
  - `build_app(settings, *, auth, server_sha: str, resolve_tester=None, now=utc_now) -> RawLogMiddleware`; `app.app` is the FastMCP Starlette app (has `.lifespan`)
  - `main() -> None` (run from `spike/`: `uv run --env-file .env python -m tutor_spike.server`; `SPIKE_DATA_DIR` and the analysis `--data` default are relative to `spike/`)

- [ ] **Step 1: Verify APIs in Context7**

Query `/websites/gofastmcp` for: `mcp.http_app(path=..., json_response=...)` parameters; `StarletteWithLifespan.lifespan`; `GoogleProvider` parameters `jwt_signing_key`, `client_storage`, `allowed_client_redirect_uris`, `redirect_path`; the in-memory store import (`key_value.aio.stores.memory.MemoryStore`). Adjust only names that differ.

- [ ] **Step 2: Write the failing tests**

`spike/tests/test_server.py`:

```python
import re
from pathlib import Path

import httpx
import pytest
from key_value.aio.stores.memory import MemoryStore

from conftest import read_log_lines
from tutor_spike.server import Settings, build_app, google_auth

HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
PROTOCOL = "2025-06-18"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0"},
    },
}
ENV = {
    "SPIKE_BASE_URL": "https://tutor-spike.example.com",
    "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "not-a-real-secret",
    "SPIKE_JWT_SIGNING_KEY": "k" * 64,
    "SPIKE_TESTERS": "me@gmail.com=author-free",
}


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env({**ENV, "SPIKE_DATA_DIR": str(tmp_path)})


def test_settings_require_every_secret() -> None:
    with pytest.raises(SystemExit, match="GOOGLE_CLIENT_SECRET"):
        Settings.from_env({k: v for k, v in ENV.items() if k != "GOOGLE_CLIENT_SECRET"})


def test_settings_defaults(tmp_path: Path) -> None:
    s = settings(tmp_path)
    assert s.port == 8765
    assert s.display_name == "Learner"
    assert s.testers == {"me@gmail.com": "author-free"}
    assert s.base_url == "https://tutor-spike.example.com"


async def test_app_serves_json_and_logs_initialize_and_tool_call(tmp_path: Path) -> None:
    app = build_app(
        settings(tmp_path), auth=None, server_sha="test", resolve_tester=lambda: "author-free"
    )
    inner = app.app
    transport = httpx.ASGITransport(app=app)
    async with inner.lifespan(inner):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as c:
            first = await c.post("/mcp", json=INIT, headers=HEADERS)
            assert first.status_code == 200
            assert first.headers["content-type"].startswith("application/json")
            session = {
                **HEADERS,
                "mcp-session-id": first.headers["mcp-session-id"],
                "mcp-protocol-version": PROTOCOL,
            }
            await c.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=session,
            )
            call = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_profile", "arguments": {}},
            }
            response = await c.post("/mcp", json=call, headers=session)
    assert response.json()["result"]["structuredContent"]["session_id"]
    http = [x for x in read_log_lines(tmp_path) if x["kind"] == "http"]
    assert http[0]["rpc"]["client_info"] == {"name": "test-client", "version": "0"}
    assert any((x["rpc"] or {}).get("tool") == "get_profile" for x in http)
    assert {x["server_sha"] for x in http} == {"test"}


async def test_unauthenticated_call_points_to_metadata_whose_resource_is_the_connector_url(
    tmp_path: Path,
) -> None:
    s = settings(tmp_path)
    app = build_app(s, auth=google_auth(s, client_storage=MemoryStore()), server_sha="test")
    inner = app.app
    transport = httpx.ASGITransport(app=app)
    async with inner.lifespan(inner):
        async with httpx.AsyncClient(transport=transport, base_url=s.base_url) as c:
            denied = await c.post("/mcp", json=INIT, headers=HEADERS)
            assert denied.status_code == 401
            match = re.search(r'resource_metadata="([^"]+)"', denied.headers["www-authenticate"])
            assert match
            metadata = (await c.get(match.group(1))).json()
    assert metadata["resource"] == f"{s.base_url}/mcp"
    assert metadata["authorization_servers"]
    assert any(x["http"]["status"] == 401 for x in read_log_lines(tmp_path))
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --directory spike pytest tests/test_server.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor_spike.server'`

- [ ] **Step 4: Implement**

`spike/src/tutor_spike/server.py`:

```python
"""Spike entry point: settings, Google OAuth proxy, logged ASGI app (spec §4, §5)."""

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.google import GoogleProvider

from tutor_spike.eventlog import JsonlLog
from tutor_spike.middleware import RawLogMiddleware
from tutor_spike.sessions import SessionRegistry
from tutor_spike.testers import parse_testers, token_tester
from tutor_spike.tools import build_mcp

CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
REQUIRED = (
    "SPIKE_BASE_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "SPIKE_JWT_SIGNING_KEY",
    "SPIKE_TESTERS",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Settings:
    base_url: str
    google_client_id: str
    google_client_secret: str
    jwt_signing_key: str
    testers: dict[str, str]
    data_dir: Path
    display_name: str
    port: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        missing = [key for key in REQUIRED if not env.get(key)]
        if missing:
            raise SystemExit(f"Missing settings in spike/.env: {', '.join(missing)}")
        return cls(
            base_url=env["SPIKE_BASE_URL"].rstrip("/"),
            google_client_id=env["GOOGLE_CLIENT_ID"],
            google_client_secret=env["GOOGLE_CLIENT_SECRET"],
            jwt_signing_key=env["SPIKE_JWT_SIGNING_KEY"],
            testers=parse_testers(env["SPIKE_TESTERS"]),
            data_dir=Path(env.get("SPIKE_DATA_DIR", "data/raw")),
            display_name=env.get("SPIKE_DISPLAY_NAME", "Learner"),
            port=int(env.get("SPIKE_PORT", "8765")),
        )


def google_auth(settings: Settings, client_storage: Any | None = None) -> GoogleProvider:
    options: dict[str, Any] = {} if client_storage is None else {"client_storage": client_storage}
    return GoogleProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        base_url=settings.base_url,
        required_scopes=SCOPES,
        jwt_signing_key=settings.jwt_signing_key,
        allowed_client_redirect_uris=[CLAUDE_CALLBACK],
        **options,
    )


def build_app(
    settings: Settings,
    *,
    auth: AuthProvider | None,
    server_sha: str,
    resolve_tester: Callable[[], str | None] | None = None,
    now: Callable[[], datetime] = utc_now,
) -> RawLogMiddleware:
    log = JsonlLog(settings.data_dir, now)
    mcp = build_mcp(
        registry=SessionRegistry(settings.data_dir / "sessions.jsonl", now),
        log=log,
        resolve_tester=resolve_tester or token_tester(settings.testers),
        now=now,
        display_name=settings.display_name,
        auth=auth,
    )
    inner = mcp.http_app(path="/mcp", json_response=True)
    return RawLogMiddleware(inner, log=log, now=now, server_sha=server_sha)


def git_sha() -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip()


def main() -> None:
    settings = Settings.from_env(os.environ)
    app = build_app(settings, auth=google_auth(settings), server_sha=git_sha())
    uvicorn.run(app, host="127.0.0.1", port=settings.port, proxy_headers=True)


if __name__ == "__main__":
    main()
```

`spike/env.example`:

```
# Copy to spike/.env (gitignored) and fill in. Never commit the filled file.
SPIKE_BASE_URL=https://tutor-spike.example.com
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
# python -c "import secrets; print(secrets.token_urlsafe(48))"
SPIKE_JWT_SIGNING_KEY=
# email=label pairs; the Free and Pro Claude accounts log in with different Google accounts
SPIKE_TESTERS=free-account@gmail.com=author-free,pro-account@gmail.com=author-pro
SPIKE_DISPLAY_NAME=Learner
SPIKE_PORT=8765
SPIKE_DATA_DIR=data/raw
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --directory spike pytest -q`
Expected: all passed.
If the server rejects protocol version `2025-06-18`, use the value the SDK exposes as its latest supported version (look it up in Context7) and note it in the commit.
If `metadata["resource"]` comes back with a trailing slash or without `/mcp`, fix `base_url` / `path` handling (Context7: FastMCP "base_url mcp_path double prefix") until it equals `https://tutor-spike.example.com/mcp`; this is the most likely silent failure with Claude (spec §4).

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff format spike`, `uv run ruff check spike`, `uv run just check`

```bash
git add spike/src/tutor_spike/server.py spike/env.example spike/tests/test_server.py
git commit -m "feat(spike): Google OAuth proxy and logged HTTP app"
```

---

### Task 6: Run sheet loading and experiment scoring

**Files:**
- Create: `spike/src/tutor_spike/analysis/__init__.py`, `spike/src/tutor_spike/analysis/runs.py`, `spike/src/tutor_spike/analysis/scoring.py`, `spike/runs.template.csv`
- Test: `spike/tests/test_runs.py`, `spike/tests/test_scoring.py`

**Interfaces:**
- Consumes: `said_in_turns` (Task 1); `end_session_schema` fixture (Task 4)
- Produces:
  - `Run` (frozen dataclass: `run_id, date, account, mode, model_shown, start, end, voice_stayed_active, continued_with_result, status, transcript_file`), `load_runs(path: Path, tz: ZoneInfo) -> list[Run]`
  - `load_records(directory: Path) -> list[dict]`, `assign(runs, records) -> dict[str, list[dict]]`, `payload_valid(arguments, schema, issued) -> bool`, `score_run(run, records, schema) -> RunOutcome`, `end_session_schema_from(records) -> dict`
  - `RunOutcome` (frozen dataclass: `run_id, tools_fired, end_calls, valid_first, valid_final, said_total, said_pass, cefr, chunks_fabricated, final_arguments`)
  - `Exp1Summary(passes, total, by_account, verdict)`, `summarize_exp1(runs, outcomes) -> Exp1Summary`
  - `Exp2Summary(n, valid_final, valid_first, said_pass, said_total, verdict)`, `summarize_exp2(runs, outcomes) -> Exp2Summary`

- [ ] **Step 1: Write the run sheet template**

`spike/runs.template.csv`:

```
run_id,date,account,device,app_version,mode,model_shown,situation_card,start_local,end_local,voice_stayed_active,continued_with_result,recording_file,transcript_file,status,abort_reason,notes
```

Format rules (also in the README, Task 9): `run_id` like `r01`; `date` `YYYY-MM-DD`; `account` `free|pro`; `mode` `voice|text`; times `HH:MM` local; `voice_stayed_active` and `continued_with_result` `y|n|na`; `status` `ok|aborted`.

- [ ] **Step 2: Write the failing tests**

`spike/tests/test_runs.py`:

```python
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tutor_spike.analysis.runs import load_runs

HEADER = (
    "run_id,date,account,device,app_version,mode,model_shown,situation_card,start_local,"
    "end_local,voice_stayed_active,continued_with_result,recording_file,transcript_file,"
    "status,abort_reason,notes\n"
)


def test_load_runs_converts_local_times_to_utc(tmp_path: Path) -> None:
    path = tmp_path / "runs.csv"
    path.write_text(
        HEADER + "r01,2026-10-06,free,iphone,1.0,voice,Sonnet,3,09:00,09:17,y,y,"
        "rec.mp4,transcripts/r01.md,ok,,\n",
        encoding="utf-8",
    )
    [run] = load_runs(path, ZoneInfo("America/Mexico_City"))
    assert run.start == datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
    assert run.end == datetime(2026, 10, 6, 15, 17, tzinfo=UTC)
    assert (run.account, run.mode, run.status) == ("free", "voice", "ok")


def test_run_crossing_midnight_ends_next_day(tmp_path: Path) -> None:
    path = tmp_path / "runs.csv"
    path.write_text(
        HEADER + "r02,2026-10-06,pro,laptop,web,text,Opus,1,23:50,00:06,na,na,,,ok,,\n",
        encoding="utf-8",
    )
    [run] = load_runs(path, ZoneInfo("UTC"))
    assert run.end == datetime(2026, 10, 7, 0, 6, tzinfo=UTC)
```

`spike/tests/test_scoring.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Any

from tutor_spike.analysis.runs import Run
from tutor_spike.analysis.scoring import (
    RunOutcome,
    assign,
    payload_valid,
    score_run,
    summarize_exp1,
    summarize_exp2,
)

T0 = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


def at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def run(
    run_id: str,
    start: float,
    end: float,
    mode: str = "text",
    account: str = "free",
    stayed: str = "na",
    continued: str = "na",
    status: str = "ok",
) -> Run:
    return Run(
        run_id=run_id,
        date="2026-10-06",
        account=account,
        mode=mode,
        model_shown="Sonnet",
        start=T0 + timedelta(minutes=start),
        end=T0 + timedelta(minutes=end),
        voice_stayed_active=stayed,
        continued_with_result=continued,
        status=status,
        transcript_file="",
    )


def http_call(
    minutes: float,
    tool: str,
    arguments: Any = None,
    result: Any = None,
    error: Any = None,
    status: int = 200,
) -> dict[str, Any]:
    return {
        "kind": "http",
        "ts_in": at(minutes),
        "http": {"status": status, "mcp_session_id": "s1"},
        "rpc": {
            "method": "tools/call",
            "tool": tool,
            "params_raw": {"name": tool, "arguments": arguments},
            "result_raw": result,
            "error_raw": error,
        },
    }


def issued(minutes: float, session_id: str, tester: str = "author-free") -> dict[str, Any]:
    return {
        "kind": "tool",
        "ts": at(minutes),
        "tool": "get_profile",
        "tester": tester,
        "session_id": session_id,
    }


def args(session_id: str = "sid-1", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": session_id,
        "user_turns": ["Yesterday I go to the office."],
        "errors": [
            {
                "said": "I go to the office",
                "correct": "I went to the office",
                "category": "grammar",
            },
            {"said": "invented error", "correct": "x", "category": "other"},
        ],
        "chunks_used": [],
        "task_result": "achieved",
        "hints_given": 0,
        "cefr_estimate": {"speaking": "B1+", "confidence": "low", "evidence": ["e"]},
        "confidence_1_5": 3,
    }
    return {**base, **overrides}


def outcome(
    run_id: str,
    *,
    fired: bool = True,
    valid_final: bool = True,
    valid_first: bool = True,
    said: tuple[int, int] = (1, 1),
) -> RunOutcome:
    return RunOutcome(
        run_id=run_id,
        tools_fired=fired,
        end_calls=1,
        valid_first=valid_first,
        valid_final=valid_final,
        said_pass=said[0],
        said_total=said[1],
        cefr="B1+",
        chunks_fabricated=0,
        final_arguments=None,
    )


def test_assign_gives_overlapping_records_to_the_later_started_run() -> None:
    runs = [run("r01", 0, 15), run("r02", 18, 33)]
    early, late = http_call(14, "end_session"), http_call(19, "get_profile")
    grouped = assign(runs, [early, late])
    assert grouped == {"r01": [early], "r02": [late]}


def test_assign_drops_records_outside_every_window() -> None:
    grouped = assign([run("r01", 0, 15)], [http_call(60, "get_profile")])
    assert grouped == {"r01": []}


def test_payload_valid_requires_schema_turns_and_issued_session(
    end_session_schema: dict[str, Any],
) -> None:
    assert payload_valid(args(), end_session_schema, {"sid-1"})
    assert not payload_valid(args(), end_session_schema, set())
    assert not payload_valid(args(user_turns=[]), end_session_schema, {"sid-1"})
    assert not payload_valid({**args(), "glossary": []}, end_session_schema, {"sid-1"})
    assert not payload_valid(args(task_result="done"), end_session_schema, {"sid-1"})
    assert not payload_valid(None, end_session_schema, {"sid-1"})


def test_score_run_valid_by_final_call_after_a_rejected_first_call(
    end_session_schema: dict[str, Any],
) -> None:
    records = [
        issued(1, "sid-1"),
        http_call(1, "get_profile", {}, result={"structuredContent": {}}),
        http_call(14, "end_session", {**args(), "glossary": []}, error={"code": -32602}),
        http_call(14.5, "end_session", args(), result={"structuredContent": {}}),
    ]
    result = score_run(run("r01", 0, 15), records, end_session_schema)
    assert (result.end_calls, result.valid_first, result.valid_final) == (2, False, True)
    assert (result.said_pass, result.said_total) == (1, 2)
    assert result.cefr == "B1+"
    assert result.tools_fired


def test_score_run_without_end_session_is_invalid(end_session_schema: dict[str, Any]) -> None:
    records = [issued(1, "sid-1"), http_call(1, "get_profile", {}, result={})]
    result = score_run(run("r01", 0, 15), records, end_session_schema)
    assert (result.end_calls, result.valid_final, result.said_total) == (0, False, 0)


def test_session_issued_to_the_other_account_does_not_count(
    end_session_schema: dict[str, Any],
) -> None:
    records = [issued(1, "sid-1", tester="author-pro"), http_call(14, "end_session", args())]
    result = score_run(run("r01", 0, 15, account="free"), records, end_session_schema)
    assert not result.valid_final


def test_non_empty_chunks_used_counts_as_fabrication(end_session_schema: dict[str, Any]) -> None:
    records = [issued(1, "sid-1"), http_call(14, "end_session", args(chunks_used=["c1", "c2"]))]
    result = score_run(run("r01", 0, 15), records, end_session_schema)
    assert result.chunks_fabricated == 2


def test_exp1_counts_voice_runs_meeting_all_three_conditions() -> None:
    runs = [
        run("v1", 0, 15, "voice", "free", "y", "y"),
        run("v2", 20, 35, "voice", "free", "y", "y"),
        run("v3", 40, 55, "voice", "free", "n", "y"),
        run("v4", 60, 75, "voice", "pro", "y", "y"),
        run("v5", 80, 95, "voice", "pro", "y", "n"),
        run("t1", 100, 115),
    ]
    outcomes = {r.run_id: outcome(r.run_id) for r in runs}
    summary = summarize_exp1(runs, outcomes)
    assert (summary.passes, summary.total, summary.verdict) == (3, 5, "FAIL")
    assert summary.by_account == {"free": (2, 3), "pro": (1, 2)}


def test_exp1_ignores_aborted_runs_and_waits_for_five() -> None:
    runs = [run("v1", 0, 15, "voice", stayed="y", continued="y", status="aborted")]
    summary = summarize_exp1(runs, {"v1": outcome("v1")})
    assert (summary.total, summary.verdict) == (0, "INCOMPLETE")


def test_exp2_pass_at_23_of_25() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    outcomes = {r.run_id: outcome(r.run_id, valid_final=i < 23) for i, r in enumerate(runs)}
    summary = summarize_exp2(runs, outcomes)
    assert (summary.n, summary.valid_final, summary.verdict) == (25, 23, "PASS")


def test_exp2_fallback_at_22_of_25_and_extension_below_70_percent() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    at_22 = {r.run_id: outcome(r.run_id, valid_final=i < 22) for i, r in enumerate(runs)}
    at_17 = {r.run_id: outcome(r.run_id, valid_final=i < 17) for i, r in enumerate(runs)}
    assert summarize_exp2(runs, at_22).verdict == "FAIL_WITH_FALLBACK"
    assert summarize_exp2(runs, at_17).verdict == "EXTEND"


def test_exp2_said_below_80_percent_fails_with_fallback() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    outcomes = {r.run_id: outcome(r.run_id, said=(3, 4)) for r in runs}
    assert summarize_exp2(runs, outcomes).verdict == "FAIL_WITH_FALLBACK"


def test_exp2_with_no_errors_reported_treats_said_as_not_applicable() -> None:
    runs = [run(f"r{i}", i * 20, i * 20 + 15) for i in range(25)]
    outcomes = {r.run_id: outcome(r.run_id, said=(0, 0)) for r in runs}
    summary = summarize_exp2(runs, outcomes)
    assert (summary.said_total, summary.verdict) == (0, "PASS")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --directory spike pytest tests/test_runs.py tests/test_scoring.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor_spike.analysis'`

- [ ] **Step 4: Implement**

`spike/src/tutor_spike/analysis/__init__.py`:

```python
"""Offline scoring of spike runs (spec §8). No LLM calls."""
```

`spike/src/tutor_spike/analysis/runs.py`:

```python
"""The author's run sheet (spec §6.2 plus continued_with_result)."""

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Run:
    run_id: str
    date: str
    account: str
    mode: str
    model_shown: str
    start: datetime
    end: datetime
    voice_stayed_active: str
    continued_with_result: str
    status: str
    transcript_file: str

    @property
    def tester(self) -> str:
        return f"author-{self.account}"


def load_runs(path: Path, tz: ZoneInfo) -> list[Run]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    runs: list[Run] = []
    for row in rows:
        day = date.fromisoformat(row["date"])
        start = datetime.combine(day, time.fromisoformat(row["start_local"]), tz)
        end = datetime.combine(day, time.fromisoformat(row["end_local"]), tz)
        if end < start:
            end += timedelta(days=1)
        runs.append(
            Run(
                run_id=row["run_id"],
                date=row["date"],
                account=row["account"],
                mode=row["mode"],
                model_shown=row["model_shown"],
                start=start.astimezone(UTC),
                end=end.astimezone(UTC),
                voice_stayed_active=row["voice_stayed_active"],
                continued_with_result=row["continued_with_result"],
                status=row["status"],
                transcript_file=row["transcript_file"],
            )
        )
    return runs
```

`spike/src/tutor_spike/analysis/scoring.py`:

```python
"""Experiment 01 and 02 scoring and verdicts (spec §8.1–8.3, §9.1–9.2)."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tutor_spike.analysis.runs import Run
from tutor_spike.normalize import said_in_turns

GRACE_BEFORE = timedelta(minutes=2)
GRACE_AFTER = timedelta(minutes=10)
VOICE_RUNS = 5
TEXT_AND_VOICE_RUNS = 25

Record = dict[str, Any]


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    tools_fired: bool
    end_calls: int
    valid_first: bool
    valid_final: bool
    said_total: int
    said_pass: int
    cefr: str | None
    chunks_fabricated: int
    final_arguments: Any


@dataclass(frozen=True)
class Exp1Summary:
    passes: int
    total: int
    by_account: dict[str, tuple[int, int]]
    verdict: str


@dataclass(frozen=True)
class Exp2Summary:
    n: int
    valid_final: int
    valid_first: int
    said_pass: int
    said_total: int
    verdict: str


def load_records(directory: Path) -> list[Record]:
    records: list[Record] = []
    for path in sorted(directory.glob("calls-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def _ts(record: Record) -> datetime:
    return datetime.fromisoformat(record.get("ts_in") or record["ts"])


def assign(runs: list[Run], records: list[Record]) -> dict[str, list[Record]]:
    """Each record goes to the latest-started run whose grace window contains it."""
    ordered = sorted(runs, key=lambda r: r.start)
    grouped: dict[str, list[Record]] = {r.run_id: [] for r in runs}
    for record in sorted(records, key=_ts):
        ts = _ts(record)
        started = [r for r in ordered if r.start - GRACE_BEFORE <= ts]
        if started and ts <= started[-1].end + GRACE_AFTER:
            grouped[started[-1].run_id].append(record)
    return grouped


def end_session_schema_from(records: list[Record]) -> dict[str, Any]:
    """inputSchema of end_session from the last logged tools/list response."""
    schema: dict[str, Any] | None = None
    for record in sorted(records, key=_ts):
        rpc = record.get("rpc") or {}
        if record.get("kind") == "http" and rpc.get("method") == "tools/list":
            for tool in (rpc.get("result_raw") or {}).get("tools", []):
                if tool.get("name") == "end_session":
                    schema = tool["inputSchema"]
    if schema is None:
        raise SystemExit("No tools/list response with end_session in the log.")
    return schema


def payload_valid(arguments: Any, schema: dict[str, Any], issued: set[str]) -> bool:
    if not isinstance(arguments, dict):
        return False
    if next(Draft202012Validator(schema).iter_errors(arguments), None) is not None:
        return False
    return bool(arguments.get("user_turns")) and arguments.get("session_id") in issued


def _tool_calls(records: list[Record]) -> list[Record]:
    calls = [
        r
        for r in records
        if r.get("kind") == "http" and (r.get("rpc") or {}).get("method") == "tools/call"
    ]
    return sorted(calls, key=_ts)


def _arguments(call: Record) -> Any:
    params = call["rpc"].get("params_raw") or {}
    return params.get("arguments") if isinstance(params, dict) else None


def score_run(run: Run, records: list[Record], schema: dict[str, Any]) -> RunOutcome:
    issued = {
        r["session_id"]
        for r in records
        if r.get("kind") == "tool"
        and r.get("tool") == "get_profile"
        and r.get("tester") == run.tester
    }
    calls = _tool_calls(records)
    ends = [_arguments(c) for c in calls if c["rpc"].get("tool") == "end_session"]
    validity = [payload_valid(a, schema, issued) for a in ends]
    final = ends[-1] if ends else None
    valid_final = bool(validity) and validity[-1]
    said_total = said_pass = chunks = 0
    cefr: str | None = None
    if valid_final:
        assert isinstance(final, dict)
        said_total = len(final["errors"])
        said_pass = sum(said_in_turns(e["said"], final["user_turns"]) for e in final["errors"])
        cefr = final["cefr_estimate"]["speaking"]
        chunks = len(final["chunks_used"])
    fired = any(
        c["http"]["status"] == 200
        and (c["rpc"].get("result_raw") is not None or c["rpc"].get("error_raw") is not None)
        for c in calls
    )
    return RunOutcome(
        run_id=run.run_id,
        tools_fired=fired,
        end_calls=len(ends),
        valid_first=bool(validity) and validity[0],
        valid_final=valid_final,
        said_total=said_total,
        said_pass=said_pass,
        cefr=cefr,
        chunks_fabricated=chunks,
        final_arguments=final,
    )


def voice_pass(run: Run, outcome: RunOutcome) -> bool:
    return (
        outcome.tools_fired and run.voice_stayed_active == "y" and run.continued_with_result == "y"
    )


def summarize_exp1(runs: list[Run], outcomes: dict[str, RunOutcome]) -> Exp1Summary:
    voice = sorted(
        (r for r in runs if r.mode == "voice" and r.status == "ok"), key=lambda r: r.start
    )[:VOICE_RUNS]
    by_account: dict[str, tuple[int, int]] = {}
    for r in voice:
        passed, total = by_account.get(r.account, (0, 0))
        by_account[r.account] = (passed + voice_pass(r, outcomes[r.run_id]), total + 1)
    passes = sum(p for p, _ in by_account.values())
    if len(voice) < VOICE_RUNS:
        verdict = "INCOMPLETE"
    else:
        verdict = "PASS" if passes >= 4 else "FAIL"
    return Exp1Summary(passes=passes, total=len(voice), by_account=by_account, verdict=verdict)


def summarize_exp2(runs: list[Run], outcomes: dict[str, RunOutcome]) -> Exp2Summary:
    ok = sorted((r for r in runs if r.status == "ok"), key=lambda r: r.start)
    ok = ok[:TEXT_AND_VOICE_RUNS]
    scored = [outcomes[r.run_id] for r in ok]
    n = len(scored)
    valid_final = sum(o.valid_final for o in scored)
    said_pass = sum(o.said_pass for o in scored)
    said_total = sum(o.said_total for o in scored)
    said_ok = said_total == 0 or said_pass / said_total >= 0.8
    if n < TEXT_AND_VOICE_RUNS:
        verdict = "INCOMPLETE"
    elif valid_final / n < 0.7:
        verdict = "EXTEND"
    elif valid_final / n >= 0.9 and said_ok:
        verdict = "PASS"
    else:
        verdict = "FAIL_WITH_FALLBACK"
    return Exp2Summary(
        n=n,
        valid_final=valid_final,
        valid_first=sum(o.valid_first for o in scored),
        said_pass=said_pass,
        said_total=said_total,
        verdict=verdict,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --directory spike pytest -q`
Expected: all passed.

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff format spike`, `uv run ruff check spike`, `uv run just check`
(If ruff flags the `assert isinstance` in `score_run` under `S101`, replace it with `if not isinstance(final, dict): raise TypeError("final payload is not an object")`.)

```bash
git add spike/src/tutor_spike/analysis spike/runs.template.csv spike/tests/test_runs.py spike/tests/test_scoring.py
git commit -m "feat(spike): run sheet loading and experiment 01/02 scoring"
```

---

### Task 7: Evidence fidelity, CEFR spread and the report CLI

**Files:**
- Create: `spike/src/tutor_spike/analysis/fidelity.py`, `spike/src/tutor_spike/analysis/__main__.py`
- Test: `spike/tests/test_fidelity.py`, `spike/tests/test_report.py`

**Interfaces:**
- Consumes: `normalize` (Task 1); `Run`, `load_runs`, `RunOutcome`, `load_records`, `assign`, `score_run`, `end_session_schema_from`, `summarize_exp1`, `summarize_exp2` (Task 6)
- Produces:
  - `user_messages(transcript: str) -> list[str]` (transcript format: lines starting `U:` or `A:`, continuation lines join the previous turn)
  - `Match(hit_ref: int, n_ref: int, hit_pred: int, n_pred: int)` with `recall` / `precision` properties (`None` when the denominator is 0) and `__add__`
  - `turns_fidelity(payload_turns, transcript_turns, fuzzy=False) -> Match`; `errors_fidelity(payload_said, annotated_said) -> Match`
  - `CefrStats(n, mode, low, high, sd_half_steps, within_one_of_mode, full_level_jumps)`, `cefr_stats(levels: list[str]) -> CefrStats | None`
  - `render_report(data_dir: Path, tz: ZoneInfo) -> str` and CLI `python -m tutor_spike.analysis --data data/raw --tz America/Mexico_City`

- [ ] **Step 1: Write the failing tests**

`spike/tests/test_fidelity.py`:

```python
import pytest

from tutor_spike.analysis.fidelity import (
    Match,
    cefr_stats,
    errors_fidelity,
    turns_fidelity,
    user_messages,
)

TRANSCRIPT = """U: Let's practise English for 15 minutes.
A: Sure! Which situation?
U: The standup, about the outage
that happened yesterday.
A: Great.
U: Yesterday I go to the office.
"""


def test_user_messages_joins_continuation_lines() -> None:
    assert user_messages(TRANSCRIPT) == [
        "Let's practise English for 15 minutes.",
        "The standup, about the outage that happened yesterday.",
        "Yesterday I go to the office.",
    ]


def test_turns_fidelity_counts_substring_matches_both_ways() -> None:
    payload = ["the standup about the outage", "Yesterday I go to the office", "I love Python"]
    match = turns_fidelity(payload, user_messages(TRANSCRIPT))
    assert (match.hit_ref, match.n_ref) == (2, 3)
    assert (match.hit_pred, match.n_pred) == (2, 3)


def test_fuzzy_turns_accept_near_copies() -> None:
    transcript = ["We need two more days for the release"]
    strict = turns_fidelity(["We need two more day for the release"], transcript)
    fuzzy = turns_fidelity(["We need two more day for the release"], transcript, fuzzy=True)
    assert strict.recall == 0.0
    assert fuzzy.recall == 1.0


def test_errors_fidelity_matches_when_one_said_contains_the_other() -> None:
    match = errors_fidelity(
        payload_said=["I go to the office", "invented"],
        annotated_said=["Yesterday I go to the office", "I have 30 years"],
    )
    assert (match.recall, match.precision) == (0.5, 0.5)


def test_match_is_none_when_there_is_nothing_to_compare() -> None:
    assert errors_fidelity([], []).recall is None
    assert errors_fidelity([], []).precision is None


def test_matches_add_up_as_micro_average() -> None:
    total = Match(1, 2, 1, 1) + Match(1, 2, 0, 1)
    assert (total.recall, total.precision) == (0.5, 0.5)


def test_cefr_stats_on_half_step_scale() -> None:
    stats = cefr_stats(["B1+", "B1+", "B2", "B1", "C1"])
    assert stats is not None
    assert (stats.n, stats.mode, stats.low, stats.high) == (5, "B1+", "B1", "C1")
    # values [1, 1, 2, 0, 4]: mode 1; |v-1| <= 1 for 4 of 5; jumps 2->0 and 0->4
    assert stats.within_one_of_mode == pytest.approx(0.8)
    assert stats.full_level_jumps == 2
    assert stats.sd_half_steps == pytest.approx(1.3565, abs=1e-3)


def test_cefr_stats_empty_is_none() -> None:
    assert cefr_stats([]) is None
```

`spike/tests/test_report.py`:

```python
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tutor_spike.analysis.__main__ import render_report

HEADER = (
    "run_id,date,account,device,app_version,mode,model_shown,situation_card,start_local,"
    "end_local,voice_stayed_active,continued_with_result,recording_file,transcript_file,"
    "status,abort_reason,notes\n"
)


def write_sample_data(tmp_path: Path, end_session_schema: dict[str, Any]) -> None:
    """One text run r01 (free) with transcript, annotation, schema and a valid payload."""
    (tmp_path / "runs.csv").write_text(
        HEADER + "r01,2026-10-06,free,laptop,web,text,Sonnet,3,15:00,15:15,na,na,,"
        "transcripts/r01.md,ok,,\n",
        encoding="utf-8",
    )
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "r01.md").write_text(
        "U: Yesterday I go to the office.\nA: Oh no.\n", encoding="utf-8"
    )
    (tmp_path / "annotations").mkdir()
    (tmp_path / "annotations" / "r01.json").write_text(
        json.dumps([{"said": "I go to the office", "correct": "I went to the office"}]),
        encoding="utf-8",
    )
    t = datetime(2026, 10, 6, 15, 1, tzinfo=UTC)
    arguments = {
        "session_id": "sid-1",
        "user_turns": ["Yesterday I go to the office."],
        "errors": [{"said": "I go to the office", "correct": "I went", "category": "grammar"}],
        "chunks_used": [],
        "task_result": "achieved",
        "hints_given": 0,
        "cefr_estimate": {"speaking": "B1+", "confidence": "low", "evidence": ["e"]},
        "confidence_1_5": 3,
    }
    tools_list = {"tools": [{"name": "end_session", "inputSchema": end_session_schema}]}
    lines = [
        {
            "kind": "http",
            "ts_in": t.isoformat(),
            "http": {"status": 200},
            "rpc": {"method": "tools/list", "result_raw": tools_list},
        },
        {
            "kind": "tool",
            "ts": t.isoformat(),
            "tool": "get_profile",
            "tester": "author-free",
            "session_id": "sid-1",
        },
        {
            "kind": "http",
            "ts_in": (t + timedelta(minutes=13)).isoformat(),
            "http": {"status": 200},
            "rpc": {
                "method": "tools/call",
                "tool": "end_session",
                "params_raw": {"name": "end_session", "arguments": arguments},
                "result_raw": {"structuredContent": {"accepted": True}},
                "error_raw": None,
            },
        },
    ]
    (tmp_path / "calls-2026-10-06.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )


def test_report_renders_every_section(tmp_path: Path, end_session_schema: dict[str, Any]) -> None:
    write_sample_data(tmp_path, end_session_schema)
    report = render_report(tmp_path, ZoneInfo("UTC"))
    for heading in ("## 01", "## 02", "## 03", "## 04"):
        assert heading in report
    assert "| r01 | 2026-10-06 | web / free / text | valid (final), 1 call(s), said 1/1 |" in report
    assert "INCOMPLETE" in report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --directory spike pytest tests/test_fidelity.py tests/test_report.py -q`
Expected: FAIL with `ModuleNotFoundError` for `tutor_spike.analysis.fidelity` and `tutor_spike.analysis.__main__`

- [ ] **Step 3: Implement fidelity**

`spike/src/tutor_spike/analysis/fidelity.py`:

```python
"""Evidence fidelity and grader spread (spec §8.4–8.5)."""

import statistics
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from tutor_spike.normalize import normalize

SCALE = {"B1": 0, "B1+": 1, "B2": 2, "B2+": 3, "C1": 4}
LEVELS = {value: name for name, value in SCALE.items()}


def user_messages(transcript: str) -> list[str]:
    turns: list[tuple[str, str]] = []
    for line in transcript.splitlines():
        if line.startswith(("U:", "A:")):
            turns.append((line[0], line[2:].strip()))
        elif line.strip() and turns:
            speaker, text = turns[-1]
            turns[-1] = (speaker, f"{text} {line.strip()}")
    return [text for speaker, text in turns if speaker == "U"]


@dataclass(frozen=True)
class Match:
    hit_ref: int
    n_ref: int
    hit_pred: int
    n_pred: int

    @property
    def recall(self) -> float | None:
        return self.hit_ref / self.n_ref if self.n_ref else None

    @property
    def precision(self) -> float | None:
        return self.hit_pred / self.n_pred if self.n_pred else None

    def __add__(self, other: "Match") -> "Match":
        return Match(
            self.hit_ref + other.hit_ref,
            self.n_ref + other.n_ref,
            self.hit_pred + other.hit_pred,
            self.n_pred + other.n_pred,
        )


def _turn_matches(payload_turn: str, transcript_turn: str, fuzzy: bool) -> bool:
    p, t = normalize(payload_turn), normalize(transcript_turn)
    if not p:
        return False
    if p in t:
        return True
    return fuzzy and SequenceMatcher(None, p, t).ratio() >= 0.9


def turns_fidelity(
    payload_turns: list[str], transcript_turns: list[str], fuzzy: bool = False
) -> Match:
    return Match(
        hit_ref=sum(
            any(_turn_matches(p, t, fuzzy) for p in payload_turns) for t in transcript_turns
        ),
        n_ref=len(transcript_turns),
        hit_pred=sum(
            any(_turn_matches(p, t, fuzzy) for t in transcript_turns) for p in payload_turns
        ),
        n_pred=len(payload_turns),
    )


def _said_matches(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na and nb) and (na in nb or nb in na)


def errors_fidelity(payload_said: list[str], annotated_said: list[str]) -> Match:
    return Match(
        hit_ref=sum(any(_said_matches(p, a) for p in payload_said) for a in annotated_said),
        n_ref=len(annotated_said),
        hit_pred=sum(any(_said_matches(p, a) for a in annotated_said) for p in payload_said),
        n_pred=len(payload_said),
    )


@dataclass(frozen=True)
class CefrStats:
    n: int
    mode: str
    low: str
    high: str
    sd_half_steps: float
    within_one_of_mode: float
    full_level_jumps: int


def cefr_stats(levels: list[str]) -> CefrStats | None:
    """Levels in chronological order; ties for the mode go to the lower level."""
    if not levels:
        return None
    values = [SCALE[level] for level in levels]
    counts = Counter(values)
    mode = min(counts, key=lambda v: (-counts[v], v))
    return CefrStats(
        n=len(values),
        mode=LEVELS[mode],
        low=LEVELS[min(values)],
        high=LEVELS[max(values)],
        sd_half_steps=statistics.pstdev(values),
        within_one_of_mode=sum(abs(v - mode) <= 1 for v in values) / len(values),
        full_level_jumps=sum(abs(b - a) >= 2 for a, b in zip(values, values[1:], strict=False)),
    )
```

- [ ] **Step 4: Implement the report CLI**

`spike/src/tutor_spike/analysis/__main__.py`:

```python
"""Print markdown tables for docs/spike/01–04 (spec §6.4). Usage:
(from spike/) uv run python -m tutor_spike.analysis --data data/raw --tz America/Mexico_City
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from tutor_spike.analysis.fidelity import (
    Match,
    cefr_stats,
    errors_fidelity,
    turns_fidelity,
    user_messages,
)
from tutor_spike.analysis.runs import Run, load_runs
from tutor_spike.analysis.scoring import (
    RunOutcome,
    assign,
    end_session_schema_from,
    load_records,
    score_run,
    summarize_exp1,
    summarize_exp2,
    voice_pass,
)

HEAD = "| Run | Date | Client / plan / mode | Outcome | Evidence (file, screenshot, log line) |"
RULE = "| --- | --- | --- | --- | --- |"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _client(run: Run) -> str:
    return f"{'mobile' if run.mode == 'voice' else 'web'} / {run.account} / {run.mode}"


def _exp2_cell(o: RunOutcome) -> str:
    validity = "valid (final)" if o.valid_final else "invalid"
    return f"{validity}, {o.end_calls} call(s), said {o.said_pass}/{o.said_total}"


def render_report(data_dir: Path, tz: ZoneInfo) -> str:
    runs = load_runs(data_dir / "runs.csv", tz)
    records = load_records(data_dir)
    schema = end_session_schema_from(records)
    grouped = assign(runs, records)
    outcomes = {r.run_id: score_run(r, grouped[r.run_id], schema) for r in runs}
    ok_runs = [r for r in runs if r.status == "ok"]
    out: list[str] = []

    exp1 = summarize_exp1(runs, outcomes)
    out += ["## 01 — Voice-mode tool calls", "", HEAD, RULE]
    for r in (r for r in runs if r.mode == "voice"):
        o = outcomes[r.run_id]
        result = "aborted" if r.status != "ok" else ("pass" if voice_pass(r, o) else "fail")
        evidence = f"tools fired: {o.tools_fired}; recording; log {r.date}"
        out.append(f"| {r.run_id} | {r.date} | {_client(r)} | {result} | {evidence} |")
    out += [
        "",
        f"Passes: {exp1.passes}/{exp1.total}; by account: {exp1.by_account}.",
        f"Verdict: {exp1.verdict}",
        "",
    ]

    exp2 = summarize_exp2(runs, outcomes)
    out += ["## 02 — end_session reliability", "", HEAD, RULE]
    for r in runs:
        cell = "aborted" if r.status != "ok" else _exp2_cell(outcomes[r.run_id])
        out.append(f"| {r.run_id} | {r.date} | {_client(r)} | {cell} | log {r.date} |")
    said_rate = exp2.said_pass / exp2.said_total if exp2.said_total else None
    out += [
        "",
        f"Valid by final call: {exp2.valid_final}/{exp2.n}; valid on first call: "
        f"{exp2.valid_first}/{exp2.n}; said passing: {exp2.said_pass}/{exp2.said_total} "
        f"({_pct(said_rate)}).",
        f"Verdict: {exp2.verdict}",
        "",
    ]

    strict, fuzzy, errors = Match(0, 0, 0, 0), Match(0, 0, 0, 0), Match(0, 0, 0, 0)
    out += ["## 03 — Evidence fidelity", "", HEAD, RULE]
    for r in ok_runs:
        o = outcomes[r.run_id]
        transcript = data_dir / r.transcript_file if r.transcript_file else None
        if not o.valid_final or transcript is None or not transcript.exists():
            continue
        messages = user_messages(transcript.read_text(encoding="utf-8"))
        payload = o.final_arguments
        s = turns_fidelity(payload["user_turns"], messages)
        f = turns_fidelity(payload["user_turns"], messages, fuzzy=True)
        strict, fuzzy = strict + s, fuzzy + f
        cell = f"turns R {_pct(s.recall)} / P {_pct(s.precision)}"
        annotation = data_dir / "annotations" / f"{r.run_id}.json"
        if annotation.exists():
            annotated = [e["said"] for e in json.loads(annotation.read_text(encoding="utf-8"))]
            e = errors_fidelity([x["said"] for x in payload["errors"]], annotated)
            errors = errors + e
            cell += f"; errors R {_pct(e.recall)} / P {_pct(e.precision)}"
        out.append(f"| {r.run_id} | {r.date} | {_client(r)} | {cell} | {r.transcript_file} |")
    fabricated = sum(outcomes[r.run_id].chunks_fabricated for r in ok_runs)
    out += [
        "",
        f"user_turns strict: R {_pct(strict.recall)} / P {_pct(strict.precision)} "
        "(thresholds ≥ 90% / ≥ 90%); "
        f"fuzzy: R {_pct(fuzzy.recall)} / P {_pct(fuzzy.precision)}.",
        f"errors: R {_pct(errors.recall)} / P {_pct(errors.precision)} (thresholds ≥ 70% / ≥ 80%).",
        f"chunks_used: not measurable; fabricated chunk IDs across runs: {fabricated}.",
        "",
    ]

    out += [
        "## 04 — Grader reliability",
        "",
        "| Group | n | Mode | Range | SD (half-steps) | Within ±1 of mode | Full-level jumps |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    groups: dict[str, list[str]] = defaultdict(list)
    for r in sorted(ok_runs, key=lambda r: r.start):
        level = outcomes[r.run_id].cefr
        if level is None:
            continue
        for key in ("all", f"account={r.account}", f"mode={r.mode}", f"model={r.model_shown}"):
            groups[key].append(level)
    for key, levels in groups.items():
        stats = cefr_stats(levels)
        if stats is None:
            continue
        out.append(
            f"| {key} | {stats.n} | {stats.mode} | {stats.low}–{stats.high} | "
            f"{stats.sd_half_steps:.2f} | {stats.within_one_of_mode:.0%} | "
            f"{stats.full_level_jumps} |"
        )
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--tz", default="America/Mexico_City")
    args = parser.parse_args()
    print(render_report(args.data, ZoneInfo(args.tz)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --directory spike pytest -q`
Expected: all passed (with the corrected CEFR assertions from Step 3).

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff format spike`, `uv run ruff check spike`, `uv run just check`

```bash
git add spike/src/tutor_spike/analysis spike/tests/test_fidelity.py spike/tests/test_report.py
git commit -m "feat(spike): evidence fidelity, CEFR spread and markdown report"
```

---

### Task 8: Redaction and evidence export

**Files:**
- Create: `spike/src/tutor_spike/redact.py`
- Test: `spike/tests/test_redact.py`

**Interfaces:**
- Consumes: `load_runs`, `load_records`, `assign`, `score_run`, `end_session_schema_from` (Task 6)
- Produces: `redact(text: str, names: list[str]) -> str`; `export(data_dir: Path, repo_root: Path, names: list[str], tz: ZoneInfo) -> list[Path]`; CLI `python -m tutor_spike.redact --names "Name1,Name2"`

Export layout (spec §6.4, `evals/README.md` naming `YYYY-MM-DD-claude-<plan>-NN`):
- `docs/spike/02-data/runs.csv` (redacted run sheet)
- per `ok` run with a valid final payload: `docs/spike/02-data/payloads/<run_id>.json`
- per text run with a transcript: `docs/spike/03-data/transcripts/<run_id>.md`, and its annotation (if any) to `docs/spike/03-data/annotations/<run_id>.json`
- the same transcript, annotation and payload to `evals/fixtures/transcripts/<basename>.md`, `evals/fixtures/annotations/<basename>.json`, `evals/fixtures/transcripts/<basename>.payload.json`

- [ ] **Step 1: Write the failing tests**

`spike/tests/test_redact.py`:

```python
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from test_report import write_sample_data
from tutor_spike.redact import export, redact


def test_redact_replaces_names_case_insensitively_and_emails() -> None:
    text = "Hi Jorge, mail jorge.x@gmail.com. JORGE said hi to Jorgensen."
    assert redact(text, ["Jorge"]) == "Hi [name], mail [email]. [name] said hi to Jorgensen."


def test_redact_handles_longer_names_first() -> None:
    assert redact("Ana Maria and Ana", ["Ana", "Ana Maria"]) == "[name] and [name]"


def test_export_writes_redacted_copies(tmp_path: Path, end_session_schema: dict[str, Any]) -> None:
    data = tmp_path / "data"
    data.mkdir()
    write_sample_data(data, end_session_schema)
    transcript = data / "transcripts" / "r01.md"
    transcript.write_text(
        transcript.read_text(encoding="utf-8") + "A: Bye Jorge.\n", encoding="utf-8"
    )
    repo = tmp_path / "repo"
    written = export(data, repo, ["Jorge"], ZoneInfo("UTC"))
    exported = repo / "docs/spike/03-data/transcripts/r01.md"
    assert exported in written
    assert "Jorge" not in exported.read_text(encoding="utf-8")
    fixture = repo / "evals/fixtures/transcripts/2026-10-06-claude-free-r01.md"
    assert fixture.exists()
    payload = json.loads((repo / "docs/spike/02-data/payloads/r01.json").read_text("utf-8"))
    assert payload["session_id"] == "sid-1"
    assert (repo / "evals/fixtures/annotations/2026-10-06-claude-free-r01.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --directory spike pytest tests/test_redact.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor_spike.redact'`

- [ ] **Step 3: Implement**

`spike/src/tutor_spike/redact.py`:

```python
"""Copy redacted spike evidence into docs/spike/NN-data and evals/fixtures (spec §6.4).
Usage (from spike/): uv run python -m tutor_spike.redact --names "Name1,Name2"
"""

import argparse
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from tutor_spike.analysis.runs import load_runs
from tutor_spike.analysis.scoring import (
    assign,
    end_session_schema_from,
    load_records,
    score_run,
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def redact(text: str, names: list[str]) -> str:
    text = _EMAIL.sub("[email]", text)
    for name in sorted((n.strip() for n in names if n.strip()), key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", "[name]", text, flags=re.IGNORECASE)
    return text


def _write(path: Path, text: str, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    written.append(path)


def export(data_dir: Path, repo_root: Path, names: list[str], tz: ZoneInfo) -> list[Path]:
    runs = load_runs(data_dir / "runs.csv", tz)
    records = load_records(data_dir)
    schema = end_session_schema_from(records)
    grouped = assign(runs, records)
    written: list[Path] = []
    spike_docs = repo_root / "docs" / "spike"
    fixtures = repo_root / "evals" / "fixtures"
    run_sheet = (data_dir / "runs.csv").read_text(encoding="utf-8")
    _write(spike_docs / "02-data" / "runs.csv", redact(run_sheet, names), written)
    for run in (r for r in runs if r.status == "ok"):
        outcome = score_run(run, grouped[run.run_id], schema)
        basename = f"{run.date}-claude-{run.account}-{run.run_id}"
        payload = None
        if outcome.valid_final:
            payload = redact(
                json.dumps(outcome.final_arguments, indent=2, ensure_ascii=False), names
            )
            _write(spike_docs / "02-data" / "payloads" / f"{run.run_id}.json", payload, written)
        transcript = data_dir / run.transcript_file if run.transcript_file else None
        if run.mode != "text" or transcript is None or not transcript.exists():
            continue
        text = redact(transcript.read_text(encoding="utf-8"), names)
        _write(spike_docs / "03-data" / "transcripts" / f"{run.run_id}.md", text, written)
        _write(fixtures / "transcripts" / f"{basename}.md", text, written)
        if payload is not None:
            _write(fixtures / "transcripts" / f"{basename}.payload.json", payload, written)
        annotation = data_dir / "annotations" / f"{run.run_id}.json"
        if annotation.exists():
            notes = redact(annotation.read_text(encoding="utf-8"), names)
            _write(spike_docs / "03-data" / "annotations" / f"{run.run_id}.json", notes, written)
            _write(fixtures / "annotations" / f"{basename}.json", notes, written)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--repo", type=Path, default=Path(".."))
    parser.add_argument("--names", required=True, help="Comma-separated names to redact")
    parser.add_argument("--tz", default="America/Mexico_City")
    args = parser.parse_args()
    for path in export(args.data, args.repo, args.names.split(","), ZoneInfo(args.tz)):
        print(path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --directory spike pytest -q`
Expected: all passed.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff format spike`, `uv run ruff check spike`, `uv run just check`

```bash
git add spike/src/tutor_spike/redact.py spike/tests/test_redact.py
git commit -m "feat(spike): redacted evidence export to docs/spike and evals fixtures"
```

---

### Task 9: Runbook, situation deck and review

**Files:**
- Create: `spike/README.md`, `spike/deck.md`

**Interfaces:**
- Consumes: every CLI from Tasks 5, 7, 8.

- [ ] **Step 1: Write the situation deck**

`spike/deck.md`:

```markdown
# Situation deck (spec §7)

Draw without replacement; reshuffle when empty. Record the card number in runs.csv.

| # | Type | Situation |
| --- | --- | --- |
| 1 | explain | Standup: explain last night's outage and what you are doing about it |
| 2 | explain | Explain a design decision to a product manager who is not technical |
| 3 | negotiate | Ask your lead to move a deadline by three days |
| 4 | negotiate | Agree with a client which features to cut from a release |
| 5 | disagree | Push back on a code review comment you think is wrong |
| 6 | disagree | Disagree with an architecture choice in a team meeting |
| 7 | ask for help | You are stuck on a bug; ask a senior engineer for help |
| 8 | ask for help | Ask the infrastructure team for production access |
| 9 | give feedback | Give feedback on a junior developer's pull request |
| 10 | give feedback | Give your manager feedback in a 1:1 |
| 11 | small talk | The first two minutes of a call with a US client |
| 12 | small talk | Coffee chat with a new teammate from the US |

Shuffled order for the week (generate once, paste here):
`uv run python -c "import random; print(random.sample(range(1, 13), 12))"` (from `spike/`)
```

- [ ] **Step 2: Write the runbook**

`spike/README.md`:

````markdown
# Spike runbook (throwaway)

Design: `docs/superpowers/specs/2026-10-03-spike-design.md`. Experiments: `docs/spike/01–04`.
This code is deleted at the go/no-go (ADR 0001). Raw data in `data/raw/` never leaves this machine.

## One-time setup

1. **Google Cloud** (console.cloud.google.com): new project → OAuth consent screen: External,
   Testing, scopes `openid` and `.../auth/userinfo.email`, test users = the two Google
   accounts used by the Free and Pro Claude accounts → Credentials → OAuth client ID, type
   Web application, authorized redirect URI `https://<spike-host>/auth/callback`.
2. **Cloudflare Tunnel** (PowerShell):
   ```
   winget install --id Cloudflare.cloudflared
   cloudflared tunnel login
   cloudflared tunnel create tutor-spike
   cloudflared tunnel route dns tutor-spike <spike-host>
   ```
   `%USERPROFILE%\.cloudflared\config.yml`:
   ```
   tunnel: tutor-spike
   credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
   ingress:
     - hostname: <spike-host>
       service: http://localhost:8765
     - service: http_status:404
   ```
   In the Cloudflare dashboard for `<spike-host>`: proxied DNS record, a Cache Rule
   "bypass cache", no Cloudflare Access application, and a WAF custom rule *Skip* (all
   remaining custom rules, Bot Fight Mode / Super Bot Fight Mode) for
   `ip.src in {160.79.104.0/21}`.
3. **Settings**: copy `env.example` to `.env` and fill it in (Claude never edits `.env`).
4. **Install**: from the repo root, `uv sync --directory spike`.

All commands below run from the `spike/` directory (`cd spike`), so `data/raw` and `.env`
resolve there.

## Start (every session day)

```
powercfg /change standby-timeout-ac 0
cloudflared tunnel run tutor-spike
uv run --env-file .env python -m tutor_spike.server
```

Check: `curl -s https://<spike-host>/.well-known/oauth-protected-resource/mcp` returns JSON
whose `resource` is exactly `https://<spike-host>/mcp`.

## Setup acceptance (once, before run 1)

- [ ] On claude.ai web, Free account: Settings → Connectors → Add custom connector →
      URL `https://<spike-host>/mcp` → sign in with the Free Google account.
- [ ] Same for the Pro account with the Pro Google account.
- [ ] In a text chat on each account: ask Claude to call `get_profile`; check a `kind: tool`
      line with the right tester label in `data/raw/calls-*.jsonl`.
- [ ] Restart the server; call `get_profile` again without reconnecting (persistence).
- [ ] Open the Claude app on the phone, confirm the connector is listed for both accounts.
- [ ] Delete the test lines: move `data/raw/` to `data/setup/` so runs start clean.
- [ ] Freeze: `git tag spike-instructions-v1` and record `git rev-parse --short HEAD` in the
      Setup of `docs/spike/01–04`.

## Pre-run checklist (every run)

- Laptop on mains power, sleep disabled; tunnel and server running; the curl check passes.
- Only this connector enabled in the chat; note the model the client shows.
- Voice runs: start the phone screen recording first.

## During and after a run (spec §7)

- New chat. Say only "Let's practise English for 15 minutes." Never mention tools or saving.
- Use the next card from `deck.md`. Speak naturally. Phone timer: 15 minutes.
- Close like a person: "OK, I have to go, thanks." No nudge.
- Add the row to `data/raw/runs.csv` (header in `runs.template.csv`).
- Copy the conversation from claude.ai web into `data/raw/transcripts/<run_id>.md`, one turn
  per block, user lines starting `U: ` and Claude lines starting `A: `; leave tool-call
  blocks out.

## Annotation (Sat Oct 10, before looking at payloads)

For each of 10 text runs (5 Free, 5 Pro) write `data/raw/annotations/<run_id>.json`:
`[{"said": "<exact words>", "correct": "<correct version>"}]`, from the transcript only.

## Analysis and export

```
uv run python -m tutor_spike.analysis --data data/raw
uv run python -m tutor_spike.redact --names "<your name>,<other names>"
```

Paste the tables into `docs/spike/01–04`, review the exported files for anything personal,
then commit them.

If rule 9.2's extension triggers, run the extension series with its own data directory
(`SPIKE_DATA_DIR=data/raw-extension`) so the first 25 runs stay unchanged.
````

- [ ] **Step 3: Review the spike diff**

Dispatch in parallel, each over `git diff main...HEAD -- spike/`:
- `mcp-contract-reviewer` (tools, schemas, descriptions, `response_rules`, instructions; note it is spike code under `spike/`, not `src/tutor/mcp`)
- `security-reviewer` (OAuth configuration, what is logged, secrets handling, public exposure through the tunnel)

Fix every confirmed finding with a test where one applies; re-run `uv run --directory spike pytest -q`.

- [ ] **Step 4: Full verification and commit**

Run: `uv run --directory spike pytest -q` → all pass.
Run: `uv run just check` → passes (root unaffected).

```bash
git add spike/README.md spike/deck.md
git commit -m "docs(spike): runbook, situation deck and run sheet instructions"
```

---

### Task 10: Setup acceptance with the author (manual)

Not code. The author and the agent go through `spike/README.md` "One-time setup" and "Setup acceptance" together on Mon Oct 5. The agent can run commands and read logs; the author does the console clicks, `.env`, and logins.

- [ ] **Step 1:** Google Cloud OAuth client and consent screen (author).
- [ ] **Step 2:** Cloudflare tunnel, DNS, cache bypass, WAF skip rule (author runs the commands; agent checks `curl` output).
- [ ] **Step 3:** `.env` filled (author); server starts; metadata `resource` matches exactly.
- [ ] **Step 4:** Both connectors added and authorized on claude.ai web; tool lines show `author-free` and `author-pro`.
- [ ] **Step 5:** Restart persistence check passes. If it fails, record it in `docs/spike/01` Setup and accept reconnecting after restarts.
- [ ] **Step 6:** Connector visible in the phone app for both accounts.
- [ ] **Step 7:** If any of Steps 3–6 fails with `GoogleProvider` after one debugging pass (superpowers:systematic-debugging), switch to `GitHubProvider`, then AuthKit (spec §4), and record the switch in `docs/spike/01–02` Setup.
- [ ] **Step 8:** Tag `spike-instructions-v1`; record the commit in the Setup sections of `docs/spike/01–04`; commit:

```bash
git add docs/spike
git commit -m "docs(spike): record server commit and setup acceptance"
```

## After this plan

The 25 runs, annotation, analysis, write-ups, `phase-auditor` and ADR 0001 follow the spec's schedule (§7) and decision rules (§9). They are the author's protocol, not implementation tasks.
