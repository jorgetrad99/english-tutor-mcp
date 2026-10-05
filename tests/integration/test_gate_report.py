"""Gate report on db-test, seeded through the core services (spec section 14)."""

import json
import secrets
import zoneinfo
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from psycopg.errors import ReadOnlySqlTransaction
from repo_contract import DEFAULT_TURNS, sample_evidence
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from tutor.domain.glossary import IncomingItem
from tutor.domain.profile import ProfileInput
from tutor.domain.text import count_words
from tutor.domain.validation import Evidence, ReportedError
from tutor.mcp.observe import user_hash
from tutor.ops.gate_report import build_report, main, read_only_engine, render
from tutor.ops.report_role import REPORT_ROLE, REPORT_ROLE_SQL
from tutor.services.context import Services
from tutor.services.glossary import save_glossary
from tutor.services.lesson import start_lesson
from tutor.services.ports import Mode, UowFactory
from tutor.services.profile import save_profile
from tutor.services.session_end import end_session
from tutor.services.views import StartLessonRequest
from tutor.web.memory import FixedClock

pytestmark = pytest.mark.integration

DAY = date(2026, 10, 14)
TZ = "America/Mexico_City"
PROFILE = ProfileInput(
    self_level="B1",
    domains=["it"],
    use_cases=["standup", "code_review"],
    minutes_per_day=20,
    days_per_week=3,
    target_level="B2",
)


def _raw(ev: Evidence) -> dict[str, object]:
    return {
        "user_turns": list(ev.user_turns),
        "errors": [
            {"said": e.said, "correct": e.correct, "category": e.category} for e in ev.errors
        ],
        "chunks_used": list(ev.chunks_used),
    }


def _item(text_: str) -> IncomingItem:
    return IncomingItem(
        kind="term",
        text=text_,
        meaning="Team word.",
        context_sentence=f"We said {text_} in the standup.",
        domain="it",
    )


def _lesson(
    svc: Services,
    clock: FixedClock,
    uid: UUID,
    mode: Mode,
    minutes: int,
    evidence: Evidence,
    *,
    used: int = 0,
    glossary: bool = False,
) -> None:
    lesson = start_lesson(svc, uid, StartLessonRequest(mode=mode))
    if glossary:
        both = [_item("roll back"), _item("heads-up")]
        save_glossary(svc, uid, lesson.session_id, "confirmed", both)
        save_glossary(svc, uid, lesson.session_id, "provisional", [_item("on track")])
        save_glossary(svc, uid, lesson.session_id, "declined", [_item("blocker")])
    chunks = tuple(c.id for c in lesson.item.chunks)[:used]
    ev = replace(evidence, chunks_used=chunks)
    clock.advance(timedelta(minutes=minutes))
    end_session(svc, uid, lesson.session_id, ev, _raw(ev))
    clock.advance(timedelta(minutes=1))


@pytest.fixture
def seeded(
    uow_factory: UowFactory, now: datetime, user_id: UUID, other_user_id: UUID
) -> dict[str, str]:
    """Author (user_id): two closed voice lessons (2 and 3 min) and one incomplete text lesson.
    Friend (other_user_id): one closed text lesson (6 min) whose only error is rejected."""
    clock = FixedClock(now)
    svc = Services(
        uow=uow_factory, clock=clock, valid_timezones=frozenset(zoneinfo.available_timezones())
    )
    save_profile(svc, user_id, PROFILE)
    save_profile(svc, other_user_id, PROFILE)
    valid = ReportedError(said="I am blocked", correct="I'm blocked", category="grammar")
    rejected = ReportedError(said="I goed there", correct="I went there", category="grammar")
    _lesson(svc, clock, user_id, "voice", 2, sample_evidence(errors=[valid]), used=2, glossary=True)
    _lesson(svc, clock, user_id, "voice", 3, sample_evidence(), used=1)
    _lesson(svc, clock, user_id, "text", 1, sample_evidence(turns=["Short answer only."]))
    _lesson(svc, clock, other_user_id, "text", 6, sample_evidence(errors=[rejected]))
    return {user_hash(user_id): "author", user_hash(other_user_id): "dev-1"}


def test_report_counts_the_gate_numbers(engine: Engine, seeded: dict[str, str]) -> None:
    with engine.connect() as conn:
        report = build_report(conn, DAY, DAY, TZ, seeded, "author")
    words = sum(count_words(t) for t in DEFAULT_TURNS)
    out = render(report)
    assert "| author | 2 | 0 | 1 | 2 |" in out
    assert "| dev-1 | 1 | 1 | 0 | 0 |" in out
    assert "| All | 3 | 1 | 1 | 2 |" in out
    assert "| Real sessions (closed, low trust included) | 3 | >= 15 |" in out
    assert "| Voice sessions (closed) | 2 | >= 5 |" in out
    assert "| Author sessions (author) | 2 | >= 10 in 3 weeks |" in out
    assert (
        f"| Median user_words_per_min, voice | {(words / 2 + words / 3) / 2:.1f} | >= 20 |" in out
    )
    assert f"| Median user_words_per_min, text | {words / 6:.1f} | - |" in out
    assert "| Glossary confirmation rate | 50% (2 of 4) | >= 60% |" in out
    assert "| Activation rate | 20% (3 of 15 phrases) | - |" in out
    assert "| end_session validity (closed vs incomplete) | 75% (3 of 4) | - |" in out
    assert "| Rejected-error share | 50% (1 of 2) | - |" in out
    assert "purged" not in out  # no caveat line: the rate comes from glossary_saved audit rows


def test_confirmation_rate_survives_the_provisional_purge(
    engine: Engine,
    seeded: dict[str, str],
    uow_factory: UowFactory,
    user_id: UUID,
    now: datetime,
) -> None:
    with uow_factory(user_id) as uow:
        assert uow.glossary.purge(now + timedelta(days=8)) == 1  # "on track" expired
    with engine.connect() as conn:
        report = build_report(conn, DAY, DAY, TZ, seeded, "author")
    assert "| Glossary confirmation rate | 50% (2 of 4) | >= 60% |" in render(report)


def test_an_empty_window_reads_not_available(engine: Engine, seeded: dict[str, str]) -> None:
    with engine.connect() as conn:
        report = build_report(conn, DAY + timedelta(days=1), DAY + timedelta(days=2), TZ, {}, None)
    out = render(report)
    assert "| Median user_words_per_min, voice | n/a | >= 20 |" in out
    assert "| Glossary confirmation rate | n/a | >= 60% |" in out
    assert "| Author sessions (pass --author) | n/a | >= 10 in 3 weeks |" in out


@pytest.fixture(scope="module")
def report_url(engine: Engine) -> Iterator[str]:
    """The URL of a tutor_report role created from the documented SQL snippet."""
    password = secrets.token_hex(16)  # exists only for this test module
    with engine.begin() as conn:
        conn.exec_driver_sql(REPORT_ROLE_SQL)
        conn.execute(text(f"ALTER ROLE {REPORT_ROLE} PASSWORD '{password}'"))
    url = make_url(engine.url.render_as_string(hide_password=False)).set(
        username=REPORT_ROLE, password=password
    )
    yield url.render_as_string(hide_password=False)
    with engine.begin() as conn:
        conn.execute(text(f"DROP OWNED BY {REPORT_ROLE}"))
        conn.execute(text(f"DROP ROLE IF EXISTS {REPORT_ROLE}"))


def test_main_prints_labels_never_emails_or_ids(
    report_url: str,
    seeded: dict[str, str],
    user_id: UUID,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(seeded), encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GATE_REPORT_DATABASE_URL", report_url)
    code = main(
        [
            "--from",
            "2026-10-14",
            "--to",
            "2026-10-14",
            "--labels",
            str(labels),
            "--author",
            "author",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "| author | 2 |" in out
    assert "| All | 3 | 1 | 1 | 2 |" in out
    assert "ana@example.com" not in out and "beto@example.com" not in out
    assert str(user_id) not in out


def test_the_report_role_reads_only_what_it_needs(engine: Engine, report_url: str) -> None:
    report_engine = read_only_engine(report_url)
    try:
        with report_engine.connect() as conn:
            conn.execute(text("SELECT count(*) FROM audit_log"))
            conn.rollback()
            for sql in ("SELECT 1 FROM users", "SELECT 1 FROM profiles"):
                with pytest.raises(DBAPIError):
                    conn.execute(text(sql))
                conn.rollback()
            with pytest.raises(DBAPIError):
                conn.execute(text("DELETE FROM audit_log"))
    finally:
        report_engine.dispose()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolconfig "
                "FROM pg_roles WHERE rolname = :r"
            ),
            {"r": REPORT_ROLE},
        ).one()
    assert row[:4] == (False, False, False, True)
    assert "default_transaction_read_only=on" in row[4]


def test_the_role_snippet_is_idempotent(engine: Engine, report_url: str) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(REPORT_ROLE_SQL)


def test_build_report_runs_in_a_read_only_transaction(
    engine: Engine, seeded: dict[str, str]
) -> None:
    with engine.connect() as conn:
        build_report(conn, DAY, DAY, TZ, {}, None)
        assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"


def test_unlabelled_users_print_only_their_hash(
    engine: Engine, seeded: dict[str, str], user_id: UUID, other_user_id: UUID
) -> None:
    with engine.connect() as conn:
        out = render(build_report(conn, DAY, DAY, TZ, {}, None))
    assert f"| {user_hash(user_id)} | 2 |" in out
    assert str(user_id) not in out and str(other_user_id) not in out


def test_main_needs_the_report_url_and_ignores_other_urls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.delenv("GATE_REPORT_DATABASE_URL", raising=False)
    assert main(["--from", "2026-10-14"]) == 2
    assert "GATE_REPORT_DATABASE_URL" in capsys.readouterr().err
    # Only the app's and the migration URLs are set: the report refuses (and never prints them).
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:s3cret@localhost:1/x")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://own:s3cret@localhost:1/x")
    assert main(["--from", "2026-10-14"]) == 2
    err = capsys.readouterr().err
    assert "GATE_REPORT_DATABASE_URL" in err and "s3cret" not in err


def test_a_bad_or_unreachable_url_exits_with_a_fixed_message() -> None:
    for url in ("not a url", "postgresql://u:s3cret@127.0.0.1:1/db?connect_timeout=2"):
        with pytest.raises(SystemExit) as err:
            main(["--from", "2026-10-14", "--to", "2026-10-14", "--database-url", url])
        message = str(err.value)
        assert message == "cannot connect (check the report URL)"


def test_bad_dates_and_zone_exit_with_clear_messages(engine: Engine) -> None:
    url = engine.url.render_as_string(hide_password=False)
    with pytest.raises(SystemExit, match="time zone"):
        main(
            [
                "--from",
                "2026-10-14",
                "--to",
                "2026-10-14",
                "--tz",
                "Mars/Olympus",
                "--database-url",
                url,
            ]
        )
    with pytest.raises(SystemExit, match="before"):
        main(["--from", "2026-10-14", "--to", "2026-10-01", "--database-url", url])


def test_the_app_login_role_is_refused(login_engine: Engine) -> None:
    """Under FORCE ROW LEVEL SECURITY the app role would read zero rows: stop, do not mislead."""
    url = login_engine.url.render_as_string(hide_password=False)
    with pytest.raises(SystemExit, match="BYPASSRLS") as err:
        main(["--from", "2026-10-14", "--to", "2026-10-14", "--database-url", url])
    password = login_engine.url.password
    assert password is not None and password not in str(err.value)


def test_bad_labels_file_is_refused(tmp_path: Path, engine: Engine) -> None:
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"ana@example.com": "Ana"}), encoding="utf-8")
    url = engine.url.render_as_string(hide_password=False)
    with pytest.raises(SystemExit, match="12 hex"):
        main(
            [
                "--from",
                "2026-10-14",
                "--to",
                "2026-10-14",
                "--labels",
                str(labels),
                "--database-url",
                url,
            ]
        )


@pytest.mark.parametrize(
    "name",
    ["a | b", "dev-1\n", "a\nb", "a\x85b", "a\u2028b", "a\u2029b", "a\x1bb", "", "x" * 41],
)
def test_label_names_cannot_break_the_table(tmp_path: Path, engine: Engine, name: str) -> None:
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"0123456789ab": name}), encoding="utf-8")
    url = engine.url.render_as_string(hide_password=False)
    with pytest.raises(SystemExit, match="names"):
        main(
            [
                "--from",
                "2026-10-14",
                "--to",
                "2026-10-14",
                "--labels",
                str(labels),
                "--database-url",
                url,
            ]
        )


def test_the_author_name_follows_the_label_rule(engine: Engine) -> None:
    url = engine.url.render_as_string(hide_password=False)
    for author in ("a | b", "dev-1\n"):
        with pytest.raises(SystemExit, match="--author"):
            main(
                [
                    "--from",
                    "2026-10-14",
                    "--to",
                    "2026-10-14",
                    "--author",
                    author,
                    "--database-url",
                    url,
                ]
            )


def test_the_report_connection_is_read_only(engine: Engine) -> None:
    report_engine = read_only_engine(engine.url.render_as_string(hide_password=False))
    try:
        with pytest.raises(DBAPIError) as err, report_engine.connect() as conn:
            conn.execute(text("CREATE TABLE gate_report_probe (id int)"))
        assert isinstance(err.value.orig, ReadOnlySqlTransaction)
    finally:
        report_engine.dispose()
