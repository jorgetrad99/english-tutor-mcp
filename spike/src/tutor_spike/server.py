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
        result = subprocess.run(
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
