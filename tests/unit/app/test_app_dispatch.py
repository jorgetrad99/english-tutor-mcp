import json
from typing import Any

import pytest

from tutor.app import MCP_PATHS, BodySizeGuard, Message, PathDispatch, Scope

pytestmark = pytest.mark.unit


class Recorder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.seen: list[tuple[str, str]] = []
        self.bodies: list[bytes] = []

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        self.seen.append((scope["type"], scope.get("path", "")))
        if scope["type"] == "http":
            message = await receive()
            self.bodies.append(message.get("body", b""))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": self.name.encode()})


async def drive(app: Any, scope: Scope, body: bytes = b"", chunks: int = 1) -> list[Message]:
    sent: list[Message] = []
    size = max(1, len(body) // chunks)
    parts = [body[i : i + size] for i in range(0, len(body), size)] or [b""]
    queue = [
        {"type": "http.request", "body": p, "more_body": i < len(parts) - 1}
        for i, p in enumerate(parts)
    ]

    async def receive() -> Message:
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def http(path: str, method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {"type": "http", "method": method, "path": path, "headers": headers or []}


def test_mcp_paths_are_exactly_the_proxy_routes() -> None:
    assert (
        frozenset({"/mcp", "/authorize", "/token", "/register", "/consent", "/oauth/callback"})
        == MCP_PATHS
    )


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("/mcp", "mcp"),
        ("/authorize", "mcp"),
        ("/token", "mcp"),
        ("/register", "mcp"),
        ("/consent", "mcp"),
        ("/oauth/callback", "mcp"),
        ("/.well-known/oauth-protected-resource/mcp", "mcp"),
        ("/.well-known/oauth-authorization-server", "mcp"),
        ("/", "web"),
        ("/app/", "web"),
        ("/auth/callback", "web"),
        ("/login", "web"),
        ("/mcp/", "web"),
        ("/mcpx", "web"),
        ("/static/app.css", "web"),
    ],
)
@pytest.mark.asyncio
async def test_requests_are_routed_by_path(path: str, target: str) -> None:
    mcp, web = Recorder("mcp"), Recorder("web")
    sent = await drive(PathDispatch(mcp, web), http(path))
    assert sent[-1]["body"] == target.encode()
    assert (mcp if target == "mcp" else web).seen == [("http", path)]


@pytest.mark.asyncio
async def test_lifespan_goes_to_the_mcp_app() -> None:
    mcp, web = Recorder("mcp"), Recorder("web")
    await PathDispatch(mcp, web)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert (mcp.seen, web.seen) == ([("lifespan", "")], [])


@pytest.mark.asyncio
async def test_without_web_app_other_paths_are_404_json() -> None:
    sent = await drive(PathDispatch(Recorder("mcp"), None), http("/app/"))
    assert sent[0]["status"] == 404
    assert json.loads(sent[1]["body"]) == {"error": "not_found"}


@pytest.mark.asyncio
async def test_without_web_app_websockets_are_closed() -> None:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {"type": "websocket", "path": "/ws"}
    await PathDispatch(Recorder("mcp"), None)(scope, None, send)  # type: ignore[arg-type]
    assert sent == [{"type": "websocket.close", "code": 1000}]


@pytest.mark.parametrize(
    ("size", "chunks", "status"),
    [(65_536, 1, 200), (65_537, 1, 413), (70_000, 7, 413), (10, 1, 200)],
)
@pytest.mark.asyncio
async def test_post_bodies_over_64_kb_are_413(size: int, chunks: int, status: int) -> None:
    inner = Recorder("mcp")
    sent = await drive(BodySizeGuard(inner), http("/mcp", "POST"), b"x" * size, chunks)
    assert sent[0]["status"] == status
    if status == 200:
        assert inner.bodies == [b"x" * size]
    else:
        assert inner.seen == []
        assert json.loads(sent[1]["body"]) == {"error": "payload_too_large"}


@pytest.mark.parametrize("declared", [b"70000", b"abc"])
@pytest.mark.asyncio
async def test_declared_length_over_limit_is_413_without_reading(declared: bytes) -> None:
    inner = Recorder("mcp")
    scope = http("/mcp", "POST", [(b"content-length", declared)])
    sent = await drive(BodySizeGuard(inner), scope, b"x")
    assert (sent[0]["status"], inner.seen) == (413, [])


@pytest.mark.asyncio
async def test_get_requests_pass_the_guard_untouched() -> None:
    inner = Recorder("mcp")
    sent = await drive(BodySizeGuard(inner), http("/.well-known/oauth-authorization-server"))
    assert sent[0]["status"] == 200


def test_role_problems_name_each_unsafe_property() -> None:
    from tutor.db.engine import role_problems

    safe = {
        "superuser": False,
        "bypassrls": False,
        "reaches_privileged": False,
        "owned_tables": [],
        "in_tutor_app": True,
    }
    assert role_problems(**safe) == []  # type: ignore[arg-type]
    unsafe = {
        "superuser": True,
        "bypassrls": True,
        "reaches_privileged": True,
        "owned_tables": ["users"],
        "in_tutor_app": False,
    }
    assert len(role_problems(**unsafe)) == 5  # type: ignore[arg-type]
    for key, bad in unsafe.items():
        [problem] = role_problems(**{**safe, key: bad})  # type: ignore[arg-type]
        assert problem


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
@pytest.mark.asyncio
async def test_every_method_that_can_carry_a_body_is_capped(method: str) -> None:
    inner = Recorder("mcp")
    sent = await drive(BodySizeGuard(inner), http("/token", method), b"x" * 70_000, 3)
    assert (sent[0]["status"], inner.seen) == (413, [])


@pytest.mark.asyncio
async def test_a_disconnect_while_buffering_never_reaches_the_app() -> None:
    inner = Recorder("mcp")
    sent: list[Message] = []
    queue: list[Message] = [
        {"type": "http.request", "body": b"abc", "more_body": True},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return queue.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    await BodySizeGuard(inner)(http("/mcp", "POST"), receive, send)
    assert (sent, inner.seen) == ([], [])


def test_role_problems_flag_extra_memberships_and_owned_objects() -> None:
    from tutor.db.engine import role_problems

    base = {
        "superuser": False,
        "bypassrls": False,
        "reaches_privileged": False,
        "owned_tables": [],
        "in_tutor_app": True,
    }
    assert role_problems(**base) == []  # type: ignore[arg-type]
    assert len(role_problems(**base, other_memberships=["pg_monitor"])) == 1  # type: ignore[arg-type]
    assert len(role_problems(**base, owned_objects=["function"])) == 1  # type: ignore[arg-type]
