"""Dashboard app on PgWebBackend with a fake Google login (core Tasks 23 and 25)."""

from dataclasses import replace

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from tutor.db.tables import web_sessions
from tutor.settings import Settings
from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.memory import FakeGoogle
from tutor.web.pg import pg_web_deps
from tutor.web.ports import GoogleIdentity
from tutor.web.sessions import COOKIE, hash_token

BASE = "https://testserver"
WEB_CONFIG = WebConfig(
    env="test", base_url=BASE, mcp_url=f"{BASE}/mcp", support_email="soporte@example.test"
)


def pg_settings() -> Settings:
    return Settings.from_env(
        {
            "TUTOR_BASE_URL": BASE,
            "DATABASE_URL": "postgresql://tutor:tutor@localhost:5433/tutor_test",
            "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
            "TUTOR_JWT_SIGNING_KEY": "j" * 40,
            "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
        }
    )


def pg_web_app(engine: Engine, google: FakeGoogle) -> FastAPI:
    """The production web deps with Google replaced by FakeGoogle.

    Pass the non-superuser `login_engine` fixture, as production connects (ruling S2)."""
    deps = replace(pg_web_deps(engine, pg_settings(), WEB_CONFIG), google=google)
    return create_app(deps, WEB_CONFIG)


def google_login(
    client: TestClient, google: FakeGoogle, identity: GoogleIdentity, next_path: str = "/app/"
) -> str:
    """Run /auth/google -> /auth/callback; return where the callback redirects."""
    google.next_identity = identity
    start = client.get(f"/auth/google?next={next_path}")
    assert start.status_code == 302, start.text
    callback = client.get(start.headers["location"].replace(BASE, ""))
    assert callback.status_code == 303, callback.text
    return str(callback.headers["location"])


def csrf_token(engine: Engine, client: TestClient) -> str:
    """The logged-in session's CSRF token, read as the owner from web_sessions."""
    token = client.cookies.get(COOKIE)
    assert token, "no session cookie"
    with engine.connect() as conn:
        value = conn.execute(
            select(web_sessions.c.csrf_token).where(web_sessions.c.token_hash == hash_token(token))
        ).scalar_one()
    return str(value)
