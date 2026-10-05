"""Rows for the starter-track seed migration (spec 7.1)."""

from collections.abc import Sequence
from typing import Any

from tutor.domain.track import TrackItem


def track_rows(items: Sequence[TrackItem]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(track_items rows, track_chunks rows), keyed by column name."""
    item_rows = [
        {
            "id": item.id,
            "domain": item.domain,
            "order_no": item.order_no,
            "cefr": item.cefr,
            "can_do_en": item.can_do_en,
            "can_do_es": item.can_do_es,
            "skill": item.skill,
            "interaction_type": item.interaction_type,
            "use_cases": list(item.use_cases),
            "character": item.character,
            "objective": item.objective,
            "obstacle": item.obstacle,
            "scenario_hint": item.scenario_hint,
        }
        for item in items
    ]
    chunk_rows = [
        {
            "id": chunk.id,
            "track_item_id": item.id,
            "position": chunk.position,
            "text": chunk.text,
            "example": chunk.example,
        }
        for item in items
        for chunk in item.chunks
    ]
    return item_rows, chunk_rows
