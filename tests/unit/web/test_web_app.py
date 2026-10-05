import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang, Role, User
from tutor.web import i18n
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


def test_unhandled_error_logs_only_class_and_route(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    probe = "PROBE-learner-text-7f3a"

    @app.get("/boom")
    async def boom() -> None:
        raise ValueError(probe)

    caplog.set_level(logging.DEBUG)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        response = c.get(f"/boom?q={probe}")
    assert response.status_code == 500
    assert "Algo sali\N{LATIN SMALL LETTER O WITH ACUTE} mal" in response.text
    assert probe not in response.text
    ours = [r for r in caplog.records if r.name.startswith("tutor")]  # not the HTTP client's
    assert ours, "the failure must be logged"
    for record in ours:
        assert probe not in record.getMessage()
        assert record.exc_info is None
        assert probe not in str(record.args) and probe not in (record.stack_info or "")
    line = ours[0].getMessage()
    assert "path=/boom" in line and "exc=ValueError" in line


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
