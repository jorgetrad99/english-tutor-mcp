"""Practice plans, sessions and showings on Postgres (subjects spec 6, 15.4.1, 16.4; slice B
plan rulings 29, 30, 63; migration 0007).

Every statement filters by the unit of work's user on top of row-level security, and every
write that references another row checks ownership and the subject first (foreign keys ignore
RLS and tie rows to the same user only). Answers travel as bound JSON parameters and are never
logged. `shown_at` is written only by `UPDATE ... WHERE shown_at IS NULL`, so the first moment
wins (ruling 63).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, exists, func, insert, or_, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from tutor.db.repos._guards import require_subject
from tutor.db.repos.templates import _spec
from tutor.db.tables import (
    exercise_templates,
    practice_items,
    practice_plans,
    practice_sessions,
    template_instances,
)
from tutor.domain.practice import Candidate, DrawnItem, ItemSource, PracticeMode
from tutor.domain.practice_plan import PlanInputs, total_sessions
from tutor.domain.templates import Draw
from tutor.services.ports import (
    OpenPracticeExists,
    PracticeItemRow,
    PracticePlanRow,
    PracticeSessionRow,
)

OPEN_SESSION_INDEX = "ux_practice_sessions_one_open_per_user"
SESSION_SEQ_KEY = "practice_items_session_seq_key"


def _violates(exc: IntegrityError, name: str) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return diag is not None and getattr(diag, "constraint_name", None) == name


def draw_to_json(shown: Draw) -> dict[str, list[int]]:
    return {"entries": list(shown.entries), "order": list(shown.order)}


def draw_from_json(data: Mapping[str, Any]) -> Draw:
    return Draw(
        entries=tuple(int(i) for i in data.get("entries", ())),
        order=tuple(int(i) for i in data.get("order", ())),
    )


def _plan_row(m: RowMapping) -> PracticePlanRow:
    return PracticePlanRow(
        id=m["id"],
        subject_id=m["subject_id"],
        version=m["version"],
        status=m["status"],
        start_date=m["start_date"],
        inputs=PlanInputs(
            minutes_per_session=m["minutes_per_session"],
            sessions_per_week=m["sessions_per_week"],
            target_date=m["target_date"],
        ),
        total_sessions=m["total_sessions"],
        rationale=m["rationale"],
        created_at=m["created_at"],
    )


def _session_row(m: RowMapping) -> PracticeSessionRow:
    return PracticeSessionRow(
        id=m["id"],
        subject_id=m["subject_id"],
        plan_id=m["plan_id"],
        mode=m["mode"],
        status=m["status"],
        started_at=m["started_at"],
        ended_at=m["ended_at"],
        self_rating=m["self_rating"],
        summary=m["summary"],
        item_count=m["item_count"],
    )


def _item_row(m: RowMapping) -> PracticeItemRow:
    return PracticeItemRow(
        id=m["id"],
        session_id=m["session_id"],
        seq=m["seq"],
        instance_id=m["instance_id"],
        draw_seed=m["draw_seed"],
        draw=draw_from_json(m["draw"]),
        shown_at=m["shown_at"],
        answer=m["answer"],
        correct=m["correct"],
        score=m["score"],
        answered_at=m["answered_at"],
        source=m["source"],
    )


class PgPracticeRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    # --- reads used by the writes ---------------------------------------------------------------

    def _session(self, session_id: UUID) -> RowMapping | None:
        return (
            self._conn.execute(
                select(practice_sessions).where(
                    practice_sessions.c.id == session_id,
                    practice_sessions.c.user_id == self._user_id,
                )
            )
            .mappings()
            .one_or_none()
        )

    def _require_instances(self, subject_id: UUID, instance_ids: Sequence[UUID]) -> None:
        wanted = set(instance_ids)
        if not wanted:
            return
        found = set(
            self._conn.execute(
                select(template_instances.c.id).where(
                    template_instances.c.user_id == self._user_id,
                    template_instances.c.subject_id == subject_id,
                    template_instances.c.id.in_(wanted),
                )
            ).scalars()
        )
        if found != wanted:
            raise LookupError("instance not found")

    def _insert_items(
        self, session_id: UUID, items: Sequence[DrawnItem], shown_at: datetime | None
    ) -> None:
        if not items:
            return
        try:
            with self._conn.begin_nested():
                self._conn.execute(
                    insert(practice_items),
                    [
                        {
                            "id": item.item_id,
                            "user_id": self._user_id,
                            "session_id": session_id,
                            "seq": item.seq,
                            "instance_id": item.instance_id,
                            "draw_seed": item.draw_seed,
                            "draw": draw_to_json(item.draw),
                            "shown_at": shown_at,
                        }
                        for item in items
                    ],
                )
        except IntegrityError as exc:
            if _violates(exc, SESSION_SEQ_KEY):
                raise ValueError("duplicate seq in the session") from exc
            raise

    # --- plans ----------------------------------------------------------------------------------

    def active_plan(self, subject_id: UUID) -> PracticePlanRow | None:
        m = (
            self._conn.execute(
                select(practice_plans).where(
                    practice_plans.c.user_id == self._user_id,
                    practice_plans.c.subject_id == subject_id,
                    practice_plans.c.status == "active",
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _plan_row(m)

    def create_plan(
        self,
        subject_id: UUID,
        inputs: PlanInputs,
        start_date: date,
        now: datetime,
        rationale: Mapping[str, Any],
    ) -> PracticePlanRow:
        # Same TypeError as the in-memory store for values JSONB cannot hold, before any write.
        json.dumps(dict(rationale))
        require_subject(self._conn, self._user_id, subject_id)
        mine = (
            practice_plans.c.user_id == self._user_id,
            practice_plans.c.subject_id == subject_id,
        )
        latest = self._conn.execute(
            select(func.coalesce(func.max(practice_plans.c.version), 0)).where(*mine)
        ).scalar_one()
        self._conn.execute(
            update(practice_plans)
            .where(*mine, practice_plans.c.status == "active")
            .values(status="superseded")
        )
        m = (
            self._conn.execute(
                insert(practice_plans)
                .values(
                    user_id=self._user_id,
                    subject_id=subject_id,
                    version=latest + 1,
                    status="active",
                    start_date=start_date,
                    minutes_per_session=inputs.minutes_per_session,
                    sessions_per_week=inputs.sessions_per_week,
                    target_date=inputs.target_date,
                    total_sessions=total_sessions(inputs, start_date),
                    rationale=dict(rationale),
                    created_at=now,
                )
                .returning(*practice_plans.c)
            )
            .mappings()
            .one()
        )
        return _plan_row(m)

    def completed_count(self, plan_id: UUID) -> int:
        answered = exists().where(
            practice_items.c.session_id == practice_sessions.c.id,
            practice_items.c.user_id == self._user_id,
            practice_items.c.answered_at.is_not(None),
        )
        return int(
            self._conn.execute(
                select(func.count()).where(
                    practice_sessions.c.user_id == self._user_id,
                    practice_sessions.c.plan_id == plan_id,
                    or_(
                        and_(
                            practice_sessions.c.mode != "class",
                            practice_sessions.c.status == "closed",
                        ),
                        and_(practice_sessions.c.mode == "class", answered),
                    ),
                )
            ).scalar_one()
        )

    # --- sessions -------------------------------------------------------------------------------

    def open_session(self) -> PracticeSessionRow | None:
        m = (
            self._conn.execute(
                select(practice_sessions).where(
                    practice_sessions.c.user_id == self._user_id,
                    practice_sessions.c.status == "open",
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _session_row(m)

    def create_session(
        self,
        session_id: UUID,
        *,
        subject_id: UUID,
        plan_id: UUID | None,
        mode: PracticeMode,
        items: Sequence[DrawnItem],
        now: datetime,
    ) -> PracticeSessionRow:
        require_subject(self._conn, self._user_id, subject_id)
        if plan_id is not None:
            found = self._conn.execute(
                select(practice_plans.c.id).where(
                    practice_plans.c.id == plan_id,
                    practice_plans.c.user_id == self._user_id,
                    practice_plans.c.subject_id == subject_id,
                )
            ).scalar_one_or_none()
            if found is None:
                raise LookupError("plan not found")
        self._require_instances(subject_id, [item.instance_id for item in items])
        if len({item.seq for item in items}) != len(items):
            raise ValueError("duplicate seq in the session")
        try:
            # A savepoint keeps the caller's transaction usable after a unique violation.
            with self._conn.begin_nested():
                self._conn.execute(
                    insert(practice_sessions).values(
                        id=session_id,
                        user_id=self._user_id,
                        subject_id=subject_id,
                        plan_id=plan_id,
                        mode=mode,
                        status="open",
                        started_at=now,
                        item_count=len(items),
                    )
                )
        except IntegrityError as exc:
            if _violates(exc, OPEN_SESSION_INDEX):
                raise OpenPracticeExists from exc
            raise
        self._insert_items(session_id, items, shown_at=None)
        created = self.session(session_id)
        if created is None:  # pragma: no cover - the insert just succeeded under RLS
            raise RuntimeError("inserted practice session is not visible to its user")
        return created

    def session(self, session_id: UUID) -> PracticeSessionRow | None:
        m = self._session(session_id)
        return None if m is None else _session_row(m)

    def items(self, session_id: UUID) -> tuple[PracticeItemRow, ...]:
        rows = self._conn.execute(
            select(practice_items)
            .where(
                practice_items.c.user_id == self._user_id,
                practice_items.c.session_id == session_id,
            )
            .order_by(practice_items.c.seq)
        ).mappings()
        return tuple(_item_row(m) for m in rows)

    def item(self, item_id: UUID) -> PracticeItemRow | None:
        m = (
            self._conn.execute(
                select(practice_items).where(
                    practice_items.c.id == item_id, practice_items.c.user_id == self._user_id
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _item_row(m)

    def add_item(self, session_id: UUID, item: DrawnItem, now: datetime) -> PracticeItemRow:
        session = self._session(session_id)
        if session is None:
            raise LookupError("session not found")
        self._require_instances(session["subject_id"], [item.instance_id])
        if session["status"] != "open":
            raise ValueError("the session is not open")
        self._insert_items(session_id, [item], shown_at=now)
        stored = self.item(item.item_id)
        if stored is None:  # pragma: no cover - the insert just succeeded under RLS
            raise RuntimeError("inserted practice item is not visible to its user")
        return stored

    def mark_shown(self, item_id: UUID, now: datetime) -> datetime | None:
        mine = (practice_items.c.id == item_id, practice_items.c.user_id == self._user_id)
        self._conn.execute(
            update(practice_items)
            .where(*mine, practice_items.c.shown_at.is_(None))
            .values(shown_at=now)
        )
        shown: datetime | None = self._conn.execute(
            select(practice_items.c.shown_at).where(*mine)
        ).scalar_one_or_none()
        return shown

    def record(
        self,
        item_id: UUID,
        *,
        answer: Mapping[str, Any],
        correct: bool | None,
        score: int,
        source: ItemSource,
        now: datetime,
    ) -> PracticeItemRow | None:
        current = self.item(item_id)
        if current is None:
            raise LookupError("item not found")
        if current.answered_at is not None:
            return None
        if current.shown_at is None or now < current.shown_at:
            raise ValueError("an item is answered only after it was shown")
        m = (
            self._conn.execute(
                update(practice_items)
                .where(
                    practice_items.c.id == item_id,
                    practice_items.c.user_id == self._user_id,
                    practice_items.c.answered_at.is_(None),
                )
                .values(
                    answer=dict(answer),
                    correct=correct,
                    score=score,
                    answered_at=now,
                    source=source,
                )
                .returning(*practice_items.c)
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _item_row(m)

    def mark_incomplete(self, session_id: UUID, now: datetime) -> None:
        self._conn.execute(
            update(practice_sessions)
            .where(
                practice_sessions.c.id == session_id,
                practice_sessions.c.user_id == self._user_id,
                practice_sessions.c.status == "open",
            )
            .values(status="incomplete", ended_at=now)
        )

    def close(
        self,
        session_id: UUID,
        *,
        summary: Mapping[str, Any],
        self_rating: int | None,
        now: datetime,
    ) -> None:
        self._conn.execute(
            update(practice_sessions)
            .where(
                practice_sessions.c.id == session_id,
                practice_sessions.c.user_id == self._user_id,
                practice_sessions.c.status == "open",
            )
            .values(status="closed", ended_at=now, summary=dict(summary), self_rating=self_rating)
        )

    def candidates(self, subject_id: UUID) -> tuple[Candidate, ...]:
        last_shown = (
            select(
                practice_items.c.instance_id,
                func.max(practice_items.c.shown_at).label("last_shown_at"),
            )
            .where(practice_items.c.user_id == self._user_id)
            .group_by(practice_items.c.instance_id)
            .subquery()
        )
        rows = self._conn.execute(
            select(
                template_instances.c.id,
                template_instances.c.template_id,
                template_instances.c.params,
                last_shown.c.last_shown_at,
            )
            .select_from(
                template_instances.outerjoin(
                    last_shown, last_shown.c.instance_id == template_instances.c.id
                )
            )
            .where(
                template_instances.c.user_id == self._user_id,
                template_instances.c.subject_id == subject_id,
                template_instances.c.status == "active",
                template_instances.c.template_purpose == "exercise",
            )
            .order_by(template_instances.c.id)
        ).mappings()
        found = list(rows)
        template_ids = {m["template_id"] for m in found}
        specs = {
            t["id"]: _spec(t)
            for t in self._conn.execute(
                select(exercise_templates).where(
                    exercise_templates.c.user_id == self._user_id,
                    exercise_templates.c.id.in_(template_ids),
                )
            ).mappings()
        }
        return tuple(
            Candidate(
                instance_id=m["id"],
                spec=specs[m["template_id"]],
                params=m["params"],
                last_shown_at=m["last_shown_at"],
            )
            for m in found
        )
