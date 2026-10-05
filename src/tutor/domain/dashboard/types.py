"""View data for the web dashboard.

The service layer fills these types; `tutor.web` renders them. Every number here
was computed by the server; templates only format it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID


class Tier(StrEnum):
    FREE = "free"
    ANNUAL = "annual"


class SubStatus(StrEnum):
    NONE = "none"  # Free, never paid
    ACTIVE = "active"
    PAST_DUE = "past_due"  # invoice.payment_failed
    LAPSED = "lapsed"  # customer.subscription.deleted: back on Free, nothing deleted


class Mode(StrEnum):
    VOICE = "voice"
    TEXT = "text"


class Client(StrEnum):
    CLAUDE = "claude"
    CHATGPT = "chatgpt"
    CODE = "code"


class SessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    INCOMPLETE = "incomplete"


class TaskResult(StrEnum):
    ACHIEVED = "achieved"
    PARTIAL = "partial"
    NOT_ACHIEVED = "not_achieved"


class ItemStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"


class Skill(StrEnum):
    SPEAKING = "speaking"
    WRITING = "writing"
    LISTENING = "listening"


class GlossaryKind(StrEnum):
    CORRECTION = "correction"
    CHUNK = "chunk"
    TERM = "term"


class GlossaryStatus(StrEnum):
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    ARCHIVED = "archived"


class DueFilter(StrEnum):
    TODAY = "today"  # due on or before today
    WEEK = "week"  # due within the next 7 days, today included


class ErrorCategory(StrEnum):
    GRAMMAR = "grammar"
    LEXIS = "lexis"
    WORD_ORDER = "word_order"
    REGISTER = "register"
    OTHER = "other"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Role(StrEnum):
    LEARNER = "learner"
    ADMIN = "admin"


class Lang(StrEnum):
    ES_MX = "es_MX"
    EN = "en"


class SettingType(StrEnum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    STR = "str"


class BannerKind(StrEnum):
    PAYMENT_FAILED = "payment_failed"
    RENEWAL_SOON = "renewal_soon"
    LAPSED = "lapsed"
    SESSION_LIMIT = "session_limit"
    GLOSSARY_LIMIT = "glossary_limit"


class DayState(StrEnum):
    DONE = "done"  # planned day with a closed session
    TODAY = "today"  # planned, today, no closed session yet
    UPCOMING = "upcoming"
    MISSED = "missed"  # planned, past, no closed session: shown as "sin sesión"
    REST = "rest"  # not a planned day


class BillingEventKind(StrEnum):
    CHECKOUT_COMPLETED = "checkout.session.completed"
    INVOICE_PAID = "invoice.paid"
    PAYMENT_FAILED = "invoice.payment_failed"
    SUBSCRIPTION_DELETED = "customer.subscription.deleted"


# --- identity and billing -------------------------------------------------


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    display_name: str
    role: Role
    lang: Lang
    timezone: str  # IANA name, e.g. "America/Mexico_City"
    reduce_motion: bool
    deletion_requested_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Subscription:
    tier: Tier
    status: SubStatus
    price_id: str  # the learner's cohort Price for Annual
    price_cents: int  # USD cents
    period_end: date | None = None
    subscription_ref: str | None = None
    last_event_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FreeUsage:
    sessions_this_week: int
    session_cap: int
    glossary_items: int
    glossary_cap: int
    week_resets_on: date


@dataclass(frozen=True, slots=True)
class Banner:
    kind: BannerKind
    on: date | None = None
    amount_cents: int | None = None


@dataclass(frozen=True, slots=True)
class BillingEvent:
    event_id: str
    kind: BillingEventKind
    created: datetime
    customer_id: str
    subscription_ref: str | None
    user_id: UUID | None  # from client_reference_id; checkout events only
    paid: bool
    period_end: date | None


# --- Inicio ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlannedDay:
    day: date
    title: str


@dataclass(frozen=True, slots=True)
class SessionMark:
    day: date
    on_plan: bool
    status: SessionStatus


@dataclass(frozen=True, slots=True)
class TrailNode:
    day: date
    state: DayState
    is_today: bool
    title: str | None
    extra_sessions: int  # closed off-plan (prep) sessions that day


@dataclass(frozen=True, slots=True)
class TodayItem:
    title: str
    scenario_hint: str
    skill: Skill
    domain: str


@dataclass(frozen=True, slots=True)
class Stamp:
    text: str
    used: bool


@dataclass(frozen=True, slots=True)
class SessionSummary:
    id: UUID
    started_at: datetime
    mode: Mode
    client: Client
    label: str  # plan item title or the prep event text
    duration_min: int
    words_per_min: float | None
    task_result: TaskResult | None
    status: SessionStatus
    low_trust: bool


@dataclass(frozen=True, slots=True)
class ReportNumbers:
    week_start: date
    sessions_done: int
    sessions_planned: int
    minutes_spoken: int
    errors_stopped: int
    chunks_activated: int


@dataclass(frozen=True, slots=True)
class HomeData:
    has_connected: bool  # at least one session ever
    has_plan: bool
    today: TodayItem | None
    week_start: date
    planned_days: tuple[PlannedDay, ...]
    session_marks: tuple[SessionMark, ...]
    streak: int
    stamps: tuple[Stamp, ...]
    last_session: SessionSummary | None
    reviews_due: int
    provisional_items: int
    latest_report: ReportNumbers | None
    newest_closed_session_id: UUID | None
    last_celebrated_session_id: UUID | None


# --- Plan -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanItemView:
    week_no: int
    order_no: int
    key: str  # stable identity across versions: "<can-do id>:<domain>:<skill>"
    title: str
    skill: Skill
    domain: str
    scenario_hint: str
    status: ItemStatus


@dataclass(frozen=True, slots=True)
class PlanVersion:
    version: int
    generated_at: datetime
    items: tuple[PlanItemView, ...]


@dataclass(frozen=True, slots=True)
class PlanPage:
    current_week_no: int
    rationale: str
    realism_message: str | None
    versions: tuple[PlanVersion, ...]  # newest first; never empty


# --- Progreso -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Series:
    x: tuple[date, ...]
    y: tuple[float | None, ...]  # None = gap (excluded point)
    target: float | None = None


@dataclass(frozen=True, slots=True)
class Checkpoint:
    taken_on: date
    measured_level: str
    model_trend_level: str | None


@dataclass(frozen=True, slots=True)
class ProgressData:
    sessions_counted: int
    wpm_voice: Series
    wpm_text: Series
    errors_per_100w: Series
    activation_rate: Series  # 0..1
    minutes_spoken: Series  # per week
    sessions_done: Series  # per week
    sessions_planned: Series  # per week, same x as sessions_done
    cefr_trend: Series  # numeric 1.0-6.0
    cefr_excluded: tuple[date, ...]
    confidence_trend: Series  # 1-5
    recurring_stopped: tuple[str, ...]  # correct forms
    parked_leeches: tuple[str, ...]
    checkpoint: Checkpoint | None


# --- Sesiones -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionFilter:
    mode: Mode | None = None
    status: SessionStatus | None = None


@dataclass(frozen=True, slots=True)
class SessionPage:
    items: tuple[SessionSummary, ...]
    page: int
    has_next: bool


@dataclass(frozen=True, slots=True)
class ErrorView:
    said: str
    correct: str
    category: ErrorCategory
    taken_up: bool


@dataclass(frozen=True, slots=True)
class CefrOpinion:
    level: str
    confidence: Confidence
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionDetail:
    summary: SessionSummary
    errors: tuple[ErrorView, ...]
    hints_given: int
    chunks_offered: tuple[str, ...]
    chunks_used: tuple[str, ...]
    cefr: CefrOpinion | None
    confidence_1_5: int | None
    user_turns: tuple[str, ...]


# --- Glosario -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlossaryRow:
    id: UUID
    kind: GlossaryKind
    text: str
    meaning: str
    context_sentence: str
    domain: str
    status: GlossaryStatus
    due_on: date | None
    leech: bool
    expires_on: date | None  # provisional items only


@dataclass(frozen=True, slots=True)
class GlossaryFilter:
    kind: GlossaryKind | None = None
    domain: str | None = None
    status: GlossaryStatus | None = None
    due: DueFilter | None = None
    q: str = ""


# --- Reportes -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeeklyReport:
    numbers: ReportNumbers
    focus: str
    leech: str | None
    win: str | None


@dataclass(frozen=True, slots=True)
class ReportsPage:
    weekly: tuple[WeeklyReport, ...]  # newest first
    checkpoints: tuple[Checkpoint, ...]  # newest first


# --- Cuenta and admin -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Preferences:
    lang: Lang
    reduce_motion: bool
    email_weekly: bool
    email_reminders: bool


@dataclass(frozen=True, slots=True)
class Profile:
    domains: tuple[str, ...]
    minutes_per_day: int
    days_per_week: int
    target_level: str | None
    target_date: date | None


@dataclass(frozen=True, slots=True)
class ConnectedClient:
    id: str
    name: str
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class AccountData:
    user: User
    profile: Profile | None
    prefs: Preferences
    clients: tuple[ConnectedClient, ...]
    subscription: Subscription
    install_prompt_dismissed: bool


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    value: str  # canonical string form, see settings.parse_setting
    type: SettingType
    description: str
    updated_at: datetime
