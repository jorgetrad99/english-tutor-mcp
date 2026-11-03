"""get_subjects and save_subject (subjects spec 3.1, 3.2; plan rulings 5, 6, 13)."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from tutor.domain.subjects import SUBJECT_QUESTIONS, Subject, SubjectInput, validate_subject
from tutor.services.context import Services, local_date, user_zone
from tutor.services.errors import ServiceError, field_error
from tutor.services.ports import SubjectRow
from tutor.services.subject_views import SaveSubjectResult, SubjectsView, SubjectView


def get_subjects(svc: Services, user_id: UUID) -> SubjectsView:
    """Every subject of the learner (active first) and the onboarding questions."""
    with svc.uow(user_id) as uow:
        rows = uow.subjects.list()
    return SubjectsView(subjects=tuple(_view(row) for row in rows), questions=SUBJECT_QUESTIONS)


def save_subject(
    svc: Services,
    user_id: UUID,
    data: SubjectInput,
    subject_id: UUID | None = None,
    *,
    keep_target_date: bool = False,
) -> SaveSubjectResult:
    """Create a subject, or update one of the learner's; `target_date` is checked against the
    learner's local date. No plan exists in this slice, so `plan_changed` is always False.

    On update an omitted optional answer keeps the stored value: `status`, `goal_text` and
    `context_text` None keep it (an empty text clears it), and `keep_target_date` keeps the
    stored date unchecked (a None date without it clears the date). On create, None means
    the default and `keep_target_date` means no date."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        current = None
        if subject_id is not None:
            found = uow.subjects.get(subject_id)
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
        uow.audit.record(
            "subject_saved",
            {
                "subject_id": str(row.id),
                "created": created,
                "subject_status": checked.status,
                "plan_changed": False,
            },
            now,
        )
        return SaveSubjectResult(subject=_view(row), created=created, plan_changed=False)


def _merge(data: SubjectInput, current: Subject) -> SubjectInput:
    """Fill the omitted optional answers of an update from the stored subject. The target
    date is left out here: a kept date is not checked against today again."""
    return replace(
        data,
        goal_text=current.goal_text if data.goal_text is None else data.goal_text,
        context_text=current.context_text if data.context_text is None else data.context_text,
        status=current.status if data.status is None else data.status,
    )


def _view(row: SubjectRow) -> SubjectView:
    return SubjectView(id=row.id, subject=row.subject, plan_status=None)
