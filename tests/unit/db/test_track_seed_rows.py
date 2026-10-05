import hashlib
import json

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


# v0 track snapshot seeded by revision 0003. Before the first production deploy, update this hash
# when the author edits the track. After it, every change to track_it_v0.yaml must ship with a new
# seed revision (upsert/delete by id) and then update this hash.
TRACK_SNAPSHOT_SHA256 = "fcc01e9b02ed5cf1a953c5aecedd3aabdd42131b14e0ca51a76ef3cac21a4d6d"


def test_seeded_track_snapshot_is_pinned() -> None:
    items, chunks = track_rows(load_track())
    canonical = json.dumps(
        {"items": items, "chunks": chunks},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == TRACK_SNAPSHOT_SHA256
