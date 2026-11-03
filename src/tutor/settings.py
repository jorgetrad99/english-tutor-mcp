"""Process settings, read once from the environment (spec section 13: secrets come from env)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

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
)
# Credentials of other roles (migration owner, gate report): the production app never holds them.
OWNER_ONLY_KEYS = ("MIGRATION_DATABASE_URL", "GATE_REPORT_DATABASE_URL")
# Keys that only the local dev profile sets; no production env file or compose file carries them
# (dev-environment design D1, D10). Without the flag the Google keys are required as before.
DEV_ONLY_KEYS = ("TUTOR_DEV_AUTH",)
GOOGLE_KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
DEV_AUTH_TRUE = frozenset({"1", "true", "yes"})
DEV_AUTH_GUARD = "TUTOR_DEV_AUTH requires TUTOR_ENV=dev|test and a loopback TUTOR_BASE_URL"
MIN_SIGNING_KEY_CHARS = 32
DEFAULT_OAUTH_STORAGE_DIR = "data/oauth"
DEFAULT_PORT = 8000


def _is_fernet_key(value: str) -> bool:
    try:
        Fernet(value.encode())
    except ValueError:
        return False
    return True


def parse_base_url(raw: str) -> str | None:
    """Normalised origin, or None. https unless the host is loopback."""
    try:
        parts = urlsplit(raw.strip())
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        return None
    if parts.scheme == "http" and not is_loopback(host):
        return None
    return f"{parts.scheme}://{parts.netloc}"


def is_loopback(host: str) -> bool:
    """localhost, a *.localhost name (RFC 6761) or a loopback IP address."""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def dev_auth_flag(env: Mapping[str, str]) -> bool:
    """TUTOR_DEV_AUTH is on for 1, true or yes (case-insensitive); anything else is off."""
    return env.get("TUTOR_DEV_AUTH", "").strip().lower() in DEV_AUTH_TRUE


def dev_auth_allowed(tutor_env: str, base_url: str) -> bool:
    """The guard shared with the website: dev auth only in dev or test on a loopback origin."""
    return tutor_env in ("dev", "test") and is_loopback(urlsplit(base_url).hostname or "")


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
    port: int = DEFAULT_PORT
    # Dev-only bearer tokens instead of the Google OAuth proxy (dev-environment design D1).
    dev_auth: bool = False

    def __post_init__(self) -> None:
        # The same guard as from_env and WebConfig, so a Settings built directly (or through
        # dataclasses.replace) cannot carry the flag outside loopback dev either.
        if self.dev_auth and not dev_auth_allowed(self.env, self.base_url):
            raise SystemExit(f"Invalid settings: {DEV_AUTH_GUARD}")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """Validate every key and exit naming the bad keys; values are never printed."""
        tutor_env = env.get("TUTOR_ENV", "dev").strip()
        raw_base_url = env.get("TUTOR_BASE_URL", "")
        base_url = parse_base_url(raw_base_url)
        dev_auth = dev_auth_flag(env)
        # The Google keys are optional only when the flag is on AND allowed: a refused flag
        # must not loosen anything.
        dev_auth_ok = dev_auth and base_url is not None and dev_auth_allowed(tutor_env, base_url)
        optional = GOOGLE_KEYS if dev_auth_ok else ()
        missing = [key for key in REQUIRED if key not in optional and not env.get(key, "").strip()]
        if missing:
            raise SystemExit(f"Missing settings: {', '.join(missing)}")
        problems: list[str] = []
        if tutor_env not in ENVS:
            problems.append("TUTOR_ENV must be dev, test or prod")
        if base_url is None:
            problems.append(
                "TUTOR_BASE_URL must be an https URL (http only for localhost) "
                "with no credentials, path, query or fragment"
            )
        if dev_auth and not dev_auth_ok:
            problems.append(DEV_AUTH_GUARD)
        jwt_key = env["TUTOR_JWT_SIGNING_KEY"].strip()
        storage_key = env["TUTOR_OAUTH_STORAGE_KEY"].strip()
        if len(jwt_key) < MIN_SIGNING_KEY_CHARS:
            problems.append(
                f"TUTOR_JWT_SIGNING_KEY must be at least {MIN_SIGNING_KEY_CHARS} characters"
            )
        if not _is_fernet_key(storage_key):
            problems.append("TUTOR_OAUTH_STORAGE_KEY must be a Fernet key")
        if jwt_key == storage_key:
            problems.append(
                "TUTOR_JWT_SIGNING_KEY, TUTOR_OAUTH_STORAGE_KEY must not share the same value"
            )
        if tutor_env == "prod" and not env["DATABASE_URL"].strip().startswith(
            ("postgresql:", "postgresql+", "postgres:")
        ):
            problems.append("DATABASE_URL must be a PostgreSQL URL when TUTOR_ENV is prod")
        if tutor_env == "prod":
            held = [k for k in OWNER_ONLY_KEYS if env.get(k, "").strip()]
            if held:
                problems.append(
                    f"{', '.join(held)} must not be set in the app env when TUTOR_ENV is prod"
                )
        port = _port(env.get("TUTOR_PORT", str(DEFAULT_PORT)).strip())
        if port is None:
            problems.append("TUTOR_PORT must be a number from 1 to 65535")
        if problems or port is None or base_url is None:
            raise SystemExit("Invalid settings: " + "; ".join(problems))
        return cls(
            env=cast(Env, tutor_env),
            base_url=base_url,
            database_url=env["DATABASE_URL"].strip(),
            google_client_id=env.get("GOOGLE_CLIENT_ID", "").strip(),
            google_client_secret=env.get("GOOGLE_CLIENT_SECRET", "").strip(),
            jwt_signing_key=jwt_key,
            oauth_storage_key=storage_key,
            oauth_storage_dir=Path(
                env.get("TUTOR_OAUTH_STORAGE_DIR", "").strip() or DEFAULT_OAUTH_STORAGE_DIR
            ),
            port=port,
            dev_auth=dev_auth,
        )
