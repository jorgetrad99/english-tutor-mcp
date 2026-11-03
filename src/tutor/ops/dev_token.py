"""`python -m tutor.ops.dev_token [--sub dev-ana] [--hours 12]`: mint a dev bearer token.

Dev-environment design D3. Prints the MCP URL, the token and ready-to-paste lines for MCP
Inspector, Claude Code and Claude Desktop. Refuses (exit 2) unless the environment carries
TUTOR_DEV_AUTH, which Settings only accepts in dev or test on a loopback TUTOR_BASE_URL. Tokens
live at most 24 hours. The signing key is never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TextIO

from tutor.auth.dev_auth import (
    ALGORITHM,
    DEV_IDENTITIES,
    DEV_SUBS,
    MAX_TOKEN_HOURS,
    DevSub,
    mcp_url,
)
from tutor.settings import Settings

with warnings.catch_warnings():
    # authlib 1.8 deprecates authlib.jose in favour of joserfc (it stays until authlib 2.0) and
    # installs an "always" filter for its warning at import; ours must go in front of that one.
    from authlib.deprecate import AuthlibDeprecationWarning

    warnings.simplefilter("ignore", AuthlibDeprecationWarning)
    from authlib.jose import jwt as jose_jwt

# The 24-hour cap (MAX_TOKEN_HOURS) lives in tutor.auth.dev_auth, whose verifier enforces it.
DEFAULT_TOKEN_HOURS = 12


def mint_dev_token(
    settings: Settings,
    sub: DevSub,
    hours: int = DEFAULT_TOKEN_HOURS,
    *,
    now: datetime | None = None,
) -> str:
    """An HS256 JWT for one seeded identity: sub, email, name, iss, aud, iat and exp."""
    if not settings.dev_auth:
        raise ValueError("dev tokens need TUTOR_DEV_AUTH (refused outside loopback dev)")
    if sub not in DEV_IDENTITIES:
        raise ValueError(f"unknown dev identity; use one of {', '.join(DEV_SUBS)}")
    if not 1 <= hours <= MAX_TOKEN_HOURS:
        raise ValueError(f"hours must be from 1 to {MAX_TOKEN_HOURS}")
    who = DEV_IDENTITIES[sub]
    issued = int((now if now is not None else datetime.now(UTC)).timestamp())
    claims = {
        "sub": who.sub,
        "email": who.email,
        "name": who.name,
        "iss": settings.base_url,
        "aud": mcp_url(settings),
        "iat": issued,
        "exp": issued + hours * 3600,
    }
    token: bytes = jose_jwt.encode({"alg": ALGORITHM}, claims, settings.jwt_signing_key)
    return token.decode()


def connection_notes(url: str, token: str) -> str:
    """The three client snippets of the design's section 3."""
    return "\n".join(
        [
            "MCP Inspector CLI (host Node):",
            f"  npx @modelcontextprotocol/inspector --cli {url} --transport http "
            f'--header "Authorization: Bearer {token}" --method tools/list',
            "",
            "Claude Code:",
            f"  claude mcp add --transport http tutor-dev {url} "
            f'--header "Authorization: Bearer {token}"',
            "",
            "Claude Desktop (claude_desktop_config.json, under mcpServers):",
            '  "tutor-dev": {"command": "npx", "args": ['
            f'"mcp-remote", "{url}", "--header", "Authorization:Bearer {token}"]}}',
        ]
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tutor.ops.dev_token", description=__doc__)
    parser.add_argument("--sub", choices=DEV_SUBS, default="dev-ana", help="seeded identity")
    parser.add_argument(
        "--hours",
        type=int,
        default=DEFAULT_TOKEN_HOURS,
        choices=range(1, MAX_TOKEN_HOURS + 1),
        metavar=f"1-{MAX_TOKEN_HOURS}",
        help=f"token lifetime (default {DEFAULT_TOKEN_HOURS})",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env(os.environ if env is None else env)
    if not settings.dev_auth:
        print(
            "dev tokens need TUTOR_DEV_AUTH=1 (only accepted with TUTOR_ENV=dev|test on a "
            "loopback TUTOR_BASE_URL); use `uv run just dev`",
            file=err,
        )
        return 2
    url = mcp_url(settings)
    token = mint_dev_token(settings, args.sub, args.hours)
    print(f"MCP URL: {url}", file=out)
    print(f"Token ({args.hours} h): {token}", file=out)
    print("", file=out)
    print(connection_notes(url, token), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
