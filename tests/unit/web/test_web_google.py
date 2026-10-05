from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from tutor.web.google import GoogleOidcLogin, identity_from_claims
from tutor.web.ports import LoginFailed


def test_verified_claims_become_identity() -> None:
    ident = identity_from_claims(
        {"sub": "123", "email": "a@example.com", "email_verified": True, "name": "Ana"}
    )
    assert (ident.sub, ident.email, ident.name) == ("123", "a@example.com", "Ana")


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"sub": "1", "email": "a@example.com"},
        {"sub": "1", "email": "a@example.com", "email_verified": "true"},
        {"email": "a@example.com", "email_verified": True},
    ],
)
def test_missing_or_unverified_claims_fail(claims: dict[str, object]) -> None:
    with pytest.raises(LoginFailed):
        identity_from_claims(claims)


def test_authorize_redirect_carries_pkce_s256_and_nonce() -> None:
    login = GoogleOidcLogin("cid", "secret")
    # Preloaded metadata: no network access in a unit test.
    login.client.server_metadata.update(
        {
            "_loaded_at": 1,
            "authorization_endpoint": "https://accounts.example/auth",
            "token_endpoint": "https://accounts.example/token",
            "jwks_uri": "https://accounts.example/jwks",
        }
    )
    app = FastAPI()
    session: dict[str, object] = {}

    @app.middleware("http")
    async def attach_session(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.scope["session"] = session
        return await call_next(request)

    @app.get("/go")
    async def go(request: Request) -> Response:
        return await login.redirect(request, "https://testserver/auth/callback")

    response = TestClient(app, follow_redirects=False).get("/go")
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 43
    assert query["scope"] == ["openid email profile"]
    assert query["nonce"] and query["state"]
    (state,) = (v for k, v in session.items() if k.startswith("_state_google_"))
    assert state["data"]["code_verifier"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_identity_fails_closed_without_userinfo_or_on_any_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login = GoogleOidcLogin("cid", "secret")

    async def no_userinfo(request: object) -> dict[str, object]:
        return {"access_token": "t"}  # no id_token, so Authlib sets no "userinfo"

    async def boom(request: object) -> dict[str, object]:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(login.client, "authorize_access_token", no_userinfo)
    with pytest.raises(LoginFailed):
        await login.identity(object())  # type: ignore[arg-type]
    monkeypatch.setattr(login.client, "authorize_access_token", boom)
    with pytest.raises(LoginFailed) as info:
        await login.identity(object())  # type: ignore[arg-type]
    assert "secret" not in str(info.value)
