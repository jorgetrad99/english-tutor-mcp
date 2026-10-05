"""Profile use cases: get_profile, save_profile and the plan summary (spec 6.2-6.4)."""

from __future__ import annotations

from uuid import UUID
from zoneinfo import ZoneInfo

from tutor.domain.plan_lite import build_plan_lite, feasibility, horizon_weeks, rationale
from tutor.domain.profile import (
    DOMAINS,
    Profile,
    ProfileInput,
    plan_inputs_changed,
    validate_profile,
)
from tutor.domain.track import TrackItem
from tutor.services.context import Services, current_streak, local_date, user_zone
from tutor.services.errors import ServiceError
from tutor.services.ports import ActivePlan, PlanItemRow, UnitOfWork
from tutor.services.views import PlanItemView, PlanSummary, ProfileView, SaveProfileResult


def get_profile(svc: Services, user_id: UUID) -> ProfileView:
    """Read-only state for get_profile and the web pages (spec 8.1)."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        profile = uow.profiles.get()
        plan = uow.plans.active() if profile is not None else None
        summary = None
        if profile is not None and plan is not None:
            summary = plan_summary(uow, plan, profile)
        open_session = uow.sessions.open_session()
        return ProfileView(
            onboarding_needed=profile is None,
            profile=profile,
            plan=summary,
            streak=current_streak(uow, now, user_zone(uow)),
            open_session_id=open_session.id if open_session is not None else None,
            provisional_count=uow.glossary.count_provisional(),
            due_reviews_count=uow.glossary.count_due(now),
        )


def save_profile(svc: Services, user_id: UUID, raw: ProfileInput) -> SaveProfileResult:
    """Validate, upsert, regenerate the plan when its inputs changed, audit (spec 6.4)."""
    now = svc.clock()
    with svc.uow(user_id) as uow:
        current_tz = uow.users.timezone()
        requested_tz = (raw.timezone or "").strip()
        zone = requested_tz if requested_tz in svc.valid_timezones else current_tz
        today = local_date(now, ZoneInfo(zone))
        checked = validate_profile(
            raw, today, valid_timezones=svc.valid_timezones, current_timezone=current_tz
        )
        if isinstance(checked, tuple):
            fields = tuple(dict.fromkeys(error.field for error in checked))
            raise ServiceError("validation_failed", fields)
        profile = checked
        old = uow.profiles.get()
        plan = uow.plans.active()
        if plan is not None and old == profile:
            return SaveProfileResult(
                profile=profile, plan=plan_summary(uow, plan, profile), plan_changed=False
            )
        changed = plan is None or plan_inputs_changed(old, profile)
        uow.profiles.upsert(profile, now)
        uow.audit.record("profile_saved", {"plan_inputs_changed": changed}, now)
        if plan is None or changed:
            track = [item for domain in profile.domains for item in uow.track.items(domain)]
            built = build_plan_lite(profile, track, today, uow.plans.done_base_track_ids())
            plan = uow.plans.create(built.items, rationale(built.feasibility, profile), now)
            uow.audit.record(
                "plan_generated",
                {
                    "version": plan.version,
                    "sessions_planned": len(plan.items),
                    "reachable": built.feasibility.reachable,
                },
                now,
            )
        return SaveProfileResult(
            profile=profile, plan=plan_summary(uow, plan, profile), plan_changed=changed
        )


def plan_summary(uow: UnitOfWork, plan: ActivePlan, profile: Profile) -> PlanSummary:
    """Weeks, this week's items with status, the next item and the feasibility result."""
    track = _track_by_id(uow)
    ordered = sorted(plan.items, key=lambda i: (i.week_no, i.order_no))
    views = tuple(_item_view(item, track[item.track_item_id]) for item in ordered)
    next_item = next((v for v in views if v.status == "pending"), None)
    weeks = max(
        (v.week_no for v in views),
        default=horizon_weeks(profile.target_date, plan.generated_at.date()),
    )
    current_week = next_item.week_no if next_item is not None else weeks
    return PlanSummary(
        version=plan.version,
        current_week_no=current_week,
        week_items=tuple(v for v in views if v.week_no == current_week),
        next_item=next_item,
        feasibility=feasibility(profile, weeks),
        weeks=weeks,
        sessions_planned=len(views),
        sessions_done=sum(1 for v in views if v.status == "done"),
    )


def _track_by_id(uow: UnitOfWork) -> dict[str, TrackItem]:
    return {item.id: item for domain in DOMAINS for item in uow.track.items(domain)}


def _item_view(row: PlanItemRow, item: TrackItem) -> PlanItemView:
    return PlanItemView(
        plan_item_id=row.id,
        week_no=row.week_no,
        order_no=row.order_no,
        track_item_id=row.track_item_id,
        can_do_en=item.can_do_en,
        can_do_es=item.can_do_es,
        skill=item.skill,
        interaction_type=item.interaction_type,
        variant=row.variant,
        status=row.status,
    )
