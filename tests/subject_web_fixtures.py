"""A learner's subject world for the website tests, built through the use cases (slice D).

Not collected (no test_ prefix). The same builder seeds the in-memory store (unit tests) and
Postgres (integration tests), so both adapters render the same rows. `label` is prefixed to
every learner text that a page shows (note titles, concepts, template names, prompts), so a test
can pass markup and check it renders as text (Review Focus 5).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from template_fixtures import HTML, NUMERIC, TABLE, numeric_params, table_params

from tutor.domain.notes import InstanceInput, NoteInput, SourceInput
from tutor.domain.subjects import SubjectInput
from tutor.domain.templates import TEMPLATE_GUIDE_VERSION, ParamPair
from tutor.services.context import Services
from tutor.services.notes import save_notes
from tutor.services.practice import end_practice, record_result, start_practice
from tutor.services.subjects import save_subject
from tutor.services.templates import create_instance, save_template


@dataclass(frozen=True, slots=True)
class SubjectWorld:
    subject_id: UUID
    empty_subject_id: UUID  # a second subject with no notes, templates or practice
    note_id: UUID  # course source, lesson 1, with the numeric and table instances
    second_note_id: UUID  # course source, lesson 2 (replaced the superseded one)
    superseded_note_id: UUID
    pdf_note_id: UUID  # pdf source
    template_id: UUID  # numeric
    table_template_id: UUID
    instance_id: UUID  # numeric, on note_id
    display_instance_id: UUID  # table, on note_id
    retired_instance_id: UUID  # replaced by instance_id
    session_id: UUID  # a closed text session with one right answer


def subject_input(name: str) -> SubjectInput:
    return SubjectInput(
        name=name,
        goal_text="Tocar en la boda",
        context_text="Clases los sábados",
        preference="balanced",
        exercise_kinds=["quiz", "exercise"],
        minutes_per_session=30,
        sessions_per_week=3,
        target_date=None,
        status=None,
    )


def _note(title: str, lesson_ref: str, position: int, text: str, **changes: Any) -> NoteInput:
    return dataclasses.replace(
        NoteInput(
            title=title, lesson_ref=lesson_ref, position=position, raw_text=text, concepts=()
        ),
        **changes,
    )


def seed_subject_world(
    svc: Services, user_id: UUID, *, name: str = "Guitarra", label: str = ""
) -> SubjectWorld:
    subject_id = save_subject(svc, user_id, subject_input(name)).subject.id
    empty = save_subject(svc, user_id, subject_input(f"{name} 2")).subject.id
    course = SourceInput("course", f"Curso de {name}")
    lesson1 = _note(
        f"{label}Clase 1",
        "L1",
        1,
        "El acorde de séptima tiene cuatro notas.",
        concepts=(f"{label}Acordes",),
    )
    old2 = _note(f"{label}Clase 2", "L2", 2, "Primera versión de la clase dos.")
    saved = save_notes(svc, user_id, subject_id, course, [lesson1, old2])
    note_id, superseded_id = (n.note_id for n in saved.notes)
    new2 = dataclasses.replace(old2, raw_text="Segunda versión de la clase dos.")
    second_id = save_notes(svc, user_id, subject_id, course, [new2]).notes[0].note_id
    pdf = SourceInput("pdf", "Apuntes en PDF")
    pdf_id = (
        save_notes(svc, user_id, subject_id, pdf, [_note("Resumen", "R1", 1, "Un resumen.")])
        .notes[0]
        .note_id
    )
    numeric = dataclasses.replace(NUMERIC, name=f"{label}Respuesta numérica")
    template_id = save_template(
        svc, user_id, subject_id, numeric, HTML, TEMPLATE_GUIDE_VERSION
    ).template_id
    table_id = save_template(
        svc, user_id, subject_id, TABLE, HTML, TEMPLATE_GUIDE_VERSION
    ).template_id

    def instance(
        template: UUID, params: tuple[ParamPair, ...], prompt: str, replaces: UUID | None = None
    ) -> UUID:
        data = InstanceInput(template, params, f"{label}{prompt}", None, replaces)
        return create_instance(svc, user_id, subject_id, data, note_id).instance_id

    retired = instance(template_id, numeric_params(3.0), "Suma vieja.")
    instance_id = instance(template_id, numeric_params(4.0), "Suma dos y dos.", retired)
    display_id = instance(table_id, table_params(), "Mira la tabla.")
    started = start_practice(svc, user_id, subject_id, "text")
    assert started.first is not None
    record_result(
        svc,
        user_id,
        started.session_id,
        started.first.item_id,
        {"answer_kind": "number", "value": "4"},
    )
    end_practice(svc, user_id, started.session_id)
    return SubjectWorld(
        subject_id=subject_id,
        empty_subject_id=empty,
        note_id=note_id,
        second_note_id=second_id,
        superseded_note_id=superseded_id,
        pdf_note_id=pdf_id,
        template_id=template_id,
        table_template_id=table_id,
        instance_id=instance_id,
        display_instance_id=display_id,
        retired_instance_id=retired,
        session_id=started.session_id,
    )
