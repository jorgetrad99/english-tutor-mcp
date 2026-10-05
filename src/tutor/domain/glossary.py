"""Glossary save rules, spontaneous use and the local-morning due time (spec 10.2, 10.3)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from tutor.domain.profile import Domain
from tutor.domain.text import normalize

GlossaryKind = Literal["correction", "chunk", "term"]
GlossaryStatus = Literal["provisional", "confirmed", "declined", "archived"]
SaveStatus = Literal["confirmed", "provisional", "declined"]
RejectReason = Literal[
    "empty", "too_long", "missing_context", "duplicate_in_call", "already_confirmed"
]

PROVISIONAL_DAYS = 7
DECLINED_RETENTION_DAYS = 30
LEECH_WINDOW_DAYS = 30
LEECH_SEEN_COUNT = 3
TEXT_MAX = 120
MEANING_MAX = 200
CONTEXT_MAX = 300


@dataclass(frozen=True, slots=True)
class IncomingItem:
    kind: GlossaryKind
    text: str
    meaning: str
    context_sentence: str
    domain: Domain


@dataclass(frozen=True, slots=True)
class ExistingItem:
    id: UUID
    kind: GlossaryKind
    status: GlossaryStatus
    seen_count: int
    leech: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class InsertItem:
    index: int
    item: IncomingItem
    text_norm: str
    status: SaveStatus
    provisional_expires_at: datetime | None
    first_due: datetime | None


@dataclass(frozen=True, slots=True)
class Reinforce:
    index: int
    item_id: UUID
    kind: GlossaryKind
    seen_count: int
    leech: bool
    due: datetime


@dataclass(frozen=True, slots=True)
class Promote:
    index: int
    item_id: UUID
    first_due: datetime


@dataclass(frozen=True, slots=True)
class SetStatus:
    index: int
    item_id: UUID
    status: Literal["provisional", "declined"]
    provisional_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class Reject:
    index: int
    reason: RejectReason


GlossaryAction = InsertItem | Reinforce | Promote | SetStatus | Reject


def local_morning(now: datetime, tz: ZoneInfo, *, days_ahead: int = 1, hour: int = 4) -> datetime:
    """UTC instant of `hour`:00 local time on the local date of `now` plus `days_ahead`."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_date = now.astimezone(tz).date() + timedelta(days=days_ahead)
    return datetime.combine(local_date, time(hour), tzinfo=tz).astimezone(UTC)


def _check(item: IncomingItem, text_norm: str) -> RejectReason | None:
    if not text_norm or not item.meaning.strip():
        return "empty"
    if (
        len(item.text) > TEXT_MAX
        or len(item.meaning) > MEANING_MAX
        or len(item.context_sentence) > CONTEXT_MAX
    ):
        return "too_long"
    if not item.context_sentence.strip():
        return "missing_context"
    return None


def _expiry(status: SaveStatus, now: datetime) -> datetime | None:
    return now + timedelta(days=PROVISIONAL_DAYS) if status == "provisional" else None


def _reinforce(
    index: int, item: IncomingItem, old: ExistingItem, now: datetime, tz: ZoneInfo
) -> Reinforce:
    seen = old.seen_count + 1
    recent = now - old.created_at <= timedelta(days=LEECH_WINDOW_DAYS)
    return Reinforce(
        index=index,
        item_id=old.id,
        kind="correction" if item.kind == "correction" else old.kind,
        seen_count=seen,
        leech=old.leech or (seen >= LEECH_SEEN_COUNT and recent),
        due=local_morning(now, tz),
    )


def _plan_one(
    index: int,
    item: IncomingItem,
    text_norm: str,
    status: SaveStatus,
    old: ExistingItem | None,
    now: datetime,
    tz: ZoneInfo,
) -> GlossaryAction:
    if old is None:
        return InsertItem(
            index=index,
            item=item,
            text_norm=text_norm,
            status=status,
            provisional_expires_at=_expiry(status, now),
            first_due=local_morning(now, tz) if status == "confirmed" else None,
        )
    if old.status in ("confirmed", "archived"):  # ruling 9: archived behaves like confirmed
        if status == "confirmed":
            return _reinforce(index, item, old, now, tz)
        return Reject(index=index, reason="already_confirmed")
    if status == "confirmed":
        return Promote(index=index, item_id=old.id, first_due=local_morning(now, tz))
    return SetStatus(
        index=index,
        item_id=old.id,
        status=status,
        provisional_expires_at=_expiry(status, now),
    )


def plan_glossary_save(
    items: Sequence[IncomingItem],
    status: SaveStatus,
    existing: Mapping[str, ExistingItem],
    now: datetime,
    tz: ZoneInfo,
) -> tuple[GlossaryAction, ...]:
    """One action per input index. `existing` is keyed by `text_norm`."""
    actions: list[GlossaryAction] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        text_norm = normalize(item.text)
        reason = _check(item, text_norm)
        if reason is None and text_norm in seen:
            reason = "duplicate_in_call"
        if reason is not None:
            actions.append(Reject(index=index, reason=reason))
            continue
        seen.add(text_norm)
        actions.append(_plan_one(index, item, text_norm, status, existing.get(text_norm), now, tz))
    return tuple(actions)


def spontaneous_use(texts: Mapping[UUID, str], turns: Sequence[str]) -> frozenset[UUID]:
    """Ids whose normalized text appears, as whole words, in >= 2 distinct normalized turns."""
    padded = {f" {t} " for t in (normalize(turn) for turn in turns) if t}
    used: set[UUID] = set()
    for item_id, text in texts.items():
        needle = normalize(text)
        if needle and sum(1 for turn in padded if f" {needle} " in turn) >= 2:
            used.add(item_id)
    return frozenset(used)
