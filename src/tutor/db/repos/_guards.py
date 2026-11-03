"""Ownership checks that run before a write, because foreign keys bypass row-level security."""

from uuid import UUID

from sqlalchemy import Connection, select

from tutor.db.tables import glossary_items, notes, sessions, subjects


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


def require_subject(conn: Connection, user_id: UUID, subject_id: UUID) -> None:
    """Raise LookupError unless `subject_id` is a subject of `user_id`."""
    found = conn.execute(
        select(subjects.c.id).where(subjects.c.id == subject_id, subjects.c.user_id == user_id)
    ).scalar_one_or_none()
    if found is None:
        raise LookupError("subject not found")


def require_note(conn: Connection, user_id: UUID, note_id: UUID) -> None:
    """Raise LookupError unless `note_id` is a note of `user_id`."""
    found = conn.execute(
        select(notes.c.id).where(notes.c.id == note_id, notes.c.user_id == user_id)
    ).scalar_one_or_none()
    if found is None:
        raise LookupError("note not found")
