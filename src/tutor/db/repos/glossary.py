"""Glossary items, FSRS review states and review logs on Postgres."""

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping

from tutor.db.tables import glossary_items, review_logs, review_states, sessions
from tutor.domain.fsrs import FsrsState, new_state, state_from_json, state_to_json
from tutor.domain.glossary import (
    DECLINED_RETENTION_DAYS,
    GlossaryAction,
    IncomingItem,
    InsertItem,
    Promote,
    Reinforce,
    Reject,
    SetStatus,
)
from tutor.domain.lesson import DueCandidate
from tutor.services.ports import GlossaryRowData, ReviewLogRow


def _row(m: RowMapping) -> GlossaryRowData:
    return GlossaryRowData(
        id=m["id"],
        kind=m["kind"],
        text=m["text"],
        text_norm=m["text_norm"],
        meaning=m["meaning"],
        context_sentence=m["context_sentence"],
        domain=m["domain"],
        status=m["status"],
        seen_count=m["seen_count"],
        leech=m["leech"],
        created_at=m["created_at"],
        provisional_expires_at=m["provisional_expires_at"],
    )


def require_session(conn: Connection, user_id: UUID, session_id: UUID) -> None:
    """Foreign keys bypass RLS, so a write must first see its session as this user."""
    found = conn.execute(
        select(sessions.c.id).where(sessions.c.id == session_id, sessions.c.user_id == user_id)
    ).scalar_one_or_none()
    if found is None:
        raise LookupError("session not found")


def require_item(conn: Connection, user_id: UUID, item_id: UUID) -> None:
    found = conn.execute(
        select(glossary_items.c.id).where(
            glossary_items.c.id == item_id, glossary_items.c.user_id == user_id
        )
    ).scalar_one_or_none()
    if found is None:
        raise LookupError("glossary item not found")


def upsert_state(conn: Connection, user_id: UUID, item_id: UUID, state: FsrsState) -> None:
    """Write an FSRS state; last_ratings is maintained from review_logs, never here."""
    values = {
        "stability": state.stability,
        "difficulty": state.difficulty,
        "due_at": state.due,
        "last_review_at": state.last_review,
        "reps": state.reps,
        "lapses": state.lapses,
    }
    conn.execute(
        pg_insert(review_states)
        .values(glossary_item_id=item_id, user_id=user_id, **values)
        .on_conflict_do_update(index_elements=[review_states.c.glossary_item_id], set_=values)
    )


class PgGlossaryRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def by_norms(self, norms: Collection[str]) -> Mapping[str, GlossaryRowData]:
        if not norms:
            return {}
        rows = self._conn.execute(
            select(glossary_items).where(
                glossary_items.c.user_id == self._user_id,
                glossary_items.c.text_norm.in_(list(norms)),
            )
        ).mappings()
        return {m["text_norm"]: _row(m) for m in rows}

    def get_many(self, ids: Collection[UUID]) -> Mapping[UUID, GlossaryRowData]:
        if not ids:
            return {}
        rows = self._conn.execute(
            select(glossary_items).where(
                glossary_items.c.user_id == self._user_id, glossary_items.c.id.in_(list(ids))
            )
        ).mappings()
        return {m["id"]: _row(m) for m in rows}

    def apply(
        self,
        actions: Sequence[GlossaryAction],
        items: Sequence[IncomingItem],
        *,
        session_id: UUID,
        now: datetime,
    ) -> None:
        # `items` is part of the port; every action already carries what it writes.
        if any(not isinstance(a, Reject) for a in actions):
            require_session(self._conn, self._user_id, session_id)
        for action in actions:
            if isinstance(action, InsertItem):
                self._insert(action, session_id, now)
            elif isinstance(action, Reinforce):
                self._reinforce(action, session_id, now)
            elif isinstance(action, Promote):
                self._set_item(
                    action.item_id,
                    status="confirmed",
                    provisional_expires_at=None,
                    last_seen_session_id=session_id,
                    updated_at=now,
                )
                upsert_state(self._conn, self._user_id, action.item_id, new_state(action.first_due))
            elif isinstance(action, SetStatus):
                self._set_item(
                    action.item_id,
                    status=action.status,
                    provisional_expires_at=action.provisional_expires_at,
                    last_seen_session_id=session_id,
                    updated_at=now,
                )
            # Reject: nothing to write.

    def _set_item(self, item_id: UUID, **values: object) -> None:
        changed = self._conn.execute(
            update(glossary_items)
            .where(glossary_items.c.id == item_id, glossary_items.c.user_id == self._user_id)
            .values(**values)
        )
        if changed.rowcount != 1:
            raise LookupError("glossary item not found")

    def _insert(self, action: InsertItem, session_id: UUID, now: datetime) -> None:
        item_id = uuid4()
        self._conn.execute(
            insert(glossary_items).values(
                id=item_id,
                user_id=self._user_id,
                kind=action.item.kind,
                text=action.item.text,
                text_norm=action.text_norm,
                meaning=action.item.meaning,
                context_sentence=action.item.context_sentence,
                domain=action.item.domain,
                origin_session_id=session_id,
                status=action.status,
                seen_count=1,
                last_seen_session_id=session_id,
                leech=False,
                provisional_expires_at=action.provisional_expires_at,
                created_at=now,
                updated_at=now,
            )
        )
        if action.first_due is not None:
            upsert_state(self._conn, self._user_id, item_id, new_state(action.first_due))

    def _reinforce(self, action: Reinforce, session_id: UUID, now: datetime) -> None:
        self._set_item(
            action.item_id,
            kind=action.kind,
            seen_count=action.seen_count,
            leech=action.leech,
            last_seen_session_id=session_id,
            updated_at=now,
        )
        moved = self._conn.execute(
            update(review_states)
            .where(
                review_states.c.glossary_item_id == action.item_id,
                review_states.c.user_id == self._user_id,
            )
            .values(due_at=action.due)
        )
        if moved.rowcount == 0:  # an archived item has no state yet (ruling 9)
            upsert_state(self._conn, self._user_id, action.item_id, new_state(action.due))

    def due_candidates(self, now: datetime) -> tuple[DueCandidate, ...]:
        rows = self._conn.execute(
            select(
                glossary_items.c.id,
                glossary_items.c.kind,
                glossary_items.c.leech,
                review_states.c.due_at,
                review_states.c.last_ratings,
            )
            .join(review_states, review_states.c.glossary_item_id == glossary_items.c.id)
            .where(
                glossary_items.c.user_id == self._user_id,
                glossary_items.c.status == "confirmed",
                review_states.c.due_at <= now,
            )
            .order_by(review_states.c.due_at, glossary_items.c.id)
        ).all()
        return tuple(
            DueCandidate(
                item_id=r.id,
                kind=r.kind,
                leech=r.leech,
                due=r.due_at,
                last_ratings=tuple(r.last_ratings),
            )
            for r in rows
        )

    def provisional(self, limit: int) -> tuple[GlossaryRowData, ...]:
        rows = self._conn.execute(
            select(glossary_items)
            .where(
                glossary_items.c.user_id == self._user_id,
                glossary_items.c.status == "provisional",
            )
            .order_by(glossary_items.c.created_at, glossary_items.c.id)
            .limit(limit)
        ).mappings()
        return tuple(_row(m) for m in rows)

    def count_provisional(self) -> int:
        count = self._conn.execute(
            select(func.count())
            .select_from(glossary_items)
            .where(
                glossary_items.c.user_id == self._user_id,
                glossary_items.c.status == "provisional",
            )
        ).scalar_one()
        return int(count)

    def count_due(self, now: datetime) -> int:
        count = self._conn.execute(
            select(func.count())
            .select_from(glossary_items)
            .join(review_states, review_states.c.glossary_item_id == glossary_items.c.id)
            .where(
                glossary_items.c.user_id == self._user_id,
                glossary_items.c.status == "confirmed",
                review_states.c.due_at <= now,
            )
        ).scalar_one()
        return int(count)

    def purge(self, now: datetime) -> int:
        expired = self._conn.execute(
            delete(glossary_items).where(
                glossary_items.c.user_id == self._user_id,
                glossary_items.c.status == "provisional",
                glossary_items.c.provisional_expires_at < now,
            )
        )
        declined = self._conn.execute(
            delete(glossary_items).where(
                glossary_items.c.user_id == self._user_id,
                glossary_items.c.status == "declined",
                glossary_items.c.created_at < now - timedelta(days=DECLINED_RETENTION_DAYS),
            )
        )
        return expired.rowcount + declined.rowcount


class PgReviewRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def state(self, item_id: UUID) -> FsrsState | None:
        m = (
            self._conn.execute(
                select(review_states).where(
                    review_states.c.glossary_item_id == item_id,
                    review_states.c.user_id == self._user_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if m is None:
            return None
        return FsrsState(
            stability=m["stability"],
            difficulty=m["difficulty"],
            reps=m["reps"],
            lapses=m["lapses"],
            last_review=m["last_review_at"],
            due=m["due_at"],
        )

    def save_state(self, item_id: UUID, state: FsrsState) -> None:
        require_item(self._conn, self._user_id, item_id)
        upsert_state(self._conn, self._user_id, item_id, state)

    def log(
        self,
        session_id: UUID,
        item_id: UUID,
        rating: int,
        now: datetime,
        state_before: FsrsState,
    ) -> bool:
        require_session(self._conn, self._user_id, session_id)
        require_item(self._conn, self._user_id, item_id)
        elapsed = 0.0
        if state_before.last_review is not None:
            elapsed = max(0.0, (now - state_before.last_review).total_seconds() / 86400)
        inserted = self._conn.execute(
            pg_insert(review_logs)
            .values(
                id=uuid4(),
                glossary_item_id=item_id,
                user_id=self._user_id,
                session_id=session_id,
                rating=rating,
                reviewed_at=now,
                elapsed_days=elapsed,
                state_before=state_to_json(state_before),
            )
            .on_conflict_do_nothing(
                index_elements=[review_logs.c.session_id, review_logs.c.glossary_item_id]
            )
            .returning(review_logs.c.id)
        ).scalar_one_or_none()
        if inserted is None:
            return False
        self._refresh_last_ratings(item_id)
        return True

    def session_logs(self, session_id: UUID) -> tuple[ReviewLogRow, ...]:
        rows = self._conn.execute(
            select(review_logs)
            .where(review_logs.c.session_id == session_id, review_logs.c.user_id == self._user_id)
            .order_by(review_logs.c.reviewed_at, review_logs.c.id)
        ).mappings()
        return tuple(
            ReviewLogRow(
                item_id=m["glossary_item_id"],
                rating=m["rating"],
                reviewed_at=m["reviewed_at"],
                state_before=state_from_json(m["state_before"]),
            )
            for m in rows
        )

    def set_log_rating(self, session_id: UUID, item_id: UUID, rating: int) -> None:
        self._conn.execute(
            update(review_logs)
            .where(
                review_logs.c.session_id == session_id,
                review_logs.c.glossary_item_id == item_id,
                review_logs.c.user_id == self._user_id,
            )
            .values(rating=rating)
        )
        self._refresh_last_ratings(item_id)

    def _refresh_last_ratings(self, item_id: UUID) -> None:
        newest = self._conn.execute(
            select(review_logs.c.rating)
            .where(
                review_logs.c.glossary_item_id == item_id,
                review_logs.c.user_id == self._user_id,
            )
            .order_by(review_logs.c.reviewed_at.desc(), review_logs.c.id.desc())
            .limit(2)
        ).scalars()
        self._conn.execute(
            update(review_states)
            .where(
                review_states.c.glossary_item_id == item_id,
                review_states.c.user_id == self._user_id,
            )
            .values(last_ratings=list(reversed(list(newest))))  # oldest first, newest last
        )
