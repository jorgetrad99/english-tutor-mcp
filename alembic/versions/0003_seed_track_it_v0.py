"""Seed the IT starter track v0 (24 items, 120 chunks) from tutor.content.

The tables are declared inline so this migration keeps working when tutor.db.tables changes.
The rows come from the packaged track YAML as it is when the migration runs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op
from tutor.content import load_track
from tutor.db.seed import track_rows

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

track_items = sa.table(
    "track_items",
    sa.column("id", sa.Text),
    sa.column("domain", sa.Text),
    sa.column("order_no", sa.Integer),
    sa.column("cefr", sa.Text),
    sa.column("can_do_en", sa.Text),
    sa.column("can_do_es", sa.Text),
    sa.column("skill", sa.Text),
    sa.column("interaction_type", sa.Text),
    sa.column("use_cases", ARRAY(sa.Text)),
    sa.column("character", sa.Text),
    sa.column("objective", sa.Text),
    sa.column("obstacle", sa.Text),
    sa.column("scenario_hint", sa.Text),
)
track_chunks = sa.table(
    "track_chunks",
    sa.column("id", sa.Text),
    sa.column("track_item_id", sa.Text),
    sa.column("position", sa.SmallInteger),
    sa.column("text", sa.Text),
    sa.column("example", sa.Text),
)


def upgrade() -> None:
    items, chunks = track_rows(load_track())
    op.bulk_insert(track_items, items)
    op.bulk_insert(track_chunks, chunks)


def downgrade() -> None:
    ids = [item.id for item in load_track()]
    op.execute(sa.delete(track_chunks).where(track_chunks.c.track_item_id.in_(ids)))
    op.execute(sa.delete(track_items).where(track_items.c.id.in_(ids)))
