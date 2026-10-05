"""Tenant-safe references, narrower users grant, stricter web_sessions check, role guard.

Postgres checks foreign keys without row-level security, so a single-column FK lets a row
scoped to user B point at user A's parent row. Every FK between user-owned tables becomes
composite (parent_id, user_id) -> parent (id, user_id).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_USER_ID = "nullif(current_setting('app.user_id', true), '')::uuid"
CURRENT_WEB_SESSION = "nullif(current_setting('app.web_session', true), '')"

# Parents that get UNIQUE (id, user_id) so children can reference the pair.
PARENTS = ("plans", "plan_items", "sessions", "glossary_items")

# (child, column, parent, ON DELETE clause); the old FK is <child>_<column>_fkey.
REFERENCES = (
    ("plan_items", "plan_id", "plans", "CASCADE"),
    ("sessions", "plan_item_id", "plan_items", "SET NULL (plan_item_id)"),
    ("session_metrics", "session_id", "sessions", "CASCADE"),
    ("session_errors", "session_id", "sessions", "CASCADE"),
    ("review_states", "glossary_item_id", "glossary_items", "CASCADE"),
    ("review_logs", "glossary_item_id", "glossary_items", "CASCADE"),
    ("review_logs", "session_id", "sessions", "CASCADE"),
)
# Single-column ON DELETE clause used by 0001 (the downgrade restores it).
_OLD_ON_DELETE = {"SET NULL (plan_item_id)": "SET NULL"}

# What PgIdentity inserts; id (generated) and role (default 'learner') are never app-writable.
USERS_INSERT_COLUMNS = "google_sub, email, display_name, created_at"

_HOLDER_OLD_CHECK = f"token_hash = {CURRENT_WEB_SESSION} OR user_id = {CURRENT_USER_ID}"
# A cookie alone may create or touch an anonymous session, never attach it to a user.
_HOLDER_NEW_CHECK = (
    f"(token_hash = {CURRENT_WEB_SESSION}"
    f" AND (user_id IS NULL OR user_id = {CURRENT_USER_ID}))"
    f" OR user_id = {CURRENT_USER_ID}"
)


def _fk_name(child: str, column: str) -> str:
    return f"{child}_{column}_user_id_fkey"


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles
                WHERE rolname = 'tutor_app' AND (rolsuper OR rolbypassrls)
            ) THEN
                RAISE EXCEPTION 'role tutor_app must not be SUPERUSER or BYPASSRLS';
            END IF;
        END
        $$;
        """
    )
    for table in PARENTS:
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_id_user_id_key UNIQUE (id, user_id)"
        )
    for child, column, parent, on_delete in REFERENCES:
        op.execute(f"ALTER TABLE {child} DROP CONSTRAINT {child}_{column}_fkey")
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {_fk_name(child, column)}"
            f" FOREIGN KEY ({column}, user_id) REFERENCES {parent} (id, user_id)"
            f" ON DELETE {on_delete}"
        )
    op.execute("REVOKE INSERT ON users FROM tutor_app")
    op.execute(f"GRANT INSERT ({USERS_INSERT_COLUMNS}) ON users TO tutor_app")
    op.execute(f"ALTER POLICY web_sessions_holder ON web_sessions WITH CHECK ({_HOLDER_NEW_CHECK})")


def downgrade() -> None:
    op.execute(f"ALTER POLICY web_sessions_holder ON web_sessions WITH CHECK ({_HOLDER_OLD_CHECK})")
    op.execute("REVOKE INSERT ON users FROM tutor_app")
    op.execute("GRANT INSERT ON users TO tutor_app")
    for child, column, parent, on_delete in reversed(REFERENCES):
        op.execute(f"ALTER TABLE {child} DROP CONSTRAINT {_fk_name(child, column)}")
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {child}_{column}_fkey"
            f" FOREIGN KEY ({column}) REFERENCES {parent} (id)"
            f" ON DELETE {_OLD_ON_DELETE.get(on_delete, on_delete)}"
        )
    for table in reversed(PARENTS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_id_user_id_key")
