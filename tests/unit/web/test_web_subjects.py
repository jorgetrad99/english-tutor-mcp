"""Materias, Materia and Nota: the subjects port and its pages (subjects spec 9; slice D plan
rulings 50, 53-56, 61, 62, 72, 73; Review Focus 3, 5 and 7)."""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from pdf_fixtures import build_pdf
from subject_web_fixtures import SubjectWorld, seed_subject_world
from template_fixtures import numeric_params

from tutor.domain.dashboard.types import Lang
from tutor.domain.notes import MAX_WEB_NOTE_CHARS, InstanceInput, NoteInput
from tutor.services import pdf_text
from tutor.services._pdf_worker import parse
from tutor.services.errors import ServiceError
from tutor.services.notes import get_whole_note, list_notes, save_notes
from tutor.services.pdf_text import MAX_PDF_BYTES, MAX_PDF_PAGES, PdfProblem, read_answer
from tutor.services.progress import subject_progress
from tutor.services.templates import list_templates
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend
from tutor.web.subjects import MemorySubjects
from tutor.web.views import Views

from .conftest import csrf_of, fake_frame_url

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


def test_the_memory_adapter_writes_only_for_the_owner(
    subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld, beto_world: SubjectWorld
) -> None:
    saved = subjects.add_text(demo.ana, ana_world.subject_id, "Texto", None, "Hola.")
    assert saved.outcome == "new"
    with pytest.raises(ServiceError) as caught:
        subjects.add_text(demo.ana, beto_world.subject_id, "Texto", None, "Hola.")
    assert caught.value.code == "not_found"
    assert subjects.template(demo.ana, beto_world.template_id) is None
    assert subjects.retire_template(demo.ana, beto_world.template_id) is None
    ref = subjects.template(demo.ana, ana_world.template_id)
    assert ref is not None and ref.subject_id == ana_world.subject_id and not ref.retired
    assert ref.card.active_instances == 1
    assert subjects.retire_template(demo.ana, ana_world.template_id) == "retired"
    assert subjects.retire_template(demo.ana, ana_world.template_id) == "already_retired"
    again = subjects.template(demo.ana, ana_world.template_id)
    assert again is not None and again.retired


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


# --- uploads and pasted text (Task 4) -----------------------------------------------------------

LESSON = "El acorde de septima tiene cuatro notas"
PDF_NAME = "Clase-secreta-de-armonia.pdf"
PASSWORD = "clave"  # noqa: S105 - a fixture's throwaway password


@pytest.fixture
def in_process_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """The child's parse in this process (fast); `extract`'s own checks still run first."""

    def run(data: bytes, timeout_s: float) -> str | PdfProblem:
        return read_answer(json.dumps(parse(data)).encode())

    monkeypatch.setattr(pdf_text, "_run_child", run)


def _notes(subjects: MemorySubjects, user: UUID, subject_id: UUID) -> int:
    return len(list_notes(subjects.svc, user, subject_id))


def _hx(htmx: bool) -> dict[str, str]:
    return {"hx-request": "true"} if htmx else {}


def _post_pdf(
    c: TestClient,
    subject_id: UUID | str,
    data: bytes,
    *,
    title: str = "",
    name: str = PDF_NAME,
    htmx: bool = False,
) -> Response:
    return c.post(
        f"/app/subjects/{subject_id}/pdf",
        data={"csrf_token": csrf_of(c), "title": title},
        files={"file": (name, data, "application/pdf")},
        headers=_hx(htmx),
    )


def _post_text(
    c: TestClient,
    subject_id: UUID | str,
    *,
    title: str = "Clase 3",
    lesson_ref: str = "",
    text: str = "La escala mayor tiene siete notas.",
    htmx: bool = False,
) -> Response:
    form = {"csrf_token": csrf_of(c), "title": title, "lesson_ref": lesson_ref, "text": text}
    # multipart, as the form sends it (its enctype), so a long paste fits the 2 MB route limit
    return c.post(
        f"/app/subjects/{subject_id}/text",
        data=form,
        files={"extra": ("", b"", "application/octet-stream")},
        headers=_hx(htmx),
    )


def _note_of(response: Response) -> UUID:
    return UUID(response.headers["location"].removeprefix("/app/notes/"))


def test_the_subject_page_shows_both_upload_forms(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    html = login(demo.ana).get(f"/app/subjects/{ana_world.subject_id}").text
    forms = _section(html, 'id="upload-forms"', "</section>")
    assert f'action="/app/subjects/{ana_world.subject_id}/pdf"' in forms
    assert f'action="/app/subjects/{ana_world.subject_id}/text"' in forms
    assert forms.count('enctype="multipart/form-data"') == 2
    assert forms.count('name="csrf_token"') == 2
    assert 'accept="application/pdf"' in forms and 'maxlength="400000"' in forms
    assert "Hasta 400,000 caracteres." in forms


def test_a_pdf_upload_adds_a_web_note_and_redirects_to_it(
    login: Login,
    subjects: MemorySubjects,
    demo: DemoUsers,
    ana_world: SubjectWorld,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    c = login(demo.ana)
    response = _post_pdf(c, ana_world.subject_id, build_pdf([LESSON]))  # through the child
    assert response.status_code == 303
    note_id = _note_of(response)
    note = get_whole_note(subjects.svc, demo.ana, note_id)
    assert note.origin == "web" and note.clean_text == LESSON
    assert note.title == "Clase-secreta-de-armonia"  # the file name without its extension
    assert note.source_title == note.title and note.passage_count >= 1
    rows = {r.id: r for r in list_notes(subjects.svc, demo.ana, ana_world.subject_id)}
    assert rows[note_id].source_kind == "pdf"
    assert LESSON not in caplog.text and "secreta" not in caplog.text


def test_the_same_pdf_twice_is_existing_and_another_pdf_is_new(
    login: Login,
    subjects: MemorySubjects,
    demo: DemoUsers,
    ana_world: SubjectWorld,
    in_process_parse: None,
) -> None:
    c = login(demo.ana)
    pdf = build_pdf([LESSON])
    first = _post_pdf(c, ana_world.subject_id, pdf, title="Armonía")
    count = _notes(subjects, demo.ana, ana_world.subject_id)
    again = _post_pdf(c, ana_world.subject_id, pdf, title="Armonía", htmx=True)
    assert again.status_code == 200 and "<html" not in again.text
    assert "Ese material ya estaba guardado." in again.text
    assert f'href="/app/notes/{_note_of(first)}"' in again.text
    assert _notes(subjects, demo.ana, ana_world.subject_id) == count
    other = _post_pdf(c, ana_world.subject_id, build_pdf(["Otra"]), title="Armonía", htmx=True)
    # No lesson ref, so nothing to supersede: a new note under the same source.
    assert "Apunte guardado." in other.text and "ya estaba guardado" not in other.text
    assert _notes(subjects, demo.ana, ana_world.subject_id) == count + 1
    assert 'id="upload-forms"' in other.text and 'hx-swap-oob="true"' in other.text


def test_a_title_given_wins_over_the_file_name_and_a_long_name_is_cut(
    login: Login,
    subjects: MemorySubjects,
    demo: DemoUsers,
    ana_world: SubjectWorld,
    in_process_parse: None,
) -> None:
    c = login(demo.ana)
    named = _post_pdf(c, ana_world.subject_id, build_pdf([LESSON]), title="  Mi   resumen ")
    assert get_whole_note(subjects.svc, demo.ana, _note_of(named)).title == "Mi resumen"
    long_name = "C:\\carpeta\\" + "x" * 250 + ".pdf"
    cut = _post_pdf(c, ana_world.subject_id, build_pdf(["Otra"]), name=long_name)
    assert cut.status_code == 303
    assert get_whole_note(subjects.svc, demo.ana, _note_of(cut)).title == "x" * 200


def _too_large() -> bytes:
    return b"%PDF-" + b"0" * (MAX_PDF_BYTES + 1 - 5)


def _too_long() -> bytes:
    half = MAX_WEB_NOTE_CHARS // 2
    return build_pdf(["a" * half, "b" * half])


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (_too_large, "El PDF pasa de 20 MB."),
        (lambda: b"no soy un pdf", "El archivo no es un PDF."),
        (lambda: build_pdf([LESSON], password=PASSWORD), "El PDF está protegido con contraseña."),
        (lambda: build_pdf(["a"] * (MAX_PDF_PAGES + 1)), "El PDF tiene más de 200 páginas."),
        (lambda: build_pdf(["", ""]), "El PDF no tiene texto."),
        (_too_long, "El texto del PDF pasa de 400,000 caracteres."),
        (lambda: b"%PDF-1.7 roto", "No pudimos leer el PDF."),
    ],
    ids=["too_large", "not_pdf", "encrypted", "too_many_pages", "no_text", "too_long", "bad"],
)
def test_review_focus_4_pdfs_that_should_fail(
    login: Login,
    subjects: MemorySubjects,
    demo: DemoUsers,
    ana_world: SubjectWorld,
    in_process_parse: None,
    make: Callable[[], bytes],
    message: str,
) -> None:
    # Review Focus 4
    c = login(demo.ana)
    before = _notes(subjects, demo.ana, ana_world.subject_id)
    data = make()
    page = _post_pdf(c, ana_world.subject_id, data, title="Armonía")
    assert page.status_code == 422 and "<html" in page.text and message in page.text
    assert 'id="pdf-file-error"' in page.text and 'value="Armonía"' in page.text
    partial = _post_pdf(c, ana_world.subject_id, data, title="Armonía", htmx=True)
    assert partial.status_code == 422 and "<html" not in partial.text and message in partial.text
    assert _notes(subjects, demo.ana, ana_world.subject_id) == before


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        (PdfProblem.BUSY, "Hay demasiados PDF procesándose; intenta en un momento."),
        (PdfProblem.TIMEOUT, "El PDF tardó demasiado en leerse."),
    ],
)
def test_a_busy_or_slow_parse_is_a_field_message(
    login: Login,
    subjects: MemorySubjects,
    demo: DemoUsers,
    ana_world: SubjectWorld,
    monkeypatch: pytest.MonkeyPatch,
    problem: PdfProblem,
    message: str,
) -> None:
    monkeypatch.setattr(pdf_text, "_run_child", lambda data, timeout_s: problem)
    before = _notes(subjects, demo.ana, ana_world.subject_id)
    page = _post_pdf(login(demo.ana), ana_world.subject_id, build_pdf([LESSON]))
    assert page.status_code == 422 and message in page.text
    assert _notes(subjects, demo.ana, ana_world.subject_id) == before


def test_review_focus_4_pdf_form_fields_that_should_fail(
    login: Login, subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    # Review Focus 4: a title of 201 characters; no file chosen.
    c = login(demo.ana)
    before = _notes(subjects, demo.ana, ana_world.subject_id)
    long_title = _post_pdf(c, ana_world.subject_id, build_pdf([LESSON]), title="t" * 201)
    assert long_title.status_code == 422 and "Máximo 200 caracteres." in long_title.text
    assert 'id="pdf-title-error"' in long_title.text
    none = _post_pdf(c, ana_world.subject_id, b"", name="")
    assert none.status_code == 422 and "Elige un archivo PDF." in none.text
    assert "Escribe un título." in none.text  # no file name to take the title from
    assert _notes(subjects, demo.ana, ana_world.subject_id) == before


def test_pasted_text_adds_a_web_note(
    login: Login, subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    c = login(demo.ana)
    response = _post_text(c, ana_world.subject_id, lesson_ref="L3")
    assert response.status_code == 303
    note = get_whole_note(subjects.svc, demo.ana, _note_of(response))
    assert note.origin == "web" and note.lesson_ref == "L3" and note.source_title == "Clase 3"
    rows = {r.id: r for r in list_notes(subjects.svc, demo.ana, ana_world.subject_id)}
    assert rows[note.note_id].source_kind == "text"
    again = _post_text(c, ana_world.subject_id, lesson_ref="L3", htmx=True)
    assert "Ese material ya estaba guardado." in again.text
    other = _post_text(c, ana_world.subject_id, lesson_ref="L3", text="Otra versión.", htmx=True)
    assert "reemplaza la versión anterior" in other.text


def test_a_paste_of_exactly_400000_characters_is_saved(
    login: Login, subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    text = ("Acorde de séptima. " * 30_000)[: MAX_WEB_NOTE_CHARS - 1] + "x"
    response = _post_text(login(demo.ana), ana_world.subject_id, text=text)
    assert response.status_code == 303
    note = get_whole_note(subjects.svc, demo.ana, _note_of(response))
    assert len(note.clean_text) == MAX_WEB_NOTE_CHARS


@pytest.mark.parametrize(
    ("fields", "error_id", "message"),
    [
        ({"text": "   "}, "text-body-error", "Pega el texto de tu material."),
        ({"text": "a" * (MAX_WEB_NOTE_CHARS + 1)}, "text-body-error", "Máximo 400,000 caracteres."),
        ({"title": ""}, "text-title-error", "Escribe un título."),
        ({"title": "t" * 201}, "text-title-error", "Máximo 200 caracteres."),
        ({"lesson_ref": "l" * 121}, "text-lesson-error", "Máximo 120 caracteres."),
    ],
    ids=["empty", "too_long", "no_title", "long_title", "long_lesson"],
)
def test_review_focus_4_pasted_text_that_should_fail(
    login: Login,
    subjects: MemorySubjects,
    demo: DemoUsers,
    ana_world: SubjectWorld,
    fields: dict[str, str],
    error_id: str,
    message: str,
) -> None:
    # Review Focus 4
    c = login(demo.ana)
    before = _notes(subjects, demo.ana, ana_world.subject_id)
    page = _post_text(c, ana_world.subject_id, **fields)  # type: ignore[arg-type]
    assert page.status_code == 422 and "<html" in page.text
    assert f'id="{error_id}"' in page.text and message in page.text
    partial = _post_text(c, ana_world.subject_id, htmx=True, **fields)  # type: ignore[arg-type]
    assert partial.status_code == 422 and "<html" not in partial.text and message in partial.text
    assert _notes(subjects, demo.ana, ana_world.subject_id) == before


def test_review_focus_3_uploads_to_foreign_or_missing_subjects_are_not_found(
    login: Login,
    subjects: MemorySubjects,
    demo: DemoUsers,
    beto_world: SubjectWorld,
    in_process_parse: None,
) -> None:
    # Review Focus 3 (POSTs): 404 and nothing stored.
    c = login(demo.ana)
    before = _notes(subjects, demo.beto, beto_world.subject_id)
    for sid in (str(beto_world.subject_id), str(uuid4()), "nope", "0" * 40):
        assert _post_pdf(c, sid, build_pdf([LESSON]), title="x").status_code == 404, sid
        assert _post_text(c, sid).status_code == 404, sid
        assert _post_text(c, sid, htmx=True).status_code == 404, sid
    assert _notes(subjects, demo.beto, beto_world.subject_id) == before


# --- retiring a template (Task 5) ---------------------------------------------------------------


def _retire(c: TestClient, template_id: UUID | str, *, htmx: bool = False) -> Response:
    return c.post(
        f"/app/templates/{template_id}/retire",
        data={"csrf_token": csrf_of(c)},
        headers=_hx(htmx),
    )


def test_each_template_row_has_a_retire_form(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    html = login(demo.ana).get(f"/app/subjects/{ana_world.subject_id}").text
    row = _section(html, f'<tr id="template-{ana_world.template_id}"', "</tr>")
    assert f'action="/app/templates/{ana_world.template_id}/retire"' in row
    assert 'name="csrf_token"' in row and 'method="post"' in row
    confirm = "¿Retirar esta plantilla? Claude ya no la usará para ejercicios nuevos."
    assert f'hx-confirm="{confirm}"' in row


def test_review_focus_6_retiring_from_the_website(
    login: Login, subjects: MemorySubjects, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    # Review Focus 6
    c = login(demo.ana)
    response = _retire(c, ana_world.template_id)
    assert response.status_code == 303
    assert response.headers["location"] == f"/app/subjects/{ana_world.subject_id}"
    listed = list_templates(subjects.svc, demo.ana, ana_world.subject_id).templates
    assert ana_world.template_id not in [t.template_id for t in listed]
    data = InstanceInput(ana_world.template_id, numeric_params(5.0), "Suma.", None, None)
    note = NoteInput(title="Clase 9", lesson_ref="L9", position=9, raw_text="x", instances=[data])
    with pytest.raises(ServiceError) as caught:
        save_notes(subjects.svc, demo.ana, ana_world.subject_id, None, [note])
    assert caught.value.code == "not_found"
    page = c.get(f"/app/subjects/{ana_world.subject_id}").text
    assert f'/app/templates/{ana_world.template_id}/retire"' not in page
    assert f'/app/templates/{ana_world.table_template_id}/retire"' in page
    second = _retire(c, ana_world.template_id, htmx=True)
    assert second.status_code == 200  # already_retired is not an error
    assert _retire(c, ana_world.template_id).status_code == 303
    # Amendment A56: its instances stay active and shown.
    note_page = c.get(f"/app/notes/{ana_world.note_id}").text
    assert len(FRAME.findall(note_page)) == 2 and "Respuesta numérica" in note_page


def test_the_htmx_retire_answer_is_the_row_without_its_button(
    login: Login, demo: DemoUsers, ana_world: SubjectWorld
) -> None:
    row = _retire(login(demo.ana), ana_world.template_id, htmx=True)
    assert row.status_code == 200 and "<html" not in row.text
    assert f'<tr id="template-{ana_world.template_id}"' in row.text
    assert "<form" not in row.text and "Retirada" in row.text
    assert "Respuesta numérica" in row.text


def test_review_focus_3_retiring_a_foreign_or_missing_template_changes_nothing(
    login: Login, subjects: MemorySubjects, demo: DemoUsers, beto_world: SubjectWorld
) -> None:
    # Review Focus 3 (the retire POST)
    c = login(demo.ana)
    for tid in (beto_world.template_id, uuid4(), "nope", "0" * 40):
        assert _retire(c, tid).status_code == 404, tid
        assert _retire(c, tid, htmx=True).status_code == 404, tid
    listed = list_templates(subjects.svc, demo.beto, beto_world.subject_id).templates
    assert beto_world.template_id in [t.template_id for t in listed]
