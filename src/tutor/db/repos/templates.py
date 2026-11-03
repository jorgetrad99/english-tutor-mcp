"""Exercise templates and template instances on Postgres (subjects spec 15.3, 15.4, 15.10).

Every statement filters by the unit of work's user on top of row-level security, and every
write that references another row checks ownership and the subject first (foreign keys ignore
RLS and tie rows to the same user only, plan ruling 16). Template and instance text travels as
bound parameters only. The app never deletes either kind of row: it retires them.
"""

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping

from tutor.db.repos._guards import require_subject
from tutor.db.repos.notes import upsert_concept
from tutor.db.tables import concepts, exercise_templates, notes, template_instances
from tutor.domain.notes import ConceptName, name_norm
from tutor.domain.templates import (
    Origin,
    TemplateSpec,
    param_specs_from_json,
    param_specs_to_json,
    spec_hash,
)
from tutor.services.ports import InstanceRow, TemplateRow

_LISTED = [c for c in exercise_templates.c if c.name != "html"]


def _spec(m: RowMapping) -> TemplateSpec:
    return TemplateSpec(
        name=m["name"],
        description=m["description"],
        purpose=m["template_purpose"],
        practice_kind=m["practice_kind"],
        grading_kind=m["grading_kind"],
        text_match=m["text_match"],
        numeric_tolerance=m["numeric_tolerance"],
        pool_draw=m["pool_draw"],
        pool_count=m["pool_count"],
        needs_view=m["needs_view"],
        params=param_specs_from_json(m["params"]),
        options_param=m["options_param"],
    )


def _template_row(m: RowMapping, *, with_html: bool) -> TemplateRow:
    return TemplateRow(
        id=m["id"],
        subject_id=m["subject_id"],
        spec=_spec(m),
        html=m["html"] if with_html else None,
        html_sha256=m["html_sha256"],
        bytes=m["bytes"],
        guide_version=m["guide_version"],
        status=m["status"],
        supersedes_template_id=m["supersedes_template_id"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
        retired_at=m["retired_at"],
    )


class PgTemplateRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def _mine(self, subject_id: UUID) -> Any:
        return (
            exercise_templates.c.user_id == self._user_id,
            exercise_templates.c.subject_id == subject_id,
        )

    def list_active(self, subject_id: UUID) -> tuple[TemplateRow, ...]:
        rows = self._conn.execute(
            select(*_LISTED)
            .where(*self._mine(subject_id), exercise_templates.c.status == "active")
            # Code-point order ("C"), the order the in-memory store uses.
            .order_by(exercise_templates.c.name_norm.collate("C"), exercise_templates.c.id)
        ).mappings()
        return tuple(_template_row(m, with_html=False) for m in rows)

    def get(self, template_id: UUID) -> TemplateRow | None:
        m = (
            self._conn.execute(
                select(exercise_templates).where(
                    exercise_templates.c.id == template_id,
                    exercise_templates.c.user_id == self._user_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _template_row(m, with_html=True)

    def find_same(self, subject_id: UUID, spec_sha256: str, html_sha256: str) -> TemplateRow | None:
        rows = self._conn.execute(
            select(exercise_templates)
            .where(
                *self._mine(subject_id),
                exercise_templates.c.status == "active",
                exercise_templates.c.html_sha256 == html_sha256,
            )
            .order_by(exercise_templates.c.created_at, exercise_templates.c.id)
        ).mappings()
        for m in rows:
            row = _template_row(m, with_html=True)
            if spec_hash(row.spec) == spec_sha256:
                return row
        return None

    def active_by_name(self, subject_id: UUID, name_norm: str) -> TemplateRow | None:
        m = (
            self._conn.execute(
                select(*_LISTED).where(
                    *self._mine(subject_id),
                    exercise_templates.c.status == "active",
                    exercise_templates.c.name_norm == name_norm,
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _template_row(m, with_html=False)

    def create(
        self,
        subject_id: UUID,
        spec: TemplateSpec,
        html: str,
        guide_version: int,
        now: datetime,
        supersedes: UUID | None = None,
    ) -> TemplateRow:
        require_subject(self._conn, self._user_id, subject_id)
        if supersedes is not None:
            old = self._conn.execute(
                select(exercise_templates.c.status)
                .where(exercise_templates.c.id == supersedes, *self._mine(subject_id))
                .with_for_update()
            ).scalar_one_or_none()
            if old is None:
                raise LookupError("template not found")
            if old == "active":
                self._conn.execute(
                    update(exercise_templates)
                    .where(exercise_templates.c.id == supersedes, *self._mine(subject_id))
                    .values(status="retired", retired_at=now, updated_at=now)
                )
        encoded = html.encode("utf-8")
        m = (
            self._conn.execute(
                pg_insert(exercise_templates)
                .values(
                    id=uuid4(),
                    user_id=self._user_id,
                    subject_id=subject_id,
                    name=spec.name,
                    name_norm=name_norm(spec.name),
                    description=spec.description,
                    template_purpose=spec.purpose,
                    practice_kind=spec.practice_kind,
                    grading_kind=spec.grading_kind,
                    text_match=spec.text_match,
                    numeric_tolerance=spec.numeric_tolerance,
                    pool_draw=spec.pool_draw,
                    pool_count=spec.pool_count,
                    needs_view=spec.needs_view,
                    params=param_specs_to_json(spec.params),
                    options_param=spec.options_param,
                    html=html,
                    html_sha256=hashlib.sha256(encoded).hexdigest(),
                    bytes=len(encoded),
                    guide_version=guide_version,
                    status="active",
                    supersedes_template_id=supersedes,
                    created_at=now,
                    updated_at=now,
                    retired_at=None,
                )
                # The partial unique index on active names: a taken name inserts nothing.
                .on_conflict_do_nothing(
                    index_elements=["user_id", "subject_id", "name_norm"],
                    index_where=exercise_templates.c.status == "active",
                )
                .returning(exercise_templates)
            )
            .mappings()
            .one_or_none()
        )
        if m is None:
            raise ValueError("an active template of this subject has this name")
        return _template_row(m, with_html=True)


_INSTANCE = select(template_instances, concepts.c.name.label("concept_name")).outerjoin(
    concepts,
    (concepts.c.id == template_instances.c.concept_id)
    & (concepts.c.user_id == template_instances.c.user_id),
)


def _instance_row(m: RowMapping) -> InstanceRow:
    return InstanceRow(
        id=m["id"],
        subject_id=m["subject_id"],
        note_id=m["note_id"],
        seq=m["seq"],
        template_id=m["template_id"],
        purpose=m["template_purpose"],
        practice_kind=m["practice_kind"],
        grading_kind=m["grading_kind"],
        params=m["params"],
        prompt_text=m["prompt_text"],
        concept_id=m["concept_id"],
        concept=m["concept_name"],
        origin=m["origin"],
        content_key=m["content_key"],
        status=m["status"],
        supersedes_instance_id=m["supersedes_instance_id"],
        created_at=m["created_at"],
        retired_at=m["retired_at"],
    )


class PgInstanceRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def _one(self, *where: Any) -> InstanceRow | None:
        m = (
            self._conn.execute(
                _INSTANCE.where(template_instances.c.user_id == self._user_id, *where)
            )
            .mappings()
            .one_or_none()
        )
        return None if m is None else _instance_row(m)

    def _require(self, subject_id: UUID, note_id: UUID | None, template_id: UUID) -> None:
        require_subject(self._conn, self._user_id, subject_id)
        if note_id is not None:
            found = self._conn.execute(
                select(notes.c.id).where(
                    notes.c.id == note_id,
                    notes.c.user_id == self._user_id,
                    notes.c.subject_id == subject_id,
                )
            ).scalar_one_or_none()
            if found is None:
                raise LookupError("note not found")
        # FOR SHARE: a concurrent retirement of this template waits for this unit of work.
        active = self._conn.execute(
            select(exercise_templates.c.id)
            .where(
                exercise_templates.c.id == template_id,
                exercise_templates.c.user_id == self._user_id,
                exercise_templates.c.subject_id == subject_id,
                exercise_templates.c.status == "active",
            )
            .with_for_update(read=True)
        ).scalar_one_or_none()
        if active is None:
            raise LookupError("template not found")

    def insert(
        self,
        *,
        subject_id: UUID,
        note_id: UUID | None,
        template: TemplateRow,
        params: Mapping[str, Any],
        prompt_text: str,
        concept: ConceptName | None,
        origin: Origin,
        key: str,
        now: datetime,
        replaces: UUID | None = None,
    ) -> tuple[InstanceRow, bool]:
        self._require(subject_id, note_id, template.id)
        old_status = None
        if replaces is not None:
            old_status = self._conn.execute(
                select(template_instances.c.status)
                .where(
                    template_instances.c.id == replaces,
                    template_instances.c.user_id == self._user_id,
                    template_instances.c.subject_id == subject_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if old_status is None:
                raise LookupError("instance not found")
        existing = self._active_by_key(key)
        if (
            replaces is not None
            and old_status == "active"
            and (existing is None or existing.id != replaces)
        ):
            self._conn.execute(
                update(template_instances)
                .where(
                    template_instances.c.id == replaces,
                    template_instances.c.user_id == self._user_id,
                )
                .values(status="retired", retired_at=now)
            )
        if existing is not None:
            return existing, False
        seq = None
        if note_id is not None:
            seq = (
                1
                + self._conn.execute(
                    select(func.coalesce(func.max(template_instances.c.seq), 0)).where(
                        template_instances.c.note_id == note_id,
                        template_instances.c.user_id == self._user_id,
                    )
                ).scalar_one()
            )
        concept_id = (
            upsert_concept(self._conn, self._user_id, subject_id, concept)
            if concept is not None
            else None
        )
        spec = template.spec
        instance_id = self._conn.execute(
            pg_insert(template_instances)
            .values(
                id=uuid4(),
                user_id=self._user_id,
                subject_id=subject_id,
                note_id=note_id,
                seq=seq,
                template_id=template.id,
                template_purpose=spec.purpose,
                practice_kind=spec.practice_kind,
                grading_kind=spec.grading_kind,
                params=dict(params),
                prompt_text=prompt_text,
                concept_id=concept_id,
                origin=origin,
                content_key=key,
                status="active",
                supersedes_instance_id=replaces,
                created_at=now,
                retired_at=None,
            )
            # A concurrent identical call wins the partial unique key: no violation, no row.
            .on_conflict_do_nothing(
                index_elements=["user_id", "content_key"],
                index_where=template_instances.c.status == "active",
            )
            .returning(template_instances.c.id)
        ).scalar_one_or_none()
        if instance_id is None:  # pragma: no cover - needs a concurrent insert of the same key
            found = self._active_by_key(key)
            if found is None:
                raise LookupError("instance not found")
            return found, False
        row = self._one(template_instances.c.id == instance_id)
        if row is None:  # pragma: no cover - just inserted
            raise LookupError("instance not found")
        return row, True

    def _active_by_key(self, key: str) -> InstanceRow | None:
        return self._one(
            template_instances.c.content_key == key, template_instances.c.status == "active"
        )

    def get(self, instance_id: UUID) -> InstanceRow | None:
        return self._one(template_instances.c.id == instance_id)

    def for_note(self, note_id: UUID) -> tuple[InstanceRow, ...]:
        rows = self._conn.execute(
            _INSTANCE.where(
                template_instances.c.user_id == self._user_id,
                template_instances.c.note_id == note_id,
                template_instances.c.status == "active",
            ).order_by(template_instances.c.seq, template_instances.c.id)
        ).mappings()
        return tuple(_instance_row(m) for m in rows)
