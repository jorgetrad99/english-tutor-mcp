"""Web settings from environment variables. Secrets are read by the adapters, not here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from tutor.settings import _base_url

_ENVS = ("dev", "test", "prod")


@dataclass(frozen=True)
class WebConfig:
    env: Literal["dev", "test", "prod"]
    base_url: str
    mcp_url: str
    support_email: str
    test_login: bool = False
    session_idle_days: int = 14
    session_max_days: int = 30

    def __post_init__(self) -> None:
        if self.env not in _ENVS:
            raise ValueError(f"TUTOR_ENV must be one of {_ENVS}")
        if self.test_login and self.env != "test":
            raise ValueError("test login is only allowed with TUTOR_ENV=test")

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> WebConfig:
        env = environ.get("TUTOR_ENV", "dev")
        if env not in _ENVS:
            raise ValueError(f"TUTOR_ENV must be one of {_ENVS}")
        base_url = _base_url(environ["TUTOR_BASE_URL"])
        if base_url is None:
            raise ValueError("TUTOR_BASE_URL must be an https origin (http only on loopback)")
        return cls(
            env=cast(Literal["dev", "test", "prod"], env),
            base_url=base_url,
            mcp_url=environ["TUTOR_MCP_URL"],
            support_email=environ["TUTOR_SUPPORT_EMAIL"],
            test_login=environ.get("TUTOR_TEST_LOGIN") == "1",
        )
