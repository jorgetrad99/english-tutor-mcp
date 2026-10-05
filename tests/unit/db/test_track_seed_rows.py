import pytest

from tutor.content import load_track
from tutor.db.seed import track_rows
from tutor.db.tables import track_chunks, track_items

pytestmark = pytest.mark.unit


def test_track_rows_cover_every_item_and_chunk() -> None:
    track = load_track()
    items, chunks = track_rows(track)
    assert (len(items), len(chunks)) == (24, 120)
    assert [r["id"] for r in items] == [i.id for i in track]
    assert {r["id"] for r in chunks} == {c.id for i in track for c in i.chunks}


def test_track_rows_match_the_table_columns_and_values() -> None:
    track = load_track()
    items, chunks = track_rows(track)
    first = track[0]
    assert set(items[0]) == {c.name for c in track_items.columns}
    assert set(chunks[0]) == {c.name for c in track_chunks.columns}
    assert items[0] == {
        "id": first.id,
        "domain": first.domain,
        "order_no": first.order_no,
        "cefr": first.cefr,
        "can_do_en": first.can_do_en,
        "can_do_es": first.can_do_es,
        "skill": first.skill,
        "interaction_type": first.interaction_type,
        "use_cases": list(first.use_cases),
        "character": first.character,
        "objective": first.objective,
        "obstacle": first.obstacle,
        "scenario_hint": first.scenario_hint,
    }
    chunk = first.chunks[0]
    assert chunks[0] == {
        "id": chunk.id,
        "track_item_id": first.id,
        "position": chunk.position,
        "text": chunk.text,
        "example": chunk.example,
    }
