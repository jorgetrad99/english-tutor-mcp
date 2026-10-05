"""What the web layer needs from the rest of the system.

Routes call these ports only; they never open a database session. Every method that
takes a `user_id` must return only that user's data (requirements section 5).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from starlette.requests import Request
from starlette.responses import Response

from tutor.domain.dashboard.types import (
    AccountData,
    BillingEvent,
    FreeUsage,
    GlossaryFilter,
    GlossaryRow,
    HomeData,
    PlanPage,
    Preferences,
    ProgressData,
    ReportsPage,
    SessionDetail,
    SessionFilter,
    SessionPage,
    Setting,
    Subscription,
    User,
)

Clock = Callable[[], datetime]


@dataclass
class WebSession:
    token_hash: str
    user_id: UUID | None
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str
    email_verified: bool


class LoginFailed(Exception):
    pass


class InvalidSignature(Exception):
    pass


class UserDirectory(Protocol):
    def sign_in(self, identity: GoogleIdentity, now: datetime) -> User:
        """Find or create the user by Google `sub` (never by email alone)."""
        ...

    def find_user(self, user_id: UUID) -> User | None: ...


class WebSessionStore(Protocol):
    def load_session(self, token_hash: str) -> WebSession | None: ...

    def create_session(self, session: WebSession) -> None:
        """Insert only. Never replaces an existing row."""
        ...

    def touch_session(self, token_hash: str, last_seen_at: datetime, data: dict[str, Any]) -> bool:
        """Update only. Returns False when the row is gone (it must not be recreated)."""
        ...

    def delete_session(self, token_hash: str) -> None: ...

    def delete_user_sessions(self, user_id: UUID) -> None: ...

    def purge_expired(
        self, now: datetime, *, idle: timedelta, absolute: timedelta, anonymous: timedelta
    ) -> int:
        """Delete expired rows (idle and absolute for users, `anonymous` for pre-login)."""
        ...


class DashboardReader(Protocol):
    def home(self, user_id: UUID, today: date) -> HomeData: ...

    def usage(self, user_id: UUID, today: date) -> FreeUsage: ...

    def plan(self, user_id: UUID) -> PlanPage | None: ...

    def progress(self, user_id: UUID) -> ProgressData: ...

    def sessions(
        self, user_id: UUID, f: SessionFilter, page: int, per_page: int
    ) -> SessionPage: ...

    def session_detail(self, user_id: UUID, session_id: UUID) -> SessionDetail | None: ...

    def glossary(
        self, user_id: UUID, f: GlossaryFilter, today: date
    ) -> tuple[GlossaryRow, ...]: ...

    def glossary_domains(self, user_id: UUID) -> tuple[str, ...]: ...

    def reports(self, user_id: UUID) -> ReportsPage: ...

    def account(self, user_id: UUID) -> AccountData: ...

    def has_any_session(self, user_id: UUID) -> bool: ...


class GlossaryEditor(Protocol):
    def update_glossary_text(
        self, user_id: UUID, item_id: UUID, meaning: str, context_sentence: str
    ) -> GlossaryRow | None:
        """Values are already cleaned. Returns None when the item is not this user's."""
        ...


class AccountService(Protocol):
    def set_preferences(self, user_id: UUID, prefs: Preferences) -> None: ...

    def revoke_client(self, user_id: UUID, client_id: str) -> bool: ...

    def export_data(self, user_id: UUID) -> dict[str, Any]: ...

    def request_deletion(self, user_id: UUID, now: datetime) -> None:
        """Soft delete, cancel any subscription renewal, end all web sessions.
        The worker purges within 24 h."""
        ...

    def dismiss_install_prompt(self, user_id: UUID, now: datetime) -> None: ...

    def mark_celebrated(self, user_id: UUID, session_id: UUID) -> None: ...


class SettingsStore(Protocol):
    def list_settings(self) -> tuple[Setting, ...]: ...

    def update_setting(self, key: str, value: str, actor_id: UUID, now: datetime) -> Setting | None:
        """`value` is canonical (domain.dashboard.settings.parse_setting). Writes audit_log.
        Returns None for an unknown key."""
        ...


class SubscriptionStore(Protocol):
    def subscription(self, user_id: UUID) -> Subscription: ...

    def customer_id(self, user_id: UUID) -> str | None: ...

    def user_for_customer(self, customer_id: str) -> UUID | None: ...

    def link_customer(self, user_id: UUID, customer_id: str) -> None: ...

    def apply_event(self, user_id: UUID, event: BillingEvent) -> bool:
        """In one transaction: skip if event_id was seen (return False), otherwise store
        next_subscription(current, event) and record the event id (return True)."""
        ...


class BillingGateway(Protocol):
    def checkout_url(
        self,
        *,
        user_id: UUID,
        customer_id: str | None,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> str: ...

    def portal_url(self, *, customer_id: str, return_url: str) -> str: ...

    def parse_event(self, payload: bytes, signature: str) -> BillingEvent | None:
        """Verify and parse a webhook. Raises InvalidSignature; returns None for event
        types the dashboard ignores."""
        ...


class GoogleLogin(Protocol):
    async def redirect(self, request: Request, redirect_uri: str) -> Response: ...

    async def identity(self, request: Request) -> GoogleIdentity:
        """Raises LoginFailed."""
        ...


@dataclass(frozen=True)
class WebDeps:
    users: UserDirectory
    sessions: WebSessionStore
    reader: DashboardReader
    glossary: GlossaryEditor
    account: AccountService
    settings: SettingsStore
    subscriptions: SubscriptionStore
    billing: BillingGateway
    google: GoogleLogin
    clock: Clock


UNCAPPED = 2**31 - 1
"""A FreeUsage cap at this value means "no cap" (core loop v0: caps are not enforced, spec 3.2).
banners_for raises no limit banner for it, and render() hides the Free counters."""
