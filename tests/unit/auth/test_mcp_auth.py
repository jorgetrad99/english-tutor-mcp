import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from key_value.aio.stores.memory import MemoryStore

from tutor.auth.mcp_auth import (
    ACCESS_TOKEN_SECONDS,
    CLAUDE_REDIRECT_URIS,
    MCP_CALLBACK_PATH,
    REFRESH_TOKEN_SECONDS,
    SCOPES,
    build_google_provider,
    oauth_storage,
)
from tutor.settings import Settings

pytestmark = pytest.mark.unit


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "TUTOR_BASE_URL": "https://tutor.example.com",
            "DATABASE_URL": "postgresql+psycopg://tutor@db/tutor",
            "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
            "TUTOR_JWT_SIGNING_KEY": "j" * 40,
            "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
            "TUTOR_OAUTH_STORAGE_DIR": str(tmp_path / "oauth"),
        }
    )


def test_provider_uses_the_v0_configuration(tmp_path: Path) -> None:
    provider = build_google_provider(settings(tmp_path), client_storage=MemoryStore())
    # Private attributes of the pinned fastmcp 4.0.11 OAuthProxy (proxy.py:491-558).
    assert provider._redirect_path == MCP_CALLBACK_PATH == "/oauth/callback"
    assert provider._fallback_refresh_token_expiry_seconds == REFRESH_TOKEN_SECONDS == 2_592_000
    assert provider._allowed_client_redirect_uris == list(CLAUDE_REDIRECT_URIS)
    assert provider._require_authorization_consent is True
    assert provider._fastmcp_access_token_expiry_seconds == ACCESS_TOKEN_SECONDS == 3600
    assert provider.required_scopes == list(SCOPES)
    assert str(provider.base_url).rstrip("/") == "https://tutor.example.com"


def test_provider_serves_the_callback_and_discovery_routes(tmp_path: Path) -> None:
    provider = build_google_provider(settings(tmp_path), client_storage=MemoryStore())
    paths = {getattr(route, "path", None) for route in provider.get_routes(mcp_path="/mcp")}
    assert {
        "/authorize",
        "/token",
        "/register",
        "/consent",
        "/oauth/callback",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource/mcp",
    } <= paths
    assert "/auth/callback" not in paths


@pytest.mark.asyncio
async def test_default_storage_is_encrypted_on_disk(tmp_path: Path) -> None:
    s = settings(tmp_path)
    store = oauth_storage(s)
    await store.put(key="client-1", value={"redirect": "visible-marker"}, collection="clients")
    assert await store.get(key="client-1", collection="clients") == {"redirect": "visible-marker"}
    files = [p for p in (tmp_path / "oauth").rglob("*") if p.is_file()]
    assert files
    assert all("visible-marker" not in p.read_text(encoding="utf-8") for p in files)


@pytest.mark.asyncio
async def test_storage_with_a_new_key_reads_as_a_miss(tmp_path: Path) -> None:
    s = settings(tmp_path)
    await oauth_storage(s).put(key="client-1", value={"a": 1}, collection="clients")
    rotated = Settings(**{**s.__dict__, "oauth_storage_key": Fernet.generate_key().decode()})
    assert await oauth_storage(rotated).get(key="client-1", collection="clients") is None


def test_provider_builds_default_storage_in_the_settings_dir(tmp_path: Path) -> None:
    build_google_provider(settings(tmp_path))
    assert (tmp_path / "oauth").is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes only")
def test_storage_directory_is_private(tmp_path: Path) -> None:
    (tmp_path / "oauth").mkdir(mode=0o755)
    oauth_storage(settings(tmp_path))
    assert (tmp_path / "oauth").stat().st_mode & 0o777 == 0o700
