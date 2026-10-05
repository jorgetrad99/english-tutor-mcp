"""Google OIDC through Authlib. State, nonce and the PKCE verifier live in `request.session`,
which the server-side session middleware backs (no extra cookie)."""

from __future__ import annotations

import logging
from typing import Any

from authlib.integrations.starlette_client import OAuth, OAuthError
from starlette.requests import Request
from starlette.responses import Response

from tutor.web.ports import GoogleIdentity, LoginFailed

GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"
log = logging.getLogger("tutor.web")


class GoogleOidcLogin:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self._oauth = OAuth()
        self._oauth.register(
            "google",
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url=GOOGLE_METADATA,
            client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
        )

    @property
    def client(self) -> Any:
        return self._oauth.google

    async def redirect(self, request: Request, redirect_uri: str) -> Response:
        response: Response = await self.client.authorize_redirect(request, redirect_uri)
        return response

    async def identity(self, request: Request) -> GoogleIdentity:
        try:
            token = await self.client.authorize_access_token(request)
        except OAuthError as exc:
            raise LoginFailed(exc.error or "oauth_error") from None
        except Exception as exc:  # network, JWT or metadata failure: fail closed
            # Class name only: messages and tokens must never reach the logs.
            log.warning("google login failed exc=%s", type(exc).__name__)
            raise LoginFailed("oauth_error") from None
        return identity_from_claims(token.get("userinfo") or {})


def identity_from_claims(info: dict[str, Any]) -> GoogleIdentity:
    """Identity comes from the validated ID-token claims; users are keyed by `sub`."""
    sub, email = info.get("sub"), info.get("email")
    if not sub or not email or info.get("email_verified") is not True:
        raise LoginFailed("missing or unverified claims")
    return GoogleIdentity(str(sub), str(email), str(info.get("name") or ""), True)
