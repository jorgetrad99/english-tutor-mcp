import asyncio
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tutor.domain.dashboard.types import Lang, Role, User
from tutor.web import i18n
from tutor.web.app import ErrorGuardMiddleware
from tutor.web.security import CSP, safe_next


def test_login_page_renders_in_spanish_with_security_headers(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert '<html lang="es-MX"' in response.text
    assert "Entrar con Google" in response.text
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "max-age=" in response.headers["strict-transport-security"]
    assert response.headers["cache-control"] == "no-store"


def test_login_page_in_english_by_query(client: TestClient) -> None:
    response = client.get("/login?lang=en")
    assert '<html lang="en"' in response.text and "Sign in with Google" in response.text
    assert 'href="/privacy?lang=en"' in response.text


def test_root_redirects_to_app(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 307 and response.headers["location"] == "/app/"


def test_privacy_and_terms_render(client: TestClient) -> None:
    privacy = client.get("/privacy")
    assert privacy.status_code == 200 and "soporte@example.test" in privacy.text
    assert client.get("/terms").status_code == 200


def test_unknown_page_uses_friendly_404(client: TestClient) -> None:
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "No encontramos esta p\N{LATIN SMALL LETTER A WITH ACUTE}gina" in response.text


def test_static_assets_are_immutable_when_versioned(client: TestClient) -> None:
    page = client.get("/login").text
    assert "/static/css/app.css?v=" in page
    href = page.split('href="/static/css/app.css?v=')[1].split('"')[0]
    response = client.get(f"/static/css/app.css?v={href}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_no_inline_scripts_or_styles(client: TestClient) -> None:
    page = client.get("/login").text
    assert "<script>" not in page and "style=" not in page and "<style" not in page


def test_day_filter_uses_learner_timezone(app: FastAPI) -> None:
    env = app.state.views.envs[Lang.EN]
    user = User(uuid4(), "Ana", Role.LEARNER, Lang.EN, "America/Mexico_City", False)
    late_evening = datetime(2027, 1, 12, 3, tzinfo=UTC)  # 21:00 on Jan 11 in CDMX
    assert env.from_string("{{ when|day }}").render(when=late_evening, user=user) == "Jan 11"
    assert env.from_string("{{ when|day }}").render(when=late_evening, user=None) == "Jan 12"


def test_safe_next_blocks_open_redirects() -> None:
    assert safe_next("/app/glossary?q=x") == "/app/glossary?q=x"
    for bad in (None, "", "https://evil.example", "//evil.example", "/\\evil", "app"):
        assert safe_next(bad) == "/app/"


def _boom_client(app: FastAPI, probe: str) -> TestClient:
    @app.get("/boom")
    async def boom() -> None:
        raise ValueError(probe)

    return TestClient(app, base_url="https://localhost", raise_server_exceptions=False)


def test_unhandled_error_is_guarded_headed_and_logged_safely(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    probe = "PROBE-learner-text-7f3a"
    caplog.set_level(logging.DEBUG)  # root logger: every logger is captured
    with _boom_client(app, probe) as c:
        response = c.get(f"/boom?q={probe}")
    assert response.status_code == 500
    assert "Algo sali\N{LATIN SMALL LETTER O WITH ACUTE} mal" in response.text
    assert probe not in response.text
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    ours = [r for r in caplog.records if r.name == "tutor.web"]
    assert len(ours) == 1
    assert ours[0].getMessage().endswith("route=/boom exc=ValueError")
    for record in caplog.records:
        if record.name.startswith("httpx"):  # the test client's own request line
            continue
        text = record.getMessage() + str(record.args) + (record.exc_text or "")
        assert probe not in text and "Traceback" not in text
        assert record.exc_info is None


def test_error_page_has_no_inline_code(app: FastAPI) -> None:
    with _boom_client(app, "x") as c:
        page = c.get("/boom").text
    for banned in ("<script>", "style=", "<style", "hx-on", " onclick=", " onerror=", " onload="):
        assert banned not in page


def test_language_link_never_leaves_the_origin(client: TestClient) -> None:
    for path in ("//evil.example/x", "/%5Cevil.example"):
        page = client.get(path).text
        hrefs = re.findall(r'href="([^"]*)"', page)
        assert hrefs
        for href in hrefs:
            assert not href.startswith(("//", "http", "\\")), href
    hostile = client.get("/login?next=//evil.example").text
    assert "evil.example" not in hostile
    ok = client.get("/login?next=/app/glossary%3Fq%3Dx").text
    assert 'href="?lang=en&amp;next=/app/glossary%3Fq%3Dx"' in ok


@pytest.mark.parametrize(
    "bad",
    [
        "/\t/evil.example",
        "/\n/evil.example",
        "/\r\\evil.example",
        "/\\evil",
        "https://evil",
        "//evil",
        "//[x",
        "",
    ],
)
def test_safe_next_rejects_control_and_scheme_tricks(bad: str) -> None:
    assert safe_next(bad) == "/app/"


def test_static_cache_policy_is_honest(client: TestClient) -> None:
    miss = client.get("/static/nope?v=1")
    assert miss.status_code == 404 and miss.headers["cache-control"] == "no-store"
    page = client.get("/login").text
    good = page.split('href="/static/css/app.css?v=')[1].split('"')[0]
    assert client.get("/static/css/app.css?v=deadbeef").headers["cache-control"] == "no-cache"
    assert client.get(f"/static/css/app.css?xv={good}").headers["cache-control"] == "no-cache"
    assert client.get("/static/css/app.css").headers["cache-control"] == "no-cache"


def test_cross_origin_headers_and_trusted_host(client: TestClient) -> None:
    ok = client.get("/login")
    assert ok.headers["cross-origin-opener-policy"] == "same-origin"
    assert ok.headers["cross-origin-resource-policy"] == "same-origin"
    forged = client.get("/", headers={"host": "evil.example"})
    assert forged.status_code == 400 and "location" not in forged.headers


def test_http_error_keeps_headers_and_validation_error_does_not_echo(app: FastAPI) -> None:
    @app.get("/needs-int")
    async def needs_int(n: int) -> None:
        return None

    with TestClient(app, base_url="https://localhost", follow_redirects=False) as c:
        not_allowed = c.post("/login")
        assert not_allowed.status_code == 405 and "GET" in not_allowed.headers["allow"]
        invalid = c.get("/needs-int?n=SECRET-input")
        assert invalid.status_code == 422 and "SECRET-input" not in invalid.text
        assert invalid.headers["content-security-policy"] == CSP


def test_missing_translations_detects_untranslated_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "x.html").write_text(
        '<p>{{ _("Texto sin traducir ' + "zzz" + '") }}</p>', encoding="utf-8"
    )
    monkeypatch.setattr(i18n, "TEMPLATES_DIR", templates)
    assert i18n.missing_translations() == ["Texto sin traducir zzz"]


def test_safe_next_survives_unparseable_input(client: TestClient) -> None:
    assert safe_next("//[x") == "/app/"
    assert client.get("/login?next=//[x").status_code == 200
    assert client.get("/login?next=/a[b").status_code == 200


async def _run_asgi(app: ASGIApp) -> list[Message]:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "raw_path": b"/x",
        "query_string": b"",
        "headers": [],
        "state": {},
    }
    await app(scope, receive, send)
    return sent


def test_error_guard_after_response_started_sends_nothing_more(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def half_sent(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise ValueError("PROBE-late")

    caplog.set_level(logging.DEBUG)
    sent = asyncio.run(_run_asgi(ErrorGuardMiddleware(half_sent)))
    assert [m["type"] for m in sent] == ["http.response.start"]
    assert sent[0]["status"] == 200
    assert "PROBE-late" not in caplog.text


def test_error_guard_suppresses_a_failing_error_page_without_chaining(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(scope: Scope, receive: Receive, send: Send) -> None:
        raise ValueError("PROBE-original")

    async def run() -> None:
        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def dead_send(message: Message) -> None:
            raise OSError("PROBE-disconnected")

        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/x",
            "raw_path": b"/x",
            "query_string": b"",
            "headers": [],
            "app": app,
            "state": {},
        }
        await ErrorGuardMiddleware(broken)(scope, receive, dead_send)

    caplog.set_level(logging.DEBUG)
    asyncio.run(run())  # must not raise
    assert "PROBE" not in caplog.text
    assert "error page not sent" in caplog.text


def test_safe_next_is_bounded() -> None:
    assert safe_next("/" + "a" * 511) == "/" + "a" * 511
    assert safe_next("/" + "a" * 512) == "/app/"


def test_htmx_config_pins_every_safety_key(client: TestClient) -> None:
    import json

    html = client.get("/login").text
    raw = re.search(r"<meta name=\"htmx-config\" content='([^']+)'>", html)
    assert raw
    config = json.loads(raw.group(1))
    assert config["allowEval"] is False
    assert config["includeIndicatorStyles"] is False
    assert config["historyCacheSize"] == 0
    assert config["selfRequestsOnly"] is True
    assert config["allowScriptTags"] is False


def test_forms_post_only_to_this_site_or_google() -> None:
    # Final review M9: no billing in v0, so no Stripe origin in form-action.
    [form_action] = [d for d in CSP.split("; ") if d.startswith("form-action")]
    assert form_action == "form-action 'self' https://accounts.google.com"
    assert "stripe" not in CSP
