"""Migration 0006 on db-test: round trip, the text search configurations, the narrow grants,
the GIN index behind the search statement (subjects spec 8.1, plan Task 3), and the exercise
template tables with their combination CHECKs (spec 15.10, plan Task 4b)."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from psycopg.errors import CheckViolation, InsufficientPrivilege
from repo_contract import add_note, long_paragraph, sample_subject, valid_note
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError
from template_fixtures import HTML, KEY_NAME, NUMERIC, REVEAL_NAME, spec

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
    "concepts",
    "note_concepts",
    "concept_links",
    "exercise_templates",
    "template_instances",
)
OLD_TABLES = ("note_artifacts", "exercises", "quiz_items")  # replaced by the two above
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
    assert set(inspect(engine).get_table_names()).isdisjoint(OLD_TABLES)
    assert len(NEW_TABLES) == 9
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


# The downgrade drops nine tables of learner data: it refuses unless they are empty or the
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


# --- exercise templates and instances (spec 15.10) ----------------------------------------------


def _seed_template(
    uow_factory: UowFactory, user_id: UUID, now: datetime
) -> tuple[UUID, UUID, UUID, UUID]:
    """(subject, note, template, instance) of one user, written through the repositories."""
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject(), now).id
        note = add_note(uow, subject_id, valid_note("Clase", long_paragraph("texto.")), now)
        template = uow.templates.create(subject_id, spec(NUMERIC), HTML, 1, now)
        instance, _ = uow.instances.insert(
            subject_id=subject_id,
            note_id=note.id,
            template=template,
            params={"problem": "2 + 2", KEY_NAME: 4.0, REVEAL_NAME: "r"},
            prompt_text="Suma.",
            concept_id=None,
            origin="ingest",
            key="a" * 64,
            now=now,
        )
    return subject_id, note.id, template.id, instance.id


def test_app_role_may_only_retire_templates_and_instances(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    _, _, template_id, instance_id = _seed_template(uow_factory, user_id, now)
    ids: dict[str, object] = {"id": template_id}
    for sql in (
        "UPDATE exercise_templates SET html = html WHERE id = :id",
        "UPDATE exercise_templates SET params = params WHERE id = :id",
        "UPDATE exercise_templates SET name = name WHERE id = :id",
        "DELETE FROM exercise_templates WHERE id = :id",
    ):
        _assert_denied(engine, user_id, sql, ids)
    ids = {"id": instance_id}
    for sql in (
        "UPDATE template_instances SET params = params WHERE id = :id",
        "UPDATE template_instances SET prompt_text = prompt_text WHERE id = :id",
        "UPDATE template_instances SET template_id = template_id WHERE id = :id",
        "DELETE FROM template_instances WHERE id = :id",
    ):
        _assert_denied(engine, user_id, sql, ids)
    with scoped_connection(engine, user_id=user_id) as conn:
        retired = conn.execute(
            text(
                "UPDATE template_instances SET status = 'retired', retired_at = :now WHERE id = :id"
            ),
            {"id": instance_id, "now": now},
        )
        assert retired.rowcount == 1
        retired = conn.execute(
            text(
                "UPDATE exercise_templates SET status = 'retired', retired_at = :now,"
                " updated_at = :now WHERE id = :id"
            ),
            {"id": template_id, "now": now},
        )
        assert retired.rowcount == 1


TEMPLATE_COLUMNS: dict[str, Any] = {
    "name": "Plantilla",
    "name_norm": "plantilla",
    "description": None,
    "template_purpose": "exercise",
    "practice_kind": "quiz",
    "grading_kind": "numeric",
    "text_match": None,
    "numeric_tolerance": 0,
    "pool_draw": "none",
    "pool_count": None,
    "needs_view": False,
    "params": json.dumps([{"name": "x"}]),
    "options_param": None,
    "html": "<!doctype html>",
    "html_sha256": "0" * 64,
    "bytes": 15,
    "guide_version": 1,
    "status": "active",
    "retired_at": None,
}


def _insert_template(engine: Engine, user_id: UUID, subject_id: UUID, **changes: Any) -> UUID:
    columns = {**TEMPLATE_COLUMNS, **changes}
    if "html" in changes and "bytes" not in changes:
        columns["bytes"] = len(str(changes["html"]).encode("utf-8"))
    names = ", ".join(columns)
    values = ", ".join(f"CAST(:{n} AS jsonb)" if n == "params" else f":{n}" for n in columns)
    template_id = uuid4()
    head = "INSERT INTO exercise_templates (id, user_id, subject_id, created_at, updated_at, "
    sql = f"{head}{names}) VALUES (:id, :uid, :sid, now(), now(), {values})"
    with engine.begin() as conn:
        conn.execute(text(sql), {"id": template_id, "uid": user_id, "sid": subject_id, **columns})
    return template_id


@pytest.mark.parametrize(
    "changes",
    [
        {"template_purpose": "display", "grading_kind": "none", "practice_kind": "quiz"},
        {
            "template_purpose": "display",
            "grading_kind": "none",
            "practice_kind": None,
            "numeric_tolerance": None,
            "pool_draw": "one",
        },
        {"grading_kind": "self_report", "numeric_tolerance": None, "practice_kind": "quiz"},
        {"text_match": "fold_all"},
        {"grading_kind": "exact_text", "numeric_tolerance": 0.5, "text_match": "fold_all"},
        {
            "grading_kind": "choice_index",
            "numeric_tolerance": None,
            "pool_draw": "one_of_choices",
            "practice_kind": "drill",
            "pool_count": 9,
        },
        {
            "grading_kind": "exact_sequence",
            "numeric_tolerance": None,
            "text_match": "fold_all",
            "pool_draw": "ordered",
            "practice_kind": "drill",
            "pool_count": None,
        },
        {"html": "x" * 40_001},
        {"bytes": 3},  # not octet_length(html)
        {"params": json.dumps([])},
        {"params": json.dumps({"a": 1})},
        {"status": "retired"},  # without retired_at
        {"options_param": "Bad name"},
        {"grading_kind": "essay"},
    ],
    ids=lambda changes: ",".join(f"{k}={str(v)[:12]}" for k, v in changes.items()),
)
def test_template_combination_checks(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime, changes: dict[str, Any]
) -> None:
    subject_id, _, _, _ = _seed_template(uow_factory, user_id, now)
    _insert_template(engine, user_id, subject_id, name="Ok", name_norm="ok")  # the base is valid
    with pytest.raises(DBAPIError) as err:
        _insert_template(engine, user_id, subject_id, **changes)
    assert isinstance(err.value.orig, CheckViolation)


def test_instance_params_cap_and_subject_exercise_kinds(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    subject_id, _, template_id, instance_id = _seed_template(uow_factory, user_id, now)
    with pytest.raises(DBAPIError) as err, engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO template_instances (id, user_id, subject_id, template_id,"
                " template_purpose, practice_kind, grading_kind, params, prompt_text, origin,"
                " content_key, status, created_at) VALUES (gen_random_uuid(), :uid, :sid, :tid,"
                " 'exercise', 'quiz', 'numeric', CAST(:params AS jsonb), 'p', 'class',"
                " repeat('b', 64), 'active', now())"
            ),
            {
                "uid": user_id,
                "sid": subject_id,
                "tid": template_id,
                "params": json.dumps({"x": "y" * 32_768}),
            },
        )
    assert isinstance(err.value.orig, CheckViolation)
    with pytest.raises(DBAPIError) as err, engine.begin() as conn:
        conn.execute(
            text("UPDATE subjects SET exercise_kinds = ARRAY['quiz', 'ear'] WHERE id = :id"),
            {"id": subject_id},
        )
    assert isinstance(err.value.orig, CheckViolation)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE subjects SET exercise_kinds = ARRAY['quiz', 'drill'] WHERE id = :id"),
            {"id": subject_id},
        )
    assert instance_id


def test_deleting_a_subject_removes_its_templates_and_instances(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, now: datetime
) -> None:
    subject_id, note_id, template_id, instance_id = _seed_template(uow_factory, user_id, now)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM notes WHERE id = :id"), {"id": note_id})
        # A note's deletion keeps the instance and nulls only note_id (plan ruling 18).
        kept = conn.execute(
            text("SELECT note_id, user_id FROM template_instances WHERE id = :id"),
            {"id": instance_id},
        ).one()
        assert (kept.note_id, kept.user_id) == (None, user_id)
        conn.execute(text("DELETE FROM subjects WHERE id = :id"), {"id": subject_id})
        for table, row_id in (
            ("exercise_templates", template_id),
            ("template_instances", instance_id),
        ):
            left = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            ).scalar_one()
            assert left == 0


def test_templates_and_instances_are_isolated_between_users(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
) -> None:
    _, _, template_id, instance_id = _seed_template(uow_factory, user_id, now)
    with scoped_connection(engine, user_id=other_user_id) as conn:
        for table in ("exercise_templates", "template_instances"):
            seen = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
            assert seen == 0
        changed = conn.execute(
            text(
                "UPDATE template_instances SET status = 'retired', retired_at = now()"
                " WHERE id = :id"
            ),
            {"id": instance_id},
        )
        assert changed.rowcount == 0
    with scoped_connection(engine, user_id=user_id) as conn:
        assert conn.execute(text("SELECT count(*) FROM exercise_templates")).scalar_one() == 1
        status = conn.execute(
            text("SELECT status FROM template_instances WHERE id = :id"), {"id": instance_id}
        ).scalar_one()
        assert status == "active"
    assert template_id
