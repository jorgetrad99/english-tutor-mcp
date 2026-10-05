"""Core schema: tables, the tutor_app role, grants and row-level security.

Spec section 5 as amended by rulings 3 (users.mcp_first_seen_at), 4 (UI preferences and
timezone on users), 5 (SET LOCAL ROLE tutor_app) and 7 (review_logs.state_before).

Revision ID: 0001
Revises:
Create Date: 2026-10-19
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# An unset setting reads as NULL or '' (after a SET LOCAL ends); nullif makes both NULL,
# and a NULL comparison never matches.
CURRENT_USER_ID = "nullif(current_setting('app.user_id', true), '')::uuid"
CURRENT_GOOGLE_SUB = "nullif(current_setting('app.google_sub', true), '')"
CURRENT_WEB_SESSION = "nullif(current_setting('app.web_session', true), '')"

LEVELS = "('B1', 'B1+', 'B2', 'B2+', 'C1')"

# Tables whose rows belong to user_id (one "own rows" policy each).
OWNED_TABLES = (
    "profiles",
    "plans",
    "plan_items",
    "sessions",
    "session_metrics",
    "session_errors",
    "glossary_items",
    "review_states",
    "review_logs",
    "audit_log",
)
RLS_TABLES = ("users", *OWNED_TABLES, "web_sessions")
# Creation order; downgrade drops in reverse.
ALL_TABLES = (
    "users",
    "profiles",
    "track_items",
    "track_chunks",
    "plans",
    "plan_items",
    "sessions",
    "session_metrics",
    "session_errors",
    "glossary_items",
    "review_states",
    "review_logs",
    "audit_log",
    "web_sessions",
)
# tutor_app gets full DML on these; users, review_logs and audit_log are narrower (_grant).
DML_TABLES = (
    "profiles",
    "plans",
    "plan_items",
    "sessions",
    "session_metrics",
    "session_errors",
    "glossary_items",
    "review_states",
    "web_sessions",
)


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _user_id(*, nullable: bool = False, ondelete: str = "CASCADE") -> sa.Column[Any]:
    return sa.Column(
        "user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete=ondelete), nullable=nullable
    )


def _ts(name: str, *, nullable: bool = False, now: bool = False) -> sa.Column[Any]:
    default = sa.text("now()") if now else None
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=default)


def _max_len(table: str, column: str, limit: int) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"char_length({column}) <= {limit}", name=f"ck_{table}_{column}_len")


def _one_of(table: str, column: str, values: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} IN {values}", name=f"ck_{table}_{column}")


def upgrade() -> None:
    _create_role()
    _create_tables()
    _enable_rls()
    _grant()


def downgrade() -> None:
    # The role is cluster-wide and may serve other databases (db-test, dev): it is kept.
    for table in reversed(ALL_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("REVOKE USAGE ON SCHEMA public FROM tutor_app")


def _create_role() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tutor_app') THEN
                CREATE ROLE tutor_app NOLOGIN;
            END IF;
            IF NOT pg_has_role(current_user, 'tutor_app', 'SET') THEN
                EXECUTE 'GRANT tutor_app TO ' || quote_ident(current_user);
            END IF;
        END
        $$;
        """
    )


def _create_tables() -> None:
    op.create_table(
        "users",
        _id(),
        sa.Column("google_sub", sa.Text, nullable=False),
        sa.Column("email", sa.Text),
        sa.Column("display_name", sa.Text, nullable=False, server_default=""),
        sa.Column("native_lang", sa.Text, nullable=False, server_default="es"),
        sa.Column("role", sa.Text, nullable=False, server_default="learner"),
        sa.Column("lang", sa.Text, nullable=False, server_default="es_MX"),
        sa.Column("timezone", sa.Text, nullable=False, server_default="America/Mexico_City"),
        sa.Column("reduce_motion", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _ts("install_prompt_dismissed_at", nullable=True),
        sa.Column("last_celebrated_session_id", sa.Uuid),
        _ts("deletion_requested_at", nullable=True),
        sa.Column("email_weekly", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("email_reminders", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _ts("mcp_first_seen_at", nullable=True),
        _ts("created_at", now=True),
        _ts("deleted_at", nullable=True),
        sa.UniqueConstraint("google_sub", name="users_google_sub_key"),
        _one_of("users", "role", "('learner', 'admin')"),
        _one_of("users", "lang", "('es_MX', 'en')"),
        _max_len("users", "display_name", 200),
        _max_len("users", "timezone", 64),
    )
    op.create_table(
        "profiles",
        sa.Column(
            "user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("domains", ARRAY(sa.Text), nullable=False),
        sa.Column("use_cases", ARRAY(sa.Text), nullable=False),
        sa.Column("goal_text", sa.Text),
        sa.Column("minutes_per_day", sa.SmallInteger, nullable=False),
        sa.Column("days_per_week", sa.SmallInteger, nullable=False),
        sa.Column("self_level", sa.Text, nullable=False),
        sa.Column("target_level", sa.Text, nullable=False),
        sa.Column("target_date", sa.Date),
        _ts("onboarded_at"),
        _ts("updated_at"),
        _max_len("profiles", "goal_text", 300),
        _one_of("profiles", "minutes_per_day", "(15, 20, 30)"),
        sa.CheckConstraint("days_per_week BETWEEN 2 AND 7", name="ck_profiles_days_per_week"),
        _one_of("profiles", "self_level", LEVELS),
        _one_of("profiles", "target_level", LEVELS),
        sa.CheckConstraint("cardinality(domains) >= 1", name="ck_profiles_domains"),
        sa.CheckConstraint("cardinality(use_cases) BETWEEN 1 AND 4", name="ck_profiles_use_cases"),
    )
    op.create_table(
        "track_items",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("domain", sa.Text, nullable=False),
        sa.Column("order_no", sa.Integer, nullable=False),
        sa.Column("cefr", sa.Text, nullable=False),
        sa.Column("can_do_en", sa.Text, nullable=False),
        sa.Column("can_do_es", sa.Text, nullable=False),
        sa.Column("skill", sa.Text, nullable=False),
        sa.Column("interaction_type", sa.Text, nullable=False),
        sa.Column("use_cases", ARRAY(sa.Text), nullable=False),
        sa.Column("character", sa.Text, nullable=False),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("obstacle", sa.Text, nullable=False),
        sa.Column("scenario_hint", sa.Text, nullable=False),
        sa.UniqueConstraint("domain", "order_no", name="track_items_domain_order_key"),
        _one_of("track_items", "cefr", "('B1', 'B2')"),
        _one_of("track_items", "skill", "('speaking', 'writing')"),
        _one_of(
            "track_items",
            "interaction_type",
            "('explain', 'negotiate', 'disagree', 'ask_for_help', 'give_feedback', 'small_talk')",
        ),
    )
    op.create_table(
        "track_chunks",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "track_item_id",
            sa.Text,
            sa.ForeignKey("track_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("example", sa.Text, nullable=False),
        sa.UniqueConstraint("track_item_id", "position", name="track_chunks_item_position_key"),
        sa.CheckConstraint("position BETWEEN 1 AND 5", name="ck_track_chunks_position"),
    )
    op.create_table(
        "plans",
        _id(),
        _user_id(),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        _ts("generated_at"),
        sa.Column("rationale", JSONB, nullable=False),
        sa.UniqueConstraint("user_id", "version", name="plans_user_version_key"),
        sa.CheckConstraint("version >= 1", name="ck_plans_version"),
        _one_of("plans", "status", "('active', 'superseded')"),
    )
    op.create_index(
        "plans_one_active_per_user",
        "plans",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "plan_items",
        _id(),
        sa.Column(
            "plan_id", sa.Uuid, sa.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
        ),
        _user_id(),
        sa.Column("week_no", sa.Integer, nullable=False),
        sa.Column("order_no", sa.Integer, nullable=False),
        sa.Column("track_item_id", sa.Text, sa.ForeignKey("track_items.id"), nullable=False),
        sa.Column("variant", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        # A pointer, not a foreign key: sessions also reference plan_items.
        sa.Column("done_session_id", sa.Uuid),
        sa.CheckConstraint("week_no >= 1 AND order_no >= 1", name="ck_plan_items_slot"),
        _one_of("plan_items", "variant", "('base', 'complication')"),
        _one_of("plan_items", "status", "('pending', 'done', 'skipped')"),
    )
    op.create_index("ix_plan_items_plan", "plan_items", ["plan_id"])
    op.create_index("ix_plan_items_user_track", "plan_items", ["user_id", "track_item_id"])
    op.create_table(
        "sessions",
        _id(),
        _user_id(),
        sa.Column("plan_item_id", sa.Uuid, sa.ForeignKey("plan_items.id", ondelete="SET NULL")),
        sa.Column("track_item_id", sa.Text, sa.ForeignKey("track_items.id"), nullable=False),
        sa.Column("prep_text", sa.Text),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("client", sa.Text, nullable=False),
        _ts("started_at"),
        _ts("ended_at", nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("low_trust", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("brief_variant", sa.Text, nullable=False),
        sa.Column("chunks_offered", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("task_result", sa.Text),
        sa.Column("hints_given", sa.Integer),
        sa.Column("cefr_estimate_speaking", sa.Text),
        sa.Column("cefr_confidence", sa.Text),
        sa.Column("cefr_excluded", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("confidence_1_5", sa.SmallInteger),
        sa.Column("raw_evidence", JSONB),
        sa.Column("result", JSONB),
        _max_len("sessions", "prep_text", 300),
        _one_of("sessions", "mode", "('voice', 'text')"),
        _one_of("sessions", "client", "('claude', 'chatgpt', 'code', 'unknown')"),
        _one_of("sessions", "status", "('open', 'closed', 'incomplete')"),
        _one_of("sessions", "brief_variant", "('base', 'complication', 'simpler')"),
        _one_of("sessions", "task_result", "('achieved', 'partial', 'not_achieved')"),
        _one_of("sessions", "cefr_estimate_speaking", LEVELS),
        _one_of("sessions", "cefr_confidence", "('low', 'medium', 'high')"),
        sa.CheckConstraint("hints_given >= 0", name="ck_sessions_hints_given"),
        sa.CheckConstraint("confidence_1_5 BETWEEN 1 AND 5", name="ck_sessions_confidence_1_5"),
        # Backstop only; the service enforces the 20 KB raw_evidence cap (spec section 5).
        sa.CheckConstraint(
            "octet_length(raw_evidence::text) <= 65536", name="ck_sessions_raw_evidence_len"
        ),
    )
    op.create_index(
        "sessions_one_open_per_user",
        "sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index("ix_sessions_user_started", "sessions", ["user_id", "started_at"])
    op.create_table(
        "session_metrics",
        sa.Column(
            "session_id",
            sa.Uuid,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _user_id(),
        sa.Column("user_words", sa.Integer, nullable=False),
        sa.Column("assistant_words_estimate", sa.Integer),
        sa.Column("user_ratio", sa.Double),
        sa.Column("turns", sa.Integer, nullable=False),
        sa.Column("words_per_turn", sa.Double, nullable=False),
        sa.Column("duration_min", sa.Double, nullable=False),
        sa.Column("user_words_per_min", sa.Double, nullable=False),
        sa.Column("errors_total", sa.Integer, nullable=False),
        sa.Column("errors_rejected", sa.Integer, nullable=False),
        sa.Column("errors_by_category", JSONB, nullable=False),
        sa.Column("errors_per_100w", sa.Double, nullable=False),
        sa.Column("recurring_errors", sa.Integer, nullable=False),
        sa.Column("uptake_count", sa.Integer, nullable=False),
        sa.Column("chunks_offered", sa.Integer, nullable=False),
        sa.Column("chunks_used", sa.Integer, nullable=False),
        sa.Column("chunks_rejected", sa.Integer, nullable=False),
        sa.Column("activation_rate", sa.Double, nullable=False),
        sa.Column("unique_lemmas", sa.Integer),
        sa.Column("lexical_diversity", sa.Double),
        sa.Column("l1_switches", sa.Integer),
    )
    op.create_table(
        "session_errors",
        _id(),
        sa.Column(
            "session_id", sa.Uuid, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
        ),
        _user_id(),
        sa.Column("said", sa.Text, nullable=False),
        sa.Column("correct", sa.Text, nullable=False),
        sa.Column("correct_norm", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("turn_index", sa.Integer, nullable=False),
        _max_len("session_errors", "said", 300),
        _max_len("session_errors", "correct", 300),
        _one_of(
            "session_errors", "category", "('grammar', 'lexis', 'word_order', 'register', 'other')"
        ),
        sa.CheckConstraint("turn_index >= 0", name="ck_session_errors_turn_index"),
    )
    op.create_index("ix_session_errors_session", "session_errors", ["session_id"])
    op.create_index("ix_session_errors_user_norm", "session_errors", ["user_id", "correct_norm"])
    op.create_table(
        "glossary_items",
        _id(),
        _user_id(),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("text_norm", sa.Text, nullable=False),
        sa.Column("meaning", sa.Text, nullable=False),
        sa.Column("context_sentence", sa.Text, nullable=False),
        sa.Column("domain", sa.Text, nullable=False),
        # Pointers, not foreign keys: a glossary item outlives nothing it points to.
        sa.Column("origin_session_id", sa.Uuid),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("seen_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_seen_session_id", sa.Uuid),
        sa.Column("leech", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _ts("provisional_expires_at", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("user_id", "text_norm", name="glossary_items_user_text_norm_key"),
        _one_of("glossary_items", "kind", "('correction', 'chunk', 'term')"),
        _one_of("glossary_items", "status", "('provisional', 'confirmed', 'declined', 'archived')"),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 120", name="ck_glossary_items_text_len"
        ),
        sa.CheckConstraint("char_length(text_norm) >= 1", name="ck_glossary_items_text_norm"),
        _max_len("glossary_items", "meaning", 200),
        _max_len("glossary_items", "context_sentence", 300),
        sa.CheckConstraint("seen_count >= 1", name="ck_glossary_items_seen_count"),
    )
    op.create_index("ix_glossary_items_user_status", "glossary_items", ["user_id", "status"])
    op.create_table(
        "review_states",
        sa.Column(
            "glossary_item_id",
            sa.Uuid,
            sa.ForeignKey("glossary_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _user_id(),
        sa.Column("stability", sa.Double),
        sa.Column("difficulty", sa.Double),
        _ts("due_at"),
        _ts("last_review_at", nullable=True),
        sa.Column("reps", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lapses", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "last_ratings",
            ARRAY(sa.SmallInteger),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.CheckConstraint("cardinality(last_ratings) <= 2", name="ck_review_states_last_ratings"),
    )
    op.create_index("ix_review_states_user_due", "review_states", ["user_id", "due_at"])
    op.create_table(
        "review_logs",
        _id(),
        sa.Column(
            "glossary_item_id",
            sa.Uuid,
            sa.ForeignKey("glossary_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user_id(),
        sa.Column(
            "session_id", sa.Uuid, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("rating", sa.SmallInteger, nullable=False),
        _ts("reviewed_at"),
        sa.Column("elapsed_days", sa.Double, nullable=False, server_default="0"),
        sa.Column("state_before", JSONB, nullable=False),
        sa.UniqueConstraint("session_id", "glossary_item_id", name="review_logs_session_item_key"),
        sa.CheckConstraint("rating BETWEEN 1 AND 4", name="ck_review_logs_rating"),
    )
    op.create_index(
        "ix_review_logs_item_reviewed", "review_logs", ["glossary_item_id", "reviewed_at"]
    )
    op.create_table(
        "audit_log",
        _id(),
        # SET NULL, not CASCADE: the deletion script keeps audit rows with a hashed id.
        _user_id(nullable=True, ondelete="SET NULL"),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("meta", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("at"),
        sa.CheckConstraint("char_length(event) BETWEEN 1 AND 64", name="ck_audit_log_event"),
    )
    op.create_index("ix_audit_log_user_at", "audit_log", ["user_id", "at"])
    op.create_table(
        "web_sessions",
        sa.Column("token_hash", sa.Text, primary_key=True),
        _user_id(nullable=True),
        sa.Column("csrf_token", sa.Text, nullable=False),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at"),
        _ts("last_seen_at"),
        sa.CheckConstraint("char_length(token_hash) = 64", name="ck_web_sessions_token_hash"),
    )
    op.create_index("ix_web_sessions_user", "web_sessions", ["user_id"])


def _enable_rls() -> None:
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    own = f"user_id = {CURRENT_USER_ID}"
    for table in OWNED_TABLES:
        op.execute(f"CREATE POLICY {table}_own_rows ON {table} USING ({own}) WITH CHECK ({own})")
    # Ruling 5: the second branch lets PgIdentity resolve a sub before app.user_id is known.
    self_row = f"id = {CURRENT_USER_ID} OR google_sub = {CURRENT_GOOGLE_SUB}"
    op.execute(f"CREATE POLICY users_self ON users USING ({self_row}) WITH CHECK ({self_row})")
    # The cookie's token hash is the capability: the middleware loads a session by hash before
    # it knows the user, and a "user_id IS NULL" branch would expose every anonymous session's
    # OAuth state to any query while still failing to load a logged-in one.
    holder = f"token_hash = {CURRENT_WEB_SESSION} OR user_id = {CURRENT_USER_ID}"
    op.execute(
        f"CREATE POLICY web_sessions_holder ON web_sessions USING ({holder}) WITH CHECK ({holder})"
    )


def _grant() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO tutor_app")
    op.execute("GRANT SELECT ON track_items, track_chunks TO tutor_app")
    op.execute("GRANT SELECT, INSERT ON users TO tutor_app")
    # id, google_sub, role and created_at are never writable by the app.
    op.execute(
        "GRANT UPDATE (email, display_name, lang, timezone, reduce_motion,"
        " install_prompt_dismissed_at, last_celebrated_session_id, deletion_requested_at,"
        " email_weekly, email_reminders, mcp_first_seen_at) ON users TO tutor_app"
    )
    for table in DML_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO tutor_app")
    op.execute("GRANT SELECT, INSERT, UPDATE (rating) ON review_logs TO tutor_app")
    op.execute("GRANT SELECT, INSERT ON audit_log TO tutor_app")  # append-only
