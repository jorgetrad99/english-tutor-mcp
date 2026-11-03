"""The facts behind the subject metrics, on Postgres (subjects spec 16.7, 16.10, 16.11; slice B
plan ruling 70).

Reads only. Every statement filters `user_id = :uid` on every table it reads, on top of forced
row-level security, and none selects learner text except concept and template names:
`practice_items.answer`, prompts, parameters and note text are never read (a test checks the
compiled statements). The domain computes; these return facts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Date, Select, cast, func, select
from sqlalchemy.sql.elements import ColumnElement

from tutor.db.tables import (
    audit_log,
    concepts,
    exercise_templates,
    notes,
    practice_items,
    practice_sessions,
    subjects,
    template_instances,
)
from tutor.domain.subject_metrics import (
    ActivityFacts,
    ConceptFact,
    SessionFact,
    ShowingFact,
    TemplateFact,
)

SEARCH_EVENT = "notes_searched"


def showings_statement(user_id: UUID, subject_id: UUID) -> Select[Any]:
    """Answered showings of the subject with their instance's template, concept and kind."""
    return (
        select(
            template_instances.c.id.label("instance_id"),
            template_instances.c.template_id,
            template_instances.c.concept_id,
            template_instances.c.grading_kind,
            practice_items.c.correct,
            practice_items.c.score,
            practice_items.c.shown_at,
            practice_items.c.answered_at,
        )
        .select_from(
            practice_items.join(
                practice_sessions, practice_sessions.c.id == practice_items.c.session_id
            ).join(template_instances, template_instances.c.id == practice_items.c.instance_id)
        )
        .where(
            practice_items.c.user_id == user_id,
            practice_sessions.c.user_id == user_id,
            template_instances.c.user_id == user_id,
            practice_sessions.c.subject_id == subject_id,
            practice_items.c.answered_at.is_not(None),
        )
        .order_by(practice_items.c.answered_at, practice_items.c.id)
    )


def practice_days_statement(user_id: UUID, subject_id: UUID, tz_name: str) -> Select[Any]:
    """The earliest answered_at per local day of the learner's zone."""
    local_day = cast(func.timezone(tz_name, practice_items.c.answered_at), Date)
    first = func.min(practice_items.c.answered_at).label("first_at")
    return (
        select(first)
        .select_from(
            practice_items.join(
                practice_sessions, practice_sessions.c.id == practice_items.c.session_id
            )
        )
        .where(
            practice_items.c.user_id == user_id,
            practice_sessions.c.user_id == user_id,
            practice_sessions.c.subject_id == subject_id,
            practice_items.c.answered_at.is_not(None),
        )
        .group_by(local_day)
        .order_by(first)
    )


def sessions_statement(user_id: UUID, subject_id: UUID) -> Select[Any]:
    """Each session of the subject with its latest activity (greatest skips nulls)."""
    latest = func.greatest(
        practice_sessions.c.started_at,
        func.max(practice_items.c.shown_at),
        func.max(practice_items.c.answered_at),
    ).label("last_activity_at")
    return (
        select(practice_sessions.c.started_at, practice_sessions.c.status, latest)
        .select_from(
            practice_sessions.outerjoin(
                practice_items,
                (practice_items.c.session_id == practice_sessions.c.id)
                & (practice_items.c.user_id == user_id),
            )
        )
        .where(
            practice_sessions.c.user_id == user_id,
            practice_sessions.c.subject_id == subject_id,
        )
        .group_by(practice_sessions.c.id)
        .order_by(practice_sessions.c.started_at, practice_sessions.c.id)
    )


def _created(table: Any, user_id: UUID, subject_id: UUID) -> Select[Any]:
    return (
        select(table.c.created_at)
        .where(table.c.user_id == user_id, table.c.subject_id == subject_id)
        .order_by(table.c.created_at)
    )


def _searches(user_id: UUID, scope: ColumnElement[bool]) -> Select[Any]:
    return select(audit_log.c.at).where(
        audit_log.c.user_id == user_id, audit_log.c.event == SEARCH_EVENT, scope
    )


def activity_statements(user_id: UUID, subject_id: UUID) -> tuple[Select[Any], ...]:
    """created_at of notes, templates and instances, and the subject's search moments."""
    return (
        _created(notes, user_id, subject_id),
        _created(exercise_templates, user_id, subject_id),
        _created(template_instances, user_id, subject_id),
        _searches(user_id, audit_log.c.meta["subject_id"].astext == str(subject_id)).order_by(
            audit_log.c.at
        ),
    )


def concepts_statement(user_id: UUID, subject_id: UUID) -> Select[Any]:
    return (
        select(concepts.c.id, concepts.c.name, concepts.c.name_norm)
        .where(concepts.c.user_id == user_id, concepts.c.subject_id == subject_id)
        # Code-point order ("C"), the order the in-memory store uses.
        .order_by(concepts.c.name_norm.collate("C"), concepts.c.id)
    )


def templates_statement(user_id: UUID, subject_id: UUID) -> Select[Any]:
    return (
        select(
            exercise_templates.c.id,
            exercise_templates.c.name,
            exercise_templates.c.grading_kind,
            exercise_templates.c.status,
        )
        .where(
            exercise_templates.c.user_id == user_id,
            exercise_templates.c.subject_id == subject_id,
        )
        .order_by(exercise_templates.c.name_norm.collate("C"), exercise_templates.c.id)
    )


def unscoped_searches_statement(user_id: UUID) -> Select[Any]:
    scope = audit_log.c.meta["subject_id"].astext.is_(None)
    return select(func.count()).select_from(_searches(user_id, scope).subquery())


class PgProgressRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def _owns(self, subject_id: UUID) -> bool:
        found = self._conn.execute(
            select(subjects.c.id).where(
                subjects.c.id == subject_id, subjects.c.user_id == self._user_id
            )
        ).scalar_one_or_none()
        return found is not None

    def showings(self, subject_id: UUID) -> tuple[ShowingFact, ...]:
        rows = self._conn.execute(showings_statement(self._user_id, subject_id)).mappings()
        return tuple(
            ShowingFact(
                instance_id=m["instance_id"],
                template_id=m["template_id"],
                concept_id=m["concept_id"],
                grading_kind=m["grading_kind"],
                correct=m["correct"],
                score=m["score"],
                shown_at=m["shown_at"],
                answered_at=m["answered_at"],
            )
            for m in rows
        )

    def practice_days(self, subject_id: UUID, tz_name: str) -> tuple[datetime, ...]:
        statement = practice_days_statement(self._user_id, subject_id, tz_name)
        return tuple(self._conn.execute(statement).scalars())

    def sessions(self, subject_id: UUID) -> tuple[SessionFact, ...]:
        rows = self._conn.execute(sessions_statement(self._user_id, subject_id)).mappings()
        return tuple(
            SessionFact(
                started_at=m["started_at"],
                status=m["status"],
                last_activity_at=m["last_activity_at"],
            )
            for m in rows
        )

    def activity(self, subject_id: UUID) -> ActivityFacts:
        if not self._owns(subject_id):
            return ActivityFacts((), (), (), ())
        found = [
            tuple(self._conn.execute(statement).scalars())
            for statement in activity_statements(self._user_id, subject_id)
        ]
        return ActivityFacts(
            notes=found[0], templates=found[1], instances=found[2], searches=found[3]
        )

    def concepts(self, subject_id: UUID) -> tuple[ConceptFact, ...]:
        rows = self._conn.execute(concepts_statement(self._user_id, subject_id)).mappings()
        return tuple(ConceptFact(m["id"], m["name"], m["name_norm"]) for m in rows)

    def templates(self, subject_id: UUID) -> tuple[TemplateFact, ...]:
        rows = self._conn.execute(templates_statement(self._user_id, subject_id)).mappings()
        return tuple(TemplateFact(m["id"], m["name"], m["grading_kind"], m["status"]) for m in rows)

    def unscoped_search_count(self) -> int:
        return int(self._conn.execute(unscoped_searches_statement(self._user_id)).scalar_one())
