"""get_profile and save_profile on the in-memory store (spec 6.2-6.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from repo_contract import add_closed_session, first_item, insert_glossary, present, start_session

from tutor.domain.plan_lite import DEFAULT_WEEKS, horizon_weeks
from tutor.services.context import Services
from tutor.services.errors import ServiceError
from tutor.services.memory import MemoryStore
from tutor.services.profile import get_profile, save_profile
from tutor.services.views import ProfileView

from .conftest import NEW_YORK, FixedClock, onboard, profile_input

pytestmark = pytest.mark.unit


def events(store: MemoryStore, user_id: UUID) -> list[str]:
    return [a.event for a in store.tables.audit if a.user_id == user_id]


def mark_first_done(svc: Services, user_id: UUID, now: datetime, count: int) -> None:
    with svc.uow(user_id) as uow:
        session = start_session(uow, first_item(uow), now)
        for item in present(uow.plans.active()).items[:count]:
            assert uow.plans.mark_done(item.id, session.id)


def test_get_profile_before_onboarding(svc: Services, user_id: UUID) -> None:
    assert get_profile(svc, user_id) == ProfileView(
        onboarding_needed=True,
        profile=None,
        plan=None,
        streak=0,
        open_session_id=None,
        provisional_count=0,
        due_reviews_count=0,
    )


def test_save_profile_creates_the_profile_and_a_plan(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    result = onboard(svc, user_id)
    assert result.plan_changed
    assert result.profile.days_per_week == 3
    assert set(result.profile.use_cases) == {"standup", "code_review"}
    plan = result.plan
    assert (plan.version, plan.weeks, plan.sessions_planned, plan.sessions_done) == (
        1,
        DEFAULT_WEEKS,
        3 * DEFAULT_WEEKS,
        0,
    )
    assert plan.feasibility.weeks == DEFAULT_WEEKS
    assert plan.current_week_no == 1
    assert [(v.week_no, v.order_no, v.status) for v in plan.week_items] == [
        (1, 1, "pending"),
        (1, 2, "pending"),
        (1, 3, "pending"),
    ]
    assert plan.next_item == plan.week_items[0]
    assert events(store, user_id) == ["profile_saved", "plan_generated"]
    view = get_profile(svc, user_id)
    assert not view.onboarding_needed
    assert (view.profile, view.plan) == (result.profile, plan)


def test_plan_items_carry_their_track_details(svc: Services, user_id: UUID) -> None:
    result = onboard(svc, user_id)
    with svc.uow(user_id) as uow:
        track = {t.id: t for t in uow.track.items("it")}
    for view in result.plan.week_items:
        item = track[view.track_item_id]
        assert (view.can_do_en, view.can_do_es, view.skill, view.interaction_type) == (
            item.can_do_en,
            item.can_do_es,
            item.skill,
            item.interaction_type,
        )


def test_invalid_answers_fail_with_field_names_and_write_nothing(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    with pytest.raises(ServiceError) as info:
        save_profile(svc, user_id, profile_input(use_cases=[], days_per_week=9))
    assert info.value.code == "validation_failed"
    assert {"use_cases", "days_per_week"} <= set(info.value.fields)
    assert get_profile(svc, user_id).onboarding_needed
    assert events(store, user_id) == []


def test_identical_save_is_a_no_op(svc: Services, store: MemoryStore, user_id: UUID) -> None:
    first = onboard(svc, user_id)
    again = onboard(svc, user_id)
    assert not again.plan_changed
    assert (again.profile, again.plan) == (first.profile, first.plan)
    assert events(store, user_id) == ["profile_saved", "plan_generated"]


def test_goal_text_change_keeps_the_plan(svc: Services, store: MemoryStore, user_id: UUID) -> None:
    onboard(svc, user_id)
    result = onboard(svc, user_id, goal_text="Lead the incident review")
    assert not result.plan_changed
    assert result.plan.version == 1
    assert result.profile.goal_text == "Lead the incident review"
    assert events(store, user_id) == ["profile_saved", "plan_generated", "profile_saved"]


def test_changed_plan_inputs_make_a_new_plan_version(
    svc: Services, store: MemoryStore, user_id: UUID
) -> None:
    onboard(svc, user_id)
    result = onboard(svc, user_id, days_per_week=5)
    assert result.plan_changed
    assert (result.plan.version, result.plan.sessions_planned) == (2, 5 * DEFAULT_WEEKS)
    assert [v.order_no for v in result.plan.week_items] == [1, 2, 3, 4, 5]
    with svc.uow(user_id) as uow:
        assert present(uow.plans.active()).version == 2
    assert events(store, user_id) == [
        "profile_saved",
        "plan_generated",
        "profile_saved",
        "plan_generated",
    ]


def test_new_plan_skips_track_items_done_in_their_base_variant(
    svc: Services, user_id: UUID, now: datetime
) -> None:
    first = onboard(svc, user_id)
    done = present(first.plan.next_item)
    mark_first_done(svc, user_id, now, 1)
    onboard(svc, user_id, days_per_week=4)
    with svc.uow(user_id) as uow:
        plan = present(uow.plans.active())
    assert (done.track_item_id, "base") not in {(i.track_item_id, i.variant) for i in plan.items}


def test_web_form_timezone_is_saved_on_the_user(svc: Services, user_id: UUID) -> None:
    result = onboard(svc, user_id, timezone=NEW_YORK)
    assert result.profile.timezone == NEW_YORK
    with svc.uow(user_id) as uow:
        assert uow.users.timezone() == NEW_YORK


def test_unknown_timezone_is_a_validation_error(svc: Services, user_id: UUID) -> None:
    with pytest.raises(ServiceError) as info:
        onboard(svc, user_id, timezone="Mars/Olympus_Mons")
    assert info.value.code == "validation_failed"
    assert "timezone" in info.value.fields


def test_target_date_is_checked_against_the_local_date(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    clock.now = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)  # Oct 14, 21:00 in Mexico City
    result = onboard(svc, user_id, target_date=date(2026, 11, 11))  # 28 days after Oct 14
    assert result.plan.weeks == horizon_weeks(date(2026, 11, 11), date(2026, 10, 14))


def test_plan_summary_moves_to_the_week_of_the_next_pending_item(
    svc: Services, user_id: UUID, now: datetime
) -> None:
    onboard(svc, user_id)
    mark_first_done(svc, user_id, now, 3)
    summary = present(get_profile(svc, user_id).plan)
    assert (summary.current_week_no, summary.sessions_done) == (2, 3)
    assert (present(summary.next_item).week_no, present(summary.next_item).order_no) == (2, 1)
    assert [v.week_no for v in summary.week_items] == [2, 2, 2]


def test_plan_summary_shows_the_last_week_when_everything_is_done(
    svc: Services, user_id: UUID, now: datetime
) -> None:
    onboard(svc, user_id)
    mark_first_done(svc, user_id, now, 3 * DEFAULT_WEEKS)
    summary = present(get_profile(svc, user_id).plan)
    assert summary.next_item is None
    assert (summary.current_week_no, summary.sessions_done) == (
        DEFAULT_WEEKS,
        3 * DEFAULT_WEEKS,
    )
    assert [v.status for v in summary.week_items] == ["done", "done", "done"]


def test_get_profile_reports_the_open_session_and_glossary_counts(
    svc: Services, user_id: UUID, now: datetime
) -> None:
    onboard(svc, user_id)
    with svc.uow(user_id) as uow:
        session = start_session(uow, first_item(uow), now)
        insert_glossary(uow, session.id, "circle back", now, status="provisional")
        insert_glossary(uow, session.id, "keep you posted", now, status="provisional")
        insert_glossary(uow, session.id, "roll back the deploy", now, first_due=now)
        insert_glossary(
            uow, session.id, "blameless postmortem", now, first_due=now + timedelta(days=1)
        )
    view = get_profile(svc, user_id)
    assert (view.open_session_id, view.provisional_count, view.due_reviews_count) == (
        session.id,
        2,
        1,
    )


def test_get_profile_streak_uses_the_learner_timezone(svc: Services, user_id: UUID) -> None:
    onboard(svc, user_id)
    with svc.uow(user_id) as uow:
        item = first_item(uow)
        add_closed_session(  # Mexico City: Oct 13, 23:00; New York: Oct 14, 01:00
            uow,
            item,
            datetime(2026, 10, 14, 4, 40, tzinfo=UTC),
            datetime(2026, 10, 14, 5, 0, tzinfo=UTC),
        )
        add_closed_session(  # Oct 14 in both zones
            uow,
            item,
            datetime(2026, 10, 14, 13, 40, tzinfo=UTC),
            datetime(2026, 10, 14, 14, 0, tzinfo=UTC),
        )
    assert get_profile(svc, user_id).streak == 2
    with svc.uow(user_id) as uow:
        uow.users.set_timezone(NEW_YORK)
    assert get_profile(svc, user_id).streak == 1


def test_target_date_window_follows_the_timezone_sent_in_the_request(
    svc: Services, clock: FixedClock, user_id: UUID
) -> None:
    clock.now = datetime(
        2026, 10, 15, 3, 0, tzinfo=UTC
    )  # Oct 14, 21:00 Mexico; Oct 15, 05:00 Madrid
    target = date(2026, 11, 12)  # 29 days after Oct 14, 28 days after Oct 15
    result = onboard(svc, user_id, target_date=target, timezone="Europe/Madrid")
    assert result.plan.weeks == horizon_weeks(target, date(2026, 10, 15)) == 4
    assert horizon_weeks(target, date(2026, 10, 14)) == 5
