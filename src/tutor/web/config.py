"""Web settings from environment variables. Secrets are read by the adapters, not here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlsplit

from tutor.settings import (
    dev_auth_allowed,
    dev_auth_flag,
    is_loopback,
    parse_base_url,
    template_origin_problem,
)

_ENVS = ("dev", "test", "prod")
MCP_PATH = "/mcp"


@dataclass(frozen=True)
class WebConfig:
    env: Literal["dev", "test", "prod"]
    base_url: str
    mcp_url: str
    support_email: str
    test_login: bool = False
    session_idle_days: int = 14
    session_max_days: int = 30
    # GET /auth/dev-login for the seeded dev identities (dev-environment design D5); the route is
    # not registered at all when this is off. Same guard as Settings.dev_auth.
    dev_auth: bool = False
    # The origin that serves template frames (Settings.template_origin); None: no frames, and
    # the dashboard CSP lets nothing be framed.
    template_origin: str | None = None

    def __post_init__(self) -> None:
        if self.env not in _ENVS:
            raise ValueError(f"TUTOR_ENV must be one of {_ENVS}")
        if self.test_login and self.env != "test":
            raise ValueError("test login is only allowed with TUTOR_ENV=test")
        if self.test_login and not is_loopback(urlsplit(self.base_url).hostname or ""):
            raise ValueError("test login is only allowed on a loopback TUTOR_BASE_URL")
        if self.dev_auth and not dev_auth_allowed(self.env, self.base_url):
            raise ValueError(
                "TUTOR_DEV_AUTH is only allowed with TUTOR_ENV=dev|test on a loopback "
                "TUTOR_BASE_URL"
            )
        if self.template_origin is not None:
            problem = template_origin_problem(self.template_origin, self.base_url)
            if problem:
                raise ValueError(problem)
        if self.mcp_url != self.base_url + MCP_PATH:
            raise ValueError(f"TUTOR_MCP_URL must equal TUTOR_BASE_URL followed by {MCP_PATH}")

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> WebConfig:
        """TUTOR_MCP_URL is derived from TUTOR_BASE_URL when unset; if set it must match."""
        env = environ.get("TUTOR_ENV", "dev").strip()
        if env not in _ENVS:
            raise ValueError(f"TUTOR_ENV must be one of {_ENVS}")
        base_url = parse_base_url(environ["TUTOR_BASE_URL"])
        if base_url is None:
            raise ValueError("TUTOR_BASE_URL must be an https origin (http only on loopback)")
        support_email = environ["TUTOR_SUPPORT_EMAIL"].strip()
        if not support_email:  # blank is missing: compose turns an unset variable into ""
            raise ValueError("Missing settings: TUTOR_SUPPORT_EMAIL")
        return cls(
            env=cast(Literal["dev", "test", "prod"], env),
            base_url=base_url,
            mcp_url=environ.get("TUTOR_MCP_URL", "").strip() or base_url + MCP_PATH,
            support_email=support_email,
            test_login=environ.get("TUTOR_TEST_LOGIN") == "1",
            dev_auth=dev_auth_flag(environ),
            template_origin=_template_origin(environ.get("TUTOR_TEMPLATE_ORIGIN", "")),
        )


def _template_origin(raw: str) -> str | None:
    """Blank is unset; anything else must be an origin (the full check is __post_init__)."""
    if not raw.strip():
        return None
    origin = parse_base_url(raw)
    if origin is None:
        raise ValueError("TUTOR_TEMPLATE_ORIGIN must be an https origin (http only on loopback)")
    return origin
