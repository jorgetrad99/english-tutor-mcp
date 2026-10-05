"""end_session: validate evidence, compute metrics, write everything once (spec 11.1-11.2)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from tutor.domain.fsrs import review
from tutor.domain.glossary import spontaneous_use
from tutor.domain.metrics import compute_metrics, summary_text
from tutor.domain.validation import Evidence, validate_evidence
from tutor.services.context import Services, current_streak, user_zone
from tutor.services.errors import ServiceError
from tutor.services.ports import SessionRow, UnitOfWork
from tutor.services.views import EndSessionResult

MAX_RAW_EVIDENCE_BYTES = 20 * 1024
RECURRING_WINDOW = timedelta(days=30)


def end_session(
    svc: Services,
    user_id: UUID,
    session_id: UUID,
    evidence: Evidence,
    raw_evidence: Mapping[str, Any],
) -> EndSessionResult:
    """Close the lesson. A repeat returns the stored result with already_closed=True."""
    size = len(json.dumps(dict(raw_evidence), ensure_ascii=False).encode("utf-8"))
    if size > MAX_RAW_EVIDENCE_BYTES:
        raise ServiceError("payload_too_large")
    now = svc.clock()
    with svc.uow(user_id) as uow:
        session = uow.sessions.get(session_id)
        if session is None:
            raise ServiceError("session_not_found")
        if session.result is not None:
            # Review Focus 2: idempotent, whatever this payload says.
            return EndSessionResult.from_json(session.result, already_closed=True)
        if session.status != "open":
            raise ServiceError("session_closed")  # replaced by a newer start_lesson
        profile = uow.profiles.get()
        if profile is None:
            raise ServiceError("onboarding_needed")
        checked = validate_evidence(
            evidence,
            session.chunks_offered,
            previous_cefr=uow.sessions.previous_cefr(),
            self_level=profile.self_level,
        )
        metrics = compute_metrics(
            evidence,
            checked,
            chunks_offered=len(session.chunks_offered),
            started_at=session.started_at,
            ended_at=now,
            recent_correct_norms=uow.sessions.recent_correct_norms(
                now - RECURRING_WINDOW, session_id
            ),
        )
        closed = checked.status == "closed"
        streak = current_streak(uow, now, user_zone(uow), closing_now=closed)
        result = EndSessionResult(
            status=checked.status,
            low_trust=checked.low_trust,
            metrics=metrics,
            summary_text=summary_text(metrics, streak),
            streak=streak,
            already_closed=False,
            errors_rejected=checked.errors_rejected,
            chunks_rejected=checked.chunks_rejected,
        )
        uow.sessions.close(
            session_id,
            status=checked.status,
            low_trust=checked.low_trust,
            ended_at=now,
            evidence=evidence,
            raw_evidence=raw_evidence,
            cefr_excluded=checked.cefr_excluded,
            result=result.to_json(),
        )
        uow.sessions.save_metrics(session_id, metrics)
        if closed:
            uow.sessions.save_errors(session_id, checked.errors)
            _mark_plan_item_done(uow, session)
        _upgrade_spontaneous_use(uow, session_id, evidence.user_turns)
        uow.audit.record(
            "session_closed",
            {
                "session_id": str(session_id),
                "status": checked.status,
                "low_trust": checked.low_trust,
            },
            now,
        )
        return result


def _mark_plan_item_done(uow: UnitOfWork, session: SessionRow) -> None:
    """Review Focus 4: the item may belong to a superseded plan version (the profile changed
    while the session was open). Then the active plan still holds a pending base item for the
    same track item; mark it done too, so start_lesson never offers that lesson again."""
    if session.plan_item_id is None:
        return
    uow.plans.mark_done(session.plan_item_id, session.id)
    active = uow.plans.active()
    if active is None or any(item.id == session.plan_item_id for item in active.items):
        return
    for item in active.items:
        if (
            item.track_item_id == session.track_item_id
            and item.variant == "base"
            and item.status == "pending"
        ):
            uow.plans.mark_done(item.id, session.id)


def _upgrade_spontaneous_use(uow: UnitOfWork, session_id: UUID, turns: Sequence[str]) -> None:
    """Rating 3 -> 4 for reviewed items reused in >= 2 turns (spec 10.3), replaying FSRS from
    the state before the review (ruling 7)."""
    rated_three = {
        log.item_id: log for log in uow.reviews.session_logs(session_id) if log.rating == 3
    }
    if not rated_three:
        return
    rows = uow.glossary.get_many(rated_three.keys())
    texts = {item_id: rows[item_id].text for item_id in rated_three if item_id in rows}
    for item_id in sorted(spontaneous_use(texts, turns), key=str):
        log = rated_three[item_id]
        uow.reviews.save_state(item_id, review(log.state_before, 4, log.reviewed_at))
        uow.reviews.set_log_rating(session_id, item_id, 4)
