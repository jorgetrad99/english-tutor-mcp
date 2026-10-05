"""`python -m tutor`: serve MCP, OAuth and the website with uvicorn."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from typing import Any

import uvicorn

from tutor.app import build_app
from tutor.settings import Settings


def run_kwargs(settings: Settings, env: Mapping[str, str]) -> dict[str, Any]:
    """No access log: it would print /oauth/callback?code=... and /authorize query strings."""
    allowed = env.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
    if settings.env == "prod" and "*" in allowed:
        raise SystemExit("FORWARDED_ALLOW_IPS must list the proxy addresses, not '*', in prod")
    return {
        "host": env.get("TUTOR_HOST", "127.0.0.1"),
        "port": settings.port,
        "proxy_headers": True,
        "forwarded_allow_ips": allowed,
        "server_header": False,
        "access_log": False,
    }


def configure_logging() -> None:
    """JSON lines from tutor.* loggers go to stdout as they are."""
    logger = logging.getLogger("tutor")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Authlib logs the PKCE code_verifier at DEBUG; keep that out of any handler.
    logging.getLogger("authlib").setLevel(logging.WARNING)


def main() -> None:
    settings = Settings.from_env(os.environ)
    configure_logging()
    uvicorn.run(build_app(settings), **run_kwargs(settings, os.environ))


if __name__ == "__main__":
    main()
