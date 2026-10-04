# Dashboard Web Layer Implementation Plan — Part 1 of 2 (Tasks 1–15)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

This plan is split in two files to stay reviewable: Part 1 (`2026-10-04-dashboard-web-1-foundation.md`, Tasks 1–15: domain, web foundation, look and install) and Part 2 (`2026-10-04-dashboard-web-2-pages.md`, Tasks 16–30: payments, pages, verification). Execute Part 1 first; both share the header below.

**Status: parked.** Do not start before 2027-01-05, and only if the gate decision on 2026-11-30 says continue. Scope is re-estimated at the gate (requirements section 16); re-read this plan then.

**Goal:** Build the installable web dashboard (pages, payments, PWA shell, motion layer) as a server-rendered layer that talks to the rest of the system only through typed ports.

**Architecture:** A new `tutor.web` package (FastAPI routes, Jinja2 templates, HTMX partials, static assets) renders view types from a new pure package `tutor.domain.dashboard`. Routes call ports (`tutor.web.ports`) and never touch the database. This plan ships an in-memory implementation of the ports for tests and a local demo, plus the real Google and Stripe adapters; the Postgres adapters that connect the ports to the core-loop and plan-engine services are a follow-up plan (see "Scope of this plan").

**Tech Stack:** Python 3.12, FastAPI ≥ 0.142.2, Jinja2 with `jinja2.ext.i18n`, Babel ≥ 2.18, HTMX 2.0.11 (vendored), Web Animations API and CSS (no animation library), Authlib ≥ 1.8.0 (Google OIDC), stripe-python ≥ 16.0.0 (`StripeClient`), pytest, Playwright for Python ≥ 1.63 with axe-playwright-python ≥ 0.1.8.

**Spec:** `docs/superpowers/specs/2026-10-04-dashboard-design.md` (approved 2026-10-04, amended 2026-10-04; see "Rulings against the spec").

## Scope of this plan

**In scope:** everything in the spec that lives in the web layer: every page and state in spec sections 6 and 11, payments (section 7) with the real Stripe adapter, the PWA shell (section 8), the motion system (section 10), accessibility (section 12), tests and budgets (section 14), the in-memory backend and the local demo (`just dashboard-demo`).

**Not in this plan (follow-up plan, written when the services exist):**
- Postgres adapters for `UserDirectory`, `WebSessionStore`, `DashboardReader`, `GlossaryEditor`, `AccountService`, `SettingsStore`, `SubscriptionStore`, wrapping the core-loop and plan-engine services (requirements section 6 plus the additions in spec 9.7) and their Alembic migrations.
- Mounting `create_app` into the product FastAPI app next to the MCP endpoint and the REST API, and wiring `GoogleOidcLogin` and `StripeGateway` with real secrets.
- The worker jobs (weekly report, purge, nightly Stripe reconciliation) and the weekly email.
- The shared per-user and per-IP rate limiter (requirements section 5) in front of the web routes; this plan only adds the in-process export limit (Task 26).
- Sentry wiring and structured-log configuration for the web layer.

Those services do not exist yet (the core loop starts 2026-10-12, the plan engine 2026-12-01), so their adapters cannot be written against real code today. Every port method documents the contract the adapters must meet; the follow-up plan adds an integration test per port that runs the same assertions as `tests/unit/web/test_web_memory.py` against Postgres.

## Before Task 1 (on 2027-01-05)

1. Confirm the gate passed and the re-estimated scope still includes the dashboard.
2. Re-verify with Context7 (CLAUDE.md requires it for Stripe and OAuth) every fact in the table below. If one changed, rule on the change, record it in the ledger, and adjust the affected task before dispatching it.
3. Check that the core loop created `src/tutor/` packages as expected and that `uv run just check` passes on `main`.

| Fact (checked 2026-10-04) | Used in |
| --- | --- |
| HTMX 2.0.11 is the latest 2.x; npm `next` is 4.0.0, so pin `htmx.org@2.0.11` | Task 15 |
| HTMX config via `<meta name="htmx-config">` JSON: `allowEval`, `includeIndicatorStyles`, `historyCacheSize`, `responseHandling`; 4xx/5xx are not swapped by default, so 422 needs a `responseHandling` entry; `allowEval:false` also disables `hx-on` | Task 10 |
| `HX-Redirect` response header triggers a full-page redirect | Task 11 |
| Authlib Starlette client: `OAuth().register(..., server_metadata_url=..., client_kwargs={"scope": "openid email profile"})`, `authorize_redirect`, `authorize_access_token` returns validated claims under `token["userinfo"]`; it keeps state, nonce and PKCE data in `request.session` | Tasks 11, 12 |
| `stripe.StripeClient(api_key)`; `client.v1.checkout.sessions.create(params=...)`, `client.v1.billing_portal.sessions.create(params=...)`, `client.construct_event(payload, sig_header, secret)` raising `stripe.SignatureVerificationError` | Task 16 |
| API 2025-03-31.basil and later: `invoice.subscription` removed (use `invoice.parent.subscription_details.subscription`); `current_period_end` moved to subscription items; invoice line `period.end` still exists | Task 16 |
| OXXO is not supported in Checkout subscription mode or the Customer Portal | Ruling 2 |
| Motion 14 UMD bundle is ≈ 47.5 KB gzip | Ruling 1 |
| `Clear-Site-Data` supports `"cache"`, `"cookies"`, `"storage"` in Chrome, Firefox and Safari 17+ (best effort) | Task 12 |
| `axe-playwright-python` 0.1.8: `Axe().run(page)`; verify `violations_count` and `response["violations"]` in the installed version | Task 30 |

## Rulings against the spec

These were decided while planning, with evidence; the spec is amended in the same branch (section "Amendments" at the end of the spec).

1. **No Motion library.** Motion's browser bundle is ≈ 47.5 KB gzip, and HTMX is ≈ 16.8 KB, which breaks the 50 KB JavaScript budget (spec 14.4). Springs, counters, staggers and path drawing use the Web Animations API (`element.animate`) with CSS `linear()` spring easings and `IntersectionObserver`, in our own `app.js` (≤ 8 KB). Cost if wrong: springs look slightly less physical; swapping in Motion later only changes `app.js`.
2. **Card only; no pending-payment page.** Stripe Checkout does not support OXXO for subscriptions, so the spec's Q3 condition applies: the pending page is not built and the pre-pay summary lists card only. SPEI is not checked for subscriptions either; it is out until verified. Cost if wrong: Mexican users without cards cannot buy Annual in v1; a one-time `mode=payment` "Annual prepaid" product is the documented way to add OXXO later.
3. **es-MX numbers use a decimal point.** Mexico writes "3.1" and "1,234.5" (Babel `es_MX`); the spec's "3,1" example was wrong.
4. **Device check (spec S0) runs as Task 19,** as soon as the shell, login, the installable PWA and checkout exist, because it needs all four. Context7 checks and the ADR stay at the start (above, and Task 19 writes the ADR).

## Global Constraints

- Python 3.12; FastAPI ≥ 0.142.2; server-rendered Jinja2 + HTMX; no SPA, no JavaScript build step, no Node.
- HTMX pinned to 2.0.11, vendored with its SHA-256 in `static/js/VENDORED.md`; never load `htmx.org` unpinned (the `next` tag is 4.x).
- Budgets: JavaScript ≤ 50 KB gzip total; CSS ≤ 30 KB gzip; fonts 2 families (Baloo 2, Public Sans), Latin subset woff2, ≤ 120 KB total; LCP on Inicio ≤ 2.5 s at 4× CPU and 1.6 Mbps / 150 ms; CLS ≤ 0.1; HTMX partial server time p95 ≤ 300 ms.
- CSP exactly as `tutor.web.security.CSP`. No inline `<script>`, no `<style>` element, no `style=""` attribute, no `hx-on`, no `on*=` handler attributes anywhere in templates. Sizes and positions in markup use SVG attributes or classes only.
- One cookie: `__Host-tutor_session` (Secure, HttpOnly, SameSite=Lax, Path=/; 14 days idle, 30 days absolute). Nothing personal in `localStorage`, `sessionStorage`, IndexedDB or the service-worker cache; HTMX `historyCacheSize` is 0; authenticated responses are `Cache-Control: no-store`.
- Every port call that reads or writes user data passes the session user's id (`current_user`); no route takes a user id from the request, except the test-only `POST /auth/test-login`.
- Learner text is data: never `|safe`, never `Markup(`, never linkified. Autoescape stays on.
- Copy: template text is es-MX and is the msgid; every string has an English translation in `src/tutor/web/locale/en/LC_MESSAGES/messages.po`; address the learner as "tú"; at most one exclamation mark per page; no shame copy (never "perdiste tu racha"); missed days read "sin sesión".
- The CEFR line is always labelled "opinión del modelo"; a level is stated only in the monthly checkpoint.
- The streak is the only game-like element. Celebrations fire for exactly two events: a newly closed session (first Inicio visit after it) and Annual confirmed on the return page.
- Motion: only `transform`, `opacity` and SVG `stroke-dashoffset` animate; ambient loops stop by themselves within 5 s; with `prefers-reduced-motion: reduce` or `data-reduce-motion` on `<body>`, every element shows its final state with no movement; no information is carried by motion alone.
- Touch targets ≥ 44 × 44 px; layout breakpoint 900 px (sidebar at ≥ 900 px, bottom tab bar below); no horizontal scroll at 320 px or 200% zoom.
- Payments: card only; Stripe Checkout (hosted) and Customer Portal; webhooks are the only writer of subscription state; events `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.deleted`; idempotent by event id.
- Privacy notice and terms wording is drafted from the requirements; the author reviews it before launch (it is legal text, not code).
- Code blocks in this plan are not pre-formatted: run `uv run just fmt` before `uv run just lint` in every task (some literal-heavy lines exceed 100 characters until ruff wraps them).
- A task is done when `uv run just check` passes; a branch is ready to merge when `uv run just test-e2e` also passes.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` when an agent writes them.

## Review Focus

1. **Learner text that looks like code** (HTML tags, `<script>`, spreadsheet formulas such as `=HYPERLINK(...)`, very long words) in user turns, prep labels, glossary meaning and context. Expected: shown as literal text on every page, wrapped without horizontal scroll, and neutralised with a leading `'` in CSV. Pinned by Task 5 (CSV), Task 22 (session detail escapes a `<script>` turn) and Task 23 (a glossary edit containing `<img src=x onerror=alert(1)>` round-trips as text).
2. **Webhooks that arrive twice, out of order, or for an unknown customer.** Expected: the final subscription state is the same as in-order delivery; a duplicate returns 200 without changes; an unknown customer returns 200 and is logged (so Stripe does not retry forever). Pinned by Task 1 and Task 17.
3. **Shared device after logout** (back button, bfcache, HTMX history). Expected: no personal page is shown from cache; the back button lands on login. Pinned by Task 10 (`no-store`), Task 10 (`historyCacheSize: 0`) and Task 30 (e2e: logout, back, login page shown).
4. **The learner's day boundary.** A learner in Mexico City at 21:00 is still on "today" even though UTC is tomorrow. Expected: Inicio's today node, the Free reset date and "due today" use the learner's timezone. Pinned by Task 2 and Task 21.
5. **Empty and extreme data:** a first-day learner on every page, a chart with one point or only gaps, a 300-character context sentence at 390 px. Expected: friendly empty states, no exceptions, no layout overflow. Pinned by Task 3, the first-day test in every page task (Tasks 20–28) and Task 30 (no horizontal scroll at 390 px).

## File structure

```
src/tutor/domain/dashboard/        pure view logic (mypy strict, 90% coverage)
  types.py        view dataclasses and enums (the contract)
  billing.py      banners, webhook state transitions
  home.py         local day, week trail, celebration rule
  charts.py       SVG chart geometry, meter, CEFR labels
  plan_diff.py    plan version diff
  glossary.py     text cleaning, filters, CSV export
  settings.py     admin setting parsing
src/tutor/web/
  ports.py        Protocols + WebDeps (the only way out of the web layer)
  config.py       WebConfig from env
  memory.py       in-memory adapters, FakeBilling, FakeGoogle, FixedClock
  demo.py         demo seed (Ana, Beto, Nuevo, Admin)
  demo_server.py  `just dashboard-demo`
  i18n.py         negotiation, catalogs, formatters
  assets.py       content-hashed static URLs
  views.py        per-language Jinja envs, render(), base context
  security.py     security headers, CSP, safe_next
  sessions.py     server-side session cookie middleware
  deps.py         current_user, require_csrf, require_admin
  google.py       Authlib Google OIDC adapter
  stripe_gateway.py  Stripe adapter (Task 16)
  routes/         one module per page: public, auth, billing, connect, home, sessions,
                  glossary, plan, progress, reports, account, admin, pwa
  templates/      base.html, layouts/, pages/, partials/
  static/         css/app.css, js/app.js, js/htmx.min.js, js/VENDORED.md, fonts/, icons/, sw.js source
  locale/en/LC_MESSAGES/messages.po
tests/unit/domain/test_dashboard_*.py
tests/unit/web/test_web_*.py, conftest.py
tests/e2e/                         Playwright (own marker, `just test-e2e`)
```

## Conventions for page tasks (Tasks 13–30)

**Route module.** One module per page in `src/tutor/web/routes/`, appended to `all_routers` in `routes/__init__.py`:

```python
router = APIRouter(dependencies=APP_ROUTER_DEPS)


@router.get("/app/glossary", response_class=HTMLResponse)
async def glossary_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    deps = get_deps(request)
    rows = deps.reader.glossary(user.id, GlossaryFilter(), today_for(request))
    template = "partials/glossary_table.html" if is_htmx(request) else "pages/glossary.html"
    return render(request, template, {"rows": rows})
```

Full pages and their HTMX partials share markup: the page template `{% include %}`s the partial. Every authenticated page sets `active_nav` and extends the app layout:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "glossary" %}
{% block title %}{{ _("Glosario") }} · English Tutor{% endblock %}
{% block content %}…{% endblock %}
```

**HTMX patterns.**
- Filters: `<form hx-get="…" hx-target="#…" hx-swap="outerHTML" hx-push-url="true" hx-trigger="change, input changed delay:300ms from:#q">` (the search input's id; never brackets in a trigger, because htmx treats `[...]` as an eval filter, which is disabled); the server returns the partial for `HX-Request`, the full page otherwise.
- Inline edits: a GET returns the edit form partial; a POST returns the row partial (200) or the form partial with field errors (422, swapped thanks to `responseHandling`). `hx-swap="outerHTML"`.
- Polling without JavaScript: the partial carries `hx-get="…?n={{ n + 1 }}" hx-trigger="every 2s" hx-swap="outerHTML"` while waiting; the final state is returned without `hx-trigger`, which stops the polling.
- Every form also works without JavaScript (plain `method="post"` + hidden `csrf_token`, server answers 303).

**Class vocabulary** (Task 13 styles exactly these; pages use only these plus page-specific classes they style in the same task's CSS section): `shell`, `sidebar`, `sidebar__nav`, `sidebar__foot`, `nav-link`, `nav-link--active`, `tabbar`, `tabbar__link`, `tabbar__link--active`, `tabbar__more`, `tabbar__menu`, `brand`, `brand--light`, `main`, `public-head`, `public-main`, `public-foot`, `page-head`, `page-head__actions`, `grid`, `card`, `card--wide`, `card--narrow`, `card__title`, `card__foot`, `banner`, `banner--warn`, `banner--info`, `chip`, `chip--free`, `chip--annual`, `chip--ok`, `chip--warn`, `chip--muted`, `btn`, `btn--primary`, `btn--ghost`, `btn--small`, `btn--block`, `btn--danger`, `copy`, `meter`, `meter__track`, `meter__fill`, `meter-link`, `kpi`, `kpi__value`, `kpi__label`, `kpi__target`, `trail`, `trail__path`, `trail__node`, `trail__node--done`, `trail__node--today`, `trail__node--upcoming`, `trail__node--missed`, `trail__node--rest`, `trail__extra`, `streak`, `stamps`, `stamp`, `stamp--used`, `table-wrap`, `table`, `filters`, `empty`, `muted`, `prose`, `form-row`, `form-error`, `inline-form`, `field-error`, `chart`, `chart__line`, `chart__target`, `chart__dot`, `as-table`, `said`, `correct`, `diff`, `diff--added`, `diff--removed`, `diff--moved`, `skeleton`, `error-box`, `skip-link`, `sr-only`, `icon`, `steps`, `code-copy`, `celebrate`.

**Motion hooks** (Task 15 implements them; pages only add the attributes):
- `data-count-to="27.0" data-count-decimals="1"` on an `aria-hidden="true"` span whose text is already the final formatted value; a sibling `sr-only` span carries the same value for screen readers.
- `data-draw` on an SVG `<path>`: drawn when scrolled into view.
- `data-pop` on a container: its `.stamp--used` children press in with a spring and a 60 ms stagger.
- `data-strike` on a session error item: "dijiste" strikes through, then "mejor" writes in.
- `data-grow` on an SVG `<rect>` meter fill: grows from zero.
- `data-celebrate` on an element rendered only when a celebration is due: one burst, then nothing.
- `data-copy="text"` on a button: copies, and the label (the last `<span>`, or the button text when the button has no child elements) becomes "Copiado" for 1.5 s; the word comes from `#live`'s `data-copied` attribute.
- `data-install` (Android/desktop install button, hidden until `beforeinstallprompt`), `data-ios-install` (iPhone guide, shown only in Safari on iOS outside standalone mode).
- View Transitions: `vt-today` and `vt-last-session` classes give the shared elements their `view-transition-name`.

**No plural forms.** Do not use `{% pluralize %}` or `ngettext`; write counts as "%(n)s de %(m)s" or as a label next to a number, so `missing_translations()` can check every string.

**Learner-local dates.** The `day` filter converts timezone-aware datetimes to the learner's timezone before formatting (Task 10), so pass `started_at` directly.

**Tests.** Use the fixtures in `tests/unit/web/conftest.py`: `login(demo.ana)` returns a logged-in `TestClient`; `csrf_of(client)` returns the CSRF token; send `headers={"hx-request": "true"}` to get a partial. Every page task tests at least: the normal state (Ana or Beto), the first-day state (Nuevo), the HTMX partial (no `<html` in the body), and English rendering for one string (`backend.users[demo.ana] = replace(user, lang=Lang.EN)`).

## Task index

| Part | Tasks |
| --- | --- |
| A — Domain | 1 View types and subscription state · 2 Week trail, local day, celebration · 3 Chart geometry · 4 Plan diff · 5 Glossary rules and CSV · 6 Setting parsing |
| B — Foundation | 7 Ports, config, in-memory backend · 8 Demo seed · 9 Localization · 10 App factory, views, headers, public pages · 11 Sessions, CSRF, login guard · 12 Google login, logout, language, demo |
| C — Look and install | 13 Design tokens, fonts, icons, layout CSS · 14 PWA: manifest, service worker, offline, install · 15 Motion layer and vendored HTMX |
| D — Payments | 16 Stripe gateway adapter · 17 Webhook route · 18 Pricing, checkout, portal and return pages · 19 Real-device check and ADR |
| E — Pages | 20 Conectar · 21 Inicio · 22 Sesiones · 23 Glosario · 24 Plan · 25 Progreso · 26 Cuenta · 27 Reportes · 28 Admin |
| F — Verification | 29 Route sweep (auth, isolation, CSRF, template safety) · 30 Browser tests, accessibility, budgets, CI |

---

## Part A — Dashboard domain (pure, mypy strict, 90% coverage)

Everything in Part A lives in `src/tutor/domain/dashboard/`, does no I/O and is covered by the `tutor/domain` coverage gate. The web layer (Part B onwards) only formats what these functions return.

### Task 1: View types and subscription state

**Files:**
- Create: `src/tutor/domain/dashboard/__init__.py`
- Create: `src/tutor/domain/dashboard/types.py`
- Create: `src/tutor/domain/dashboard/billing.py`
- Test: `tests/unit/domain/test_dashboard_billing.py`

**Interfaces:**
- Consumes: nothing.
- Produces: every dataclass and enum in `types.py` (the contract every later task uses, copy names exactly); `banners_for(sub: Subscription, usage: FreeUsage | None, today: date) -> tuple[Banner, ...]`; `next_subscription(current: Subscription, event: BillingEvent) -> Subscription`; constants `RENEWAL_NOTICE_DAYS = 14`, `LAPSED_NOTICE_DAYS = 30`.

- [ ] **Step 1: Write the types module**

`src/tutor/domain/dashboard/__init__.py`:

```python
"""Dashboard view logic (spec 2026-10-04-dashboard-design.md). Pure: no I/O."""
```

`src/tutor/domain/dashboard/types.py`:

```python
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
```

- [ ] **Step 2: Write the failing tests for banners and subscription transitions**

`tests/unit/domain/test_dashboard_billing.py`:

```python
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

from tutor.domain.dashboard.billing import banners_for, next_subscription
from tutor.domain.dashboard.types import (
    BannerKind,
    BillingEvent,
    BillingEventKind,
    FreeUsage,
    SubStatus,
    Subscription,
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_dashboard_billing.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.domain.dashboard.billing'`.

- [ ] **Step 4: Implement `billing.py`**

`src/tutor/domain/dashboard/billing.py`:

```python
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
    SubStatus,
    Subscription,
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
```

- [ ] **Step 5: Run the tests and the type check**

Run: `uv run pytest tests/unit/domain/test_dashboard_billing.py -q` then `uv run mypy`
Expected: all tests PASS; mypy reports no errors (strict on `tutor.domain`).

- [ ] **Step 6: Commit**

```bash
git add src/tutor/domain/dashboard tests/unit/domain/test_dashboard_billing.py
git commit -m "feat(dashboard): view types, banners and webhook state transitions"
```

### Task 2: Week trail, local day and celebration rule

**Files:**
- Create: `src/tutor/domain/dashboard/home.py`
- Test: `tests/unit/domain/test_dashboard_home.py`

**Interfaces:**
- Consumes: `PlannedDay`, `SessionMark`, `TrailNode`, `DayState`, `SessionStatus` from Task 1.
- Produces: `local_today(now: datetime, tz: str) -> date`; `week_start(day: date) -> date` (Monday); `build_week_trail(start: date, planned: Sequence[PlannedDay], marks: Sequence[SessionMark], today: date) -> tuple[TrailNode, ...]` (always 7 nodes); `should_celebrate(newest_closed: UUID | None, last_celebrated: UUID | None) -> bool`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/domain/test_dashboard_home.py`:

```python
from datetime import UTC, date, datetime
from uuid import uuid4

from tutor.domain.dashboard.home import (
    build_week_trail,
    local_today,
    should_celebrate,
    week_start,
)
from tutor.domain.dashboard.types import DayState, PlannedDay, SessionMark, SessionStatus

MON = date(2027, 1, 11)


def test_local_today_uses_learner_timezone_not_utc() -> None:
    late_evening_cdmx = datetime(2027, 1, 12, 3, 0, tzinfo=UTC)  # 21:00 on Jan 11 in CDMX
    assert local_today(late_evening_cdmx, "America/Mexico_City") == date(2027, 1, 11)
    assert local_today(late_evening_cdmx, "UTC") == date(2027, 1, 12)


def test_local_today_falls_back_to_utc_for_unknown_zone() -> None:
    assert local_today(datetime(2027, 1, 12, 3, tzinfo=UTC), "Mars/Base") == date(2027, 1, 12)


def test_week_start_is_monday() -> None:
    assert week_start(date(2027, 1, 17)) == MON  # Sunday
    assert week_start(MON) == MON


def test_trail_states_across_the_week() -> None:
    planned = [
        PlannedDay(date(2027, 1, 11), "Standup update"),
        PlannedDay(date(2027, 1, 12), "Code review"),
        PlannedDay(date(2027, 1, 13), "Demo"),
        PlannedDay(date(2027, 1, 15), "Interview"),
    ]
    marks = [
        SessionMark(date(2027, 1, 11), True, SessionStatus.CLOSED),
        SessionMark(date(2027, 1, 12), True, SessionStatus.INCOMPLETE),
        SessionMark(date(2027, 1, 12), False, SessionStatus.CLOSED),
        SessionMark(date(2027, 1, 12), False, SessionStatus.CLOSED),
    ]
    trail = build_week_trail(MON, planned, marks, today=date(2027, 1, 13))
    assert [n.state for n in trail] == [
        DayState.DONE,
        DayState.MISSED,  # an incomplete session does not count
        DayState.TODAY,
        DayState.REST,
        DayState.UPCOMING,
        DayState.REST,
        DayState.REST,
    ]
    assert trail[1].extra_sessions == 2
    assert trail[2].is_today and not trail[3].is_today
    assert trail[0].title == "Standup update" and trail[3].title is None


def test_off_plan_closed_session_on_rest_day_is_extra_not_done() -> None:
    marks = [SessionMark(date(2027, 1, 16), False, SessionStatus.CLOSED)]
    trail = build_week_trail(MON, [], marks, today=date(2027, 1, 17))
    assert trail[5].state is DayState.REST and trail[5].extra_sessions == 1
    assert trail[6].is_today and trail[6].state is DayState.REST


def test_planned_today_with_closed_session_is_done() -> None:
    planned = [PlannedDay(MON, "Standup")]
    marks = [SessionMark(MON, True, SessionStatus.CLOSED)]
    node = build_week_trail(MON, planned, marks, today=MON)[0]
    assert node.state is DayState.DONE and node.is_today


def test_should_celebrate_only_new_closed_sessions() -> None:
    a, b = uuid4(), uuid4()
    assert should_celebrate(a, None)
    assert should_celebrate(b, a)
    assert not should_celebrate(a, a)
    assert not should_celebrate(None, a)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/domain/test_dashboard_home.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.domain.dashboard.home'`.

- [ ] **Step 3: Implement**

`src/tutor/domain/dashboard/home.py`:

```python
"""Inicio: the learner's local day, the week trail and the celebration rule."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tutor.domain.dashboard.types import (
    DayState,
    PlannedDay,
    SessionMark,
    SessionStatus,
    TrailNode,
)


def local_today(now: datetime, tz: str) -> date:
    """The learner's calendar day. `now` must be timezone-aware."""
    try:
        zone: ZoneInfo | type[UTC] = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return now.astimezone(UTC).date()
    return now.astimezone(zone).date()


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_week_trail(
    start: date,
    planned: Sequence[PlannedDay],
    marks: Sequence[SessionMark],
    today: date,
) -> tuple[TrailNode, ...]:
    titles = {p.day: p.title for p in planned}
    nodes: list[TrailNode] = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        closed = [m for m in marks if m.day == day and m.status is SessionStatus.CLOSED]
        extra = sum(1 for m in closed if not m.on_plan)
        title = titles.get(day)
        if title is None:
            state = DayState.REST
        elif any(m.on_plan for m in closed):
            state = DayState.DONE
        elif day == today:
            state = DayState.TODAY
        elif day > today:
            state = DayState.UPCOMING
        else:
            state = DayState.MISSED
        nodes.append(TrailNode(day, state, day == today, title, extra))
    return tuple(nodes)


def should_celebrate(newest_closed: UUID | None, last_celebrated: UUID | None) -> bool:
    """Celebrate a finished session once: the newest closed session was not celebrated yet."""
    return newest_closed is not None and newest_closed != last_celebrated
```

Note: `ZoneInfo("")` raises `ValueError`; unknown names raise `ZoneInfoNotFoundError`. If mypy rejects the `ZoneInfo | type[UTC]` annotation, annotate `zone` as `ZoneInfo` and return early for the UTC fallback as written (the fallback path never assigns `zone`).

- [ ] **Step 4: Run tests and mypy**

Run: `uv run pytest tests/unit/domain/test_dashboard_home.py -q` then `uv run mypy`
Expected: PASS; no mypy errors.

- [ ] **Step 5: Commit**

```bash
git add src/tutor/domain/dashboard/home.py tests/unit/domain/test_dashboard_home.py
git commit -m "feat(dashboard): week trail, local day and celebration rule"
```

### Task 3: SVG chart geometry and CEFR labels

**Files:**
- Create: `src/tutor/domain/dashboard/charts.py`
- Test: `tests/unit/domain/test_dashboard_charts.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `LineChart` dataclass (`width: int, height: int, path: str, dots: tuple[tuple[float, float], ...], target_y: float | None, y_max: float`); `line_chart(values: Sequence[float | None], *, target: float | None = None, width: int = 320, height: int = 120, pad: int = 8) -> LineChart`; `meter_fraction(used: int, cap: int) -> float`; `cefr_label(value: float) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/domain/test_dashboard_charts.py`:

```python
import pytest

from tutor.domain.dashboard.charts import cefr_label, line_chart, meter_fraction


def test_two_points_scale_into_padded_box() -> None:
    chart = line_chart([10, 20])
    assert chart.path == "M8 64.7 L312 17.5"
    assert chart.dots == ((8, 64.7), (312, 17.5))
    assert chart.y_max == pytest.approx(22.0)


def test_single_point_is_centred() -> None:
    assert line_chart([5]).path == "M160 17.5"


def test_gaps_lift_the_pen() -> None:
    assert line_chart([1, None, 1]).path.count("M") == 2


def test_all_none_and_empty_give_empty_path() -> None:
    assert line_chart([None, None]).path == ""
    assert line_chart([]).path == ""


def test_all_zero_does_not_divide_by_zero() -> None:
    chart = line_chart([0, 0])
    assert chart.path == "M8 112 L312 112"
    assert chart.y_max == 1.0


def test_target_extends_the_scale_and_gets_a_y() -> None:
    chart = line_chart([20], target=25)
    assert chart.y_max == pytest.approx(27.5)
    assert chart.target_y == 17.5


@pytest.mark.parametrize(
    ("used", "cap", "expected"),
    [(2, 3, 2 / 3), (5, 3, 1.0), (0, 3, 0.0), (1, 0, 1.0), (-1, 3, 0.0)],
)
def test_meter_fraction_is_clamped(used: int, cap: int, expected: float) -> None:
    assert meter_fraction(used, cap) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "label"),
    [(3.0, "B1"), (3.2, "B1"), (3.4, "B1+"), (4.5, "B2+"), (5.0, "C1"), (0.2, "A1"), (9, "C2")],
)
def test_cefr_label_rounds_to_half_steps(value: float, label: str) -> None:
    assert cefr_label(value) == label
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/domain/test_dashboard_charts.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/tutor/domain/dashboard/charts.py`:

```python
"""Geometry for server-drawn SVG charts. The browser only animates the result."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")


@dataclass(frozen=True, slots=True)
class LineChart:
    width: int
    height: int
    path: str  # SVG path data; "" when there is nothing to draw
    dots: tuple[tuple[float, float], ...]
    target_y: float | None
    y_max: float


def line_chart(
    values: Sequence[float | None],
    *,
    target: float | None = None,
    width: int = 320,
    height: int = 120,
    pad: int = 8,
) -> LineChart:
    present = [v for v in values if v is not None]
    top = max([*present, *([target] if target is not None else []), 0.0])
    y_max = top * 1.1 if top > 0 else 1.0
    n = len(values)
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad

    def x(i: int) -> float:
        return pad + (inner_w / 2 if n == 1 else inner_w * i / (n - 1))

    def y(v: float) -> float:
        return pad + inner_h * (1 - v / y_max)

    parts: list[str] = []
    dots: list[tuple[float, float]] = []
    pen_down = False
    for i, v in enumerate(values):
        if v is None:
            pen_down = False
            continue
        px, py = round(x(i), 1), round(y(v), 1)
        parts.append(f"{'L' if pen_down else 'M'}{px:g} {py:g}")
        dots.append((px, py))
        pen_down = True
    target_y = None if target is None else round(y(target), 1)
    return LineChart(width, height, " ".join(parts), tuple(dots), target_y, y_max)


def meter_fraction(used: int, cap: int) -> float:
    if cap <= 0:
        return 1.0
    return min(max(used / cap, 0.0), 1.0)


def cefr_label(value: float) -> str:
    """Numeric level (1.0 = A1 … 6.0 = C2) to a half-step label such as "B1+"."""
    halves = min(max(round(value * 2), 2), 12)
    base, plus = divmod(halves, 2)
    return _LEVELS[base - 1] + ("+" if plus else "")
```

- [ ] **Step 4: Run tests and mypy**

Run: `uv run pytest tests/unit/domain/test_dashboard_charts.py -q` then `uv run mypy`
Expected: PASS; no mypy errors.

- [ ] **Step 5: Commit**

```bash
git add src/tutor/domain/dashboard/charts.py tests/unit/domain/test_dashboard_charts.py
git commit -m "feat(dashboard): SVG chart geometry, meter and CEFR labels"
```

### Task 4: Plan version diff

**Files:**
- Create: `src/tutor/domain/dashboard/plan_diff.py`
- Test: `tests/unit/domain/test_dashboard_plan_diff.py`

**Interfaces:**
- Consumes: `PlanItemView` from Task 1.
- Produces: `PlanDiff` dataclass (`added`, `removed`: `tuple[PlanItemView, ...]`; `moved: tuple[tuple[PlanItemView, PlanItemView], ...]` as (before, after); property `empty -> bool`); `diff_plan(old: Sequence[PlanItemView], new: Sequence[PlanItemView]) -> PlanDiff`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/domain/test_dashboard_plan_diff.py`:

```python
from tutor.domain.dashboard.plan_diff import diff_plan
from tutor.domain.dashboard.types import ItemStatus, PlanItemView, Skill


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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/domain/test_dashboard_plan_diff.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/tutor/domain/dashboard/plan_diff.py`:

```python
"""What changed between two plan versions (requirements section 9: the user sees the diff)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tutor.domain.dashboard.types import PlanItemView


@dataclass(frozen=True, slots=True)
class PlanDiff:
    added: tuple[PlanItemView, ...]
    removed: tuple[PlanItemView, ...]
    moved: tuple[tuple[PlanItemView, PlanItemView], ...]  # (before, after)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.moved)


def _pos(item: PlanItemView) -> tuple[int, int]:
    return (item.week_no, item.order_no)


def diff_plan(old: Sequence[PlanItemView], new: Sequence[PlanItemView]) -> PlanDiff:
    pool: dict[str, list[PlanItemView]] = {}
    for item in sorted(old, key=_pos):
        pool.setdefault(item.key, []).append(item)
    added: list[PlanItemView] = []
    moved: list[tuple[PlanItemView, PlanItemView]] = []
    for item in sorted(new, key=_pos):
        bucket = pool.get(item.key)
        if bucket:
            before = bucket.pop(0)
            if _pos(before) != _pos(item):
                moved.append((before, item))
        else:
            added.append(item)
    removed = sorted((i for bucket in pool.values() for i in bucket), key=_pos)
    return PlanDiff(tuple(added), tuple(removed), tuple(moved))
```

- [ ] **Step 4: Run tests and mypy**

Run: `uv run pytest tests/unit/domain/test_dashboard_plan_diff.py -q` then `uv run mypy`
Expected: PASS; no mypy errors.

- [ ] **Step 5: Commit**

```bash
git add src/tutor/domain/dashboard/plan_diff.py tests/unit/domain/test_dashboard_plan_diff.py
git commit -m "feat(dashboard): plan version diff"
```

### Task 5: Glossary text cleaning, filtering and CSV export

**Files:**
- Create: `src/tutor/domain/dashboard/glossary.py`
- Test: `tests/unit/domain/test_dashboard_glossary.py`

**Interfaces:**
- Consumes: `GlossaryRow`, `GlossaryFilter`, `GlossaryKind`, `GlossaryStatus`, `DueFilter` from Task 1.
- Produces: `MEANING_MAX = 200`, `CONTEXT_MAX = 300`; `class TextError(ValueError)` with attribute `code: str` (`"empty"` or `"too_long"`); `clean_user_text(value: str, *, max_len: int, required: bool) -> str`; `fold(text: str) -> str`; `filter_glossary(rows: Sequence[GlossaryRow], f: GlossaryFilter, today: date) -> tuple[GlossaryRow, ...]`; `glossary_csv(rows: Sequence[GlossaryRow]) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/domain/test_dashboard_glossary.py`:

```python
import csv
import io
from datetime import date
from uuid import uuid4

import pytest

from tutor.domain.dashboard.glossary import (
    CONTEXT_MAX,
    TextError,
    clean_user_text,
    filter_glossary,
    glossary_csv,
)
from tutor.domain.dashboard.types import (
    DueFilter,
    GlossaryFilter,
    GlossaryKind,
    GlossaryRow,
    GlossaryStatus,
)

TODAY = date(2027, 1, 12)


def row(text: str, **kw: object) -> GlossaryRow:
    base: dict[str, object] = {
        "id": uuid4(),
        "kind": GlossaryKind.CHUNK,
        "text": text,
        "meaning": "",
        "context_sentence": f"I said {text}.",
        "domain": "it",
        "status": GlossaryStatus.CONFIRMED,
        "due_on": None,
        "leech": False,
        "expires_on": None,
    }
    base.update(kw)
    return GlossaryRow(**base)  # type: ignore[arg-type]


def test_clean_collapses_whitespace_and_strips_control_characters() -> None:
    assert clean_user_text("  a\tb​  c\x00 ", max_len=50, required=True) == "a b c"


def test_clean_rejects_empty_when_required_and_too_long() -> None:
    with pytest.raises(TextError) as empty:
        clean_user_text("   ", max_len=10, required=True)
    assert empty.value.code == "empty"
    assert clean_user_text("   ", max_len=10, required=False) == ""
    with pytest.raises(TextError) as long:
        clean_user_text("x" * (CONTEXT_MAX + 1), max_len=CONTEXT_MAX, required=True)
    assert long.value.code == "too_long"


def test_clean_keeps_html_as_plain_text() -> None:
    assert clean_user_text("<b>trade-off</b>", max_len=50, required=True) == "<b>trade-off</b>"


def test_filters_combine() -> None:
    rows = [
        row("trade-off", due_on=TODAY),
        row("ship it", kind=GlossaryKind.TERM, due_on=date(2027, 1, 18)),
        row("rollback", domain="business", due_on=date(2027, 1, 30)),
        row("blocker", status=GlossaryStatus.PROVISIONAL),
    ]
    by_due = filter_glossary(rows, GlossaryFilter(due=DueFilter.TODAY), TODAY)
    assert [r.text for r in by_due] == ["trade-off"]
    week = filter_glossary(rows, GlossaryFilter(due=DueFilter.WEEK), TODAY)
    assert [r.text for r in week] == ["trade-off", "ship it"]
    assert [r.text for r in filter_glossary(rows, GlossaryFilter(domain="business"), TODAY)] == [
        "rollback"
    ]
    provisional = GlossaryFilter(status=GlossaryStatus.PROVISIONAL)
    assert [r.text for r in filter_glossary(rows, provisional, TODAY)] == ["blocker"]


def test_search_ignores_case_and_accents_across_fields() -> None:
    rows = [row("follow up", meaning="dar seguimiento"), row("deadline", meaning="fecha límite")]
    assert [r.text for r in filter_glossary(rows, GlossaryFilter(q="LIMITE"), TODAY)] == [
        "deadline"
    ]


def test_sort_is_due_first_then_text() -> None:
    rows = [row("b"), row("a"), row("z", due_on=TODAY)]
    assert [r.text for r in filter_glossary(rows, GlossaryFilter(), TODAY)] == ["z", "a", "b"]


def test_csv_has_bom_header_and_neutralises_formulas() -> None:
    out = glossary_csv([row('=HYPERLINK("http://x")', meaning="@cmd", due_on=TODAY)])
    assert out.startswith("﻿")
    parsed = list(csv.reader(io.StringIO(out.lstrip("﻿"))))
    assert parsed[0] == [
        "text",
        "kind",
        "meaning",
        "context_sentence",
        "domain",
        "status",
        "due_on",
        "leech",
    ]
    assert parsed[1][0] == '\'=HYPERLINK("http://x")'
    assert parsed[1][2] == "'@cmd"
    assert parsed[1][6] == "2027-01-12"
    assert parsed[1][7] == "false"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/domain/test_dashboard_glossary.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/tutor/domain/dashboard/glossary.py`:

```python
"""Glossary editing rules, filters and CSV export (requirements section 10)."""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import Sequence
from datetime import date, timedelta

from tutor.domain.dashboard.types import DueFilter, GlossaryFilter, GlossaryRow

MEANING_MAX = 200
CONTEXT_MAX = 300
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_CSV_HEADER = (
    "text",
    "kind",
    "meaning",
    "context_sentence",
    "domain",
    "status",
    "due_on",
    "leech",
)


class TextError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def clean_user_text(value: str, *, max_len: int, required: bool) -> str:
    """Normalise learner-typed text. It stays data: HTML is kept as literal text."""
    text = unicodedata.normalize("NFC", value)
    text = "".join(" " if ch.isspace() else ch for ch in text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf"))
    text = " ".join(text.split())
    if required and not text:
        raise TextError("empty")
    if len(text) > max_len:
        raise TextError("too_long")
    return text


def fold(text: str) -> str:
    """Case- and accent-insensitive form for search."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def filter_glossary(
    rows: Sequence[GlossaryRow], f: GlossaryFilter, today: date
) -> tuple[GlossaryRow, ...]:
    needle = fold(f.q.strip())
    horizon = today if f.due is DueFilter.TODAY else today + timedelta(days=6)

    def keep(r: GlossaryRow) -> bool:
        if f.kind is not None and r.kind is not f.kind:
            return False
        if f.domain is not None and r.domain != f.domain:
            return False
        if f.status is not None and r.status is not f.status:
            return False
        if f.due is not None and (r.due_on is None or r.due_on > horizon):
            return False
        return not needle or needle in fold(f"{r.text} {r.meaning} {r.context_sentence}")

    def order(r: GlossaryRow) -> tuple[bool, date, str]:
        return (r.due_on is None, r.due_on or date.max, fold(r.text))

    return tuple(sorted(filter(keep, rows), key=order))


def _cell(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def glossary_csv(rows: Sequence[GlossaryRow]) -> str:
    """UTF-8 CSV with a BOM so Excel shows accents; formula-like cells are neutralised."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADER)
    for r in rows:
        writer.writerow(
            [
                _cell(r.text),
                r.kind.value,
                _cell(r.meaning),
                _cell(r.context_sentence),
                r.domain,
                r.status.value,
                r.due_on.isoformat() if r.due_on else "",
                "true" if r.leech else "false",
            ]
        )
    return "﻿" + buf.getvalue()
```

- [ ] **Step 4: Run tests and mypy**

Run: `uv run pytest tests/unit/domain/test_dashboard_glossary.py -q` then `uv run mypy`
Expected: PASS; no mypy errors.

- [ ] **Step 5: Commit**

```bash
git add src/tutor/domain/dashboard/glossary.py tests/unit/domain/test_dashboard_glossary.py
git commit -m "feat(dashboard): glossary text rules, filters and safe CSV export"
```

### Task 6: Admin setting parsing

**Files:**
- Create: `src/tutor/domain/dashboard/settings.py`
- Test: `tests/unit/domain/test_dashboard_settings.py`

**Interfaces:**
- Consumes: `SettingType` (Task 1), `clean_user_text`, `TextError` (Task 5).
- Produces: `class SettingError(ValueError)`; `parse_setting(kind: SettingType, raw: str) -> str` returning the canonical string.

- [ ] **Step 1: Write the failing tests**

`tests/unit/domain/test_dashboard_settings.py`:

```python
import pytest

from tutor.domain.dashboard.settings import SettingError, parse_setting
from tutor.domain.dashboard.types import SettingType


@pytest.mark.parametrize(
    ("kind", "raw", "canonical"),
    [
        (SettingType.INT, " 12 ", "12"),
        (SettingType.INT, "-3", "-3"),
        (SettingType.FLOAT, "0.85", "0.85"),
        (SettingType.FLOAT, "1", "1.0"),
        (SettingType.BOOL, "Sí", "true"),
        (SettingType.BOOL, "0", "false"),
        (SettingType.STR, "  hola   mundo ", "hola mundo"),
    ],
)
def test_valid_values_are_canonicalised(kind: SettingType, raw: str, canonical: str) -> None:
    assert parse_setting(kind, raw) == canonical


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        (SettingType.INT, "1.5"),
        (SettingType.INT, ""),
        (SettingType.FLOAT, "0,85"),
        (SettingType.FLOAT, "nan"),
        (SettingType.BOOL, "maybe"),
        (SettingType.STR, "x" * 201),
    ],
)
def test_invalid_values_raise(kind: SettingType, raw: str) -> None:
    with pytest.raises(SettingError):
        parse_setting(kind, raw)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/domain/test_dashboard_settings.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/tutor/domain/dashboard/settings.py`:

```python
"""Validation for the admin settings table (requirements section 14)."""

from __future__ import annotations

import re

from tutor.domain.dashboard.glossary import TextError, clean_user_text
from tutor.domain.dashboard.types import SettingType

_INT = re.compile(r"-?\d+")
_FLOAT = re.compile(r"-?\d+(\.\d+)?")
_TRUE = {"true", "1", "si", "sí", "yes"}
_FALSE = {"false", "0", "no"}
STR_MAX = 200


class SettingError(ValueError):
    pass


def parse_setting(kind: SettingType, raw: str) -> str:
    value = raw.strip()
    match kind:
        case SettingType.INT:
            if not _INT.fullmatch(value):
                raise SettingError(kind)
            return str(int(value))
        case SettingType.FLOAT:
            if not _FLOAT.fullmatch(value):
                raise SettingError(kind)
            return str(float(value))
        case SettingType.BOOL:
            lowered = value.casefold()
            if lowered in _TRUE:
                return "true"
            if lowered in _FALSE:
                return "false"
            raise SettingError(kind)
        case SettingType.STR:
            try:
                return clean_user_text(raw, max_len=STR_MAX, required=False)
            except TextError as exc:
                raise SettingError(kind) from exc
```

- [ ] **Step 4: Run tests, mypy and the domain coverage gate**

Run: `uv run pytest tests/unit/domain -q --cov=tutor --cov-report=term` then `uv run coverage report --include="src/tutor/domain/*" --fail-under=90` then `uv run mypy`
Expected: PASS; coverage of `src/tutor/domain/dashboard/*` ≥ 90%; no mypy errors.

- [ ] **Step 5: Commit**

```bash
git add src/tutor/domain/dashboard/settings.py tests/unit/domain/test_dashboard_settings.py
git commit -m "feat(dashboard): admin setting parsing"
```

## Part B — Web foundation

### Task 7: Dependencies, ports, config and the in-memory backend

The web layer depends only on the ports defined here. Production adapters for the Postgres-backed ports are written in the follow-up plan (see "Scope of this plan"); this task ships the in-memory implementation used by every test and by the local demo.

**Files:**
- Modify: `pyproject.toml` (dependencies, mypy override)
- Create: `src/tutor/web/__init__.py`
- Create: `src/tutor/web/ports.py`
- Create: `src/tutor/web/config.py`
- Create: `src/tutor/web/memory.py`
- Test: `tests/unit/web/test_web_memory.py`, `tests/unit/web/test_web_config.py`

**Interfaces:**
- Consumes: all types from Task 1; `next_subscription` (Task 1); `filter_glossary` (Task 5); `week_start` (Task 2).
- Produces:
  - `tutor.web.ports`: `WebSession` (mutable dataclass: `token_hash: str, user_id: UUID | None, csrf_token: str, created_at: datetime, last_seen_at: datetime, data: dict[str, Any]`), `GoogleIdentity(sub, email, name, email_verified)`, `class LoginFailed(Exception)`, `class InvalidSignature(Exception)`, `Clock = Callable[[], datetime]`, Protocols `UserDirectory`, `WebSessionStore`, `DashboardReader`, `GlossaryEditor`, `AccountService`, `SettingsStore`, `SubscriptionStore`, `BillingGateway`, `GoogleLogin`, and the frozen dataclass `WebDeps` (fields `users, sessions, reader, glossary, account, settings, subscriptions, billing, google, clock`).
  - `tutor.web.config.WebConfig` (frozen: `env: Literal["dev", "test", "prod"]`, `base_url: str`, `mcp_url: str`, `support_email: str`, `test_login: bool = False`, `session_idle_days: int = 14`, `session_max_days: int = 30`) with `WebConfig.from_env(environ: Mapping[str, str]) -> WebConfig`.
  - `tutor.web.memory`: `FixedClock(now)` (callable, `.advance(delta)`), `MemoryBackend` (implements every data port; method names are unique across ports so one object can play all of them), `FakeBilling`, `FakeGoogle`, `memory_deps(backend: MemoryBackend, clock: Clock, *, billing: BillingGateway | None = None, google: GoogleLogin | None = None) -> WebDeps`, `empty_home(today: date) -> HomeData`, `empty_progress() -> ProgressData`.

- [ ] **Step 1: Add the dependencies**

Run (skip any package the core loop already added; keep the newer floor if one exists):

```bash
uv add "fastapi>=0.142.2" "jinja2>=3.1" "babel>=2.18" "python-multipart>=0.0.32" "authlib>=1.8.0" "httpx>=0.28.1" "stripe>=16.0.0" uvicorn
uv add --dev "playwright>=1.63" "pytest-playwright>=0.9.0" "axe-playwright-python>=0.1.8"
```

Append to `pyproject.toml` after the existing `[[tool.mypy.overrides]]` block:

```toml
# Authlib ships no type information.
[[tool.mypy.overrides]]
module = ["authlib", "authlib.*"]
ignore_missing_imports = true
```

Run: `uv run just check-fast`
Expected: PASS (nothing uses the new packages yet).

- [ ] **Step 2: Write `ports.py`**

`src/tutor/web/__init__.py`:

```python
"""Web dashboard: server-rendered pages over the dashboard ports (spec 2026-10-04)."""
```

`src/tutor/web/ports.py`:

```python
"""What the web layer needs from the rest of the system.

Routes call these ports only; they never open a database session. Every method that
takes a `user_id` must return only that user's data (requirements section 5).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
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

    def save_session(self, session: WebSession) -> None: ...

    def delete_session(self, token_hash: str) -> None: ...

    def delete_user_sessions(self, user_id: UUID) -> None: ...


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
```

- [ ] **Step 3: Write the config test and `config.py`**

`tests/unit/web/test_web_config.py`:

```python
import pytest

from tutor.web.config import WebConfig

ENV = {
    "TUTOR_ENV": "prod",
    "TUTOR_BASE_URL": "https://tutor.example.com/",
    "TUTOR_MCP_URL": "https://tutor.example.com/mcp",
    "TUTOR_SUPPORT_EMAIL": "soporte@example.com",
}


def test_from_env_strips_trailing_slash() -> None:
    config = WebConfig.from_env(ENV)
    assert config.base_url == "https://tutor.example.com"
    assert config.env == "prod" and not config.test_login


def test_test_login_is_refused_outside_test_env() -> None:
    with pytest.raises(ValueError, match="TUTOR_ENV=test"):
        WebConfig.from_env({**ENV, "TUTOR_TEST_LOGIN": "1"})


def test_test_login_allowed_in_test_env() -> None:
    assert WebConfig.from_env({**ENV, "TUTOR_ENV": "test", "TUTOR_TEST_LOGIN": "1"}).test_login


def test_unknown_env_is_refused() -> None:
    with pytest.raises(ValueError, match="TUTOR_ENV"):
        WebConfig.from_env({**ENV, "TUTOR_ENV": "staging"})
```

`src/tutor/web/config.py`:

```python
"""Web settings from environment variables. Secrets are read by the adapters, not here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

_ENVS = ("dev", "test", "prod")


@dataclass(frozen=True)
class WebConfig:
    env: Literal["dev", "test", "prod"]
    base_url: str
    mcp_url: str
    support_email: str
    test_login: bool = False
    session_idle_days: int = 14
    session_max_days: int = 30

    def __post_init__(self) -> None:
        if self.env not in _ENVS:
            raise ValueError(f"TUTOR_ENV must be one of {_ENVS}")
        if self.test_login and self.env != "test":
            raise ValueError("test login is only allowed with TUTOR_ENV=test")

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> WebConfig:
        env = environ.get("TUTOR_ENV", "dev")
        if env not in _ENVS:
            raise ValueError(f"TUTOR_ENV must be one of {_ENVS}")
        return cls(
            env=cast(Literal["dev", "test", "prod"], env),
            base_url=environ["TUTOR_BASE_URL"].rstrip("/"),
            mcp_url=environ["TUTOR_MCP_URL"],
            support_email=environ["TUTOR_SUPPORT_EMAIL"],
            test_login=environ.get("TUTOR_TEST_LOGIN") == "1",
        )
```

Run: `uv run pytest tests/unit/web/test_web_config.py -q`
Expected: PASS (4 tests).

- [ ] **Step 4: Write the failing memory-backend tests**

`tests/unit/web/test_web_memory.py`:

```python
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
```

Run: `uv run pytest tests/unit/web/test_web_memory.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.web.memory'`.

- [ ] **Step 5: Implement `memory.py`**

`src/tutor/web/memory.py`:

```python
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
    Setting,
    SubStatus,
    Subscription,
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

    def save_session(self, session: WebSession) -> None:
        self.web_sessions[session.token_hash] = session

    def delete_session(self, token_hash: str) -> None:
        self.web_sessions.pop(token_hash, None)

    def delete_user_sessions(self, user_id: UUID) -> None:
        for key in [k for k, s in self.web_sessions.items() if s.user_id == user_id]:
            del self.web_sessions[key]

    # --- DashboardReader --------------------------------------------------

    def home(self, user_id: UUID, today: date) -> HomeData:
        home = self.homes.get(user_id, empty_home(today))
        celebrated = self.celebrated.get(user_id, home.last_celebrated_session_id)
        return replace(home, last_celebrated_session_id=celebrated)

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
                return detail
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

    def has_any_session(self, user_id: UUID) -> bool:
        return bool(self.session_rows.get(user_id))

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
```

- [ ] **Step 6: Run the tests, lint and types**

Run: `uv run pytest tests/unit/web -q` then `uv run just lint`
Expected: PASS. If ruff reformats long literal lines in the test file, run `uv run just fmt` and re-run.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/tutor/web tests/unit/web
git commit -m "feat(web): dashboard ports, config and in-memory backend"
```

### Task 8: Demo data seed

**Files:**
- Create: `src/tutor/web/demo.py`
- Test: `tests/unit/web/test_web_demo.py`

**Interfaces:**
- Consumes: `MemoryBackend` (Task 7), all view types (Task 1).
- Produces: `DemoUsers` frozen dataclass (`ana: UUID` Free with a normal week, `beto: UUID` Annual with a renewal in 10 days and reports, `nuevo: UUID` first day with nothing, `admin: UUID` admin role); `seed_demo(backend: MemoryBackend, today: date) -> DemoUsers`. Every later web test uses these four users.

- [ ] **Step 1: Write the failing test**

`tests/unit/web/test_web_demo.py`:

```python
from datetime import date

from tutor.domain.dashboard.types import GlossaryFilter, Role, SubStatus, Tier
from tutor.web.demo import seed_demo
from tutor.web.memory import MemoryBackend

TODAY = date(2027, 1, 12)


def test_seed_creates_four_distinct_situations() -> None:
    backend = MemoryBackend()
    demo = seed_demo(backend, TODAY)
    assert len({demo.ana, demo.beto, demo.nuevo, demo.admin}) == 4

    ana_home = backend.home(demo.ana, TODAY)
    assert ana_home.has_connected and ana_home.has_plan and ana_home.today is not None
    assert backend.usage(demo.ana, TODAY).sessions_this_week == 2
    assert len(backend.glossary(demo.ana, GlossaryFilter(), TODAY)) >= 6
    assert backend.subscription(demo.ana).tier is Tier.FREE

    beto = backend.subscription(demo.beto)
    assert (beto.tier, beto.status) == (Tier.ANNUAL, SubStatus.ACTIVE)
    assert backend.reports(demo.beto).weekly

    assert not backend.home(demo.nuevo, TODAY).has_connected
    assert backend.plan(demo.nuevo) is None

    admin = backend.find_user(demo.admin)
    assert admin is not None and admin.role is Role.ADMIN
    assert {s.key for s in backend.list_settings()} >= {"free_sessions_per_week"}
```

Run: `uv run pytest tests/unit/web/test_web_demo.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Implement `demo.py`**

`src/tutor/web/demo.py`:

```python
"""Demo data for tests, screenshots and `just dashboard-demo`. Fictional people only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

from tutor.domain.dashboard.home import week_start
from tutor.domain.dashboard.types import (
    Checkpoint,
    Client,
    ConnectedClient,
    Confidence,
    CefrOpinion,
    ErrorCategory,
    ErrorView,
    GlossaryKind,
    GlossaryRow,
    GlossaryStatus,
    HomeData,
    ItemStatus,
    Lang,
    Mode,
    PlannedDay,
    PlanItemView,
    PlanPage,
    PlanVersion,
    Profile,
    ProgressData,
    ReportNumbers,
    ReportsPage,
    Role,
    Series,
    SessionDetail,
    SessionMark,
    SessionStatus,
    SessionSummary,
    Setting,
    SettingType,
    Skill,
    Stamp,
    SubStatus,
    Subscription,
    TaskResult,
    Tier,
    TodayItem,
    User,
    WeeklyReport,
)
from tutor.web.memory import MemoryBackend

TZ = "America/Mexico_City"


@dataclass(frozen=True)
class DemoUsers:
    ana: UUID
    beto: UUID
    nuevo: UUID
    admin: UUID


def _at(day: date, hour: int = 15) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


def _session(day: date, label: str, mode: Mode, wpm: float, status: SessionStatus) -> SessionDetail:
    summary = SessionSummary(
        id=uuid4(),
        started_at=_at(day),
        mode=mode,
        client=Client.CLAUDE,
        label=label,
        duration_min=16,
        words_per_min=wpm,
        task_result=TaskResult.ACHIEVED if status is SessionStatus.CLOSED else None,
        status=status,
        low_trust=False,
    )
    return SessionDetail(
        summary=summary,
        errors=(
            ErrorView(
                "I have went to the meeting", "I went to the meeting", ErrorCategory.GRAMMAR, True
            ),
            ErrorView("we must to deploy", "we must deploy", ErrorCategory.GRAMMAR, False),
        ),
        hints_given=1,
        chunks_offered=("on track", "a quick heads-up", "trade-off"),
        chunks_used=("on track",),
        cefr=CefrOpinion("B1+", Confidence.MEDIUM, ("I went to the meeting", "we are on track")),
        confidence_1_5=3,
        user_turns=(
            "Yesterday I have went to the meeting with the client.",
            "We are on track for the release, but we must to deploy on Friday.",
        ),
    )


def _plan(today: date) -> PlanPage:
    def item(week: int, order: int, key: str, title: str, status: ItemStatus) -> PlanItemView:
        return PlanItemView(week, order, key, title, Skill.SPEAKING, "it", "Daily standup", status)

    v1 = (
        item(
            1, 1, "s1:it:speaking", "Dar una actualización de estado en el standup", ItemStatus.DONE
        ),
        item(1, 2, "s2:it:speaking", "Explicar un bloqueo técnico", ItemStatus.PENDING),
        item(
            2,
            1,
            "s3:it:speaking",
            "Pedir aclaraciones en una revisión de código",
            ItemStatus.PENDING,
        ),
    )
    v2 = (
        v1[0],
        item(
            1,
            2,
            "s3:it:speaking",
            "Pedir aclaraciones en una revisión de código",
            ItemStatus.PENDING,
        ),
        item(2, 1, "s2:it:speaking", "Explicar un bloqueo técnico", ItemStatus.PENDING),
        item(2, 2, "s4:it:speaking", "Presentar una demo al cliente", ItemStatus.PENDING),
    )
    return PlanPage(
        current_week_no=1,
        rationale="Tu diagnóstico mostró que te cuesta hablar de bloqueos; empezamos por ahí.",
        realism_message=None,
        versions=(
            PlanVersion(2, _at(today - timedelta(days=2)), v2),
            PlanVersion(1, _at(today - timedelta(days=9)), v1),
        ),
    )


def _progress(today: date) -> ProgressData:
    days = tuple(today - timedelta(days=3 * i) for i in range(9, -1, -1))
    weeks = tuple(week_start(today) - timedelta(weeks=i) for i in range(3, -1, -1))
    return ProgressData(
        sessions_counted=10,
        wpm_voice=Series(days, (18, 19, None, 21, 22, 22, 24, 23, 25, 27), 25),
        wpm_text=Series(days, (9, 10, 10, 11, 11, None, 12, 12, 13, 13), 12),
        errors_per_100w=Series(days, (6.2, 5.8, 5.9, 5.1, 4.8, 4.4, 4.1, 3.9, 3.4, 3.1)),
        activation_rate=Series(days, (0.2, 0.25, 0.3, 0.3, 0.33, 0.35, 0.38, 0.4, 0.41, 0.42), 0.4),
        minutes_spoken=Series(weeks, (32, 41, 45, 30)),
        sessions_done=Series(weeks, (3, 4, 4, 2)),
        sessions_planned=Series(weeks, (4, 4, 4, 4)),
        cefr_trend=Series(days, (3.3, 3.4, 3.4, 3.5, 3.5, 3.5, 3.6, 3.6, 3.6, 3.7)),
        cefr_excluded=(days[4],),
        confidence_trend=Series(days, (2, 2, 3, 3, 3, 3, 3, 4, 3, 4)),
        recurring_stopped=("I went to the meeting", "we must deploy"),
        parked_leeches=("since three years",),
        checkpoint=Checkpoint(today - timedelta(days=20), "B1+", "B1+"),
    )


def _glossary(today: date) -> list[GlossaryRow]:
    def row(text: str, kind: GlossaryKind, meaning: str, ctx: str, **kw: object) -> GlossaryRow:
        base: dict[str, object] = {
            "id": uuid4(),
            "kind": kind,
            "text": text,
            "meaning": meaning,
            "context_sentence": ctx,
            "domain": "it",
            "status": GlossaryStatus.CONFIRMED,
            "due_on": None,
            "leech": False,
            "expires_on": None,
        }
        base.update(kw)
        return GlossaryRow(**base)  # type: ignore[arg-type]

    return [
        row(
            "on track", GlossaryKind.CHUNK, "en tiempo", "We are on track for Friday.", due_on=today
        ),
        row(
            "a quick heads-up",
            GlossaryKind.CHUNK,
            "un aviso rápido",
            "Just a quick heads-up: the build is red.",
            due_on=today,
        ),
        row(
            "trade-off",
            GlossaryKind.TERM,
            "compromiso",
            "The trade-off is speed versus safety.",
            due_on=today + timedelta(days=3),
        ),
        row(
            "I went to the meeting",
            GlossaryKind.CORRECTION,
            "",
            "Yesterday I went to the meeting.",
            due_on=today + timedelta(days=1),
            leech=True,
        ),
        row(
            "follow up",
            GlossaryKind.CHUNK,
            "dar seguimiento",
            "I'll follow up with QA.",
            domain="business",
        ),
        row(
            "blocker",
            GlossaryKind.TERM,
            "impedimento",
            "My blocker is the staging DB.",
            status=GlossaryStatus.PROVISIONAL,
            expires_on=today + timedelta(days=5),
        ),
        row(
            "since three years",
            GlossaryKind.CORRECTION,
            "",
            "I have worked here for three years.",
            status=GlossaryStatus.ARCHIVED,
        ),
    ]


def _settings(now: datetime) -> dict[str, Setting]:
    rows = (
        ("free_sessions_per_week", "3", SettingType.INT, "Sesiones por semana en Free"),
        ("free_glossary_items", "50", SettingType.INT, "Elementos de glosario en Free"),
        ("review_cap_per_session", "8", SettingType.INT, "Repasos por sesión"),
        ("fsrs_retention_target", "0.85", SettingType.FLOAT, "Retención objetivo FSRS"),
        ("weekly_report_enabled", "true", SettingType.BOOL, "Enviar reporte semanal"),
    )
    return {k: Setting(k, v, t, d, now) for k, v, t, d in rows}


def seed_demo(backend: MemoryBackend, today: date) -> DemoUsers:
    monday = week_start(today)
    now = _at(today, 12)

    ana = User(uuid4(), "Ana", Role.LEARNER, Lang.ES_MX, TZ, False)
    beto = User(uuid4(), "Beto", Role.LEARNER, Lang.ES_MX, TZ, False)
    nuevo = User(uuid4(), "Nuevo", Role.LEARNER, Lang.ES_MX, TZ, False)
    admin = User(uuid4(), "Admin", Role.ADMIN, Lang.ES_MX, TZ, False)
    for user in (ana, beto, nuevo, admin):
        backend.add_user(user, google_sub=f"demo-{user.display_name.lower()}")

    for user in (ana, beto):
        sessions = [
            _session(
                monday,
                "Dar una actualización de estado en el standup",
                Mode.VOICE,
                27,
                SessionStatus.CLOSED,
            ),
            _session(
                monday - timedelta(days=3),
                "Explicar un bloqueo técnico",
                Mode.TEXT,
                13,
                SessionStatus.CLOSED,
            ),
            _session(
                monday - timedelta(days=5),
                "prepare me for tomorrow's demo",
                Mode.VOICE,
                19,
                SessionStatus.INCOMPLETE,
            ),
        ]
        backend.session_rows[user.id] = sessions
        planned = (
            PlannedDay(monday, "Dar una actualización de estado en el standup"),
            PlannedDay(monday + timedelta(days=1), "Pedir aclaraciones en una revisión de código"),
            PlannedDay(monday + timedelta(days=3), "Explicar un bloqueo técnico"),
            PlannedDay(monday + timedelta(days=4), "Presentar una demo al cliente"),
        )
        report = ReportNumbers(monday - timedelta(days=7), 3, 4, 45, 2, 3)
        backend.homes[user.id] = HomeData(
            has_connected=True,
            has_plan=True,
            today=TodayItem(
                "Pedir aclaraciones en una revisión de código",
                "Tu compañero dejó 12 comentarios en tu PR",
                Skill.SPEAKING,
                "it",
            ),
            week_start=monday,
            planned_days=planned,
            session_marks=(SessionMark(monday, True, SessionStatus.CLOSED),),
            streak=4,
            stamps=(
                Stamp("on track", True),
                Stamp("a quick heads-up", False),
                Stamp("trade-off", False),
            ),
            last_session=sessions[0].summary,
            reviews_due=5,
            provisional_items=1,
            latest_report=report if user is beto else None,
            newest_closed_session_id=sessions[0].summary.id,
            last_celebrated_session_id=None,
        )
        backend.sessions_this_week[user.id] = 2
        backend.plans[user.id] = _plan(today)
        backend.progresses[user.id] = _progress(today)
        backend.glossaries[user.id] = _glossary(today)
        backend.profiles[user.id] = Profile(
            ("it", "business"), 15, 4, "B2", date(today.year, 6, 30)
        )
        backend.clients[user.id] = [ConnectedClient("claude-web", "Claude", now)]

    backend.subs[beto.id] = Subscription(
        Tier.ANNUAL,
        SubStatus.ACTIVE,
        backend.price_id,
        backend.price_cents,
        period_end=today + timedelta(days=10),
        subscription_ref="sub_demo",
        last_event_at=now - timedelta(days=355),
    )
    backend.customers[beto.id] = "cus_demo_beto"
    backend.report_pages[beto.id] = ReportsPage(
        weekly=(
            WeeklyReport(
                ReportNumbers(monday - timedelta(days=7), 3, 4, 45, 2, 3),
                "Pedir aclaraciones sin disculparte de más",
                "since three years",
                "Usaste «on track» sin ayuda tres veces",
            ),
            WeeklyReport(
                ReportNumbers(monday - timedelta(days=14), 4, 4, 52, 1, 2),
                "Bloqueos técnicos en el standup",
                None,
                "Primera semana completa",
            ),
        ),
        checkpoints=(Checkpoint(today - timedelta(days=20), "B1+", "B1+"),),
    )
    backend.settings_rows.update(_settings(now))
    return DemoUsers(ana.id, beto.id, nuevo.id, admin.id)
```

- [ ] **Step 3: Run tests and lint**

Run: `uv run pytest tests/unit/web/test_web_demo.py -q` then `uv run just fmt` then `uv run just lint`
Expected: PASS; ruff format wraps the long literal lines; lint clean.

- [ ] **Step 4: Commit**

```bash
git add src/tutor/web/demo.py tests/unit/web/test_web_demo.py
git commit -m "feat(web): demo data seed for tests and the local demo"
```

### Task 9: Localization (es-MX first, English second)

**Files:**
- Create: `src/tutor/web/i18n.py`
- Create: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Test: `tests/unit/web/test_web_i18n.py`

**Interfaces:**
- Consumes: `Lang`, `User` (Task 1).
- Produces: `TEMPLATES_DIR: Path` (`src/tutor/web/templates`); `load_translations(lang: Lang) -> gettext.NullTranslations`; `negotiate(query: str | None, accept_language: str | None, user: User | None) -> Lang`; formatters `fmt_decimal(value: float, lang: Lang, digits: int = 1) -> str`, `fmt_int(value: int, lang: Lang) -> str`, `fmt_percent(fraction: float, lang: Lang) -> str`, `fmt_money(cents: int, lang: Lang) -> str`, `fmt_date(day: date, lang: Lang, style: Literal["short", "long", "weekday"] = "short") -> str`; `missing_translations() -> list[str]` (msgids in templates without an English translation).

**Rules every later task follows:** template text is written in Spanish (es-MX) inside `{{ _("…") }}` or `{% trans %}…{% endtrans %}`; the Spanish string is the msgid. Every task that adds template text adds its English translation to `src/tutor/web/locale/en/LC_MESSAGES/messages.po` in the same commit; `test_every_template_string_has_an_english_translation` fails otherwise. Mexico writes decimals with a point ("3.1") and groups with a comma ("1,234.5"); the spec's "3,1" example is wrong for es-MX and is corrected in the spec amendment.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_i18n.py`:

```python
import re
from datetime import date
from uuid import uuid4

import pytest

from tutor.domain.dashboard.types import Lang, Role, User
from tutor.web.i18n import (
    fmt_date,
    fmt_decimal,
    fmt_int,
    fmt_money,
    fmt_percent,
    load_translations,
    missing_translations,
    negotiate,
)


def test_decimals_use_point_in_mexico_and_english() -> None:
    assert fmt_decimal(3.14, Lang.ES_MX) == "3.1"
    assert fmt_decimal(1234.5, Lang.ES_MX) == "1,234.5"
    assert fmt_decimal(3.14, Lang.EN) == "3.1"
    assert fmt_int(12345, Lang.ES_MX) == "12,345"


def test_percent_and_money() -> None:
    assert re.sub(r"\s", "", fmt_percent(0.42, Lang.ES_MX)) == "42%"
    money = fmt_money(2900, Lang.ES_MX)
    assert "29.00" in money and ("USD" in money or "US$" in money)


def test_dates_are_localised() -> None:
    assert fmt_date(date(2027, 1, 8), Lang.ES_MX, "weekday").startswith("viernes 8 de ene")
    assert fmt_date(date(2027, 1, 8), Lang.EN, "weekday") == "Fri, Jan 8"
    assert fmt_date(date(2027, 1, 8), Lang.ES_MX, "long") == "8 de enero de 2027"


@pytest.mark.parametrize(
    ("query", "header", "expected"),
    [
        ("en", None, Lang.EN),
        ("es", "en-US", Lang.ES_MX),
        (None, "en-US,en;q=0.9,es;q=0.8", Lang.EN),
        (None, "es-MX,es;q=0.9,en;q=0.8", Lang.ES_MX),
        (None, "fr-FR,en;q=0.5", Lang.EN),
        (None, "de-DE", Lang.ES_MX),
        (None, None, Lang.ES_MX),
        ("xx", "garbage;;;q=abc", Lang.ES_MX),
    ],
)
def test_negotiate_for_anonymous_visitors(
    query: str | None, header: str | None, expected: Lang
) -> None:
    assert negotiate(query, header, None) is expected


def test_logged_in_user_language_wins() -> None:
    user = User(uuid4(), "Ana", Role.LEARNER, Lang.EN, "UTC", False)
    assert negotiate("es", "es-MX", user) is Lang.EN


def test_spanish_needs_no_catalog_and_english_translates() -> None:
    assert load_translations(Lang.ES_MX).gettext("Inicio") == "Inicio"
    assert load_translations(Lang.EN).gettext("Entrar con Google") == "Sign in with Google"


def test_every_template_string_has_an_english_translation() -> None:
    assert missing_translations() == []
```

Run: `uv run pytest tests/unit/web/test_web_i18n.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.web.i18n'`.

- [ ] **Step 2: Create the English catalog**

`src/tutor/web/locale/en/LC_MESSAGES/messages.po`:

```po
# English translations for the dashboard. msgids are the es-MX source text.
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"Language: en\n"

msgid "Entrar con Google"
msgstr "Sign in with Google"
```

- [ ] **Step 3: Implement `i18n.py`**

`src/tutor/web/i18n.py`:

```python
"""Language choice, translations and locale-aware formatting (requirements section 14)."""

from __future__ import annotations

import gettext
from datetime import date
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import Literal

from babel.dates import format_date
from babel.messages.extract import extract_from_dir
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.numbers import format_currency, format_decimal, format_percent
from babel.support import Translations

from tutor.domain.dashboard.types import Lang, User

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
LOCALE_DIR = WEB_DIR / "locale"

_DATE_PATTERNS: dict[Lang, dict[str, str]] = {
    Lang.ES_MX: {"short": "d MMM", "long": "d 'de' MMMM 'de' y", "weekday": "EEEE d 'de' MMM"},
    Lang.EN: {"short": "MMM d", "long": "MMMM d, y", "weekday": "EEE, MMM d"},
}


def _po_path(lang: Lang) -> Path:
    return LOCALE_DIR / lang.value / "LC_MESSAGES" / "messages.po"


@cache
def load_translations(lang: Lang) -> gettext.NullTranslations:
    """Compile the .po in memory; es-MX is the source language and needs no catalog."""
    po = _po_path(lang)
    if not po.exists():
        return gettext.NullTranslations()
    with po.open("rb") as fh:
        catalog = read_po(fh, locale=lang.value)
    buf = BytesIO()
    write_mo(buf, catalog)
    buf.seek(0)
    return Translations(buf)


def negotiate(query: str | None, accept_language: str | None, user: User | None) -> Lang:
    if user is not None:
        return user.lang
    if query in ("es", "en"):
        return Lang.ES_MX if query == "es" else Lang.EN
    weighted: list[tuple[float, int, str]] = []
    for i, part in enumerate((accept_language or "").split(",")):
        tag, _, params = part.strip().partition(";")
        q = 1.0
        if params.strip().startswith("q="):
            try:
                q = float(params.strip()[2:])
            except ValueError:
                q = 0.0
        weighted.append((-q, i, tag.strip().lower()))
    for _, _, tag in sorted(weighted):
        primary = tag.split("-", 1)[0]
        if primary == "en":
            return Lang.EN
        if primary == "es":
            return Lang.ES_MX
    return Lang.ES_MX


def fmt_decimal(value: float, lang: Lang, digits: int = 1) -> str:
    pattern = "#,##0." + "0" * digits if digits > 0 else "#,##0"
    return format_decimal(value, format=pattern, locale=lang.value)


def fmt_int(value: int, lang: Lang) -> str:
    return format_decimal(value, format="#,##0", locale=lang.value)


def fmt_percent(fraction: float, lang: Lang) -> str:
    return format_percent(fraction, locale=lang.value)


def fmt_money(cents: int, lang: Lang) -> str:
    return format_currency(cents / 100, "USD", locale=lang.value)


def fmt_date(day: date, lang: Lang, style: Literal["short", "long", "weekday"] = "short") -> str:
    return format_date(day, _DATE_PATTERNS[lang][style], locale=lang.value)


def missing_translations() -> list[str]:
    """msgids used in templates that the English catalog does not translate."""
    english = load_translations(Lang.EN)
    method_map = [("**.html", "jinja2.ext:babel_extract")]
    options = {"**.html": {"extensions": "jinja2.ext.i18n"}}
    missing: set[str] = set()
    if not TEMPLATES_DIR.exists():
        return []
    for _file, _line, message, _comments, _ctx in extract_from_dir(
        str(TEMPLATES_DIR), method_map, options
    ):
        ids = message if isinstance(message, tuple) else (message,)
        msgid = ids[0]
        if msgid and english.gettext(msgid) == msgid:
            missing.add(msgid)
    return sorted(missing)
```

Note: `extract_from_dir` yields 5-tuples in Babel 2.18 (`filename, lineno, message, comments, context`). If the installed version yields 4-tuples, unpack `_file, _line, message, _comments`. Plural msgids arrive as tuples; the singular form is checked.

- [ ] **Step 4: Run tests and types**

Run: `uv run pytest tests/unit/web/test_web_i18n.py -q` then `uv run mypy`
Expected: PASS. If a date assertion differs only by CLDR punctuation (for example "ene." vs "ene"), keep the `startswith` form and record the actual output in the test as a comment rather than loosening other assertions.

- [ ] **Step 5: Commit**

```bash
git add src/tutor/web/i18n.py src/tutor/web/locale tests/unit/web/test_web_i18n.py
git commit -m "feat(web): language negotiation, catalogs and locale formatting"
```

### Task 10: App factory, views, security headers, error pages and public pages

**Files:**
- Create: `src/tutor/web/app.py`
- Create: `src/tutor/web/assets.py`
- Create: `src/tutor/web/views.py`
- Create: `src/tutor/web/security.py`
- Create: `src/tutor/web/routes/__init__.py`
- Create: `src/tutor/web/routes/public.py`
- Create: `src/tutor/web/templates/base.html`
- Create: `src/tutor/web/templates/layouts/public.html`
- Create: `src/tutor/web/templates/layouts/app.html`
- Create: `src/tutor/web/templates/partials/banners.html`
- Create: `src/tutor/web/templates/partials/error.html`
- Create: `src/tutor/web/templates/pages/login.html`
- Create: `src/tutor/web/templates/pages/privacy.html`
- Create: `src/tutor/web/templates/pages/terms.html`
- Create: `src/tutor/web/templates/pages/error.html`
- Create: `src/tutor/web/static/css/app.css` (empty placeholder file so the asset manifest has an entry; Task 13 fills it)
- Create: `src/tutor/web/static/js/app.js` (empty file; Task 15 fills it)
- Create: `tests/unit/web/__init__.py` (empty; makes `from .conftest import …` work under pytest's default import mode)
- Create: `tests/unit/web/conftest.py`
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Test: `tests/unit/web/test_web_app.py`

**Interfaces:**
- Consumes: `WebDeps`, `WebConfig` (Task 7); `memory_deps`, `FixedClock`, `MemoryBackend` (Task 7); `seed_demo` (Task 8); i18n (Task 9); `banners_for` (Task 1); `local_today` (Task 2).
- Produces:
  - `create_app(deps: WebDeps, config: WebConfig) -> FastAPI` (stores `deps`, `config`, `assets`, `views` on `app.state`).
  - `tutor.web.views`: `render(request: Request, template: str, ctx: Mapping[str, Any] | None = None, *, status_code: int = 200, headers: Mapping[str, str] | None = None) -> HTMLResponse`; `is_htmx(request: Request) -> bool`; `request_lang(request: Request) -> Lang`; `today_for(request: Request) -> date` (learner's local date; UTC when logged out). Base context available in every template: `request`, `user` (`User | None`), `csrf_token` (str, "" when logged out), `config`, `lang` (`Lang`), `html_lang` ("es-MX"/"en"), `path`, `banners` (tuple of `Banner`), `subscription` (`Subscription | None`), `usage` (`FreeUsage | None`), `today` (`date`), `reduce_motion` (bool). Jinja globals: `asset(path) -> str`, `public_url(path) -> str` (adds `?lang=` for logged-out visitors). Filters: `decimal(value, digits=1)`, `integer`, `percent`, `money`, `day(style="short")`.
  - `tutor.web.routes.all_routers(config: WebConfig) -> list[APIRouter]` — every later route task appends its router here.
  - `tutor.web.security`: `SecurityHeadersMiddleware`, `CSP` (str constant), `safe_next(value: str | None) -> str`.
  - Template blocks: `base.html` defines `title`, `head`, `body`; `layouts/app.html` defines `content` and sets `active_nav` via `{% set active_nav = "home" %}` in child templates (values: `home`, `plan`, `progress`, `sessions`, `glossary`, `reports`, `account`, `connect`, `admin`); `layouts/public.html` defines `content`.
  - Test fixtures in `tests/unit/web/conftest.py`: `NOW`, `TODAY` constants; fixtures `backend`, `demo` (`DemoUsers`), `clock` (`FixedClock`), `config`, `app`, `client` (anonymous `TestClient` on `https://testserver`), `login` (callable `login(user_id) -> TestClient`), and helper `csrf_of(client) -> str`.

The layout and page templates below use the class vocabulary defined in "Conventions for page tasks"; Task 13 styles them.

- [ ] **Step 1: Write the test fixtures**

Create the empty package marker first: `touch tests/unit/web/__init__.py` (web tests import shared constants with `from .conftest import …`).

`tests/unit/web/conftest.py`:

```python
import re
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import DemoUsers, seed_demo
from tutor.web.memory import FakeBilling, FakeGoogle, FixedClock, MemoryBackend, memory_deps

NOW = datetime(2027, 1, 12, 18, 0, tzinfo=UTC)  # 12:00 in Mexico City, a Tuesday
TODAY = date(2027, 1, 12)
BASE = "https://testserver"


@pytest.fixture
def backend() -> MemoryBackend:
    return MemoryBackend()


@pytest.fixture
def demo(backend: MemoryBackend) -> DemoUsers:
    return seed_demo(backend, TODAY)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def billing() -> FakeBilling:
    return FakeBilling()


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def config() -> WebConfig:
    return WebConfig(
        env="test",
        base_url=BASE,
        mcp_url=f"{BASE}/mcp",
        support_email="soporte@example.test",
        test_login=True,
    )


@pytest.fixture
def app(
    backend: MemoryBackend,
    demo: DemoUsers,
    clock: FixedClock,
    billing: FakeBilling,
    google: FakeGoogle,
    config: WebConfig,
) -> FastAPI:
    return create_app(memory_deps(backend, clock, billing=billing, google=google), config)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        yield c


@pytest.fixture
def login(app: FastAPI) -> Iterator[Callable[[UUID], TestClient]]:
    clients: list[TestClient] = []

    def _login(user_id: UUID) -> TestClient:
        c = TestClient(app, base_url=BASE, follow_redirects=False)
        response = c.post("/auth/test-login", data={"user_id": str(user_id)})
        assert response.status_code == 303, response.text
        clients.append(c)
        return c

    yield _login
    for c in clients:
        c.close()


_CSRF = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


def csrf_of(client: TestClient) -> str:
    match = _CSRF.search(client.get("/app/account").text)
    assert match, "csrf meta tag missing"
    return match.group(1)
```

Until Task 11 adds sessions and Task 12 adds `/auth/test-login`, only the `client` fixture is usable; tests in this task use it alone.

- [ ] **Step 2: Write the failing tests**

`tests/unit/web/test_web_app.py`:

```python
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang, Role, User
from tutor.web.security import CSP, safe_next


def test_login_page_renders_in_spanish_with_security_headers(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert '<html lang="es-MX"' in response.text
    assert "Entrar con Google" in response.text
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "max-age=" in response.headers["strict-transport-security"]
    assert response.headers["cache-control"] == "no-store"


def test_login_page_in_english_by_query(client: TestClient) -> None:
    response = client.get("/login?lang=en")
    assert '<html lang="en"' in response.text and "Sign in with Google" in response.text
    assert 'href="/privacy?lang=en"' in response.text


def test_root_redirects_to_app(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 307 and response.headers["location"] == "/app/"


def test_privacy_and_terms_render(client: TestClient) -> None:
    privacy = client.get("/privacy")
    assert privacy.status_code == 200 and "soporte@example.test" in privacy.text
    assert client.get("/terms").status_code == 200


def test_unknown_page_uses_friendly_404(client: TestClient) -> None:
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "No encontramos esta página" in response.text


def test_static_assets_are_immutable_when_versioned(client: TestClient) -> None:
    page = client.get("/login").text
    assert "/static/css/app.css?v=" in page
    href = page.split('href="/static/css/app.css?v=')[1].split('"')[0]
    response = client.get(f"/static/css/app.css?v={href}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_no_inline_scripts_or_styles(client: TestClient) -> None:
    page = client.get("/login").text
    assert "<script>" not in page and "style=" not in page and "<style" not in page


def test_day_filter_uses_learner_timezone(app: FastAPI) -> None:
    env = app.state.views.envs[Lang.EN]
    user = User(uuid4(), "Ana", Role.LEARNER, Lang.EN, "America/Mexico_City", False)
    late_evening = datetime(2027, 1, 12, 3, tzinfo=UTC)  # 21:00 on Jan 11 in CDMX
    assert env.from_string("{{ when|day }}").render(when=late_evening, user=user) == "Jan 11"
    assert env.from_string("{{ when|day }}").render(when=late_evening, user=None) == "Jan 12"


def test_safe_next_blocks_open_redirects() -> None:
    assert safe_next("/app/glossary?q=x") == "/app/glossary?q=x"
    for bad in (None, "", "https://evil.example", "//evil.example", "/\\evil", "app"):
        assert safe_next(bad) == "/app/"
```

Run: `uv run pytest tests/unit/web/test_web_app.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.web.app'`.

- [ ] **Step 3: Implement assets, security and views**

`src/tutor/web/assets.py`:

```python
"""Content-hashed static URLs, computed once at startup (no build step)."""

from __future__ import annotations

import hashlib
from pathlib import Path


class AssetManifest:
    def __init__(self, static_dir: Path) -> None:
        self._hashes: dict[str, str] = {}
        combined = hashlib.sha256()
        for path in sorted(p for p in static_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(static_dir).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self._hashes[rel] = digest[:10]
            combined.update(f"{rel}:{digest}".encode())
        self.version = combined.hexdigest()[:12]

    def url(self, rel: str) -> str:
        """`/static/<rel>?v=<hash>`; raises KeyError for a missing file so typos fail tests."""
        return f"/static/{rel}?v={self._hashes[rel]}"

    def files(self) -> tuple[str, ...]:
        return tuple(self._hashes)
```

`src/tutor/web/security.py`:

```python
"""Security headers and redirect hygiene (spec section 9.6)."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
    "form-action 'self' https://checkout.stripe.com https://billing.stripe.com "
    "https://accounts.google.com; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
_HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        versioned = path.startswith("/static/") and b"v=" in scope.get("query_string", b"")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["Strict-Transport-Security"] = _HSTS
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                if versioned:
                    headers["Cache-Control"] = "public, max-age=31536000, immutable"
                elif path.startswith("/static/") or path == "/sw.js":
                    headers["Cache-Control"] = "no-cache"
                else:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def safe_next(value: str | None) -> str:
    """Only same-site absolute paths survive; anything else goes to the dashboard."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")):
        return "/app/"
    return value
```

`src/tutor/web/views.py`:

```python
"""Jinja rendering with one environment per language and the shared base context."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from functools import partial
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, pass_context
from jinja2.runtime import Context

from tutor.domain.dashboard.billing import banners_for
from tutor.domain.dashboard.home import local_today
from tutor.domain.dashboard.types import Lang, Tier, User
from tutor.web.assets import AssetManifest
from tutor.web.i18n import (
    TEMPLATES_DIR,
    fmt_date,
    fmt_decimal,
    fmt_int,
    fmt_money,
    fmt_percent,
    load_translations,
    negotiate,
)
from tutor.web.ports import WebDeps


def _day_filter(lang: Lang) -> Callable[..., str]:
    """`{{ value|day("weekday") }}`; aware datetimes become the learner's local date."""

    @pass_context
    def day(ctx: Context, value: date | datetime, style: str = "short") -> str:
        if isinstance(value, datetime):
            user = ctx.get("user")
            value = local_today(value, user.timezone if user else "UTC")
        return fmt_date(value, lang, style)  # type: ignore[arg-type]

    return day


class Views:
    def __init__(self, assets: AssetManifest) -> None:
        self.envs = {lang: self._env(lang, assets) for lang in Lang}

    @staticmethod
    def _env(lang: Lang, assets: AssetManifest) -> Environment:
        env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=True,
            extensions=["jinja2.ext.i18n"],
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        translations = load_translations(lang)
        env.install_gettext_translations(translations, newstyle=True)  # type: ignore[attr-defined]
        env.globals["asset"] = assets.url
        env.filters["decimal"] = lambda v, digits=1: fmt_decimal(v, lang, digits)
        env.filters["integer"] = partial(fmt_int, lang=lang)
        env.filters["percent"] = partial(fmt_percent, lang=lang)
        env.filters["money"] = partial(fmt_money, lang=lang)
        env.filters["day"] = _day_filter(lang)
        return env


def _user(request: Request) -> User | None:
    user: User | None = getattr(request.state, "user", None)
    return user


def is_htmx(request: Request) -> bool:
    return (
        request.headers.get("hx-request") == "true"
        and request.headers.get("hx-history-restore-request") != "true"
    )


def request_lang(request: Request) -> Lang:
    return negotiate(
        request.query_params.get("lang"), request.headers.get("accept-language"), _user(request)
    )


def today_for(request: Request) -> date:
    deps: WebDeps = request.app.state.deps
    user = _user(request)
    now = deps.clock()
    return local_today(now, user.timezone) if user else now.astimezone(UTC).date()


def _csrf(request: Request) -> str:
    holder = getattr(request.state, "web", None)
    session = getattr(holder, "session", None)
    return session.csrf_token if session is not None and session.user_id is not None else ""


def render(
    request: Request,
    template: str,
    ctx: Mapping[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    views: Views = request.app.state.views
    deps: WebDeps = request.app.state.deps
    lang = request_lang(request)
    user = _user(request)
    today = today_for(request)
    subscription = deps.subscriptions.subscription(user.id) if user else None
    usage = deps.reader.usage(user.id, today) if user else None

    def public_url(path: str) -> str:
        if user is not None:
            return path
        code = "en" if lang is Lang.EN else "es"
        return f"{path}{'&' if '?' in path else '?'}lang={code}"

    base: dict[str, Any] = {
        "request": request,
        "user": user,
        "csrf_token": _csrf(request),
        "config": request.app.state.config,
        "lang": lang,
        "html_lang": "es-MX" if lang is Lang.ES_MX else "en",
        "path": request.url.path,
        "banners": banners_for(subscription, usage, today) if subscription else (),
        "subscription": subscription,
        "usage": usage if subscription and subscription.tier is Tier.FREE else None,
        "today": today,
        "reduce_motion": bool(user and user.reduce_motion),
        "public_url": public_url,
    }
    html = views.envs[lang].get_template(template).render({**base, **(ctx or {})})
    return HTMLResponse(html, status_code=status_code, headers=dict(headers or {}))
```

- [ ] **Step 4: Implement routes, error handlers and the app factory**

`src/tutor/web/routes/__init__.py`:

```python
"""Every web router. Each page task appends its router to `all_routers`."""

from __future__ import annotations

from fastapi import APIRouter

from tutor.web.config import WebConfig


def all_routers(config: WebConfig) -> list[APIRouter]:
    from tutor.web.routes import public

    routers: list[APIRouter] = [public.router]
    return routers
```

`src/tutor/web/routes/public.py`:

```python
"""Pages that need no login."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tutor.web.views import render

router = APIRouter()


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/app/", status_code=307)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return render(request, "pages/login.html", {"next": request.query_params.get("next", "")})


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return render(request, "pages/privacy.html")


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return render(request, "pages/terms.html")
```

`src/tutor/web/app.py`:

```python
"""FastAPI application for the dashboard."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from tutor.web.assets import AssetManifest
from tutor.web.config import WebConfig
from tutor.web.ports import WebDeps
from tutor.web.routes import all_routers
from tutor.web.security import SecurityHeadersMiddleware
from tutor.web.views import Views, is_htmx, render

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("tutor.web")


def create_app(deps: WebDeps, config: WebConfig) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps
    app.state.config = config
    app.state.assets = AssetManifest(STATIC_DIR)
    app.state.views = Views(app.state.assets)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in all_routers(config):
        app.include_router(router)
    _install_error_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        template = "partials/error.html" if is_htmx(request) else "pages/error.html"
        ctx = {"status": exc.status_code, "ref": ""}
        return render(request, template, ctx, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> Response:
        ref = uuid.uuid4().hex[:8]
        log.exception("unhandled error ref=%s path=%s", ref, request.url.path)
        try:
            template = "partials/error.html" if is_htmx(request) else "pages/error.html"
            return render(request, template, {"status": 500, "ref": ref}, status_code=500)
        except Exception:  # rendering itself failed; never leak details
            return HTMLResponse(f"Error {ref}", status_code=500)
```

Create the two empty static files so the manifest has entries:

```bash
mkdir -p src/tutor/web/static/css src/tutor/web/static/js
printf '/* Filled by Task 13. */\n' > src/tutor/web/static/css/app.css
printf '// Filled by Task 15.\n' > src/tutor/web/static/js/app.js
```

- [ ] **Step 5: Write the templates**

`src/tutor/web/templates/base.html`:

```html
<!doctype html>
<html lang="{{ html_lang }}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{% block title %}English Tutor{% endblock %}</title>
  <meta name="theme-color" content="#11402c">
  {% if csrf_token %}<meta name="csrf-token" content="{{ csrf_token }}">{% endif %}
  <meta name="htmx-config" content='{"allowEval":false,"includeIndicatorStyles":false,"historyCacheSize":0,"responseHandling":[{"code":"204","swap":false},{"code":"[23]..","swap":true},{"code":"422","swap":true},{"code":"[45]..","swap":false,"error":true}]}'>
  <link rel="stylesheet" href="{{ asset('css/app.css') }}">
  {% block head %}{% endblock %}
  <script src="{{ asset('js/app.js') }}" type="module"></script>
</head>
<body{% if csrf_token %} hx-headers='{"X-CSRF-Token": "{{ csrf_token }}"}'{% endif %}{% if reduce_motion %} data-reduce-motion{% endif %}>
  <a class="skip-link" href="#main">{{ _("Saltar al contenido") }}</a>
  {% block body %}{% endblock %}
  <div class="sr-only" aria-live="polite" id="live"></div>
</body>
</html>
```

`historyCacheSize: 0` keeps HTMX from storing page snapshots (which can hold personal data) in `localStorage`. Task 15 adds the HTMX `<script>` tag and the manifest link.

`src/tutor/web/templates/layouts/public.html`:

```html
{% extends "base.html" %}
{% block body %}
<header class="public-head">
  <a class="brand" href="{{ public_url('/login') }}">English Tutor</a>
  <nav class="public-head__nav" aria-label="{{ _('Idioma') }}">
    {% if lang == "es_MX" %}<a href="{{ path }}?lang=en" hreflang="en" lang="en">English</a>
    {% else %}<a href="{{ path }}?lang=es" hreflang="es" lang="es">Español</a>{% endif %}
  </nav>
</header>
<main id="main" class="public-main">
  {% block content %}{% endblock %}
</main>
<footer class="public-foot">
  <a href="{{ public_url('/pricing') }}">{{ _("Precios") }}</a>
  <a href="{{ public_url('/privacy') }}">{{ _("Aviso de privacidad") }}</a>
  <a href="{{ public_url('/terms') }}">{{ _("Términos") }}</a>
</footer>
{% endblock %}
```

`src/tutor/web/templates/layouts/app.html`:

```html
{% extends "base.html" %}
{% set nav = [
  ("home", "/app/", _("Inicio"), "home"),
  ("plan", "/app/plan", _("Plan"), "plan"),
  ("progress", "/app/progress", _("Progreso"), "progress"),
  ("sessions", "/app/sessions", _("Sesiones"), "sessions"),
  ("glossary", "/app/glossary", _("Glosario"), "glossary"),
  ("reports", "/app/reports", _("Reportes"), "reports"),
  ("account", "/app/account", _("Cuenta"), "account"),
] %}
{% block body %}
<div class="shell">
  <aside class="sidebar">
    <a class="brand brand--light" href="/app/">English Tutor</a>
    <nav class="sidebar__nav" aria-label="{{ _('Secciones') }}">
      {% for key, href, label, icon in nav %}
      <a class="nav-link{% if active_nav == key %} nav-link--active{% endif %}" href="{{ href }}"{% if active_nav == key %} aria-current="page"{% endif %}>
        <svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#{{ icon }}"></use></svg>{{ label }}
      </a>
      {% endfor %}
      {% if user.role == "admin" %}
      <a class="nav-link{% if active_nav == 'admin' %} nav-link--active{% endif %}" href="/admin/settings">{{ _("Ajustes") }}</a>
      {% endif %}
    </nav>
    <div class="sidebar__foot">
      {% if subscription.tier == "annual" %}
      <span class="chip chip--annual">{{ _("Annual") }}</span>
      {% else %}
      <span class="chip chip--free">{{ _("Free") }}</span>
      {% if usage %}
      <a class="meter-link" href="/billing/checkout">
        <span>{% trans used=usage.sessions_this_week, cap=usage.session_cap %}{{ used }} de {{ cap }} sesiones esta semana{% endtrans %}</span>
        <svg class="meter" viewBox="0 0 100 6" aria-hidden="true" preserveAspectRatio="none">
          <rect class="meter__track" width="100" height="6" rx="3"></rect>
          <rect class="meter__fill" data-grow width="{{ (100 * [usage.sessions_this_week, usage.session_cap]|min / (usage.session_cap or 1))|round(1) }}" height="6" rx="3"></rect>
        </svg>
      </a>
      {% endif %}
      {% endif %}
      <form method="post" action="/app/lang" class="inline-form">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <input type="hidden" name="back" value="{{ path }}">
        <button class="btn btn--ghost btn--small" name="lang" value="{{ 'en' if lang == 'es_MX' else 'es_MX' }}">{{ "English" if lang == "es_MX" else "Español" }}</button>
      </form>
      <form method="post" action="/auth/logout" class="inline-form">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button class="btn btn--ghost btn--small">{{ _("Cerrar sesión") }}</button>
      </form>
    </div>
  </aside>
  <main id="main" class="main">
    {% include "partials/banners.html" %}
    {% block content %}{% endblock %}
  </main>
  <nav class="tabbar" aria-label="{{ _('Secciones') }}">
    {% for key, href, label, icon in nav[:3] + [nav[4]] %}
    <a class="tabbar__link{% if active_nav == key %} tabbar__link--active{% endif %}" href="{{ href }}"{% if active_nav == key %} aria-current="page"{% endif %}>
      <svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#{{ icon }}"></use></svg><span>{{ label }}</span>
    </a>
    {% endfor %}
    <details class="tabbar__more">
      <summary class="tabbar__link{% if active_nav in ('sessions', 'reports', 'account', 'admin') %} tabbar__link--active{% endif %}">
        <svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#more"></use></svg><span>{{ _("Más") }}</span>
      </summary>
      <ul class="tabbar__menu">
        <li><a href="/app/sessions">{{ _("Sesiones") }}</a></li>
        <li><a href="/app/reports">{{ _("Reportes") }}</a></li>
        <li><a href="/app/account">{{ _("Cuenta") }}</a></li>
      </ul>
    </details>
  </nav>
</div>
{% endblock %}
```

The layout references `icons/sprite.svg`, which Task 13 creates. Until then `asset()` raises `KeyError` for it, which is why no test renders `layouts/app.html` before Task 13. Task 11 is the first task that renders it, and Task 11 Step 1 creates a stub sprite.

`src/tutor/web/templates/partials/banners.html`:

```html
{% for banner in banners %}
<div class="banner banner--{{ 'warn' if banner.kind in ('payment_failed', 'session_limit', 'glossary_limit') else 'info' }}" role="status">
  {% if banner.kind == "payment_failed" %}
    <p>{{ _("No pudimos cobrar tu renovación. Actualiza tu método de pago para conservar Annual.") }}</p>
    <form method="post" action="/billing/portal"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn--primary btn--small">{{ _("Actualizar método de pago") }}</button></form>
  {% elif banner.kind == "renewal_soon" %}
    <p>{% trans when=banner.on|day("long"), amount=banner.amount_cents|money %}Tu plan Annual se renueva el {{ when }} por {{ amount }}.{% endtrans %}</p>
    <form method="post" action="/billing/portal"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="btn btn--ghost btn--small">{{ _("Gestionar pago") }}</button></form>
  {% elif banner.kind == "lapsed" %}
    <p>{{ _("Tu plan volvió a Free; no se borró nada.") }}</p>
    <a class="btn btn--ghost btn--small" href="/billing/checkout">{{ _("Volver a Annual") }}</a>
  {% elif banner.kind == "session_limit" %}
    <p>{% trans when=banner.on|day("weekday") %}Usaste tus sesiones Free de esta semana. Se renuevan el {{ when }}.{% endtrans %}</p>
    <a class="btn btn--ghost btn--small" href="/billing/checkout">{{ _("Ver Annual") }}</a>
  {% elif banner.kind == "glossary_limit" %}
    <p>{{ _("Tu glosario llegó a 50 elementos, el máximo en Free. Lo que ya tienes se queda.") }}</p>
    <a class="btn btn--ghost btn--small" href="/billing/checkout">{{ _("Ver Annual") }}</a>
  {% endif %}
</div>
{% endfor %}
```

`src/tutor/web/templates/pages/login.html`:

```html
{% extends "layouts/public.html" %}
{% block title %}{{ _("Entrar") }} · English Tutor{% endblock %}
{% block content %}
<section class="card card--narrow">
  <h1>{{ _("Habla con confianza en el trabajo") }}</h1>
  <p>{{ _("Tu tutor de inglés recuerda cada sesión, tus errores y tus frases. Tu progreso vive en tu servidor, no dentro de un solo chat.") }}</p>
  {% if error is defined and error %}<p class="form-error" role="alert">{{ _("No pudimos iniciar sesión con Google. Intenta de nuevo.") }}</p>{% endif %}
  <a class="btn btn--primary btn--block" href="/auth/google{% if next %}?next={{ next|urlencode }}{% endif %}">{{ _("Entrar con Google") }}</a>
  <p class="muted">{% trans privacy=public_url('/privacy'), terms=public_url('/terms') %}Al entrar aceptas los <a href="{{ terms }}">términos</a> y el <a href="{{ privacy }}">aviso de privacidad</a>.{% endtrans %}</p>
</section>
{% endblock %}
```

`src/tutor/web/templates/pages/privacy.html` (content drawn from requirements section 5; the author reviews the wording before launch, see Global Constraints):

```html
{% extends "layouts/public.html" %}
{% block title %}{{ _("Aviso de privacidad") }} · English Tutor{% endblock %}
{% block content %}
<article class="prose">
  <h1>{{ _("Aviso de privacidad") }}</h1>
  <h2>{{ _("Qué datos guardamos") }}</h2>
  <p>{{ _("Tu nombre y correo de Google, tu plan de estudio, los turnos de conversación que tu asistente reporta al terminar cada sesión, tus errores corregidos y tu glosario. No guardamos audio ni transcripciones completas.") }}</p>
  <h2>{{ _("Para qué") }}</h2>
  <p>{{ _("Para continuar cada sesión donde quedó la anterior, calcular tu progreso y enviarte el reporte semanal si lo activas.") }}</p>
  <h2>{{ _("Tu asistente de IA") }}</h2>
  <p>{{ _("El contenido de tus lecciones pasa por el proveedor de tu asistente (por ejemplo Claude) bajo los términos de ese proveedor.") }}</p>
  <h2>{{ _("Cookies") }}</h2>
  <p>{{ _("Solo usamos una cookie de sesión para mantenerte dentro del tablero. No hay analítica ni publicidad.") }}</p>
  <h2>{{ _("Tus derechos") }}</h2>
  <p>{{ _("Puedes descargar todos tus datos o eliminar tu cuenta desde Cuenta. La eliminación se completa en 24 horas.") }}</p>
  <p>{% trans email=config.support_email %}Contacto: {{ email }}{% endtrans %}</p>
</article>
{% endblock %}
```

`src/tutor/web/templates/pages/terms.html`:

```html
{% extends "layouts/public.html" %}
{% block title %}{{ _("Términos") }} · English Tutor{% endblock %}
{% block content %}
<article class="prose">
  <h1>{{ _("Términos del servicio") }}</h1>
  <p>{{ _("English Tutor te ayuda a practicar inglés hablado y escrito para el trabajo a través de tu asistente de IA.") }}</p>
  <h2>{{ _("Lo que no hace") }}</h2>
  <p>{{ _("No evalúa pronunciación ni audio y no emite certificados de nivel. Los niveles CEFR que ves son la opinión del modelo, mostrada como tendencia.") }}</p>
  <h2>{{ _("Pagos y reembolsos") }}</h2>
  <p>{{ _("Annual se cobra una vez al año con tarjeta. Puedes pedir el reembolso completo dentro de los primeros 14 días. No emitimos CFDI por ahora.") }}</p>
  <p>{% trans email=config.support_email %}Contacto: {{ email }}{% endtrans %}</p>
</article>
{% endblock %}
```

`src/tutor/web/templates/pages/error.html`:

```html
{% extends "layouts/public.html" %}
{% block title %}{{ _("Algo salió mal") }} · English Tutor{% endblock %}
{% block content %}
<section class="card card--narrow">{% include "partials/error.html" %}</section>
{% endblock %}
```

`src/tutor/web/templates/partials/error.html`:

```html
<div class="error-box" role="alert">
  {% if status == 404 %}
    <h1>{{ _("No encontramos esta página") }}</h1>
    <a class="btn btn--ghost" href="/app/">{{ _("Ir a Inicio") }}</a>
  {% elif status == 403 %}
    <h1>{{ _("Tu sesión cambió") }}</h1>
    <p>{{ _("Recarga la página e intenta de nuevo.") }}</p>
  {% else %}
    <h1>{{ _("Algo salió mal") }}</h1>
    <p>{{ _("Tu progreso está a salvo. Intenta de nuevo en un momento.") }}</p>
    {% if ref %}<p class="muted">{% trans ref=ref %}Referencia: {{ ref }}{% endtrans %}</p>{% endif %}
    <a class="btn btn--ghost" href="">{{ _("Reintentar") }}</a>
  {% endif %}
</div>
```

The public layout renders for errors on `/app/*` pages too; that is intentional (an error page must not depend on the data that just failed).

- [ ] **Step 6: Add the English translations**

Append to `src/tutor/web/locale/en/LC_MESSAGES/messages.po` one entry per Spanish string used in the templates of this task. The entries:

```po
msgid "Saltar al contenido"
msgstr "Skip to content"

msgid "Idioma"
msgstr "Language"

msgid "Precios"
msgstr "Pricing"

msgid "Aviso de privacidad"
msgstr "Privacy notice"

msgid "Términos"
msgstr "Terms"

msgid "Inicio"
msgstr "Home"

msgid "Plan"
msgstr "Plan"

msgid "Progreso"
msgstr "Progress"

msgid "Sesiones"
msgstr "Sessions"

msgid "Glosario"
msgstr "Glossary"

msgid "Reportes"
msgstr "Reports"

msgid "Cuenta"
msgstr "Account"

msgid "Secciones"
msgstr "Sections"

msgid "Ajustes"
msgstr "Settings"

msgid "Annual"
msgstr "Annual"

msgid "Free"
msgstr "Free"

msgid "%(used)s de %(cap)s sesiones esta semana"
msgstr "%(used)s of %(cap)s sessions this week"

msgid "Cerrar sesión"
msgstr "Log out"

msgid "Más"
msgstr "More"

msgid "No pudimos cobrar tu renovación. Actualiza tu método de pago para conservar Annual."
msgstr "We couldn't charge your renewal. Update your payment method to keep Annual."

msgid "Actualizar método de pago"
msgstr "Update payment method"

msgid "Tu plan Annual se renueva el %(when)s por %(amount)s."
msgstr "Your Annual plan renews on %(when)s for %(amount)s."

msgid "Gestionar pago"
msgstr "Manage payment"

msgid "Tu plan volvió a Free; no se borró nada."
msgstr "Your plan is back on Free; nothing was deleted."

msgid "Volver a Annual"
msgstr "Go back to Annual"

msgid "Usaste tus sesiones Free de esta semana. Se renuevan el %(when)s."
msgstr "You've used this week's Free sessions. They renew on %(when)s."

msgid "Ver Annual"
msgstr "See Annual"

msgid "Tu glosario llegó a 50 elementos, el máximo en Free. Lo que ya tienes se queda."
msgstr "Your glossary reached 50 items, the Free maximum. What you have stays."

msgid "Entrar"
msgstr "Sign in"

msgid "Habla con confianza en el trabajo"
msgstr "Speak with confidence at work"

msgid "Tu tutor de inglés recuerda cada sesión, tus errores y tus frases. Tu progreso vive en tu servidor, no dentro de un solo chat."
msgstr "Your English tutor remembers every session, your mistakes and your phrases. Your progress lives on your server, not inside one chat."

msgid "No pudimos iniciar sesión con Google. Intenta de nuevo."
msgstr "We couldn't sign you in with Google. Please try again."

msgid "Al entrar aceptas los <a href=\"%(terms)s\">términos</a> y el <a href=\"%(privacy)s\">aviso de privacidad</a>."
msgstr "By signing in you accept the <a href=\"%(terms)s\">terms</a> and the <a href=\"%(privacy)s\">privacy notice</a>."

msgid "Qué datos guardamos"
msgstr "What we store"

msgid "Tu nombre y correo de Google, tu plan de estudio, los turnos de conversación que tu asistente reporta al terminar cada sesión, tus errores corregidos y tu glosario. No guardamos audio ni transcripciones completas."
msgstr "Your Google name and email, your study plan, the conversation turns your assistant reports at the end of each session, your corrected mistakes and your glossary. We store no audio and no full transcripts."

msgid "Para qué"
msgstr "Why"

msgid "Para continuar cada sesión donde quedó la anterior, calcular tu progreso y enviarte el reporte semanal si lo activas."
msgstr "To continue each session where the last one ended, compute your progress and send your weekly report if you turn it on."

msgid "Tu asistente de IA"
msgstr "Your AI assistant"

msgid "El contenido de tus lecciones pasa por el proveedor de tu asistente (por ejemplo Claude) bajo los términos de ese proveedor."
msgstr "Your lesson content passes through your assistant's vendor (for example Claude) under that vendor's terms."

msgid "Cookies"
msgstr "Cookies"

msgid "Solo usamos una cookie de sesión para mantenerte dentro del tablero. No hay analítica ni publicidad."
msgstr "We only use one session cookie to keep you signed in to the dashboard. No analytics, no advertising."

msgid "Tus derechos"
msgstr "Your rights"

msgid "Puedes descargar todos tus datos o eliminar tu cuenta desde Cuenta. La eliminación se completa en 24 horas."
msgstr "You can download all your data or delete your account from Account. Deletion completes within 24 hours."

msgid "Contacto: %(email)s"
msgstr "Contact: %(email)s"

msgid "Términos del servicio"
msgstr "Terms of service"

msgid "English Tutor te ayuda a practicar inglés hablado y escrito para el trabajo a través de tu asistente de IA."
msgstr "English Tutor helps you practise spoken and written English for work through your AI assistant."

msgid "Lo que no hace"
msgstr "What it does not do"

msgid "No evalúa pronunciación ni audio y no emite certificados de nivel. Los niveles CEFR que ves son la opinión del modelo, mostrada como tendencia."
msgstr "It does not score pronunciation or audio and issues no level certificates. The CEFR levels you see are the model's opinion, shown as a trend."

msgid "Pagos y reembolsos"
msgstr "Payments and refunds"

msgid "Annual se cobra una vez al año con tarjeta. Puedes pedir el reembolso completo dentro de los primeros 14 días. No emitimos CFDI por ahora."
msgstr "Annual is charged once a year by card. You can ask for a full refund within the first 14 days. We do not issue CFDI invoices for now."

msgid "Algo salió mal"
msgstr "Something went wrong"

msgid "No encontramos esta página"
msgstr "We couldn't find this page"

msgid "Ir a Inicio"
msgstr "Go to Home"

msgid "Tu sesión cambió"
msgstr "Your session changed"

msgid "Recarga la página e intenta de nuevo."
msgstr "Reload the page and try again."

msgid "Tu progreso está a salvo. Intenta de nuevo en un momento."
msgstr "Your progress is safe. Try again in a moment."

msgid "Referencia: %(ref)s"
msgstr "Reference: %(ref)s"

msgid "Reintentar"
msgstr "Try again"
```

- [ ] **Step 7: Run tests, lint and the translation check**

Run: `uv run pytest tests/unit/web -q` then `uv run just lint`
Expected: PASS, including `test_every_template_string_has_an_english_translation`. If the extractor reports a msgid with different whitespace than the catalog (multi-line `{% trans %}` blocks are whitespace-sensitive), copy the msgid exactly as `missing_translations()` prints it.

- [ ] **Step 8: Commit**

```bash
git add src/tutor/web tests/unit/web
git commit -m "feat(web): app factory, views, security headers, error and public pages"
```

### Task 11: Server-side sessions, CSRF and the login guard

**Files:**
- Create: `src/tutor/web/sessions.py`
- Create: `src/tutor/web/deps.py`
- Create: `src/tutor/web/static/icons/sprite.svg` (stub with empty symbols; Task 13 draws them)
- Modify: `src/tutor/web/app.py` (add the middleware and the `NotAuthenticated` handler)
- Test: `tests/unit/web/test_web_sessions.py`

**Interfaces:**
- Consumes: `WebSession`, `WebSessionStore`, `WebDeps` (Task 7); `WebConfig` (Task 7); `render`, `is_htmx` (Task 10); `safe_next` (Task 10).
- Produces:
  - `tutor.web.sessions`: `COOKIE = "__Host-tutor_session"`; `hash_token(token: str) -> str`; `SessionHolder` (attributes `session: WebSession | None`; methods `login(user_id: UUID) -> None`, `logout() -> None`); `ServerSessionMiddleware(app, deps: WebDeps, config: WebConfig)`. The middleware exposes the holder as `request.state.web` and the session's `data` dict as `request.session` (Authlib stores its OAuth state there, so the dashboard needs no second cookie).
  - `tutor.web.deps`: `class NotAuthenticated(Exception)`; FastAPI dependencies `current_user(request: Request) -> User`, `optional_user(request: Request) -> User | None`, `require_admin(user: User = Depends(current_user)) -> User` (404 for non-admins), `require_csrf(request: Request) -> None` (403 on a missing or wrong token for any non-GET/HEAD/OPTIONS request), `get_deps(request: Request) -> WebDeps`, `get_config(request: Request) -> WebConfig`; constant `APP_ROUTER_DEPS = [Depends(require_csrf)]` that every authenticated router passes as `dependencies=`.

- [ ] **Step 1: Create the stub sprite**

`src/tutor/web/static/icons/sprite.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg"><symbol id="home" viewBox="0 0 24 24"/><symbol id="plan" viewBox="0 0 24 24"/><symbol id="progress" viewBox="0 0 24 24"/><symbol id="sessions" viewBox="0 0 24 24"/><symbol id="glossary" viewBox="0 0 24 24"/><symbol id="reports" viewBox="0 0 24 24"/><symbol id="account" viewBox="0 0 24 24"/><symbol id="more" viewBox="0 0 24 24"/></svg>
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/web/test_web_sessions.py`:

```python
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import User
from tutor.web.deps import APP_ROUTER_DEPS, current_user
from tutor.web.memory import FixedClock, MemoryBackend
from tutor.web.sessions import COOKIE, hash_token

from .conftest import BASE


def _probe_router() -> APIRouter:
    router = APIRouter(dependencies=APP_ROUTER_DEPS)

    @router.get("/app/probe")
    async def probe(user: User = Depends(current_user)) -> PlainTextResponse:
        return PlainTextResponse(user.display_name)

    @router.post("/app/probe")
    async def probe_post(user: User = Depends(current_user)) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @router.post("/probe/login/{user_id}")
    async def probe_login(user_id: UUID, request: Request) -> PlainTextResponse:
        request.state.web.login(user_id)
        return PlainTextResponse("in")

    @router.get("/probe/anon-write")
    async def anon_write(request: Request) -> PlainTextResponse:
        request.session["_state_google_x"] = {"n": 1}
        return PlainTextResponse("stored")

    return router


def _client(app: FastAPI) -> TestClient:
    app.include_router(_probe_router())
    return TestClient(app, base_url=BASE, follow_redirects=False)


def test_anonymous_request_sets_no_cookie(app: FastAPI) -> None:
    c = _client(app)
    response = c.get("/login")
    assert COOKIE not in response.cookies


def test_anonymous_session_data_creates_one_cookie(app: FastAPI) -> None:
    c = _client(app)
    response = c.get("/probe/anon-write")
    header = response.headers["set-cookie"]
    assert header.startswith(f"{COOKIE}=")
    for attr in ("Path=/", "Secure", "HttpOnly", "SameSite=lax"):
        assert attr.lower() in header.lower()


def test_unauthenticated_page_redirects_and_htmx_gets_401(app: FastAPI) -> None:
    c = _client(app)
    page = c.get("/app/probe")
    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=%2Fapp%2Fprobe"
    partial = c.get("/app/probe", headers={"hx-request": "true"})
    assert partial.status_code == 401
    assert partial.headers["hx-redirect"] == "/login?next=%2Fapp%2Fprobe"


def test_session_expires_after_idle_limit(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    from tutor.web.ports import WebSession

    c = _client(app)
    token = "t" * 43
    ana = next(u for u in backend.users.values() if u.display_name == "Ana")
    backend.save_session(WebSession(hash_token(token), ana.id, "csrf", clock(), clock()))
    c.cookies.set(COOKIE, token)
    assert c.get("/app/probe").text == "Ana"
    clock.advance(timedelta(days=15))
    assert c.get("/app/probe").status_code == 303
    assert backend.load_session(hash_token(token)) is None


def test_absolute_lifetime_even_when_active(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    from tutor.web.ports import WebSession

    c = _client(app)
    token = "u" * 43
    ana = next(u for u in backend.users.values() if u.display_name == "Ana")
    backend.save_session(WebSession(hash_token(token), ana.id, "csrf", clock(), clock()))
    c.cookies.set(COOKIE, token)
    for _ in range(4):
        clock.advance(timedelta(days=8))
        response = c.get("/app/probe")
    assert response.status_code == 303  # 32 days after creation


def test_post_without_or_with_wrong_csrf_is_403(
    app: FastAPI, backend: MemoryBackend, clock: FixedClock, demo: object
) -> None:
    from tutor.web.ports import WebSession

    c = _client(app)
    token = "v" * 43
    ana = next(u for u in backend.users.values() if u.display_name == "Ana")
    backend.save_session(WebSession(hash_token(token), ana.id, "right", clock(), clock()))
    c.cookies.set(COOKIE, token)
    assert c.post("/app/probe").status_code == 403
    assert c.post("/app/probe", headers={"x-csrf-token": "wrong"}).status_code == 403
    assert c.post("/app/probe", headers={"x-csrf-token": "right"}).text == "ok"
    assert c.post("/app/probe", data={"csrf_token": "right"}).text == "ok"


def test_login_rotates_the_session_token(
    app: FastAPI, backend: MemoryBackend, demo: object
) -> None:
    c = _client(app)
    c.get("/probe/anon-write")
    before = c.cookies.get(COOKIE)
    assert before is not None
    ana = next(u for u in backend.users.values() if u.display_name == "Ana")
    anon = backend.load_session(hash_token(before))
    assert anon is not None
    response = c.post(f"/probe/login/{ana.id}", headers={"x-csrf-token": anon.csrf_token})
    assert response.status_code == 200
    after = c.cookies.get(COOKIE)
    assert after and after != before
    assert backend.load_session(hash_token(before)) is None
    new = backend.load_session(hash_token(after))
    assert new is not None and new.user_id == ana.id
    assert not any(k.startswith("_state_") for k in new.data)
```

Note on the CSRF rule for anonymous sessions: an anonymous session has a CSRF token too, so `/probe/login` above is only reachable with the anonymous session's token, exactly like a real form would send it.

Run: `uv run pytest tests/unit/web/test_web_sessions.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.web.deps'`.

- [ ] **Step 3: Implement `sessions.py`**

`src/tutor/web/sessions.py`:

```python
"""Server-side sessions behind one cookie (spec 9.3).

The cookie holds a random token; the store keeps only its SHA-256. The session's
`data` dict is exposed as `request.session` so Authlib can keep OAuth state in it.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tutor.web.config import WebConfig
from tutor.web.ports import WebDeps, WebSession

COOKIE = "__Host-tutor_session"
_TOUCH_EVERY = timedelta(minutes=5)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionHolder:
    def __init__(self, session: WebSession | None) -> None:
        self.session = session
        self.login_as: UUID | None = None
        self.logged_out = False

    def login(self, user_id: UUID) -> None:
        self.login_as = user_id

    def logout(self) -> None:
        self.logged_out = True


class ServerSessionMiddleware:
    def __init__(self, app: ASGIApp, deps: WebDeps, config: WebConfig) -> None:
        self.app = app
        self.deps = deps
        self.idle = timedelta(days=config.session_idle_days)
        self.absolute = timedelta(days=config.session_max_days)

    def _load(self, token: str | None, now: datetime) -> WebSession | None:
        if not token:
            return None
        session = self.deps.sessions.load_session(hash_token(token))
        if session is None:
            return None
        if now - session.last_seen_at > self.idle or now - session.created_at > self.absolute:
            self.deps.sessions.delete_session(session.token_hash)
            return None
        return session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        now = self.deps.clock()
        session = self._load(HTTPConnection(scope).cookies.get(COOKIE), now)
        holder = SessionHolder(session)
        scope.setdefault("state", {})["web"] = holder
        data: dict[str, Any] = dict(session.data) if session else {}
        scope["session"] = data
        original = dict(data)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                cookie = self._commit(holder, data, original, now)
                if cookie is not None:
                    MutableHeaders(scope=message).append("set-cookie", cookie)
            await send(message)

        await self.app(scope, receive, send_wrapper)

    def _cookie(self, token: str, max_age: int) -> str:
        return f"{COOKIE}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"

    def _new(self, user_id: UUID | None, data: dict[str, Any], now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        self.deps.sessions.save_session(
            WebSession(hash_token(token), user_id, secrets.token_urlsafe(32), now, now, data)
        )
        return self._cookie(token, int(self.absolute.total_seconds()))

    def _commit(
        self,
        holder: SessionHolder,
        data: dict[str, Any],
        original: dict[str, Any],
        now: datetime,
    ) -> str | None:
        session = holder.session
        if holder.logged_out:
            if session is not None:
                self.deps.sessions.delete_session(session.token_hash)
            return self._cookie("", 0)
        if holder.login_as is not None:
            if session is not None:
                self.deps.sessions.delete_session(session.token_hash)
            kept = {k: v for k, v in data.items() if not k.startswith("_state_")}
            return self._new(holder.login_as, kept, now)
        if session is not None:
            if data != original or now - session.last_seen_at > _TOUCH_EVERY:
                session.data = data
                session.last_seen_at = now
                self.deps.sessions.save_session(session)
            return None
        if data:
            return self._new(None, data, now)
        return None
```

- [ ] **Step 4: Implement `deps.py`**

`src/tutor/web/deps.py`:

```python
"""FastAPI dependencies shared by every page."""

from __future__ import annotations

import secrets
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request

from tutor.domain.dashboard.types import Role, User
from tutor.web.config import WebConfig
from tutor.web.ports import WebDeps

CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class NotAuthenticated(Exception):
    def __init__(self, next_path: str) -> None:
        super().__init__(next_path)
        self.next_path = next_path


def get_deps(request: Request) -> WebDeps:
    deps: WebDeps = request.app.state.deps
    return deps


def get_config(request: Request) -> WebConfig:
    config: WebConfig = request.app.state.config
    return config


def optional_user(request: Request) -> User | None:
    holder = getattr(request.state, "web", None)
    session = getattr(holder, "session", None)
    if session is None or session.user_id is None:
        return None
    user = get_deps(request).users.find_user(session.user_id)
    if user is None or user.deletion_requested_at is not None:
        return None
    request.state.user = user
    return user


def current_user(request: Request) -> User:
    user = optional_user(request)
    if user is None:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise NotAuthenticated(target)
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role is not Role.ADMIN:
        raise HTTPException(status_code=404)
    return user


async def require_csrf(request: Request) -> None:
    if request.method in _SAFE_METHODS:
        return
    holder = getattr(request.state, "web", None)
    session = getattr(holder, "session", None)
    expected = session.csrf_token if session is not None else None
    sent = request.headers.get(CSRF_HEADER)
    if sent is None and request.headers.get("content-type", "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    ):
        field = (await request.form()).get(CSRF_FIELD)
        sent = field if isinstance(field, str) else None
    if not expected or not sent or not secrets.compare_digest(sent, expected):
        raise HTTPException(status_code=403)


APP_ROUTER_DEPS = [Depends(require_csrf)]


def login_redirect_target(next_path: str) -> str:
    return f"/login?next={quote(next_path, safe='')}"
```

- [ ] **Step 5: Wire the middleware and the redirect handler**

In `src/tutor/web/app.py`, add the imports:

```python
from fastapi.responses import RedirectResponse

from tutor.web.deps import NotAuthenticated, login_redirect_target
from tutor.web.sessions import ServerSessionMiddleware
```

In `create_app`, replace `app.add_middleware(SecurityHeadersMiddleware)` with:

```python
    app.add_middleware(ServerSessionMiddleware, deps=deps, config=config)
    app.add_middleware(SecurityHeadersMiddleware)  # outermost: headers on every response
```

In `_install_error_handlers`, add before the `HTTPException` handler:

```python
    @app.exception_handler(NotAuthenticated)
    async def not_authenticated(request: Request, exc: NotAuthenticated) -> Response:
        target = login_redirect_target(exc.next_path)
        if is_htmx(request):
            return Response(status_code=401, headers={"HX-Redirect": target})
        return RedirectResponse(target, status_code=303)
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/unit/web -q` then `uv run just lint`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/tutor/web tests/unit/web
git commit -m "feat(web): server-side session cookie, CSRF and login guard"
```

### Task 12: Google login, logout, language switch, test login and the local demo

**Files:**
- Create: `src/tutor/web/google.py`
- Create: `src/tutor/web/routes/auth.py`
- Create: `src/tutor/web/demo_server.py`
- Create: `src/tutor/web/templates/pages/deletion_pending.html`
- Create: `src/tutor/web/templates/pages/test_login.html`
- Modify: `src/tutor/web/routes/__init__.py` (append `auth.router`)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `justfile` (add `dashboard-demo`)
- Test: `tests/unit/web/test_web_auth.py`, `tests/unit/web/test_web_google.py`

**Interfaces:**
- Consumes: `GoogleLogin`, `GoogleIdentity`, `LoginFailed` (Task 7); `SessionHolder` (Task 11); `APP_ROUTER_DEPS`, `current_user`, `get_deps` (Task 11); `safe_next`, `render` (Task 10); `seed_demo` (Task 8).
- Produces: routes `GET /auth/google`, `GET /auth/callback`, `POST /auth/logout`, `POST /app/lang`, and, only when `config.test_login`, `GET /auth/test-login` (lists demo users) and `POST /auth/test-login` (form `user_id`); `GoogleOidcLogin(client_id: str, client_secret: str)` implementing `GoogleLogin`; `tutor.web.demo_server:app`; recipe `just dashboard-demo`.

The test-login POST is deliberately outside CSRF protection (it exists only with `TUTOR_ENV=test`); the route sweep in Task 29 lists it as the only exemption besides the Stripe webhook.

- [ ] **Step 1: Write the failing route tests**

`tests/unit/web/test_web_auth.py`:

```python
from collections.abc import Callable
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import FakeGoogle, MemoryBackend
from tutor.web.ports import GoogleIdentity
from tutor.web.sessions import COOKIE

from .conftest import csrf_of


def test_google_round_trip_creates_user_and_lands_on_next(
    client: TestClient, google: FakeGoogle, backend: MemoryBackend
) -> None:
    google.next_identity = GoogleIdentity("g-123", "carla@example.com", "Carla", True)
    start = client.get("/auth/google?next=/app/glossary")
    assert start.status_code == 302
    callback = client.get(start.headers["location"].replace("https://testserver", ""))
    assert callback.status_code == 303 and callback.headers["location"] == "/app/glossary"
    assert any(u.display_name == "Carla" for u in backend.users.values())
    assert COOKIE in client.cookies


def test_failed_google_login_shows_error(client: TestClient, google: FakeGoogle) -> None:
    google.next_identity = None
    client.get("/auth/google")
    response = client.get("/auth/callback?code=x&state=y")
    assert response.status_code == 400
    assert "No pudimos iniciar sesión con Google" in response.text


def test_open_redirect_in_next_is_ignored(client: TestClient, google: FakeGoogle) -> None:
    google.next_identity = GoogleIdentity("g-9", "x@example.com", "X", True)
    start = client.get("/auth/google?next=https://evil.example")
    callback = client.get(start.headers["location"].replace("https://testserver", ""))
    assert callback.headers["location"] == "/app/"


def test_deleted_user_cannot_sign_in(
    client: TestClient, google: FakeGoogle, backend: MemoryBackend, demo: DemoUsers
) -> None:
    from .conftest import NOW

    backend.request_deletion(demo.ana, NOW)
    google.next_identity = GoogleIdentity("demo-ana", "ana@example.com", "Ana", True)
    start = client.get("/auth/google")
    response = client.get(start.headers["location"].replace("https://testserver", ""))
    assert response.status_code == 200
    assert "Tu cuenta se está eliminando" in response.text
    assert COOKIE not in client.cookies


def test_logout_clears_cookie_and_site_data(
    login: Callable[[UUID], TestClient], demo: DemoUsers
) -> None:
    c = login(demo.ana)
    token = csrf_of(c)
    response = c.post("/auth/logout", data={"csrf_token": token})
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert response.headers["clear-site-data"] == '"cache", "cookies", "storage"'
    assert c.get("/app/account").status_code == 303


def test_language_switch_saves_preference(
    login: Callable[[UUID], TestClient], demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    response = c.post(
        "/app/lang", data={"csrf_token": csrf_of(c), "lang": "en", "back": "/app/glossary"}
    )
    assert response.status_code == 303 and response.headers["location"] == "/app/glossary"
    assert backend.users[demo.ana].lang is Lang.EN


def test_test_login_page_lists_demo_users(client: TestClient) -> None:
    page = client.get("/auth/test-login")
    assert page.status_code == 200 and "Ana" in page.text
```

Run: `uv run pytest tests/unit/web/test_web_auth.py -q`
Expected: FAIL (routes return 404).

- [ ] **Step 2: Implement the routes**

`src/tutor/web/routes/auth.py`:

```python
"""Login with Google, logout, language switch and the test-only login."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.types import Lang, User
from tutor.web.config import WebConfig
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_config, get_deps
from tutor.web.ports import LoginFailed
from tutor.web.security import safe_next
from tutor.web.views import render

router = APIRouter()
app_router = APIRouter(dependencies=APP_ROUTER_DEPS)
_NEXT_KEY = "login_next"


@router.get("/auth/google")
async def google_start(request: Request, next: str | None = None) -> Response:
    request.session[_NEXT_KEY] = safe_next(next)
    config = get_config(request)
    return await get_deps(request).google.redirect(request, f"{config.base_url}/auth/callback")


@router.get("/auth/callback")
async def google_callback(request: Request) -> Response:
    deps = get_deps(request)
    try:
        identity = await deps.google.identity(request)
    except LoginFailed:
        return render(request, "pages/login.html", {"next": "", "error": True}, status_code=400)
    user = deps.users.sign_in(identity, deps.clock())
    if user.deletion_requested_at is not None:
        request.state.web.logout()  # drop the anonymous login session and its cookie
        return render(request, "pages/deletion_pending.html")
    target = safe_next(request.session.pop(_NEXT_KEY, None))
    request.state.web.login(user.id)
    return RedirectResponse(target, status_code=303)


@app_router.post("/auth/logout")
async def logout(request: Request) -> Response:
    request.state.web.logout()
    return RedirectResponse(
        "/login",
        status_code=303,
        headers={"Clear-Site-Data": '"cache", "cookies", "storage"'},
    )


@app_router.post("/app/lang")
async def switch_lang(
    request: Request,
    user: User = Depends(current_user),
    lang: str = Form(...),
    back: str = Form("/app/"),
) -> Response:
    if lang not in {item.value for item in Lang}:
        raise HTTPException(status_code=422)
    deps = get_deps(request)
    prefs = deps.reader.account(user.id).prefs
    deps.account.set_preferences(user.id, replace(prefs, lang=Lang(lang)))
    return RedirectResponse(safe_next(back), status_code=303)


test_router = APIRouter()


@test_router.get("/auth/test-login", response_class=HTMLResponse)
async def test_login_page(request: Request) -> HTMLResponse:
    users = getattr(get_deps(request).users, "users", {})
    return render(request, "pages/test_login.html", {"demo_users": list(users.values())})


@test_router.post("/auth/test-login")
async def test_login(request: Request, user_id: UUID = Form(...)) -> Response:
    if get_deps(request).users.find_user(user_id) is None:
        raise HTTPException(status_code=404)
    request.state.web.login(user_id)
    return RedirectResponse("/app/", status_code=303)


def routers(config: WebConfig) -> list[APIRouter]:
    return [router, app_router, *([test_router] if config.test_login else [])]
```

`/auth/logout` sits on `app_router` so it is CSRF-checked but needs no `current_user` (logging out an expired session must still clear the cookie).

In `src/tutor/web/routes/__init__.py`, change `all_routers` to:

```python
def all_routers(config: WebConfig) -> list[APIRouter]:
    from tutor.web.routes import auth, public

    routers: list[APIRouter] = [public.router, *auth.routers(config)]
    return routers
```

`src/tutor/web/templates/pages/deletion_pending.html`:

```html
{% extends "layouts/public.html" %}
{% block title %}{{ _("Cuenta en eliminación") }} · English Tutor{% endblock %}
{% block content %}
<section class="card card--narrow">
  <h1>{{ _("Tu cuenta se está eliminando") }}</h1>
  <p>{{ _("Recibimos tu solicitud. Borraremos todos tus datos en un máximo de 24 horas.") }}</p>
  <p>{% trans email=config.support_email %}Si fue un error, escríbenos hoy a {{ email }}.{% endtrans %}</p>
</section>
{% endblock %}
```

`src/tutor/web/templates/pages/test_login.html` (test and demo only; never reachable in production):

```html
{% extends "layouts/public.html" %}
{% block title %}Demo login{% endblock %}
{% block content %}
<section class="card card--narrow">
  <h1>Demo login</h1>
  {% for u in demo_users %}
  <form method="post" action="/auth/test-login" class="inline-form">
    <input type="hidden" name="user_id" value="{{ u.id }}">
    <button class="btn btn--ghost btn--block">{{ u.display_name }} · {{ u.role }}</button>
  </form>
  {% endfor %}
</section>
{% endblock %}
```

`test_login.html` uses untranslated English on purpose; add `{# i18n: skip #}` at the top. Then make `missing_translations()` skip files whose first line is `{# i18n: skip #}`: in `i18n.py`, before the extraction loop, collect `skip = {p.relative_to(TEMPLATES_DIR).as_posix() for p in TEMPLATES_DIR.rglob("*.html") if p.read_text(encoding="utf-8").startswith("{# i18n: skip #}")}` and `continue` when the yielded filename (relative path) is in `skip`. This template has no `_()` calls, so in practice it yields nothing; the marker documents intent.

Add to the English catalog:

```po
msgid "Cuenta en eliminación"
msgstr "Account being deleted"

msgid "Tu cuenta se está eliminando"
msgstr "Your account is being deleted"

msgid "Recibimos tu solicitud. Borraremos todos tus datos en un máximo de 24 horas."
msgstr "We received your request. We'll delete all your data within 24 hours."

msgid "Si fue un error, escríbenos hoy a %(email)s."
msgstr "If this was a mistake, write to us today at %(email)s."
```

- [ ] **Step 3: Run the route tests**

Run: `uv run pytest tests/unit/web/test_web_auth.py -q`
Expected: PASS. `csrf_of` loads `/app/account`, which Task 26 builds; until then add this temporary route at the end of `routes/auth.py` and delete it in Task 26 Step 3 (Task 26 says so):

```python
@app_router.get("/app/account", response_class=HTMLResponse)
async def account_stub(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    return render(request, "layouts/app.html", {"active_nav": "account"})
```

- [ ] **Step 4: Write the Google adapter and its test**

Re-verify with Context7 (`/authlib/authlib`) that `authorize_access_token` still returns the validated ID-token claims under `token["userinfo"]` in the installed Authlib version before writing this file.

`src/tutor/web/google.py`:

```python
"""Google OIDC through Authlib. State, nonce and PKCE data live in `request.session`,
which the server-side session middleware backs (no extra cookie)."""

from __future__ import annotations

from typing import Any

from authlib.integrations.starlette_client import OAuth, OAuthError
from starlette.requests import Request
from starlette.responses import Response

from tutor.web.ports import GoogleIdentity, LoginFailed

GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"


class GoogleOidcLogin:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self._oauth = OAuth()
        self._oauth.register(
            "google",
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url=GOOGLE_METADATA,
            client_kwargs={"scope": "openid email profile"},
        )

    @property
    def client(self) -> Any:
        return self._oauth.google

    async def redirect(self, request: Request, redirect_uri: str) -> Response:
        response: Response = await self.client.authorize_redirect(request, redirect_uri)
        return response

    async def identity(self, request: Request) -> GoogleIdentity:
        try:
            token = await self.client.authorize_access_token(request)
        except OAuthError as exc:
            raise LoginFailed(exc.error or "oauth_error") from exc
        return identity_from_claims(token.get("userinfo") or {})


def identity_from_claims(info: dict[str, Any]) -> GoogleIdentity:
    sub, email = info.get("sub"), info.get("email")
    if not sub or not email or info.get("email_verified") is not True:
        raise LoginFailed("missing or unverified claims")
    return GoogleIdentity(str(sub), str(email), str(info.get("name") or ""), True)
```

`tests/unit/web/test_web_google.py`:

```python
import pytest

from tutor.web.google import identity_from_claims
from tutor.web.ports import LoginFailed


def test_verified_claims_become_identity() -> None:
    ident = identity_from_claims(
        {"sub": "123", "email": "a@example.com", "email_verified": True, "name": "Ana"}
    )
    assert (ident.sub, ident.email, ident.name) == ("123", "a@example.com", "Ana")


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"sub": "1", "email": "a@example.com"},
        {"sub": "1", "email": "a@example.com", "email_verified": "true"},
        {"email": "a@example.com", "email_verified": True},
    ],
)
def test_missing_or_unverified_claims_fail(claims: dict[str, object]) -> None:
    with pytest.raises(LoginFailed):
        identity_from_claims(claims)
```

Run: `uv run pytest tests/unit/web/test_web_google.py -q`
Expected: PASS.

- [ ] **Step 5: Add the local demo server and recipe**

`src/tutor/web/demo_server.py`:

```python
"""`just dashboard-demo`: the dashboard over in-memory demo data. No Google, no Stripe."""

from __future__ import annotations

from datetime import UTC, datetime

from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import seed_demo
from tutor.web.memory import MemoryBackend, memory_deps

_backend = MemoryBackend()
seed_demo(_backend, datetime.now(UTC).date())
app = create_app(
    memory_deps(_backend, lambda: datetime.now(UTC)),
    WebConfig(
        env="test",
        base_url="http://localhost:8780",
        mcp_url="http://localhost:8780/mcp",
        support_email="soporte@example.test",
        test_login=True,
    ),
)
```

Append to `justfile`:

```just
# Dashboard over in-memory demo data (no Google, no Stripe); open http://localhost:8780/auth/test-login
dashboard-demo:
    uv run uvicorn tutor.web.demo_server:app --port 8780 --reload
```

Run: `uv run just dashboard-demo`, open `http://localhost:8780/auth/test-login`, click "Ana", and check that `/app/account` renders the shell. Stop the server.
Expected: the shell renders (unstyled until Task 13).

- [ ] **Step 6: Run everything and commit**

Run: `uv run just check-fast`
Expected: PASS.

```bash
git add src/tutor/web tests/unit/web justfile
git commit -m "feat(web): Google login, logout, language switch and local demo"
```

## Part C — Look and install

### Task 13: Design tokens, fonts, icons and layout CSS

**Files:**
- Modify: `src/tutor/web/static/css/app.css` (replace the Task 10 placeholder completely)
- Modify: `src/tutor/web/static/icons/sprite.svg` (replace the Task 11 stub)
- Create: `src/tutor/web/static/fonts/baloo2-latin.woff2`, `src/tutor/web/static/fonts/publicsans-latin.woff2`
- Create: `src/tutor/web/static/fonts/OFL-Baloo2.txt`, `src/tutor/web/static/fonts/OFL-PublicSans.txt`
- Test: `tests/unit/web/test_web_look.py`, `tests/unit/web/test_web_template_safety.py`

**Interfaces:**
- Consumes: the class vocabulary and the motion hooks in "Conventions for page tasks"; `layouts/app.html`, `layouts/public.html`, `base.html` (Task 10).
- Produces:
  - Every class in the vocabulary, styled, plus these extra classes that page tasks may use: `trail__progress` (solid path from the first node to today; carries `data-draw`), `trail__label` (SVG `<text>` under a trail node), `table--stack` (on `<table class="table table--stack">`: below 600 px each row becomes a card and each `<td>` shows its `data-label` attribute as its heading), `row--saved` (on a `<tr>` returned after an inline save: one leaf tint fade), `celebrate__leaf`, `celebrate__leaf--a`, `celebrate__leaf--b`, `celebrate__leaf--c` (used by `app.js`).
  - CSS custom properties: `--paper`, `--card`, `--ink`, `--ink-muted`, `--line`, `--leaf`, `--leaf-strong`, `--coral`, `--sun`, `--sidebar`, `--on-sidebar`, `--primary`, `--on-primary`, `--danger`, `--on-danger`, `--warn-bg`, `--info-bg`, `--ok-bg`, `--focus`, `--radius`, `--radius-sm`, `--shadow`, `--font-head`, `--font-body`, `--ease-out`, `--tabbar-h`.
  - Font families `"Baloo 2"` (headings, numbers; weights 500–800) and `"Public Sans"` (body; weights 400–700).
  - Icon ids in `sprite.svg`: `home`, `plan`, `progress`, `sessions`, `glossary`, `reports`, `account`, `more`, `copy`, `check`, `install`, `share`, `connect`, `settings`, `add`, `close`. Use as `<svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#copy"></use></svg>`.
  - `tests/unit/web/test_web_template_safety.py`: the template-safety rules (no `style=`, `<style>`, inline `<script>`, `hx-on`, `on*=` handlers, `hx-vals="js:…"`, `|safe`; no `Markup(` in `src/tutor/web/**/*.py`). Task 29 extends this file instead of duplicating it.
- Layout notes for page tasks: below 900 px the sidebar collapses into a slim top bar that shows only the brand and the plan chip (the Free meter text is shown down to 400 px), so the language switch and logout are hidden there; Task 26 (Cuenta) must offer both.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_look.py`:

```python
import gzip
import re
from pathlib import Path

import tutor.web

WEB = Path(tutor.web.__file__).parent
STATIC = WEB / "static"
TEMPLATES = WEB / "templates"

VOCABULARY = """
shell sidebar sidebar__nav sidebar__foot nav-link nav-link--active tabbar tabbar__link
tabbar__link--active tabbar__more tabbar__menu brand brand--light main public-head public-main
public-foot page-head page-head__actions grid card card--wide card--narrow card__title card__foot
banner banner--warn banner--info chip chip--free chip--annual chip--ok chip--warn chip--muted btn
btn--primary btn--ghost btn--small btn--block btn--danger copy meter meter__track meter__fill
meter-link kpi kpi__value kpi__label kpi__target trail trail__path trail__node trail__node--done
trail__node--today trail__node--upcoming trail__node--missed trail__node--rest trail__extra streak
stamps stamp stamp--used table-wrap table filters empty muted prose form-row form-error inline-form
field-error chart chart__line chart__target chart__dot as-table said correct diff diff--added
diff--removed diff--moved skeleton error-box skip-link sr-only icon steps code-copy celebrate
""".split()
EXTRA_CLASSES = [
    "trail__progress",
    "trail__label",
    "table--stack",
    "row--saved",
    "celebrate__leaf",
    "celebrate__leaf--a",
    "celebrate__leaf--b",
    "celebrate__leaf--c",
    "vt-today",
    "vt-last-session",
    "htmx-indicator",
]
NAV_ICONS = {"home", "plan", "progress", "sessions", "glossary", "reports", "account"}
REQUIRED_ICONS = NAV_ICONS | {
    "more",
    "copy",
    "check",
    "install",
    "share",
    "connect",
    "settings",
    "add",
    "close",
}


def css() -> str:
    return (STATIC / "css" / "app.css").read_text(encoding="utf-8")


def test_css_is_within_budget() -> None:
    assert len(gzip.compress(css().encode("utf-8"), 9)) <= 30 * 1024


def test_every_vocabulary_class_is_styled() -> None:
    text = css()
    missing = [
        name
        for name in VOCABULARY + EXTRA_CLASSES
        if not re.search(rf"\.{re.escape(name)}(?![\w-])", text)
    ]
    assert missing == []


def test_two_font_families_within_budget_with_licenses() -> None:
    fonts = sorted((STATIC / "fonts").glob("*.woff2"))
    assert [f.name for f in fonts] == ["baloo2-latin.woff2", "publicsans-latin.woff2"]
    assert sum(f.stat().st_size for f in fonts) <= 120 * 1024
    families = set(re.findall(r'@font-face\s*\{[^}]*font-family:\s*"([^"]+)"', css()))
    assert families == {"Baloo 2", "Public Sans"}
    for license_file in ("OFL-Baloo2.txt", "OFL-PublicSans.txt"):
        assert (
            "SIL OPEN FONT LICENSE"
            in (STATIC / "fonts" / license_file).read_text(encoding="utf-8").upper()
        )


def test_every_token_has_a_dark_value() -> None:
    dark = css().split("@media (prefers-color-scheme: dark)", 1)[1]
    for token in (
        "--paper",
        "--card",
        "--ink",
        "--ink-muted",
        "--line",
        "--leaf-strong",
        "--primary",
        "--on-primary",
        "--danger",
        "--on-danger",
        "--warn-bg",
        "--focus",
        "--sidebar",
    ):
        assert f"{token}:" in dark, token


def test_reduced_motion_is_honoured_both_ways() -> None:
    text = css()
    assert "@media (prefers-reduced-motion: reduce)" in text
    assert "body[data-reduce-motion]" in text
    assert "@view-transition" in text


def test_only_cheap_properties_are_animated() -> None:
    keyframes = re.findall(r"@keyframes\s+[\w-]+\s*\{(.*?)\}\s*\}", css(), flags=re.S)
    assert keyframes
    for body in keyframes:
        props = set(re.findall(r"([a-z-]+)\s*:", body))
        assert props <= {"transform", "opacity", "stroke-dashoffset"}, props


def _sprite_ids() -> set[str]:
    sprite = (STATIC / "icons" / "sprite.svg").read_text(encoding="utf-8")
    return set(re.findall(r'<symbol id="([\w-]+)"', sprite))


def test_sprite_has_every_icon_templates_use() -> None:
    used: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        used |= set(re.findall(r"sprite\.svg'\) \}\}#([\w-]+)", path.read_text(encoding="utf-8")))
    assert used | REQUIRED_ICONS <= _sprite_ids()
```

`tests/unit/web/test_web_template_safety.py`:

```python
"""Template and code rules that keep the CSP strict and learner text inert (spec 9.6).
Task 29 extends this file; do not duplicate these checks elsewhere."""

import re
from pathlib import Path

import tutor.web

WEB = Path(tutor.web.__file__).parent
TEMPLATES = WEB / "templates"
ALLOW = "safe: static"  # a line carrying this comment may mark a named constant safe

TEMPLATE_RULES: dict[str, re.Pattern[str]] = {
    "inline style attribute": re.compile(r"\sstyle\s*="),
    "style element": re.compile(r"<style\b", re.IGNORECASE),
    "inline script": re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE),
    "hx-on attribute": re.compile(r"\bhx-on"),
    "event handler attribute": re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE),
    "js: in hx-vals": re.compile(r"hx-vals\s*=\s*[\"']js:"),
    "safe filter": re.compile(r"\|\s*safe\b"),
}
JS_TEMPLATE_RULES = ("safe filter",)


def template_violations() -> list[str]:
    found: list[str] = []
    for path in sorted(TEMPLATES.rglob("*")):
        if path.suffix not in {".html", ".js"}:
            continue
        rules = (
            TEMPLATE_RULES
            if path.suffix == ".html"
            else {k: TEMPLATE_RULES[k] for k in JS_TEMPLATE_RULES}
        )
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ALLOW in line:
                continue
            for name, pattern in rules.items():
                if pattern.search(line):
                    found.append(f"{path.relative_to(TEMPLATES)}:{number}: {name}: {line.strip()}")
    return found


def python_violations() -> list[str]:
    found: list[str] = []
    for path in sorted(WEB.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "Markup(" in line and ALLOW not in line:
                found.append(f"{path.relative_to(WEB)}:{number}: {line.strip()}")
    return found


def test_templates_have_no_inline_code_or_unsafe_output() -> None:
    assert template_violations() == []


def test_python_never_marks_text_safe() -> None:
    assert python_violations() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/web/test_web_look.py tests/unit/web/test_web_template_safety.py -q`
Expected: `test_web_template_safety.py` PASSES already (the Task 10–12 templates follow the rules); `test_web_look.py` FAILS: missing classes, no fonts, stub sprite without symbols such as `copy`.

- [ ] **Step 3: Download, trim and subset the fonts**

Both fonts are SIL OFL 1.1 from the official `google/fonts` repository. The variable fonts are trimmed to the weights the design uses, then subset to Latin and compressed to woff2. `brotli` is needed for woff2 output.

```bash
mkdir -p build/fonts src/tutor/web/static/fonts
curl -fL -o build/fonts/Baloo2.ttf "https://raw.githubusercontent.com/google/fonts/main/ofl/baloo2/Baloo2%5Bwght%5D.ttf"
curl -fL -o build/fonts/PublicSans.ttf "https://raw.githubusercontent.com/google/fonts/main/ofl/publicsans/PublicSans%5Bwght%5D.ttf"
curl -fL -o src/tutor/web/static/fonts/OFL-Baloo2.txt "https://raw.githubusercontent.com/google/fonts/main/ofl/baloo2/OFL.txt"
curl -fL -o src/tutor/web/static/fonts/OFL-PublicSans.txt "https://raw.githubusercontent.com/google/fonts/main/ofl/publicsans/OFL.txt"
uv run --with fonttools --with brotli fonttools varLib.instancer build/fonts/Baloo2.ttf wght=500:800 -o build/fonts/Baloo2-trim.ttf
uv run --with fonttools --with brotli fonttools varLib.instancer build/fonts/PublicSans.ttf wght=400:700 -o build/fonts/PublicSans-trim.ttf
LATIN="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2190-2193,U+2212,U+2215,U+FEFF,U+FFFD"
FEATURES="kern,liga,calt,ccmp,locl,mark,mkmk,tnum"
uv run --with fonttools --with brotli pyftsubset build/fonts/Baloo2-trim.ttf --unicodes="$LATIN" --layout-features="$FEATURES" --flavor=woff2 --output-file=src/tutor/web/static/fonts/baloo2-latin.woff2
uv run --with fonttools --with brotli pyftsubset build/fonts/PublicSans-trim.ttf --unicodes="$LATIN" --layout-features="$FEATURES" --flavor=woff2 --output-file=src/tutor/web/static/fonts/publicsans-latin.woff2
ls -l src/tutor/web/static/fonts
rm -rf build/fonts
```

Expected: two woff2 files whose sizes add up to at most 120 KB (122,880 bytes). If the sum is over budget, narrow Baloo 2 to `wght=600:800` and re-run its two commands; if still over, drop `calt` from `FEATURES`. If either download URL returns 404, open `https://github.com/google/fonts/tree/main/ofl/baloo2` (or `/publicsans`) and use the variable `.ttf` listed there; record the file name you used in the commit message.

- [ ] **Step 4: Draw the icon sprite**

Replace `src/tutor/web/static/icons/sprite.svg` with:

```svg
<svg xmlns="http://www.w3.org/2000/svg">
  <symbol id="home" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/></symbol>
  <symbol id="plan" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/></symbol>
  <symbol id="progress" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></symbol>
  <symbol id="sessions" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z"/></symbol>
  <symbol id="glossary" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M8 7h7M8 11h5"/></symbol>
  <symbol id="reports" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6M9 13h6M9 17h6"/></symbol>
  <symbol id="account" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></symbol>
  <symbol id="more" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></symbol>
  <symbol id="copy" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></symbol>
  <symbol id="check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></symbol>
  <symbol id="install" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></symbol>
  <symbol id="share" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12M8 7l4-4 4 4"/><path d="M5 12v8h14v-8"/></symbol>
  <symbol id="connect" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></symbol>
  <symbol id="settings" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></symbol>
  <symbol id="add" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="4"/><path d="M12 8v8M8 12h8"/></symbol>
  <symbol id="close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></symbol>
</svg>
```

The `stroke`, `fill` and `stroke-width` here are SVG presentation attributes, not `style` attributes, so the CSP allows them; `currentColor` makes each icon take the text colour of its link or button.

- [ ] **Step 5: Write the stylesheet**

Replace `src/tutor/web/static/css/app.css` with:

```css
/* English Tutor dashboard (spec sections 4, 5, 10, 12; plan Task 13).
   Markup carries no inline styles. Animations touch only transform, opacity and
   stroke-dashoffset, and every animated element's natural style is its final state, so
   reduced motion (or no animation at all) shows a complete page.

   Contrast pairs relied on (WCAG 2.1 relative luminance):
     light  --ink #11402c on --paper #e3f4ea ............ 10.3:1  body text
     light  --ink on --card #ffffff ...................... 11.7:1  body text
     light  #ffffff on --ink (sidebar, primary button) ... 11.7:1
     light  --ink-muted #3d6b55 on --paper / --card ...... 5.4:1 / 6.1:1
     light  --leaf-strong #1f7a4c on --paper / --card .... 4.7:1 / 5.3:1  links, focus ring
     light  #ffffff on --leaf-strong (used stamp) ......... 5.3:1
     light  #11402c on --sun #ffcc33 (streak, Annual) ..... 7.8:1
     light  --sun on --ink (focus ring in the sidebar) .... 7.8:1
     light  #ffffff on --danger #a8321a .................. 6.7:1
     light  --danger on --paper (field errors) ............ 5.9:1
     light  --leaf #2f9e66, --coral #ff6f4f .............. decorative only (3.4:1, 2.8:1 on white)
     dark   --ink #e3f4ea on --card #12291e .............. 13.5:1
     dark   --ink-muted #a9cbb8 on --card ................. 8.7:1
     dark   --leaf-strong #6fd6a3 on --card / --paper ..... 8.6:1 / 9.7:1  links, focus ring
     dark   --on-primary #0b1f16 on --primary #6fd6a3 ..... 9.7:1  buttons, used stamps
     dark   --on-danger #0b1f16 on --danger #ff8f7a ....... 7.7:1
     dark   --ink on --warn-bg #3a2e0e / --ok-bg #163a2a .. 11.7:1 / 11.0:1
*/

@font-face {
  font-family: "Baloo 2";
  src: url("../fonts/baloo2-latin.woff2") format("woff2");
  font-weight: 500 800;
  font-style: normal;
  font-display: swap;
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+2000-206F, U+20AC, U+2122, U+2190-2193, U+2212, U+2215, U+FEFF, U+FFFD;
}
@font-face {
  font-family: "Public Sans";
  src: url("../fonts/publicsans-latin.woff2") format("woff2");
  font-weight: 400 700;
  font-style: normal;
  font-display: swap;
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+2000-206F, U+20AC, U+2122, U+2190-2193, U+2212, U+2215, U+FEFF, U+FFFD;
}

:root {
  color-scheme: light dark;
  --paper: #e3f4ea;
  --card: #ffffff;
  --ink: #11402c;
  --ink-muted: #3d6b55;
  --line: #c9e2d4;
  --leaf: #2f9e66;
  --leaf-strong: #1f7a4c;
  --coral: #ff6f4f;
  --sun: #ffcc33;
  --sidebar: #11402c;
  --on-sidebar: #ffffff;
  --primary: #11402c;
  --on-primary: #ffffff;
  --danger: #a8321a;
  --on-danger: #ffffff;
  --warn-bg: #fff1d1;
  --info-bg: #ffffff;
  --ok-bg: #d7f0e2;
  --focus: #1f7a4c;
  --radius: 16px;
  --radius-sm: 10px;
  --shadow: 0 1px 2px rgba(17, 64, 44, 0.08), 0 4px 16px rgba(17, 64, 44, 0.06);
  --font-head: "Baloo 2", ui-rounded, system-ui, sans-serif;
  --font-body: "Public Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --tabbar-h: 64px;
}

@media (prefers-color-scheme: dark) {
  :root {
    --paper: #0b1f16;
    --card: #12291e;
    --ink: #e3f4ea;
    --ink-muted: #a9cbb8;
    --line: #24463a;
    --leaf: #4cc38a;
    --leaf-strong: #6fd6a3;
    --coral: #ff8a70;
    --sun: #ffd45c;
    --sidebar: #071510;
    --on-sidebar: #e3f4ea;
    --primary: #6fd6a3;
    --on-primary: #0b1f16;
    --danger: #ff8f7a;
    --on-danger: #0b1f16;
    --warn-bg: #3a2e0e;
    --info-bg: #12291e;
    --ok-bg: #163a2a;
    --focus: #6fd6a3;
    --shadow: none;
  }
}

/* ---- base ------------------------------------------------------------- */

*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 400 1rem/1.55 var(--font-body);
  overflow-wrap: break-word;
}
h1, h2, h3 { font-family: var(--font-head); font-weight: 700; line-height: 1.15; margin: 0 0 0.5rem; }
h1 { font-size: clamp(1.6rem, 4vw, 2.1rem); }
h2 { font-size: 1.3rem; }
h3 { font-size: 1.1rem; }
p { margin: 0 0 0.75rem; }
a { color: var(--leaf-strong); text-underline-offset: 2px; }
img, svg { max-width: 100%; }
button, input, select, textarea { font: inherit; color: inherit; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; border-radius: 4px; }
[hidden] { display: none !important; }

.skip-link {
  position: absolute; left: 8px; top: -64px; z-index: 100;
  background: var(--card); color: var(--ink); padding: 10px 14px;
  border-radius: var(--radius-sm); box-shadow: var(--shadow);
}
.skip-link:focus { top: 8px; }
.sr-only {
  position: absolute !important; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}
.muted { color: var(--ink-muted); }
.icon { width: 24px; height: 24px; flex: none; vertical-align: middle; }

/* ---- shell: sidebar (>= 900 px), top bar + tab bar (< 900 px) -------- */

.shell { min-height: 100vh; min-height: 100dvh; }
.main { min-width: 0; }
.sidebar { background: var(--sidebar); color: var(--on-sidebar); }
.sidebar :focus-visible { outline-color: var(--sun); }
.brand {
  display: inline-flex; align-items: center; min-height: 44px;
  font: 800 1.25rem var(--font-head); color: var(--ink); text-decoration: none;
}
.brand--light { color: var(--on-sidebar); }
.sidebar__nav { display: flex; flex-direction: column; gap: 4px; margin: 20px 0; }
.nav-link {
  display: flex; align-items: center; gap: 12px; min-height: 44px; padding: 8px 12px;
  border-radius: 12px; color: var(--on-sidebar); text-decoration: none; font-weight: 600;
  opacity: 0.85;
}
.nav-link:hover { opacity: 1; background: rgba(255, 255, 255, 0.08); }
.nav-link--active { opacity: 1; background: rgba(255, 255, 255, 0.14); }
.sidebar__foot { margin-top: auto; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.sidebar .btn--ghost { color: var(--on-sidebar); }
.meter-link {
  display: flex; flex-direction: column; gap: 6px; min-height: 44px; padding: 8px 0;
  color: inherit; text-decoration: none; font-size: 0.85rem; width: 100%;
}
.meter { display: block; width: 100%; height: 6px; }
.meter__track { fill: rgba(255, 255, 255, 0.22); }
.card .meter__track { fill: var(--line); }
.meter__fill { fill: var(--sun); transform-box: fill-box; transform-origin: left center; }
.card .meter__fill { fill: var(--leaf-strong); }

.tabbar { display: none; }
.tabbar__link {
  position: relative; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 2px; min-width: 56px; min-height: 52px; padding: 4px 6px;
  color: var(--ink-muted); text-decoration: none; font-size: 0.72rem; font-weight: 600;
  border-radius: 12px; cursor: pointer;
}
.tabbar__link::before {
  content: ""; position: absolute; top: 0; left: 18%; right: 18%; height: 3px;
  border-radius: 0 0 3px 3px; background: var(--leaf-strong);
  transform: scaleX(0); transition: transform 0.2s var(--ease-out);
}
.tabbar__link--active { color: var(--ink); }
.tabbar__link--active::before { transform: scaleX(1); }
.tabbar__more { position: relative; display: flex; }
.tabbar__more > summary { list-style: none; }
.tabbar__more > summary::-webkit-details-marker { display: none; }
.tabbar__menu {
  position: absolute; right: 0; bottom: calc(100% + 8px); margin: 0; padding: 6px;
  min-width: 180px; list-style: none; background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius-sm); box-shadow: var(--shadow);
}
.tabbar__menu a {
  display: flex; align-items: center; min-height: 44px; padding: 0 12px;
  color: var(--ink); text-decoration: none; border-radius: 8px;
}
.tabbar__menu a:hover { background: var(--paper); }

@media (min-width: 900px) {
  .shell { display: grid; grid-template-columns: 248px minmax(0, 1fr); }
  .sidebar {
    position: sticky; top: 0; height: 100vh; height: 100dvh; overflow-y: auto;
    display: flex; flex-direction: column; padding: 24px 16px;
  }
  .main { padding: 32px 40px 48px; max-width: 1240px; }
}

@media (max-width: 899.98px) {
  .sidebar {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: calc(8px + env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) 8px
      max(16px, env(safe-area-inset-left));
  }
  .sidebar__nav, .sidebar__foot .inline-form, .meter-link .meter { display: none; }
  .sidebar__foot { margin: 0; flex-direction: row; align-items: center; gap: 10px; }
  .meter-link { width: auto; min-height: 0; padding: 0; font-size: 0.8rem; }
  .main {
    padding: 16px max(16px, env(safe-area-inset-right))
      calc(var(--tabbar-h) + 24px + env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left));
  }
  .tabbar {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 20;
    display: flex; justify-content: space-around; align-items: stretch;
    background: var(--card); border-top: 1px solid var(--line);
    padding: 4px max(4px, env(safe-area-inset-right)) env(safe-area-inset-bottom)
      max(4px, env(safe-area-inset-left));
  }
}
@media (max-width: 400px) {
  .meter-link { display: none; }
}

/* ---- public pages ----------------------------------------------------- */

.public-head {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  max-width: 1100px; margin: 0 auto; padding: calc(14px + env(safe-area-inset-top)) 16px 14px;
}
.public-head a { display: inline-flex; align-items: center; min-height: 44px; }
.public-main { max-width: 1100px; margin: 0 auto; padding: 0 16px 32px; }
.public-foot {
  display: flex; flex-wrap: wrap; justify-content: center; gap: 8px 20px; font-size: 0.9rem;
  padding: 24px 16px calc(24px + env(safe-area-inset-bottom));
}
.public-foot a { display: inline-flex; align-items: center; min-height: 44px; }
.prose { max-width: 68ch; margin: 0 auto; }
.prose h2 { margin-top: 1.5rem; }

/* ---- page structure ----------------------------------------------------- */

.page-head {
  display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between;
  gap: 12px; margin-bottom: 20px;
}
.page-head__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); }
.card {
  min-width: 0; padding: 20px; background: var(--card); border-radius: var(--radius);
  box-shadow: var(--shadow); overflow-wrap: anywhere;
}
.card--wide { grid-column: 1 / -1; }
.card--narrow { max-width: 520px; margin: 32px auto; }
.card__title {
  display: flex; align-items: center; gap: 8px; margin: 0 0 12px;
  font: 700 1.1rem var(--font-head);
}
.card__foot { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.empty { text-align: center; padding: 32px 16px; color: var(--ink-muted); }
.empty .btn { margin-top: 12px; }
.error-box { text-align: center; }
.error-box .btn { margin-top: 12px; }

/* ---- banners, chips, buttons ------------------------------------------ */

.banner {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 8px 16px; margin: 0 0 16px; padding: 12px 16px; border-radius: var(--radius-sm);
  border-left: 6px solid var(--leaf); background: var(--info-bg); box-shadow: var(--shadow);
}
.banner p { margin: 0; flex: 1 1 240px; }
.banner form { margin: 0; }
.banner--info { border-left-color: var(--leaf); }
.banner--warn { background: var(--warn-bg); border-left-color: var(--coral); }

.chip {
  display: inline-flex; align-items: center; min-height: 28px; padding: 2px 10px;
  border: 1.5px solid transparent; border-radius: 999px; font-size: 0.8rem; font-weight: 700;
  white-space: nowrap;
}
.chip--free { border-color: currentColor; }
.chip--annual { background: var(--sun); color: #11402c; }
.chip--ok { background: var(--ok-bg); color: var(--ink); border-color: var(--leaf); }
.chip--warn { background: var(--warn-bg); color: var(--ink); border-color: var(--coral); }
.chip--muted { background: var(--paper); color: var(--ink-muted); border-color: var(--line); }

.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  min-height: 44px; padding: 10px 18px; border: 2px solid transparent; border-radius: 12px;
  background: var(--card); color: var(--ink); font-weight: 700; text-decoration: none;
  cursor: pointer; transition: transform 0.15s var(--ease-out);
}
.btn:active { transform: translateY(1px); }
.btn--primary { background: var(--primary); color: var(--on-primary); }
.btn--ghost { background: transparent; border-color: currentColor; color: inherit; }
.btn--small { padding: 6px 12px; font-size: 0.9rem; }
.btn--block { display: flex; width: 100%; }
.btn--danger { background: var(--danger); color: var(--on-danger); }
.copy {
  display: inline-flex; align-items: center; gap: 6px; min-height: 44px; min-width: 44px;
  padding: 6px 12px; border: 1.5px solid var(--line); border-radius: 10px;
  background: var(--card); color: var(--ink); font-weight: 700; cursor: pointer;
}
.code-copy {
  display: flex; align-items: center; gap: 8px; max-width: 100%;
  padding: 6px 6px 6px 12px; background: var(--paper); border: 1.5px solid var(--line);
  border-radius: 10px;
}
.code-copy code {
  flex: 1; min-width: 0; overflow-wrap: anywhere;
  font: 600 0.95rem ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.steps { counter-reset: step; list-style: none; margin: 0; padding: 0; display: grid; gap: 14px; }
.steps > li { counter-increment: step; position: relative; min-height: 32px; padding-left: 44px; }
.steps > li::before {
  content: counter(step); position: absolute; left: 0; top: 0; width: 32px; height: 32px;
  display: grid; place-items: center; border-radius: 50%;
  background: var(--ink); color: var(--paper); font: 800 0.95rem var(--font-head);
}

/* ---- forms ---------------------------------------------------------- */

.filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 8px 12px; margin-bottom: 16px; }
.filters label, .form-row label {
  display: flex; flex-direction: column; gap: 4px;
  font-size: 0.85rem; font-weight: 600; color: var(--ink-muted);
}
input[type="text"], input[type="search"], input[type="email"], input[type="number"], select, textarea {
  min-height: 44px; max-width: 100%; padding: 8px 12px; border: 1.5px solid var(--line);
  border-radius: 10px; background: var(--card); color: var(--ink);
}
textarea { width: 100%; min-height: 88px; resize: vertical; }
input[type="checkbox"], input[type="radio"] { width: 22px; height: 22px; accent-color: var(--leaf-strong); }
input[aria-invalid="true"], textarea[aria-invalid="true"] { border-color: var(--danger); }
.form-row { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
.form-error, .field-error { margin: 4px 0 0; color: var(--danger); font-size: 0.9rem; font-weight: 600; }
.inline-form { display: inline; margin: 0; }

/* ---- tables ----------------------------------------------------------- */

.table-wrap {
  overflow-x: auto; -webkit-overflow-scrolling: touch; background: var(--card);
  border-radius: var(--radius); box-shadow: var(--shadow);
}
.table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
.table th, .table td {
  padding: 10px 12px; text-align: left; vertical-align: top;
  border-bottom: 1px solid var(--line); overflow-wrap: anywhere;
}
.table th { font-size: 0.8rem; letter-spacing: 0.04em; text-transform: uppercase; color: var(--ink-muted); }
.table tr:last-child td { border-bottom: 0; }
@media (max-width: 599.98px) {
  .table--stack thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .table--stack tr { display: block; padding: 8px 0; border-bottom: 1px solid var(--line); }
  .table--stack td { display: flex; justify-content: space-between; gap: 12px; padding: 6px 12px; border: 0; }
  .table--stack td::before { content: attr(data-label); flex: none; font-weight: 600; color: var(--ink-muted); }
}
.row--saved td { position: relative; }
.row--saved td::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: var(--leaf); opacity: 0; animation: saved-flash 0.8s ease-out 1;
}

/* ---- Inicio: trail, streak, stamps, KPIs --------------------------- */

.trail { display: block; width: 100%; height: auto; overflow: visible; }
.trail__path {
  fill: none; stroke: var(--line); stroke-width: 3; stroke-linecap: round;
  stroke-dasharray: 6 8; animation: trail-march 0.9s linear 0.9s 2;
}
.trail__progress { fill: none; stroke: var(--leaf); stroke-width: 4; stroke-linecap: round; }
.trail__node { stroke-width: 3; transform-box: fill-box; transform-origin: center; }
.trail__node--done { fill: var(--leaf); stroke: var(--leaf); }
.trail__node--today { fill: var(--coral); stroke: var(--card); animation: today-pulse 1s ease-in-out 2; }
.trail__node--upcoming { fill: var(--card); stroke: var(--ink-muted); }
.trail__node--missed { fill: var(--paper); stroke: var(--ink-muted); stroke-dasharray: 3 3; }
.trail__node--rest { fill: var(--line); stroke: none; }
.trail__extra { fill: var(--sun); stroke: var(--ink); stroke-width: 1.5; }
.trail__label { font: 600 11px var(--font-body); fill: var(--ink-muted); text-anchor: middle; }

.streak {
  display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px;
  border-radius: 999px; background: var(--sun); color: #11402c;
  font: 800 1rem var(--font-head); animation: streak-bob 0.6s var(--ease-out) 1;
}
.stamps { display: flex; flex-wrap: wrap; gap: 8px; margin: 0; padding: 0; list-style: none; }
.stamp {
  display: inline-flex; align-items: center; gap: 6px; min-height: 36px; padding: 4px 12px;
  border: 2px dashed var(--ink-muted); border-radius: 999px; color: var(--ink);
  font-size: 0.9rem; font-weight: 600; transform-origin: center;
}
.stamp--used { background: var(--leaf-strong); border-style: solid; border-color: var(--leaf-strong); color: var(--on-primary); }
@media (prefers-color-scheme: dark) {
  .stamp--used { background: var(--primary); border-color: var(--primary); }
}

.kpi { display: flex; flex-direction: column; gap: 2px; }
.kpi__value { font: 800 2rem/1 var(--font-head); font-variant-numeric: tabular-nums; }
.kpi__label { font-size: 0.9rem; color: var(--ink-muted); }
.kpi__target { font-size: 0.8rem; color: var(--ink-muted); }

/* ---- charts, session detail, plan diff ----------------------------- */

.chart { display: block; width: 100%; height: auto; overflow: visible; }
.chart__line { fill: none; stroke: var(--leaf-strong); stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
.chart__target { stroke: var(--coral); stroke-width: 1.5; stroke-dasharray: 4 4; }
.chart__dot { fill: var(--card); stroke: var(--leaf-strong); stroke-width: 2; }
.as-table { margin-top: 8px; }
.as-table > summary {
  display: inline-flex; align-items: center; min-height: 44px; cursor: pointer;
  color: var(--leaf-strong); font-weight: 600;
}
.said { color: var(--ink-muted); text-decoration: line-through; text-decoration-thickness: 2px; }
.correct { display: inline-block; color: var(--leaf-strong); font-weight: 700; }
.diff { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.diff > li { padding: 8px 12px; border-left: 4px solid var(--line); border-radius: 8px; background: var(--paper); }
.diff > .diff--added { border-left-color: var(--leaf); }
.diff > .diff--removed { border-left-color: var(--danger); color: var(--ink-muted); text-decoration: line-through; }
.diff > .diff--moved { border-left-color: var(--sun); }

/* ---- loading, HTMX, celebration ---------------------------------- */

.skeleton {
  position: relative; overflow: hidden; min-height: 1em; border-radius: 8px;
  background: var(--line); color: transparent;
}
.skeleton::after {
  content: ""; position: absolute; inset: 0; transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.45), transparent);
  animation: shimmer 1.2s ease-in-out 4;
}
.htmx-indicator { opacity: 0; transition: opacity 0.2s ease 0.3s; }
.htmx-request .htmx-indicator, .htmx-request.htmx-indicator { opacity: 1; }
.htmx-added { opacity: 0; }
.card, .table-wrap, .banner, .table tr { transition: opacity 0.15s ease-out; }

.celebrate { position: fixed; inset: 0; z-index: 60; overflow: hidden; pointer-events: none; }
.celebrate svg { width: 100%; height: 100%; }
.celebrate__leaf { transform-box: fill-box; transform-origin: center; }
.celebrate__leaf--a { fill: var(--leaf); }
.celebrate__leaf--b { fill: var(--sun); }
.celebrate__leaf--c { fill: var(--coral); }

/* ---- page transitions (cross-document View Transitions) ----------- */

@view-transition { navigation: auto; }
.vt-today { view-transition-name: vt-today; }
.vt-last-session { view-transition-name: vt-last-session; }
::view-transition-group(*) { animation-duration: 0.3s; animation-timing-function: var(--ease-out); }

/* ---- keyframes: transform, opacity and stroke-dashoffset only ------ */

@keyframes trail-march { to { stroke-dashoffset: -28; } }
@keyframes today-pulse { 50% { transform: scale(1.25); } }
@keyframes streak-bob { 40% { transform: translateY(-4px); } }
@keyframes shimmer { to { transform: translateX(100%); } }
@keyframes saved-flash { from { opacity: 0.2; } to { opacity: 0; } }

/* ---- reduced motion: final state, no movement --------------------- */

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation: none !important;
    transition: none !important;
    scroll-behavior: auto !important;
  }
  ::view-transition-group(*), ::view-transition-old(*), ::view-transition-new(*) { animation: none !important; }
}
body[data-reduce-motion] *, body[data-reduce-motion] *::before, body[data-reduce-motion] *::after {
  animation: none !important;
  transition: none !important;
}
:root:has(body[data-reduce-motion])::view-transition-group(*),
:root:has(body[data-reduce-motion])::view-transition-old(*),
:root:has(body[data-reduce-motion])::view-transition-new(*) { animation: none !important; }
```

Notes on the ambient effects (spec section 10, WCAG 2.2.2): the trail march runs 2 cycles after a 0.9 s delay (ends at 2.7 s), the today pulse runs 2 × 1 s, the streak bobs once, and the skeleton shimmer runs 4 × 1.2 s; all stop by themselves within 5 s. `trail-march` ends at −28, exactly two dash periods (6 + 8 = 14), so the jump back to the natural offset is invisible.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/unit/web/test_web_look.py tests/unit/web/test_web_template_safety.py -q`
Expected: PASS (8 tests).

- [ ] **Step 7: Look at it**

Run: `uv run just dashboard-demo`, open `http://localhost:8780/auth/test-login`, sign in as Ana and Beto, and check `/app/account` (the only authenticated page so far) and `/login`, `/privacy` in Chrome DevTools at 390 × 844 and at 1280 × 800, in light and dark (`Rendering → Emulate CSS prefers-color-scheme`). Check: sidebar at 1280, top bar + bottom tab bar at 390, the "Más" menu opens upward, the tab bar clears the iPhone home indicator (`env(safe-area-inset-bottom)`, emulate an iPhone 14 device), keyboard Tab shows a visible focus ring everywhere, no horizontal scroll at 320 px width. Stop the server.
Expected: all checks hold. Fix the CSS (not the markup) if one does not.

- [ ] **Step 8: Run the full fast gate and commit**

Run: `uv run just check-fast`
Expected: PASS.

```bash
git add src/tutor/web/static tests/unit/web/test_web_look.py tests/unit/web/test_web_template_safety.py
git commit -m "feat(web): design tokens, fonts, icons and layout styles"
```

### Task 14: Installable shell — manifest, service worker, offline page and install prompt

**Files:**
- Create: `scripts/make_icons.py`
- Create: `src/tutor/web/static/icons/icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png`, `splash-750x1334.png`, `splash-1170x2532.png`, `splash-1179x2556.png`, `splash-1290x2796.png` (generated)
- Create: `src/tutor/web/routes/pwa.py`
- Create: `src/tutor/web/templates/pwa/sw.js`
- Create: `src/tutor/web/templates/pages/offline.html`
- Create: `src/tutor/web/templates/partials/install.html`
- Modify: `src/tutor/web/templates/base.html` (manifest, icons, iOS meta tags)
- Modify: `src/tutor/web/routes/__init__.py` (register `pwa.router`, `pwa.app_router`)
- Modify: `src/tutor/web/static/css/app.css` (append the install section)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Test: `tests/unit/web/test_web_pwa.py`

**Interfaces:**
- Consumes: `AssetManifest` (`files()`, `url()`, `version`; Task 10), `Views` (`envs`; Task 10), `render`, `is_htmx` (Task 10), `APP_ROUTER_DEPS`, `current_user`, `get_deps` (Task 11), `safe_next` (Task 10), `AccountService.dismiss_install_prompt` (Task 7), fixtures `client`, `login`, `demo`, `backend`, `app`, `csrf_of`.
- Produces:
  - Routes: `GET /manifest.webmanifest` (`application/manifest+json`), `GET /sw.js` (`text/javascript`, `Cache-Control: no-cache` from the security middleware), `GET /offline` (public, never personal), `POST /app/install/dismiss` (form `back`, CSRF; 303 to `safe_next(back)`, or `200` with an empty body for HTMX so `hx-swap="outerHTML"` removes the card — a 204 would not be swapped under the `responseHandling` config of Task 10).
  - `tutor.web.routes.pwa.precache_list(assets: AssetManifest) -> list[str]`: versioned URLs of `.css`, `.js`, `.woff2`, `.svg` files plus `icons/icon-192.png` and `icons/icon-512.png`, sorted, then `"/offline"`.
  - `partials/install.html`: include it with `{% include "partials/install.html" %}`; it needs `install_dismissed: bool` in the page context (Inicio: `deps.reader.account(user.id).install_prompt_dismissed`; Cuenta: `account.install_prompt_dismissed`) plus the base context (`csrf_token`, `path`). It renders nothing when dismissed. The card starts `hidden`; `app.js` (Task 15) reveals it only when installation is possible and the app is not already running standalone.
  - Extra classes styled in this task: `install-card`, `ios-step`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_pwa.py`:

```python
import re
import struct
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tutor.web
from tutor.domain.dashboard.types import Lang
from tutor.web.assets import AssetManifest
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend
from tutor.web.routes.pwa import precache_list

from .conftest import csrf_of

ICONS = Path(tutor.web.__file__).parent / "static" / "icons"


def test_manifest_fields(client: TestClient) -> None:
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = response.json()
    assert (
        manifest["display"],
        manifest["start_url"],
        manifest["scope"],
        manifest["theme_color"],
        manifest["background_color"],
        manifest["lang"],
        manifest["short_name"],
    ) == ("standalone", "/app/?source=pwa", "/", "#11402c", "#e3f4ea", "es-MX", "Tutor")
    purposes = {(icon["sizes"], icon["purpose"]) for icon in manifest["icons"]}
    assert purposes == {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("icon-192.png", (192, 192)),
        ("icon-512.png", (512, 512)),
        ("icon-maskable-512.png", (512, 512)),
        ("apple-touch-icon.png", (180, 180)),
        ("splash-750x1334.png", (750, 1334)),
        ("splash-1170x2532.png", (1170, 2532)),
        ("splash-1179x2556.png", (1179, 2556)),
        ("splash-1290x2796.png", (1290, 2796)),
    ],
)
def test_icons_are_pngs_of_the_right_size(name: str, size: tuple[int, int]) -> None:
    data = (ICONS / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == size


def test_service_worker_embeds_version_and_never_cache_list(
    client: TestClient, app: FastAPI
) -> None:
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"
    body = response.text
    assert f'"{app.state.assets.version}"' in body
    for prefix in ("/app/", "/api/", "/billing/", "/admin/", "/auth/", "/webhooks/"):
        assert f'"{prefix}"' in body
    assert '"/offline"' in body and app.state.assets.url("css/app.css") in body
    for needle in ("skipWaiting", "clients.claim", 'credentials: "omit"', "HX-Request"):
        assert needle in body


def test_precache_list_takes_shell_assets_only(tmp_path: Path) -> None:
    for rel in (
        "css/app.css",
        "js/app.js",
        "js/VENDORED.md",
        "fonts/baloo2-latin.woff2",
        "fonts/OFL-Baloo2.txt",
        "icons/sprite.svg",
        "icons/icon-192.png",
        "icons/apple-touch-icon.png",
        "icons/splash-750x1334.png",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode())
    urls = precache_list(AssetManifest(tmp_path))
    assert [u.split("?")[0] for u in urls] == [
        "/static/css/app.css",
        "/static/fonts/baloo2-latin.woff2",
        "/static/icons/icon-192.png",
        "/static/icons/sprite.svg",
        "/static/js/app.js",
        "/offline",
    ]
    assert all("?v=" in u for u in urls[:-1])


def test_offline_page_renders_without_login(client: TestClient) -> None:
    response = client.get("/offline")
    assert response.status_code == 200
    assert "Tu progreso está a salvo en el servidor" in response.text


def test_offline_page_never_contains_personal_data(
    login: Callable[[UUID], TestClient], demo: DemoUsers
) -> None:
    page = login(demo.ana).get("/offline").text
    assert "Ana" not in page
    assert 'name="csrf-token"' not in page and "X-CSRF-Token" not in page


def test_base_links_manifest_and_ios_assets(client: TestClient) -> None:
    page = client.get("/login").text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page
    assert 'rel="apple-touch-icon"' in page
    assert page.count('rel="apple-touch-startup-image"') == 4
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in page


def test_dismiss_install_requires_csrf_and_is_remembered(
    login: Callable[[UUID], TestClient], demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    assert c.post("/app/install/dismiss", data={"back": "/app/"}).status_code == 403
    response = c.post(
        "/app/install/dismiss", data={"csrf_token": csrf_of(c), "back": "/app/glossary"}
    )
    assert response.status_code == 303 and response.headers["location"] == "/app/glossary"
    assert demo.ana in backend.install_dismissed


def test_dismiss_install_over_htmx_returns_empty_fragment(
    login: Callable[[UUID], TestClient], demo: DemoUsers
) -> None:
    c = login(demo.ana)
    response = c.post(
        "/app/install/dismiss", data={"csrf_token": csrf_of(c)}, headers={"hx-request": "true"}
    )
    assert response.status_code == 200 and response.text == ""


def test_dismiss_install_redirect_ignores_foreign_back(
    login: Callable[[UUID], TestClient], demo: DemoUsers
) -> None:
    c = login(demo.ana)
    response = c.post(
        "/app/install/dismiss", data={"csrf_token": csrf_of(c), "back": "https://evil.example"}
    )
    assert response.headers["location"] == "/app/"


def _install_partial(app: FastAPI, dismissed: bool, lang: Lang = Lang.ES_MX) -> str:
    template = app.state.views.envs[lang].get_template("partials/install.html")
    return template.render(install_dismissed=dismissed, csrf_token="tok", path="/app/")


def test_install_partial_offers_both_paths_until_dismissed(app: FastAPI) -> None:
    html = _install_partial(app, dismissed=False)
    assert "data-install-card" in html and "data-install" in html and "data-ios-install" in html
    assert re.search(r"<section[^>]*data-install-card[^>]*hidden", html)
    assert "Agregar a pantalla de inicio" in html
    assert _install_partial(app, dismissed=True).strip() == ""
    assert "Add to Home Screen" in _install_partial(app, dismissed=False, lang=Lang.EN)


def test_install_styles_exist() -> None:
    css = (Path(tutor.web.__file__).parent / "static" / "css" / "app.css").read_text("utf-8")
    assert ".install-card" in css and ".ios-step" in css
```

Run: `uv run pytest tests/unit/web/test_web_pwa.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.web.routes.pwa'`.

- [ ] **Step 2: Generate the icons and splash screens**

`scripts/make_icons.py`:

```python
"""Render the PWA icons and iOS splash screens from the brand mark (a practice trail).

Run: uv run --with pillow python scripts/make_icons.py
Writes src/tutor/web/static/icons/*.png; the PNGs are committed. Re-run only when the mark
changes.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "tutor" / "web" / "static" / "icons"
INK = (17, 64, 44)
PAPER = (227, 244, 234)
LEAF = (47, 158, 102)
CORAL = (255, 111, 79)
SUPERSAMPLE = 4
TRAIL = ((0.10, 0.72), (0.28, 0.50), (0.44, 0.46), (0.58, 0.62), (0.74, 0.40), (0.90, 0.26))
SPLASHES = ((750, 1334), (1170, 2532), (1179, 2556), (1290, 2796))


def _mark(draw: ImageDraw.ImageDraw, x0: float, y0: float, size: float) -> None:
    points = [(x0 + px * size, y0 + py * size) for px, py in TRAIL]
    draw.line(points, fill=LEAF, width=max(2, round(size * 0.08)), joint="curve")
    dot = size * 0.045
    for x, y in points[:-1]:
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=PAPER)
    end_x, end_y = points[-1]
    big = size * 0.09
    draw.ellipse((end_x - big, end_y - big, end_x + big, end_y + big), fill=CORAL)


def icon_image(size: int, *, opaque: bool, rounded: bool, inset_ratio: float) -> Image.Image:
    """Draw at 4x and downsample, which gives smooth edges without a vector renderer."""
    s = size * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (*INK, 255) if opaque else (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if rounded:
        draw.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(s * 0.22), fill=INK)
    inset = s * inset_ratio
    _mark(draw, inset, inset, s - 2 * inset)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        icon = icon_image(size, opaque=False, rounded=True, inset_ratio=0.12)
        icon.save(OUT / f"icon-{size}.png", optimize=True)
    # Maskable: full bleed, mark inside the central 80% safe zone.
    maskable = icon_image(512, opaque=True, rounded=False, inset_ratio=0.2)
    maskable.save(OUT / "icon-maskable-512.png", optimize=True)
    # iOS rounds the corners itself and turns transparency black, so this one is opaque.
    apple = icon_image(180, opaque=True, rounded=False, inset_ratio=0.14).convert("RGB")
    apple.save(OUT / "apple-touch-icon.png", optimize=True)
    for width, height in SPLASHES:
        canvas = Image.new("RGB", (width, height), PAPER)
        tile = round(min(width, height) * 0.32)
        logo = icon_image(tile, opaque=False, rounded=True, inset_ratio=0.12)
        canvas.paste(logo, ((width - tile) // 2, (height - tile) // 2), logo)
        canvas.save(OUT / f"splash-{width}x{height}.png", optimize=True)


if __name__ == "__main__":
    main()
```

Run: `uv run --with pillow python scripts/make_icons.py` then `ls -l src/tutor/web/static/icons`
Expected: eight new PNG files. Open `icon-512.png` and `icon-maskable-512.png` in an image viewer: a deep-green tile with a green trail ending in a coral dot; on the maskable icon the mark stays clear of the edges.

- [ ] **Step 3: Write the service worker template**

`src/tutor/web/templates/pwa/sw.js`:

```js
// Service worker for the installable shell (spec 8.2). Generated per deploy by /sw.js.
// It caches only the static shell and the offline page. Pages, partials and data always
// come from the network and are never stored on the device.
const VERSION = {{ version|tojson }};
const CACHE = `tutor-shell-${VERSION}`;
const PRECACHE = {{ precache|tojson }};
const NEVER_CACHE = ["/app/", "/api/", "/billing/", "/admin/", "/auth/", "/webhooks/"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      // credentials: "omit" so the cached offline page is rendered for nobody in particular.
      .then((cache) => cache.addAll(PRECACHE.map((url) => new Request(url, { credentials: "omit" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("tutor-shell-") && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.headers.get("HX-Request") === "true") return;
  if (request.mode === "navigate") {
    // Network only; the cached offline page is the fallback when there is no network.
    event.respondWith(fetch(request).catch(() => caches.match("/offline")));
    return;
  }
  if (NEVER_CACHE.some((prefix) => url.pathname.startsWith(prefix))) return;
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
  }
});
```

The fetch handler never calls `cache.put`, so nothing fetched at runtime is stored; only the precache list is.

- [ ] **Step 4: Write the routes**

`src/tutor/web/routes/pwa.py`:

```python
"""Installable shell: web app manifest, service worker, offline page (spec section 8)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.types import Lang, User
from tutor.web.assets import AssetManifest
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.security import safe_next
from tutor.web.views import Views, is_htmx, render

router = APIRouter()
app_router = APIRouter(dependencies=APP_ROUTER_DEPS)

_PRECACHE_SUFFIXES = (".css", ".js", ".woff2", ".svg")
_PRECACHE_ICONS = frozenset({"icons/icon-192.png", "icons/icon-512.png"})


def precache_list(assets: AssetManifest) -> list[str]:
    files = sorted(
        f for f in assets.files() if f.endswith(_PRECACHE_SUFFIXES) or f in _PRECACHE_ICONS
    )
    return [assets.url(f) for f in files] + ["/offline"]


@router.get("/manifest.webmanifest")
async def manifest(request: Request) -> Response:
    assets: AssetManifest = request.app.state.assets
    body = {
        "id": "/app/",
        "name": "English Tutor",
        "short_name": "Tutor",
        "description": "Tu tutor de inglés para el trabajo: plan, progreso y glosario.",
        "lang": "es-MX",
        "dir": "ltr",
        "start_url": "/app/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "theme_color": "#11402c",
        "background_color": "#e3f4ea",
        "categories": ["education", "productivity"],
        "icons": [
            {
                "src": assets.url("icons/icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": assets.url("icons/icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": assets.url("icons/icon-maskable-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    return Response(json.dumps(body, ensure_ascii=False), media_type="application/manifest+json")


@router.get("/sw.js")
async def service_worker(request: Request) -> Response:
    assets: AssetManifest = request.app.state.assets
    views: Views = request.app.state.views
    script = (
        views.envs[Lang.ES_MX]
        .get_template("pwa/sw.js")
        .render(version=assets.version, precache=precache_list(assets))
    )
    return Response(script, media_type="text/javascript")


@router.get("/offline", response_class=HTMLResponse)
async def offline(request: Request) -> HTMLResponse:
    # No user dependency and an empty CSRF token: this page is cached on the device.
    return render(request, "pages/offline.html", {"csrf_token": ""})


@app_router.post("/app/install/dismiss")
async def dismiss_install(
    request: Request,
    user: User = Depends(current_user),
    back: str = Form("/app/"),
) -> Response:
    deps = get_deps(request)
    deps.account.dismiss_install_prompt(user.id, deps.clock())
    if is_htmx(request):
        return HTMLResponse("")  # 200 so htmx swaps the card away; a 204 is not swapped
    return RedirectResponse(safe_next(back), status_code=303)
```

In `src/tutor/web/routes/__init__.py`, add `pwa` to the import inside `all_routers` and append both routers, keeping every router already listed:

```python
def all_routers(config: WebConfig) -> list[APIRouter]:
    from tutor.web.routes import auth, public, pwa

    routers: list[APIRouter] = [
        public.router,
        *auth.routers(config),
        pwa.router,
        pwa.app_router,
    ]
    return routers
```

- [ ] **Step 5: Write the templates and link them from `base.html`**

`src/tutor/web/templates/pages/offline.html`:

```html
{% extends "layouts/public.html" %}
{% block title %}{{ _("Sin conexión") }} · English Tutor{% endblock %}
{% block content %}
<section class="card card--narrow empty">
  <h1>{{ _("Sin conexión") }}</h1>
  <p>{{ _("Sin conexión. Tu progreso está a salvo en el servidor.") }}</p>
  <a class="btn btn--primary" href="/app/">{{ _("Reintentar") }}</a>
</section>
{% endblock %}
```

`src/tutor/web/templates/partials/install.html`:

```html
{% if not install_dismissed %}
<section class="card install-card" data-install-card hidden aria-labelledby="install-title">
  <h2 class="card__title" id="install-title">
    <svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#install"></use></svg>{{ _("Instala English Tutor") }}
  </h2>
  <p>{{ _("Ábrelo desde tu pantalla de inicio, como una app. No necesitas tienda de apps.") }}</p>
  <button type="button" class="btn btn--primary" data-install hidden>
    <svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#install"></use></svg><span>{{ _("Instalar") }}</span>
  </button>
  <ol class="steps" data-ios-install hidden>
    <li>
      <svg class="ios-step" viewBox="0 0 48 48" aria-hidden="true"><rect x="4" y="30" width="40" height="14" rx="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M24 33v7M21 36l3-3 3 3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="24" cy="37" r="9" fill="none" stroke="#ff6f4f" stroke-width="2"/></svg>
      <span>{{ _("Toca Compartir en la barra de Safari.") }}</span>
    </li>
    <li>
      <svg class="ios-step" viewBox="0 0 48 48" aria-hidden="true"><rect x="4" y="10" width="40" height="28" rx="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M10 24h16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><rect x="30" y="18" width="10" height="12" rx="2" fill="none" stroke="#ff6f4f" stroke-width="2"/><path d="M35 21v6M32 24h6" stroke="#ff6f4f" stroke-width="2" stroke-linecap="round"/></svg>
      <span>{{ _("Elige «Agregar a pantalla de inicio».") }}</span>
    </li>
    <li>
      <svg class="ios-step" viewBox="0 0 48 48" aria-hidden="true"><rect x="6" y="6" width="14" height="14" rx="4" fill="none" stroke="currentColor" stroke-width="2"/><rect x="28" y="6" width="14" height="14" rx="4" fill="none" stroke="currentColor" stroke-width="2"/><rect x="6" y="28" width="14" height="14" rx="4" fill="none" stroke="currentColor" stroke-width="2"/><rect x="28" y="28" width="14" height="14" rx="4" fill="#11402c" stroke="#ff6f4f" stroke-width="2"/></svg>
      <span>{{ _("Toca «Agregar». English Tutor aparecerá junto a tus apps.") }}</span>
    </li>
  </ol>
  <form method="post" action="/app/install/dismiss" hx-post="/app/install/dismiss" hx-target="closest [data-install-card]" hx-swap="outerHTML" class="inline-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <input type="hidden" name="back" value="{{ path }}">
    <button class="btn btn--ghost btn--small">{{ _("Ahora no") }}</button>
  </form>
</section>
{% endif %}
```

The illustrations use SVG presentation attributes (allowed by the CSP); the "Agregar a pantalla de inicio" text is the label iOS shows in Spanish, and "Add to Home Screen" the English one.

In `src/tutor/web/templates/base.html`, insert directly after `<meta name="theme-color" content="#11402c">`:

```html
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" type="image/png" sizes="192x192" href="{{ asset('icons/icon-192.png') }}">
  <link rel="apple-touch-icon" href="{{ asset('icons/apple-touch-icon.png') }}">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Tutor">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <link rel="apple-touch-startup-image" media="screen and (device-width: 375px) and (device-height: 667px) and (-webkit-device-pixel-ratio: 2) and (orientation: portrait)" href="{{ asset('icons/splash-750x1334.png') }}">
  <link rel="apple-touch-startup-image" media="screen and (device-width: 390px) and (device-height: 844px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="{{ asset('icons/splash-1170x2532.png') }}">
  <link rel="apple-touch-startup-image" media="screen and (device-width: 393px) and (device-height: 852px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="{{ asset('icons/splash-1179x2556.png') }}">
  <link rel="apple-touch-startup-image" media="screen and (device-width: 430px) and (device-height: 932px) and (-webkit-device-pixel-ratio: 3) and (orientation: portrait)" href="{{ asset('icons/splash-1290x2796.png') }}">
```

Append to `src/tutor/web/static/css/app.css`, just before the `/* ---- keyframes` section:

```css
/* ---- install card (Task 14) ------------------------------------------ */

.install-card { display: flex; flex-direction: column; gap: 12px; }
.install-card .steps > li { display: flex; align-items: center; gap: 12px; }
.ios-step { width: 48px; height: 48px; flex: none; color: var(--ink); }
```

- [ ] **Step 6: Add the English translations**

Append to `src/tutor/web/locale/en/LC_MESSAGES/messages.po` ("Reintentar" is already in the catalog from Task 10; do not add it twice):

```po
msgid "Sin conexión"
msgstr "Offline"

msgid "Sin conexión. Tu progreso está a salvo en el servidor."
msgstr "You're offline. Your progress is safe on the server."

msgid "Instala English Tutor"
msgstr "Install English Tutor"

msgid "Ábrelo desde tu pantalla de inicio, como una app. No necesitas tienda de apps."
msgstr "Open it from your home screen, like an app. No app store needed."

msgid "Instalar"
msgstr "Install"

msgid "Toca Compartir en la barra de Safari."
msgstr "Tap Share in Safari's toolbar."

msgid "Elige «Agregar a pantalla de inicio»."
msgstr "Choose “Add to Home Screen”."

msgid "Toca «Agregar». English Tutor aparecerá junto a tus apps."
msgstr "Tap “Add”. English Tutor will appear next to your apps."

msgid "Ahora no"
msgstr "Not now"
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/unit/web -q`
Expected: PASS, including `test_every_template_string_has_an_english_translation`, `test_web_template_safety.py` and `test_sprite_has_every_icon_templates_use` (the install partial uses `#install`).

- [ ] **Step 8: Check installability in Chrome**

Run: `uv run just dashboard-demo`, open `http://localhost:8780/login` in Chrome (localhost counts as a secure context), then DevTools → Application → Manifest: no errors, icons shown, "Installability" has no warnings once Task 15 registers the service worker (until then it reports the missing service worker; that is expected in this task). DevTools → Application → Service workers stays empty until Task 15. Stop the server.
Expected: manifest parsed with all three icons.

- [ ] **Step 9: Commit**

```bash
git add scripts/make_icons.py src/tutor/web tests/unit/web/test_web_pwa.py
git commit -m "feat(web): installable shell with manifest, service worker and offline page"
```

### Task 15: Motion layer and vendored HTMX

**Files:**
- Create: `src/tutor/web/static/js/htmx.min.js` (vendored, HTMX 2.0.11)
- Create: `src/tutor/web/static/js/VENDORED.md`
- Modify: `src/tutor/web/static/js/app.js` (replace the Task 10 placeholder completely)
- Modify: `src/tutor/web/templates/base.html` (HTMX script tag; translated copy labels on `#live`)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Test: `tests/unit/web/test_web_js.py`

**Interfaces:**
- Consumes: the motion hooks and class names in "Conventions for page tasks"; `celebrate__leaf--a/b/c` and `.celebrate` styles (Task 13); `partials/install.html` markup (`data-install-card`, `data-install`, `data-ios-install`; Task 14); `/sw.js` (Task 14); `#live` region in `base.html` (Task 10).
- Produces, for page tasks:
  - Every hook in the conventions works after a full page load and after any HTMX swap (`htmx:afterSettle` re-scans the document; each element animates at most once, tracked with `data-motion-done`).
  - Copy buttons: `<button type="button" class="copy" data-copy="start my lesson"><svg class="icon" aria-hidden="true"><use href="…#copy"></use></svg><span>{{ _("Copiar") }}</span></button>`. The visible label is the button's last `<span>`, or the button's own text when it has no child elements; it reads "Copiado" for 1.5 s and the same word is announced in `#live`. The translated words come from `#live`'s `data-copied` and `data-copy-failed` attributes, so pages add nothing else.
  - Announcing a save from a route: return the header `HX-Trigger: {"announce": "<translated text>"}` built with `json.dumps({"announce": text}, ensure_ascii=True)` (HTTP headers must be ASCII); `app.js` writes the text into `#live`.
  - Error items for `data-strike`: `<li data-strike><span class="said">…</span> → <span class="correct">…</span></li>`. The strike-through itself is static CSS; the motion is "dijiste" settling in and "mejor" sliding in, because the Global Constraints allow only transform, opacity and stroke-dashoffset (the spec's `clip-path` wording yields to that rule).
  - The service worker is registered on every page from `app.js`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_js.py`:

```python
import gzip
import hashlib
import re
from pathlib import Path

from fastapi.testclient import TestClient

import tutor.web

JS = Path(tutor.web.__file__).parent / "static" / "js"


def test_total_javascript_is_within_budget() -> None:
    total = sum(len(gzip.compress(p.read_bytes(), 9)) for p in JS.glob("*.js"))
    assert total <= 50 * 1024


def test_vendored_files_match_their_recorded_hashes() -> None:
    rows = re.findall(
        r"^\|\s*(js/[\w.-]+)\s*\|[^|]*\|\s*([\d.]+)\s*\|[^|]*\|\s*([0-9a-f]{64})\s*\|",
        (JS / "VENDORED.md").read_text(encoding="utf-8"),
        flags=re.M,
    )
    assert ("js/htmx.min.js", "2.0.11") in {(f, v) for f, v, _ in rows}
    for rel, _version, digest in rows:
        data = (JS.parent / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, rel


def test_app_js_builds_no_code_or_markup_from_strings() -> None:
    source = (JS / "app.js").read_text(encoding="utf-8")
    for forbidden in (
        "eval(",
        "new Function",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
    ):
        assert forbidden not in source, forbidden


def test_app_js_respects_both_reduced_motion_signals() -> None:
    source = (JS / "app.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in source
    assert "reduceMotion" in source


def test_app_js_implements_every_hook() -> None:
    source = (JS / "app.js").read_text(encoding="utf-8")
    for hook in (
        "data-count-to",
        "data-draw",
        "data-pop",
        "data-strike",
        "data-grow",
        "data-celebrate",
        "data-copy",
        "data-install",
        "data-ios-install",
        "htmx:afterSettle",
        "serviceWorker",
        "beforeinstallprompt",
    ):
        assert hook in source, hook


def test_scripts_load_only_from_static_and_htmx_first(client: TestClient) -> None:
    page = client.get("/login").text
    tags = re.findall(r"<script\b([^>]*)>", page)
    sources = [re.search(r'src="([^"]+)"', attrs) for attrs in tags]
    assert all(m and m.group(1).startswith("/static/") for m in sources)
    assert page.index("js/htmx.min.js") < page.index("js/app.js")


def test_live_region_carries_translated_copy_labels(client: TestClient) -> None:
    assert 'data-copied="Copiado"' in client.get("/login").text
    assert 'data-copied="Copied"' in client.get("/login?lang=en").text
```

Run: `uv run pytest tests/unit/web/test_web_js.py -q`
Expected: FAIL (no `VENDORED.md`, `app.js` is the placeholder, no HTMX tag).

- [ ] **Step 2: Vendor HTMX 2.0.11 and verify it against a second source**

```bash
curl -fsSL -o src/tutor/web/static/js/htmx.min.js "https://cdn.jsdelivr.net/npm/htmx.org@2.0.11/dist/htmx.min.js"
uv run python - <<'EOF'
import base64, hashlib, httpx
from pathlib import Path
data = Path("src/tutor/web/static/js/htmx.min.js").read_bytes()
digest = hashlib.sha256(data)
print("bytes", len(data))
print("sha256", digest.hexdigest())
listing = httpx.get("https://data.jsdelivr.com/v1/package/npm/htmx.org@2.0.11/flat", timeout=30).json()
entry = next(f for f in listing["files"] if f["name"] == "/dist/htmx.min.js")
assert base64.b64decode(entry["hash"]) == digest.digest(), "jsDelivr integrity hash differs"
unpkg = httpx.get("https://unpkg.com/htmx.org@2.0.11/dist/htmx.min.js", follow_redirects=True, timeout=30).content
assert unpkg == data, "unpkg copy differs"
print("verified against jsDelivr metadata and unpkg")
EOF
```

Expected: about 52,182 bytes, the printed SHA-256, and `verified against jsDelivr metadata and unpkg`. If either assertion fails, stop: do not vendor a file two sources disagree on; report it.

Write `src/tutor/web/static/js/VENDORED.md`, replacing `SHA256_FROM_STEP_2` with the 64-character hex digest printed above (the test fails on anything else):

```markdown
# Vendored front-end files

Pinned, hash-checked copies. Update by re-running Task 15 Step 2 with the new version and
replacing the row; `tests/unit/web/test_web_js.py` checks the hash.

| File | Package | Version | Source URL | SHA-256 | License |
| --- | --- | --- | --- | --- | --- |
| js/htmx.min.js | htmx.org | 2.0.11 | https://cdn.jsdelivr.net/npm/htmx.org@2.0.11/dist/htmx.min.js | SHA256_FROM_STEP_2 | 0BSD |
```

This is the one value in the plan that only exists after the download; the test pins it.

- [ ] **Step 3: Write `app.js`**

Replace `src/tutor/web/static/js/app.js` with:

```js
// Motion layer and small behaviours for the dashboard (spec section 10, plan Task 15).
// The server renders every element in its final state; this file only adds movement on top,
// so reduced motion, an old browser or an error here leaves a complete page.
// Allowed properties: transform, opacity, stroke-dashoffset. Text is set with textContent only.

const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)";
const SPRING_CURVE =
  "linear(0, 0.009, 0.035 2.1%, 0.141, 0.281 6.7%, 0.723 12.9%, 0.938 16.7%, 1.017, 1.077, " +
  "1.121, 1.149 24.3%, 1.159, 1.163, 1.161, 1.154 29.9%, 1.129 32.8%, 1.051 39.6%, " +
  "1.017 43.1%, 0.991, 0.977 51%, 0.974 53.8%, 0.975 57.1%, 0.997 69.8%, 1.003 76.9%, 1)";
const SPRING = CSS.supports("animation-timing-function", "linear(0, 1)") ? SPRING_CURVE : EASE_OUT;
const SVG_NS = "http://www.w3.org/2000/svg";
const LEAF_PATH = "M0 -9 C 6 -4 6 4 0 9 C -6 4 -6 -4 0 -9 Z";

const reducedMotion = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
  document.body.dataset.reduceMotion !== undefined;

const liveRegion = () => document.getElementById("live");

function announce(text) {
  const region = liveRegion();
  if (!region || !text) return;
  region.textContent = "";
  window.setTimeout(() => {
    region.textContent = text;
  }, 50);
}

// Each element animates at most once, even if several HTMX swaps re-scan the page.
function claim(el) {
  if (el.hasAttribute("data-motion-done")) return false;
  el.setAttribute("data-motion-done", "");
  return true;
}

function whenVisible(el, run) {
  if (!("IntersectionObserver" in window)) {
    run();
    return;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect();
        run();
      }
    },
    { threshold: 0.3 },
  );
  observer.observe(el);
}

function countUp(el) {
  const target = Number.parseFloat(el.dataset.countTo);
  if (!Number.isFinite(target)) return;
  const decimals = Number.parseInt(el.dataset.countDecimals || "0", 10);
  const finalText = el.textContent;
  const format = new Intl.NumberFormat(document.documentElement.lang || "es-MX", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const start = performance.now();
  const step = (now) => {
    const t = Math.min((now - start) / 700, 1);
    const eased = 1 - (1 - t) ** 3;
    el.textContent = t < 1 ? format.format(target * eased) : finalText;
    if (t < 1) window.requestAnimationFrame(step);
  };
  window.requestAnimationFrame(step);
}

function draw(path) {
  const length = path.getTotalLength();
  const dash = `${length} ${length}`;
  path.animate(
    [
      { strokeDasharray: dash, strokeDashoffset: length },
      { strokeDasharray: dash, strokeDashoffset: 0 },
    ],
    { duration: 800, easing: EASE_OUT },
  );
  path
    .closest("svg")
    ?.querySelectorAll(".chart__target")
    .forEach((line) =>
      line.animate([{ opacity: 0 }, { opacity: 1 }], {
        duration: 400,
        delay: 400,
        easing: "ease-out",
        fill: "backwards",
      }),
    );
}

function pop(container) {
  container.querySelectorAll(".stamp--used").forEach((stamp, index) => {
    stamp.animate(
      [
        { transform: "scale(0.6)", opacity: 0 },
        { transform: "scale(1)", opacity: 1 },
      ],
      { duration: 500, delay: Math.min(index * 60, 400), easing: SPRING, fill: "backwards" },
    );
  });
}

function strike(item, index) {
  const offset = Math.min(index * 120, 600);
  item.querySelector(".said")?.animate([{ opacity: 0.4 }, { opacity: 1 }], {
    duration: 300,
    delay: offset,
    easing: "ease-out",
    fill: "backwards",
  });
  item.querySelector(".correct")?.animate(
    [
      { opacity: 0, transform: "translateX(-8px)" },
      { opacity: 1, transform: "none" },
    ],
    { duration: 500, delay: offset + 350, easing: EASE_OUT, fill: "backwards" },
  );
}

function grow(rect) {
  rect.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], {
    duration: 600,
    easing: EASE_OUT,
  });
}

function celebrate(el) {
  const box = el.getBoundingClientRect();
  const cx = box.left + box.width / 2;
  const cy = box.top + box.height / 2;
  const layer = document.createElement("div");
  layer.className = "celebrate";
  layer.setAttribute("aria-hidden", "true");
  const svg = document.createElementNS(SVG_NS, "svg");
  layer.append(svg);
  document.body.append(layer);
  const kinds = ["a", "b", "c"];
  for (let i = 0; i < 14; i += 1) {
    const leaf = document.createElementNS(SVG_NS, "path");
    leaf.setAttribute("d", LEAF_PATH);
    leaf.setAttribute("class", `celebrate__leaf celebrate__leaf--${kinds[i % 3]}`);
    svg.append(leaf);
    const angle = (i / 14) * Math.PI * 2 + Math.random() * 0.4;
    const distance = 70 + Math.random() * 70;
    const dx = Math.cos(angle) * distance;
    const dy = Math.sin(angle) * distance - 30;
    const turn = Math.round(Math.random() * 240 - 120);
    leaf.animate(
      [
        { transform: `translate(${cx}px, ${cy}px) scale(0.4) rotate(0deg)`, opacity: 1 },
        {
          transform: `translate(${cx + dx}px, ${cy + dy}px) scale(1) rotate(${turn}deg)`,
          opacity: 0,
        },
      ],
      { duration: 900 + Math.random() * 250, easing: EASE_OUT, fill: "forwards" },
    );
  }
  window.setTimeout(() => layer.remove(), 1200);
}

// ---- install prompt -----------------------------------------------------

let deferredPrompt = null;

const isStandalone = () =>
  window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;

const isIosSafari = () => {
  const ua = window.navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  return ios && /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
};

function showInstall(root) {
  if (isStandalone()) return;
  root.querySelectorAll("[data-install-card]").forEach((card) => {
    const button = card.querySelector("[data-install]");
    const guide = card.querySelector("[data-ios-install]");
    if (deferredPrompt && button) {
      button.hidden = false;
      card.hidden = false;
    } else if (guide && isIosSafari()) {
      guide.hidden = false;
      card.hidden = false;
    }
  });
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredPrompt = event;
  showInstall(document);
});

window.addEventListener("appinstalled", () => {
  deferredPrompt = null;
  document.querySelectorAll("[data-install-card]").forEach((card) => {
    card.hidden = true;
  });
});

// ---- delegated clicks: copy and install --------------------------------

document.addEventListener("click", async (event) => {
  const copyButton = event.target.closest("[data-copy]");
  if (copyButton) {
    const region = liveRegion();
    try {
      await navigator.clipboard.writeText(copyButton.dataset.copy);
      const copied = region?.dataset.copied || "Copiado";
      // The label is the last <span>, or the button itself when it holds only text.
      const label =
        copyButton.querySelector("span:last-of-type") ??
        (copyButton.children.length === 0 ? copyButton : null);
      if (label) {
        const original = label.textContent;
        label.textContent = copied;
        window.setTimeout(() => {
          label.textContent = original;
        }, 1500);
      }
      announce(copied);
    } catch {
      announce(region?.dataset.copyFailed);
    }
    return;
  }
  const installButton = event.target.closest("[data-install]");
  if (installButton && deferredPrompt) {
    const prompt = deferredPrompt;
    deferredPrompt = null;
    await prompt.prompt();
    await prompt.userChoice;
    installButton.hidden = true;
  }
});

// ---- wiring -------------------------------------------------------------

function init(root) {
  showInstall(root);
  if (reducedMotion()) return;
  root.querySelectorAll("[data-count-to]").forEach((el) => {
    if (claim(el)) whenVisible(el, () => countUp(el));
  });
  root.querySelectorAll("path[data-draw]").forEach((path) => {
    if (claim(path)) whenVisible(path, () => draw(path));
  });
  root.querySelectorAll("[data-pop]").forEach((container) => {
    if (claim(container)) whenVisible(container, () => pop(container));
  });
  root.querySelectorAll("[data-strike]").forEach((item, index) => {
    if (claim(item)) whenVisible(item, () => strike(item, index));
  });
  root.querySelectorAll("rect[data-grow]").forEach((rect) => {
    if (claim(rect)) whenVisible(rect, () => grow(rect));
  });
  root.querySelectorAll("[data-celebrate]").forEach((el) => {
    if (claim(el)) celebrate(el);
  });
}

document.addEventListener("htmx:afterSettle", () => init(document));
document.body.addEventListener("announce", (event) => announce(event.detail?.value));

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

init(document);
```

- [ ] **Step 4: Load HTMX and add the copy labels in `base.html`**

In `src/tutor/web/templates/base.html`, replace:

```html
  <script src="{{ asset('js/app.js') }}" type="module"></script>
```

with:

```html
  <script src="{{ asset('js/htmx.min.js') }}" defer></script>
  <script src="{{ asset('js/app.js') }}" type="module"></script>
```

Deferred classic scripts and module scripts run in document order after parsing, so HTMX is loaded before `app.js`.

Replace:

```html
  <div class="sr-only" aria-live="polite" id="live"></div>
```

with:

```html
  <div class="sr-only" aria-live="polite" id="live" data-copied="{{ _('Copiado') }}" data-copy-failed="{{ _('No se pudo copiar; selecciona el texto.') }}"></div>
```

Append to `src/tutor/web/locale/en/LC_MESSAGES/messages.po`:

```po
msgid "Copiado"
msgstr "Copied"

msgid "No se pudo copiar; selecciona el texto."
msgstr "Couldn't copy; select the text instead."
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/unit/web -q`
Expected: PASS. `test_total_javascript_is_within_budget` reports about 17 KB of HTMX plus about 3 KB of `app.js`.

- [ ] **Step 6: Smoke-test in a browser**

Run: `uv run just dashboard-demo`, open `http://localhost:8780/login` in Chrome, then DevTools:
- Console: no errors and no CSP violation reports.
- Application → Service workers: `/sw.js` activated; Application → Cache storage: one `tutor-shell-<version>` cache holding only `/static/…` files and `/offline`.
- Network → Offline, then reload `/app/account`: the offline page appears. Back online, reload: the real page.
- Application → Manifest → Installability: no warnings.
Stop the server.
Expected: all four checks hold.

- [ ] **Step 7: Commit**

```bash
git add src/tutor/web tests/unit/web/test_web_js.py
git commit -m "feat(web): motion layer, copy and install behaviour, vendored htmx 2.0.11"
```

**Handed to Task 30 (Playwright), because these are browser behaviours that unit tests cannot see:**
1. With `reduced_motion="reduce"`, and separately with a user whose `reduce_motion` preference is on, `document.getAnimations().length == 0` one second after load on Inicio and on a session detail page.
2. Count-up: after 1.5 s every `[data-count-to]` element's text equals the server-rendered text.
3. `data-draw`, `data-pop`, `data-strike`, `data-grow`: animations start only when scrolled into view and every element ends with no running animation and its natural styles.
4. Celebration: on the first Inicio visit after a new closed session one `.celebrate` overlay appears and is removed within 1.5 s; a reload shows none.
5. Copy: with clipboard permissions granted, clicking a `[data-copy]` button puts the text on the clipboard, shows "Copiado" in the button for about 1.5 s and writes it into `#live`.
6. Install: without `beforeinstallprompt` the install card stays hidden in Chromium; with an iPhone Safari user agent and a non-standalone display mode the iOS guide is shown; dispatching a synthetic `beforeinstallprompt` reveals the install button.
7. Service worker: registers; with `context.set_offline(True)` a navigation shows the offline page; Cache storage holds no `/app/` URL after visiting every page.
8. HTMX swaps re-run hooks for new content (glossary filter, inline save) and a response with `HX-Trigger: {"announce": …}` writes its text into `#live`.
9. No `securitypolicyviolation` event fires on any page (listen before navigation).
