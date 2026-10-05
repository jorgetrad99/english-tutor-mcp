"""Deployment step: create or update the app login role and the gate-report role (idempotent).

Run by the one-shot `migrate` service after `alembic upgrade head`, as the migration owner:
    MIGRATION_DATABASE_URL  owner URL (never given to the app)
    APP_DB_USER             name of the app's LOGIN role (becomes DATABASE_URL's user)
    APP_DB_PASSWORD         its password (secret)
    REPORT_DB_PASSWORD      password of `tutor_report` (secret; the role itself comes from
                            tutor.ops.report_role)
The app role is NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE and a member of `tutor_app` WITH
INHERIT FALSE, SET TRUE (the app does SET LOCAL ROLE tutor_app per transaction, so the login
itself holds no table privileges). Passwords are sent as SCRAM-SHA-256 verifiers computed client
side (the
server never sees plaintext) and are never printed; errors report the exception class only.
Needs PostgreSQL 16 or later.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping

import psycopg
from psycopg import sql

from tutor.ops.report_role import REPORT_ROLE, REPORT_ROLE_SQL

APP_ROLE = "tutor_app"
MIN_PASSWORD_CHARS = 32
ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
RESERVED = frozenset({APP_ROLE, REPORT_ROLE, "postgres", "public"})
KEYS = ("MIGRATION_DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD", "REPORT_DB_PASSWORD")
_LOGIN_ATTRS = sql.SQL("LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION")


def check_inputs(env: Mapping[str, str]) -> list[str]:
    """Names the settings that are missing or invalid; never echoes a value."""
    problems = [f"{key} is missing" for key in KEYS if not env.get(key, "").strip()]
    if problems:
        return problems
    user = env["APP_DB_USER"].strip()
    if not ROLE_NAME.fullmatch(user) or user in RESERVED or user.startswith("pg_"):
        problems.append("APP_DB_USER must be a lowercase role name other than the reserved ones")
    for key in ("APP_DB_PASSWORD", "REPORT_DB_PASSWORD"):
        value = env[key].strip()
        if len(value) < MIN_PASSWORD_CHARS or "\x00" in value:
            problems.append(f"{key} must be at least {MIN_PASSWORD_CHARS} characters")
    if env["APP_DB_PASSWORD"].strip() == env["REPORT_DB_PASSWORD"].strip():
        problems.append("APP_DB_PASSWORD and REPORT_DB_PASSWORD must differ")
    if not env["MIGRATION_DATABASE_URL"].strip().startswith(("postgresql:", "postgres:")):
        problems.append("MIGRATION_DATABASE_URL must be a PostgreSQL URL")
    return problems


def _verifier(conn: psycopg.Connection, user: str, password: str) -> str:
    """SCRAM-SHA-256 verifier computed client side: the server never sees the plaintext."""
    return conn.pgconn.encrypt_password(password.encode(), user.encode(), b"scram-sha-256").decode()


def _set_login_role(conn: psycopg.Connection, user: str, password: str) -> None:
    name = sql.Identifier(user)
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
    verb = sql.SQL("ALTER ROLE") if exists else sql.SQL("CREATE ROLE")
    conn.execute(
        sql.SQL("{verb} {name} {attrs} PASSWORD {pw}").format(
            verb=verb,
            name=name,
            attrs=_LOGIN_ATTRS,
            pw=sql.Literal(_verifier(conn, user, password)),
        )
    )
    conn.execute(
        sql.SQL("GRANT {app} TO {name} WITH INHERIT FALSE, SET TRUE").format(
            app=sql.Identifier(APP_ROLE), name=name
        )
    )


def _assert_unprivileged(conn: psycopg.Connection, user: str) -> None:
    """The login is a member of tutor_app only and owns no object at all."""
    memberships = {
        row[0]
        for row in conn.execute(
            "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
            "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = %s",
            (user,),
        )
    }
    owned = conn.execute(
        "SELECT (SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
        "WHERE r.rolname = %(u)s) + (SELECT count(*) FROM pg_proc p JOIN pg_roles r "
        "ON r.oid = p.proowner WHERE r.rolname = %(u)s) + (SELECT count(*) FROM pg_namespace n "
        "JOIN pg_roles r ON r.oid = n.nspowner WHERE r.rolname = %(u)s)",
        {"u": user},
    ).fetchone()
    if memberships != {APP_ROLE} or owned is None or owned[0]:
        raise SystemExit(
            "APP_DB_USER must be a member of tutor_app only and own nothing; "
            "use a dedicated role name"
        )


def provision(
    conn: psycopg.Connection, *, app_user: str, app_password: str, report_password: str
) -> None:
    """Create or update both roles in one transaction (commit left to the caller)."""
    owner = conn.execute("SELECT current_user").fetchone()
    if owner is None or owner[0] == app_user:
        raise SystemExit("APP_DB_USER must not be the migration owner")
    _set_login_role(conn, app_user, app_password)
    _assert_unprivileged(conn, app_user)
    conn.execute(REPORT_ROLE_SQL.encode())
    conn.execute(
        sql.SQL("ALTER ROLE {name} PASSWORD {pw}").format(
            name=sql.Identifier(REPORT_ROLE),
            pw=sql.Literal(_verifier(conn, REPORT_ROLE, report_password)),
        )
    )


def main(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    problems = check_inputs(env)
    if problems:
        print("Cannot provision roles: " + "; ".join(problems), file=sys.stderr)
        return 2
    try:
        with psycopg.connect(env["MIGRATION_DATABASE_URL"].strip()) as conn:
            provision(
                conn,
                app_user=env["APP_DB_USER"].strip(),
                app_password=env["APP_DB_PASSWORD"].strip(),
                report_password=env["REPORT_DB_PASSWORD"].strip(),
            )
    except psycopg.Error as exc:  # the message can carry the URL or statement text
        print(f"Cannot provision roles: database error {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"roles ready: {env['APP_DB_USER'].strip()}, {REPORT_ROLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
