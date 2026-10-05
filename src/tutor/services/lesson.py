"""Lesson use cases: start_lesson and record_review (spec sections 8.1, 9 and 10.3)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from tutor.domain.fsrs import Rating, new_state, review
from tutor.domain.lesson import (
    STARTS_PER_DAY,
    PendingPlanItem,
    choose_item,
    choose_variant,
    pick_due_reviews,
)
from tutor.domain.profile import USE_CASES
from tutor.services.context import Services, local_date, local_midnight, user_zone
from tutor.services.errors import ServiceError
from tutor.services.ports import OpenSessionExists, PlanItemRow, UnitOfWork
from tutor.services.views import (
    DueReviewView,
    LessonStart,
    ProvisionalView,
    ReviewResultView,
    StartLessonRequest,
)

MAX_PREP_CHARS = 300
MIN_MINUTES = 10
MAX_MINUTES = 30
MAX_PROVISIONAL = 8
MAX_REVIEW_RESULTS = 8
RECENT_RESULTS = 3
RATINGS = (1, 2, 3, 4)


def start_lesson(svc: Services, user_id: UUID, req: StartLessonRequest) -> LessonStart:
    """Housekeeping, rate limit, item and variant choice, session insert: one unit of work."""
    prep = _checked_prep(req)
    try:
        return _start_once(svc, user_id, req, prep)
    except OpenSessionExists:
        # Review Focus 5: a concurrent start won the one-open-session index and the first
        # unit of work rolled back. Retry once: housekeeping now closes the winner as
        # incomplete by the normal rule. A second loss is reported, never a 500.
        try:
            return _start_once(svc, user_id, req, prep)
        except OpenSessionExists as exc:
            raise ServiceError("rate_limited") from exc


def record_review(
    svc: Services, user_id: UUID, session_id: UUID, results: Sequence[tuple[UUID, Rating]]
) -> tuple[ReviewResultView, ...]:
    """FSRS reviews inside an open lesson; idempotent per (session, item)."""
    _check_results(results)
    now = svc.clock()
    with svc.uow(user_id) as uow:
        session = uow.sessions.get(session_id)
        if session is None:
            raise ServiceError("session_not_found")
        if session.status != "open":
            raise ServiceError("session_closed")
        rows = uow.glossary.get_many([item_id for item_id, _ in results])
        bad = tuple(
            f"results[{i}].item_id"
            for i, (item_id, _) in enumerate(results)
            if item_id not in rows or rows[item_id].status != "confirmed"
        )
        if bad:
            raise ServiceError("validation_failed", bad)
        tz = user_zone(uow)
        views: list[ReviewResultView] = []
        for item_id, rating in results:
            before = uow.reviews.state(item_id)
            if before is None:
                before = new_state(now)
            if uow.reviews.log(session_id, item_id, rating, now, before):
                after = review(before, rating, now)
                uow.reviews.save_state(item_id, after)
                views.append(ReviewResultView(item_id, local_date(after.due, tz), "recorded"))
            else:
                views.append(
                    ReviewResultView(item_id, local_date(before.due, tz), "already_recorded")
                )
        return tuple(views)


def _checked_prep(req: StartLessonRequest) -> str | None:
    """Validate the request; return the stripped prep text or None."""
    prep = req.prep.strip() if req.prep is not None else None
    if not prep:
        prep = None
    fields: list[str] = []
    if prep is not None and len(prep) > MAX_PREP_CHARS:
        fields.append("prep")
    if prep is None and req.prep_use_case is not None:
        fields.append("prep")
    if prep is not None and req.prep_use_case is None:
        fields.append("prep_use_case")
    if req.prep_use_case is not None and req.prep_use_case not in USE_CASES:
        fields.append("prep_use_case")
    if req.minutes is not None and not MIN_MINUTES <= req.minutes <= MAX_MINUTES:
        fields.append("minutes")
    if fields:
        raise ServiceError("validation_failed", tuple(dict.fromkeys(fields)))
    return prep


def _start_once(
    svc: Services, user_id: UUID, req: StartLessonRequest, prep: str | None
) -> LessonStart:
    now = svc.clock()
    with svc.uow(user_id) as uow:
        profile = uow.profiles.get()
        plan = uow.plans.active()
        if profile is None or plan is None or not plan.items:
            raise ServiceError("onboarding_needed")
        # Housekeeping (spec 9.4): replace the open session, purge, then the daily cap.
        replaced = uow.sessions.open_session()
        if replaced is not None:
            uow.sessions.mark_incomplete(replaced.id, now)
        uow.glossary.purge(now)
        if uow.sessions.count_started_since(local_midnight(now, user_zone(uow))) >= (
            STARTS_PER_DAY
        ):
            raise ServiceError("rate_limited")
        track = {item.id: item for item in uow.track.items(req.domain)}
        ordered = sorted(plan.items, key=lambda i: (i.week_no, i.order_no))
        choice = choose_item(
            tuple(_pending(i) for i in ordered if i.status == "pending"),
            track,
            req.prep_use_case if prep is not None else None,
            uow.sessions.last_done_by_track(),
            _pending(ordered[-1]),
        )
        variant = choose_variant(choice.variant, uow.sessions.recent_results(RECENT_RESULTS))
        item = track[choice.track_item_id]
        session = uow.sessions.create(
            plan_item_id=choice.plan_item_id,
            track_item_id=item.id,
            prep_text=prep,
            mode=req.mode,
            client=req.client,
            brief_variant=variant,
            chunks_offered=[chunk.id for chunk in item.chunks],
            now=now,
        )
        provisional = tuple(
            ProvisionalView(item_id=row.id, kind=row.kind, text=row.text, meaning=row.meaning)
            for row in uow.glossary.provisional(MAX_PROVISIONAL)
        )
        return LessonStart(
            session_id=session.id,
            mode=req.mode,
            item=item,
            variant=variant,
            prep_text=prep,
            due_reviews=_due_reviews(uow, now),
            provisional_items=provisional,
            plan_exhausted=choice.plan_exhausted,
            replaced_session=replaced is not None,
        )


def _pending(row: PlanItemRow) -> PendingPlanItem:
    return PendingPlanItem(
        plan_item_id=row.id,
        week_no=row.week_no,
        order_no=row.order_no,
        track_item_id=row.track_item_id,
        variant=row.variant,
    )


def _due_reviews(uow: UnitOfWork, now: datetime) -> tuple[DueReviewView, ...]:
    picks = pick_due_reviews(uow.glossary.due_candidates(now), now)
    rows = uow.glossary.get_many([pick.item_id for pick in picks])
    return tuple(
        DueReviewView(
            item_id=pick.item_id,
            kind=rows[pick.item_id].kind,
            text=rows[pick.item_id].text,
            meaning=rows[pick.item_id].meaning,
            context_sentence=rows[pick.item_id].context_sentence,
            format=pick.format,
        )
        for pick in picks
        if pick.item_id in rows
    )


def _check_results(results: Sequence[tuple[UUID, Rating]]) -> None:
    if not 1 <= len(results) <= MAX_REVIEW_RESULTS:
        raise ServiceError("validation_failed", ("results",))
    fields: list[str] = []
    seen: set[UUID] = set()
    for i, (item_id, rating) in enumerate(results):
        if item_id in seen:
            fields.append(f"results[{i}].item_id")
        seen.add(item_id)
        if rating not in RATINGS:
            fields.append(f"results[{i}].rating")
    if fields:
        raise ServiceError("validation_failed", tuple(fields))
