"""subject_progress: the subject metrics (subjects spec 16; slice B plan rulings 64-71).

The repository returns facts, the domain computes: one unit of work reads the subject's
showings, practice days, sessions, activity, concepts and templates, then
`tutor.domain.subject_metrics.subject_progress` turns them into numbers with the server clock
and the learner's timezone. A foreign or missing subject is `not_found` before any read.
"""

from __future__ import annotations

from uuid import UUID

from tutor.domain.subject_metrics import SubjectProgress
from tutor.domain.subject_metrics import subject_progress as compute_progress
from tutor.services.context import Services, user_zone
from tutor.services.errors import ServiceError


def subject_progress(svc: Services, user_id: UUID, subject_id: UUID) -> SubjectProgress:
    """Accuracy per concept, time per item, the subject streak and weekly counts; every concept
    and template in order (the MCP output keeps the first 20 of each, ruling 71)."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        if uow.subjects.get(subject_id) is None:
            raise ServiceError("not_found", ("subject_id",))
        tz = user_zone(uow)
        progress = uow.progress
        return compute_progress(
            progress.showings(subject_id),
            progress.practice_days(subject_id, uow.users.timezone()),
            progress.sessions(subject_id),
            progress.activity(subject_id),
            progress.concepts(subject_id),
            progress.templates(subject_id),
            progress.unscoped_search_count(),
            now=now,
            tz=tz,
        )
