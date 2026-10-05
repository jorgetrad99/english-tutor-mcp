import pytest

from tutor.domain.dashboard.plan_diff import diff_plan
from tutor.domain.dashboard.types import ItemStatus, PlanItemView, Skill

pytestmark = pytest.mark.unit


def item(key: str, week: int, order: int, status: ItemStatus = ItemStatus.PENDING) -> PlanItemView:
    return PlanItemView(week, order, key, key.title(), Skill.SPEAKING, "it", "hint", status)


def test_identical_plans_have_empty_diff() -> None:
    plan = [item("a", 1, 1), item("b", 1, 2)]
    assert diff_plan(plan, plan).empty


def test_added_removed_and_moved() -> None:
    old = [item("a", 1, 1), item("b", 1, 2), item("c", 2, 1)]
    new = [item("a", 1, 1), item("c", 1, 2), item("d", 2, 1)]
    diff = diff_plan(old, new)
    assert [i.key for i in diff.added] == ["d"]
    assert [i.key for i in diff.removed] == ["b"]
    assert [(b.week_no, a.week_no) for b, a in diff.moved] == [(2, 1)]


def test_status_change_alone_is_not_a_change() -> None:
    old = [item("a", 1, 1)]
    new = [item("a", 1, 1, ItemStatus.DONE)]
    assert diff_plan(old, new).empty


def test_repeated_keys_are_matched_in_order() -> None:
    old = [item("a", 1, 1), item("a", 2, 1)]
    new = [item("a", 1, 1), item("a", 2, 1), item("a", 3, 1)]
    diff = diff_plan(old, new)
    assert [(i.week_no) for i in diff.added] == [3]
    assert diff.removed == () and diff.moved == ()
