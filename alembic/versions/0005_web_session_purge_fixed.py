"""Purge expired web sessions with fixed lifetimes.

0004's function let the caller pass the lifetimes (with floors). A compromised app role
could still pick the floors and end sessions a day early. This replaces it with a STRICT
SECURITY DEFINER function that takes only the clock: the lifetimes are constants here
(14 days idle, 30 days absolute, 10 minutes anonymous) and must equal tutor.web's
WebConfig defaults and ANONYMOUS_LIFETIME (the app asserts that at startup). The cut-off is
`least(p_now, now())`, so a future "now" cannot end live sessions. The owner policies from
0004 stay.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SIGNATURE = "purge_expired_web_sessions(timestamptz, interval, interval, interval)"
NEW_SIGNATURE = "purge_expired_web_sessions(timestamptz)"

CREATE_NEW = """
CREATE FUNCTION public.purge_expired_web_sessions(p_now timestamptz) RETURNS integer
LANGUAGE plpgsql
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    cutoff timestamptz := least(p_now, now());
    removed integer;
BEGIN
    DELETE FROM public.web_sessions
     WHERE (user_id IS NULL AND created_at < cutoff - interval '10 minutes')
        OR (user_id IS NOT NULL
            AND (last_seen_at < cutoff - interval '14 days'
                 OR created_at < cutoff - interval '30 days'));
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$$
"""

CREATE_OLD = """
CREATE FUNCTION public.purge_expired_web_sessions(
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
    op.execute(f"DROP FUNCTION public.{OLD_SIGNATURE}")
    op.execute(CREATE_NEW)
    op.execute(f"REVOKE ALL ON FUNCTION public.{NEW_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{NEW_SIGNATURE} TO tutor_app")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION public.{NEW_SIGNATURE}")
    op.execute(CREATE_OLD)
    op.execute(f"REVOKE ALL ON FUNCTION public.{OLD_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{OLD_SIGNATURE} TO tutor_app")
