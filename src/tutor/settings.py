"""Process settings, read once from the environment (spec section 13: secrets come from env)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from tutor.web.security import dashboard_csp, template_frame_csp

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
# FORWARDED_ALLOW_IPS means a proxy (a tunnel) is in front: dev auth must not be reachable.
DEV_AUTH_PROXY_GUARD = "TUTOR_DEV_AUTH must not run behind a proxy: leave FORWARDED_ALLOW_IPS unset"
MIN_SIGNING_KEY_CHARS = 32
# The origin that serves composed templates to the website's frames, and the key of the signed
# frame tokens (spec 10, binding (c) and (d)). Optional: both set, or both empty and the website
# shows no frames.
TEMPLATE_ORIGIN_KEYS = ("TUTOR_TEMPLATE_ORIGIN", "TUTOR_FRAME_TOKEN_KEY")
_TOGETHER = "TUTOR_TEMPLATE_ORIGIN and TUTOR_FRAME_TOKEN_KEY must be set together or both empty"
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


def template_origin_problem(origin: str | None, base_url: str) -> str | None:
    """Why `origin` (already through parse_base_url) cannot serve templates to `base_url`.

    A distinct host from the website's (requests are routed by Host), the same scheme, and a
    website origin that `template_frame_csp` can name in `frame-ancestors`."""
    if origin is None:
        return "TUTOR_TEMPLATE_ORIGIN must be an https origin (http only on loopback)"
    template, website = urlsplit(origin), urlsplit(base_url)
    if template.scheme != website.scheme:
        return "TUTOR_TEMPLATE_ORIGIN must use the scheme of TUTOR_BASE_URL"
    if template.hostname == website.hostname:
        return "TUTOR_TEMPLATE_ORIGIN must have a host of its own, not the host of TUTOR_BASE_URL"
    try:
        dashboard_csp(origin)
    except ValueError:
        return "TUTOR_TEMPLATE_ORIGIN must be lower case, https or http on a *.localhost name"
    try:
        template_frame_csp(base_url)
    except ValueError:
        return "TUTOR_BASE_URL must be https or http://localhost to serve template frames"
    return None


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
    # Both or neither (TEMPLATE_ORIGIN_KEYS); None means the website renders no template frames.
    template_origin: str | None = None
    frame_token_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        # The same guard as from_env and WebConfig, so a Settings built directly (or through
        # dataclasses.replace) cannot carry the flag outside loopback dev either.
        if self.dev_auth and not dev_auth_allowed(self.env, self.base_url):
            raise SystemExit(f"Invalid settings: {DEV_AUTH_GUARD}")
        if (self.template_origin is None) != (self.frame_token_key is None):
            raise SystemExit(f"Invalid settings: {_TOGETHER}")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        """Validate every key and exit naming the bad keys; values are never printed."""
        tutor_env = env.get("TUTOR_ENV", "dev").strip()
        raw_base_url = env.get("TUTOR_BASE_URL", "")
        base_url = parse_base_url(raw_base_url)
        dev_auth = dev_auth_flag(env)
        loopback_dev = base_url is not None and dev_auth_allowed(tutor_env, base_url)
        # Read only under the flag (blank is unset, as compose turns an unset variable into "").
        behind_proxy = dev_auth and bool(env.get("FORWARDED_ALLOW_IPS", "").strip())
        # The Google keys are optional only when the flag is on AND allowed: a refused flag
        # must not loosen anything.
        dev_auth_ok = dev_auth and loopback_dev and not behind_proxy
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
        if dev_auth and not loopback_dev:
            problems.append(DEV_AUTH_GUARD)
        if behind_proxy:
            problems.append(DEV_AUTH_PROXY_GUARD)
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
        raw_origin = env.get("TUTOR_TEMPLATE_ORIGIN", "").strip()
        frame_key = env.get("TUTOR_FRAME_TOKEN_KEY", "").strip()
        template_origin: str | None = None
        if bool(raw_origin) != bool(frame_key):
            problems.append(_TOGETHER)
        elif raw_origin and base_url is not None:
            template_origin = parse_base_url(raw_origin)
            origin_problem = template_origin_problem(template_origin, base_url)
            if origin_problem:
                problems.append(origin_problem)
        if frame_key and len(frame_key) < MIN_SIGNING_KEY_CHARS:
            problems.append(
                f"TUTOR_FRAME_TOKEN_KEY must be at least {MIN_SIGNING_KEY_CHARS} characters"
            )
        if frame_key and frame_key in (jwt_key, storage_key):
            problems.append(
                "TUTOR_FRAME_TOKEN_KEY must not share a value with TUTOR_JWT_SIGNING_KEY or "
                "TUTOR_OAUTH_STORAGE_KEY"
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
            template_origin=template_origin,
            frame_token_key=frame_key or None,
        )
