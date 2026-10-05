"""Sessions, metrics and validated errors on Postgres."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from tutor.db.repos._guards import require_session
from tutor.db.tables import plan_items, session_errors, session_metrics, sessions
from tutor.domain.lesson import BriefVariant, RecentResult
from tutor.domain.levels import CefrLevel
from tutor.domain.metrics import SessionMetrics
from tutor.domain.validation import Evidence, SessionOutcome, ValidError
from tutor.services.ports import ClientName, Mode, OpenSessionExists, SessionRow

OPEN_SESSION_INDEX = "sessions_one_open_per_user"


def _violates(exc: IntegrityError, name: str) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return diag is not None and getattr(diag, "constraint_name", None) == name


def _row(m: RowMapping) -> SessionRow:
    return SessionRow(
        id=m["id"],
        plan_item_id=m["plan_item_id"],
        track_item_id=m["track_item_id"],
        prep_text=m["prep_text"],
        mode=m["mode"],
        client=m["client"],
        started_at=m["started_at"],
        ended_at=m["ended_at"],
        status=m["status"],
        low_trust=m["low_trust"],
        brief_variant=m["brief_variant"],
        chunks_offered=tuple(m["chunks_offered"]),
        result=m["result"],
    )


def _metric_values(m: SessionMetrics) -> dict[str, Any]:
    return {
        "user_words": m.user_words,
        "assistant_words_estimate": m.assistant_words_estimate,
        "user_ratio": m.user_ratio,
        "turns": m.turns,
        "words_per_turn": m.words_per_turn,
        "duration_min": m.duration_min,
        "user_words_per_min": m.user_words_per_min,
        "errors_total": m.errors_total,
        "errors_rejected": m.errors_rejected,
        "errors_by_category": dict(m.errors_by_category),
        "errors_per_100w": m.errors_per_100w,
        "recurring_errors": m.recurring_errors,
        "uptake_count": m.uptake_count,
        "chunks_offered": m.chunks_offered,
        "chunks_used": m.chunks_used,
        "chunks_rejected": m.chunks_rejected,
        "activation_rate": m.activation_rate,
    }


class PgSessionRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def _one(self, *where: Any, lock: bool = False) -> SessionRow | None:
        query = select(sessions).where(sessions.c.user_id == self._user_id, *where)
        if lock:
            query = query.with_for_update()
        m = self._conn.execute(query).mappings().one_or_none()
        return None if m is None else _row(m)

    def get(self, session_id: UUID) -> SessionRow | None:
        # FOR UPDATE: a concurrent end_session waits for the first to commit, then sees `result`.
        return self._one(sessions.c.id == session_id, lock=True)

    def open_session(self) -> SessionRow | None:
        return self._one(sessions.c.status == "open")

    def create(
        self,
        *,
        plan_item_id: UUID | None,
        track_item_id: str,
        prep_text: str | None,
        mode: Mode,
        client: ClientName,
        brief_variant: BriefVariant,
        chunks_offered: Sequence[str],
        now: datetime,
    ) -> SessionRow:
        if plan_item_id is not None:
            visible = self._conn.execute(
                select(plan_items.c.id).where(
                    plan_items.c.id == plan_item_id, plan_items.c.user_id == self._user_id
                )
            ).scalar_one_or_none()
            if visible is None:
                raise LookupError("plan item not found")
        session_id = uuid4()
        values = {
            "id": session_id,
            "user_id": self._user_id,
            "plan_item_id": plan_item_id,
            "track_item_id": track_item_id,
            "prep_text": prep_text,
            "mode": mode,
            "client": client,
            "started_at": now,
            "status": "open",
            "low_trust": False,
            "brief_variant": brief_variant,
            "chunks_offered": list(chunks_offered),
            "cefr_excluded": False,
        }
        try:
            # A savepoint keeps the caller's transaction usable after a unique violation.
            with self._conn.begin_nested():
                self._conn.execute(insert(sessions).values(**values))
        except IntegrityError as exc:
            if _violates(exc, OPEN_SESSION_INDEX):
                raise OpenSessionExists from exc
            raise
        created = self.get(session_id)
        if created is None:
            raise RuntimeError("inserted session is not visible to its user")
        return created

    def mark_incomplete(self, session_id: UUID, now: datetime) -> None:
        self._conn.execute(
            update(sessions)
            .where(
                sessions.c.id == session_id,
                sessions.c.user_id == self._user_id,
                sessions.c.status == "open",
            )
            .values(status="incomplete", ended_at=now)
        )

    def close(
        self,
        session_id: UUID,
        *,
        status: SessionOutcome,
        low_trust: bool,
        ended_at: datetime,
        evidence: Evidence,
        raw_evidence: Mapping[str, Any],
        cefr_excluded: bool,
        result: Mapping[str, Any],
    ) -> None:
        self._conn.execute(
            update(sessions)
            .where(sessions.c.id == session_id, sessions.c.user_id == self._user_id)
            .values(
                status=status,
                low_trust=low_trust,
                ended_at=ended_at,
                task_result=evidence.task_result,
                hints_given=evidence.hints_given,
                cefr_estimate_speaking=evidence.cefr_level,
                cefr_confidence=evidence.cefr_confidence,
                cefr_excluded=cefr_excluded,
                confidence_1_5=evidence.confidence_1_5,
                raw_evidence=dict(raw_evidence),
                result=dict(result),
            )
        )

    def count_started_since(self, since: datetime) -> int:
        count = self._conn.execute(
            select(func.count())
            .select_from(sessions)
            .where(sessions.c.user_id == self._user_id, sessions.c.started_at >= since)
        ).scalar_one()
        return int(count)

    def recent_results(self, limit: int) -> tuple[RecentResult, ...]:
        rows = self._conn.execute(
            select(sessions.c.task_result, sessions.c.hints_given)
            .where(
                sessions.c.user_id == self._user_id,
                sessions.c.status == "closed",
                sessions.c.task_result.is_not(None),
            )
            .order_by(sessions.c.ended_at.desc().nulls_last(), sessions.c.started_at.desc())
            .limit(limit)
        ).all()
        return tuple(
            RecentResult(task_result=r.task_result, hints_given=r.hints_given or 0) for r in rows
        )

    def closed_ended_at(self, since: datetime) -> tuple[datetime, ...]:
        ended = self._conn.execute(
            select(sessions.c.ended_at)
            .where(
                sessions.c.user_id == self._user_id,
                sessions.c.status == "closed",
                sessions.c.ended_at >= since,
            )
            .order_by(sessions.c.ended_at)
        ).scalars()
        return tuple(ended)

    def previous_cefr(self) -> CefrLevel | None:
        level = self._conn.execute(
            select(sessions.c.cefr_estimate_speaking)
            .where(
                sessions.c.user_id == self._user_id,
                sessions.c.status == "closed",
                sessions.c.cefr_excluded.is_(False),
                sessions.c.cefr_estimate_speaking.is_not(None),
            )
            .order_by(sessions.c.ended_at.desc().nulls_last())
            .limit(1)
        ).scalar_one_or_none()
        return level

    def last_done_by_track(self) -> Mapping[str, datetime]:
        rows = self._conn.execute(
            select(sessions.c.track_item_id, func.max(sessions.c.ended_at).label("last"))
            .where(sessions.c.user_id == self._user_id, sessions.c.status == "closed")
            .group_by(sessions.c.track_item_id)
        ).all()
        return {r.track_item_id: r.last for r in rows if r.last is not None}

    def save_metrics(self, session_id: UUID, metrics: SessionMetrics) -> None:
        require_session(self._conn, self._user_id, session_id)
        values = _metric_values(metrics)
        self._conn.execute(
            pg_insert(session_metrics)
            .values(session_id=session_id, user_id=self._user_id, **values)
            .on_conflict_do_update(index_elements=[session_metrics.c.session_id], set_=values)
        )

    def save_errors(self, session_id: UUID, errors: Sequence[ValidError]) -> None:
        require_session(self._conn, self._user_id, session_id)
        # Replace, so a repeated end_session write never duplicates rows.
        self._conn.execute(
            delete(session_errors).where(
                session_errors.c.session_id == session_id,
                session_errors.c.user_id == self._user_id,
            )
        )
        if errors:
            self._conn.execute(
                insert(session_errors),
                [
                    {
                        "id": uuid4(),
                        "session_id": session_id,
                        "user_id": self._user_id,
                        "said": e.said,
                        "correct": e.correct,
                        "correct_norm": e.correct_norm,
                        "category": e.category,
                        "turn_index": e.turn_index,
                    }
                    for e in errors
                ],
            )

    def recent_correct_norms(self, since: datetime, exclude: UUID) -> frozenset[str]:
        norms = self._conn.execute(
            select(session_errors.c.correct_norm)
            .join(sessions, sessions.c.id == session_errors.c.session_id)
            .where(
                session_errors.c.user_id == self._user_id,
                sessions.c.user_id == self._user_id,
                sessions.c.status == "closed",
                sessions.c.ended_at >= since,
                sessions.c.id != exclude,
            )
            .distinct()
        ).scalars()
        return frozenset(norms)
