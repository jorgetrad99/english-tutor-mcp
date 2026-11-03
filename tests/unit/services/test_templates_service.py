"""Templates and instances on the in-memory store (subjects spec 15; plan rulings 21-29)."""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from template_fixtures import (
    CHOICE,
    FLASHCARD,
    HTML,
    KEY_NAME,
    LEAKS,
    NUMERIC,
    POOL_NAME,
    TABLE,
    choice_params,
    flashcard_params,
    numeric_params,
    pool,
    table_params,
)

from tutor.domain.html_guard import MAX_TEMPLATE_BYTES, TEMPLATE_MARKER
from tutor.domain.notes import InstanceInput, NoteInput
from tutor.domain.templates import (
    GUIDE_SECTIONS,
    TEMPLATE_GUIDE_VERSION,
    ParamPair,
    PoolEntryInput,
    TemplateInput,
)
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.memory import MemoryStore
from tutor.services.notes import get_note, save_notes
from tutor.services.practice import start_practice
from tutor.services.subject_views import InstanceSaved, TemplateSaved
from tutor.services.template_files import bridge, inline_guide
from tutor.services.templates import (
    create_instance,
    get_instance_view,
    get_template_guide,
    instance_counts,
    list_templates,
    retire_template,
    save_template,
    web_instance_document,
)

from .conftest import FixedClock, create_subject

pytestmark = pytest.mark.unit

V = TEMPLATE_GUIDE_VERSION


def events(store: MemoryStore, user_id: UUID, event: str) -> list[dict[str, Any]]:
    return [a.meta for a in store.tables.audit if a.user_id == user_id and a.event == event]


def saved(svc: Services, user_id: UUID, subject_id: UUID, data: TemplateInput) -> UUID:
    return save_template(svc, user_id, subject_id, data, HTML, V).template_id


def note(**changes: Any) -> NoteInput:
    data: dict[str, Any] = {
        "title": "Clase 1",
        "lesson_ref": "Módulo 1 / Clase 1",
        "position": 1,
        "raw_text": "La cuenta, por favor. Dos más dos son cuatro.",
    }
    data.update(changes)
    return NoteInput(**data)


def fields_of(info: pytest.ExceptionInfo[ServiceError]) -> tuple[str, tuple[str, ...]]:
    return info.value.code, info.value.fields


# --- the guide ----------------------------------------------------------------------------------


@pytest.mark.parametrize("section", GUIDE_SECTIONS)
def test_get_template_guide_for_every_section(section: Any) -> None:
    view = get_template_guide(section)
    assert (view.guide_version, view.section) == (V, section)
    assert view.text.strip()


def test_the_default_guide_section_is_the_contract() -> None:
    assert get_template_guide().section == "contract"


# --- list_templates -----------------------------------------------------------------------------


def test_a_subject_without_templates_gets_the_inline_guide(svc: Services, user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id)
    view = list_templates(svc, user_id, subject_id)
    assert (view.guide_version, view.templates, view.guide) == (V, (), inline_guide())
    saved(svc, user_id, subject_id, NUMERIC)
    view = list_templates(svc, user_id, subject_id)
    assert view.guide is None and [t.name for t in view.templates] == ["Respuesta numérica"]
    [listed] = view.templates
    assert (listed.practice_kind, listed.grading_kind, listed.html, listed.status) == (
        "quiz",
        "numeric",
        None,
        "active",
    )


def test_list_one_template_with_its_html(svc: Services, user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id)
    template_id = saved(svc, user_id, subject_id, TABLE)
    [one] = list_templates(svc, user_id, subject_id, template_id, include_html=True).templates
    assert (one.template_id, one.html, one.purpose) == (template_id, HTML, "display")
    [bare] = list_templates(svc, user_id, subject_id, template_id).templates
    assert bare.html is None
    with pytest.raises(ServiceError) as info:
        list_templates(svc, user_id, subject_id, include_html=True)
    assert fields_of(info) == ("validation_failed", ("include_html",))


def test_review_focus_5_list_templates_of_another_user(
    svc: Services, user_id: UUID, other_user_id: UUID
) -> None:
    theirs = create_subject(svc, other_user_id)
    their_template = saved(svc, other_user_id, theirs, TABLE)
    mine = create_subject(svc, user_id)
    for subject_id in (theirs, uuid4()):
        with pytest.raises(ServiceError) as info:
            list_templates(svc, user_id, subject_id)
        assert fields_of(info) == ("not_found", ("subject_id",))
    for template_id in (their_template, uuid4()):
        with pytest.raises(ServiceError) as info:
            list_templates(svc, user_id, mine, template_id)
        assert fields_of(info) == ("not_found", ("template_id",))


# --- save_template ------------------------------------------------------------------------------


def test_save_template_new_existing_and_superseded(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = create_subject(svc, user_id)
    first = save_template(svc, user_id, subject_id, FLASHCARD, HTML, V)
    assert (first.practice_kind, first.outcome) == ("drill", "new")  # derived, never sent
    again = save_template(svc, user_id, subject_id, FLASHCARD, HTML, V)
    assert again == TemplateSaved(first.template_id, "drill", "existing")
    changed = dataclasses.replace(FLASHCARD, text_match="fold_all")
    replaced = save_template(
        svc, user_id, subject_id, changed, HTML, V, replaces_template_id=first.template_id
    )
    assert (replaced.outcome, replaced.practice_kind) == ("superseded", "drill")
    assert replaced.template_id != first.template_id
    [active] = list_templates(svc, user_id, subject_id).templates
    assert (active.template_id, active.supersedes_template_id) == (
        replaced.template_id,
        first.template_id,
    )
    [old] = list_templates(svc, user_id, subject_id, first.template_id).templates
    assert old.status == "retired"
    retry = save_template(
        svc, user_id, subject_id, changed, HTML, V, replaces_template_id=first.template_id
    )
    assert retry == TemplateSaved(replaced.template_id, "drill", "existing")
    assert [e["template_outcome"] for e in events(store, user_id, "template_saved")] == [
        "new",
        "existing",
        "superseded",
        "existing",
    ]
    meta = events(store, user_id, "template_saved")[0]
    assert meta == {
        "template_id": str(first.template_id),
        "subject_id": str(subject_id),
        "template_outcome": "new",
        "grading_kind": "exact_text",
        "pool_draw": "one",
        "bytes": len(HTML.encode("utf-8")),
        "guide_version": V,
    }


def test_save_template_name_collision_and_validation(svc: Services, user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id)
    saved(svc, user_id, subject_id, NUMERIC)
    other = dataclasses.replace(CHOICE, name="  respuesta   NUMÉRICA ")
    with pytest.raises(ServiceError) as info:
        save_template(svc, user_id, subject_id, other, HTML, V)
    assert fields_of(info) == ("validation_failed", ("name",))
    # Review Focus 9: every field at once, the html included; nothing stored.
    bad = dataclasses.replace(CHOICE, options_param=None, name="")
    with pytest.raises(ServiceError) as info:
        save_template(svc, user_id, subject_id, bad, "<p>no</p>", V + 1)
    assert fields_of(info) == (
        "validation_failed",
        ("guide_version", "name", "options_param", "html"),
    )
    assert len(list_templates(svc, user_id, subject_id).templates) == 1


@pytest.mark.parametrize(
    "html",
    [
        # Review Focus 4: escape attempts.
        HTML.replace("<main>", '<main><iframe src="https://x.test"></iframe>'),
        HTML.replace("<main>", '<main><meta http-equiv="refresh" content="0">'),
        HTML.replace("<main>", '<main><a href="https://x.test">x</a>'),
        HTML.replace("<main>", '<main><img src="data:image/png;base64,AA">'),
        HTML.replace("<main>", '<main><p style="background: url(x)">x</p>'),
        HTML.replace(TEMPLATE_MARKER, ""),  # Review Focus 9: no marker
        HTML.replace(TEMPLATE_MARKER, TEMPLATE_MARKER * 2),  # two markers
        HTML.replace("<main>", "<main>" + "x" * (MAX_TEMPLATE_BYTES + 1 - len(HTML.encode()))),
    ],
    ids=["iframe", "meta", "href", "data", "css", "no_marker", "two_markers", "40001_bytes"],
)
def test_save_template_refuses_bad_html(
    svc: Services, store: MemoryStore, user_id: UUID, html: str
) -> None:
    subject_id = create_subject(svc, user_id)
    with pytest.raises(ServiceError) as info:
        save_template(svc, user_id, subject_id, TABLE, html, V)
    assert fields_of(info) == ("validation_failed", ("html",))
    assert store.tables.exercise_templates == {}
    assert events(store, user_id, "template_saved") == []


def test_save_template_accepts_inline_script_style_and_fragments_at_the_byte_cap(
    svc: Services, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    html = HTML.replace("<main>", '<main><a href="#top">arriba</a>')
    html = html.replace("<main>", "<main>" + "x" * (MAX_TEMPLATE_BYTES - len(html.encode())))
    assert len(html.encode()) == MAX_TEMPLATE_BYTES
    assert save_template(svc, user_id, subject_id, TABLE, html, V).outcome == "new"


def test_review_focus_5_save_template_with_foreign_ids(
    svc: Services, store: MemoryStore, user_id: UUID, other_user_id: UUID
) -> None:
    theirs = create_subject(svc, other_user_id)
    their_template = saved(svc, other_user_id, theirs, TABLE)
    mine = create_subject(svc, user_id)
    other = create_subject(svc, user_id, name="Piano")
    other_template = saved(svc, user_id, other, TABLE)
    with pytest.raises(ServiceError) as info:
        save_template(svc, user_id, theirs, TABLE, HTML, V)
    assert fields_of(info) == ("not_found", ("subject_id",))
    for replaces in (their_template, other_template, uuid4()):
        with pytest.raises(ServiceError) as info:
            save_template(svc, user_id, mine, TABLE, HTML, V, replaces_template_id=replaces)
        assert fields_of(info) == ("not_found", ("replaces_template_id",))
    assert list_templates(svc, user_id, mine).templates == ()


# --- create_instance and get_instance_view ------------------------------------------------------


def test_create_instance_new_then_existing_with_a_preview(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    template_id = saved(svc, user_id, subject_id, NUMERIC)
    data = InstanceInput(template_id, numeric_params(), "Resuelve.")
    first = create_instance(svc, user_id, subject_id, data, include_document=True)
    assert (first.outcome, first.purpose) == ("new", "exercise")
    assert first.text == "Resuelve.\nEtiqueta problem: 2 + 2\nResponde con un número."
    assert first.document is not None
    assert first.document.count('id="tutor-params"') == 1 and 'id="tutor-bridge"' in first.document
    again = create_instance(svc, user_id, subject_id, data, include_document=True)
    assert again == dataclasses.replace(first, outcome="existing")
    plain = create_instance(svc, user_id, subject_id, data)  # the default: no document
    assert plain == dataclasses.replace(first, outcome="existing", document=None)
    view = get_instance_view(svc, user_id, first.instance_id)
    assert (view.document, view.text, view.purpose, view.status) == (
        first.document,
        first.text,
        "exercise",
        "active",
    )
    assert [e["instance_outcome"] for e in events(store, user_id, "instance_created")] == [
        "new",
        "existing",
        "existing",
    ]
    assert events(store, user_id, "instance_created")[0] == {
        "instance_id": str(first.instance_id),
        "subject_id": str(subject_id),
        "note_id": None,
        "origin": "class",
        "grading_kind": "numeric",
        "instance_outcome": "new",
    }


def test_review_focus_7_views_never_show_keys_reveals_or_undrawn_entries(
    svc: Services, user_id: UUID
) -> None:
    # Review Focus 7 (plan ruling 25)
    subject_id = create_subject(svc, user_id)
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    flash = saved(svc, user_id, subject_id, FLASHCARD)
    shown: list[InstanceSaved] = [
        create_instance(
            svc,
            user_id,
            subject_id,
            InstanceInput(numeric, numeric_params(), "R."),
            include_document=True,
        ),
        create_instance(
            svc,
            user_id,
            subject_id,
            InstanceInput(flash, flashcard_params(6), "T."),
            include_document=True,
        ),
    ]
    for result in shown:
        view = get_instance_view(svc, user_id, result.instance_id)
        assert result.document is not None
        for text in (result.document, result.text, view.document, view.text):
            assert not any(leak in text for leak in LEAKS), text
    # The pool preview shows exactly one drawn cue, the same on every view.
    pool_doc = shown[1].document
    assert pool_doc is not None
    assert sum(f"cue {i}" in pool_doc for i in range(6)) == 1
    assert get_instance_view(svc, user_id, shown[1].instance_id).document == pool_doc


def test_create_instance_on_a_note_and_replacing_one(svc: Services, user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id)
    note_id = save_notes(svc, user_id, subject_id, None, [note()]).notes[0].note_id
    template_id = saved(svc, user_id, subject_id, CHOICE)
    first = create_instance(
        svc, user_id, subject_id, InstanceInput(template_id, choice_params(1), "Elige."), note_id
    )
    fixed = create_instance(
        svc,
        user_id,
        subject_id,
        InstanceInput(template_id, choice_params(2), "Elige.", None, first.instance_id),
        note_id,
    )
    assert fixed.outcome == "superseded"  # plan ruling 28
    instances = get_note(svc, user_id, note_id).instances
    assert [(i.instance_id, i.seq) for i in instances] == [(fixed.instance_id, 2)]
    assert get_instance_view(svc, user_id, first.instance_id).status == "retired"


def test_create_instance_errors(svc: Services, user_id: UUID, other_user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id)
    other = create_subject(svc, user_id, name="Piano")
    template_id = saved(svc, user_id, subject_id, CHOICE)
    other_template = saved(svc, user_id, other, CHOICE)
    other_note = save_notes(svc, user_id, other, None, [note()]).notes[0].note_id
    theirs = create_subject(svc, other_user_id)
    their_note = save_notes(svc, other_user_id, theirs, None, [note()]).notes[0].note_id
    good = InstanceInput(template_id, choice_params(), "Elige.")
    cases: list[tuple[UUID, InstanceInput, UUID | None, tuple[str, tuple[str, ...]]]] = [
        (theirs, good, None, ("not_found", ("subject_id",))),
        (subject_id, good, their_note, ("not_found", ("note_id",))),
        (subject_id, good, other_note, ("not_found", ("note_id",))),
        (
            subject_id,
            dataclasses.replace(good, template_id=other_template),
            None,
            ("not_found", ("template_id",)),
        ),
        (
            subject_id,
            dataclasses.replace(good, replaces_instance_id=uuid4()),
            None,
            ("not_found", ("replaces_instance_id",)),
        ),
        (
            subject_id,
            dataclasses.replace(good, params=choice_params(5)),
            None,
            ("validation_failed", ("params[2].integer",)),
        ),
        (
            subject_id,
            dataclasses.replace(good, prompt_text=" "),
            None,
            ("validation_failed", ("prompt_text",)),
        ),
    ]
    for target, data, note_id, expected in cases:
        with pytest.raises(ServiceError) as info:
            create_instance(svc, user_id, target, data, note_id)
        assert fields_of(info) == expected
    with pytest.raises(ServiceError) as info:
        get_instance_view(svc, user_id, uuid4())
    assert fields_of(info) == ("not_found", ("instance_id",))


def test_review_focus_5_instances_of_another_user_are_not_found(
    svc: Services, user_id: UUID, other_user_id: UUID
) -> None:
    theirs = create_subject(svc, other_user_id)
    their_template = saved(svc, other_user_id, theirs, TABLE)
    their_instance = create_instance(
        svc, other_user_id, theirs, InstanceInput(their_template, table_params(), "Mira.")
    )
    mine = create_subject(svc, user_id)
    with pytest.raises(ServiceError) as info:
        get_instance_view(svc, user_id, their_instance.instance_id)
    assert fields_of(info) == ("not_found", ("instance_id",))
    with pytest.raises(ServiceError) as info:
        create_instance(svc, user_id, mine, InstanceInput(their_template, table_params(), "M."))
    assert fields_of(info) == ("not_found", ("template_id",))
    replacing = InstanceInput(
        saved(svc, user_id, mine, TABLE), table_params(), "M.", None, their_instance.instance_id
    )
    with pytest.raises(ServiceError) as info:
        create_instance(svc, user_id, mine, replacing)
    assert fields_of(info) == ("not_found", ("replaces_instance_id",))


# --- save_notes and get_note with instances -----------------------------------------------------


def test_save_notes_with_instances_of_two_templates(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    flash = saved(svc, user_id, subject_id, FLASHCARD)
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    lesson = note(
        concepts=("Vocabulario",),
        instances=(
            InstanceInput(flash, flashcard_params(5), "Traduce.", "Vocabulario"),
            InstanceInput(numeric, numeric_params(), "Suma."),
        ),
    )
    result = save_notes(svc, user_id, subject_id, None, [lesson])
    [outcome] = result.notes
    assert outcome.outcome == "new" and len(outcome.instance_ids) == 2
    view = get_note(svc, user_id, outcome.note_id)
    first, second = view.instances
    assert (first.instance_id, first.seq, first.template_id) == (outcome.instance_ids[0], 1, flash)
    assert (first.practice_kind, first.grading_kind, first.concept) == (
        "drill",
        "exact_text",
        "Vocabulario",
    )
    assert (first.params, first.pool_entries, first.needs_view) == ({"hint": None}, 5, False)
    assert (second.params, second.pool_entries, second.prompt_text) == (
        {"problem": "2 + 2"},
        None,
        "Suma.",
    )
    assert events(store, user_id, "notes_saved")[0]["instances"] == 2
    # Review Focus 7: get_note shows no answer key and no pool entry.
    dumped = json.dumps([dataclasses.asdict(i) for i in view.instances], default=str)
    assert not any(leak in dumped for leak in LEAKS)
    assert "cue 0" not in dumped
    # The same lesson again: existing, with the instances it already has.
    again = save_notes(svc, user_id, subject_id, None, [lesson]).notes[0]
    assert (again.outcome, again.instance_ids) == ("existing", outcome.instance_ids)


def test_save_notes_instances_are_all_or_nothing(
    svc: Services, store: MemoryStore, user_id: UUID, other_user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    flash = saved(svc, user_id, subject_id, FLASHCARD)
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    retired = saved(svc, user_id, subject_id, TABLE)
    save_template(
        svc,
        user_id,
        subject_id,
        dataclasses.replace(TABLE, name="Tabla nueva"),
        HTML,
        V,
        replaces_template_id=retired,
    )
    other_subject = create_subject(svc, user_id, name="Piano")
    foreign = saved(svc, user_id, other_subject, TABLE)
    bad_pool = (*pool(3), PoolEntryInput("cue", " "))
    cases: list[tuple[list[NoteInput], tuple[str, tuple[str, ...]]]] = [
        (
            [
                note(),
                note(
                    title="Clase 2",
                    lesson_ref="2",
                    instances=(
                        InstanceInput(numeric, numeric_params(), "Ok."),
                        InstanceInput(retired, table_params(), "Retirada."),
                        InstanceInput(foreign, table_params(), "De otra materia."),
                        InstanceInput(uuid4(), (), "No existe."),
                    ),
                ),
            ],
            (
                "not_found",
                (
                    "notes[1].instances[1].template_id",
                    "notes[1].instances[2].template_id",
                    "notes[1].instances[3].template_id",
                ),
            ),
        ),
        (
            [
                note(
                    instances=(
                        InstanceInput(flash, (ParamPair(POOL_NAME, "pool", bad_pool),), "T."),
                        InstanceInput(numeric, (ParamPair(KEY_NAME, "number", "x"),), "S."),
                    )
                )
            ],
            (
                "validation_failed",
                (
                    "notes[0].instances[0].params[0].pool[3].answer",
                    "notes[0].instances[1].params[0].number",
                    "notes[0].instances[1].params",
                ),
            ),
        ),
    ]
    for notes, expected in cases:
        with pytest.raises(ServiceError) as info:
            save_notes(svc, user_id, subject_id, None, notes)
        assert fields_of(info) == expected
    assert store.tables.notes == {}
    assert store.tables.template_instances == {}
    assert events(store, user_id, "notes_saved") == []
    theirs = create_subject(svc, other_user_id)
    with pytest.raises(ServiceError) as info:  # Review Focus 5
        save_notes(
            svc,
            other_user_id,
            theirs,
            None,
            [note(instances=(InstanceInput(numeric, numeric_params(), "S."),))],
        )
    assert fields_of(info) == ("not_found", ("notes[0].instances[0].template_id",))


def test_a_corrected_lesson_can_replace_its_instances(svc: Services, user_id: UUID) -> None:
    # Plan ruling 28 through save_notes: the new note's instance retires the old one.
    subject_id = create_subject(svc, user_id)
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    first = save_notes(
        svc,
        user_id,
        subject_id,
        None,
        [note(instances=(InstanceInput(numeric, numeric_params(5), "Suma."),))],
    ).notes[0]
    fixed = save_notes(
        svc,
        user_id,
        subject_id,
        None,
        [
            note(
                raw_text="La cuenta, por favor. Dos más dos son cuatro, corregido.",
                instances=(
                    InstanceInput(numeric, numeric_params(4), "Suma.", None, first.instance_ids[0]),
                ),
            )
        ],
    ).notes[0]
    assert fixed.outcome == "superseded"
    assert get_note(svc, user_id, first.note_id).instances == ()  # retired, out of the list
    assert len(get_note(svc, user_id, fixed.note_id).instances) == 1
    assert get_instance_view(svc, user_id, first.instance_ids[0]).status == "retired"


# --- concepts of instances (plan ruling 30) -----------------------------------------------------


def test_an_instance_concept_must_exist_or_be_the_notes(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    other = create_subject(svc, user_id, name="Piano")
    save_notes(svc, user_id, other, None, [note(concepts=("Ajeno",))])
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    concepts_before = len(store.tables.concepts)
    with pytest.raises(ServiceError) as info:
        create_instance(
            svc, user_id, subject_id, InstanceInput(numeric, numeric_params(), "S.", "Suma")
        )
    assert fields_of(info) == ("not_found", ("concept",))
    lesson = note(
        concepts=("Suma",),
        instances=(
            InstanceInput(numeric, numeric_params(), "S.", "  SUMA "),  # the note's own
            InstanceInput(numeric, numeric_params(5), "S.", "Ajeno"),  # another subject's
        ),
    )
    with pytest.raises(ServiceError) as info:
        save_notes(svc, user_id, subject_id, None, [lesson])
    assert fields_of(info) == ("not_found", ("notes[0].instances[1].concept",))
    assert len(store.tables.concepts) == concepts_before  # nothing written
    saved_note = save_notes(
        svc,
        user_id,
        subject_id,
        None,
        [dataclasses.replace(lesson, instances=lesson.instances[:1])],
    ).notes[0]
    [instance] = get_note(svc, user_id, saved_note.note_id).instances
    assert instance.concept == "Suma"  # the note's spelling
    # Now "Suma" exists in the subject: create_instance may name it, and creates no concept.
    count = len(store.tables.concepts)
    made = create_instance(
        svc, user_id, subject_id, InstanceInput(numeric, numeric_params(7), "S.", "suma")
    )
    assert store.tables.template_instances[made.instance_id].concept_id is not None
    assert len(store.tables.concepts) == count


def test_a_note_with_one_concept_gives_it_to_instances_without_one(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    one = note(concepts=("Suma",), instances=(InstanceInput(numeric, numeric_params(), "S."),))
    two = note(
        title="Clase 2",
        lesson_ref="2",
        concepts=("Suma", "Resta"),
        instances=(InstanceInput(numeric, numeric_params(9), "S."),),
    )
    first, second = save_notes(svc, user_id, subject_id, None, [one, two]).notes
    assert [i.concept for i in get_note(svc, user_id, first.note_id).instances] == ["Suma"]
    assert [i.concept for i in get_note(svc, user_id, second.note_id).instances] == [None]
    assert sorted(c.name for c in store.tables.concepts.values()) == ["Resta", "Suma"]


# --- the website's reads (slice D rulings 50 and 59) -------------------------------------------


def test_web_instance_document_uses_the_web_bridge_and_the_same_preview_draw(
    svc: Services, user_id: UUID, other_user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    flash = saved(svc, user_id, subject_id, FLASHCARD)
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    pooled = create_instance(
        svc, user_id, subject_id, InstanceInput(flash, flashcard_params(6), "T.")
    ).instance_id
    sums = create_instance(
        svc, user_id, subject_id, InstanceInput(numeric, numeric_params(), "R.")
    ).instance_id
    document = web_instance_document(svc, user_id, pooled)
    assert document is not None
    assert bridge("web") in document and bridge("artifact") not in document
    assert document.count('id="tutor-params"') == 1
    assert web_instance_document(svc, user_id, pooled) == document  # seeded by the instance id
    artifact = get_instance_view(svc, user_id, pooled).document
    assert sum(f"cue {i}" in document for i in range(6)) == 1
    assert [f"cue {i}" in document for i in range(6)] == [f"cue {i}" in artifact for i in range(6)]
    for iid in (pooled, sums):
        text = web_instance_document(svc, user_id, iid)
        assert text is not None and not any(leak in text for leak in LEAKS)
    assert web_instance_document(svc, other_user_id, pooled) is None
    assert web_instance_document(svc, user_id, uuid4()) is None


def test_web_instance_document_still_serves_a_retired_instance(
    svc: Services, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    template_id = saved(svc, user_id, subject_id, CHOICE)
    first = create_instance(
        svc, user_id, subject_id, InstanceInput(template_id, choice_params(1), "Elige.")
    )
    create_instance(
        svc,
        user_id,
        subject_id,
        InstanceInput(template_id, choice_params(2), "Elige.", None, first.instance_id),
    )
    assert get_instance_view(svc, user_id, first.instance_id).status == "retired"
    assert web_instance_document(svc, user_id, first.instance_id) is not None


def test_instance_counts_count_active_instances_per_template(
    svc: Services, user_id: UUID, other_user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    choice = saved(svc, user_id, subject_id, CHOICE)
    table = saved(svc, user_id, subject_id, TABLE)
    saved(svc, user_id, subject_id, NUMERIC)  # no instance: left out
    first = create_instance(
        svc, user_id, subject_id, InstanceInput(choice, choice_params(1), "Elige.")
    )
    create_instance(
        svc,
        user_id,
        subject_id,
        InstanceInput(choice, choice_params(2), "Elige.", None, first.instance_id),
    )
    create_instance(svc, user_id, subject_id, InstanceInput(table, table_params(), "Mira."))
    assert instance_counts(svc, user_id, subject_id) == {choice: 1, table: 1}
    with pytest.raises(ServiceError) as info:
        instance_counts(svc, other_user_id, subject_id)
    assert info.value.code == "not_found"


# --- retire_template (slice D ruling 59, spec 15.13 D9, amendment A56) -------------------------


def test_review_focus_6_retire_template_once_then_already_retired(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    subject_id = create_subject(svc, user_id)
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    table = saved(svc, user_id, subject_id, TABLE)
    created = clock.now
    retired_at = clock.advance(timedelta(minutes=5))
    assert retire_template(svc, user_id, numeric) == "retired"
    assert [t.template_id for t in list_templates(svc, user_id, subject_id).templates] == [table]
    [one] = list_templates(svc, user_id, subject_id, numeric).templates
    assert one.status == "retired"
    rec = store.tables.exercise_templates[numeric]
    assert (rec.created_at, rec.retired_at, rec.updated_at) == (created, retired_at, retired_at)
    clock.advance(timedelta(minutes=5))
    assert retire_template(svc, user_id, numeric) == "already_retired"
    assert rec.retired_at == retired_at  # the second call changes nothing
    metas = events(store, user_id, "template_retired")
    assert metas == [
        {
            "template_id": str(numeric),
            "subject_id": str(subject_id),
            "retire_outcome": "retired",
        },
        {
            "template_id": str(numeric),
            "subject_id": str(subject_id),
            "retire_outcome": "already_retired",
        },
    ]
    # Ids and enums only: no name, description or html.
    dumped = json.dumps(metas)
    assert NUMERIC.name not in dumped and "html" not in dumped


def test_review_focus_6_a_retired_template_is_not_found_for_new_instances(
    svc: Services, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    retire_template(svc, user_id, numeric)
    lesson = note(instances=(InstanceInput(numeric, numeric_params(), "Suma."),))
    with pytest.raises(ServiceError) as info:
        save_notes(svc, user_id, subject_id, None, [lesson])
    assert fields_of(info) == ("not_found", ("notes[0].instances[0].template_id",))
    with pytest.raises(ServiceError) as info:
        create_instance(svc, user_id, subject_id, InstanceInput(numeric, numeric_params(), "S."))
    assert fields_of(info) == ("not_found", ("template_id",))


def test_review_focus_3_retire_template_of_another_user_or_missing(
    svc: Services, store: MemoryStore, user_id: UUID, other_user_id: UUID
) -> None:
    theirs = create_subject(svc, other_user_id)
    their_template = saved(svc, other_user_id, theirs, NUMERIC)
    for template_id in (their_template, uuid4()):
        assert retire_template(svc, user_id, template_id) is None
    assert store.tables.exercise_templates[their_template].status == "active"
    assert [t.template_id for t in list_templates(svc, other_user_id, theirs).templates] == [
        their_template
    ]
    assert events(store, user_id, "template_retired") == []
    assert events(store, other_user_id, "template_retired") == []


def test_a56_instances_of_a_retired_template_stay_active_and_drawn(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id, exercise_kinds=["quiz"])
    numeric = saved(svc, user_id, subject_id, NUMERIC)
    lesson = note(instances=(InstanceInput(numeric, numeric_params(), "Suma."),))
    [outcome] = save_notes(svc, user_id, subject_id, None, [lesson]).notes
    [instance_id] = outcome.instance_ids
    assert retire_template(svc, user_id, numeric) == "retired"
    assert store.tables.template_instances[instance_id].status == "active"
    [shown] = get_note(svc, user_id, outcome.note_id).instances
    assert shown.instance_id == instance_id
    started = start_practice(svc, user_id, subject_id, "text")
    assert started.first is not None and started.first.instance_id == instance_id
    drawn = {
        i.instance_id
        for i in store.tables.practice_items.values()
        if i.session_id == started.session_id
    }
    assert drawn == {instance_id}
