from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tutor.domain.glossary import (
    ExistingItem,
    GlossaryKind,
    GlossaryStatus,
    IncomingItem,
    InsertItem,
    Promote,
    Reinforce,
    Reject,
    RejectReason,
    SaveStatus,
    SetStatus,
    local_morning,
    plan_glossary_save,
    spontaneous_use,
)

pytestmark = pytest.mark.unit

MX = ZoneInfo("America/Mexico_City")
NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # 09:00 in Mexico City
TOMORROW_4AM_MX = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
ID_A = UUID("00000000-0000-0000-0000-00000000000a")
ID_B = UUID("00000000-0000-0000-0000-00000000000b")


def item(
    text: str = "push back on",
    *,
    kind: GlossaryKind = "chunk",
    meaning: str = "resist a request",
    context: str = "I had to push back on the deadline.",
) -> IncomingItem:
    return IncomingItem(
        kind=kind, text=text, meaning=meaning, context_sentence=context, domain="it"
    )


def existing(
    status: GlossaryStatus,
    *,
    kind: GlossaryKind = "chunk",
    seen_count: int = 1,
    leech: bool = False,
    age_days: int = 3,
) -> ExistingItem:
    return ExistingItem(
        id=ID_A,
        kind=kind,
        status=status,
        seen_count=seen_count,
        leech=leech,
        created_at=NOW - timedelta(days=age_days),
    )


def plan_one(incoming: IncomingItem, status: SaveStatus, old: ExistingItem | None = None) -> object:
    known = {} if old is None else {"push back on": old}
    (action,) = plan_glossary_save([incoming], status, known, NOW, MX)
    return action


# --- local_morning ---------------------------------------------------------------


def test_local_morning_default_is_tomorrow_4am_local() -> None:
    assert local_morning(NOW, MX) == TOMORROW_4AM_MX


def test_local_morning_days_ahead_and_hour() -> None:
    assert local_morning(NOW, MX, days_ahead=0, hour=9) == datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
    assert local_morning(NOW, MX, days_ahead=3) == datetime(2026, 10, 17, 10, 0, tzinfo=UTC)


def test_local_morning_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        local_morning(datetime(2026, 10, 14, 15, 0), MX)


@pytest.mark.parametrize(
    ("now", "tz", "expected"),
    [
        # Review Focus 3: Mexico City has no DST since 2022; always UTC-6.
        (datetime(2026, 3, 7, 18, 0, tzinfo=UTC), MX, datetime(2026, 3, 8, 10, 0, tzinfo=UTC)),
        (datetime(2026, 3, 8, 12, 0, tzinfo=UTC), MX, datetime(2026, 3, 9, 10, 0, tzinfo=UTC)),
        (datetime(2026, 4, 4, 12, 0, tzinfo=UTC), MX, datetime(2026, 4, 5, 10, 0, tzinfo=UTC)),
        (datetime(2026, 10, 31, 18, 0, tzinfo=UTC), MX, datetime(2026, 11, 1, 10, 0, tzinfo=UTC)),
        # 23:30 local on Oct 31 (05:30 UTC Nov 1) is still "today" = Oct 31.
        (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), MX, datetime(2026, 11, 1, 10, 0, tzinfo=UTC)),
        # 00:30 local on Nov 1 rolls to Nov 2.
        (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), MX, datetime(2026, 11, 2, 10, 0, tzinfo=UTC)),
        # Review Focus 3: New York, spring forward on 2026-03-08 02:00 EST -> 03:00 EDT.
        (datetime(2026, 3, 6, 15, 0, tzinfo=UTC), NY, datetime(2026, 3, 7, 9, 0, tzinfo=UTC)),
        (datetime(2026, 3, 7, 15, 0, tzinfo=UTC), NY, datetime(2026, 3, 8, 8, 0, tzinfo=UTC)),
        (datetime(2026, 3, 8, 15, 0, tzinfo=UTC), NY, datetime(2026, 3, 9, 8, 0, tzinfo=UTC)),
        # Review Focus 3: New York, fall back on 2026-11-01 02:00 EDT -> 01:00 EST.
        (datetime(2026, 10, 31, 15, 0, tzinfo=UTC), NY, datetime(2026, 11, 1, 9, 0, tzinfo=UTC)),
        # 23:30 EDT on Oct 31 (03:30 UTC Nov 1) is still Oct 31 locally.
        (datetime(2026, 11, 1, 3, 30, tzinfo=UTC), NY, datetime(2026, 11, 1, 9, 0, tzinfo=UTC)),
        # 00:30 EDT on Nov 1 (04:30 UTC) is Nov 1 locally.
        (datetime(2026, 11, 1, 4, 30, tzinfo=UTC), NY, datetime(2026, 11, 2, 9, 0, tzinfo=UTC)),
        # The repeated 01:30 hour (EST this time, 06:30 UTC) is still Nov 1.
        (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), NY, datetime(2026, 11, 2, 9, 0, tzinfo=UTC)),
    ],
)
def test_local_morning_across_dst(now: datetime, tz: ZoneInfo, expected: datetime) -> None:
    # Review Focus 3
    result = local_morning(now, tz)
    assert result == expected
    assert result.tzinfo is UTC
    assert result.astimezone(tz).hour == 4


@given(
    st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2035, 12, 31),
        timezones=st.just(UTC),
    ),
    st.sampled_from([MX, NY]),
)
def test_local_morning_is_always_4am_on_the_next_local_day(now: datetime, tz: ZoneInfo) -> None:
    due = local_morning(now, tz).astimezone(tz)
    assert due.hour == 4
    assert due.minute == 0
    assert due.date() == now.astimezone(tz).date() + timedelta(days=1)


# --- plan_glossary_save: rejections ----------------------------------------------


@pytest.mark.parametrize(
    ("incoming", "reason"),
    [
        (item("   "), "empty"),
        (item("?!…"), "empty"),
        (item("push back on", meaning="  "), "empty"),
        (item("x" * 121), "too_long"),
        (item("push back on", meaning="m" * 201), "too_long"),
        (item("push back on", context="c" * 301), "too_long"),
        (item("push back on", context="   "), "missing_context"),
    ],
)
def test_invalid_items_are_rejected(incoming: IncomingItem, reason: RejectReason) -> None:
    assert plan_one(incoming, "confirmed") == Reject(index=0, reason=reason)


def test_length_caps_are_inclusive() -> None:
    incoming = item("x" * 120, meaning="m" * 200, context="c" * 300)
    assert isinstance(plan_one(incoming, "confirmed"), InsertItem)


def test_duplicate_in_call_keeps_the_first_valid_one() -> None:
    items = [item("   "), item("Push back on"), item("push  back on!"), item("ship it")]
    actions = plan_glossary_save(items, "confirmed", {}, NOW, MX)
    assert [type(a).__name__ for a in actions] == [
        "Reject",
        "InsertItem",
        "Reject",
        "InsertItem",
    ]
    assert actions[0] == Reject(index=0, reason="empty")
    assert actions[2] == Reject(index=2, reason="duplicate_in_call")
    assert [a.index for a in actions] == [0, 1, 2, 3]


def test_curly_apostrophe_matches_existing_plain_text() -> None:
    # Review Focus 1: a curly apostrophe matches the same entry typed plainly.
    old = existing("confirmed")
    known = {"i'm on it": old}
    (action,) = plan_glossary_save(
        [item("I\N{RIGHT SINGLE QUOTATION MARK}m on it")], "confirmed", known, NOW, MX
    )
    assert isinstance(action, Reinforce)
    assert action.item_id == ID_A


# --- plan_glossary_save: new items -----------------------------------------------


def test_new_confirmed_item_is_inserted_and_due_tomorrow_morning() -> None:
    incoming = item("Push back on")
    assert plan_one(incoming, "confirmed") == InsertItem(
        index=0,
        item=incoming,
        text_norm="push back on",
        status="confirmed",
        provisional_expires_at=None,
        first_due=TOMORROW_4AM_MX,
    )


def test_new_provisional_item_expires_in_seven_days_and_is_not_scheduled() -> None:
    action = plan_one(item(), "provisional")
    assert isinstance(action, InsertItem)
    assert action.status == "provisional"
    assert action.provisional_expires_at == NOW + timedelta(days=7)
    assert action.first_due is None


def test_new_declined_item_is_stored_unscheduled() -> None:
    action = plan_one(item(), "declined")
    assert isinstance(action, InsertItem)
    assert action.status == "declined"
    assert action.provisional_expires_at is None
    assert action.first_due is None


# --- plan_glossary_save: existing items ------------------------------------------


def test_confirmed_again_reinforces() -> None:
    action = plan_one(item(kind="chunk"), "confirmed", existing("confirmed", seen_count=1))
    assert action == Reinforce(
        index=0, item_id=ID_A, kind="chunk", seen_count=2, leech=False, due=TOMORROW_4AM_MX
    )


def test_reinforce_upgrades_kind_to_correction_but_never_downgrades() -> None:
    up = plan_one(item(kind="correction"), "confirmed", existing("confirmed", kind="term"))
    assert isinstance(up, Reinforce)
    assert up.kind == "correction"
    keep = plan_one(item(kind="term"), "confirmed", existing("confirmed", kind="correction"))
    assert isinstance(keep, Reinforce)
    assert keep.kind == "correction"


def test_third_appearance_within_30_days_sets_leech() -> None:
    action = plan_one(item(), "confirmed", existing("confirmed", seen_count=2, age_days=30))
    assert isinstance(action, Reinforce)
    assert action.seen_count == 3
    assert action.leech is True


def test_third_appearance_after_30_days_is_not_a_leech() -> None:
    action = plan_one(item(), "confirmed", existing("confirmed", seen_count=2, age_days=31))
    assert isinstance(action, Reinforce)
    assert action.leech is False


def test_second_appearance_is_not_a_leech_and_leech_is_sticky() -> None:
    second = plan_one(item(), "confirmed", existing("confirmed", seen_count=1))
    assert isinstance(second, Reinforce)
    assert second.leech is False
    sticky = plan_one(item(), "confirmed", existing("confirmed", leech=True, age_days=90))
    assert isinstance(sticky, Reinforce)
    assert sticky.leech is True


@pytest.mark.parametrize("status", ["provisional", "declined"])
def test_confirmed_item_cannot_be_downgraded(status: SaveStatus) -> None:
    assert plan_one(item(), status, existing("confirmed")) == Reject(
        index=0, reason="already_confirmed"
    )


def test_archived_behaves_like_confirmed() -> None:
    # Ruling 9
    assert isinstance(plan_one(item(), "confirmed", existing("archived")), Reinforce)
    assert plan_one(item(), "declined", existing("archived")) == Reject(
        index=0, reason="already_confirmed"
    )


@pytest.mark.parametrize("old_status", ["provisional", "declined"])
def test_provisional_or_declined_is_promoted_when_confirmed(old_status: GlossaryStatus) -> None:
    assert plan_one(item(), "confirmed", existing(old_status)) == Promote(
        index=0, item_id=ID_A, first_due=TOMORROW_4AM_MX
    )


def test_provisional_to_declined_sets_status() -> None:
    assert plan_one(item(), "declined", existing("provisional")) == SetStatus(
        index=0, item_id=ID_A, status="declined", provisional_expires_at=None
    )


def test_declined_to_provisional_restarts_expiry() -> None:
    assert plan_one(item(), "provisional", existing("declined")) == SetStatus(
        index=0, item_id=ID_A, status="provisional", provisional_expires_at=NOW + timedelta(days=7)
    )


@given(
    st.lists(
        st.builds(
            IncomingItem,
            kind=st.sampled_from(["correction", "chunk", "term"]),
            text=st.text(max_size=130),
            meaning=st.text(max_size=210),
            context_sentence=st.text(max_size=310),
            domain=st.just("it"),
        ),
        max_size=10,
    ),
    st.sampled_from(["confirmed", "provisional", "declined"]),
)
def test_one_action_per_input_in_order(items: list[IncomingItem], status: SaveStatus) -> None:
    actions = plan_glossary_save(items, status, {}, NOW, MX)
    assert [a.index for a in actions] == list(range(len(items)))
    inserted = [a.text_norm for a in actions if isinstance(a, InsertItem)]
    assert len(inserted) == len(set(inserted))
    assert all(norm for norm in inserted)


# --- spontaneous_use -------------------------------------------------------------


def test_spontaneous_use_needs_two_distinct_turns() -> None:
    texts = {ID_A: "push back on", ID_B: "ship it"}
    turns = [
        "We should PUSH BACK ON the scope.",
        "I\N{RIGHT SINGLE QUOTATION MARK}d push back on that, honestly.",
        "Let's ship it.",
        "Let's ship it.",
    ]
    assert spontaneous_use(texts, turns) == frozenset({ID_A})


def test_spontaneous_use_matches_whole_words_only() -> None:
    texts = {ID_A: "it"}
    turns = ["with a bit of luck", "item one", "it works", "fix it"]
    assert spontaneous_use(texts, turns) == frozenset({ID_A})
    assert spontaneous_use(texts, turns[:3]) == frozenset()


def test_spontaneous_use_ignores_empty_texts_and_turns() -> None:
    assert spontaneous_use({ID_A: "?!"}, ["?!", "?!"]) == frozenset()
    assert spontaneous_use({ID_A: "ok"}, []) == frozenset()
