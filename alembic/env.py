"""Alembic environment.

The connection comes from, in order: Config.attributes["connection"] (tests), `-x url=...`,
or the DATABASE_URL environment variable. Migrations run as the database owner.
"""

import os
from logging.config import fileConfig

from sqlalchemy import Connection, create_engine, pool

from alembic import context
from tutor.db.engine import psycopg_url
from tutor.db.tables import metadata

config = context.config
target_metadata = metadata


def _database_url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL or pass -x url=postgresql://...")
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
