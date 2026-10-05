"""The Task 20 scripted lesson through the real server on Postgres.

The app connects as the non-superuser login role; the caller is identified by a real access token
in context, so `get_access_token()` -> `current_user_id` runs inside the tool worker thread.
"""

import secrets
import zoneinfo
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from fastmcp import Client, FastMCP
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.shared.exceptions import MCPError
from mcp_lesson import (
    Clock,
    call,
    random_sub,
    run_provisional_round_trip,
    run_review_then_reuse,
    run_scripted_lesson,
)
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from tutor.app import build_app
from tutor.db.engine import check_app_role
from tutor.db.uow import PgIdentity, pg_uow_factory
from tutor.domain.fsrs import review
from tutor.mcp.server import build_mcp
from tutor.services.context import Services
from tutor.settings import Settings
from tutor.web.config import WebConfig

pytestmark = pytest.mark.integration


@contextmanager
def signed_in(sub: str) -> Iterator[None]:
    """A verified access token in context, as FastMCP's bearer middleware would leave it."""
    token = AccessToken(
        token="test-token",  # noqa: S106 - not a secret
        client_id=sub,
        scopes=[],
        claims={"sub": sub, "email": f"{sub}@example.com", "name": "Test Learner"},
    )
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


def server(login_engine: Engine, clock: Clock) -> FastMCP:
    svc = Services(
        uow=pg_uow_factory(login_engine),
        clock=clock,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    return build_mcp(svc, PgIdentity(login_engine), auth=None)


@pytest.mark.asyncio
async def test_scripted_lesson_on_postgres_with_the_login_role(login_engine: Engine) -> None:
    clock = Clock()
    with signed_in(random_sub()):
        await run_scripted_lesson(server(login_engine, clock), clock)


@pytest.mark.asyncio
async def test_provisional_items_round_trip_on_postgres(login_engine: Engine) -> None:
    clock = Clock()
    with signed_in(random_sub()):
        await run_provisional_round_trip(server(login_engine, clock), clock)


@pytest.mark.asyncio
@pytest.mark.parametrize(("reinforce", "rating"), [(False, 4), (True, 3)])
async def test_rating_four_upgrade_on_postgres(
    login_engine: Engine, reinforce: bool, rating: int
) -> None:
    clock = Clock()
    sub = random_sub()
    with signed_in(sub):
        session_id = await run_review_then_reuse(
            server(login_engine, clock), clock, reinforce=reinforce
        )
    user_id = PgIdentity(login_engine).resolve(sub, None, None, clock.now).id
    with pg_uow_factory(login_engine)(user_id) as uow:
        (log,) = uow.reviews.session_logs(UUID(session_id))
        assert log.rating == rating
        upgraded = review(log.state_before, 4, log.reviewed_at)
        assert (uow.reviews.state(log.item_id) == upgraded) is (rating == 4)


@pytest.mark.asyncio
async def test_a_second_user_sees_none_of_the_first_users_data(login_engine: Engine) -> None:
    clock = Clock()
    mcp = server(login_engine, clock)
    with signed_in(random_sub()):
        await run_scripted_lesson(mcp, clock)
        async with Client(mcp) as client:
            mine = await call(client, "get_profile", {})
    assert mine["onboarding_needed"] is False
    with signed_in(random_sub()):
        async with Client(mcp) as client:
            view = await call(client, "get_profile", {})
    assert view["onboarding_needed"] is True
    assert (view["streak"], view["due_reviews_count"], view["provisional_count"]) == (0, 0, 0)
    assert view["profile"] is None
    assert view["open_session_id"] is None


@pytest.mark.asyncio
async def test_a_call_without_a_token_is_refused(login_engine: Engine) -> None:
    async with Client(server(login_engine, Clock())) as client:
        with pytest.raises(MCPError, match="Not authorized"):
            await client.call_tool("get_profile", {})


WEB_CONFIG = WebConfig(
    env="test",
    base_url="https://tutor.example.com",
    mcp_url="https://tutor.example.com/mcp",
    support_email="soporte@example.test",
)


def app_settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "TUTOR_BASE_URL": "https://tutor.example.com",
            "DATABASE_URL": "postgresql://unused@localhost/unused",
            "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
            "TUTOR_JWT_SIGNING_KEY": "j" * 40,
            "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
            "TUTOR_OAUTH_STORAGE_DIR": str(tmp_path / "oauth"),
        }
    )


def test_startup_check_accepts_the_app_login_role(login_engine: Engine) -> None:
    check_app_role(login_engine)


def test_startup_check_refuses_the_owner(engine: Engine) -> None:
    with pytest.raises(SystemExit) as refused:
        check_app_role(engine)
    message = str(refused.value)
    assert "DATABASE_URL" in message
    assert "tutor:tutor" not in message
    assert "localhost" not in message


def test_build_app_checks_the_role_before_serving(
    login_engine: Engine, engine: Engine, tmp_path: Path
) -> None:
    build_app(app_settings(tmp_path), engine=login_engine, web_config=WEB_CONFIG)
    with pytest.raises(SystemExit, match="login role"):
        build_app(app_settings(tmp_path), engine=engine, web_config=WEB_CONFIG)


OWNER_MEMBER = "tutor_owner_member_test"


@pytest.fixture
def owner_member_engine(engine: Engine) -> Iterator[Engine]:
    """A NOSUPERUSER login that is a member of tutor_app AND of the table owner's role."""
    from tutor.db.engine import make_engine
    from tutor.db.tables import USER_TABLES

    owner = engine.connect()
    with owner:
        who = owner.execute(
            text("SELECT tableowner FROM pg_tables WHERE tablename = :t"), {"t": USER_TABLES[0]}
        ).scalar_one()
        password = secrets.token_hex(16)
        with engine.begin() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {OWNER_MEMBER}"))
            conn.execute(
                text(
                    f"CREATE ROLE {OWNER_MEMBER} LOGIN NOSUPERUSER NOBYPASSRLS "
                    f"PASSWORD '{password}'"
                )
            )
            conn.execute(text(f"GRANT tutor_app TO {OWNER_MEMBER}"))
            conn.execute(text(f'GRANT "{who}" TO {OWNER_MEMBER}'))
    url = make_url(engine.url.render_as_string(hide_password=False)).set(
        username=OWNER_MEMBER, password=password
    )
    eng = make_engine(url.render_as_string(hide_password=False))
    yield eng
    eng.dispose()
    with engine.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {OWNER_MEMBER}"))


def test_startup_check_refuses_a_login_that_is_a_member_of_the_owner(
    owner_member_engine: Engine,
) -> None:
    with pytest.raises(SystemExit, match="login role"):
        check_app_role(owner_member_engine)


def test_startup_check_refuses_a_login_outside_tutor_app(engine: Engine) -> None:
    from tutor.db.engine import make_engine

    password = secrets.token_hex(16)
    name = "tutor_no_app_test"
    with engine.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {name}"))
        conn.execute(
            text(f"CREATE ROLE {name} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}'")
        )
    try:
        url = engine.url.set(username=name, password=password)
        eng = make_engine(url.render_as_string(hide_password=False))
        with pytest.raises(SystemExit, match="not a member of tutor_app"):
            check_app_role(eng)
        eng.dispose()
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {name}"))
