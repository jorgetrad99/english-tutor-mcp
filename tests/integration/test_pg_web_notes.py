"""`save_web_note` on Postgres (slice D ruling 57): origin web, the 400,000-character cap,
idempotency and supersede, the audit event with counts only, and user isolation."""

import zoneinfo
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from repo_contract import long_paragraph, sample_subject
from sqlalchemy import Engine, text

from tutor.domain.notes import MAX_WEB_NOTE_CHARS, SourceInput
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.notes import get_note, save_web_note
from tutor.services.ports import UowFactory
from tutor.services.subject_views import NoteOutcome

pytestmark = pytest.mark.integration

PDF = SourceInput("pdf", "Apuntes de armonía")


def services(uow_factory: UowFactory, clock: list[datetime]) -> Services:
    return Services(
        uow=uow_factory,
        clock=lambda: clock[0],
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )


def test_save_web_note_on_postgres(
    engine: Engine, uow_factory: UowFactory, user_id: UUID, other_user_id: UUID, now: datetime
) -> None:
    clock = [now]
    svc = services(uow_factory, clock)
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject("Armonía"), now).id
    long_text = ("palabra " * (MAX_WEB_NOTE_CHARS // 8))[:MAX_WEB_NOTE_CHARS]
    big = save_web_note(svc, user_id, subject_id, PDF, "Libro entero", None, long_text)
    lesson = long_paragraph("Uno.") + "\n\n" + long_paragraph("Dos.")
    clock[0] = now + timedelta(minutes=1)
    first = save_web_note(svc, user_id, subject_id, PDF, "Clase 1", "C1", lesson)
    clock[0] = now + timedelta(minutes=1, seconds=30)  # one instant per event: ORDER BY at
    again = save_web_note(svc, user_id, subject_id, PDF, "Clase 1", "C1", lesson)
    clock[0] = now + timedelta(minutes=2)
    fixed = save_web_note(svc, user_id, subject_id, PDF, "Clase 1", "C1", lesson + " Fin.")
    assert (big.outcome, first.outcome, fixed.outcome) == ("new", "new", "superseded")
    assert again == NoteOutcome(first.note_id, "existing")
    view = get_note(svc, user_id, fixed.note_id)
    assert (view.origin, view.supersedes_note_id, view.source_title, view.concepts) == (
        "web",
        first.note_id,
        "Apuntes de armonía",
        (),
    )
    assert get_note(svc, user_id, first.note_id).passage_count == 2
    with pytest.raises(ServiceError) as info:
        save_web_note(svc, user_id, subject_id, PDF, "Más", None, long_text + "x")
    assert (info.value.code, info.value.fields) == ("validation_failed", ("raw_text",))
    with pytest.raises(ServiceError) as foreign:
        save_web_note(svc, other_user_id, subject_id, PDF, "Ajena", None, "texto")
    assert (foreign.value.code, foreign.value.fields) == ("not_found", ("subject_id",))
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT origin FROM notes WHERE subject_id = :s"), {"s": subject_id}
        ).all()
        sources = conn.execute(
            text("SELECT kind, title FROM sources WHERE subject_id = :s"), {"s": subject_id}
        ).all()
        metas = conn.execute(
            text(
                "SELECT meta FROM audit_log WHERE user_id = :u AND event = 'notes_saved' "
                "ORDER BY at"
            ),
            {"u": user_id},
        ).all()
    assert [r.origin for r in rows] == ["web", "web", "web"]
    assert [tuple(s) for s in sources] == [("pdf", "Apuntes de armonía")]
    assert [
        (m.meta["origin"], m.meta["new"], m.meta["existing"], m.meta["superseded"]) for m in metas
    ] == [
        ("web", 1, 0, 0),
        ("web", 1, 0, 0),
        ("web", 0, 1, 0),
        ("web", 0, 0, 1),
    ]
    for m in metas:
        assert set(m.meta) == {
            "subject_id",
            "source_id",
            "origin",
            "new",
            "existing",
            "superseded",
            "instances",
        }
