"""Lesson composition: item choice, difficulty variant and due reviews (spec 9.1-9.3)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from tutor.domain.glossary import GlossaryKind
from tutor.domain.plan_lite import Variant
from tutor.domain.profile import UseCase
from tutor.domain.track import TrackItem

BriefVariant = Literal["base", "complication", "simpler"]
ReviewFormat = Literal["produce", "recall", "correct", "use"]
TaskResult = Literal["achieved", "partial", "not_achieved"]

MAX_DUE_REVIEWS = 8
MAX_DRILLED_REVIEWS = 4
STARTS_PER_DAY = 10
VARIANT_WINDOW = 3


@dataclass(frozen=True, slots=True)
class PendingPlanItem:
    plan_item_id: UUID
    week_no: int
    order_no: int
    track_item_id: str
    variant: Variant


@dataclass(frozen=True, slots=True)
class ItemChoice:
    plan_item_id: UUID | None
    track_item_id: str
    variant: Variant
    plan_exhausted: bool


@dataclass(frozen=True, slots=True)
class RecentResult:
    task_result: TaskResult
    hints_given: int


@dataclass(frozen=True, slots=True)
class DueCandidate:
    item_id: UUID
    kind: GlossaryKind
    leech: bool
    due: datetime
    last_ratings: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DueReview:
    item_id: UUID
    format: ReviewFormat


def _from_plan(p: PendingPlanItem) -> ItemChoice:
    return ItemChoice(
        plan_item_id=p.plan_item_id,
        track_item_id=p.track_item_id,
        variant=p.variant,
        plan_exhausted=False,
    )


def _prep_choice(
    ordered: Sequence[PendingPlanItem],
    track: Mapping[str, TrackItem],
    use_case: UseCase,
    last_done: Mapping[str, datetime],
) -> ItemChoice | None:
    for p in ordered:
        t = track.get(p.track_item_id)
        if t is not None and use_case in t.use_cases:
            return _from_plan(p)
    matching = [t for t in track.values() if use_case in t.use_cases]
    if not matching:
        return None
    # Least recently done: never-done items first (by order_no), then the oldest done.
    never = [t for t in matching if t.id not in last_done]
    if never:
        pick = min(never, key=lambda t: t.order_no)
    else:
        pick = min(matching, key=lambda t: (last_done[t.id], t.order_no))
    return ItemChoice(
        plan_item_id=None, track_item_id=pick.id, variant="base", plan_exhausted=False
    )


def choose_item(
    pending: Sequence[PendingPlanItem],
    track: Mapping[str, TrackItem],
    prep_use_case: UseCase | None,
    last_done: Mapping[str, datetime],
    last_plan_item: PendingPlanItem | None,
) -> ItemChoice:
    """Spec 9.1. Off-plan choices (prep fallback, exhausted plan) have no plan_item_id."""
    ordered = sorted(pending, key=lambda p: (p.week_no, p.order_no))
    if prep_use_case is not None:
        choice = _prep_choice(ordered, track, prep_use_case, last_done)
        if choice is not None:
            return choice
    if ordered:
        return _from_plan(ordered[0])
    if last_plan_item is None:
        raise ValueError("no pending plan item and no last plan item")
    return ItemChoice(
        plan_item_id=None,
        track_item_id=last_plan_item.track_item_id,
        variant="complication",
        plan_exhausted=True,
    )


def choose_variant(plan_variant: Variant, recent: Sequence[RecentResult]) -> BriefVariant:
    """Spec 9.2. `recent` holds closed sessions, newest first; only the first 3 count."""
    window = recent[:VARIANT_WINDOW]
    if sum(1 for r in window if r.task_result == "not_achieved") >= 2:
        return "simpler"
    if len(window) == VARIANT_WINDOW and all(
        r.task_result == "achieved" and r.hints_given <= 1 for r in window
    ):
        return "complication"
    return plan_variant


def review_format(kind: GlossaryKind, leech: bool, last_ratings: Sequence[int]) -> ReviewFormat:
    """Spec 9.3 format table, first matching row wins. `last_ratings` is newest last."""
    if leech and kind == "correction":
        return "correct"
    if len(last_ratings) >= 2 and all(r == 3 for r in last_ratings[-2:]):
        return "use"
    if kind in ("correction", "chunk"):
        return "produce"
    return "recall"


def _group(c: DueCandidate) -> int:
    if c.kind == "correction" or c.leech:
        return 0
    return 1 if c.kind == "chunk" else 2


def pick_due_reviews(
    candidates: Sequence[DueCandidate], now: datetime, limit: int = MAX_DUE_REVIEWS
) -> tuple[DueReview, ...]:
    """Due items by group (correction or leech, chunk, term), most overdue first, capped."""
    due = sorted(
        (c for c in candidates if c.due <= now),
        key=lambda c: (_group(c), c.due, str(c.item_id)),
    )
    return tuple(
        DueReview(item_id=c.item_id, format=review_format(c.kind, c.leech, c.last_ratings))
        for c in due[: max(limit, 0)]
    )
