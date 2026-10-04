"""Map the OAuth identity (Google email) to a tester label; logs keep only the label."""

from collections.abc import Callable


def parse_testers(raw: str) -> dict[str, str]:
    """'a@x.com=author-free,b@y.com=author-pro' -> {email (lowercased): label}."""
    testers: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        email, sep, label = entry.partition("=")
        if not sep or not email.strip() or not label.strip():
            raise ValueError(f"Bad SPIKE_TESTERS entry: {entry!r} (want email=label)")
        testers[email.strip().lower()] = label.strip()
    return testers


def token_tester(testers: dict[str, str]) -> Callable[[], str | None]:
    """Resolver for tools: the caller's tester label, or None if not verified and allowlisted."""

    def resolve() -> str | None:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
        if token is None:
            return None
        if str(token.claims.get("email_verified")).lower() != "true":
            return None
        return testers.get(str(token.claims.get("email", "")).lower())

    return resolve
