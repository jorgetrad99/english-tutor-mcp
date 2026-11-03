"""The template origin, its frame tokens and the raw instance document (spec 9 and 10,
binding (b) to (d); ADR 0005 decision 8; slice D Task 3 as amended by the controller)."""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import OctKey

from tutor.app import PathDispatch, build_app, request_host
from tutor.settings import Settings
from tutor.web.config import WebConfig
from tutor.web.frames import FRAME_TOKEN_SECONDS, FrameTokens, make_frame_url
from tutor.web.security import dashboard_csp, template_frame_csp
from tutor.web.template_origin import TemplateOriginApp, raw_html_response

pytestmark = pytest.mark.unit

BASE = "https://tutor.example.com"
ORIGIN = "https://templates.example.com"
KEY = "k" * 48
ANA, BETO = UUID(int=1), UUID(int=2)
ANA_INSTANCE, BETO_INSTANCE = uuid4(), uuid4()
DOCUMENT = '<!doctype html><html><head><meta charset="utf-8"></head><body>ñ é</body></html>'
RAW_HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "content-security-policy": template_frame_csp(BASE),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "private, no-store",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
}
DASHBOARD_ONLY = ("permissions-policy", "cross-origin-opener-policy", "set-cookie")


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def documents() -> Callable[[UUID, UUID], str | None]:
    """A stub of the subjects port's `instance_document`: Ana owns one instance, Beto one."""
    owned = {(ANA, ANA_INSTANCE), (BETO, BETO_INSTANCE)}
    return lambda user_id, iid: DOCUMENT if (user_id, iid) in owned else None


def raw(iid: UUID, token: str) -> str:
    return f"/app/instances/{iid}/raw?t={token}"


# --- frame tokens ----------------------------------------------------------------------------


def test_a_token_names_its_user_once_for_its_instance_only() -> None:
    tokens = FrameTokens(KEY, ORIGIN, clock=Clock())
    token = tokens.sign(ANA, ANA_INSTANCE)
    assert tokens.verify(token, BETO_INSTANCE) is None  # another instance
    assert tokens.verify(token, ANA_INSTANCE) == ANA
    assert tokens.verify(token, ANA_INSTANCE) is None  # single use


def test_expired_tokens_are_refused() -> None:
    clock = Clock()
    tokens = FrameTokens(KEY, ORIGIN, clock=clock)
    token = tokens.sign(ANA, ANA_INSTANCE)
    clock.now += FRAME_TOKEN_SECONDS + 1
    assert tokens.verify(token, ANA_INSTANCE) is None


def test_tokens_for_another_audience_or_key_or_tampered_are_refused() -> None:
    clock = Clock()
    tokens = FrameTokens(KEY, ORIGIN, clock=clock)
    other_aud = FrameTokens(KEY, "https://other.example.com", clock=clock)
    other_key = FrameTokens("x" * 48, ORIGIN, clock=clock)
    assert tokens.verify(other_aud.sign(ANA, ANA_INSTANCE), ANA_INSTANCE) is None
    assert tokens.verify(other_key.sign(ANA, ANA_INSTANCE), ANA_INSTANCE) is None
    head, body, sig = tokens.sign(ANA, ANA_INSTANCE).split(".")
    flipped = sig[:-2] + ("A" if sig[-2] != "A" else "B") + sig[-1]
    assert tokens.verify(f"{head}.{body}.{flipped}", ANA_INSTANCE) is None
    for junk in ("", "x", "a.b.c", "." * 2000):
        assert tokens.verify(junk, ANA_INSTANCE) is None


def _forge(claims: dict[str, object], alg: str = "HS256") -> str:
    return jwt.encode({"alg": alg}, claims, OctKey.import_key(KEY), algorithms=[alg])


def test_forged_claims_are_refused() -> None:
    clock = Clock()
    tokens = FrameTokens(KEY, ORIGIN, clock=clock)
    now = int(clock.now)
    good = {
        "aud": ORIGIN,
        "user_id": str(ANA),
        "instance_id": str(ANA_INSTANCE),
        "iat": now,
        "exp": now + 60,
        "jti": "j1",
    }
    assert tokens.verify(_forge(good), ANA_INSTANCE) == ANA
    bad: list[dict[str, object]] = [
        {**good, "jti": "j2", "exp": now + FRAME_TOKEN_SECONDS + 60},  # lives too long
        {**good, "jti": "j3", "iat": now + 600, "exp": now + 700},  # issued in the future
        {k: v for k, v in good.items() if k != "jti"},
        {**good, "jti": "j4", "user_id": "not-a-uuid"},
        {k: v for k, v in good.items() if k != "aud"} | {"jti": "j5"},
    ]
    for claims in bad:
        assert tokens.verify(_forge(claims), ANA_INSTANCE) is None
    assert tokens.verify(_forge({**good, "jti": "j6"}, alg="HS512"), ANA_INSTANCE) is None


def test_the_signer_refuses_weak_keys_and_long_lives() -> None:
    with pytest.raises(ValueError, match="32"):
        FrameTokens("short", ORIGIN)
    with pytest.raises(ValueError, match="300"):
        FrameTokens(KEY, ORIGIN, ttl_s=FRAME_TOKEN_SECONDS + 1)


def test_the_frame_url_is_on_the_template_origin_with_a_fresh_token() -> None:
    tokens = FrameTokens(KEY, ORIGIN, clock=Clock())
    url = make_frame_url(tokens, ORIGIN)(ANA, ANA_INSTANCE)
    prefix = f"{ORIGIN}/app/instances/{ANA_INSTANCE}/raw?t="
    assert url.startswith(prefix)
    assert tokens.verify(url.removeprefix(prefix), ANA_INSTANCE) == ANA
    assert make_frame_url(tokens, ORIGIN)(ANA, ANA_INSTANCE) != url


# --- the template origin app on its own -------------------------------------------------------


@pytest.fixture
def tokens() -> FrameTokens:
    return FrameTokens(KEY, ORIGIN, clock=Clock())


@pytest.fixture
def origin_client(tokens: FrameTokens) -> TestClient:
    return TestClient(TemplateOriginApp(tokens, documents(), BASE), base_url=ORIGIN)


def test_raw_html_response_names_its_charset_on_the_wire() -> None:
    client = TestClient(raw_html_response(DOCUMENT, BASE))  # a Response is an ASGI app
    response = client.get("/")
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.content == DOCUMENT.encode("utf-8")


def test_the_raw_document_has_exactly_the_template_header_set(
    origin_client: TestClient, tokens: FrameTokens
) -> None:
    response = origin_client.get(
        raw(ANA_INSTANCE, tokens.sign(ANA, ANA_INSTANCE)),
        headers={"cookie": "__Host-tutor_session=abc", "sec-fetch-dest": "document"},
    )
    assert response.status_code == 200
    assert response.text == DOCUMENT
    assert {k: response.headers[k] for k in RAW_HEADERS} == RAW_HEADERS
    assert set(response.headers) == {*RAW_HEADERS, "content-length"}
    for name in DASHBOARD_ONLY:
        assert name not in response.headers
    assert response.headers["content-security-policy"].startswith("sandbox allow-scripts;")
    assert (
        "frame-ancestors https://tutor.example.com" in response.headers["content-security-policy"]
    )


@pytest.mark.parametrize(
    "path",
    [
        "/app/instances/{iid}/raw/extra?t={t}",
        "/app/instances/{iid}/raw?t={t}&t={t}",
        "/app/instances/{iid}/raw?t={t}&x=1",
        "/app/instances/{iid}/raw",
        "/app/instances/{IID}/raw?t={t}",
        "/app/instances/x/raw?t={t}",
        "/app/instances/{iid}?t={t}",
        "/mcp?t={t}",
        "/login",
        "/",
    ],
)
def test_everything_else_on_the_template_origin_is_the_same_404(
    origin_client: TestClient, tokens: FrameTokens, path: str
) -> None:
    token = tokens.sign(ANA, ANA_INSTANCE)
    url = path.format(iid=ANA_INSTANCE, IID=str(ANA_INSTANCE).upper(), t=token)
    response = origin_client.get(url)
    assert (response.status_code, response.text) == (404, "Not found")
    assert response.headers["content-security-policy"] == template_frame_csp(BASE)
    assert "location" not in response.headers and "set-cookie" not in response.headers


def test_only_get_is_served(origin_client: TestClient, tokens: FrameTokens) -> None:
    for method in ("HEAD", "POST", "PUT", "DELETE", "OPTIONS"):
        token = tokens.sign(ANA, ANA_INSTANCE)
        assert origin_client.request(method, raw(ANA_INSTANCE, token)).status_code == 404


def test_a_valid_token_does_not_reach_another_users_instance(
    origin_client: TestClient, tokens: FrameTokens
) -> None:
    # Ana's token names Beto's instance: the document function re-checks ownership.
    assert origin_client.get(raw(BETO_INSTANCE, tokens.sign(ANA, BETO_INSTANCE))).status_code == 404
    # Ana's token for her instance, sent for Beto's: the instance claim does not match.
    assert origin_client.get(raw(BETO_INSTANCE, tokens.sign(ANA, ANA_INSTANCE))).status_code == 404


def test_reused_expired_and_foreign_tokens_are_404(tokens: FrameTokens) -> None:
    clock = Clock()
    tokens = FrameTokens(KEY, ORIGIN, clock=clock)
    client = TestClient(TemplateOriginApp(tokens, documents(), BASE), base_url=ORIGIN)
    token = tokens.sign(ANA, ANA_INSTANCE)
    assert client.get(raw(ANA_INSTANCE, token)).status_code == 200
    assert client.get(raw(ANA_INSTANCE, token)).status_code == 404  # used
    late = tokens.sign(ANA, ANA_INSTANCE)
    clock.now += FRAME_TOKEN_SECONDS + 1
    assert client.get(raw(ANA_INSTANCE, late)).status_code == 404
    other = FrameTokens(KEY, "https://other.example.com", clock=clock).sign(ANA, ANA_INSTANCE)
    assert client.get(raw(ANA_INSTANCE, other)).status_code == 404


def test_no_document_function_means_nothing_is_served(tokens: FrameTokens) -> None:
    client = TestClient(TemplateOriginApp(tokens, None, BASE), base_url=ORIGIN)
    assert client.get(raw(ANA_INSTANCE, tokens.sign(ANA, ANA_INSTANCE))).status_code == 404


def test_a_failing_document_is_a_bare_500_logged_by_class_only(
    tokens: FrameTokens, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(user_id: UUID, iid: UUID) -> str | None:
        raise RuntimeError("learner text inside")

    client = TestClient(TemplateOriginApp(tokens, broken, BASE), base_url=ORIGIN)
    with caplog.at_level(logging.ERROR, logger="tutor.web"):
        response = client.get(raw(ANA_INSTANCE, tokens.sign(ANA, ANA_INSTANCE)))
    assert (response.status_code, response.text) == (500, "Error")
    assert response.headers["content-security-policy"] == template_frame_csp(BASE)
    assert "RuntimeError" in caplog.text and "learner text" not in caplog.text


# --- host routing through build_app -----------------------------------------------------------


def _settings(tmp_path: Path, **extra: str) -> Settings:
    return Settings.from_env(
        {
            "TUTOR_BASE_URL": BASE,
            "DATABASE_URL": "sqlite://",
            "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
            "TUTOR_JWT_SIGNING_KEY": "j" * 40,
            "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
            "TUTOR_OAUTH_STORAGE_DIR": str(tmp_path / "oauth"),
            **extra,
        }
    )


def _web_config(origin: str | None) -> WebConfig:
    return WebConfig(
        env="test",
        base_url=BASE,
        mcp_url=f"{BASE}/mcp",
        support_email="soporte@example.test",
        template_origin=origin,
    )


def _framed_app(tmp_path: Path) -> PathDispatch:
    settings = _settings(tmp_path, TUTOR_TEMPLATE_ORIGIN=ORIGIN, TUTOR_FRAME_TOKEN_KEY=KEY)
    return build_app(
        settings,
        engine=sqlalchemy.create_engine("sqlite://"),
        web_config=_web_config(ORIGIN),
        instance_document=documents(),
    )


def test_build_app_serves_the_raw_document_only_on_the_template_host(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = _framed_app(tmp_path)
    assert app.frame_url is not None
    url = app.frame_url(ANA, ANA_INSTANCE)
    path = url.removeprefix(ORIGIN)
    token = path.split("?t=", 1)[1]
    templates = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    dashboard = TestClient(app, base_url=BASE, follow_redirects=False)
    with caplog.at_level(logging.INFO, logger="tutor.http"):
        on_dashboard = dashboard.get(path)
        served = templates.get(path)
    assert served.status_code == 200 and served.text == DOCUMENT
    assert {k: served.headers[k] for k in RAW_HEADERS} == RAW_HEADERS
    for name in DASHBOARD_ONLY:
        assert name not in served.headers
    # The dashboard host has no raw route at all: its own 404 page under the dashboard CSP.
    assert on_dashboard.status_code == 404
    assert on_dashboard.headers["content-security-policy"] == dashboard_csp(ORIGIN)
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "tutor.http"]
    assert [(line["route"], line["status"]) for line in lines] == [
        ("unmatched", 404),
        ("/app/instances/{instance_id}/raw", 200),
    ]
    assert token not in caplog.text


def test_the_template_host_answers_nothing_but_the_raw_route(tmp_path: Path) -> None:
    templates = TestClient(_framed_app(tmp_path), base_url=ORIGIN, follow_redirects=False)
    for path in ("/login", "/app/", "/.well-known/oauth-authorization-server", "/static/x"):
        response = templates.get(path)
        assert (response.status_code, response.text) == (404, "Not found"), path
    assert templates.post("/mcp", json={}).status_code == 404
    assert templates.post("/token", content=b"x" * 70_000).status_code == 413


def test_the_dashboard_csp_frames_exactly_the_template_origin(tmp_path: Path) -> None:
    dashboard = TestClient(_framed_app(tmp_path), base_url=BASE, follow_redirects=False)
    csp = dashboard.get("/login").headers["content-security-policy"]
    assert csp == dashboard_csp(ORIGIN)
    assert f"frame-src {ORIGIN};" in csp and "frame-ancestors 'none'" in csp


def test_without_a_template_origin_there_is_no_template_host(tmp_path: Path) -> None:
    app = build_app(
        _settings(tmp_path),
        engine=sqlalchemy.create_engine("sqlite://"),
        web_config=_web_config(None),
    )
    assert app.template_app is None and app.frame_url is None
    login = TestClient(app, base_url=BASE).get("/login")
    assert "frame-src 'none';" in login.headers["content-security-policy"]


def test_build_app_refuses_a_website_config_with_another_template_origin(tmp_path: Path) -> None:
    settings = _settings(tmp_path, TUTOR_TEMPLATE_ORIGIN=ORIGIN, TUTOR_FRAME_TOKEN_KEY=KEY)
    for config in (_web_config(None), _web_config("https://frames.example.com")):
        with pytest.raises(SystemExit, match="TUTOR_TEMPLATE_ORIGIN"):
            build_app(settings, engine=sqlalchemy.create_engine("sqlite://"), web_config=config)


@pytest.mark.parametrize(
    ("host", "name"),
    [
        (b"templates.example.com", "templates.example.com"),
        (b"Templates.Example.com:443", "templates.example.com"),
        (b"[::1]:8000", "[::1]"),
    ],
)
def test_request_host_drops_the_port_and_case(host: bytes, name: str) -> None:
    assert request_host({"headers": [(b"host", host)]}) == name
    assert request_host({"headers": []}) is None


def test_path_dispatch_needs_both_template_app_and_host() -> None:
    async def nothing(scope: object, receive: object, send: object) -> None:
        return None

    with pytest.raises(ValueError, match="together"):
        PathDispatch(nothing, None, template_app=nothing)
