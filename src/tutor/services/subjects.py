"""get_subjects and save_subject (subjects spec 3.1, 3.2; plan rulings 5, 6, 13)."""

from __future__ import annotations

from uuid import UUID

from tutor.domain.subjects import SUBJECT_QUESTIONS, SubjectInput, validate_subject
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
    svc: Services, user_id: UUID, data: SubjectInput, subject_id: UUID | None = None
) -> SaveSubjectResult:
    """Create a subject, or replace one of the learner's; `target_date` is checked against the
    learner's local date. No plan exists in this slice, so `plan_changed` is always False."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        checked = validate_subject(data, local_date(now, user_zone(uow)))
        if isinstance(checked, tuple):
            raise field_error("validation_failed", checked)
        if subject_id is None:
            row = uow.subjects.create(checked, now)
        else:
            if not uow.subjects.update(subject_id, checked, now):
                raise ServiceError("not_found", ("subject_id",))
            found = uow.subjects.get(subject_id)
            if found is None:  # pragma: no cover - update just succeeded
                raise ServiceError("not_found", ("subject_id",))
            row = found
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


def _view(row: SubjectRow) -> SubjectView:
    return SubjectView(id=row.id, subject=row.subject, plan_status=None)
