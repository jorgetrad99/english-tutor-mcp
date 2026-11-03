"""Subjects and notes: nine user-scoped tables, full-text search, the concept graph and the
exercise templates.

Subjects spec section 8.1 (slice A) as amended by section 15.10. Extensions `unaccent` and
`pg_trgm` (trusted, no superuser needed); text search configurations `tutor_es` and `tutor_en`
with `unaccent` before the stemmer; every table with `user_id` (CASCADE), forced row-level
security, an own-rows policy, a CHECK per closed enum and per cap, `UNIQUE (id, user_id)` on
every parent and composite `(col, user_id)` foreign keys on every child (0002 pattern).
`raw_text` and its companions are never app-writable (column-level UPDATE grant on `notes`);
`note_passages` are written once. Templates and instances are never deleted by the app: a
template is retired (`status`, `retired_at`, `updated_at` are its only updatable columns) and
an instance likewise (`status`, `retired_at`; slice A plan ruling 28). The report role gets
nothing.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02
"""

import os
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

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
    "concepts",
    "note_concepts",
    "concept_links",
    "exercise_templates",
    "template_instances",
)
# Full DML for tutor_app; notes, note_passages and the two template tables are narrower (_grant).
DML_TABLES = (
    "subjects",
    "sources",
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
# Retiring is the only change to a stored template or instance (spec 15.10, plan ruling 28).
TEMPLATES_UPDATE_COLUMNS = "status, retired_at, updated_at"
INSTANCES_UPDATE_COLUMNS = "status, retired_at"

# Composite foreign keys whose ON DELETE clause names the column to null (PostgreSQL 15+);
# SQLAlchemy's ForeignKeyConstraint accepts only the bare phrases, so these are raw DDL.
# Same user, not same subject, as for _child_fk: the app enforces the subject (plan ruling 16).
# Nulling only the column keeps user_id intact (plan ruling 18).
SET_NULL_REFERENCES = (
    ("notes", "source_id", "sources"),
    ("notes", "supersedes_note_id", "notes"),
    ("exercise_templates", "supersedes_template_id", "exercise_templates"),
    ("template_instances", "note_id", "notes"),
    ("template_instances", "concept_id", "concepts"),
    ("template_instances", "supersedes_instance_id", "template_instances"),
)

# (configuration, base configuration, stemmer): unaccent runs before the stemmer for the
# accented token types; asciiword keeps the base mapping (nothing to strip).
TEXT_SEARCH_CONFIGURATIONS = (
    ("tutor_es", "spanish", "spanish_stem"),
    ("tutor_en", "english", "english_stem"),
)

SOURCE_KINDS = "('course', 'pdf', 'text', 'url', 'other')"
ORIGINS = "('mcp', 'web')"
LINK_KINDS = "('prerequisite', 'related')"
PREFERENCES = "('practice_first', 'theory_first', 'balanced')"
SUBJECT_STATUSES = "('active', 'paused', 'archived')"
SUBJECT_EXERCISE_KINDS = "ARRAY['quiz', 'drill', 'exercise']"
# Exercise templates (spec 15.3, 15.4; tutor.domain.templates restates every value).
TEMPLATE_PURPOSES = "('exercise', 'display')"
PRACTICE_KINDS = "('quiz', 'drill', 'exercise')"
GRADING_KINDS = (
    "('none', 'choice_index', 'exact_text', 'exact_set', 'exact_sequence', 'numeric',"
    " 'self_report')"
)
TEXT_GRADING_KINDS = "('exact_text', 'exact_set', 'exact_sequence')"
TEXT_MATCHES = "('fold_all', 'keep_accents', 'exact_case')"
POOL_DRAWS = "('none', 'one', 'one_of_choices', 'ordered')"
# (pool_draw, grading_kind) pairs a pool allows (spec 15.3 compatibility table).
POOL_DRAW_GRADING = (
    "(('one', 'exact_text'), ('one_of_choices', 'choice_index'), ('ordered', 'exact_sequence'))"
)
TEMPLATE_STATUSES = "('active', 'retired')"
INSTANCE_STATUSES = "('active', 'retired')"
INSTANCE_ORIGINS = "('ingest', 'class')"

# Caps (domain constants restated; the MCP schemas carry the same numbers).
NAME_MAX = 80
GOAL_TEXT_MAX = 300
CONTEXT_TEXT_MAX = 600
TITLE_MAX = 200
LESSON_REF_MAX = 120
URL_MAX = 2000
WEB_NOTE_CHARS = 400_000
CONCEPT_NAME_MAX = 80
TEMPLATE_NAME_MAX = 80
TEMPLATE_DESCRIPTION_MAX = 300
TEMPLATE_PARAMS_MAX = 8
# UTF-8 bytes of a template's HTML: the app rejects more (MAX_TEMPLATE_BYTES).
TEMPLATE_BYTES_MAX = 40_000
NUMERIC_TOLERANCE_MAX = 1_000_000
CHOICES_POOL_COUNT = (2, 8)
ORDERED_POOL_COUNT = (2, 12)
PARAM_NAME_PATTERN = "^[a-z][a-z0-9_]{0,31}$"
# An instance's parameters as JSON text, pool included (MAX_PARAMS_JSON_BYTES).
PARAMS_JSON_MAX = 32_768
PROMPT_TEXT_MAX = 500


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


# Composite (col, user_id) keys tie a child to a parent of the same user, not of the same
# subject. The app keeps references inside one subject: NoteRepo.insert checks the source's
# subject and resolves concepts (note_concepts, concept_links) in the note's subject,
# services.notes takes supersedes_note_id from the same subject, and TemplateRepo/InstanceRepo
# resolve the template, note, concept and replaced row in the instance's subject (plan ruling
# 16).
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
    _create_extensions_and_configurations()
    _create_tables()
    _create_template_tables()
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
            f"Refusing to downgrade 0006: it drops nine tables of learner data and {reason}."
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


def _create_template_tables() -> None:
    """Spec 15.10. The combination CHECKs restate tutor.domain.templates.validate_template, so
    a row the domain would refuse cannot be stored even by a buggy repository. A CHECK passes
    on NULL, so each combination is written to be true or false, never NULL."""
    t = "exercise_templates"
    choices_low, choices_high = CHOICES_POOL_COUNT
    ordered_low, ordered_high = ORDERED_POOL_COUNT
    op.create_table(
        t,
        _id(),
        _user_id(),
        sa.Column("subject_id", sa.Uuid, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("name_norm", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("template_purpose", sa.Text, nullable=False),
        sa.Column("practice_kind", sa.Text),
        sa.Column("grading_kind", sa.Text, nullable=False),
        sa.Column("text_match", sa.Text),
        sa.Column("numeric_tolerance", sa.Numeric),
        sa.Column("pool_draw", sa.Text, nullable=False),
        sa.Column("pool_count", sa.SmallInteger),
        sa.Column("needs_view", sa.Boolean, nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("options_param", sa.Text),
        sa.Column("html", sa.Text, nullable=False),
        sa.Column("html_sha256", sa.Text, nullable=False),
        sa.Column("bytes", sa.Integer, nullable=False),
        sa.Column("guide_version", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("supersedes_template_id", sa.Uuid),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("retired_at", nullable=True),
        _pair_key(t),
        _child_fk(t, "subject_id", "subjects"),
        _len_between(t, "name", 1, TEMPLATE_NAME_MAX),
        sa.CheckConstraint("char_length(name_norm) >= 1", name=f"ck_{t}_name_norm"),
        _max_len(t, "description", TEMPLATE_DESCRIPTION_MAX),
        _one_of(t, "template_purpose", TEMPLATE_PURPOSES),
        _one_of(t, "practice_kind", PRACTICE_KINDS),
        _one_of(t, "grading_kind", GRADING_KINDS),
        _one_of(t, "text_match", TEXT_MATCHES),
        _one_of(t, "pool_draw", POOL_DRAWS),
        _one_of(t, "status", TEMPLATE_STATUSES),
        sa.CheckConstraint(
            f"numeric_tolerance BETWEEN 0 AND {NUMERIC_TOLERANCE_MAX}",
            name=f"ck_{t}_numeric_tolerance",
        ),
        sa.CheckConstraint(
            f"jsonb_typeof(params) = 'array'"
            f" AND jsonb_array_length(params) BETWEEN 1 AND {TEMPLATE_PARAMS_MAX}",
            name=f"ck_{t}_params",
        ),
        sa.CheckConstraint(f"options_param ~ '{PARAM_NAME_PATTERN}'", name=f"ck_{t}_options_param"),
        sa.CheckConstraint("char_length(html_sha256) = 64", name=f"ck_{t}_html_sha256"),
        sa.CheckConstraint(
            f"bytes = octet_length(html) AND bytes BETWEEN 1 AND {TEMPLATE_BYTES_MAX}",
            name=f"ck_{t}_bytes",
        ),
        sa.CheckConstraint("guide_version >= 1", name=f"ck_{t}_guide_version"),
        sa.CheckConstraint(
            "(status = 'retired') = (retired_at IS NOT NULL)", name=f"ck_{t}_retired_at"
        ),
        # The combinations of spec 15.3.
        sa.CheckConstraint(
            "(template_purpose = 'display') = (grading_kind = 'none')", name=f"ck_{t}_purpose"
        ),
        sa.CheckConstraint(
            "practice_kind IS NOT DISTINCT FROM (CASE"
            " WHEN template_purpose = 'display' THEN NULL"
            " WHEN pool_draw <> 'none' THEN 'drill'"
            " WHEN grading_kind = 'self_report' THEN 'exercise'"
            " ELSE 'quiz' END)",
            name=f"ck_{t}_practice_kind_derived",
        ),
        sa.CheckConstraint(
            f"(text_match IS NOT NULL) = (grading_kind IN {TEXT_GRADING_KINDS})",
            name=f"ck_{t}_text_match_kind",
        ),
        sa.CheckConstraint(
            "(numeric_tolerance IS NOT NULL) = (grading_kind = 'numeric')",
            name=f"ck_{t}_tolerance_kind",
        ),
        sa.CheckConstraint(
            f"pool_draw = 'none' OR (pool_draw, grading_kind) IN {POOL_DRAW_GRADING}",
            name=f"ck_{t}_pool_draw_kind",
        ),
        sa.CheckConstraint(
            "CASE pool_draw"
            " WHEN 'one_of_choices' THEN"
            f" coalesce(pool_count BETWEEN {choices_low} AND {choices_high}, false)"
            " WHEN 'ordered' THEN"
            f" coalesce(pool_count BETWEEN {ordered_low} AND {ordered_high}, false)"
            " ELSE pool_count IS NULL END",
            name=f"ck_{t}_pool_count",
        ),
    )
    i = "template_instances"
    op.create_table(
        i,
        _id(),
        _user_id(),
        sa.Column("subject_id", sa.Uuid, nullable=False),
        sa.Column("note_id", sa.Uuid),
        sa.Column("seq", sa.Integer),
        sa.Column("template_id", sa.Uuid, nullable=False),
        sa.Column("template_purpose", sa.Text, nullable=False),
        sa.Column("practice_kind", sa.Text),
        sa.Column("grading_kind", sa.Text, nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("prompt_text", sa.Text, nullable=False),
        sa.Column("concept_id", sa.Uuid),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("content_key", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("supersedes_instance_id", sa.Uuid),
        _ts("created_at"),
        _ts("retired_at", nullable=True),
        _pair_key(i),
        _child_fk(i, "subject_id", "subjects"),
        # NO ACTION is checked at the end of the statement, so a subject or user cascade that
        # removes both the template and its instances succeeds; nothing else deletes a template.
        _child_fk(i, "template_id", "exercise_templates", ondelete=None),
        sa.UniqueConstraint("note_id", "seq", name=f"{i}_note_seq_key"),
        _one_of(i, "template_purpose", TEMPLATE_PURPOSES),
        _one_of(i, "practice_kind", PRACTICE_KINDS),
        _one_of(i, "grading_kind", GRADING_KINDS),
        _one_of(i, "origin", INSTANCE_ORIGINS),
        _one_of(i, "status", INSTANCE_STATUSES),
        sa.CheckConstraint(
            "(template_purpose = 'display') = (grading_kind = 'none')"
            " AND (template_purpose = 'display') = (practice_kind IS NULL)",
            name=f"ck_{i}_purpose",
        ),
        sa.CheckConstraint(
            f"jsonb_typeof(params) = 'object' AND octet_length(params::text) <= {PARAMS_JSON_MAX}",
            name=f"ck_{i}_params",
        ),
        _len_between(i, "prompt_text", 1, PROMPT_TEXT_MAX),
        sa.CheckConstraint("seq >= 1", name=f"ck_{i}_seq"),
        sa.CheckConstraint("content_key ~ '^[0-9a-f]{64}$'", name=f"ck_{i}_content_key"),
        sa.CheckConstraint(
            "(status = 'retired') = (retired_at IS NOT NULL)", name=f"ck_{i}_retired_at"
        ),
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
    # FORCE RLS applies to the table owner too, and tutor_report has no grant here. For
    # tutor_app the policy's user_id test is the only index-able qual (`@@` is not leakproof),
    # so search plans via ix_note_passages_user today; the GIN index is kept for a future
    # owner-side or leakproof path (plan ruling 17).
    op.create_index("ix_note_passages_user", "note_passages", ["user_id"])
    op.create_index("ix_note_passages_tsv", "note_passages", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_concepts_name_norm_trgm",
        "concepts",
        ["name_norm"],
        postgresql_using="gin",
        postgresql_ops={"name_norm": "gin_trgm_ops"},
    )
    op.create_index("ix_note_concepts_concept", "note_concepts", ["concept_id"])
    op.create_index("ix_concept_links_to", "concept_links", ["to_id"])
    # One active template per subject and name; replacing one retires it first.
    op.create_index(
        "ux_exercise_templates_active_name",
        "exercise_templates",
        ["user_id", "subject_id", "name_norm"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_exercise_templates_subject", "exercise_templates", ["subject_id"])
    op.create_index(
        "ix_exercise_templates_supersedes", "exercise_templates", ["supersedes_template_id"]
    )
    # The content key is unique among a user's active instances (plan ruling 28).
    op.create_index(
        "ux_template_instances_active_content_key",
        "template_instances",
        ["user_id", "content_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_template_instances_kind",
        "template_instances",
        ["user_id", "subject_id", "practice_kind"],
    )
    op.create_index("ix_template_instances_note", "template_instances", ["note_id"])
    op.create_index("ix_template_instances_template", "template_instances", ["template_id"])
    op.create_index("ix_template_instances_concept", "template_instances", ["concept_id"])
    op.create_index(
        "ix_template_instances_supersedes", "template_instances", ["supersedes_instance_id"]
    )


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
    # No DELETE on either: rows leave only through the user or subject cascade, which runs as
    # the table owner (spec 15.10).
    op.execute("GRANT SELECT, INSERT ON exercise_templates TO tutor_app")
    op.execute(f"GRANT UPDATE ({TEMPLATES_UPDATE_COLUMNS}) ON exercise_templates TO tutor_app")
    op.execute("GRANT SELECT, INSERT ON template_instances TO tutor_app")
    op.execute(f"GRANT UPDATE ({INSTANCES_UPDATE_COLUMNS}) ON template_instances TO tutor_app")
