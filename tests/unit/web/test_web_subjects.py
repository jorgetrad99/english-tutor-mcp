"""Materias, Materia and Nota: the subjects port and its pages (subjects spec 9; slice D plan
rulings 50, 53-56, 61, 62, 72, 73; Review Focus 3, 5 and 7)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from subject_web_fixtures import SubjectWorld, seed_subject_world

from tutor.services.progress import subject_progress
from tutor.web.demo import DemoUsers
from tutor.web.subjects import MemorySubjects

pytestmark = pytest.mark.unit


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
