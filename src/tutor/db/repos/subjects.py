"""Subjects on Postgres (subjects spec 3.1)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, case, insert, select, update
from sqlalchemy.engine import RowMapping

from tutor.db.tables import subjects
from tutor.domain.subjects import Subject
from tutor.services.ports import SubjectRow


def _row(m: RowMapping) -> SubjectRow:
    return SubjectRow(
        id=m["id"],
        subject=Subject(
            name=m["name"],
            goal_text=m["goal_text"],
            context_text=m["context_text"],
            preference=m["preference"],
            exercise_kinds=tuple(m["exercise_kinds"]),
            minutes_per_session=m["minutes_per_session"],
            sessions_per_week=m["sessions_per_week"],
            target_date=m["target_date"],
            status=m["status"],
        ),
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def _values(subject: Subject) -> dict[str, object]:
    return {
        "name": subject.name,
        "goal_text": subject.goal_text,
        "context_text": subject.context_text,
        "preference": subject.preference,
        "exercise_kinds": list(subject.exercise_kinds),
        "minutes_per_session": subject.minutes_per_session,
        "sessions_per_week": subject.sessions_per_week,
        "target_date": subject.target_date,
        "status": subject.status,
    }


class PgSubjectRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def list(self) -> tuple[SubjectRow, ...]:
        rows = self._conn.execute(
            select(subjects)
            .where(subjects.c.user_id == self._user_id)
            # Code-point order ("C"), the order the in-memory store uses.
            .order_by(
                case((subjects.c.status == "active", 0), else_=1),
                subjects.c.name.collate("C"),
                subjects.c.id,
            )
        ).mappings()
        return tuple(_row(m) for m in rows)

    def get(self, subject_id: UUID) -> SubjectRow | None:
        m = (
            self._conn.execute(
                select(subjects).where(
                    subjects.c.id == subject_id, subjects.c.user_id == self._user_id
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _row(m)

    def create(self, subject: Subject, now: datetime) -> SubjectRow:
        m = (
            self._conn.execute(
                insert(subjects)
                .values(
                    id=uuid4(),
                    user_id=self._user_id,
                    created_at=now,
                    updated_at=now,
                    **_values(subject),
                )
                .returning(subjects)
            )
            .mappings()
            .one()
        )
        return _row(m)

    def update(self, subject_id: UUID, subject: Subject, now: datetime) -> bool:
        changed = self._conn.execute(
            update(subjects)
            .where(subjects.c.id == subject_id, subjects.c.user_id == self._user_id)
            .values(updated_at=now, **_values(subject))
        )
        return changed.rowcount == 1
