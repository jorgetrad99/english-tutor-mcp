"""FastMCP Google OAuth proxy for MCP clients (ADR 0002; plan rulings 1 and 8)."""

from __future__ import annotations

from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from tutor.settings import Settings

MCP_CALLBACK_PATH = "/oauth/callback"
CLAUDE_REDIRECT_URIS = ("https://claude.ai/api/mcp/auth_callback",)
REFRESH_TOKEN_SECONDS = 30 * 24 * 3600
ACCESS_TOKEN_SECONDS = 3600
SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email")


def oauth_storage(settings: Settings) -> AsyncKeyValue:
    """Encrypted file store on the OAuth volume. A wrong key reads as a miss: clients reconnect."""
    directory = settings.oauth_storage_dir.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)  # no-op on Windows
    files = FileTreeStore(
        data_directory=directory,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(directory),
    )
    return FernetEncryptionWrapper(
        key_value=files,
        fernet=Fernet(settings.oauth_storage_key.encode()),
        raise_on_decryption_error=False,
    )


def build_google_provider(
    settings: Settings, *, client_storage: AsyncKeyValue | None = None
) -> GoogleProvider:
    return GoogleProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        base_url=settings.base_url,
        redirect_path=MCP_CALLBACK_PATH,
        required_scopes=list(SCOPES),
        jwt_signing_key=settings.jwt_signing_key,
        client_storage=client_storage if client_storage is not None else oauth_storage(settings),
        allowed_client_redirect_uris=list(CLAUDE_REDIRECT_URIS),
        fallback_refresh_token_expiry_seconds=REFRESH_TOKEN_SECONDS,
        fastmcp_access_token_expiry_seconds=ACCESS_TOKEN_SECONDS,
        require_authorization_consent=True,
    )
