"""Subscription banners and webhook state transitions (spec sections 7 and 11)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from tutor.domain.dashboard.types import (
    Banner,
    BannerKind,
    BillingEvent,
    BillingEventKind,
    FreeUsage,
    Subscription,
    SubStatus,
    Tier,
)

RENEWAL_NOTICE_DAYS = 14
LAPSED_NOTICE_DAYS = 30


def banners_for(sub: Subscription, usage: FreeUsage | None, today: date) -> tuple[Banner, ...]:
    """Banners to show in the shell, most urgent first."""
    out: list[Banner] = []
    if sub.status is SubStatus.PAST_DUE:
        out.append(Banner(BannerKind.PAYMENT_FAILED))
    elif (
        sub.status is SubStatus.ACTIVE
        and sub.period_end is not None
        and 0 <= (sub.period_end - today).days <= RENEWAL_NOTICE_DAYS
    ):
        out.append(Banner(BannerKind.RENEWAL_SOON, on=sub.period_end, amount_cents=sub.price_cents))
    elif (
        sub.status is SubStatus.LAPSED
        and sub.period_end is not None
        and (today - sub.period_end).days <= LAPSED_NOTICE_DAYS
    ):
        out.append(Banner(BannerKind.LAPSED))
    if sub.tier is Tier.FREE and usage is not None:
        if usage.sessions_this_week >= usage.session_cap:
            out.append(Banner(BannerKind.SESSION_LIMIT, on=usage.week_resets_on))
        if usage.glossary_items >= usage.glossary_cap:
            out.append(Banner(BannerKind.GLOSSARY_LIMIT))
    return tuple(out)


def next_subscription(current: Subscription, event: BillingEvent) -> Subscription:
    """Apply one verified webhook event. Events older than the last applied one are ignored,
    so retries and out-of-order delivery converge on the same state."""
    if current.last_event_at is not None and event.created < current.last_event_at:
        return current
    stamped = replace(current, last_event_at=event.created)
    period_end = event.period_end or current.period_end
    match event.kind:
        case BillingEventKind.CHECKOUT_COMPLETED if event.paid:
            return _active(stamped, event, period_end)
        case BillingEventKind.CHECKOUT_COMPLETED:
            return stamped  # card-only v1: an unpaid checkout grants nothing
        case BillingEventKind.INVOICE_PAID:
            return _active(stamped, event, period_end)
        case BillingEventKind.PAYMENT_FAILED:
            if current.tier is Tier.ANNUAL:
                return replace(stamped, status=SubStatus.PAST_DUE)
            return stamped
        case BillingEventKind.SUBSCRIPTION_DELETED:
            return replace(
                stamped,
                tier=Tier.FREE,
                status=SubStatus.LAPSED,
                period_end=period_end,
            )


def _active(sub: Subscription, event: BillingEvent, period_end: date | None) -> Subscription:
    return replace(
        sub,
        tier=Tier.ANNUAL,
        status=SubStatus.ACTIVE,
        period_end=period_end,
        subscription_ref=event.subscription_ref or sub.subscription_ref,
    )
