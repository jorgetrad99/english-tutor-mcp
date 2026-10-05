"""Ownership checks that run before a write, because foreign keys bypass row-level security."""

from uuid import UUID

from sqlalchemy import Connection, select

from tutor.db.tables import glossary_items, sessions


def require_session(conn: Connection, user_id: UUID, session_id: UUID) -> None:
    """Raise LookupError unless `session_id` is a session of `user_id`."""
    found = conn.execute(
        select(sessions.c.id).where(sessions.c.id == session_id, sessions.c.user_id == user_id)
    ).scalar_one_or_none()
    if found is None:
        raise LookupError("session not found")


def require_item(conn: Connection, user_id: UUID, item_id: UUID) -> None:
    """Raise LookupError unless `item_id` is a glossary item of `user_id`."""
    found = conn.execute(
        select(glossary_items.c.id).where(
            glossary_items.c.id == item_id, glossary_items.c.user_id == user_id
        )
    ).scalar_one_or_none()
    if found is None:
        raise LookupError("glossary item not found")
