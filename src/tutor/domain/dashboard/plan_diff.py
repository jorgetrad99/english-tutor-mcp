"""What changed between two plan versions (requirements section 9: the user sees the diff)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tutor.domain.dashboard.types import PlanItemView


@dataclass(frozen=True, slots=True)
class PlanDiff:
    added: tuple[PlanItemView, ...]
    removed: tuple[PlanItemView, ...]
    moved: tuple[tuple[PlanItemView, PlanItemView], ...]  # (before, after)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.moved)


def _pos(item: PlanItemView) -> tuple[int, int]:
    return (item.week_no, item.order_no)


def diff_plan(old: Sequence[PlanItemView], new: Sequence[PlanItemView]) -> PlanDiff:
    pool: dict[str, list[PlanItemView]] = {}
    for item in sorted(old, key=_pos):
        pool.setdefault(item.key, []).append(item)
    added: list[PlanItemView] = []
    moved: list[tuple[PlanItemView, PlanItemView]] = []
    for item in sorted(new, key=_pos):
        bucket = pool.get(item.key)
        if bucket:
            before = bucket.pop(0)
            if _pos(before) != _pos(item):
                moved.append((before, item))
        else:
            added.append(item)
    removed = sorted((i for bucket in pool.values() for i in bucket), key=_pos)
    return PlanDiff(tuple(added), tuple(removed), tuple(moved))
