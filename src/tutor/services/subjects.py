"""get_subjects and save_subject (subjects spec 3.1, 3.2, 6.1; slice A plan rulings 13 and 27,
slice B plan rulings 28 and 29)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import UUID

from tutor.domain.subjects import SUBJECT_QUESTIONS, Subject, SubjectInput, validate_subject
from tutor.services.context import Services, local_date, user_zone
from tutor.services.errors import ServiceError, field_error
from tutor.services.ports import SubjectRow, UnitOfWork
from tutor.services.practice import plan_status_of, sync_plan
from tutor.services.subject_views import SaveSubjectResult, SubjectsView, SubjectView


def get_subjects(svc: Services, user_id: UUID) -> SubjectsView:
    """Every subject of the learner (active first) with its plan status at the learner's local
    date, and the onboarding questions."""
    with svc.uow(user_id) as uow:
        today = local_date(svc.clock(), user_zone(uow))
        views = tuple(_view(uow, row, today) for row in uow.subjects.list())
    return SubjectsView(subjects=views, questions=SUBJECT_QUESTIONS)


def save_subject(
    svc: Services,
    user_id: UUID,
    data: SubjectInput,
    subject_id: UUID | None = None,
    *,
    keep_target_date: bool = False,
) -> SaveSubjectResult:
    """Create a subject, or update one of the learner's; `target_date` is checked against the
    learner's local date. The save creates the subject's practice plan, or a new plan version
    when `minutes_per_session`, `sessions_per_week` or `target_date` changed (ruling 29);
    `plan_changed` says whether it did.

    On update an omitted optional answer keeps the stored value: `status`, `goal_text` and
    `context_text` None keep it (an empty text clears it), and `keep_target_date` keeps the
    stored date unchecked (a None date without it clears the date). On create, None means
    the default and `keep_target_date` means no date."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        current = None
        if subject_id is not None:
            # Locked until the unit of work ends: a concurrent save waits, then merges onto
            # this one's result instead of overwriting it (plan ruling 27b).
            found = uow.subjects.get_for_update(subject_id)
            if found is None:
                raise ServiceError("not_found", ("subject_id",))
            current = found.subject
        merged = data if current is None else _merge(data, current)
        checked = validate_subject(merged, local_date(now, user_zone(uow)))
        if isinstance(checked, tuple):
            raise field_error("validation_failed", checked)
        if current is not None and keep_target_date:
            checked = replace(checked, target_date=current.target_date)
        if subject_id is None:
            row = uow.subjects.create(checked, now)
        else:
            if not uow.subjects.update(subject_id, checked, now):  # pragma: no cover - read above
                raise ServiceError("not_found", ("subject_id",))
            updated = uow.subjects.get(subject_id)
            if updated is None:  # pragma: no cover - update just succeeded
                raise ServiceError("not_found", ("subject_id",))
            row = updated
        created = subject_id is None
        plan_changed = sync_plan(uow, row.id, row.subject, now)
        uow.audit.record(
            "subject_saved",
            {
                "subject_id": str(row.id),
                "created": created,
                "subject_status": checked.status,
                "plan_changed": plan_changed,
            },
            now,
        )
        view = _view(uow, row, local_date(now, user_zone(uow)))
        return SaveSubjectResult(subject=view, created=created, plan_changed=plan_changed)


def _merge(data: SubjectInput, current: Subject) -> SubjectInput:
    """Fill the omitted optional answers of an update from the stored subject. The target
    date is left out here: a kept date is not checked against today again."""
    return replace(
        data,
        goal_text=current.goal_text if data.goal_text is None else data.goal_text,
        context_text=current.context_text if data.context_text is None else data.context_text,
        status=current.status if data.status is None else data.status,
    )


def _view(uow: UnitOfWork, row: SubjectRow, today: date) -> SubjectView:
    return SubjectView(
        id=row.id, subject=row.subject, plan_status=plan_status_of(uow, row.id, today)
    )
