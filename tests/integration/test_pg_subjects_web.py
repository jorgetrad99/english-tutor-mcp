"""Materias, Materia and Nota on Postgres: ServicesSubjects end to end under row-level
security, as production connects (slice D Tasks 1-5; Review Focus 3-7)."""

import html
import re
import zoneinfo
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pdf_fixtures import build_pdf
from sqlalchemy import Engine, select
from subject_web_fixtures import seed_subject_world
from web_pg_support import (
    BASE,
    WEB_CONFIG,
    csrf_token,
    google_login,
    pg_settings,
    pg_web_app,
)

from tutor.app import BodySizeGuard, RequestLog, build_app
from tutor.db.tables import notes, users
from tutor.db.uow import PgIdentity, pg_uow_factory
from tutor.domain.notes import MAX_WEB_NOTE_CHARS
from tutor.services.context import Services
from tutor.services.progress import subject_progress
from tutor.web.memory import FakeGoogle
from tutor.web.ports import GoogleIdentity
from tutor.web.subjects import ServicesSubjects, install_frame_url, install_subjects

pytestmark = pytest.mark.integration

XSS = "<script>alert(1)</script>"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _frame_url(user_id: object, instance_id: object) -> str:
    return f"https://templates.testserver/app/instances/{instance_id}/raw?t=test"


def test_the_postgres_adapter_renders_the_three_pages(engine: Engine, login_engine: Engine) -> None:
    svc = Services(
        uow=pg_uow_factory(login_engine),
        clock=_utc_now,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    google = FakeGoogle()
    app = pg_web_app(login_engine, google)
    install_subjects(app, ServicesSubjects(svc))
    install_frame_url(app, _frame_url)
    beto = PgIdentity(login_engine).resolve("g-beto-subjects", None, "Beto", _utc_now()).id
    theirs = seed_subject_world(svc, beto, name="Bajo")
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        identity = GoogleIdentity("g-ana-subjects", "ana-subjects@example.com", "Ana", True)
        google_login(c, google, identity)
        with engine.connect() as conn:
            ana = conn.execute(
                select(users.c.id).where(users.c.google_sub == "g-ana-subjects")
            ).scalar_one()
        world = seed_subject_world(svc, ana, label=XSS)

        listing = c.get("/app/subjects")
        assert listing.status_code == 200
        assert f'href="/app/subjects/{world.subject_id}"' in listing.text
        assert "Bajo" not in listing.text

        subject = c.get(f"/app/subjects/{world.subject_id}").text
        assert XSS not in subject and "&lt;script&gt;alert(1)&lt;/script&gt;Clase 1" in subject
        assert subject.index("Apuntes en PDF") < subject.index("Curso de Guitarra")
        assert "subject-note--superseded" in subject
        assert subject.count('<tr id="template-') == 2
        progress = subject_progress(svc, ana, world.subject_id)
        totals = progress.totals
        footer = subject.split("<tfoot>", 1)[1].split("</tfoot>", 1)[0]
        assert re.findall(r"<td[^>]*>([^<]*)</td>", footer) == [
            str(n)
            for n in (
                totals.notes_ingested,
                totals.templates_created,
                totals.instances_created,
                totals.searches,
                totals.practice_started,
                totals.practice_completed,
                totals.practice_abandoned,
            )
        ]
        assert progress.unassigned.all_time.mean_score == 100 and "100 de 100" in subject

        note = c.get(f"/app/notes/{world.note_id}").text
        assert XSS not in note
        frames = re.findall(r"<iframe\b[^>]*>", note)
        assert len(frames) == 2
        assert all('sandbox="allow-scripts"' in f for f in frames)
        assert f'src="{_frame_url(ana, world.instance_id)}"' in frames[0]
        assert str(world.retired_instance_id) not in note

        # Review Focus 3: Beto's ids, missing ids and malformed ids are 404 for Ana.
        for path in (
            f"/app/subjects/{theirs.subject_id}",
            f"/app/notes/{theirs.note_id}",
            f"/app/subjects/{uuid4()}",
            f"/app/notes/{uuid4()}",
            "/app/subjects/not-a-uuid",
        ):
            assert c.get(path).status_code == 404, path
        empty = c.get(f"/app/subjects/{world.empty_subject_id}").text
        assert "Aún no hay respuestas en esta materia." in empty
    port = ServicesSubjects(svc)
    assert port.instance_document(ana, theirs.instance_id) is None
    document = port.instance_document(ana, world.retired_instance_id)
    assert document is not None and document.count('id="tutor-params"') == 1


ORIGIN = "https://templates.testserver"


def test_build_app_frames_a_note_on_the_template_origin_with_a_single_use_token(
    engine: Engine, login_engine: Engine, tmp_path: Path
) -> None:
    settings = replace(
        pg_settings(),
        oauth_storage_dir=tmp_path / "oauth",
        template_origin=ORIGIN,
        frame_token_key="f" * 48,
    )
    app = build_app(
        settings, engine=login_engine, web_config=replace(WEB_CONFIG, template_origin=ORIGIN)
    )
    logged = app.web_app
    assert isinstance(logged, RequestLog) and isinstance(logged.app, BodySizeGuard)
    web = logged.app.app
    assert isinstance(web, FastAPI)
    google = FakeGoogle()
    web.state.deps = replace(web.state.deps, google=google)  # no real Google in tests
    svc = Services(
        uow=pg_uow_factory(login_engine),
        clock=_utc_now,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    with (
        TestClient(app, base_url=BASE, follow_redirects=False) as dashboard,
        TestClient(app, base_url=ORIGIN, follow_redirects=False) as templates,
    ):
        identity = GoogleIdentity("g-ana-frames", "ana-frames@example.com", "Ana", True)
        google_login(dashboard, google, identity)
        with engine.connect() as conn:
            ana = conn.execute(
                select(users.c.id).where(users.c.google_sub == "g-ana-frames")
            ).scalar_one()
        world = seed_subject_world(svc, ana)
        note = dashboard.get(f"/app/notes/{world.note_id}")
        assert note.status_code == 200
        sources = re.findall(r'<iframe\b[^>]*\ssrc="([^"]+)"', note.text)
        assert len(sources) == 2
        src = html.unescape(sources[0])
        prefix = f"{ORIGIN}/app/instances/{world.instance_id}/raw?t="
        assert src.startswith(prefix)
        path = src.removeprefix(ORIGIN)
        served = templates.get(path)
        assert served.status_code == 200
        assert served.headers["content-type"] == "text/html; charset=utf-8"
        assert served.text.count('id="tutor-params"') == 1
        assert templates.get(path).status_code == 404  # single use
        assert dashboard.get(path).status_code == 404  # never on the dashboard host


LESSON = "El acorde de septima tiene cuatro notas"
PASSWORD = "clave"  # noqa: S105 - a fixture's throwaway password
EXTRA = {"extra": ("", b"", "application/octet-stream")}  # makes the body multipart


def test_upload_and_paste_on_postgres_through_build_app(
    engine: Engine, login_engine: Engine, tmp_path: Path
) -> None:
    """Slice D Task 4: real multipart bodies under BodySizeGuard with a session cookie,
    the PDF parsed in its child process, the rows read back as the owner."""
    settings = replace(
        pg_settings(),
        oauth_storage_dir=tmp_path / "oauth",
        template_origin=ORIGIN,
        frame_token_key="f" * 48,
    )
    app = build_app(
        settings, engine=login_engine, web_config=replace(WEB_CONFIG, template_origin=ORIGIN)
    )
    logged = app.web_app
    assert isinstance(logged, RequestLog) and isinstance(logged.app, BodySizeGuard)
    web = logged.app.app
    assert isinstance(web, FastAPI)
    google = FakeGoogle()
    web.state.deps = replace(web.state.deps, google=google)  # no real Google in tests
    svc = Services(
        uow=pg_uow_factory(login_engine),
        clock=_utc_now,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    beto = PgIdentity(login_engine).resolve("g-beto-uploads", None, "Beto", _utc_now()).id
    theirs = seed_subject_world(svc, beto, name="Bajo")
    long_text = ("Acorde de séptima. " * 30_000)[:MAX_WEB_NOTE_CHARS]  # over 400 KB
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        identity = GoogleIdentity("g-ana-uploads", "ana-uploads@example.com", "Ana", True)
        google_login(c, google, identity)
        with engine.connect() as conn:
            ana = conn.execute(
                select(users.c.id).where(users.c.google_sub == "g-ana-uploads")
            ).scalar_one()
        world = seed_subject_world(svc, ana)
        token = csrf_token(engine, c)
        pdf_path = f"/app/subjects/{world.subject_id}/pdf"
        text_path = f"/app/subjects/{world.subject_id}/text"

        uploaded = c.post(
            pdf_path,
            data={"csrf_token": token, "title": ""},
            files={"file": ("Armonia.pdf", build_pdf([LESSON]), "application/pdf")},
        )
        assert uploaded.status_code == 303, uploaded.text
        pdf_note = UUID(uploaded.headers["location"].removeprefix("/app/notes/"))
        again = c.post(
            pdf_path,
            data={"csrf_token": token, "title": "Armonia"},
            files={"file": ("Armonia.pdf", build_pdf([LESSON]), "application/pdf")},
            headers={"hx-request": "true"},
        )
        assert again.status_code == 200 and "Ese material ya estaba guardado." in again.text
        locked = build_pdf([LESSON], password=PASSWORD)
        refused = c.post(
            pdf_path,
            data={"csrf_token": token, "title": "Cifrado"},
            files={"file": ("c.pdf", locked, "application/pdf")},
        )
        assert refused.status_code == 422 and "protegido con contraseña" in refused.text

        form = {"csrf_token": token, "title": "Clase pegada", "lesson_ref": "L7"}
        pasted = c.post(text_path, data={**form, "text": long_text}, files=EXTRA)
        assert pasted.status_code == 303, pasted.text
        text_note = UUID(pasted.headers["location"].removeprefix("/app/notes/"))

        foreign = c.post(
            f"/app/subjects/{theirs.subject_id}/text",
            data={"csrf_token": token, "title": "x", "text": "y"},
            files=EXTRA,
        )
        assert foreign.status_code == 404
        over = c.post(
            text_path,
            data={"csrf_token": token, "title": "x"},
            files={"text": ("t.txt", b"a" * (2 * 1024 * 1024 + 1), "text/plain")},
        )
        assert over.status_code == 413  # the guard's own answer past the route's 2 MB

    with engine.connect() as conn:
        saved = {
            r.id: r
            for r in conn.execute(
                select(notes.c.id, notes.c.origin, notes.c.raw_text).where(
                    notes.c.id.in_([pdf_note, text_note])
                )
            )
        }
    assert saved[pdf_note].origin == "web" and saved[pdf_note].raw_text == LESSON
    assert saved[text_note].origin == "web" and len(saved[text_note].raw_text) == len(long_text)
