"""Dev-only bearer tokens for MCP clients (dev-environment design D2; CLAUDE.md: Context7 first).

With TUTOR_DEV_AUTH on, `build_app` verifies HS256 JWTs signed with TUTOR_JWT_SIGNING_KEY instead
of running the Google OAuth proxy, so MCP Inspector, Claude Code and Claude Desktop connect with a
plain `Authorization: Bearer` header and no Google round trip. Context7 on fastmcp 4.0.x:
`fastmcp.server.auth.providers.jwt.JWTVerifier(public_key=..., issuer=..., audience=...,
algorithm="HS256")`; despite its name, `public_key` takes the shared secret for HS* algorithms,
and a plain `TokenVerifier` serves no OAuth or discovery routes. Tokens are minted by
`tutor.ops.dev_token` for the two seeded identities only (`tutor.ops.dev_seed`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from fastmcp.server.auth.providers.jwt import JWTVerifier

from tutor.settings import Settings
from tutor.web.config import MCP_PATH

DevSub = Literal["dev-ana", "dev-nuevo"]
DEV_SUBS: tuple[DevSub, ...] = ("dev-ana", "dev-nuevo")
ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class DevIdentity:
    sub: DevSub
    email: str
    name: str


# The only identities a dev token or the dev login can name (`as` and `--sub` are closed enums).
DEV_IDENTITIES: Mapping[DevSub, DevIdentity] = {
    "dev-ana": DevIdentity("dev-ana", "ana@dev.test", "Ana"),
    "dev-nuevo": DevIdentity("dev-nuevo", "nuevo@dev.test", "Nuevo"),
}


def mcp_url(settings: Settings) -> str:
    """The token audience: the MCP endpoint of this server."""
    return settings.base_url + MCP_PATH


def build_dev_verifier(settings: Settings) -> JWTVerifier:
    """HS256 on the signing key; issuer = base URL, audience = MCP URL; nothing else accepted."""
    if not settings.dev_auth:
        raise ValueError("the dev verifier needs TUTOR_DEV_AUTH (refused outside loopback dev)")
    return JWTVerifier(
        public_key=settings.jwt_signing_key,
        issuer=settings.base_url,
        audience=mcp_url(settings),
        algorithm=ALGORITHM,
    )
