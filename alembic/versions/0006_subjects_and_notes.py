"""Subjects and notes: ten user-scoped tables, full-text search and the concept graph.

Subjects spec section 8.1 (slice A). Extensions `unaccent` and `pg_trgm` (trusted, no superuser
needed); text search configurations `tutor_es` and `tutor_en` with `unaccent` before the
stemmer; every table with `user_id` (CASCADE), forced row-level security, an own-rows policy,
a CHECK per closed enum and per cap, `UNIQUE (id, user_id)` on every parent and composite
`(col, user_id)` foreign keys on every child (0002 pattern). `raw_text` and its companions are
never app-writable (column-level UPDATE grant on `notes`); `note_passages` are written once.
The report role gets nothing.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02
"""

import os
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_USER_ID = "nullif(current_setting('app.user_id', true), '')::uuid"

# The downgrade drops every table below: it runs only when `notes` and `subjects` are empty (as
# seen by a role that bypasses RLS) or when the operator sets this variable to "1".
DESTRUCTIVE_DOWNGRADE_FLAG = "TUTOR_ALLOW_DESTRUCTIVE_DOWNGRADE"

# Creation order; the downgrade drops in reverse.
TABLES = (
    "subjects",
    "sources",
    "notes",
    "note_passages",
    "note_artifacts",
    "exercises",
    "quiz_items",
    "concepts",
    "note_concepts",
    "concept_links",
)
# Full DML for tutor_app; notes and note_passages are narrower (_grant).
DML_TABLES = (
    "subjects",
    "sources",
    "note_artifacts",
    "exercises",
    "quiz_items",
    "concepts",
    "note_concepts",
    "concept_links",
)
# Everything on notes the app may change after the insert: raw_text, raw_sha256, raw_had_nul,
# idempotency_key, origin and created_at stay as received (spec 4.1, plan ruling 9).
NOTES_UPDATE_COLUMNS = (
    "title, lesson_ref, position, clean_text, clean_version, title_norm, supersedes_note_id,"
    " updated_at"
)

# Composite foreign keys whose ON DELETE clause names the column to null (PostgreSQL 15+);
# SQLAlchemy's ForeignKeyConstraint accepts only the bare phrases, so these are raw DDL.
SET_NULL_REFERENCES = (
    ("notes", "source_id", "sources"),
    ("notes", "supersedes_note_id", "notes"),
    ("quiz_items", "concept_id", "concepts"),
)

# (configuration, base configuration, stemmer): unaccent runs before the stemmer for the
# accented token types; asciiword keeps the base mapping (nothing to strip).
TEXT_SEARCH_CONFIGURATIONS = (
    ("tutor_es", "spanish", "spanish_stem"),
    ("tutor_en", "english", "english_stem"),
)

SOURCE_KINDS = "('course', 'pdf', 'text', 'url', 'other')"
ORIGINS = "('mcp', 'web')"
ARTIFACT_KINDS = "('lesson_view', 'exercise_view', 'other')"
EXERCISE_KINDS = "('technique', 'repertoire', 'theory', 'ear', 'other')"
LINK_KINDS = "('prerequisite', 'related')"
PREFERENCES = "('practice_first', 'theory_first', 'balanced')"
SUBJECT_STATUSES = "('active', 'paused', 'archived')"
SUBJECT_EXERCISE_KINDS = "ARRAY['quiz', 'ear', 'exercise']"

# Caps (domain constants restated; the MCP schemas carry the same numbers).
NAME_MAX = 80
GOAL_TEXT_MAX = 300
CONTEXT_TEXT_MAX = 600
TITLE_MAX = 200
LESSON_REF_MAX = 120
URL_MAX = 2000
WEB_NOTE_CHARS = 400_000
CONCEPT_NAME_MAX = 80
EXERCISE_NAME_MAX = 120
INSTRUCTIONS_MAX = 2000
QUESTION_MAX = 500
OPTION_MAX = 200
MAX_OPTIONS = 5
EXPLANATION_MAX = 500
ARTIFACT_TITLE_MAX = 200
# 48 KiB: the app rejects request bodies over 64 KB, so a larger document never arrives.
ARTIFACT_BYTES_MAX = 49_152


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _user_id() -> sa.Column[Any]:
    return sa.Column(
        "user_id", sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


def _ts(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _max_len(table: str, column: str, limit: int) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"char_length({column}) <= {limit}", name=f"ck_{table}_{column}_len")


def _len_between(table: str, column: str, low: int, high: int) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"char_length({column}) BETWEEN {low} AND {high}", name=f"ck_{table}_{column}_len"
    )


def _one_of(table: str, column: str, values: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} IN {values}", name=f"ck_{table}_{column}")


def _pair_key(table: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint("id", "user_id", name=f"{table}_id_user_id_key")


def _child_fk(child: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column, "user_id"],
        [f"{parent}.id", f"{parent}.user_id"],
        name=f"{child}_{column}_user_id_fkey",
        ondelete="CASCADE",
    )


def upgrade() -> None:
    _create_extensions_and_configurations()
    _create_tables()
    _set_null_references()
    _create_indexes()
    _enable_rls()
    _grant()


def downgrade() -> None:
    _refuse_to_drop_learner_data()
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for name, _, _ in TEXT_SEARCH_CONFIGURATIONS:
        op.execute(f"DROP TEXT SEARCH CONFIGURATION IF EXISTS {name}")
    # The extensions stay: harmless, and possibly shared with other schemas.


def _refuse_to_drop_learner_data() -> None:
    if os.environ.get(DESTRUCTIVE_DOWNGRADE_FLAG) == "1":
        return
    conn = op.get_bind()
    # Under FORCE RLS a role that does not bypass it sees no rows, so an empty count proves
    # nothing; only a superuser or BYPASSRLS role can tell the tables are really empty.
    sees_all = conn.execute(
        sa.text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).scalar_one()
    has_data = conn.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM notes) OR EXISTS (SELECT 1 FROM subjects)")
    ).scalar_one()
    if has_data or not sees_all:
        reason = "notes or subjects hold rows" if has_data else "this role cannot see past RLS"
        raise RuntimeError(
            f"Refusing to downgrade 0006: it drops ten tables of learner data and {reason}."
            f" Take a backup, then rerun with {DESTRUCTIVE_DOWNGRADE_FLAG}=1 to drop them."
        )


def _create_extensions_and_configurations() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, base, stemmer in TEXT_SEARCH_CONFIGURATIONS:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = '{name}') THEN
                    CREATE TEXT SEARCH CONFIGURATION {name} (COPY = {base});
                    ALTER TEXT SEARCH CONFIGURATION {name}
                        ALTER MAPPING FOR hword, hword_part, word
                        WITH unaccent, {stemmer};
                END IF;
            END
            $$;
            """  # noqa: S608 - names from TEXT_SEARCH_CONFIGURATIONS
        )


def _create_tables() -> None:
    op.create_table(
        "subjects",
        _id(),
        _user_id(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("goal_text", sa.Text),
        sa.Column("context_text", sa.Text),
        sa.Column("preference", sa.Text, nullable=False),
        sa.Column("exercise_kinds", ARRAY(sa.Text), nullable=False),
        sa.Column("minutes_per_session", sa.SmallInteger, nullable=False),
        sa.Column("sessions_per_week", sa.SmallInteger, nullable=False),
        sa.Column("target_date", sa.Date),
        sa.Column("status", sa.Text, nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        _pair_key("subjects"),
        _len_between("subjects", "name", 1, NAME_MAX),
        _max_len("subjects", "goal_text", GOAL_TEXT_MAX),
        _max_len("subjects", "context_text", CONTEXT_TEXT_MAX),
        _one_of("subjects", "preference", PREFERENCES),
        sa.CheckConstraint(
            f"cardinality(exercise_kinds) BETWEEN 1 AND 3"
            f" AND exercise_kinds <@ {SUBJECT_EXERCISE_KINDS}",
            name="ck_subjects_exercise_kinds",
        ),
        sa.CheckConstraint(
            "minutes_per_session BETWEEN 10 AND 120", name="ck_subjects_minutes_per_session"
        ),
        sa.CheckConstraint(
            "sessions_per_week BETWEEN 1 AND 7", name="ck_subjects_sessions_per_week"
        ),
        _one_of("subjects", "status", SUBJECT_STATUSES),
    )
    op.create_table(
        "sources",
        _id(),
        _user_id(),
        sa.Column("subject_id", sa.Uuid, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text),
        _ts("created_at"),
        _pair_key("sources"),
        _child_fk("sources", "subject_id", "subjects"),
        sa.UniqueConstraint(
            "user_id", "subject_id", "kind", "title", name="sources_user_subject_kind_title_key"
        ),
        _one_of("sources", "kind", SOURCE_KINDS),
        _len_between("sources", "title", 1, TITLE_MAX),
        _max_len("sources", "url", URL_MAX),
    )
    op.create_table(
        "notes",
        _id(),
        _user_id(),
        sa.Column("subject_id", sa.Uuid, nullable=False),
        sa.Column("source_id", sa.Uuid),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("title_norm", sa.Text, nullable=False),
        sa.Column("lesson_ref", sa.Text),
        sa.Column("position", sa.Integer),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("raw_sha256", sa.Text, nullable=False),
        sa.Column("raw_had_nul", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("clean_text", sa.Text, nullable=False),
        sa.Column("clean_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("supersedes_note_id", sa.Uuid),
        _ts("created_at"),
        _ts("updated_at"),
        _pair_key("notes"),
        _child_fk("notes", "subject_id", "subjects"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="notes_user_idempotency_key_key"),
        _len_between("notes", "title", 1, TITLE_MAX),
        sa.CheckConstraint("char_length(title_norm) >= 1", name="ck_notes_title_norm"),
        _max_len("notes", "lesson_ref", LESSON_REF_MAX),
        sa.CheckConstraint("position >= 0", name="ck_notes_position"),
        _len_between("notes", "raw_text", 1, WEB_NOTE_CHARS),
        sa.CheckConstraint("char_length(raw_sha256) = 64", name="ck_notes_raw_sha256"),
        sa.CheckConstraint("char_length(clean_text) >= 1", name="ck_notes_clean_text"),
        sa.CheckConstraint("clean_version >= 1", name="ck_notes_clean_version"),
        sa.CheckConstraint("char_length(idempotency_key) = 64", name="ck_notes_idempotency_key"),
        _one_of("notes", "origin", ORIGINS),
    )
    op.create_table(
        "note_passages",
        sa.Column("note_id", sa.Uuid, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        _user_id(),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "tsv",
            TSVECTOR,
            sa.Computed(
                "to_tsvector('tutor_es', text) || to_tsvector('tutor_en', text)", persisted=True
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("note_id", "seq", name="note_passages_pkey"),
        _child_fk("note_passages", "note_id", "notes"),
        sa.CheckConstraint("seq >= 1", name="ck_note_passages_seq"),
        sa.CheckConstraint("char_length(text) >= 1", name="ck_note_passages_text"),
    )
    op.create_table(
        "note_artifacts",
        _id(),
        _user_id(),
        sa.Column("note_id", sa.Uuid, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("html", sa.Text, nullable=False),
        sa.Column("html_sha256", sa.Text, nullable=False),
        sa.Column("bytes", sa.Integer, nullable=False),
        _ts("created_at"),
        _pair_key("note_artifacts"),
        _child_fk("note_artifacts", "note_id", "notes"),
        _one_of("note_artifacts", "kind", ARTIFACT_KINDS),
        _len_between("note_artifacts", "title", 1, ARTIFACT_TITLE_MAX),
        sa.CheckConstraint("char_length(html) >= 1", name="ck_note_artifacts_html"),
        sa.CheckConstraint("char_length(html_sha256) = 64", name="ck_note_artifacts_html_sha256"),
        sa.CheckConstraint(
            f"bytes BETWEEN 1 AND {ARTIFACT_BYTES_MAX}", name="ck_note_artifacts_bytes"
        ),
    )
    op.create_table(
        "exercises",
        _id(),
        _user_id(),
        sa.Column("note_id", sa.Uuid, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("instructions", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("tempo_bpm", sa.SmallInteger),
        sa.Column("duration_min", sa.SmallInteger),
        _child_fk("exercises", "note_id", "notes"),
        sa.UniqueConstraint("note_id", "seq", name="exercises_note_seq_key"),
        sa.CheckConstraint("seq >= 1", name="ck_exercises_seq"),
        _len_between("exercises", "name", 1, EXERCISE_NAME_MAX),
        _len_between("exercises", "instructions", 1, INSTRUCTIONS_MAX),
        _one_of("exercises", "kind", EXERCISE_KINDS),
        sa.CheckConstraint("tempo_bpm BETWEEN 20 AND 300", name="ck_exercises_tempo_bpm"),
        sa.CheckConstraint("duration_min BETWEEN 1 AND 120", name="ck_exercises_duration_min"),
    )
    op.create_table(
        "quiz_items",
        _id(),
        _user_id(),
        sa.Column("note_id", sa.Uuid, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("options", ARRAY(sa.Text), nullable=False),
        sa.Column("answer_index", sa.SmallInteger, nullable=False),
        sa.Column("explanation", sa.Text),
        sa.Column("concept_id", sa.Uuid),
        _child_fk("quiz_items", "note_id", "notes"),
        sa.UniqueConstraint("note_id", "seq", name="quiz_items_note_seq_key"),
        sa.CheckConstraint("seq >= 1", name="ck_quiz_items_seq"),
        _len_between("quiz_items", "question", 1, QUESTION_MAX),
        # 2-5 options of at most 200 characters each: the total is the backstop the database
        # can check without a function.
        sa.CheckConstraint(
            f"cardinality(options) BETWEEN 2 AND {MAX_OPTIONS}"
            f" AND char_length(array_to_string(options, '')) <= {MAX_OPTIONS * OPTION_MAX}",
            name="ck_quiz_items_options",
        ),
        sa.CheckConstraint(
            "answer_index >= 0 AND answer_index < cardinality(options)",
            name="ck_quiz_items_answer_index",
        ),
        _max_len("quiz_items", "explanation", EXPLANATION_MAX),
    )
    op.create_table(
        "concepts",
        _id(),
        _user_id(),
        sa.Column("subject_id", sa.Uuid, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("name_norm", sa.Text, nullable=False),
        _pair_key("concepts"),
        _child_fk("concepts", "subject_id", "subjects"),
        sa.UniqueConstraint(
            "user_id", "subject_id", "name_norm", name="concepts_user_subject_name_norm_key"
        ),
        _len_between("concepts", "name", 1, CONCEPT_NAME_MAX),
        sa.CheckConstraint("char_length(name_norm) >= 1", name="ck_concepts_name_norm"),
    )
    op.create_table(
        "note_concepts",
        sa.Column("note_id", sa.Uuid, nullable=False),
        sa.Column("concept_id", sa.Uuid, nullable=False),
        _user_id(),
        sa.PrimaryKeyConstraint("note_id", "concept_id", name="note_concepts_pkey"),
        _child_fk("note_concepts", "note_id", "notes"),
        _child_fk("note_concepts", "concept_id", "concepts"),
    )
    op.create_table(
        "concept_links",
        sa.Column("from_id", sa.Uuid, nullable=False),
        sa.Column("to_id", sa.Uuid, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        _user_id(),
        sa.PrimaryKeyConstraint("from_id", "to_id", "kind", name="concept_links_pkey"),
        _child_fk("concept_links", "from_id", "concepts"),
        _child_fk("concept_links", "to_id", "concepts"),
        _one_of("concept_links", "kind", LINK_KINDS),
        sa.CheckConstraint("from_id <> to_id", name="ck_concept_links_no_self"),
    )


def _set_null_references() -> None:
    for child, column, parent in SET_NULL_REFERENCES:
        op.execute(
            f"ALTER TABLE {child} ADD CONSTRAINT {child}_{column}_user_id_fkey"
            f" FOREIGN KEY ({column}, user_id) REFERENCES {parent} (id, user_id)"
            f" ON DELETE SET NULL ({column})"
        )


def _create_indexes() -> None:
    op.create_index("ix_sources_subject", "sources", ["subject_id"])
    op.create_index("ix_notes_subject_source", "notes", ["subject_id", "source_id"])
    op.create_index("ix_notes_supersedes", "notes", ["supersedes_note_id"])
    op.create_index(
        "ix_notes_title_norm_trgm",
        "notes",
        ["title_norm"],
        postgresql_using="gin",
        postgresql_ops={"title_norm": "gin_trgm_ops"},
    )
    # Under row-level security the policy's user_id test is the only index-able qual (the text
    # search operator is not leakproof); the GIN index serves the owner and report paths.
    op.create_index("ix_note_passages_user", "note_passages", ["user_id"])
    op.create_index("ix_note_passages_tsv", "note_passages", ["tsv"], postgresql_using="gin")
    op.create_index("ix_note_artifacts_note", "note_artifacts", ["note_id"])
    op.create_index("ix_quiz_items_concept", "quiz_items", ["concept_id"])
    op.create_index(
        "ix_concepts_name_norm_trgm",
        "concepts",
        ["name_norm"],
        postgresql_using="gin",
        postgresql_ops={"name_norm": "gin_trgm_ops"},
    )
    op.create_index("ix_note_concepts_concept", "note_concepts", ["concept_id"])
    op.create_index("ix_concept_links_to", "concept_links", ["to_id"])


def _enable_rls() -> None:
    own = f"user_id = {CURRENT_USER_ID}"
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_own_rows ON {table} USING ({own}) WITH CHECK ({own})")


def _grant() -> None:
    for table in DML_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO tutor_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON notes TO tutor_app")
    op.execute(f"GRANT UPDATE ({NOTES_UPDATE_COLUMNS}) ON notes TO tutor_app")
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON note_passages TO tutor_app"
    )  # rebuilt, never edited
