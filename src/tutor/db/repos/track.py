"""The seeded starter track (no RLS; read only for tutor_app)."""

from sqlalchemy import Connection, select

from tutor.db.tables import track_chunks, track_items
from tutor.domain.profile import Domain
from tutor.domain.track import TrackChunk, TrackItem


class PgTrackRepo:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def items(self, domain: Domain) -> tuple[TrackItem, ...]:
        item_rows = (
            self._conn.execute(
                select(track_items)
                .where(track_items.c.domain == domain)
                .order_by(track_items.c.order_no, track_items.c.id)
            )
            .mappings()
            .all()
        )
        chunk_rows = (
            self._conn.execute(
                select(track_chunks)
                .join(track_items, track_items.c.id == track_chunks.c.track_item_id)
                .where(track_items.c.domain == domain)
                .order_by(track_chunks.c.track_item_id, track_chunks.c.position)
            )
            .mappings()
            .all()
        )
        chunks: dict[str, list[TrackChunk]] = {}
        for c in chunk_rows:
            chunks.setdefault(c["track_item_id"], []).append(
                TrackChunk(id=c["id"], position=c["position"], text=c["text"], example=c["example"])
            )
        return tuple(
            TrackItem(
                id=r["id"],
                domain=r["domain"],
                order_no=r["order_no"],
                cefr=r["cefr"],
                skill=r["skill"],
                interaction_type=r["interaction_type"],
                use_cases=tuple(r["use_cases"]),
                can_do_en=r["can_do_en"],
                can_do_es=r["can_do_es"],
                character=r["character"],
                objective=r["objective"],
                obstacle=r["obstacle"],
                scenario_hint=r["scenario_hint"],
                chunks=tuple(chunks.get(r["id"], ())),
            )
            for r in item_rows
        )
