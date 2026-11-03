"""Dev bearer tokens replace the Google OAuth proxy when TUTOR_DEV_AUTH is on (design D2, D3)."""

import asyncio
import io
import json
import re
import warnings
import zoneinfo
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy
from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.jwt import JWTVerifier

from tutor.app import BodySizeGuard, Message, PathDispatch, RequestLog, build_app
from tutor.auth.dev_auth import DEV_IDENTITIES, DEV_SUBS, build_dev_verifier
from tutor.mcp.server import build_mcp
from tutor.ops import dev_token
from tutor.ops.dev_token import MAX_TOKEN_HOURS, mint_dev_token
from tutor.services.context import Services
from tutor.services.memory import MemoryIdentity, MemoryStore, memory_uow
from tutor.settings import Settings
from tutor.web.config import WebConfig

with warnings.catch_warnings():
    from authlib.deprecate import AuthlibDeprecationWarning  # installs its own "always" filter

    warnings.simplefilter("ignore", AuthlibDeprecationWarning)
    from authlib.jose import jwt as jose_jwt

pytestmark = pytest.mark.unit

BASE_URL = "http://localhost:8000"
MCP_URL = f"{BASE_URL}/mcp"
KEY = "k" * 40
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
WEB_CONFIG = WebConfig(
    env="dev", base_url=BASE_URL, mcp_url=MCP_URL, support_email="soporte@example.test"
)
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


def dev_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    return {
        "TUTOR_ENV": "dev",
        "TUTOR_DEV_AUTH": "1",
        "TUTOR_BASE_URL": BASE_URL,
        "DATABASE_URL": "sqlite://",
        "TUTOR_JWT_SIGNING_KEY": KEY,
        "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
        "TUTOR_OAUTH_STORAGE_DIR": str(tmp_path / "oauth"),
        **overrides,
    }


def dev_settings(tmp_path: Path, **overrides: str) -> Settings:
    return Settings.from_env(dev_env(tmp_path, **overrides))


GOOGLE = {
    "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
}


def google_env(tmp_path: Path) -> dict[str, str]:
    return {**dev_env(tmp_path, TUTOR_DEV_AUTH=""), **GOOGLE}


def google_settings(tmp_path: Path) -> Settings:
    return Settings.from_env(google_env(tmp_path))


def claims_of(token: str, key: str = KEY) -> dict[str, Any]:
    return dict(jose_jwt.decode(token, key))


def sign(claims: dict[str, Any], key: str = KEY, alg: str = "HS256") -> str:
    return jose_jwt.encode({"alg": alg}, claims, key).decode()


def base_claims(**overrides: Any) -> dict[str, Any]:
    issued = int(NOW.timestamp())
    return {
        "sub": "dev-ana",
        "email": "ana@dev.test",
        "name": "Ana",
        "iss": BASE_URL,
        "aud": MCP_URL,
        "iat": issued,
        "exp": issued + 3600,
        **overrides,
    }


class World:
    """The production MCP composition over memory services, behind the dev verifier."""

    def __init__(self, settings: Settings) -> None:
        self.store = MemoryStore()
        svc = Services(
            uow=memory_uow(self.store),
            clock=lambda: NOW,
            valid_timezones=frozenset(zoneinfo.available_timezones()),
        )
        mcp = build_mcp(svc, MemoryIdentity(self.store), auth=build_dev_verifier(settings))
        self.app = PathDispatch(RequestLog(BodySizeGuard(mcp.http_app(path="/mcp"))), web_app=None)

    def subs(self) -> set[str]:
        return {user.google_sub for user in self.store.tables.users.values()}


@asynccontextmanager
async def serving(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """Run the ASGI lifespan through the dispatcher, as uvicorn does, then serve requests."""
    inbox: asyncio.Queue[Message] = asyncio.Queue()
    outbox: asyncio.Queue[Message] = asyncio.Queue()
    task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, inbox.get, outbox.put)
    )
    await inbox.put({"type": "lifespan.startup"})
    assert (await outbox.get())["type"] == "lifespan.startup.complete"
    try:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            yield client
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        assert (await outbox.get())["type"] == "lifespan.shutdown.complete"
        await task


def _sse_result(body: str) -> dict[str, Any]:
    for line in body.splitlines():
        if line.startswith("data:"):
            message: dict[str, Any] = json.loads(line[5:].strip())
            return message
    raise AssertionError(f"no data line in {body!r}")


async def call_tool(client: httpx.AsyncClient, token: str, name: str) -> dict[str, Any]:
    """initialize, notifications/initialized and one tools/call over Streamable HTTP."""
    auth = {**HEADERS, "authorization": f"Bearer {token}"}
    init = await client.post("/mcp", json=INIT, headers=auth)
    assert init.status_code == 200, init.text
    session = {**auth, "mcp-session-id": init.headers["mcp-session-id"]}
    ready = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=session
    )
    assert ready.status_code == 202, ready.text
    called = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name}},
        headers=session,
    )
    assert called.status_code == 200, called.text
    return _sse_result(called.text)


# --- verifier and minting -----------------------------------------------------------------------


def test_verifier_is_hs256_on_the_signing_key_with_issuer_and_audience(tmp_path: Path) -> None:
    verifier = build_dev_verifier(dev_settings(tmp_path))
    assert isinstance(verifier, JWTVerifier)
    assert verifier.algorithm == "HS256"
    assert verifier.public_key == KEY
    assert (verifier.issuer, verifier.audience) == (BASE_URL, MCP_URL)
    assert verifier.get_routes(mcp_path="/mcp") == []  # no OAuth or discovery routes


def test_verifier_refuses_settings_without_the_flag(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="TUTOR_DEV_AUTH"):
        build_dev_verifier(google_settings(tmp_path))


def test_minted_token_carries_the_identity_issuer_audience_and_expiry(tmp_path: Path) -> None:
    token = mint_dev_token(dev_settings(tmp_path), "dev-ana", hours=3, now=NOW)
    claims = claims_of(token)
    assert claims["sub"] == "dev-ana"
    assert claims["email"] == DEV_IDENTITIES["dev-ana"].email
    assert claims["name"] == DEV_IDENTITIES["dev-ana"].name
    assert (claims["iss"], claims["aud"]) == (BASE_URL, MCP_URL)
    assert claims["iat"] == int(NOW.timestamp())
    assert claims["exp"] - claims["iat"] == 3 * 3600
    assert set(claims) == {"sub", "email", "name", "iss", "aud", "iat", "exp"}


@pytest.mark.parametrize("sub", DEV_SUBS)
@pytest.mark.asyncio
async def test_the_verifier_accepts_every_minted_identity(tmp_path: Path, sub: str) -> None:
    settings = dev_settings(tmp_path)
    token = mint_dev_token(settings, sub, now=datetime.now(UTC))  # type: ignore[arg-type]
    access = await build_dev_verifier(settings).verify_token(token)
    assert access is not None
    assert access.claims["sub"] == sub


def test_minting_refuses_bad_inputs(tmp_path: Path) -> None:
    settings = dev_settings(tmp_path)
    with pytest.raises(ValueError, match="TUTOR_DEV_AUTH"):
        mint_dev_token(google_settings(tmp_path), "dev-ana")
    with pytest.raises(ValueError, match="dev identity"):
        mint_dev_token(settings, "dev-mallory")  # type: ignore[arg-type]
    for hours in (0, MAX_TOKEN_HOURS + 1):
        with pytest.raises(ValueError, match="hours"):
            mint_dev_token(settings, "dev-ana", hours=hours)
    assert MAX_TOKEN_HOURS == 24


# --- the MCP endpoint ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_minted_token_calls_get_profile_and_resolves_dev_ana(tmp_path: Path) -> None:
    settings = dev_settings(tmp_path)
    world = World(settings)
    token = mint_dev_token(settings, "dev-ana", now=datetime.now(UTC))
    async with serving(world.app) as client:
        message = await call_tool(client, token, "get_profile")
    result = message["result"]
    assert not result.get("isError")
    assert result["structuredContent"]["onboarding_needed"] is True
    assert world.subs() == {"dev-ana"}


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("wrong audience", sign(base_claims(aud=f"{BASE_URL}/other"))),
        ("wrong issuer", sign(base_claims(iss="https://tutor.example.com"))),
        ("wrong key", sign(base_claims(), key="x" * 40)),
        ("expired", sign(base_claims(exp=int((NOW - timedelta(days=1)).timestamp())))),
        ("alg none", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJkZXYtYW5hIn0."),
        ("garbage", "not-a-token"),
    ],
)
@pytest.mark.asyncio
async def test_bad_tokens_are_401_and_create_no_user(
    tmp_path: Path, label: str, token: str
) -> None:
    world = World(dev_settings(tmp_path))
    async with serving(world.app) as client:
        denied = await client.post(
            "/mcp", json=INIT, headers={**HEADERS, "authorization": f"Bearer {token}"}
        )
        no_token = await client.post("/mcp", json=INIT, headers=HEADERS)
    assert denied.status_code == 401, label
    assert no_token.status_code == 401
    assert world.subs() == set()


@pytest.mark.asyncio
async def test_build_app_picks_the_dev_verifier_only_with_the_flag(tmp_path: Path) -> None:
    dev = build_app(
        dev_settings(tmp_path), engine=sqlalchemy.create_engine("sqlite://"), web_config=WEB_CONFIG
    )
    token = mint_dev_token(dev_settings(tmp_path), "dev-nuevo", now=datetime.now(UTC))
    async with serving(dev) as client:
        accepted = await client.post(
            "/mcp", json=INIT, headers={**HEADERS, "authorization": f"Bearer {token}"}
        )
        discovery = await client.get("/.well-known/oauth-authorization-server")
        login = await client.get("/login")
    assert accepted.status_code == 200
    assert discovery.status_code == 404  # no OAuth proxy, no discovery: bearer tokens only
    assert login.status_code == 200  # the website is still there
    assert not (tmp_path / "oauth").exists()  # the OAuth store is never touched

    google = build_app(
        google_settings(tmp_path),
        engine=sqlalchemy.create_engine("sqlite://"),
        web_config=WEB_CONFIG,
    )
    async with serving(google) as client:
        rejected = await client.post(
            "/mcp", json=INIT, headers={**HEADERS, "authorization": f"Bearer {token}"}
        )
        discovery = await client.get("/.well-known/oauth-authorization-server")
    assert rejected.status_code == 401
    assert discovery.status_code == 200


# --- the CLI ------------------------------------------------------------------------------------


def test_cli_refuses_without_the_flag(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    assert dev_token.main([], google_env(tmp_path), out=out, err=err) == 2
    assert out.getvalue() == ""
    assert "TUTOR_DEV_AUTH" in err.getvalue()
    assert KEY not in err.getvalue()


def test_cli_prints_the_url_the_token_and_the_three_client_snippets(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    argv = ["--sub", "dev-nuevo", "--hours", "2"]
    assert dev_token.main(argv, dev_env(tmp_path), out=out, err=err) == 0
    text = out.getvalue()
    assert err.getvalue() == ""
    assert f"MCP URL: {MCP_URL}" in text
    match = re.search(r"Token \(2 h\): (\S+)", text)
    assert match
    claims = claims_of(match.group(1))
    assert claims["sub"] == "dev-nuevo" and claims["exp"] - claims["iat"] == 2 * 3600
    token = match.group(1)
    assert (
        f"npx @modelcontextprotocol/inspector --cli {MCP_URL} --transport http "
        f'--header "Authorization: Bearer {token}" --method tools/list'
    ) in text
    assert (
        f"claude mcp add --transport http tutor-dev {MCP_URL} "
        f'--header "Authorization: Bearer {token}"'
    ) in text
    assert "claude_desktop_config.json" in text
    desktop_args = f'"mcp-remote", "{MCP_URL}", "--header", "Authorization:Bearer {token}"'
    assert desktop_args in text
    assert KEY not in text


def test_cli_defaults_to_dev_ana_for_twelve_hours(tmp_path: Path) -> None:
    out = io.StringIO()
    assert dev_token.main([], dev_env(tmp_path), out=out) == 0
    match = re.search(r"Token \(12 h\): (\S+)", out.getvalue())
    assert match
    assert claims_of(match.group(1))["sub"] == "dev-ana"


@pytest.mark.parametrize("argv", [["--hours", "25"], ["--hours", "0"], ["--sub", "dev-mallory"]])
def test_cli_rejects_bad_arguments(tmp_path: Path, argv: list[str]) -> None:
    out, err = io.StringIO(), io.StringIO()
    with pytest.raises(SystemExit) as info:
        dev_token.main(argv, dev_env(tmp_path), out=out, err=err)
    assert info.value.code == 2
    assert out.getvalue() == ""
