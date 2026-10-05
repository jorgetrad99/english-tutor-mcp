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


def role_problems(
    *,
    superuser: bool,
    bypassrls: bool,
    reaches_privileged: bool,
    owned_tables: Sequence[str],
    in_tutor_app: bool,
    other_memberships: Sequence[str] = (),
    owned_objects: Sequence[str] = (),
) -> list[str]:
    """What is wrong with the app's login role; empty when it is safe to serve with."""
    problems: list[str] = []
    if superuser:
        problems.append("the login role is a superuser")
    if bypassrls:
        problems.append("the login role has BYPASSRLS")
    if reaches_privileged:
        problems.append("the login role is a member of a superuser or BYPASSRLS role")
    if owned_tables:
        problems.append("the login role owns user tables, directly or through a role")
    if not in_tutor_app:
        problems.append("the login role is not a member of tutor_app")
    if other_memberships:
        problems.append("the login role is a member of roles other than tutor_app")
    if owned_objects:
        problems.append("the login role owns database objects")
    return problems


def check_app_role(engine: Engine) -> None:
    """Refuse to start unless row-level security binds this engine's login role.

    PostgreSQL skips RLS for superusers and BYPASSRLS roles, and a table's owner can alter its
    policies. Membership counts (pg_has_role ... 'MEMBER'), because SET ROLE reaches any role
    the login is a member of. DATABASE_URL must be a plain LOGIN role that is only a member of
    tutor_app (the owner's URL is MIGRATION_DATABASE_URL). Messages never carry the URL.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT r.rolsuper, r.rolbypassrls, "
                "EXISTS (SELECT 1 FROM pg_roles p WHERE (p.rolsuper OR p.rolbypassrls) "
                "AND pg_has_role(session_user, p.oid, 'MEMBER')), "
                "pg_has_role(session_user, 'tutor_app', 'MEMBER') "
                "FROM pg_roles r WHERE r.rolname = session_user"
            )
        ).one()
        owned = (
            conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = current_schema() AND c.relname = ANY(:tables) "
                    "AND pg_has_role(session_user, c.relowner, 'MEMBER')"
                ),
                {"tables": list(USER_TABLES)},
            )
            .scalars()
            .all()
        )
    with engine.connect() as conn:
        others = (
            conn.execute(
                text(
                    "SELECT g.rolname FROM pg_auth_members m "
                    "JOIN pg_roles g ON g.oid = m.roleid "
                    "JOIN pg_roles u ON u.oid = m.member "
                    "WHERE u.rolname = session_user AND g.rolname <> 'tutor_app'"
                )
            )
            .scalars()
            .all()
        )
        objects = (
            conn.execute(
                text(
                    "SELECT 'relation' FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                    "WHERE r.rolname = session_user "
                    "UNION ALL SELECT 'function' FROM pg_proc p "
                    "JOIN pg_roles r ON r.oid = p.proowner "
                    "WHERE r.rolname = session_user "
                    "UNION ALL SELECT 'schema' FROM pg_namespace n JOIN pg_roles r "
                    "ON r.oid = n.nspowner WHERE r.rolname = session_user"
                )
            )
            .scalars()
            .all()
        )
    problems = role_problems(
        superuser=row[0],
        bypassrls=row[1],
        reaches_privileged=row[2],
        owned_tables=owned,
        in_tutor_app=row[3],
        other_memberships=others,
        owned_objects=objects,
    )
    if problems:
        raise SystemExit(
            "DATABASE_URL must be a non-superuser LOGIN role without BYPASSRLS that is a member "
            "of tutor_app and owns no tables (use MIGRATION_DATABASE_URL for the owner): "
            + "; ".join(problems)
        )
