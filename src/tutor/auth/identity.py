"""Resolve the MCP caller to a user id by Google `sub`, never by email (spec section 13)."""

from __future__ import annotations

from uuid import UUID

from tutor.services.context import Services
from tutor.services.ports import IdentityResolver


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def token_identity() -> tuple[str, str | None, str | None] | None:
    """(sub, email, name) of the current access token, or None without a usable token."""
    # Imported here so tests can patch fastmcp.server.dependencies.get_access_token.
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is None:
        return None
    sub = _text(token.claims.get("sub"))
    if sub is None:
        return None
    return sub, _text(token.claims.get("email")), _text(token.claims.get("name"))


def current_user_id(identity: IdentityResolver, svc: Services) -> UUID:
    """Find or create the caller's user; audit user_created and mcp_first_use once each."""
    found = token_identity()
    if found is None:
        raise PermissionError("no MCP access token")
    sub, email, name = found
    now = svc.clock()
    user = identity.resolve(sub, email, name, now)
    with svc.uow(user.id) as uow:
        if user.created:
            uow.audit.record("user_created", {"via": "mcp"}, now)
        if uow.users.note_mcp_use(now):
            uow.audit.record("mcp_first_use", {}, now)
    return user.id
