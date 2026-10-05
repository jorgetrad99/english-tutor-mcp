"""save_glossary (spec section 10.2)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from tutor.domain.glossary import (
    ExistingItem,
    GlossaryAction,
    GlossaryStatus,
    IncomingItem,
    InsertItem,
    Promote,
    Reinforce,
    Reject,
    SaveStatus,
    SetStatus,
    plan_glossary_save,
)
from tutor.domain.text import normalize
from tutor.services.context import Services, user_zone
from tutor.services.errors import ServiceError
from tutor.services.ports import UnitOfWork
from tutor.services.views import GlossarySaveResult, RejectedView

MAX_GLOSSARY_ITEMS = 10
GLOSSARY_SAVE_WINDOW = timedelta(hours=24)
SAVE_STATUSES = ("confirmed", "provisional", "declined")


def save_glossary(
    svc: Services,
    user_id: UUID,
    session_id: UUID,
    status: SaveStatus,
    items: Sequence[IncomingItem],
) -> GlossarySaveResult:
    """Insert, reinforce, promote or reject each item, in one unit of work."""
    if not 1 <= len(items) <= MAX_GLOSSARY_ITEMS:
        raise ServiceError("validation_failed", ("items",))
    if status not in SAVE_STATUSES:
        raise ServiceError("validation_failed", ("status",))
    now = svc.clock()
    with svc.uow(user_id) as uow:
        session = uow.sessions.get(session_id)
        if session is None:
            raise ServiceError("session_not_found")
        ended_long_ago = session.ended_at is None or now - session.ended_at > GLOSSARY_SAVE_WINDOW
        if session.status != "open" and ended_long_ago:
            raise ServiceError("session_closed")
        norms = {normalize(item.text) for item in items} - {""}
        existing = {
            norm: ExistingItem(
                id=row.id,
                kind=row.kind,
                status=row.status,
                seen_count=row.seen_count,
                leech=row.leech,
                created_at=row.created_at,
            )
            for norm, row in uow.glossary.by_norms(norms).items()
        }
        actions = plan_glossary_save(items, status, existing, now, user_zone(uow))
        uow.glossary.apply(actions, items, session_id=session_id, now=now)
        uow.audit.record(
            "glossary_saved",
            {
                "session_id": str(session_id),
                "status": status,
                "items": _audit_items(uow, actions, existing),
            },
            now,
        )
        return GlossarySaveResult(
            new=sum(1 for a in actions if isinstance(a, InsertItem)),
            reinforced=sum(1 for a in actions if isinstance(a, Reinforce)),
            promoted=sum(1 for a in actions if isinstance(a, Promote)),
            rejected=tuple(
                RejectedView(index=a.index, reason=a.reason)
                for a in actions
                if isinstance(a, Reject)
            ),
        )


def _audit_items(
    uow: UnitOfWork, actions: Sequence[GlossaryAction], existing: Mapping[str, ExistingItem]
) -> list[dict[str, Any]]:
    """One {id, action, status} per applied action, in index order: ids and enums, never text.
    Inserted ids are read back by text_norm (unique per user; duplicates in a call are Rejects)."""
    inserted = uow.glossary.by_norms([a.text_norm for a in actions if isinstance(a, InsertItem)])
    status_of: dict[UUID, GlossaryStatus] = {e.id: e.status for e in existing.values()}
    out: list[dict[str, Any]] = []
    entry: tuple[UUID, str, str]
    for a in actions:
        if isinstance(a, InsertItem):
            entry = (inserted[a.text_norm].id, "insert", a.status)
        elif isinstance(a, Reinforce):
            entry = (a.item_id, "reinforce", status_of[a.item_id])
        elif isinstance(a, Promote):
            entry = (a.item_id, "promote", "confirmed")
        elif isinstance(a, SetStatus):
            entry = (a.item_id, "set_status", a.status)
        else:
            continue  # Reject: nothing was written
        out.append({"id": str(entry[0]), "action": entry[1], "status": entry[2]})
    return out
