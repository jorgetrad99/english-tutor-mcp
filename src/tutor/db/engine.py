"""Engine construction: sync SQLAlchemy 2 on psycopg 3 (spec D7)."""

from collections.abc import Sequence

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from tutor.db.tables import USER_TABLES


def psycopg_url(database_url: str) -> str:
    """Force the psycopg 3 driver on a plain postgresql:// URL."""
    url = make_url(database_url)
    if url.drivername in ("postgresql", "postgres"):
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def make_engine(database_url: str) -> Engine:
    """Pooled engine; every connection uses UTC so timestamptz values come back in UTC."""
    return create_engine(
        psycopg_url(database_url),
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC"},
    )


def role_problems(*, superuser: bool, bypassrls: bool, owned_tables: Sequence[str]) -> list[str]:
    """What is wrong with the app's login role; empty when it is safe to serve with."""
    problems: list[str] = []
    if superuser:
        problems.append("the login role is a superuser")
    if bypassrls:
        problems.append("the login role has BYPASSRLS")
    if owned_tables:
        problems.append("the login role owns user tables")
    return problems


def check_app_role(engine: Engine) -> None:
    """Refuse to start unless row-level security binds this engine's login role.

    PostgreSQL skips RLS for superusers and BYPASSRLS roles, and a table's owner can alter its
    policies, so DATABASE_URL must be a plain LOGIN role that is only a member of tutor_app (the
    owner's URL is MIGRATION_DATABASE_URL). Messages never carry the URL.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = session_user")
        ).one()
        owned = (
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() "
                    "AND tableowner = session_user AND tablename = ANY(:tables)"
                ),
                {"tables": list(USER_TABLES)},
            )
            .scalars()
            .all()
        )
    problems = role_problems(superuser=row[0], bypassrls=row[1], owned_tables=owned)
    if problems:
        raise SystemExit(
            "DATABASE_URL must be a non-superuser LOGIN role without BYPASSRLS that is a member "
            "of tutor_app and owns no tables (use MIGRATION_DATABASE_URL for the owner): "
            + "; ".join(problems)
        )
