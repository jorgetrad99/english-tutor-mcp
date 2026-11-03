"""Postgres-only behaviour of `NoteRepo.search` (subjects spec 5.1): stemming, accents, English
terms inside Spanish notes, websearch syntax, the trigram fallback, ts_headline excerpts,
stable order and user isolation. The shared ordering fixture lives in tests/repo_contract.py."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from repo_contract import add_note, long_paragraph, sample_subject, valid_note

from tutor.domain.notes import ConceptLinkInput
from tutor.services.ports import SearchHit, UowFactory

pytestmark = pytest.mark.integration

SEVENTH = "El acorde de séptima dominante se toca en el quinto traste con la cejilla."
SEVENTH_BLUES = "Los acordes de séptima aparecen en todo el blues."
POWER = "Un power chord usa la fundamental y la quinta; con distorsión suena lleno."
PENTATONIC = "La escala pentatónica menor sirve para improvisar sobre el blues."
TUNING = "Afinar la guitarra con un afinador electrónico antes de tocar."


@pytest.fixture
def notes(uow_factory: UowFactory, user_id: UUID, now: datetime) -> Iterator[dict[str, UUID]]:
    """Four lessons of one subject, one minute apart, in the order they were taught."""
    ids: dict[str, UUID] = {}
    with uow_factory(user_id) as uow:
        subject_id = uow.subjects.create(sample_subject("Guitar"), now).id
        ids["subject"] = subject_id
        lessons = (
            ("seventh", "Acordes de séptima", (SEVENTH, SEVENTH_BLUES), ()),
            ("power", "Power chords", (POWER,), ()),
            ("pentatonic", "Escala pentatónica", (PENTATONIC,), ("Pentatónica menor",)),
            ("tuning", "Afinación", (TUNING,), ()),
        )
        for minute, (key, title, leads, concepts) in enumerate(lessons, start=1):
            note = valid_note(title, *(long_paragraph(lead) for lead in leads), concepts=concepts)
            ids[key] = add_note(uow, subject_id, note, now + timedelta(minutes=minute)).id
    yield ids


def _ids(hits: tuple[SearchHit, ...]) -> list[UUID]:
    return [h.note_id for h in hits]


def test_review_focus_3_spanish_queries_with_and_without_accents(
    uow_factory: UowFactory, user_id: UUID, notes: dict[str, UUID]
) -> None:
    # Review Focus 3
    with uow_factory(user_id) as uow:
        accented = uow.notes.search("acorde de séptima en el quinto traste", None, 10)
        plain = uow.notes.search("acorde de septima en el quinto traste", None, 10)
        shouted = uow.notes.search("ACORDES DE SÉPTIMA", None, 10)
        power = uow.notes.search("power chord", None, 10)
    assert _ids(accented) == [notes["seventh"]]
    assert _ids(plain) == [notes["seventh"]]
    # Plural and upper case stem to the same lexeme; both passages of the lesson match.
    assert [(h.note_id, h.passages) for h in shouted] == [(notes["seventh"], 2)]
    assert _ids(power) == [notes["power"]]
    assert all(not h.fuzzy and h.concepts == () for h in (*accented, *plain, *shouted, *power))


def test_english_terms_match_through_the_english_configuration(
    uow_factory: UowFactory, user_id: UUID, notes: dict[str, UUID]
) -> None:
    with uow_factory(user_id) as uow:
        [hit] = uow.notes.search("power chords", None, 10)  # Spanish stemming keeps "chords"
    assert (hit.note_id, hit.passages) == (notes["power"], 1)
    # The excerpt marks the match even when only the English headline finds it.
    assert len(hit.excerpts) == 1
    assert "[[power]]" in hit.excerpts[0] and "[[chord]]" in hit.excerpts[0]


def test_websearch_syntax_phrases_and_negation(
    uow_factory: UowFactory, user_id: UUID, notes: dict[str, UUID]
) -> None:
    with uow_factory(user_id) as uow:
        phrase = uow.notes.search('"quinto traste"', None, 10)
        reversed_phrase = uow.notes.search('"traste quinto"', None, 10)
        negated = uow.notes.search("blues -pentatónica", None, 10)
        either = uow.notes.search("afinador OR distorsión", None, 10)
    assert _ids(phrase) == [notes["seventh"]]
    assert reversed_phrase == ()
    # Negation applies to passages; the concept graph matches the normalised query by name
    # similarity (spec 5.1 step 2), so the pentatonic lesson still arrives through its concept.
    assert [(h.note_id, h.passages, h.concepts) for h in negated] == [
        (notes["pentatonic"], 0, ("Pentatónica menor",)),
        (notes["seventh"], 1, ()),
    ]
    assert _ids(either) == [notes["tuning"], notes["power"]]  # one passage each, newest first


def test_review_focus_3_misspelled_concept_is_found_by_trigram_and_marked_fuzzy(
    uow_factory: UowFactory, user_id: UUID, notes: dict[str, UUID]
) -> None:
    # Review Focus 3
    with uow_factory(user_id) as uow:
        [hit] = uow.notes.search("pentatonca", None, 10)
        [exact] = uow.notes.search("pentatónica menor", None, 10)
        [stemmed] = uow.notes.search("pentatonica", None, 10)
    assert hit == SearchHit(
        note_id=notes["pentatonic"],
        passages=0,
        concepts=("Pentatónica menor",),
        depth=0,
        fuzzy=True,
        excerpts=(),
    )
    # The exact name is not fuzzy; a passage match beside a similar concept is not either.
    assert (exact.note_id, exact.passages, exact.concepts, exact.depth, exact.fuzzy) == (
        notes["pentatonic"],
        1,
        ("Pentatónica menor",),
        0,
        False,
    )
    assert (stemmed.passages, stemmed.concepts, stemmed.depth, stemmed.fuzzy) == (
        1,
        ("Pentatónica menor",),
        0,
        False,
    )


def test_title_fallback_for_short_queries_only(
    uow_factory: UowFactory, user_id: UUID, notes: dict[str, UUID]
) -> None:
    with uow_factory(user_id) as uow:
        [hit] = uow.notes.search("afinacin", None, 10)  # a typo: no passage, no concept
        long_query = uow.notes.search("afinacin de la guitarra", None, 10)
        nothing = uow.notes.search("ukelele", None, 10)
        empty = uow.notes.search("¿?", None, 10)
        no_room = uow.notes.search("blues", None, 0)
    assert hit == SearchHit(
        note_id=notes["tuning"], passages=0, concepts=(), depth=None, fuzzy=True, excerpts=()
    )
    assert long_query == ()  # four tokens: no fallback
    assert nothing == ()
    assert empty == ()
    assert no_room == ()


def test_headline_excerpts_come_from_the_matched_passages(
    uow_factory: UowFactory, user_id: UUID, notes: dict[str, UUID]
) -> None:
    with uow_factory(user_id) as uow:
        [hit] = uow.notes.search("séptima", None, 10)
    assert (hit.note_id, hit.passages) == (notes["seventh"], 2)
    assert len(hit.excerpts) == 2
    for excerpt in hit.excerpts:
        assert "[[séptima]]" in excerpt
        assert len(excerpt.split()) <= 25 + 2  # MaxWords=25 per fragment, markers inline
        assert "<" not in excerpt  # plain text


def test_concept_graph_hits_and_limit(
    uow_factory: UowFactory, user_id: UUID, notes: dict[str, UUID], now: datetime
) -> None:
    with uow_factory(user_id) as uow:
        blues = add_note(
            uow,
            notes["subject"],
            valid_note(
                "Blues",
                long_paragraph("doce compases."),
                concepts=["Blues"],
                links=[ConceptLinkInput("Pentatónica menor", "Blues", "prerequisite")],
            ),
            now + timedelta(minutes=9),
        ).id
    with uow_factory(user_id) as uow:
        hits = uow.notes.search("pentatónica menor", None, 10)
        top = uow.notes.search("pentatónica menor", None, 1)
    assert [(h.note_id, h.passages, h.concepts, h.depth, h.fuzzy) for h in hits] == [
        (notes["pentatonic"], 1, ("Pentatónica menor",), 0, False),
        (blues, 0, ("Blues",), 1, False),
    ]
    assert _ids(top) == [notes["pentatonic"]]


def test_order_is_the_same_on_every_run(
    uow_factory: UowFactory, user_id: UUID, notes: dict[str, UUID]
) -> None:
    with uow_factory(user_id) as uow:
        runs = [uow.notes.search("blues", None, 10) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert _ids(runs[0]) == [
        notes["pentatonic"],
        notes["seventh"],
    ]  # one passage each, newest first


def test_two_users_never_see_each_others_notes(
    uow_factory: UowFactory,
    user_id: UUID,
    other_user_id: UUID,
    notes: dict[str, UUID],
    now: datetime,
) -> None:
    with uow_factory(other_user_id) as uow:
        subject_id = uow.subjects.create(sample_subject("Guitar"), now).id
        theirs = add_note(
            uow,
            subject_id,
            valid_note("Afinación", long_paragraph("power chord."), concepts=["Pentatónica menor"]),
            now,
        ).id
    with uow_factory(other_user_id) as uow:
        assert _ids(uow.notes.search("power chord", None, 10)) == [theirs]
        assert _ids(uow.notes.search("pentatonca", None, 10)) == [theirs]  # their concept only
        assert _ids(uow.notes.search("afinacin", None, 10)) == [theirs]  # their title only
        assert uow.notes.search("séptima", None, 10) == ()
        assert uow.notes.search("power chord", notes["subject"], 10) == ()
    with uow_factory(user_id) as uow:
        assert _ids(uow.notes.search("power chord", None, 10)) == [notes["power"]]
        assert _ids(uow.notes.search("pentatonca", None, 10)) == [notes["pentatonic"]]
        assert _ids(uow.notes.search("afinacin", None, 10)) == [notes["tuning"]]
