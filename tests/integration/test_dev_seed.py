"""`tutor.ops.dev_seed` is idempotent and goes through the services layer (design D4)."""

import io
import zoneinfo
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, func, select
from tutor.ops.dev_seed import SeedReport, seed

from tutor.db.tables import audit_log, plans, profiles, users
from tutor.db.uow import PgIdentity, pg_uow_factory
from tutor.ops import dev_seed
from tutor.services.context import Services
from tutor.services.profile import get_profile

pytestmark = pytest.mark.integration

ALL_ZONES = frozenset(zoneinfo.available_timezones())


def _services(engine: Engine, now: datetime) -> Services:
    return Services(uow=pg_uow_factory(engine), clock=lambda: now, valid_timezones=ALL_ZONES)


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:  # the owner sees every row
        return {
            table.name: conn.execute(select(func.count()).select_from(table)).scalar_one()
            for table in (users, profiles, plans, audit_log)
        }


def test_seed_twice_creates_two_users_and_one_profile_without_duplicates(
    engine: Engine, login_engine: Engine, now: datetime
) -> None:
    identity = PgIdentity(login_engine)
    first = seed(identity, _services(login_engine, now), now)
    assert first == SeedReport(created=("dev-ana", "dev-nuevo"), existing=())
    after_first = _counts(engine)
    assert (after_first["users"], after_first["profiles"], after_first["plans"]) == (2, 1, 1)

    later = now + timedelta(minutes=5)
    second = seed(identity, _services(login_engine, later), later)
    assert second == SeedReport(created=(), existing=("dev-ana", "dev-nuevo"))
    assert _counts(engine) == after_first  # nothing added: no users, profiles, plans or audit rows

    with engine.connect() as conn:
        rows = conn.execute(
            select(users.c.google_sub, users.c.email, users.c.display_name).order_by(
                users.c.google_sub
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("dev-ana", "ana@dev.test", "Ana"),
        ("dev-nuevo", "nuevo@dev.test", "Nuevo"),
    ]
    svc = _services(login_engine, later)
    ana = get_profile(svc, identity.resolve("dev-ana", None, None, later).id)
    assert not ana.onboarding_needed and ana.plan is not None and ana.profile is not None
    assert ana.profile.timezone == "America/Mexico_City"
    nuevo = get_profile(svc, identity.resolve("dev-nuevo", None, None, later).id)
    assert nuevo.onboarding_needed and nuevo.profile is None and nuevo.plan is None


def _env(database_url: str, flag: str = "1") -> dict[str, str]:
    return {
        "TUTOR_ENV": "dev",
        "TUTOR_DEV_AUTH": flag,
        "TUTOR_BASE_URL": "http://localhost:8000",
        "DATABASE_URL": database_url,
        "TUTOR_JWT_SIGNING_KEY": "j" * 40,
        "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
        "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
    }


def test_entry_point_seeds_as_the_login_role_and_is_idempotent(
    engine: Engine, login_engine: Engine
) -> None:
    url = login_engine.url.render_as_string(hide_password=False)
    out = io.StringIO()
    assert dev_seed.main(_env(url), out=out) == 0
    assert "created dev-ana, dev-nuevo" in out.getvalue()
    again = io.StringIO()
    assert dev_seed.main(_env(url), out=again) == 0
    assert "existing dev-ana, dev-nuevo" in again.getvalue()
    counts = _counts(engine)
    assert (counts["users"], counts["profiles"], counts["plans"]) == (2, 1, 1)
    for text in (out.getvalue(), again.getvalue()):
        assert "j" * 40 not in text and url not in text


def test_entry_point_refuses_without_the_flag(login_engine: Engine, engine: Engine) -> None:
    url = login_engine.url.render_as_string(hide_password=False)
    out, err = io.StringIO(), io.StringIO()
    assert dev_seed.main(_env(url, flag=""), out=out, err=err) == 2
    assert out.getvalue() == "" and "TUTOR_DEV_AUTH" in err.getvalue()
    assert _counts(engine)["users"] == 0


def test_entry_point_refuses_the_owner_url(engine: Engine) -> None:
    # DATABASE_URL must be the app's LOGIN role: the owner bypasses row-level security.
    owner = engine.url.render_as_string(hide_password=False)
    with pytest.raises(SystemExit, match="DATABASE_URL must be a non-superuser LOGIN role"):
        dev_seed.main(_env(owner), out=io.StringIO())
    assert _counts(engine)["users"] == 0
