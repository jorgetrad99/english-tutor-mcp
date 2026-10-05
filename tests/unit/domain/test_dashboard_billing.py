from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

from tutor.domain.dashboard.billing import banners_for, next_subscription
from tutor.domain.dashboard.types import (
    BannerKind,
    BillingEvent,
    BillingEventKind,
    FreeUsage,
    Subscription,
    SubStatus,
    Tier,
)

TODAY = date(2027, 1, 12)
FREE = Subscription(tier=Tier.FREE, status=SubStatus.NONE, price_id="price_29", price_cents=2900)
ANNUAL = replace(
    FREE,
    tier=Tier.ANNUAL,
    status=SubStatus.ACTIVE,
    period_end=date(2028, 1, 12),
    subscription_ref="sub_1",
    last_event_at=datetime(2027, 1, 12, 10, tzinfo=UTC),
)


def usage(sessions: int = 0, glossary: int = 0) -> FreeUsage:
    return FreeUsage(sessions, 3, glossary, 50, date(2027, 1, 18))


def event(kind: BillingEventKind, minute: int = 0, **kw: object) -> BillingEvent:
    base: dict[str, object] = {
        "event_id": f"evt_{kind}_{minute}",
        "kind": kind,
        "created": datetime(2027, 1, 12, 11, minute, tzinfo=UTC),
        "customer_id": "cus_1",
        "subscription_ref": "sub_1",
        "user_id": None,
        "paid": True,
        "period_end": date(2028, 1, 12),
    }
    base.update(kw)
    return BillingEvent(**base)  # type: ignore[arg-type]


def kinds(banners: tuple[object, ...]) -> list[BannerKind]:
    return [b.kind for b in banners]  # type: ignore[attr-defined]


def test_free_under_limits_has_no_banner() -> None:
    assert banners_for(FREE, usage(2, 49), TODAY) == ()


def test_free_at_limits_shows_both_limit_banners_with_reset_date() -> None:
    banners = banners_for(FREE, usage(3, 50), TODAY)
    assert kinds(banners) == [BannerKind.SESSION_LIMIT, BannerKind.GLOSSARY_LIMIT]
    assert banners[0].on == date(2027, 1, 18)


def test_annual_ignores_usage() -> None:
    assert banners_for(ANNUAL, usage(9, 900), TODAY) == ()


def test_renewal_banner_inside_fourteen_days_only() -> None:
    soon = replace(ANNUAL, period_end=date(2027, 1, 26))
    later = replace(ANNUAL, period_end=date(2027, 1, 27))
    assert kinds(banners_for(soon, None, TODAY)) == [BannerKind.RENEWAL_SOON]
    assert banners_for(soon, None, TODAY)[0].amount_cents == 2900
    assert banners_for(later, None, TODAY) == ()


def test_past_due_and_lapsed_banners() -> None:
    assert kinds(banners_for(replace(ANNUAL, status=SubStatus.PAST_DUE), None, TODAY)) == [
        BannerKind.PAYMENT_FAILED
    ]
    lapsed = replace(FREE, status=SubStatus.LAPSED, period_end=date(2026, 12, 20))
    assert kinds(banners_for(lapsed, usage(), TODAY)) == [BannerKind.LAPSED]
    old_lapse = replace(lapsed, period_end=date(2026, 12, 1))
    assert banners_for(old_lapse, usage(), TODAY) == ()


def test_paid_checkout_activates_annual() -> None:
    after = next_subscription(FREE, event(BillingEventKind.CHECKOUT_COMPLETED, user_id=uuid4()))
    assert (after.tier, after.status, after.period_end) == (
        Tier.ANNUAL,
        SubStatus.ACTIVE,
        date(2028, 1, 12),
    )
    assert after.subscription_ref == "sub_1"


def test_unpaid_checkout_changes_nothing_but_the_event_stamp() -> None:
    after = next_subscription(FREE, event(BillingEventKind.CHECKOUT_COMPLETED, paid=False))
    assert (after.tier, after.status) == (Tier.FREE, SubStatus.NONE)


def test_invoice_paid_activates_and_sets_period_end() -> None:
    after = next_subscription(FREE, event(BillingEventKind.INVOICE_PAID))
    assert (after.tier, after.status, after.period_end) == (
        Tier.ANNUAL,
        SubStatus.ACTIVE,
        date(2028, 1, 12),
    )


def test_payment_failed_marks_annual_past_due_but_not_free() -> None:
    assert next_subscription(ANNUAL, event(BillingEventKind.PAYMENT_FAILED, 1)).status == (
        SubStatus.PAST_DUE
    )
    assert next_subscription(FREE, event(BillingEventKind.PAYMENT_FAILED)) == replace(
        FREE, last_event_at=datetime(2027, 1, 12, 11, 0, tzinfo=UTC)
    )


def test_deleted_returns_to_free_lapsed() -> None:
    after = next_subscription(ANNUAL, event(BillingEventKind.SUBSCRIPTION_DELETED, 2))
    assert (after.tier, after.status) == (Tier.FREE, SubStatus.LAPSED)


def test_older_event_never_overrides_newer_state() -> None:
    lapsed = next_subscription(ANNUAL, event(BillingEventKind.SUBSCRIPTION_DELETED, 30))
    stale = event(BillingEventKind.INVOICE_PAID, 5)
    assert next_subscription(lapsed, stale) == lapsed


def test_unpaid_checkout_never_downgrades_active() -> None:
    late = event(BillingEventKind.CHECKOUT_COMPLETED, 40, paid=False)
    assert next_subscription(ANNUAL, late).status == SubStatus.ACTIVE
