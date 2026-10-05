"""Purge expired web sessions without an owner connection.

Row-level security lets tutor_app see a web session only through its cookie hash or its
user, so no app query can find expired sessions of other people. This SECURITY DEFINER
function deletes only expired rows: the cut-off is never later than the server clock and
the lifetimes have floors, so a caller cannot use it to end live sessions. Under FORCE ROW
LEVEL SECURITY a table owner that is not a superuser is bound by the policies too, so the
owner gets SELECT and DELETE policies on web_sessions (only the owner; the app's login role
must not be a member of it, which tutor.db.engine.check_app_role enforces).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE = "purge_expired_web_sessions(timestamptz, interval, interval, interval)"

CREATE_FUNCTION = """
CREATE FUNCTION purge_expired_web_sessions(
    p_now timestamptz, p_idle interval, p_absolute interval, p_anonymous interval
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    cutoff timestamptz := least(p_now, now());
    removed integer;
BEGIN
    IF p_idle < interval '1 day' OR p_absolute < interval '1 day'
        OR p_anonymous < interval '10 minutes' THEN
        RAISE EXCEPTION 'web session lifetimes below the minimum';
    END IF;
    DELETE FROM public.web_sessions
     WHERE (user_id IS NULL AND created_at < cutoff - p_anonymous)
        OR (user_id IS NOT NULL
            AND (last_seen_at < cutoff - p_idle OR created_at < cutoff - p_absolute));
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$$
"""


def upgrade() -> None:
    op.execute(CREATE_FUNCTION)
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO tutor_app")
    op.execute(
        "CREATE POLICY web_sessions_owner_read ON web_sessions FOR SELECT TO CURRENT_USER"
        " USING (true)"
    )
    op.execute(
        "CREATE POLICY web_sessions_owner_purge ON web_sessions FOR DELETE TO CURRENT_USER"
        " USING (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS web_sessions_owner_purge ON web_sessions")
    op.execute("DROP POLICY IF EXISTS web_sessions_owner_read ON web_sessions")
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
