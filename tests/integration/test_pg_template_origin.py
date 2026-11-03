"""The template origin through the production composition on Postgres (spec 10, binding (d)).

The document function is a stub here; integration wires the subjects port's
`instance_document`, which re-checks ownership under RLS as the token's user."""

from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from web_pg_support import BASE, WEB_CONFIG, pg_settings

from tutor.app import build_app
from tutor.web.security import dashboard_csp, template_frame_csp

pytestmark = pytest.mark.integration

ORIGIN = "https://templates.testserver"
ANA, INSTANCE = uuid4(), uuid4()


def test_raw_document_is_served_on_the_template_host_only(
    login_engine: Engine, tmp_path: Path
) -> None:
    settings = replace(
        pg_settings(),
        oauth_storage_dir=tmp_path / "oauth",
        template_origin=ORIGIN,
        frame_token_key="f" * 48,
    )

    def document(user_id: UUID, iid: UUID) -> str | None:
        return "<!doctype html><p>ok</p>" if (user_id, iid) == (ANA, INSTANCE) else None

    app = build_app(
        settings,
        engine=login_engine,
        web_config=replace(WEB_CONFIG, template_origin=ORIGIN),
        instance_document=document,
    )
    assert app.frame_url is not None
    path = app.frame_url(ANA, INSTANCE).removeprefix(ORIGIN)
    templates = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    dashboard = TestClient(app, base_url=BASE, follow_redirects=False)
    assert dashboard.get(path).status_code == 404
    served = templates.get(path)
    assert served.status_code == 200
    assert served.headers["content-type"] == "text/html; charset=utf-8"
    assert served.headers["content-security-policy"] == template_frame_csp(BASE)
    assert served.headers["referrer-policy"] == "no-referrer"
    assert "set-cookie" not in served.headers
    assert templates.get(path).status_code == 404  # the token was used
    foreign = app.frame_url(uuid4(), INSTANCE).removeprefix(ORIGIN)
    assert templates.get(foreign).status_code == 404
    login = dashboard.get("/login")
    assert login.headers["content-security-policy"] == dashboard_csp(ORIGIN)
