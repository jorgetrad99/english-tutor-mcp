"""In-memory adapters for tests and the local demo. Never wired in production."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from tutor.domain.dashboard.billing import next_subscription
from tutor.domain.dashboard.glossary import filter_glossary
from tutor.domain.dashboard.home import week_start
from tutor.domain.dashboard.types import (
    AccountData,
    BillingEvent,
    BillingEventKind,
    ConnectedClient,
    FreeUsage,
    GlossaryFilter,
    GlossaryRow,
    HomeData,
    Lang,
    PlanPage,
    Preferences,
    Profile,
    ProgressData,
    ReportsPage,
    Role,
    Series,
    SessionDetail,
    SessionFilter,
    SessionPage,
    SessionStatus,
    Setting,
    Subscription,
    SubStatus,
    Tier,
    User,
)
from tutor.web.ports import (
    BillingGateway,
    Clock,
    GoogleIdentity,
    GoogleLogin,
    InvalidSignature,
    LoginFailed,
    WebDeps,
    WebSession,
)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


_EMPTY = Series((), ())


def empty_home(today: date) -> HomeData:
    return HomeData(
        has_connected=False,
        has_plan=False,
        today=None,
        week_start=week_start(today),
        planned_days=(),
        session_marks=(),
        streak=0,
        stamps=(),
        last_session=None,
        reviews_due=0,
        provisional_items=0,
        latest_report=None,
        newest_closed_session_id=None,
        last_celebrated_session_id=None,
    )


def empty_progress() -> ProgressData:
    return ProgressData(
        sessions_counted=0,
        wpm_voice=Series((), (), 25),
        wpm_text=Series((), (), 12),
        errors_per_100w=_EMPTY,
        activation_rate=Series((), (), 0.4),
        minutes_spoken=_EMPTY,
        sessions_done=_EMPTY,
        sessions_planned=_EMPTY,
        cefr_trend=_EMPTY,
        cefr_excluded=(),
        confidence_trend=_EMPTY,
        recurring_stopped=(),
        parked_leeches=(),
        checkpoint=None,
    )


@dataclass
class MemoryBackend:
    """Implements every data port. Fields are public so tests and the demo seed can fill them."""

    price_id: str = "price_29"
    price_cents: int = 2900
    session_cap: int = 3
    glossary_cap: int = 50
    users: dict[UUID, User] = field(default_factory=dict)
    by_sub: dict[str, UUID] = field(default_factory=dict)
    web_sessions: dict[str, WebSession] = field(default_factory=dict)
    homes: dict[UUID, HomeData] = field(default_factory=dict)
    sessions_this_week: dict[UUID, int] = field(default_factory=dict)
    plans: dict[UUID, PlanPage] = field(default_factory=dict)
    progresses: dict[UUID, ProgressData] = field(default_factory=dict)
    session_rows: dict[UUID, list[SessionDetail]] = field(default_factory=dict)
    glossaries: dict[UUID, list[GlossaryRow]] = field(default_factory=dict)
    report_pages: dict[UUID, ReportsPage] = field(default_factory=dict)
    profiles: dict[UUID, Profile] = field(default_factory=dict)
    email_prefs: dict[UUID, tuple[bool, bool]] = field(default_factory=dict)
    clients: dict[UUID, list[ConnectedClient]] = field(default_factory=dict)
    install_dismissed: set[UUID] = field(default_factory=set)
    subs: dict[UUID, Subscription] = field(default_factory=dict)
    customers: dict[UUID, str] = field(default_factory=dict)
    seen_events: set[str] = field(default_factory=set)
    settings_rows: dict[str, Setting] = field(default_factory=dict)
    celebrated: dict[UUID, UUID] = field(default_factory=dict)
    mcp_seen: set[UUID] = field(default_factory=set)  # users.mcp_first_seen_at is set
    cefr_excluded: set[UUID] = field(default_factory=set)  # session ids
    audit: list[tuple[str, UUID, str]] = field(default_factory=list)

    # --- setup helpers ----------------------------------------------------

    def add_user(self, user: User, *, google_sub: str | None = None) -> None:
        self.users[user.id] = user
        if google_sub is not None:
            self.by_sub[google_sub] = user.id
        self.subs.setdefault(
            user.id, Subscription(Tier.FREE, SubStatus.NONE, self.price_id, self.price_cents)
        )

    # --- UserDirectory ----------------------------------------------------

    def sign_in(self, identity: GoogleIdentity, now: datetime) -> User:
        known = self.by_sub.get(identity.sub)
        if known is not None:
            return self.users[known]
        name = identity.name or identity.email.split("@", 1)[0]
        user = User(uuid4(), name, Role.LEARNER, Lang.ES_MX, "America/Mexico_City", False)
        self.add_user(user, google_sub=identity.sub)
        return user

    def find_user(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)

    # --- WebSessionStore --------------------------------------------------

    def load_session(self, token_hash: str) -> WebSession | None:
        return self.web_sessions.get(token_hash)

    def create_session(self, session: WebSession) -> None:
        if session.token_hash in self.web_sessions:
            raise ValueError("session exists")
        self.web_sessions[session.token_hash] = session

    def touch_session(self, token_hash: str, last_seen_at: datetime, data: dict[str, Any]) -> bool:
        session = self.web_sessions.get(token_hash)
        if session is None:
            return False
        session.last_seen_at = last_seen_at
        session.data = data
        return True

    def purge_expired(
        self, now: datetime, *, idle: timedelta, absolute: timedelta, anonymous: timedelta
    ) -> int:
        def expired(s: WebSession) -> bool:
            if s.user_id is None:
                return now - s.created_at > anonymous
            return now - s.last_seen_at > idle or now - s.created_at > absolute

        dead = [k for k, s in self.web_sessions.items() if expired(s)]
        for key in dead:
            del self.web_sessions[key]
        return len(dead)

    def delete_session(self, token_hash: str) -> None:
        self.web_sessions.pop(token_hash, None)

    def delete_user_sessions(self, user_id: UUID) -> None:
        for key in [k for k, s in self.web_sessions.items() if s.user_id == user_id]:
            del self.web_sessions[key]

    # --- DashboardReader --------------------------------------------------

    def home(self, user_id: UUID, today: date) -> HomeData:
        home = self.homes.get(user_id, empty_home(today))
        celebrated = self.celebrated.get(user_id, home.last_celebrated_session_id)
        connected = home.has_connected or self.has_connected(user_id)
        return replace(home, last_celebrated_session_id=celebrated, has_connected=connected)

    def usage(self, user_id: UUID, today: date) -> FreeUsage:
        resets = week_start(today) + timedelta(days=7)
        return FreeUsage(
            self.sessions_this_week.get(user_id, 0),
            self.session_cap,
            len(self.glossaries.get(user_id, [])),
            self.glossary_cap,
            resets,
        )

    def plan(self, user_id: UUID) -> PlanPage | None:
        return self.plans.get(user_id)

    def progress(self, user_id: UUID) -> ProgressData:
        return self.progresses.get(user_id, empty_progress())

    def sessions(self, user_id: UUID, f: SessionFilter, page: int, per_page: int) -> SessionPage:
        rows = [
            d.summary
            for d in self.session_rows.get(user_id, [])
            if (f.mode is None or d.summary.mode is f.mode)
            and (f.status is None or d.summary.status is f.status)
        ]
        rows.sort(key=lambda s: s.started_at, reverse=True)
        start = (page - 1) * per_page
        has_next = len(rows) > start + per_page
        return SessionPage(tuple(rows[start : start + per_page]), page, has_next)

    def session_detail(self, user_id: UUID, session_id: UUID) -> SessionDetail | None:
        for detail in self.session_rows.get(user_id, []):
            if detail.summary.id == session_id:
                shown = (
                    detail.summary.status is SessionStatus.CLOSED
                    and session_id not in self.cefr_excluded
                )
                return detail if shown else replace(detail, cefr=None)
        return None

    def glossary(self, user_id: UUID, f: GlossaryFilter, today: date) -> tuple[GlossaryRow, ...]:
        return filter_glossary(self.glossaries.get(user_id, []), f, today)

    def glossary_domains(self, user_id: UUID) -> tuple[str, ...]:
        return tuple(sorted({r.domain for r in self.glossaries.get(user_id, [])}))

    def reports(self, user_id: UUID) -> ReportsPage:
        return self.report_pages.get(user_id, ReportsPage((), ()))

    def account(self, user_id: UUID) -> AccountData:
        user = self.users[user_id]
        weekly, reminders = self.email_prefs.get(user_id, (True, True))
        return AccountData(
            user=user,
            profile=self.profiles.get(user_id),
            prefs=Preferences(user.lang, user.reduce_motion, weekly, reminders),
            clients=tuple(self.clients.get(user_id, [])),
            subscription=self.subscription(user_id),
            install_prompt_dismissed=user_id in self.install_dismissed,
        )

    def has_connected(self, user_id: UUID) -> bool:
        return user_id in self.mcp_seen or bool(self.session_rows.get(user_id))

    # --- GlossaryEditor ---------------------------------------------------

    def update_glossary_text(
        self, user_id: UUID, item_id: UUID, meaning: str, context_sentence: str
    ) -> GlossaryRow | None:
        rows = self.glossaries.get(user_id, [])
        for i, row in enumerate(rows):
            if row.id == item_id:
                rows[i] = replace(row, meaning=meaning, context_sentence=context_sentence)
                return rows[i]
        return None

    # --- AccountService ---------------------------------------------------

    def set_preferences(self, user_id: UUID, prefs: Preferences) -> None:
        user = self.users[user_id]
        self.users[user_id] = replace(user, lang=prefs.lang, reduce_motion=prefs.reduce_motion)
        self.email_prefs[user_id] = (prefs.email_weekly, prefs.email_reminders)

    def revoke_client(self, user_id: UUID, client_id: str) -> bool:
        clients = self.clients.get(user_id, [])
        kept = [c for c in clients if c.id != client_id]
        self.clients[user_id] = kept
        return len(kept) != len(clients)

    def export_data(self, user_id: UUID) -> dict[str, Any]:
        def plain(obj: object) -> Any:
            return json.loads(json.dumps(asdict(obj), default=str))  # type: ignore[call-overload]

        return {
            "user": plain(self.users[user_id]),
            "subscription": plain(self.subscription(user_id)),
            "glossary": [plain(r) for r in self.glossaries.get(user_id, [])],
            "sessions": [plain(d) for d in self.session_rows.get(user_id, [])],
        }

    def request_deletion(self, user_id: UUID, now: datetime) -> None:
        self.users[user_id] = replace(self.users[user_id], deletion_requested_at=now)
        self.delete_user_sessions(user_id)

    def dismiss_install_prompt(self, user_id: UUID, now: datetime) -> None:
        self.install_dismissed.add(user_id)

    def mark_celebrated(self, user_id: UUID, session_id: UUID) -> None:
        self.celebrated[user_id] = session_id

    # --- SettingsStore ----------------------------------------------------

    def list_settings(self) -> tuple[Setting, ...]:
        return tuple(sorted(self.settings_rows.values(), key=lambda s: s.key))

    def update_setting(self, key: str, value: str, actor_id: UUID, now: datetime) -> Setting | None:
        current = self.settings_rows.get(key)
        if current is None:
            return None
        updated = replace(current, value=value, updated_at=now)
        self.settings_rows[key] = updated
        self.audit.append(("setting_changed", actor_id, key))
        return updated

    # --- SubscriptionStore ------------------------------------------------

    def subscription(self, user_id: UUID) -> Subscription:
        return self.subs.get(
            user_id, Subscription(Tier.FREE, SubStatus.NONE, self.price_id, self.price_cents)
        )

    def customer_id(self, user_id: UUID) -> str | None:
        return self.customers.get(user_id)

    def user_for_customer(self, customer_id: str) -> UUID | None:
        for user_id, cus in self.customers.items():
            if cus == customer_id:
                return user_id
        return None

    def link_customer(self, user_id: UUID, customer_id: str) -> None:
        self.customers[user_id] = customer_id

    def apply_event(self, user_id: UUID, event: BillingEvent) -> bool:
        if event.event_id in self.seen_events:
            return False
        self.subs[user_id] = next_subscription(self.subscription(user_id), event)
        self.seen_events.add(event.event_id)
        return True


class FakeBilling:
    """Stands in for Stripe. Webhook payloads are JSON of BillingEvent fields; the
    signature must equal "valid"."""

    def __init__(self) -> None:
        self.checkouts: list[dict[str, Any]] = []

    def checkout_url(
        self,
        *,
        user_id: UUID,
        customer_id: str | None,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        self.checkouts.append(
            {
                "user_id": user_id,
                "customer_id": customer_id,
                "price_id": price_id,
                "success_url": success_url,
                "cancel_url": cancel_url,
            }
        )
        return f"https://checkout.stripe.test/c/{user_id}"

    def portal_url(self, *, customer_id: str, return_url: str) -> str:
        return f"https://billing.stripe.test/p/{customer_id}"

    def parse_event(self, payload: bytes, signature: str) -> BillingEvent | None:
        if signature != "valid":
            raise InvalidSignature(signature)
        raw = json.loads(payload)
        if raw["kind"] not in {k.value for k in BillingEventKind}:
            return None
        return BillingEvent(
            event_id=raw["event_id"],
            kind=BillingEventKind(raw["kind"]),
            created=datetime.fromisoformat(raw["created"]),
            customer_id=raw["customer_id"],
            subscription_ref=raw.get("subscription_ref"),
            user_id=UUID(raw["user_id"]) if raw.get("user_id") else None,
            paid=bool(raw.get("paid", True)),
            period_end=date.fromisoformat(raw["period_end"]) if raw.get("period_end") else None,
        )


class FakeGoogle:
    """Redirects straight back to the callback; `identity` returns the configured identity."""

    def __init__(self, identity: GoogleIdentity | None = None) -> None:
        self.next_identity = identity

    async def redirect(self, request: Request, redirect_uri: str) -> Response:
        request.session["_state_google_fake"] = {"data": {"redirect_uri": redirect_uri}}
        return RedirectResponse(f"{redirect_uri}?code=fake&state=fake", status_code=302)

    async def identity(self, request: Request) -> GoogleIdentity:
        request.session.pop("_state_google_fake", None)
        if self.next_identity is None:
            raise LoginFailed("no identity configured")
        return self.next_identity


def memory_deps(
    backend: MemoryBackend,
    clock: Clock,
    *,
    billing: BillingGateway | None = None,
    google: GoogleLogin | None = None,
) -> WebDeps:
    return WebDeps(
        users=backend,
        sessions=backend,
        reader=backend,
        glossary=backend,
        account=backend,
        settings=backend,
        subscriptions=backend,
        billing=billing or FakeBilling(),
        google=google or FakeGoogle(),
        clock=clock,
    )
