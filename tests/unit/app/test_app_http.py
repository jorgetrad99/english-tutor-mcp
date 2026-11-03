import asyncio
import json
import logging
import re
import zoneinfo
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from key_value.aio.stores.memory import MemoryStore
from mcp_lesson import NOW

from tutor.app import (
    MAX_BODY_BYTES,
    MAX_CONCURRENT_UPLOADS,
    MAX_UPLOADS_PER_SESSION,
    BodySizeGuard,
    Message,
    PathDispatch,
    Receive,
    RequestLog,
    Scope,
    Send,
    UploadRule,
    body_limit,
    build_app,
    has_session_cookie,
    send_json,
    session_cookie_value,
)
from tutor.auth.mcp_auth import build_google_provider
from tutor.mcp.server import build_mcp
from tutor.services.context import Services
from tutor.services.memory import MemoryIdentity, memory_uow
from tutor.services.memory import MemoryStore as ServiceStore
from tutor.settings import Settings
from tutor.web.config import WebConfig
from tutor.web.profile import ServicesProfiles
from tutor.web.security import CSP
from tutor.web.subjects import ServicesSubjects

pytestmark = pytest.mark.unit

BASE_URL = "https://tutor.example.com"
WEB_CONFIG = WebConfig(
    env="test", base_url=BASE_URL, mcp_url=f"{BASE_URL}/mcp", support_email="soporte@example.test"
)
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0"},
    },
}


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "TUTOR_BASE_URL": BASE_URL,
            "DATABASE_URL": "sqlite://",
            "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
            "TUTOR_JWT_SIGNING_KEY": "j" * 40,
            "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
            "TUTOR_OAUTH_STORAGE_DIR": str(tmp_path / "oauth"),
        }
    )


def memory_app(tmp_path: Path) -> PathDispatch:
    """The production composition with memory services and a memory OAuth store."""
    store = ServiceStore()
    svc = Services(
        uow=memory_uow(store),
        clock=lambda: NOW,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    auth = build_google_provider(settings(tmp_path), client_storage=MemoryStore())
    mcp = build_mcp(svc, MemoryIdentity(store), auth=auth)
    return PathDispatch(RequestLog(BodySizeGuard(mcp.http_app(path="/mcp"))), web_app=None)


@asynccontextmanager
async def serving(
    app: Any, peer: tuple[str, int] = ("127.0.0.1", 123)
) -> AsyncIterator[httpx.AsyncClient]:
    """Run the ASGI lifespan through the dispatcher, as uvicorn does, then serve requests.
    `peer` is the client address; httpx's default is loopback, like the container probe."""
    inbox: asyncio.Queue[Message] = asyncio.Queue()
    outbox: asyncio.Queue[Message] = asyncio.Queue()
    task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, inbox.get, outbox.put)
    )
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    try:
        transport = httpx.ASGITransport(app=app, client=peer)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            yield client
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        assert (await outbox.get())["type"] == "lifespan.shutdown.complete"
        await task


@pytest.mark.asyncio
async def test_mcp_without_token_is_401_pointing_to_the_resource_metadata(
    tmp_path: Path,
) -> None:
    async with serving(memory_app(tmp_path)) as client:
        denied = await client.post("/mcp", json=INIT, headers=HEADERS)
        assert denied.status_code == 401
        match = re.search(r'resource_metadata="([^"]+)"', denied.headers["www-authenticate"])
        assert match
        assert match.group(1) == f"{BASE_URL}/.well-known/oauth-protected-resource/mcp"
        metadata = (await client.get(match.group(1))).json()
    assert metadata["resource"] == f"{BASE_URL}/mcp"
    assert metadata["authorization_servers"]


@pytest.mark.asyncio
async def test_authorization_server_metadata_is_at_the_root(tmp_path: Path) -> None:
    async with serving(memory_app(tmp_path)) as client:
        response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    body = response.json()
    assert body["authorization_endpoint"] == f"{BASE_URL}/authorize"
    assert body["code_challenge_methods_supported"] == ["S256"]


@pytest.mark.asyncio
async def test_proxy_callback_is_routed_to_mcp(tmp_path: Path) -> None:
    async with serving(memory_app(tmp_path)) as client:
        callback = await client.get("/oauth/callback")
        web = await client.get("/auth/callback")
    assert callback.status_code == 400  # the proxy handler, rejecting a missing code
    assert (web.status_code, web.json()) == (404, {"error": "not_found"})


@pytest.mark.asyncio
async def test_web_paths_are_404_until_the_web_app_is_mounted(tmp_path: Path) -> None:
    async with serving(memory_app(tmp_path)) as client:
        response = await client.get("/app/")
    assert (response.status_code, response.json()) == (404, {"error": "not_found"})


@pytest.mark.asyncio
async def test_oversized_mcp_post_is_413(tmp_path: Path) -> None:
    async with serving(memory_app(tmp_path)) as client:
        response = await client.post("/mcp", content=b"x" * 70_000, headers=HEADERS)
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_build_app_serves_mcp_and_the_website_with_separate_headers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="tutor.http")
    app = build_app(
        settings(tmp_path), engine=sqlalchemy.create_engine("sqlite://"), web_config=WEB_CONFIG
    )
    async with serving(app, peer=("203.0.113.9", 4000)) as client:
        denied = await client.post("/mcp", json=INIT, headers=HEADERS)
        metadata = await client.get("/.well-known/oauth-authorization-server")
        home = await client.get("/app/connect")
        login = await client.get("/login?next=%2Fapp%2Fsecret-next")
        await client.get("/oauth/callback?code=secret-code&state=s")
        big = await client.post(
            "/app/lang",
            content=b"x" * 70_000,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert denied.status_code == 401
    assert "content-security-policy" not in denied.headers
    assert "content-security-policy" not in metadata.headers
    assert (home.status_code, home.headers["location"]) == (303, "/login?next=%2Fapp%2Fconnect")
    assert login.status_code == 200
    assert login.headers["content-security-policy"] == CSP
    assert login.headers["cache-control"] == "no-store"
    assert big.status_code == 413
    assert (tmp_path / "oauth").is_dir()
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "tutor.http"]
    routes = [(line["route"], line["status"]) for line in lines]
    assert routes == [
        ("/.well-known/oauth-authorization-server", 200),
        ("/app/connect", 303),
        ("/login", 200),
        ("/oauth/callback", 400),
        ("unmatched", 413),
    ]
    for secret in ("secret-code", "secret-next", "code="):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_oauth_routes_log_one_line_without_the_query(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="tutor.http"):
        async with serving(memory_app(tmp_path)) as client:
            await client.get("/oauth/callback?code=SECRET-CODE&state=STATE-VALUE")
            await client.post("/mcp", json=INIT, headers=HEADERS)
    [record] = [r for r in caplog.records if r.name == "tutor.http"]
    line = json.loads(record.getMessage())
    assert set(line) == {"event", "route", "method", "status", "latency_ms", "user_hash"}
    assert (line["event"], line["route"], line["method"], line["status"]) == (
        "http_request",
        "/oauth/callback",
        "GET",
        400,
    )
    assert isinstance(line["latency_ms"], float)
    for secret in ("SECRET-CODE", "STATE-VALUE", "code="):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_a_failing_route_is_logged_as_500_and_re_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    scope = {"type": "http", "method": "GET", "path": "/token", "query_string": b"code=SECRET"}
    with caplog.at_level(logging.INFO, logger="tutor.http"), pytest.raises(RuntimeError):
        await RequestLog(broken)(scope, None, None)  # type: ignore[arg-type]
    assert json.loads(caplog.records[0].getMessage())["status"] == 500
    assert "SECRET" not in caplog.text
    assert "boom" not in caplog.text


def test_build_app_installs_the_profile_port(tmp_path: Path) -> None:
    app = build_app(
        settings(tmp_path), engine=sqlalchemy.create_engine("sqlite://"), web_config=WEB_CONFIG
    )
    logged = app.web_app
    assert isinstance(logged, RequestLog) and isinstance(logged.app, BodySizeGuard)
    web = logged.app.app
    assert isinstance(web, FastAPI)
    assert isinstance(web.state.profiles, ServicesProfiles)
    assert isinstance(web.state.subjects, ServicesSubjects)  # slice D ruling 50


# --- per-path body limits (slice D controller decision Q5, security review P2-2) -------------

SUBJECT = "0b5e7c1a-3f2d-4e6b-9a8c-1d2e3f4a5b6c"
PDF_LIMIT = 20 * 1024 * 1024 + MAX_BODY_BYTES
TEXT_LIMIT = 2 * 1024 * 1024
SESSION = {"cookie": "lang=es; __Host-tutor_session=opaque-value"}


async def read_body(scope: Scope, receive: Receive, send: Send) -> None:
    """An app that reads the whole body and answers its size."""
    size = 0
    while True:
        message = await receive()
        size += len(message.get("body", b""))
        if not message.get("more_body", False):
            break
    await send_json(send, 200, {"size": size})


async def post_sizes(
    path: str, *sizes: int, method: str = "POST", headers: dict[str, str] | None = None
) -> list[int]:
    transport = httpx.ASGITransport(app=BodySizeGuard(read_body))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        return [
            (await client.request(method, path, content=b"x" * size, headers=headers)).status_code
            for size in sizes
        ]


def test_the_upload_limits_are_a_fixed_table() -> None:
    for subject in (SUBJECT, SUBJECT.upper(), SUBJECT.replace("-", "")):  # UUIDConvertor forms
        assert body_limit("POST", f"/app/subjects/{subject}/pdf", session=True) == PDF_LIMIT
        assert body_limit("POST", f"/app/subjects/{subject}/text", session=True) == TEXT_LIMIT
    near_misses = [
        f"/app/subjects/{SUBJECT}/pdf/",
        f"/app/subjects/{SUBJECT}/pdfx",
        f"/app/subjects/{SUBJECT}/text/extra",
        f"/x/app/subjects/{SUBJECT}/pdf",
        f"/APP/subjects/{SUBJECT}/pdf",
        f"/app/subjects/{SUBJECT}/PDF",
        f"/app/subjects/{SUBJECT[:-1]}/pdf",
        f"/app/subjects/{{{SUBJECT}}}/pdf",  # braces: a UUID parser takes it, the table does not
        f"/app/subjects/urn:uuid:{SUBJECT}/pdf",
        "/app/subjects/------------------------------------/pdf",
        f"/app/subjects/{SUBJECT}/../{SUBJECT}/pdf",
        f"/app/notes/{SUBJECT}/pdf",
        f"/app/subjects/{SUBJECT}",
        "/mcp",
        "/app/lang",
    ]
    for path in near_misses:
        assert body_limit("POST", path, session=True) == MAX_BODY_BYTES, path
    for method in ("PUT", "PATCH", "DELETE", "GET"):
        assert body_limit(method, f"/app/subjects/{SUBJECT}/pdf", session=True) == MAX_BODY_BYTES
    # Without a session cookie the upload routes keep 64 KB too.
    assert body_limit("POST", f"/app/subjects/{SUBJECT}/pdf", session=False) == MAX_BODY_BYTES


def test_only_a_non_empty_session_cookie_counts() -> None:
    assert has_session_cookie(b"__Host-tutor_session=abc")
    assert has_session_cookie(b"lang=es;  __Host-tutor_session=abc ; other=1")
    for header in (
        None,
        b"",
        b"__Host-tutor_session=",
        b"__Host-tutor_session",
        b"tutor_session=abc",
        b"x__Host-tutor_session=abc",
        b"lang=__Host-tutor_session=abc",
    ):
        assert not has_session_cookie(header), header


@pytest.mark.asyncio
async def test_the_pdf_route_takes_20_mb_and_64_kb() -> None:
    path = f"/app/subjects/{SUBJECT}/pdf"
    assert await post_sizes(path, PDF_LIMIT, PDF_LIMIT + 1, headers=SESSION) == [200, 413]
    assert await post_sizes(path, 70_000, method="PUT", headers=SESSION) == [413]


@pytest.mark.asyncio
async def test_the_text_route_takes_2_mb() -> None:
    path = f"/app/subjects/{SUBJECT}/text"
    assert await post_sizes(path, TEXT_LIMIT, TEXT_LIMIT + 1, headers=SESSION) == [200, 413]


@pytest.mark.asyncio
async def test_an_upload_without_a_session_cookie_keeps_64_kb() -> None:
    for path in (f"/app/subjects/{SUBJECT}/pdf", f"/app/subjects/{SUBJECT}/text"):
        sizes = (MAX_BODY_BYTES, MAX_BODY_BYTES + 1)
        assert await post_sizes(path, *sizes) == [200, 413]
        empty = {"cookie": "__Host-tutor_session="}
        assert await post_sizes(path, *sizes, headers=empty) == [200, 413]


@pytest.mark.asyncio
async def test_every_other_path_keeps_64_kb() -> None:
    for path in (
        "/mcp",
        "/app/lang",
        f"/app/subjects/{SUBJECT}",
        f"/app/subjects/{SUBJECT}/pdf/",
        f"/app/templates/{SUBJECT}/retire",
    ):
        sizes = (MAX_BODY_BYTES, MAX_BODY_BYTES + 1)
        assert await post_sizes(path, *sizes, headers=SESSION) == [200, 413], path


def chunks_of(count: int, size: int) -> AsyncIterator[bytes]:
    async def generate() -> AsyncIterator[bytes]:
        for _ in range(count):
            yield b"x" * size

    return generate()


@pytest.mark.asyncio
async def test_an_upload_streams_to_the_app_without_buffering() -> None:
    seen: list[tuple[int, bool]] = []

    async def record(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            seen.append((len(message.get("body", b"")), message.get("more_body", False)))
            if not message.get("more_body", False):
                break
        await send_json(send, 200, {})

    transport = httpx.ASGITransport(app=BodySizeGuard(record))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await client.post(
            f"/app/subjects/{SUBJECT}/text", content=chunks_of(3, 100_000), headers=SESSION
        )
    assert response.status_code == 200
    # The chunks arrive as sent, not joined into one message.
    assert [size for size, _ in seen if size] == [100_000] * 3


@pytest.mark.asyncio
async def test_a_streamed_upload_over_the_limit_is_cut_and_413() -> None:
    received: list[str] = []

    async def keep_reading(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            received.append(message["type"])
            if message["type"] == "http.disconnect" or not message.get("more_body", False):
                break
        await send_json(send, 200, {"ok": True})  # dropped by the guard

    transport = httpx.ASGITransport(app=BodySizeGuard(keep_reading))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await client.post(
            f"/app/subjects/{SUBJECT}/text",
            content=chunks_of(3, TEXT_LIMIT // 2),
            headers=SESSION,
        )
    assert (response.status_code, response.json()) == (413, {"error": "payload_too_large"})
    assert received == ["http.request", "http.request", "http.disconnect"]


@pytest.mark.asyncio
async def test_an_app_that_raises_on_the_cut_body_still_gets_413() -> None:
    async def form_parser(scope: Scope, receive: Receive, send: Send) -> None:
        while (await receive())["type"] != "http.disconnect":
            pass
        raise RuntimeError("client disconnected")  # what Starlette's ClientDisconnect does

    transport = httpx.ASGITransport(app=BodySizeGuard(form_parser))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await client.post(
            f"/app/subjects/{SUBJECT}/text",
            content=chunks_of(3, TEXT_LIMIT // 2),
            headers=SESSION,
        )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_an_app_error_within_the_limit_is_not_swallowed() -> None:
    async def broken(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        raise RuntimeError("boom")

    guard = BodySizeGuard(broken)
    transport = httpx.ASGITransport(app=guard)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        with pytest.raises(RuntimeError, match="boom"):
            await client.post(f"/app/subjects/{SUBJECT}/text", content=b"x", headers=SESSION)
    assert guard._uploads == 0


@pytest.mark.asyncio
async def test_a_real_multipart_upload_over_the_limit_is_413() -> None:
    web = FastAPI()

    @web.post("/app/subjects/{subject_id}/text")
    async def upload(request: Request) -> dict[str, int]:
        form = await request.form()
        return {"fields": len(form)}

    transport = httpx.ASGITransport(app=BodySizeGuard(web))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        small = await client.post(
            f"/app/subjects/{SUBJECT}/text",
            files={"file": ("a.pdf", b"x" * 1000, "application/pdf")},
            headers=SESSION,
        )
        big = await client.post(
            f"/app/subjects/{SUBJECT}/text",
            files={"file": ("a.pdf", b"x" * (TEXT_LIMIT + 1), "application/pdf")},
            headers=SESSION,
        )
    assert (small.status_code, small.json()) == (200, {"fields": 1})
    assert big.status_code == 413


@pytest.mark.asyncio
async def test_more_uploads_at_once_than_the_cap_get_503() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    inside = 0

    async def slow(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal inside
        await receive()
        inside += 1
        if inside == MAX_CONCURRENT_UPLOADS:
            entered.set()
        await release.wait()
        await send_json(send, 200, {})

    guard = BodySizeGuard(slow)
    transport = httpx.ASGITransport(app=guard)
    path = f"/app/subjects/{SUBJECT}/pdf"
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        held = [
            asyncio.create_task(client.post(path, content=b"x", headers=session_of(f"s{n}")))
            for n in range(MAX_CONCURRENT_UPLOADS)
        ]
        await asyncio.wait_for(entered.wait(), 5)
        extra = await client.post(path, content=b"x", headers=session_of("another"))
        release.set()
        statuses = [(await task).status_code for task in held]
        after = await client.post(path, content=b"x", headers=SESSION)
    assert (extra.status_code, extra.json()) == (503, {"error": "busy"})
    assert statuses == [200] * MAX_CONCURRENT_UPLOADS
    assert after.status_code == 200
    assert guard._uploads == 0 and guard._per_session == {}


def session_of(value: str) -> dict[str, str]:
    return {"cookie": f"__Host-tutor_session={value}"}


@pytest.mark.asyncio
async def test_one_session_holds_at_most_two_upload_slots() -> None:
    # Security review P3: one learner cannot take every upload slot.
    entered = asyncio.Event()
    release = asyncio.Event()
    inside = 0

    async def slow(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal inside
        await receive()
        inside += 1
        if inside == MAX_UPLOADS_PER_SESSION:
            entered.set()
        await release.wait()
        await send_json(send, 200, {})

    guard = BodySizeGuard(slow)
    transport = httpx.ASGITransport(app=guard)
    path = f"/app/subjects/{SUBJECT}/text"
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        held = [
            asyncio.create_task(client.post(path, content=b"x", headers=SESSION))
            for _ in range(MAX_UPLOADS_PER_SESSION)
        ]
        await asyncio.wait_for(entered.wait(), 5)
        same = await client.post(path, content=b"x", headers=SESSION)
        assert len(guard._per_session) == 1
        assert "opaque-value" not in next(iter(guard._per_session))  # hashed, never raw
        other = asyncio.create_task(client.post(path, content=b"x", headers=session_of("b")))
        await asyncio.sleep(0.05)
        release.set()
        statuses = [(await task).status_code for task in held]
        assert (await other).status_code == 200
    assert (same.status_code, same.json()) == (503, {"error": "busy"})
    assert statuses == [200] * MAX_UPLOADS_PER_SESSION
    assert guard._uploads == 0 and guard._per_session == {}


def test_the_session_cookie_value_is_read_from_the_header() -> None:
    assert session_cookie_value(b"lang=es; __Host-tutor_session=abc ; x=1") == b"abc"
    assert session_cookie_value(b"__Host-tutor_session=") is None
    assert session_cookie_value(None) is None


QUICK_TEXT = UploadRule(re.compile(rf"/app/subjects/{SUBJECT}/text"), TEXT_LIMIT, 0.2)
UPLOAD_SCOPE: Scope = {
    "type": "http",
    "method": "POST",
    "path": f"/app/subjects/{SUBJECT}/text",
    "headers": [(b"cookie", b"__Host-tutor_session=slow")],
}


async def _drive_slow(receive: Receive) -> tuple[BodySizeGuard, list[str], list[Message]]:
    seen: list[str] = []
    sent: list[Message] = []

    async def reader(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            seen.append(message["type"])
            if message["type"] == "http.disconnect" or not message.get("more_body", False):
                break
        await send_json(send, 200, {"ok": True})  # dropped once the deadline passed

    async def send(message: Message) -> None:
        sent.append(message)

    guard = BodySizeGuard(reader, rules=(QUICK_TEXT,))
    await asyncio.wait_for(guard(UPLOAD_SCOPE, receive, send), 5)
    return guard, seen, sent


@pytest.mark.asyncio
async def test_a_stalled_upload_body_gets_408_at_the_deadline_and_frees_its_slot() -> None:
    calls = 0

    async def stalled() -> Message:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b"x", "more_body": True}
        await asyncio.sleep(10)  # the client sends nothing more
        return {"type": "http.request", "body": b"", "more_body": False}  # pragma: no cover

    guard, seen, sent = await _drive_slow(stalled)
    assert seen == ["http.request", "http.disconnect"]
    assert sent[0]["status"] == 408 and json.loads(sent[1]["body"]) == {"error": "timeout"}
    assert guard._uploads == 0 and guard._per_session == {}


@pytest.mark.asyncio
async def test_the_deadline_is_for_the_whole_body_not_each_chunk() -> None:
    async def dripping() -> Message:
        await asyncio.sleep(0.05)  # each chunk is quick, the body never ends
        return {"type": "http.request", "body": b"x", "more_body": True}

    guard, seen, sent = await _drive_slow(dripping)
    assert seen[-1] == "http.disconnect" and 3 <= len(seen) <= 6
    assert sent[0]["status"] == 408
    assert guard._uploads == 0


@pytest.mark.asyncio
async def test_a_body_in_time_is_not_cut_by_the_deadline() -> None:
    async def quick() -> Message:
        return {"type": "http.request", "body": b"x", "more_body": False}

    _guard, seen, sent = await _drive_slow(quick)
    assert seen == ["http.request"] and sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_build_app_keeps_64_kb_on_mcp_and_lets_an_upload_past_the_guard(
    tmp_path: Path,
) -> None:
    app = build_app(
        settings(tmp_path), engine=sqlalchemy.create_engine("sqlite://"), web_config=WEB_CONFIG
    )
    async with serving(app) as client:
        mcp = await client.post("/mcp", content=b"x" * 70_000, headers=HEADERS)
        upload = await client.post(
            f"/app/subjects/{SUBJECT}/text",
            content=b"x" * 1_000_000,
            headers={"content-type": "application/x-www-form-urlencoded", **SESSION},
        )
        anonymous = await client.post(
            f"/app/subjects/{SUBJECT}/text",
            content=b"x" * 1_000_000,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert mcp.status_code == 413
    assert upload.status_code != 413  # past the guard; the website answers (no route yet)
    assert anonymous.status_code == 413
