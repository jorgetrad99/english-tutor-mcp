"""Migration 0006 on db-test: round trip, the text search configurations, the narrow grants
and the GIN index behind the search statement (subjects spec 8.1, plan Task 3)."""

from datetime import datetime
from uuid import UUID

import pytest
from alembic.config import Config
from psycopg.errors import InsufficientPrivilege
from repo_contract import add_note, long_paragraph, sample_subject, valid_note
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from tutor.db.repos.notes import SEARCH_SQL, search_params
from tutor.db.uow import scoped_connection
from tutor.services.ports import UowFactory

from .conftest import ROOT, run_alembic

pytestmark = pytest.mark.integration

NEW_TABLES = (
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
CONFIGURATIONS = {"tutor_es", "tutor_en"}
# Received once, never changed by the app (spec 4.1, plan ruling 9).
FROZEN_NOTE_COLUMNS = (
    "raw_text",
    "raw_sha256",
    "raw_had_nul",
    "idempotency_key",
    "origin",
    "created_at",
)


def _configurations(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT cfgname FROM pg_ts_config WHERE cfgname LIKE 'tutor_%'"))
        return {r[0] for r in rows}


def _extensions(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text("SELECT extname FROM pg_extension"))}


def _assert_denied(engine: Engine, user_id: UUID, sql: str, params: dict[str, object]) -> None:
    with pytest.raises(DBAPIError) as err, scoped_connection(engine, user_id=user_id) as conn:
        conn.execute(text(sql), params)
    assert isinstance(err.value.orig, InsufficientPrivilege)


def test_upgrade_downgrade_round_trip_and_rerun_with_a_leftover_configuration(
    engine: Engine,
) -> None:
    run_alembic(engine, "downgrade", "0005")
    assert set(inspect(engine).get_table_names()).isdisjoint(NEW_TABLES)
    assert _configurations(engine) == set()
    assert {"unaccent", "pg_trgm"} <= _extensions(engine)  # left in place on purpose
    # A configuration left behind (a failed run, a manual experiment) does not break the
    # upgrade: the DO blocks only create what is missing.
    with engine.begin() as conn:
        conn.execute(text("CREATE TEXT SEARCH CONFIGURATION tutor_es (COPY = spanish)"))
    run_alembic(engine, "upgrade", "head")
    assert set(NEW_TABLES) <= set(inspect(engine).get_table_names())
    assert _configurations(engine) == CONFIGURATIONS
    # Rebuild from scratch so the rest of the suite runs on the migration's own mappings.
    run_alembic(engine, "downgrade", "0005")
    run_alembic(engine, "upgrade", "head")
    assert _configurations(engine) == CONFIGURATIONS


def test_configurations_strip_accents_before_stemming(engine: Engine) -> None:
    with engine.connect() as conn:
        es = conn.execute(
            text("SELECT to_tsvector('tutor_es', 'Séptima pentatónica, acordes')::text")
        ).scalar_one()
        en = conn.execute(
            text("SELECT to_tsvector('tutor_en', 'Chords, sevenths, pentatónica')::text")
        ).scalar_one()
    assert all(stem in es for stem in ("'septim'", "'pentaton'", "'acord'"))
    assert all(stem in en for stem in ("'chord'", "'seventh'", "'pentatonica'"))


def test_app_role_cannot_rewrite_raw_text_or_passages(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject(), now).id
        note = add_note(uow, subject_id, valid_note("Clase", long_paragraph("texto.")), now)
    for column in FROZEN_NOTE_COLUMNS:
        _assert_denied(
            engine,
            user_id,
            f"UPDATE notes SET {column} = {column} WHERE id = :id",  # noqa: S608 - constant names
            {"id": note.id},
        )
    _assert_denied(
        engine, user_id, "UPDATE note_passages SET text = text WHERE note_id = :id", {"id": note.id}
    )
    # What the app may change after the insert still works.
    with scoped_connection(engine, user_id=user_id) as conn:
        changed = conn.execute(
            text(
                "UPDATE notes SET title = title, clean_text = clean_text, updated_at = :now"
                " WHERE id = :id"
            ),
            {"id": note.id, "now": now},
        )
        assert changed.rowcount == 1
        conn.execute(text("DELETE FROM note_passages WHERE note_id = :id"), {"id": note.id})


def test_only_the_app_role_is_granted_on_the_new_tables(engine: Engine) -> None:
    with engine.connect() as conn:
        grantees = conn.execute(
            text(
                "SELECT DISTINCT grantee FROM information_schema.role_table_grants"
                " WHERE table_schema = 'public' AND table_name = ANY(:tables)"
            ),
            {"tables": list(NEW_TABLES)},
        ).scalars()
        owner = conn.execute(text("SELECT current_user")).scalar_one()
    assert set(grantees) <= {owner, "tutor_app"}  # nothing for tutor_report


def test_search_uses_the_gin_index_over_a_thousand_passages(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    """The statement's passage match is index-able: planned as the owner it reads the GIN
    index. As tutor_app the row-level policy comes first and `@@` is not leakproof, so the
    planner filters the user's rows instead (ix_note_passages_user); the result is the same."""
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject(), now).id
        note = add_note(uow, subject_id, valid_note("Clase", long_paragraph("acorde.")), now)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO note_passages (note_id, seq, user_id, text)"
                " SELECT :n, g, :u, repeat('Mantener el pulso con calma y paciencia. ', 20)"
                " || 'palabra' || g FROM generate_series(2, 1001) g"
            ),
            {"n": note.id, "u": user_id},
        )
        conn.execute(text("ANALYZE note_passages"))
    params = search_params(user_id, "palabra777", None, 5)
    assert params is not None
    with engine.begin() as conn:
        plan = "\n".join(r[0] for r in conn.execute(text("EXPLAIN " + SEARCH_SQL), params))
    assert "Bitmap Index Scan on ix_note_passages_tsv" in plan
    with uow_factory(user_id) as uow:
        [hit] = uow.notes.search("palabra777", None, 5)
        assert uow.notes.passage_count(note.id) == 1001
    assert (hit.note_id, hit.passages, hit.fuzzy) == (note.id, 1, False)
    assert len(hit.excerpts) == 1 and "[[palabra777]]" in hit.excerpts[0]


# The downgrade drops ten tables of learner data: it refuses unless they are empty or the
# operator sets the flag (runbook, "Upgrades and migrations").
DESTRUCTIVE_FLAG = "TUTOR_ALLOW_DESTRUCTIVE_DOWNGRADE"


def _tables_present(engine: Engine) -> bool:
    return set(NEW_TABLES) <= set(inspect(engine).get_table_names())


def test_downgrade_refuses_while_a_subject_exists(
    engine: Engine,
    uow_factory: UowFactory,
    user_id: UUID,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DESTRUCTIVE_FLAG, raising=False)
    with uow_factory(user_id) as uow:
        uow.subjects.create(sample_subject(), now)
    with pytest.raises(RuntimeError, match=DESTRUCTIVE_FLAG):
        run_alembic(engine, "downgrade", "0005")
    assert _tables_present(engine)
    monkeypatch.setenv(DESTRUCTIVE_FLAG, "yes")  # only "1" counts
    with pytest.raises(RuntimeError, match=DESTRUCTIVE_FLAG):
        run_alembic(engine, "downgrade", "0005")
    assert _tables_present(engine)


def test_downgrade_proceeds_over_data_with_the_flag(
    engine: Engine,
    uow_factory: UowFactory,
    user_id: UUID,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject(), now).id
        add_note(uow, subject_id, valid_note("Clase", long_paragraph("texto.")), now)
    monkeypatch.setenv(DESTRUCTIVE_FLAG, "1")
    run_alembic(engine, "downgrade", "0005")
    assert set(inspect(engine).get_table_names()).isdisjoint(NEW_TABLES)
    run_alembic(engine, "upgrade", "head")
    assert _tables_present(engine)


def test_downgrade_proceeds_on_empty_tables_without_the_flag(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DESTRUCTIVE_FLAG, raising=False)
    run_alembic(engine, "downgrade", "0005")
    assert set(inspect(engine).get_table_names()).isdisjoint(NEW_TABLES)
    run_alembic(engine, "upgrade", "head")
    assert _tables_present(engine)


def test_downgrade_refuses_when_row_level_security_hides_the_rows(
    engine: Engine,
    uow_factory: UowFactory,
    user_id: UUID,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A role that does not bypass RLS sees empty tables whatever they hold, so the check
    cannot tell; without the flag the downgrade refuses rather than trust an empty count."""
    monkeypatch.delenv(DESTRUCTIVE_FLAG, raising=False)
    with uow_factory(user_id) as uow:
        uow.subjects.create(sample_subject(), now)
    cfg = Config(str(ROOT / "alembic.ini"))
    with pytest.raises(RuntimeError, match=DESTRUCTIVE_FLAG), engine.begin() as conn:
        # Alembic must read its version row as that role; the refusal rolls the grant back.
        conn.execute(text("GRANT SELECT ON alembic_version TO tutor_app"))
        conn.execute(text("SET LOCAL ROLE tutor_app"))
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0005")
    assert _tables_present(engine)
