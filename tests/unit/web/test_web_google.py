import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.responses import Response

from tutor.web.google import GoogleOidcLogin, claims_options, identity_from_claims
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
        return await login.redirect(request, "https://localhost/auth/callback")

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


CLIENT_ID = "cid"


def _wire(login: GoogleOidcLogin, monkeypatch: pytest.MonkeyPatch, **claims: object) -> None:
    """Sign an ID token with a throwaway key and feed it to the real Authlib parsing."""
    key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "sub": "sub-1",
        "aud": CLIENT_ID,
        "exp": now + 300,
        "iat": now,
        "nonce": "n-1",
        "email": "a@example.com",
        "email_verified": True,
        **claims,
    }
    id_token = jwt.encode({"alg": "RS256", "kid": key.kid}, payload, key)

    async def fetch_access_token(**params: object) -> dict[str, object]:
        return {"access_token": "at", "id_token": id_token}

    async def fetch_jwk_set(force: bool = False) -> dict[str, object]:
        return {"keys": [key.as_dict(private=False)]}

    login.client.server_metadata.update({"_loaded_at": 1, "issuer": "https://accounts.google.com"})
    monkeypatch.setattr(login.client, "fetch_access_token", fetch_access_token)
    monkeypatch.setattr(login.client, "fetch_jwk_set", fetch_jwk_set)


def _callback_request() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "query_string": b"code=c&state=s",
        "headers": [],
        "session": {"_state_google_s": {"data": {"nonce": "n-1", "redirect_uri": "r"}, "exp": 9e9}},
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_valid_id_token_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    login = GoogleOidcLogin(CLIENT_ID, "secret")
    _wire(login, monkeypatch)
    assert (await login.identity(_callback_request())).sub == "sub-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"aud": ["other"], "azp": CLIENT_ID},
        {"aud": "other"},
        {"aud": [CLIENT_ID, "other"], "azp": "other"},
        {"iss": "https://evil.example"},
    ],
)
async def test_id_token_for_another_audience_or_issuer_is_rejected(
    monkeypatch: pytest.MonkeyPatch, claims: dict[str, object]
) -> None:
    login = GoogleOidcLogin(CLIENT_ID, "secret")
    _wire(login, monkeypatch, **claims)
    with pytest.raises(LoginFailed):
        await login.identity(_callback_request())


def test_claims_options_are_fresh_each_call() -> None:
    first = claims_options(CLIENT_ID)
    first.pop("aud")
    assert "aud" in claims_options(CLIENT_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"), [("access_denied", "access_denied"), ("<script>x</script>", "oauth_error")]
)
async def test_provider_errors_map_to_fixed_codes(
    monkeypatch: pytest.MonkeyPatch, error: str, code: str
) -> None:
    login = GoogleOidcLogin(CLIENT_ID, "secret")
    scope = {
        "type": "http",
        "method": "GET",
        "query_string": f"error={error}&error_description=evil".encode(),
        "headers": [],
        "session": {},
    }
    with pytest.raises(LoginFailed) as info:
        await login.identity(Request(scope))
    assert str(info.value) == code
