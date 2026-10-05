"""Perfil on Postgres: the web form saves through the core service under row-level security."""

import re
import zoneinfo
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from web_pg_support import BASE, google_login, pg_web_app

from tutor.db.tables import plans, profiles, users
from tutor.services.context import Services
from tutor.services.ports import UowFactory
from tutor.web.memory import FakeGoogle
from tutor.web.ports import GoogleIdentity
from tutor.web.profile import ServicesProfiles, install_profiles

pytestmark = pytest.mark.integration

CSRF = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


def _utc_now() -> datetime:
    return datetime.now(UTC)


def test_first_login_onboards_through_perfil(engine: Engine, uow_factory: UowFactory) -> None:
    svc = Services(
        uow=uow_factory, clock=_utc_now, valid_timezones=frozenset(zoneinfo.available_timezones())
    )
    google = FakeGoogle()
    app = pg_web_app(engine, google)
    install_profiles(app, ServicesProfiles(svc))
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        identity = GoogleIdentity("g-perfil", "perfil@example.com", "Pia", True)
        assert google_login(c, google, identity, next_path="/app/glossary") == "/app/profile"
        page = c.get("/app/profile")
        assert page.status_code == 200
        token = CSRF.search(page.text)
        assert token
        saved = c.post(
            "/app/profile",
            data={
                "csrf_token": token.group(1),
                "self_level": "B1+",
                "domains": ["it"],
                "use_cases": ["incident", "standup"],
                "minutes_per_day": "15",
                "days_per_week": "5",
                "target_level": "B2",
                "target_date": "",
                "goal_text": "",
                "timezone": "America/New_York",
            },
        )
        assert saved.status_code == 303
        assert 'id="plan-title"' in c.get("/app/profile").text
    with engine.connect() as conn:
        uid, tz = conn.execute(
            select(users.c.id, users.c.timezone).where(users.c.google_sub == "g-perfil")
        ).one()
        use_cases = conn.execute(
            select(profiles.c.use_cases).where(profiles.c.user_id == uid)
        ).scalar_one()
        active = conn.execute(
            select(plans.c.version).where(plans.c.user_id == uid, plans.c.status == "active")
        ).scalar_one()
    assert (tz, sorted(use_cases), active) == ("America/New_York", ["incident", "standup"], 1)
