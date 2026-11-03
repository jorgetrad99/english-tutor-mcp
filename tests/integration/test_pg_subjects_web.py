"""Materias, Materia and Nota on Postgres: ServicesSubjects end to end under row-level
security, as production connects (slice D Tasks 1-2; Review Focus 3, 5 and 7)."""

import html
import re
import zoneinfo
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from subject_web_fixtures import seed_subject_world
from web_pg_support import BASE, WEB_CONFIG, google_login, pg_settings, pg_web_app

from tutor.app import BodySizeGuard, RequestLog, build_app
from tutor.db.tables import users
from tutor.db.uow import PgIdentity, pg_uow_factory
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
