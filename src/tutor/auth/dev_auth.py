"""Dev-only bearer tokens for MCP clients (dev-environment design D2; CLAUDE.md: Context7 first).

With TUTOR_DEV_AUTH on, `build_app` verifies HS256 JWTs signed with TUTOR_JWT_SIGNING_KEY instead
of running the Google OAuth proxy, so MCP Inspector, Claude Code and Claude Desktop connect with a
plain `Authorization: Bearer` header and no Google round trip. Context7 on fastmcp 4.0.x:
`fastmcp.server.auth.providers.jwt.JWTVerifier(public_key=..., issuer=..., audience=...,
algorithm="HS256")`; despite its name, `public_key` takes the shared secret for HS* algorithms,
and a plain `TokenVerifier` serves no OAuth or discovery routes. Tokens are minted by
`tutor.ops.dev_token` for the two seeded identities only (`tutor.ops.dev_seed`).

fastmcp 4.0.11's `JWTVerifier.load_access_token` (read from the installed source) checks `exp`
only when the claim is present and never the lifetime or the subject, so `DevJWTVerifier` adds
those checks on top of it (`dev_claims_ok`).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard

from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.jwt import JWTVerifier

from tutor.settings import Settings
from tutor.web.config import MCP_PATH

DevSub = Literal["dev-ana", "dev-nuevo"]
DEV_SUBS: tuple[DevSub, ...] = ("dev-ana", "dev-nuevo")
ALGORITHM = "HS256"
MAX_TOKEN_HOURS = 24
MAX_TOKEN_SECONDS = MAX_TOKEN_HOURS * 3600


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


def _numeric_date(value: object) -> TypeGuard[float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def dev_claims_ok(claims: Mapping[str, Any], *, now: float) -> bool:
    """iat and exp both present, a lifetime of 24 hours at most, an expiry no later than 24 hours
    from now, and the subject one of the seeded dev identities."""
    iat, exp = claims.get("iat"), claims.get("exp")
    if _numeric_date(iat) and _numeric_date(exp):
        return (
            claims.get("sub") in DEV_SUBS
            and exp - iat <= MAX_TOKEN_SECONDS
            and exp <= now + MAX_TOKEN_SECONDS
        )
    return False


class DevJWTVerifier(JWTVerifier):
    """The fastmcp verifier (signature, algorithm, exp, issuer, audience), then `dev_claims_ok`.

    `verify_token` delegates to `load_access_token`, so both paths go through the dev checks."""

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = await super().load_access_token(token)
        if access is None or not dev_claims_ok(access.claims, now=time.time()):
            return None
        return access


def build_dev_verifier(settings: Settings) -> DevJWTVerifier:
    """HS256 on the signing key; issuer = base URL, audience = MCP URL; nothing else accepted."""
    if not settings.dev_auth:
        raise ValueError("the dev verifier needs TUTOR_DEV_AUTH (refused outside loopback dev)")
    return DevJWTVerifier(
        public_key=settings.jwt_signing_key,
        issuer=settings.base_url,
        audience=mcp_url(settings),
        algorithm=ALGORITHM,
    )
