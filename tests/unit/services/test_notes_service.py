"""save_notes, save_artifact, get_artifact, get_note and search_notes on the in-memory store
(subjects spec 4, 5; plan rulings 9, 11, 12)."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from repo_contract import long_paragraph

from tutor.domain.html_guard import MAX_ARTIFACT_BYTES
from tutor.domain.notes import (
    MAX_CALL_CHARS,
    MAX_CONCEPTS,
    MAX_NOTES_PER_CALL,
    MAX_QUIZ_ITEMS,
    OPTION_MAX,
    ConceptLinkInput,
    ExerciseInput,
    NoteInput,
    QuizItemInput,
    SourceInput,
)
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.memory import MemoryStore
from tutor.services.notes import (
    MAX_NOTE_TEXT_CHARS,
    get_artifact,
    get_note,
    save_artifact,
    save_notes,
    search_notes,
)
from tutor.services.subject_views import (
    ArtifactRef,
    ArtifactSaved,
    ArtifactView,
    ExerciseView,
    NoteOutcome,
    QuizItemView,
    SaveNotesResult,
    SearchWhy,
)

from .conftest import NOW, FixedClock, create_subject

pytestmark = pytest.mark.unit

GOOD_HTML = (
    "<!doctype html>\n<html><head><style>p { color: red }</style></head>"
    '<body><h1>Clase 3</h1><p>Canción</p><a href="#top">top</a>'
    "<script>document.title = 'ok';</script></body></html>"
)
BAD_HTML = '<!doctype html><html><body><iframe src="https://example.test"></iframe></body></html>'
SOURCE = SourceInput("course", "Curso de guitarra", "https://example.test/curso")
RAW_TEXT = "El acorde de séptima tiene cuatro notas.\n\nSe toca en el quinto traste."


def note(**changes: Any) -> NoteInput:
    data: dict[str, Any] = {
        "title": "Clase 3: el acorde de séptima",
        "lesson_ref": "Módulo 2 / Clase 3",
        "position": 3,
        "raw_text": RAW_TEXT,
        "concepts": ("Acorde de séptima", "Quinto traste"),
        "links": (ConceptLinkInput("Quinto traste", "Acorde de séptima", "prerequisite"),),
        "exercises": (
            ExerciseInput("Cambio C7-G7", "Cambia entre C7 y G7 cada compás.", "technique", 60, 5),
        ),
        "quiz_items": (
            QuizItemInput(
                "¿Cuántas notas tiene un acorde de séptima?",
                ("Tres", "Cuatro", "Cinco"),
                1,
                "La séptima se suma a la tríada.",
                "Acorde de séptima",
            ),
        ),
    }
    data.update(changes)
    return NoteInput(**data)


def events(store: MemoryStore, user_id: UUID, event: str) -> list[dict[str, Any]]:
    return [a.meta for a in store.tables.audit if a.user_id == user_id and a.event == event]


def add_lesson(svc: Services, user_id: UUID, **changes: Any) -> UUID:
    """A subject with one note; the note id."""
    subject_id = create_subject(svc, user_id)
    return save_notes(svc, user_id, subject_id, SOURCE, [note(**changes)]).notes[0].note_id


# --- save_notes and get_note -----------------------------------------------------------------


def test_save_notes_stores_new_notes_with_their_source(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    second = note(title="Clase 4", lesson_ref="Módulo 2 / Clase 4", raw_text="Otra lección.")
    result = save_notes(svc, user_id, subject_id, SOURCE, [note(), second])
    assert result.source_id is not None
    assert [o.outcome for o in result.notes] == ["new", "new"]
    assert len({o.note_id for o in result.notes}) == 2
    assert events(store, user_id, "notes_saved") == [
        {
            "subject_id": str(subject_id),
            "source_id": str(result.source_id),
            "origin": "mcp",
            "new": 2,
            "existing": 0,
            "superseded": 0,
        }
    ]
    view = get_note(svc, user_id, result.notes[0].note_id)
    assert (
        view.note_id,
        view.subject_id,
        view.source_id,
        view.source_title,
        view.title,
        view.lesson_ref,
        view.position,
        view.origin,
    ) == (
        result.notes[0].note_id,
        subject_id,
        result.source_id,
        "Curso de guitarra",
        "Clase 3: el acorde de séptima",
        "Módulo 2 / Clase 3",
        3,
        "mcp",
    )
    assert (view.clean_text, view.truncated, view.passage_count, view.passages) == (
        RAW_TEXT,
        False,
        1,
        (),
    )
    [exercise] = view.exercises
    assert exercise == ExerciseView(
        exercise_id=exercise.exercise_id,
        name="Cambio C7-G7",
        instructions="Cambia entre C7 y G7 cada compás.",
        kind="technique",
        tempo_bpm=60,
        duration_min=5,
    )
    assert view.quiz_items == (
        QuizItemView(
            question="¿Cuántas notas tiene un acorde de séptima?",
            options=("Tres", "Cuatro", "Cinco"),
            explanation="La séptima se suma a la tríada.",
            concept="Acorde de séptima",
        ),
    )
    assert view.concepts == ("Acorde de séptima", "Quinto traste")
    assert (view.artifacts, view.supersedes_note_id, view.created_at, view.updated_at) == (
        (),
        None,
        NOW,
        NOW,
    )


def test_quiz_items_never_carry_the_answer_index() -> None:
    assert "answer_index" not in {f.name for f in dataclasses.fields(QuizItemView)}


def test_review_focus_1_repeat_is_existing_and_a_fixed_typo_supersedes(
    svc: Services, store: MemoryStore, user_id: UUID, clock: FixedClock
) -> None:
    # Review Focus 1
    subject_id = create_subject(svc, user_id)
    first = save_notes(
        svc, user_id, subject_id, SOURCE, [note(raw_text="El acorde tiene un tipo.")]
    )
    clock.advance(timedelta(minutes=1))
    again = save_notes(
        svc, user_id, subject_id, SOURCE, [note(raw_text="El acorde tiene un tipo.")]
    )
    clock.advance(timedelta(minutes=1))
    fixed = save_notes(
        svc, user_id, subject_id, SOURCE, [note(raw_text="El acorde tiene un typo.")]
    )
    original = first.notes[0].note_id
    assert again == SaveNotesResult(
        source_id=first.source_id, notes=(NoteOutcome(original, "existing"),)
    )
    assert fixed.source_id == first.source_id
    [outcome] = fixed.notes
    assert outcome.outcome == "superseded"
    assert outcome.note_id != original
    assert get_note(svc, user_id, outcome.note_id).supersedes_note_id == original
    old = get_note(svc, user_id, original)
    assert (old.clean_text, old.created_at, old.updated_at) == (
        "El acorde tiene un tipo.",
        NOW,
        NOW,
    )
    assert [h.note_id for h in search_notes(svc, user_id, "acorde").hits] == [
        outcome.note_id,
        original,
    ]
    saved = events(store, user_id, "notes_saved")
    assert [(m["new"], m["existing"], m["superseded"]) for m in saved] == [
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
    ]


def test_outcomes_within_one_call(svc: Services, user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id)
    result = save_notes(
        svc,
        user_id,
        subject_id,
        None,
        [note(raw_text="uno"), note(raw_text="uno"), note(raw_text="dos")],
    )
    assert result.source_id is None
    assert [o.outcome for o in result.notes] == ["new", "existing", "superseded"]
    assert result.notes[1].note_id == result.notes[0].note_id
    newest = get_note(svc, user_id, result.notes[2].note_id)
    assert (newest.supersedes_note_id, newest.source_id, newest.source_title) == (
        result.notes[0].note_id,
        None,
        None,
    )


def test_a_note_without_lesson_ref_is_never_superseded(svc: Services, user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id)
    one = save_notes(svc, user_id, subject_id, SOURCE, [note(lesson_ref=None, raw_text="uno")])
    two = save_notes(svc, user_id, subject_id, SOURCE, [note(lesson_ref=None, raw_text="dos")])
    assert (one.notes[0].outcome, two.notes[0].outcome) == ("new", "new")
    assert get_note(svc, user_id, two.notes[0].note_id).supersedes_note_id is None
    # The same lesson under another source is a note of its own.
    other = save_notes(
        svc, user_id, subject_id, SourceInput("pdf", "Apuntes"), [note(raw_text="uno")]
    )
    assert other.notes[0].outcome == "new"


def test_review_focus_6_caps_are_all_or_nothing(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    # Review Focus 6
    subject_id = create_subject(svc, user_id)
    quarter = MAX_CALL_CHARS // 4 + 1  # four of these: 16,004 characters
    cases: list[tuple[list[NoteInput], tuple[str, tuple[str, ...]]]] = [
        (
            [note(raw_text=f"nota {i}") for i in range(MAX_NOTES_PER_CALL + 1)],
            ("limit_exceeded", ("notes",)),
        ),
        (
            [note(raw_text="x" * quarter, lesson_ref=str(i)) for i in range(4)],
            ("limit_exceeded", ("notes",)),
        ),
        (
            [note(), note(concepts=tuple(f"c{i}" for i in range(MAX_CONCEPTS + 1)))],
            ("limit_exceeded", ("notes[1].concepts",)),
        ),
        (
            [
                note(
                    quiz_items=tuple(
                        QuizItemInput(f"q{i}", ("a", "b"), 0) for i in range(MAX_QUIZ_ITEMS + 1)
                    )
                )
            ],
            ("limit_exceeded", ("notes[0].quiz_items",)),
        ),
        (
            [note(), note(quiz_items=(QuizItemInput("q", ("o" * (OPTION_MAX + 1),) * 3, 0),))],
            ("validation_failed", ("notes[1].quiz_items[0].options",)),
        ),
    ]
    for notes, expected in cases:
        with pytest.raises(ServiceError) as info:
            save_notes(svc, user_id, subject_id, SOURCE, notes)
        assert (info.value.code, info.value.fields) == expected
    # The valid notes of those calls were not stored either: one transaction per call.
    assert search_notes(svc, user_id, "séptima").hits == ()
    assert store.tables.notes == {}
    assert store.tables.sources == {}
    assert events(store, user_id, "notes_saved") == []


def test_review_focus_5_a_foreign_subject_is_not_found_and_writes_nothing(
    svc: Services, store: MemoryStore, user_id: UUID, other_user_id: UUID
) -> None:
    # Review Focus 5
    theirs = create_subject(svc, other_user_id)
    for subject_id in (theirs, uuid4()):
        with pytest.raises(ServiceError) as info:
            save_notes(svc, user_id, subject_id, SOURCE, [note()])
        assert (info.value.code, info.value.fields) == ("not_found", ("subject_id",))
    assert store.tables.notes == {}
    assert store.tables.sources == {}
    assert search_notes(svc, other_user_id, "séptima").hits == ()
    with pytest.raises(ServiceError) as info:
        search_notes(svc, user_id, "séptima", subject_id=theirs)
    assert (info.value.code, info.value.fields) == ("not_found", ("subject_id",))


def test_get_note_truncates_long_text_and_reads_passage_windows(
    svc: Services, user_id: UUID
) -> None:
    subject_id = create_subject(svc, user_id)
    paragraphs = [f"{n:03d}" + "p" * 996 + "." for n in range(1, 16)]  # 15 x 1,000 characters
    text = "\n\n".join(paragraphs)
    assert len(text) > MAX_NOTE_TEXT_CHARS
    saved = save_notes(svc, user_id, subject_id, None, [note(raw_text=text)], origin="web")
    note_id = saved.notes[0].note_id
    view = get_note(svc, user_id, note_id)
    assert (view.truncated, view.passage_count, view.passages, view.origin) == (True, 15, (), "web")
    assert view.clean_text == text[:MAX_NOTE_TEXT_CHARS]
    window = get_note(svc, user_id, note_id, passage_from=11, passage_to=15)
    assert [(p.seq, p.text) for p in window.passages] == [
        (n, paragraphs[n - 1]) for n in range(11, 16)
    ]
    assert window.truncated and window.clean_text == view.clean_text
    assert [p.seq for p in get_note(svc, user_id, note_id, passage_from=14).passages] == [14, 15]
    assert [p.seq for p in get_note(svc, user_id, note_id, passage_to=3).passages] == [1, 2, 3]
    ten = get_note(svc, user_id, note_id, passage_from=6, passage_to=15).passages
    assert [p.seq for p in ten] == list(range(6, 16))
    bad: list[tuple[dict[str, int], str]] = [
        ({"passage_from": 0}, "passage_from"),
        ({"passage_from": 16}, "passage_from"),
        ({"passage_from": 1, "passage_to": 11}, "passage_to"),
        ({"passage_from": 5, "passage_to": 4}, "passage_to"),
        ({"passage_to": 0}, "passage_to"),
    ]
    for kwargs, field in bad:
        with pytest.raises(ServiceError) as info:
            get_note(svc, user_id, note_id, **kwargs)
        assert (info.value.code, info.value.fields) == ("validation_failed", (field,))


# --- artifacts --------------------------------------------------------------------------------


def test_save_artifact_stores_the_document_and_returns_its_path(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    note_id = add_lesson(svc, user_id)
    saved = save_artifact(svc, user_id, note_id, "lesson_view", "  Vista   de la clase ", GOOD_HTML)
    assert saved == ArtifactSaved(
        artifact_id=saved.artifact_id,
        web_path=f"/app/notas/{note_id}/artefactos/{saved.artifact_id}/raw",
    )
    assert get_artifact(svc, user_id, saved.artifact_id) == ArtifactView(
        artifact_id=saved.artifact_id,
        note_id=note_id,
        kind="lesson_view",
        title="Vista de la clase",
        html=GOOD_HTML,
    )
    assert get_note(svc, user_id, note_id).artifacts == (
        ArtifactRef(saved.artifact_id, "lesson_view", "Vista de la clase"),
    )
    assert events(store, user_id, "artifact_saved") == [
        {
            "artifact_id": str(saved.artifact_id),
            "note_id": str(note_id),
            "artifact_kind": "lesson_view",
            "bytes": len(GOOD_HTML.encode("utf-8")),
        }
    ]
    assert events(store, user_id, "artifact_rejected") == []


def test_review_focus_6_rejected_artifacts_store_nothing_and_audit_the_first_problem(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    # Review Focus 6
    note_id = add_lesson(svc, user_id)
    frame = "<!doctype html><html><body></body></html>"
    too_big = frame.replace("<body>", "<body>" + "a" * (MAX_ARTIFACT_BYTES - len(frame) + 1))
    assert len(too_big.encode("utf-8")) == MAX_ARTIFACT_BYTES + 1
    for html in (BAD_HTML, too_big):
        with pytest.raises(ServiceError) as info:
            save_artifact(svc, user_id, note_id, "other", "Mala", html)
        assert (info.value.code, info.value.fields) == ("validation_failed", ("html",))
    assert store.tables.note_artifacts == {}
    assert get_note(svc, user_id, note_id).artifacts == ()
    assert events(store, user_id, "artifact_rejected") == [
        {"note_id": str(note_id), "problem": "tag_forbidden"},
        {"note_id": str(note_id), "problem": "too_large"},
    ]
    assert events(store, user_id, "artifact_saved") == []


def test_save_artifact_validates_kind_and_title_with_the_html(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    note_id = add_lesson(svc, user_id)
    with pytest.raises(ServiceError) as info:
        save_artifact(svc, user_id, note_id, "video", "", BAD_HTML)
    assert (info.value.code, info.value.fields) == (
        "validation_failed",
        ("artifact_kind", "title", "html"),
    )
    with pytest.raises(ServiceError) as info:
        save_artifact(svc, user_id, note_id, "other", "t" * 201, GOOD_HTML)
    assert (info.value.code, info.value.fields) == ("validation_failed", ("title",))
    assert store.tables.note_artifacts == {}
    assert len(events(store, user_id, "artifact_rejected")) == 1  # only the call with bad html


def test_review_focus_5_notes_and_artifacts_of_another_user_are_not_found(
    svc: Services, store: MemoryStore, user_id: UUID, other_user_id: UUID
) -> None:
    # Review Focus 5
    their_note = add_lesson(svc, other_user_id)
    their_artifact = save_artifact(svc, other_user_id, their_note, "other", "Suya", GOOD_HTML)
    for html in (GOOD_HTML, BAD_HTML):
        with pytest.raises(ServiceError) as info:
            save_artifact(svc, user_id, their_note, "other", "Mía", html)
        assert (info.value.code, info.value.fields) == ("not_found", ("note_id",))
    for artifact_id in (their_artifact.artifact_id, uuid4()):
        with pytest.raises(ServiceError) as info:
            get_artifact(svc, user_id, artifact_id)
        assert (info.value.code, info.value.fields) == ("not_found", ("artifact_id",))
    for note_id in (their_note, uuid4()):
        with pytest.raises(ServiceError) as info:
            get_note(svc, user_id, note_id)
        assert (info.value.code, info.value.fields) == ("not_found", ("note_id",))
    assert len(store.tables.note_artifacts) == 1
    assert events(store, user_id, "artifact_rejected") == []
    assert events(store, user_id, "artifact_saved") == []
    assert get_artifact(svc, other_user_id, their_artifact.artifact_id).html == GOOD_HTML


# --- search -----------------------------------------------------------------------------------


def test_search_notes_returns_excerpts_and_why(svc: Services, user_id: UUID) -> None:
    subject_id = create_subject(svc, user_id)
    first_passage = long_paragraph("el acorde de séptima suena bien.")
    lesson = note(raw_text=first_passage + "\n\n" + long_paragraph("otra séptima aquí."))
    other = note(
        title="Clase 4",
        lesson_ref="Módulo 2 / Clase 4",
        raw_text="Afinación básica.",
        concepts=("Afinación",),
    )
    saved = save_notes(svc, user_id, subject_id, SOURCE, [lesson, other])
    result = search_notes(svc, user_id, "séptima", subject_id=subject_id, limit=5)
    [hit] = result.hits
    assert (hit.note_id, hit.subject_id, hit.title, hit.lesson_ref, hit.source_title) == (
        saved.notes[0].note_id,
        subject_id,
        "Clase 3: el acorde de séptima",
        "Módulo 2 / Clase 3",
        "Curso de guitarra",
    )
    assert hit.why == SearchWhy(passages=2, concepts=(), depth=None, fuzzy=False)
    words = first_passage.split()
    words[3] = "[[séptima]]"
    assert hit.excerpts == (" ".join(words[:25]),)
    # A query that is also a concept name of the subject adds the concept hit at depth 0.
    [by_concept] = search_notes(svc, user_id, "acorde de séptima").hits
    assert by_concept.why == SearchWhy(
        passages=1, concepts=("Acorde de séptima",), depth=0, fuzzy=False
    )
    assert search_notes(svc, user_id, "ukelele").hits == ()


def test_search_notes_validates_query_and_limit(svc: Services, user_id: UUID) -> None:
    bad: list[tuple[dict[str, Any], str]] = [
        ({"query": "   "}, "query"),
        ({"query": "q" * 201}, "query"),
        ({"query": "x", "limit": 0}, "limit"),
        ({"query": "x", "limit": 11}, "limit"),
    ]
    for kwargs, field in bad:
        with pytest.raises(ServiceError) as info:
            search_notes(svc, user_id, **kwargs)
        assert (info.value.code, info.value.fields) == ("validation_failed", (field,))
    assert search_notes(svc, user_id, " nada ", limit=10).hits == ()
