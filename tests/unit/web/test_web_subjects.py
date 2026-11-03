"""Materias, Materia and Nota: the subjects port and its pages (subjects spec 9; slice D plan
rulings 50, 53-56, 61, 62, 72, 73; Review Focus 3, 5 and 7)."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from subject_web_fixtures import SubjectWorld, seed_subject_world

from tutor.domain.dashboard.types import Lang
from tutor.services.progress import subject_progress
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend
from tutor.web.subjects import MemorySubjects
from tutor.web.views import Views

from .conftest import fake_frame_url

pytestmark = pytest.mark.unit

Login = Callable[[UUID], TestClient]


@pytest.fixture
def ana_world(subjects: MemorySubjects, demo: DemoUsers) -> SubjectWorld:
    subjects.ensure_user(demo.ana)
    return seed_subject_world(subjects.svc, demo.ana)


# --- the port (Task 1) --------------------------------------------------------------------------


def test_the_memory_adapter_returns_none_for_another_learners_ids(
    subjects: MemorySubjects, demo: DemoUsers, beto_world: SubjectWorld
) -> None:
    ana = demo.ana
    assert subjects.subjects(ana).subjects == ()
    assert subjects.subject(ana, beto_world.subject_id) is None
    assert subjects.note(ana, beto_world.note_id) is None
    assert subjects.instance_document(ana, beto_world.instance_id) is None
    assert subjects.subject(ana, uuid4()) is None
    assert subjects.note(ana, uuid4()) is None
    assert subjects.instance_document(ana, uuid4()) is None
    assert [s.id for s in subjects.subjects(demo.beto).subjects] == [
        beto_world.subject_id,
        beto_world.empty_subject_id,
    ]


def test_the_subject_page_groups_notes_lists_templates_history_and_progress(
    subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    page = subjects.subject(demo.ana, ana_world.subject_id)
    assert page is not None
    assert page.subject.subject.name == "Guitarra" and page.subject.plan_status is not None
    assert [(g.title, g.kind) for g in page.sources] == [
        ("Apuntes en PDF", "pdf"),
        ("Curso de Guitarra", "course"),
    ]
    course = page.sources[1]
    # The superseded lesson 2 comes right after the note that replaced it.
    assert [n.id for n in course.notes] == [
        ana_world.note_id,
        ana_world.second_note_id,
        ana_world.superseded_note_id,
    ]
    assert [n.superseded for n in course.notes] == [False, False, True]
    counts = {t.id: t.active_instances for t in page.templates}
    assert counts == {ana_world.template_id: 1, ana_world.table_template_id: 1}
    assert [h.session_id for h in page.history] == [ana_world.session_id]
    assert page.history[0].counted and page.history[0].answered == 1
    assert page.progress == subject_progress(subjects.svc, demo.ana, ana_world.subject_id)


def test_the_note_page_lists_active_instances_with_their_template(
    subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    page = subjects.note(demo.ana, ana_world.note_id)
    assert page is not None
    assert page.note.title == "Clase 1" and page.note.concepts == ("Acordes",)
    assert [(i.id, i.template_name, i.purpose) for i in page.instances] == [
        (ana_world.instance_id, "Respuesta numérica", "exercise"),
        (ana_world.display_instance_id, "Tabla de la lección", "display"),
    ]
    assert page.instances[0].prompt_text == "Suma dos y dos."


def test_instance_documents_are_served_retired_ones_included(
    subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    current = subjects.instance_document(demo.ana, ana_world.instance_id)
    retired = subjects.instance_document(demo.ana, ana_world.retired_instance_id)
    assert current is not None and retired is not None
    assert current.count('id="tutor-params"') == 1


def test_the_writes_are_left_to_later_tasks(
    subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    with pytest.raises(NotImplementedError):
        subjects.add_pdf(demo.ana, ana_world.subject_id, "PDF", b"%PDF-")
    with pytest.raises(NotImplementedError):
        subjects.add_text(demo.ana, ana_world.subject_id, "Texto", None, "Hola.")
    with pytest.raises(NotImplementedError):
        subjects.retire_template(demo.ana, ana_world.template_id)


# --- the pages (Task 2) -------------------------------------------------------------------------

XSS = "<script>alert(1)</script>"
FRAME = re.compile(r"<iframe\b[^>]*>")


def _section(html: str, start: str, end: str) -> str:
    return html.split(start, 1)[1].split(end, 1)[0]


def test_materias_lists_each_subject_with_its_plan(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    html = login(demo.ana).get("/app/subjects").text
    assert f'href="/app/subjects/{ana_world.subject_id}"' in html
    assert f'href="/app/subjects/{ana_world.empty_subject_id}"' in html
    assert "Guitarra" in html and "Sesiones por semana" in html and "Cumplimiento" in html
    assert 'aria-current="page"' in html and 'href="/app/subjects" aria-current="page"' in html


def test_a_new_learner_sees_the_empty_state(login: Login, demo: DemoUsers) -> None:
    html = login(demo.nuevo).get("/app/subjects").text
    assert "Pídele a Claude que cree una materia" in html


def test_the_htmx_partial_is_not_a_whole_page(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    partial = login(demo.ana).get("/app/subjects", headers={"hx-request": "true"}).text
    assert "<html" not in partial and 'id="subjects-list"' in partial


def test_one_string_in_english(
    login: Login, demo: DemoUsers, backend: MemoryBackend, ana_world: SubjectWorld
) -> None:
    c = login(demo.ana)
    backend.users[demo.ana] = dataclasses.replace(backend.users[demo.ana], lang=Lang.EN)
    assert "Subjects" in c.get("/app/subjects").text
    subject = c.get(f"/app/subjects/{ana_world.subject_id}").text
    assert "Practice plan" in subject and "Activity by week" in subject
    assert "You can try the exercise here" in c.get(f"/app/notes/{ana_world.note_id}").text


def test_materia_shows_settings_plan_notes_templates_and_history(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    html = login(demo.ana).get(f"/app/subjects/{ana_world.subject_id}").text
    assert "Tocar en la boda" in html and "Clases los sábados" in html
    assert html.index("Apuntes en PDF") < html.index("Curso de Guitarra")  # by source title
    notes = _section(html, 'id="notes-title"', "</section>\n  </section>")
    order = [ana_world.note_id, ana_world.second_note_id, ana_world.superseded_note_id]
    positions = [html.index(f'href="/app/notes/{i}"') for i in order]
    assert positions == sorted(positions)
    assert "subject-note--superseded" in html and "Reemplazada" in html
    assert notes
    assert html.count('<tr id="template-') == 2
    assert "Respuesta numérica" in html and "Número" in html
    assert "Cuenta para el plan" in html and "1 de 1" in html
    assert "slice D Task 4" not in html  # the placeholder is a Jinja comment


def test_a_subject_with_no_notes_shows_its_empty_states(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    html = login(demo.ana).get(f"/app/subjects/{ana_world.empty_subject_id}").text
    assert "Aún no hay apuntes en esta materia" in html
    assert "Aún no hay plantillas" in html and "Aún no has practicado" in html
    # Review Focus 7: the empty state and the activity table.
    assert "Aún no hay respuestas en esta materia." in html
    assert "Actividad por semana" in html and "<tfoot>" in html


def test_review_focus_7_progreso_shows_the_servers_numbers(
    login: Login, subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    # Review Focus 7
    html = login(demo.ana).get(f"/app/subjects/{ana_world.subject_id}").text
    progress = subject_progress(subjects.svc, demo.ana, ana_world.subject_id)
    section = _section(html, 'id="progress-title"', 'id="notes-title"')
    assert f'<span class="streak__num" aria-hidden="true">{progress.streak.current_days}' in section
    assert f"Máxima: {progress.streak.longest_days} días" in section
    unassigned = progress.unassigned.all_time
    assert unassigned.attempts == 1 and unassigned.mean_score == 100
    assert "Sin concepto" in section and "100 de 100" in section
    assert [c.name for c in progress.concepts] == ["Acordes"]
    assert "Acordes" in section and "Sin practicar" in section
    assert progress.totals.practice_started == 1 and progress.totals.notes_ingested == 4
    footer = _section(section, "<tfoot>", "</tfoot>")
    cells = re.findall(r"<td[^>]*>([^<]*)</td>", footer)
    totals = progress.totals
    assert cells == [
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
    assert section.count("<tbody>") == 3  # concepts, templates, weeks
    assert len(re.findall(r"<tr>", _section(section, "Actividad por semana", "<tfoot>"))) == 9
    assert f"{progress.over_cap} respuestas de más de 10 minutos" in section
    for forbidden in ("<canvas", "<svg", "<script", " style="):
        assert forbidden not in section


def test_progreso_never_shows_another_learners_practice(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld, beto_world: SubjectWorld
) -> None:
    html = login(demo.ana).get(f"/app/subjects/{ana_world.subject_id}").text
    assert "Bajo" not in html and str(beto_world.session_id) not in html


def test_duration_seconds_in_both_languages(app: FastAPI) -> None:
    views: Views = app.state.views
    es, en = views.envs[Lang.ES_MX], views.envs[Lang.EN]
    assert es.filters["duration_seconds"](45) == "45 s"
    assert es.filters["duration_seconds"](65) == "1 min 5 s"
    assert en.filters["duration_seconds"](60) == "1 min 0 s"
    assert en.filters["duration_seconds"](3725) == "62 min 5 s"


def test_nota_shows_the_text_concepts_and_one_frame_per_active_instance(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    html = login(demo.ana).get(f"/app/notes/{ana_world.note_id}").text
    assert '<pre class="note-text">El acorde de séptima tiene cuatro notas.</pre>' in html
    assert '<li class="chip chip--muted">Acordes</li>' in html
    assert "la calificación cuenta solo en una práctica con Claude" in html
    frames = FRAME.findall(html)
    assert len(frames) == 2  # the retired instance is not listed
    for frame, (iid, purpose) in zip(
        frames,
        [(ana_world.instance_id, "exercise"), (ana_world.display_instance_id, "display")],
        strict=True,
    ):
        assert 'sandbox="allow-scripts"' in frame and "srcdoc" not in frame
        assert f'src="{fake_frame_url(demo.ana, iid)}"' in frame
        assert f"instance-frame--{purpose}" in frame
    assert str(ana_world.retired_instance_id) not in html
    assert '<p class="instance-prompt">Suma dos y dos.</p>' in html
    assert "Respuesta numérica" in html and "Tabla de la lección" in html


def test_a_note_without_instances_says_so(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    html = login(demo.ana).get(f"/app/notes/{ana_world.pdf_note_id}").text
    assert "<iframe" not in html and "Esta nota aún no tiene ejercicios." in html


def test_review_focus_5_learner_markup_renders_as_text(
    login: Login, subjects: MemorySubjects, demo: DemoUsers
) -> None:
    # Review Focus 5
    subjects.ensure_user(demo.ana)
    world = seed_subject_world(subjects.svc, demo.ana, label=XSS)
    c = login(demo.ana)
    for path in (
        "/app/subjects",
        f"/app/subjects/{world.subject_id}",
        f"/app/notes/{world.note_id}",
    ):
        html = c.get(path).text
        assert XSS not in html, path
    subject = c.get(f"/app/subjects/{world.subject_id}").text
    note = c.get(f"/app/notes/{world.note_id}").text
    escaped = "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert f"{escaped}Clase 1" in subject  # note title
    assert f"{escaped}Respuesta numérica" in subject  # template name
    assert f"{escaped}Acordes" in subject  # concept name in Progreso
    assert f"{escaped}Acordes" in note  # concept chip
    assert f'title="{escaped}Suma dos y dos."' in note  # prompt_text in the frame title
    assert f'<p class="instance-prompt">{escaped}Suma dos y dos.</p>' in note


def test_review_focus_3_foreign_and_missing_ids_are_not_found(
    login: Login, demo: DemoUsers, beto_world: SubjectWorld
) -> None:
    # Review Focus 3 (GETs)
    c = login(demo.ana)
    for path in (
        f"/app/subjects/{beto_world.subject_id}",
        f"/app/notes/{beto_world.note_id}",
        f"/app/notes/{beto_world.superseded_note_id}",
        f"/app/subjects/{uuid4()}",
        f"/app/notes/{uuid4()}",
        "/app/subjects/nope",
        "/app/notes/nope",
    ):
        assert c.get(path).status_code == 404, path
