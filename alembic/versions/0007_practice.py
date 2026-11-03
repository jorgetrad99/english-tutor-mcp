"""Practice: plans, sessions and their showings of template instances.

Subjects spec 8.2 as amended by sections 15.10 and 16.10 (slice B plan rulings 29, 30, 38, 63).
Additive only: no table of 0001-0006 changes. Three user-scoped tables with `user_id`
(CASCADE), forced row-level security, an own-rows policy, a CHECK per closed enum and per cap,
`UNIQUE (id, user_id)` on each parent and composite `(col, user_id)` foreign keys (0002
pattern). One open practice session per user (partial unique index); one active plan per
subject. A showing's `shown_at` is set by the server before any answer
(`ck_practice_items_shown_before_answer`). tutor_app may update only the columns the app
changes after the insert, and deletes nothing: rows leave with their subject or user. The
report role gets nothing.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03
"""

import os
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_USER_ID = "nullif(current_setting('app.user_id', true), '')::uuid"

# The downgrade drops the three tables: it runs only when they are empty (as seen by a role that
# bypasses RLS) or when the operator sets this variable to "1" (the 0006 pattern).
DESTRUCTIVE_DOWNGRADE_FLAG = "TUTOR_ALLOW_DESTRUCTIVE_DOWNGRADE"

# Creation order; the downgrade drops in reverse.
TABLES = ("practice_plans", "practice_sessions", "practice_items")

# The only changes after an insert: a plan is superseded; a session is closed or marked
# incomplete; a showing is shown, then answered once (ruling 63, spec 16.10).
PLANS_UPDATE_COLUMNS = "status"
SESSIONS_UPDATE_COLUMNS = "status, ended_at, self_rating, summary"
ITEMS_UPDATE_COLUMNS = "shown_at, answer, correct, score, answered_at, source"

PLAN_STATUSES = "('active', 'superseded')"
PRACTICE_MODES = "('app', 'text', 'class')"
PRACTICE_STATUSES = "('open', 'closed', 'incomplete')"
ITEM_SOURCES = "('app', 'chat')"

# Caps (domain constants restated: tutor.domain.subjects and tutor.domain.practice).
MINUTES_RANGE = (10, 120)
SESSIONS_RANGE = (1, 7)
SELF_RATING_RANGE = (1, 4)
SCORE_RANGE = (0, 100)

OPEN_SESSION_INDEX = "ux_practice_sessions_one_open_per_user"
ACTIVE_PLAN_INDEX = "ux_practice_plans_one_active_per_subject"


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _user_id() -> sa.Column[Any]:
    return sa.Column(
        "user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


def _ts(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _one_of(table: str, column: str, values: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} IN {values}", name=f"ck_{table}_{column}")


def _between(table: str, column: str, low: int, high: int) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} BETWEEN {low} AND {high}", name=f"ck_{table}_{column}")


def _pair_key(table: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint("id", "user_id", name=f"{table}_id_user_id_key")


# Same user, not same subject: the repository checks that a session's plan and every item's
# instance belong to the session's subject (slice A plan ruling 16).
def _child_fk(
    child: str, column: str, parent: str, *, ondelete: str | None = "CASCADE"
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column, "user_id"],
        [f"{parent}.id", f"{parent}.user_id"],
        name=f"{child}_{column}_user_id_fkey",
        ondelete=ondelete,
    )


def upgrade() -> None:
    _create_tables()
    _create_indexes()
    _enable_rls()
    _grant()


def downgrade() -> None:
    _refuse_to_drop_practice_data()
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def _refuse_to_drop_practice_data() -> None:
    if os.environ.get(DESTRUCTIVE_DOWNGRADE_FLAG) == "1":
        return
    conn = op.get_bind()
    # Under FORCE RLS a role that does not bypass it sees no rows, so an empty count proves
    # nothing; only a superuser or BYPASSRLS role can tell the tables are really empty.
    sees_all = conn.execute(
        sa.text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).scalar_one()
    has_data = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM practice_plans)"
            " OR EXISTS (SELECT 1 FROM practice_sessions)"
        )
    ).scalar_one()
    if has_data or not sees_all:
        reason = (
            "practice_plans or practice_sessions hold rows"
            if has_data
            else "this role cannot see past RLS"
        )
        raise RuntimeError(
            f"Refusing to downgrade 0007: it drops three tables of practice data and {reason}."
            f" Take a backup, then rerun with {DESTRUCTIVE_DOWNGRADE_FLAG}=1 to drop them."
        )


def _create_tables() -> None:
    p = "practice_plans"
    minutes_low, minutes_high = MINUTES_RANGE
    sessions_low, sessions_high = SESSIONS_RANGE
    op.create_table(
        p,
        _id(),
        _user_id(),
        sa.Column("subject_id", sa.Uuid, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("minutes_per_session", sa.SmallInteger, nullable=False),
        sa.Column("sessions_per_week", sa.SmallInteger, nullable=False),
        sa.Column("target_date", sa.Date),
        sa.Column("total_sessions", sa.Integer),
        sa.Column("rationale", JSONB, nullable=False),
        _ts("created_at"),
        _pair_key(p),
        _child_fk(p, "subject_id", "subjects"),
        # user_id first in both plan keys: a key on another user's subject id must fail on
        # the foreign key, never on a uniqueness check that would reveal that user's rows.
        sa.UniqueConstraint(
            "user_id", "subject_id", "version", name=f"{p}_user_subject_version_key"
        ),
        _one_of(p, "status", PLAN_STATUSES),
        sa.CheckConstraint("version >= 1", name=f"ck_{p}_version"),
        _between(p, "minutes_per_session", minutes_low, minutes_high),
        _between(p, "sessions_per_week", sessions_low, sessions_high),
        sa.CheckConstraint(
            "(total_sessions IS NULL) = (target_date IS NULL)"
            " AND coalesce(total_sessions >= 0, true)",
            name=f"ck_{p}_total_sessions",
        ),
        sa.CheckConstraint("jsonb_typeof(rationale) = 'object'", name=f"ck_{p}_rationale"),
    )
    s = "practice_sessions"
    rating_low, rating_high = SELF_RATING_RANGE
    op.create_table(
        s,
        _id(),
        _user_id(),
        sa.Column("subject_id", sa.Uuid, nullable=False),
        sa.Column("plan_id", sa.Uuid),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        _ts("started_at"),
        _ts("ended_at", nullable=True),
        sa.Column("self_rating", sa.SmallInteger),
        sa.Column("summary", JSONB),
        sa.Column("item_count", sa.Integer, nullable=False),
        _pair_key(s),
        _child_fk(s, "subject_id", "subjects"),
        _one_of(s, "mode", PRACTICE_MODES),
        _one_of(s, "status", PRACTICE_STATUSES),
        sa.CheckConstraint("(status = 'open') = (ended_at IS NULL)", name=f"ck_{s}_ended_at"),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name=f"ck_{s}_ended_after_start"
        ),
        sa.CheckConstraint(
            "(status = 'closed') = (summary IS NOT NULL)"
            " AND coalesce(jsonb_typeof(summary) = 'object', true)",
            name=f"ck_{s}_summary",
        ),
        sa.CheckConstraint(
            f"self_rating BETWEEN {rating_low} AND {rating_high}"
            " AND status = 'closed' OR self_rating IS NULL",
            name=f"ck_{s}_self_rating",
        ),
        sa.CheckConstraint("item_count >= 0", name=f"ck_{s}_item_count"),
    )
    i = "practice_items"
    score_low, score_high = SCORE_RANGE
    op.create_table(
        i,
        _id(),
        _user_id(),
        sa.Column("session_id", sa.Uuid, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("instance_id", sa.Uuid, nullable=False),
        sa.Column("draw_seed", sa.BigInteger, nullable=False),
        sa.Column("draw", JSONB, nullable=False),
        _ts("shown_at", nullable=True),
        sa.Column("answer", JSONB),
        sa.Column("correct", sa.Boolean),
        sa.Column("score", sa.SmallInteger),
        _ts("answered_at", nullable=True),
        sa.Column("source", sa.Text),
        _pair_key(i),
        _child_fk(i, "session_id", "practice_sessions"),
        # NO ACTION, deferred to commit: a subject cascade reaches the instances directly but
        # the showings only through their sessions, one level deeper, so a check at the end
        # of the instance delete would still see them. Nothing else deletes an instance.
        sa.ForeignKeyConstraint(
            ["instance_id", "user_id"],
            ["template_instances.id", "template_instances.user_id"],
            name=f"{i}_instance_id_user_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint("session_id", "seq", name=f"{i}_session_seq_key"),
        sa.CheckConstraint("seq >= 1", name=f"ck_{i}_seq"),
        sa.CheckConstraint(
            "jsonb_typeof(draw) = 'object'"
            " AND jsonb_typeof(draw -> 'entries') = 'array'"
            " AND jsonb_typeof(draw -> 'order') = 'array'",
            name=f"ck_{i}_draw",
        ),
        sa.CheckConstraint(
            "answered_at IS NULL OR (shown_at IS NOT NULL AND answered_at >= shown_at)",
            name=f"ck_{i}_shown_before_answer",
        ),
        # An answer is all or nothing: answer, score, answered_at and source together; correct
        # only with them (null for a self-report).
        sa.CheckConstraint(
            "(answered_at IS NULL) = (answer IS NULL)"
            " AND (answered_at IS NULL) = (score IS NULL)"
            " AND (answered_at IS NULL) = (source IS NULL)"
            " AND (answered_at IS NOT NULL OR correct IS NULL)",
            name=f"ck_{i}_answer_complete",
        ),
        sa.CheckConstraint(
            "coalesce(jsonb_typeof(answer) = 'object', true)", name=f"ck_{i}_answer"
        ),
        _between(i, "score", score_low, score_high),
        _one_of(i, "source", ITEM_SOURCES),
    )
    # A session's plan is nulled, not cascaded, if a plan ever goes (only the subject cascade
    # removes plans today); nulling only the column keeps user_id (slice A plan ruling 18).
    op.execute(
        "ALTER TABLE practice_sessions ADD CONSTRAINT practice_sessions_plan_id_user_id_fkey"
        " FOREIGN KEY (plan_id, user_id) REFERENCES practice_plans (id, user_id)"
        " ON DELETE SET NULL (plan_id)"
    )


def _create_indexes() -> None:
    op.create_index(
        ACTIVE_PLAN_INDEX,
        "practice_plans",
        ["user_id", "subject_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    # One open practice session per user, independent of English lessons (ruling 30).
    op.create_index(
        OPEN_SESSION_INDEX,
        "practice_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index("ix_practice_sessions_subject", "practice_sessions", ["user_id", "subject_id"])
    op.create_index("ix_practice_sessions_plan", "practice_sessions", ["plan_id"])
    op.create_index("ix_practice_items_instance", "practice_items", ["instance_id"])


def _enable_rls() -> None:
    own = f"user_id = {CURRENT_USER_ID}"
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_own_rows ON {table} USING ({own}) WITH CHECK ({own})")


def _grant() -> None:
    # No DELETE on any of them: rows leave only through the subject or user cascade, which runs
    # as the table owner. instance_id, draw_seed, draw, seq and session_id never change.
    op.execute("GRANT SELECT, INSERT ON practice_plans TO tutor_app")
    op.execute(f"GRANT UPDATE ({PLANS_UPDATE_COLUMNS}) ON practice_plans TO tutor_app")
    op.execute("GRANT SELECT, INSERT ON practice_sessions TO tutor_app")
    op.execute(f"GRANT UPDATE ({SESSIONS_UPDATE_COLUMNS}) ON practice_sessions TO tutor_app")
    op.execute("GRANT SELECT, INSERT ON practice_items TO tutor_app")
    op.execute(f"GRANT UPDATE ({ITEMS_UPDATE_COLUMNS}) ON practice_items TO tutor_app")
