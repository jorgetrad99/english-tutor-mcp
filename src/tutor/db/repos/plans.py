"""Plan-lite versions and items on Postgres (one active plan per user, partial unique index)."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, insert, select, update
from sqlalchemy.engine import RowMapping

from tutor.db.repos._guards import require_session
from tutor.db.tables import plan_items, plans
from tutor.domain.plan_lite import PlannedItem
from tutor.services.ports import ActivePlan, PlanItemRow


def _item(row: RowMapping) -> PlanItemRow:
    return PlanItemRow(
        id=row["id"],
        week_no=row["week_no"],
        order_no=row["order_no"],
        track_item_id=row["track_item_id"],
        variant=row["variant"],
        status=row["status"],
        done_session_id=row["done_session_id"],
    )


class PgPlanRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def active(self) -> ActivePlan | None:
        row = (
            self._conn.execute(
                select(plans).where(plans.c.user_id == self._user_id, plans.c.status == "active")
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._with_items(row)

    def create(
        self, items: Sequence[PlannedItem], rationale: Mapping[str, Any], now: datetime
    ) -> ActivePlan:
        # Same TypeError as the in-memory store for values JSONB cannot hold, before any write.
        json.dumps(dict(rationale))
        latest = self._conn.execute(
            select(func.coalesce(func.max(plans.c.version), 0)).where(
                plans.c.user_id == self._user_id
            )
        ).scalar_one()
        self._conn.execute(
            update(plans)
            .where(plans.c.user_id == self._user_id, plans.c.status == "active")
            .values(status="superseded")
        )
        plan_id = uuid4()
        self._conn.execute(
            insert(plans).values(
                id=plan_id,
                user_id=self._user_id,
                version=int(latest) + 1,
                status="active",
                generated_at=now,
                rationale=dict(rationale),
            )
        )
        if items:
            self._conn.execute(
                insert(plan_items),
                [
                    {
                        "id": uuid4(),
                        "plan_id": plan_id,
                        "user_id": self._user_id,
                        "week_no": p.week_no,
                        "order_no": p.order_no,
                        "track_item_id": p.track_item_id,
                        "variant": p.variant,
                        "status": "pending",
                        "done_session_id": None,
                    }
                    for p in items
                ],
            )
        row = (
            self._conn.execute(
                select(plans).where(plans.c.id == plan_id, plans.c.user_id == self._user_id)
            )
            .mappings()
            .one()
        )
        return self._with_items(row)

    def mark_done(self, plan_item_id: UUID, session_id: UUID) -> bool:
        require_session(self._conn, self._user_id, session_id)
        result = self._conn.execute(
            update(plan_items)
            .where(
                plan_items.c.id == plan_item_id,
                plan_items.c.user_id == self._user_id,
                plan_items.c.status != "done",
            )
            .values(status="done", done_session_id=session_id)
        )
        return result.rowcount == 1

    def done_base_track_ids(self) -> frozenset[str]:
        ids = self._conn.execute(
            select(plan_items.c.track_item_id)
            .where(
                plan_items.c.user_id == self._user_id,
                plan_items.c.status == "done",
                plan_items.c.variant == "base",
            )
            .distinct()
        ).scalars()
        return frozenset(ids)

    def _with_items(self, plan: RowMapping) -> ActivePlan:
        rows = (
            self._conn.execute(
                select(plan_items)
                .where(plan_items.c.plan_id == plan["id"], plan_items.c.user_id == self._user_id)
                .order_by(plan_items.c.week_no, plan_items.c.order_no)
            )
            .mappings()
            .all()
        )
        return ActivePlan(
            id=plan["id"],
            version=plan["version"],
            generated_at=plan["generated_at"],
            rationale=plan["rationale"],
            items=tuple(_item(r) for r in rows),
        )
