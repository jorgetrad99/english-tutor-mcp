"""Alembic environment.

The connection comes from, in order: Config.attributes["connection"] (tests), `-x url=...`,
MIGRATION_DATABASE_URL, or DATABASE_URL. Migrations run as the database owner; the running
app should use a separate non-superuser login (member of tutor_app) in DATABASE_URL.
"""

import os
from logging.config import fileConfig

from sqlalchemy import Connection, create_engine, pool

from alembic import context
from tutor.db.engine import psycopg_url

config = context.config
# Autogenerate is not used: tables.py holds no FKs, constraints or policies, so a diff against
# it would draft drops. Migrations are written by hand.
target_metadata = None


def _database_url() -> str:
    url = (
        context.get_x_argument(as_dictionary=True).get("url")
        or os.environ.get("MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise SystemExit("Set MIGRATION_DATABASE_URL, DATABASE_URL or pass -x url=postgresql://...")
    return psycopg_url(url)


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    shared = config.attributes.get("connection")
    if shared is not None:
        _run(shared)
        return
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run(connection)
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported; run against a database.")
run_migrations_online()
