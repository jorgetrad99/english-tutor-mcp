import dataclasses
from typing import Any

import pytest

from tutor.content import load_track
from tutor.domain.track import TrackChunk, TrackError, TrackItem, parse_track, track_problems

pytestmark = pytest.mark.unit

RSQ = "\N{RIGHT SINGLE QUOTATION MARK}"


def raw_item(item_id: str = "it-01", order_no: int = 1) -> dict[str, Any]:
    return {
        "id": item_id,
        "order_no": order_no,
        "cefr": "B1",
        "skill": "speaking",
        "interaction_type": "explain",
        "use_cases": ["standup"],
        "can_do_en": "Can give a standup update.",
        "can_do_es": "Puedo dar una actualización en el standup.",
        "character": "Sam, the team lead",
        "objective": "Give your update.",
        "obstacle": "Sam asks for a date.",
        "scenario_hint": "Daily standup.",
        "chunks": [
            {
                "id": f"{item_id}-c{pos}",
                "position": pos,
                "text": f"chunk number {pos}",
                "example": f"This is chunk number {pos}.",
            }
            for pos in range(1, 6)
        ],
    }


def raw_track(*items: dict[str, Any]) -> dict[str, Any]:
    return {"domain": "it", "version": 1, "items": list(items) or [raw_item()]}


def problems_of(data: dict[str, Any]) -> tuple[str, ...]:
    with pytest.raises(TrackError) as caught:
        parse_track(data)
    return caught.value.problems


def test_parse_minimal_track() -> None:
    (item,) = parse_track(raw_track())
    assert item.id == "it-01"
    assert item.domain == "it"
    assert (item.order_no, item.cefr, item.skill) == (1, "B1", "speaking")
    assert item.interaction_type == "explain"
    assert item.use_cases == ("standup",)
    assert item.chunks[0] == TrackChunk("it-01-c1", 1, "chunk number 1", "This is chunk number 1.")
    assert len(item.chunks) == 5


def test_track_error_carries_every_problem() -> None:
    error = TrackError(["a: bad", "b: bad"])
    assert error.problems == ("a: bad", "b: bad")
    assert str(error) == "a: bad; b: bad"
    assert isinstance(error, ValueError)


def test_parse_rejects_bad_top_level() -> None:
    assert problems_of({"domain": "law", "items": [], "extra": 1}) == (
        "track.extra: unknown field",
        "track.domain: invalid value 'law'",
        "track.items: must be a non-empty list",
    )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda i: i.update(color="red"), "items[0].color: unknown field"),
        (lambda i: i.pop("objective"), "items[0].objective: missing"),
        (lambda i: i.update(objective="  "), "items[0].objective: must be a non-empty string"),
        (lambda i: i.update(id=7), "items[0].id: must be a non-empty string"),
        (lambda i: i.update(order_no="1"), "items[0].order_no: must be an integer"),
        (lambda i: i.update(order_no=True), "items[0].order_no: must be an integer"),
        (lambda i: i.update(cefr="C1"), "items[0].cefr: invalid value 'C1'"),
        (lambda i: i.pop("cefr"), "items[0].cefr: missing"),
        (lambda i: i.update(skill="reading"), "items[0].skill: invalid value 'reading'"),
        (
            lambda i: i.update(interaction_type="joke"),
            "items[0].interaction_type: invalid value 'joke'",
        ),
        (lambda i: i.update(use_cases=[]), "items[0].use_cases: must be a non-empty list"),
        (lambda i: i.update(use_cases="standup"), "items[0].use_cases: must be a non-empty list"),
        (lambda i: i.update(use_cases=["karaoke"]), "items[0].use_cases: invalid value 'karaoke'"),
        (lambda i: i.update(chunks="none"), "items[0].chunks: must be a list"),
        (lambda i: i["chunks"].__setitem__(0, "text"), "items[0].chunks[0]: must be a mapping"),
        (lambda i: i["chunks"][1].pop("example"), "items[0].chunks[1].example: missing"),
        (
            lambda i: i["chunks"][2].update(note="x"),
            "items[0].chunks[2].note: unknown field",
        ),
        (
            lambda i: i["chunks"][3].update(position=4.0),
            "items[0].chunks[3].position: must be an integer",
        ),
    ],
)
def test_parse_reports_item_problems(mutate: Any, expected: str) -> None:
    item = raw_item()
    mutate(item)
    assert expected in problems_of(raw_track(item))


def test_parse_rejects_non_mapping_item() -> None:
    assert problems_of(raw_track(raw_item(), "oops")) == ("items[1]: must be a mapping",)  # type: ignore[arg-type]


# --- coverage rules (spec 7.1) on the real track ---------------------------------------


def track() -> list[TrackItem]:
    return list(load_track())


def find(items: list[TrackItem], item_id: str) -> int:
    return next(i for i, item in enumerate(items) if item.id == item_id)


def replace(items: list[TrackItem], item_id: str, **changes: Any) -> list[TrackItem]:
    index = find(items, item_id)
    items[index] = dataclasses.replace(items[index], **changes)
    return items


def replace_chunk(
    items: list[TrackItem], item_id: str, pos: int, **changes: Any
) -> list[TrackItem]:
    item = items[find(items, item_id)]
    chunks = list(item.chunks)
    chunks[pos - 1] = dataclasses.replace(chunks[pos - 1], **changes)
    return replace(items, item_id, chunks=tuple(chunks))


def test_real_track_has_no_problems() -> None:
    assert track_problems(load_track()) == ()


def test_duplicate_item_id() -> None:
    items = replace(track(), "it-24", id="it-01")
    assert "it-01: duplicate item id" in track_problems(items)


def test_order_no_must_be_contiguous() -> None:
    items = replace(track(), "it-24", order_no=30)
    assert "order_no must be contiguous from 1 to 24" in track_problems(items)


def test_each_use_case_needs_three_items() -> None:
    items = replace(track(), "it-09", use_cases=("standup",))
    assert "use case interview: 2 items, needs 3" in track_problems(items)


def test_each_interaction_type_needs_three_items() -> None:
    items = replace(track(), "it-01", interaction_type="negotiate")
    items = replace(items, "it-07", interaction_type="negotiate")
    assert "interaction type explain: 2 items, needs 3" in track_problems(items)


def test_at_least_four_writing_items() -> None:
    items = replace(track(), "it-04", skill="speaking")
    assert "writing items: 3, needs 4" in track_problems(items)


def test_five_chunks_with_positions_one_to_five() -> None:
    items = track()
    first = items[0]
    items[0] = dataclasses.replace(first, chunks=first.chunks[:4])
    assert "it-01: chunk positions must be 1..5 in order" in track_problems(items)


def test_chunk_ids_follow_item_and_position() -> None:
    items = replace_chunk(track(), "it-01", 1, id="it-01-c9")
    assert "it-01-c9: id must be it-01-c1" in track_problems(items)


def test_chunk_text_must_appear_in_its_example() -> None:
    items = replace_chunk(track(), "it-01", 1, example="I fixed the login bug.")
    assert "it-01-c1: chunk text does not appear in its example" in track_problems(items)


def test_chunk_text_matches_example_through_normalization() -> None:
    items = replace_chunk(
        track(), "it-01", 3, text="I'm blocked on", example=f"Honestly, I{RSQ}m BLOCKED on it."
    )
    assert track_problems(items) == ()


def test_chunks_must_be_multi_word() -> None:
    items = replace_chunk(track(), "it-01", 1, text="Yesterday")
    assert "it-01-c1: chunk text must have at least two words" in track_problems(items)


def test_chunk_texts_must_be_unique() -> None:
    items = replace_chunk(
        track(), "it-02", 1, text="Yesterday I worked on", example="Yesterday I worked on it."
    )
    assert "chunk text 'yesterday i worked on' appears 2 times" in track_problems(items)


def test_chunk_ids_must_be_unique() -> None:
    items = replace(track(), "it-02", chunks=load_track()[0].chunks)
    problems = track_problems(items)
    assert "it-01-c1: duplicate chunk id" in problems


def test_no_two_consecutive_items_share_an_interaction_type() -> None:
    items = replace(track(), "it-02", interaction_type="explain")
    expected = "it-01 and it-02: consecutive items share interaction type explain"
    assert expected in track_problems(items)
