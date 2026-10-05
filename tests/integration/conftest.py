"""Postgres fixtures for integration tests (db-test on port 5433, TEST_DATABASE_URL in CI)."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
from alembic.config import Config
from sqlalchemy import Engine, text

from alembic import command
from tutor.db.engine import make_engine
from tutor.db.tables import USER_TABLES
from tutor.db.uow import PgIdentity, pg_uow_factory
from tutor.services.ports import IdentityResolver, UowFactory

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "postgresql://tutor:tutor@localhost:5433/tutor_test"
NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # a Wednesday


def run_alembic(engine: Engine, action: Literal["upgrade", "downgrade"], revision: str) -> None:
    """Run one Alembic command on a connection from `engine` (env.py reads Config.attributes)."""
    cfg = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        if action == "upgrade":
            command.upgrade(cfg, revision)
        else:
            command.downgrade(cfg, revision)


def truncate_user_tables(engine: Engine) -> None:
    """Empty every user-data table; the seeded track tables are kept."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(USER_TABLES)} CASCADE"))


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = make_engine(os.environ.get("TEST_DATABASE_URL", DEFAULT_URL))
    run_alembic(eng, "upgrade", "head")
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_db(engine: Engine) -> None:
    truncate_user_tables(engine)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def uow_factory(engine: Engine) -> UowFactory:
    return pg_uow_factory(engine)


@pytest.fixture
def identity(engine: Engine) -> IdentityResolver:
    return PgIdentity(engine)


@pytest.fixture
def user_id(identity: IdentityResolver, now: datetime) -> UUID:
    return identity.resolve("sub-user-a", "ana@example.com", "Ana", now).id


@pytest.fixture
def other_user_id(identity: IdentityResolver, now: datetime) -> UUID:
    return identity.resolve("sub-user-b", "beto@example.com", "Beto", now).id
