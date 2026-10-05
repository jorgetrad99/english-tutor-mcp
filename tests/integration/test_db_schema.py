"""Schema, grants and row-level security on db-test (spec sections 5 and 13; rulings 3-5)."""

import hashlib
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import DBAPIError

from tutor.db.tables import USER_TABLES, metadata
from tutor.db.uow import scoped_connection

from .conftest import run_alembic, truncate_user_tables

pytestmark = pytest.mark.integration

TRACK_ID = "zz-rls-01"

# One row in every user table for one user, inserted as the owner (superuser bypasses RLS).
_SEED_SQL = (
    "INSERT INTO users (id, google_sub, display_name, created_at) VALUES (:u, :sub, 'Seed', now())",
    "INSERT INTO profiles (user_id, domains, use_cases, minutes_per_day, days_per_week,"
    " self_level, target_level, onboarded_at, updated_at)"
    " VALUES (:u, ARRAY['it'], ARRAY['standup'], 20, 3, 'B1', 'B2', now(), now())",
    "INSERT INTO plans (id, user_id, version, status, generated_at, rationale)"
    " VALUES (:plan, :u, 1, 'active', now(), '{}')",
    "INSERT INTO plan_items (id, plan_id, user_id, week_no, order_no, track_item_id, variant)"
    " VALUES (:item, :plan, :u, 1, 1, :t, 'base')",
    "INSERT INTO sessions (id, user_id, plan_item_id, track_item_id, mode, client, started_at,"
    " brief_variant) VALUES (:s, :u, :item, :t, 'text', 'claude', now(), 'base')",
    "INSERT INTO session_metrics (session_id, user_id, user_words, turns, words_per_turn,"
    " duration_min, user_words_per_min, errors_total, errors_rejected, errors_by_category,"
    " errors_per_100w, recurring_errors, uptake_count, chunks_offered, chunks_used,"
    " chunks_rejected, activation_rate)"
    " VALUES (:s, :u, 40, 4, 10, 12, 3.3, 1, 0, '{}', 2.5, 0, 0, 5, 1, 0, 0.2)",
    "INSERT INTO session_errors (session_id, user_id, said, correct, correct_norm, category,"
    " turn_index) VALUES (:s, :u, 'I goed', 'I went', 'i went', 'grammar', 0)",
    "INSERT INTO glossary_items (id, user_id, kind, text, text_norm, meaning, context_sentence,"
    " domain, status, created_at, updated_at) VALUES (:g, :u, 'term', 'deploy', 'deploy',"
    " 'release code', 'We deploy on Fridays.', 'it', 'confirmed', now(), now())",
    "INSERT INTO review_states (glossary_item_id, user_id, due_at) VALUES (:g, :u, now())",
    "INSERT INTO review_logs (glossary_item_id, user_id, session_id, rating, reviewed_at,"
    " state_before) VALUES (:g, :u, :s, 3, now(), '{}')",
    "INSERT INTO audit_log (user_id, event, meta, at) VALUES (:u, 'user_created', '{}', now())",
    "INSERT INTO web_sessions (token_hash, user_id, csrf_token, created_at, last_seen_at)"
    " VALUES (:h, :u, 'csrf', now(), now())",
)


def _seed_user(conn: Connection, sub: str, track_item_id: str) -> UUID:
    uid = uuid4()
    params = {
        "u": uid,
        "sub": sub,
        "t": track_item_id,
        "plan": uuid4(),
        "item": uuid4(),
        "s": uuid4(),
        "g": uuid4(),
        "h": hashlib.sha256(sub.encode()).hexdigest(),
    }
    for sql in _SEED_SQL:
        conn.execute(text(sql), params)
    return uid


def _assert_denied(engine: Engine, user_id: UUID, sql: str, params: dict[str, Any]) -> None:
    with pytest.raises(DBAPIError) as err, scoped_connection(engine, user_id=user_id) as conn:
        conn.execute(text(sql), params)
    assert isinstance(err.value.orig, InsufficientPrivilege)


@pytest.fixture
def track_item(engine: Engine) -> Iterator[str]:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO track_items (id, domain, order_no, cefr, can_do_en, can_do_es, skill,"
                " interaction_type, use_cases, character, objective, obstacle, scenario_hint)"
                " VALUES (:id, 'zz', 999, 'B1', 'x', 'x', 'speaking', 'explain',"
                " ARRAY['standup'], 'x', 'x', 'x', 'x') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": TRACK_ID},
        )
    yield TRACK_ID
    truncate_user_tables(engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM track_items WHERE id = :id"), {"id": TRACK_ID})


@pytest.fixture
def two_users(engine: Engine, track_item: str) -> tuple[UUID, UUID]:
    with engine.begin() as conn:
        return _seed_user(conn, "sub-a", track_item), _seed_user(conn, "sub-b", track_item)


def test_upgrade_downgrade_upgrade_round_trip(engine: Engine) -> None:
    run_alembic(engine, "downgrade", "base")
    assert set(inspect(engine).get_table_names()).isdisjoint(metadata.tables)
    with engine.connect() as conn:
        roles = conn.execute(text("SELECT count(*) FROM pg_roles WHERE rolname = 'tutor_app'"))
        assert roles.scalar_one() == 1  # the role survives a downgrade
    run_alembic(engine, "upgrade", "head")
    assert set(metadata.tables) <= set(inspect(engine).get_table_names())


def test_core_tables_mirror_the_migration(engine: Engine) -> None:
    insp = inspect(engine)
    for table in metadata.sorted_tables:
        in_db = {c["name"]: c["nullable"] for c in insp.get_columns(table.name)}
        in_code = {c.name: c.nullable for c in table.columns}
        assert in_db == in_code, table.name


def test_every_user_table_has_forced_rls(engine: Engine) -> None:
    with_user_id = {t.name for t in metadata.tables.values() if "user_id" in t.c}
    assert with_user_id | {"users"} == set(USER_TABLES)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public' AND c.relname = ANY(:names)"
            ),
            {"names": list(USER_TABLES)},
        ).all()
    assert {r.relname: (r.relrowsecurity, r.relforcerowsecurity) for r in rows} == {
        t: (True, True) for t in USER_TABLES
    }


@pytest.mark.parametrize("table", USER_TABLES)
def test_rls_shows_only_the_scoped_users_rows(
    engine: Engine, two_users: tuple[UUID, UUID], table: str
) -> None:
    count = text(f"SELECT count(*) FROM {table}")  # noqa: S608 - names from USER_TABLES
    with engine.connect() as conn:
        assert conn.execute(count).scalar_one() == 2  # owner sees both
    for uid in two_users:
        with scoped_connection(engine, user_id=uid) as conn:
            assert conn.execute(count).scalar_one() == 1
    with scoped_connection(engine) as conn:
        assert conn.execute(count).scalar_one() == 0  # no user set: nothing


def test_rls_blocks_cross_user_writes(engine: Engine, two_users: tuple[UUID, UUID]) -> None:
    a, b = two_users
    with scoped_connection(engine, user_id=b) as conn:
        updated = conn.execute(
            text("UPDATE glossary_items SET meaning = 'changed' WHERE user_id = :a"), {"a": a}
        )
        assert updated.rowcount == 0
        deleted = conn.execute(text("DELETE FROM sessions WHERE user_id = :a"), {"a": a})
        assert deleted.rowcount == 0
        renamed = conn.execute(
            text("UPDATE users SET display_name = 'changed' WHERE id = :a"), {"a": a}
        )
        assert renamed.rowcount == 0
    _assert_denied(
        engine,
        b,
        "INSERT INTO audit_log (user_id, event, meta, at) VALUES (:a, 'user_created', '{}', now())",
        {"a": a},
    )
    with engine.connect() as conn:
        meaning = conn.execute(
            text("SELECT meaning FROM glossary_items WHERE user_id = :a"), {"a": a}
        ).scalar_one()
    assert meaning == "release code"


def test_app_role_privileges_are_narrow(engine: Engine, two_users: tuple[UUID, UUID]) -> None:
    a, _ = two_users
    _assert_denied(engine, a, "UPDATE users SET role = 'admin' WHERE id = :a", {"a": a})
    _assert_denied(engine, a, "DELETE FROM audit_log WHERE user_id = :a", {"a": a})
    _assert_denied(
        engine, a, "UPDATE track_items SET objective = 'x' WHERE id = :t", {"t": TRACK_ID}
    )
    with scoped_connection(engine, user_id=a) as conn:
        assert conn.execute(text("SELECT count(*) FROM track_items")).scalar_one() >= 1


def test_anonymous_web_session_is_visible_only_with_its_hash(engine: Engine) -> None:
    mine, other = "a" * 64, "b" * 64
    with scoped_connection(engine, web_session=mine) as conn:
        conn.execute(
            text(
                "INSERT INTO web_sessions (token_hash, user_id, csrf_token, created_at,"
                " last_seen_at) VALUES (:h, NULL, 'csrf', now(), now())"
            ),
            {"h": mine},
        )
    count = text("SELECT count(*) FROM web_sessions")
    with scoped_connection(engine, web_session=mine) as conn:
        assert conn.execute(count).scalar_one() == 1
    with scoped_connection(engine, web_session=other) as conn:
        assert conn.execute(count).scalar_one() == 0
    with scoped_connection(engine) as conn:
        assert conn.execute(count).scalar_one() == 0


def _ids(engine: Engine, uid: UUID) -> dict[str, UUID]:
    with engine.connect() as conn:
        pick = {
            "plan": "plans",
            "item": "plan_items",
            "s": "sessions",
            "g": "glossary_items",
        }
        return {
            key: conn.execute(
                text(f"SELECT id FROM {table} WHERE user_id = :u"),  # noqa: S608
                {"u": uid},
            ).scalar_one()
            for key, table in pick.items()
        }


_METRICS_INSERT = (
    "INSERT INTO session_metrics (session_id, user_id, user_words, turns, words_per_turn,"
    " duration_min, user_words_per_min, errors_total, errors_rejected, errors_by_category,"
    " errors_per_100w, recurring_errors, uptake_count, chunks_offered, chunks_used,"
    " chunks_rejected, activation_rate)"
    " VALUES (:a_s, :b, 40, 4, 10, 12, 3.3, 1, 0, '{}', 2.5, 0, 0, 5, 1, 0, 0.2)"
)
# Each statement runs as user B and points at user A's parent row.
_CROSS_USER_CHILDREN = {
    "plan_items.plan_id": "INSERT INTO plan_items (plan_id, user_id, week_no, order_no,"
    " track_item_id, variant) VALUES (:a_plan, :b, 1, 2, :t, 'base')",
    "sessions.plan_item_id": "INSERT INTO sessions (user_id, plan_item_id, track_item_id, mode,"
    " client, started_at, brief_variant, status)"
    " VALUES (:b, :a_item, :t, 'text', 'claude', now(), 'base', 'closed')",
    "session_metrics.session_id": _METRICS_INSERT,
    "session_errors.session_id": "INSERT INTO session_errors (session_id, user_id, said, correct,"
    " correct_norm, category, turn_index) VALUES (:a_s, :b, 'x', 'y', 'y', 'grammar', 1)",
    "review_states.glossary_item_id": "INSERT INTO review_states (glossary_item_id, user_id,"
    " due_at) VALUES (:a_g, :b, now())",
    "review_logs.glossary_item_id": "INSERT INTO review_logs (glossary_item_id, user_id,"
    " session_id, rating, reviewed_at, state_before) VALUES (:a_g, :b, :b_s, 3, now(), '{}')",
    "review_logs.session_id": "INSERT INTO review_logs (glossary_item_id, user_id, session_id,"
    " rating, reviewed_at, state_before) VALUES (:b_g, :b, :a_s, 3, now(), '{}')",
}


@pytest.mark.parametrize("reference", _CROSS_USER_CHILDREN)
def test_composite_foreign_keys_reject_another_users_parent(
    engine: Engine, two_users: tuple[UUID, UUID], reference: str
) -> None:
    a, b = two_users
    with engine.begin() as conn:  # free the primary keys the child inserts would collide with
        conn.execute(text("DELETE FROM session_metrics WHERE user_id = :a"), {"a": a})
        conn.execute(text("DELETE FROM review_states WHERE user_id = :a"), {"a": a})
    a_ids, b_ids = _ids(engine, a), _ids(engine, b)
    params = {
        "b": b,
        "t": TRACK_ID,
        "a_plan": a_ids["plan"],
        "a_item": a_ids["item"],
        "a_s": a_ids["s"],
        "a_g": a_ids["g"],
        "b_s": b_ids["s"],
        "b_g": b_ids["g"],
    }
    with pytest.raises(DBAPIError) as err, scoped_connection(engine, user_id=b) as conn:
        conn.execute(text(_CROSS_USER_CHILDREN[reference]), params)
    assert isinstance(err.value.orig, ForeignKeyViolation)


def test_app_cannot_insert_users_with_a_role_or_an_id(
    engine: Engine, two_users: tuple[UUID, UUID]
) -> None:
    a, _ = two_users
    _assert_denied(engine, a, "INSERT INTO users (google_sub, role) VALUES ('evil', 'admin')", {})
    _assert_denied(
        engine, a, "INSERT INTO users (id, google_sub) VALUES (gen_random_uuid(), 'evil2')", {}
    )


def test_a_row_cannot_be_moved_to_another_user(
    engine: Engine, two_users: tuple[UUID, UUID]
) -> None:
    a, b = two_users
    _assert_denied(
        engine, a, "UPDATE glossary_items SET user_id = :b WHERE user_id = :a", {"a": a, "b": b}
    )


def test_cookie_alone_cannot_attach_a_web_session_to_a_user(
    engine: Engine, two_users: tuple[UUID, UUID]
) -> None:
    _, b = two_users
    mine = "c" * 64
    insert = text(
        "INSERT INTO web_sessions (token_hash, user_id, csrf_token, created_at, last_seen_at)"
        " VALUES (:h, :u, 'csrf', now(), now())"
    )
    with pytest.raises(DBAPIError) as err, scoped_connection(engine, web_session=mine) as conn:
        conn.execute(insert, {"h": mine, "u": b})
    assert isinstance(err.value.orig, InsufficientPrivilege)
    with scoped_connection(engine, web_session=mine) as conn:
        conn.execute(insert, {"h": mine, "u": None})
    with pytest.raises(DBAPIError) as err, scoped_connection(engine, web_session=mine) as conn:
        conn.execute(
            text("UPDATE web_sessions SET user_id = :u WHERE token_hash = :h"), {"h": mine, "u": b}
        )
    assert isinstance(err.value.orig, InsufficientPrivilege)
    # The login flow: cookie and the user's own id together may attach it.
    with scoped_connection(engine, web_session=mine, user_id=b) as conn:
        conn.execute(
            text("UPDATE web_sessions SET user_id = :u WHERE token_hash = :h"), {"h": mine, "u": b}
        )


def test_migration_0002_round_trip(engine: Engine) -> None:
    def composite_fks() -> set[str]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE contype = 'f'"
                    " AND conname LIKE '%_user_id_fkey' AND array_length(conkey, 1) = 2"
                )
            )
            return {r[0] for r in rows}

    assert len(composite_fks()) == 7
    run_alembic(engine, "downgrade", "0001")
    assert composite_fks() == set()
    run_alembic(engine, "upgrade", "head")
    assert len(composite_fks()) == 7
