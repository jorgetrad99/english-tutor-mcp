"""v0 stand-ins for billing, subscriptions and settings; uncapped Free usage (core Task 23)."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID, uuid4

import pytest
import sqlalchemy
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from web_pg_support import WEB_CONFIG, pg_settings

from tutor.app import RequestLog, web_config_from_env
from tutor.domain.dashboard.billing import banners_for
from tutor.domain.dashboard.types import FreeUsage, Subscription, SubStatus, Tier, User
from tutor.mcp.observe import user_hash
from tutor.web.config import WebConfig
from tutor.web.demo import DemoUsers
from tutor.web.deps import current_user
from tutor.web.google import GoogleOidcLogin
from tutor.web.memory import MemoryBackend
from tutor.web.pg import (
    FREE,
    BillingDisabled,
    DisabledBilling,
    PgWebBackend,
    V0Settings,
    V0Subscriptions,
    pg_web_deps,
)
from tutor.web.ports import UNCAPPED
from tutor.web.views import render

from .conftest import BASE, TODAY

pytestmark = pytest.mark.unit

Login = Callable[[UUID], TestClient]
FREE_SUB = Subscription(Tier.FREE, SubStatus.NONE, "price_29", 2900)


def _probe(app: FastAPI) -> None:
    @app.get("/app/v0-probe", response_class=HTMLResponse)
    def probe(request: Request, user: Annotated[User, Depends(current_user)]) -> HTMLResponse:
        return render(request, "layouts/app.html", {"active_nav": "home"})


def test_v0_subscription_is_always_free_and_raises_no_banner() -> None:
    subs = V0Subscriptions()
    sub = subs.subscription(uuid4())
    assert sub == FREE
    assert (sub.tier, sub.status) == (Tier.FREE, SubStatus.NONE)
    usage = FreeUsage(500, UNCAPPED, 5000, UNCAPPED, date(2027, 1, 18))
    assert banners_for(sub, usage, TODAY) == ()
    assert subs.customer_id(uuid4()) is None
    assert subs.user_for_customer("cus_1") is None
    with pytest.raises(NotImplementedError):
        subs.link_customer(uuid4(), "cus_1")


def test_billing_is_disabled() -> None:
    billing = DisabledBilling()
    with pytest.raises(BillingDisabled):
        billing.checkout_url(
            user_id=uuid4(), customer_id=None, price_id="p", success_url="/", cancel_url="/"
        )
    with pytest.raises(BillingDisabled):
        billing.portal_url(customer_id="cus_1", return_url="/")
    with pytest.raises(BillingDisabled):
        billing.parse_event(b"{}", "sig")


def test_v0_settings_are_empty() -> None:
    settings = V0Settings()
    assert settings.list_settings() == ()
    assert settings.update_setting("k", "1", uuid4(), datetime(2026, 10, 14, tzinfo=UTC)) is None


def test_uncapped_usage_shows_no_banner_and_no_counter(
    app: FastAPI, login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    _probe(app)
    backend.subs[demo.ana] = FREE_SUB
    backend.session_cap = backend.glossary_cap = UNCAPPED
    backend.sessions_this_week[demo.ana] = 50
    html = login(demo.ana).get("/app/v0-probe").text
    assert 'class="banner' not in html
    assert "sesiones esta semana" not in html


def test_capped_usage_still_reaches_the_banners(
    app: FastAPI, login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    _probe(app)
    backend.subs[demo.ana] = FREE_SUB
    backend.sessions_this_week[demo.ana] = backend.session_cap
    assert 'class="banner' in login(demo.ana).get("/app/v0-probe").text


def test_pg_web_deps_wires_the_v0_adapters() -> None:
    deps = pg_web_deps(sqlalchemy.create_engine("sqlite://"), pg_settings(), WEB_CONFIG)
    assert isinstance(deps.users, PgWebBackend)
    assert deps.users is deps.sessions is deps.reader is deps.glossary is deps.account
    assert isinstance(deps.subscriptions, V0Subscriptions)
    assert isinstance(deps.billing, DisabledBilling)
    assert isinstance(deps.settings, V0Settings)
    assert isinstance(deps.google, GoogleOidcLogin)
    assert deps.clock().tzinfo is not None


def test_pg_web_deps_refuses_the_test_login() -> None:
    config = WebConfig(
        env="test",
        base_url="https://localhost",
        mcp_url="https://localhost/mcp",
        support_email="soporte@example.test",
        test_login=True,
    )
    with pytest.raises(ValueError, match="test login"):
        pg_web_deps(sqlalchemy.create_engine("sqlite://"), pg_settings(), config)


def test_missing_web_setting_exits_naming_the_key() -> None:
    env = {
        "TUTOR_ENV": "test",
        "TUTOR_BASE_URL": "https://t.example",
        "TUTOR_MCP_URL": "https://t.example/mcp",
    }
    with pytest.raises(SystemExit, match="Missing settings: TUTOR_SUPPORT_EMAIL"):
        web_config_from_env(env)


def test_invalid_web_setting_exits_without_its_value() -> None:
    env = {
        "TUTOR_ENV": "test",
        "TUTOR_BASE_URL": "http://secret-host.example",
        "TUTOR_MCP_URL": "https://t.example/mcp",
        "TUTOR_SUPPORT_EMAIL": "soporte@example.test",
    }
    with pytest.raises(SystemExit, match="TUTOR_BASE_URL") as exited:
        web_config_from_env(env)
    assert "secret-host" not in str(exited.value)


def test_request_log_names_the_route_and_the_learner(
    app: FastAPI, demo: DemoUsers, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="tutor.http")
    with TestClient(RequestLog(app), base_url=BASE, follow_redirects=False) as client:
        assert client.post("/auth/test-login", data={"user_id": str(demo.ana)}).status_code == 303
        assert client.get("/app/account?q=private-words").status_code == 200
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "tutor.http"]
    assert [(x["route"], x["status"]) for x in lines] == [
        ("/auth/test-login", 303),
        ("/app/account", 200),
    ]
    assert lines[1]["user_hash"] == user_hash(demo.ana)
    assert "private-words" not in caplog.text
