"""Process settings, read once from the environment (spec section 13: secrets come from env)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from cryptography.fernet import Fernet

Env = Literal["dev", "test", "prod"]
ENVS: tuple[Env, ...] = ("dev", "test", "prod")
REQUIRED = (
    "TUTOR_BASE_URL",
    "DATABASE_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "TUTOR_JWT_SIGNING_KEY",
    "TUTOR_OAUTH_STORAGE_KEY",
    "TUTOR_WEB_SESSION_SECRET",
)
MIN_SIGNING_KEY_CHARS = 32
MIN_WEB_SECRET_CHARS = 32
DEFAULT_OAUTH_STORAGE_DIR = "data/oauth"
DEFAULT_PORT = 8000


def _is_fernet_key(value: str) -> bool:
    try:
        Fernet(value.encode())
    except ValueError:
        return False
    return True


def _port(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    port = int(raw)
    return port if 1 <= port <= 65535 else None


@dataclass(frozen=True)
class Settings:
    env: Env
    base_url: str
    database_url: str = field(repr=False)
    google_client_id: str
    google_client_secret: str = field(repr=False)
    jwt_signing_key: str = field(repr=False)
    oauth_storage_key: str = field(repr=False)
    oauth_storage_dir: Path
    web_session_secret: str = field(repr=False)
    port: int = DEFAULT_PORT
    migration_database_url: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """Validate every key and exit naming the bad keys; values are never printed."""
        missing = [key for key in REQUIRED if not env.get(key, "").strip()]
        if missing:
            raise SystemExit(f"Missing settings: {', '.join(missing)}")
        problems: list[str] = []
        tutor_env = env.get("TUTOR_ENV", "dev").strip()
        if tutor_env not in ENVS:
            problems.append("TUTOR_ENV must be dev, test or prod")
        base_url = env["TUTOR_BASE_URL"].strip().rstrip("/")
        if not base_url.startswith(("https://", "http://")):
            problems.append("TUTOR_BASE_URL must be an http(s) URL")
        elif tutor_env == "prod" and not base_url.startswith("https://"):
            problems.append("TUTOR_BASE_URL must use https in prod")
        if len(env["TUTOR_JWT_SIGNING_KEY"]) < MIN_SIGNING_KEY_CHARS:
            problems.append(
                f"TUTOR_JWT_SIGNING_KEY must be at least {MIN_SIGNING_KEY_CHARS} characters"
            )
        if len(env["TUTOR_WEB_SESSION_SECRET"]) < MIN_WEB_SECRET_CHARS:
            problems.append(
                f"TUTOR_WEB_SESSION_SECRET must be at least {MIN_WEB_SECRET_CHARS} characters"
            )
        if not _is_fernet_key(env["TUTOR_OAUTH_STORAGE_KEY"].strip()):
            problems.append("TUTOR_OAUTH_STORAGE_KEY must be a Fernet key")
        port = _port(env.get("TUTOR_PORT", str(DEFAULT_PORT)).strip())
        if port is None:
            problems.append("TUTOR_PORT must be a number from 1 to 65535")
        if problems or port is None:
            raise SystemExit("Invalid settings: " + "; ".join(problems))
        return cls(
            env=cast(Env, tutor_env),
            base_url=base_url,
            database_url=env["DATABASE_URL"].strip(),
            google_client_id=env["GOOGLE_CLIENT_ID"].strip(),
            google_client_secret=env["GOOGLE_CLIENT_SECRET"].strip(),
            jwt_signing_key=env["TUTOR_JWT_SIGNING_KEY"],
            oauth_storage_key=env["TUTOR_OAUTH_STORAGE_KEY"].strip(),
            oauth_storage_dir=Path(
                env.get("TUTOR_OAUTH_STORAGE_DIR", "").strip() or DEFAULT_OAUTH_STORAGE_DIR
            ),
            web_session_secret=env["TUTOR_WEB_SESSION_SECRET"],
            port=port,
            migration_database_url=env.get("MIGRATION_DATABASE_URL", "").strip() or None,
        )
