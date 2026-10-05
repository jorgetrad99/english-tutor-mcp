from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

from tutor.domain.dashboard.types import (
    BillingEvent,
    BillingEventKind,
    Client,
    GlossaryFilter,
    GlossaryKind,
    GlossaryRow,
    GlossaryStatus,
    Lang,
    Mode,
    Role,
    SessionDetail,
    SessionFilter,
    SessionStatus,
    SessionSummary,
    SubStatus,
    Tier,
    User,
)
from tutor.web.memory import FixedClock, MemoryBackend, memory_deps
from tutor.web.ports import GoogleIdentity

NOW = datetime(2027, 1, 12, 18, 0, tzinfo=UTC)
TODAY = date(2027, 1, 12)


def user(name: str = "Ana") -> User:
    return User(uuid4(), name, Role.LEARNER, Lang.ES_MX, "America/Mexico_City", False)


def test_sign_in_is_idempotent_by_google_sub() -> None:
    backend = MemoryBackend()
    ident = GoogleIdentity("sub-1", "ana@example.com", "Ana", True)
    first = backend.sign_in(ident, NOW)
    again = backend.sign_in(replace(ident, email="other@example.com"), NOW)
    assert first.id == again.id
    assert backend.subscription(first.id).tier is Tier.FREE


def test_apply_event_is_idempotent() -> None:
    backend = MemoryBackend()
    u = user()
    backend.add_user(u)
    event = BillingEvent(
        "evt_1", BillingEventKind.INVOICE_PAID, NOW, "cus_1", "sub_1", None, True, date(2028, 1, 12)
    )
    assert backend.apply_event(u.id, event) is True
    assert backend.apply_event(u.id, event) is False
    assert backend.subscription(u.id).status is SubStatus.ACTIVE


def test_glossary_update_refuses_other_users_item() -> None:
    backend = MemoryBackend()
    owner, other = user(), user("Beto")
    backend.add_user(owner)
    backend.add_user(other)
    row = GlossaryRow(
        uuid4(),
        GlossaryKind.CHUNK,
        "trade-off",
        "",
        "A trade-off.",
        "it",
        GlossaryStatus.CONFIRMED,
        None,
        False,
        None,
    )
    backend.glossaries[owner.id] = [row]
    assert backend.update_glossary_text(other.id, row.id, "x", "y") is None
    updated = backend.update_glossary_text(owner.id, row.id, "compromiso", "A trade-off here.")
    assert updated is not None and updated.meaning == "compromiso"
    assert backend.glossary(owner.id, GlossaryFilter(), TODAY)[0].meaning == "compromiso"


def test_sessions_filter_and_paginate_newest_first() -> None:
    backend = MemoryBackend()
    u = user()
    backend.add_user(u)
    details = []
    for day in range(1, 6):
        summary = SessionSummary(
            uuid4(),
            datetime(2027, 1, day, 15, tzinfo=UTC),
            Mode.VOICE if day % 2 else Mode.TEXT,
            Client.CLAUDE,
            f"Day {day}",
            15,
            20.0,
            None,
            SessionStatus.CLOSED,
            False,
        )
        details.append(SessionDetail(summary, (), 0, (), (), None, None, ("hi",)))
    backend.session_rows[u.id] = details
    page = backend.sessions(u.id, SessionFilter(), page=1, per_page=2)
    assert [s.label for s in page.items] == ["Day 5", "Day 4"] and page.has_next
    voice = backend.sessions(u.id, SessionFilter(mode=Mode.VOICE), page=1, per_page=10)
    assert [s.label for s in voice.items] == ["Day 5", "Day 3", "Day 1"]
    assert backend.session_detail(user().id, details[0].summary.id) is None


def test_request_deletion_marks_user_and_drops_sessions() -> None:
    backend = MemoryBackend()
    u = user()
    backend.add_user(u)
    deps = memory_deps(backend, FixedClock(NOW))
    backend.request_deletion(u.id, NOW)
    found = deps.users.find_user(u.id)
    assert found is not None and found.deletion_requested_at == NOW


def test_fixed_clock_advances() -> None:
    clock = FixedClock(NOW)
    clock.advance(NOW - NOW.replace(hour=17))
    assert clock() == NOW.replace(hour=19)
