# Dashboard Web Layer Implementation Plan — Part 2 of 2 (Tasks 16–30)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

This is Part 2 (Tasks 16–30: payments, pages, verification). It requires Part 1 (`2026-10-04-dashboard-web-1-foundation.md`, Tasks 1–15) to be merged first; task numbers continue from Part 1 and references to Tasks 1–15 point there. The header is shared with Part 1.

**Execution: subagent-driven** (chosen by the author on 2026-10-04).

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

## Before Task 1 (on 2027-01-05; for this part: before Task 16, re-check the Stripe rows)

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

## Part D — Payments

Card only (Ruling 2). Stripe Checkout (hosted) and the Customer Portal; webhooks are the only writer of subscription state. The pure state machine is `next_subscription` (Task 1); the in-memory `SubscriptionStore.apply_event` (Task 7) already wraps it. This part adds the real Stripe adapter, the webhook route and the pages.

### Task 16: Stripe gateway adapter

**Files:**
- Create: `src/tutor/web/stripe_gateway.py`
- Test: `tests/unit/web/test_web_stripe_gateway.py`

**Interfaces:**
- Consumes: `BillingEvent`, `BillingEventKind` (Task 1); `BillingGateway`, `InvalidSignature` (Task 7).
- Produces: `StripeGateway(api_key: str, webhook_secret: str, client: Any | None = None)` implementing `BillingGateway` (`checkout_url`, `portal_url`, `parse_event`); module function `event_from_payload(raw: dict[str, Any]) -> BillingEvent | None` (maps a verified Stripe event dict; used by `parse_event` and unit-tested on its own).

Mapping rules (API version 2025-03-31.basil or later):
- `checkout.session.completed`: only `mode == "subscription"`; `customer` and `subscription` may be ids or expanded objects; `client_reference_id` is the learner's UUID (anything else → `user_id=None`); `paid = payment_status == "paid"`; `period_end=None` (the session carries no period; `invoice.paid` sets it).
- `invoice.paid` / `invoice.payment_failed`: subscription id from `parent.subscription_details.subscription` when `parent.type == "subscription_details"`; invoices without a subscription are ignored (a one-off invoice must never grant Annual); `period_end` = the latest `lines.data[].period.end` (the invoice-level `period_end` describes the previous period and is not used); `paid` is true only for `invoice.paid`.
- `customer.subscription.deleted`: subscription id is the object `id`; `period_end` = `ended_at` (the lapse date that starts the 30-day "volvió a Free" banner), falling back to the latest `items.data[].current_period_end`.
- Any other type, or an event without a customer → `None` (the webhook answers 200 and does nothing).

- [ ] **Step 1: Re-check the Stripe API with Context7**

Query Context7 (`/stripe/stripe-python`, `/websites/stripe`) for the installed stripe-python version and confirm, before writing code:
1. `StripeClient(api_key).v1.checkout.sessions.create(params=...)` and `.v1.billing_portal.sessions.create(params=...)` are the current paths (the 2026-10-04 research inferred the `v1` path from the service layout; if the installed version uses `client.checkout.sessions.create`, change the two calls and the fake client in the test together).
2. `client.construct_event(payload, sig_header, secret)` exists on `StripeClient` and raises `stripe.SignatureVerificationError` (if it is only exported as `stripe.error.SignatureVerificationError`, import it from there).
3. The basil invoice shape (`parent.subscription_details.subscription`, line `period.end`) is unchanged.
4. Whether `payment_method_types` is still recommended for Checkout subscriptions. The plan does **not** pass it: card-only is enforced by enabling only cards for subscriptions in the Stripe Dashboard (OXXO is not offered for subscriptions anyway). Record the answer in the ledger.

- [ ] **Step 2: Write the failing tests**

`tests/unit/web/test_web_stripe_gateway.py`:

```python
import hashlib
import hmac
import json
import time
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from tutor.domain.dashboard.types import BillingEventKind
from tutor.web.ports import InvalidSignature
from tutor.web.stripe_gateway import StripeGateway, event_from_payload

SECRET = "whsec_test_secret"
USER = UUID("6f1c2a52-8a43-4d0b-9a5e-2f0d7c1b9e11")
T = 1_800_000_000  # 2027-01-15 08:00:00 UTC
YEAR = 31_536_000


def checkout_event(**obj: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "cs_test_1",
        "object": "checkout.session",
        "mode": "subscription",
        "customer": "cus_1",
        "subscription": "sub_1",
        "client_reference_id": str(USER),
        "payment_status": "paid",
        "status": "complete",
    }
    data.update(obj)
    return {
        "id": "evt_checkout",
        "object": "event",
        "type": "checkout.session.completed",
        "created": T,
        "data": {"object": data},
    }


def invoice_event(kind: str, **obj: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "in_1",
        "object": "invoice",
        "customer": "cus_1",
        "status": "paid" if kind == "invoice.paid" else "open",
        "parent": {
            "type": "subscription_details",
            "subscription_details": {"subscription": "sub_1"},
        },
        "lines": {"data": [{"period": {"start": T, "end": T + YEAR}}]},
        "period_end": T,
    }
    data.update(obj)
    return {
        "id": f"evt_{kind}",
        "object": "event",
        "type": kind,
        "created": T,
        "data": {"object": data},
    }


def deleted_event(**obj: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "sub_1",
        "object": "subscription",
        "customer": "cus_1",
        "status": "canceled",
        "ended_at": T,
        "items": {"data": [{"current_period_end": T + YEAR}]},
    }
    data.update(obj)
    return {
        "id": "evt_deleted",
        "object": "event",
        "type": "customer.subscription.deleted",
        "created": T,
        "data": {"object": data},
    }


# --- mapping ---------------------------------------------------------------


def test_paid_checkout_maps_user_customer_and_subscription() -> None:
    event = event_from_payload(checkout_event())
    assert event is not None
    assert event.kind is BillingEventKind.CHECKOUT_COMPLETED
    assert (event.event_id, event.customer_id, event.subscription_ref) == (
        "evt_checkout",
        "cus_1",
        "sub_1",
    )
    assert event.user_id == USER and event.paid and event.period_end is None
    assert event.created == datetime(2027, 1, 15, 8, 0, tzinfo=UTC)


def test_unpaid_checkout_is_not_paid() -> None:
    event = event_from_payload(checkout_event(payment_status="unpaid"))
    assert event is not None and not event.paid


def test_checkout_with_foreign_reference_has_no_user() -> None:
    event = event_from_payload(checkout_event(client_reference_id="not-a-uuid"))
    assert event is not None and event.user_id is None


def test_one_time_checkout_is_ignored() -> None:
    assert event_from_payload(checkout_event(mode="payment")) is None


def test_expanded_customer_and_subscription_objects() -> None:
    event = event_from_payload(
        checkout_event(customer={"id": "cus_9"}, subscription={"id": "sub_9"})
    )
    assert event is not None and (event.customer_id, event.subscription_ref) == ("cus_9", "sub_9")


def test_invoice_paid_uses_line_period_end() -> None:
    event = event_from_payload(invoice_event("invoice.paid"))
    assert event is not None
    assert event.kind is BillingEventKind.INVOICE_PAID
    assert event.subscription_ref == "sub_1" and event.paid
    assert event.period_end == date(2028, 1, 15)


def test_invoice_payment_failed_is_not_paid() -> None:
    event = event_from_payload(invoice_event("invoice.payment_failed"))
    assert event is not None
    assert event.kind is BillingEventKind.PAYMENT_FAILED and not event.paid


def test_invoice_without_subscription_parent_is_ignored() -> None:
    assert event_from_payload(invoice_event("invoice.paid", parent=None)) is None
    one_off = {"type": "quote_details", "quote_details": {"quote": "qt_1"}}
    assert event_from_payload(invoice_event("invoice.paid", parent=one_off)) is None


def test_deleted_uses_ended_at_then_item_period() -> None:
    event = event_from_payload(deleted_event())
    assert event is not None
    assert event.kind is BillingEventKind.SUBSCRIPTION_DELETED
    assert (event.subscription_ref, event.period_end) == ("sub_1", date(2027, 1, 15))
    fallback = event_from_payload(deleted_event(ended_at=None))
    assert fallback is not None and fallback.period_end == date(2028, 1, 15)


def test_unknown_type_and_missing_customer_are_ignored() -> None:
    other = checkout_event()
    other["type"] = "customer.created"
    assert event_from_payload(other) is None
    assert event_from_payload(invoice_event("invoice.paid", customer=None)) is None


# --- signatures (real stripe library, offline) ------------------------------


def sign(payload: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    stamp = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={mac}"


def gateway() -> StripeGateway:
    return StripeGateway("sk_test_dummy", SECRET)


def test_valid_signature_is_parsed() -> None:
    body = json.dumps(invoice_event("invoice.paid")).encode()
    event = gateway().parse_event(body, sign(body))
    assert event is not None and event.kind is BillingEventKind.INVOICE_PAID


def test_ignored_type_with_valid_signature_returns_none() -> None:
    raw = checkout_event()
    raw["type"] = "customer.created"
    body = json.dumps(raw).encode()
    assert gateway().parse_event(body, sign(body)) is None


@pytest.mark.parametrize(
    "case",
    ["tampered", "wrong_secret", "garbage", "stale", "empty"],
)
def test_bad_signatures_raise(case: str) -> None:
    body = json.dumps(invoice_event("invoice.paid")).encode()
    signature = {
        "tampered": sign(body),
        "wrong_secret": sign(body, "whsec_other"),
        "garbage": "nonsense",
        "stale": sign(body, ts=int(time.time()) - 3600),
        "empty": "",
    }[case]
    payload = body.replace(b"cus_1", b"cus_2") if case == "tampered" else body
    with pytest.raises(InvalidSignature):
        gateway().parse_event(payload, signature)


# --- checkout and portal (fake client) ---------------------------------------


class Recorder:
    def __init__(self, url: str) -> None:
        self.url = url
        self.calls: list[dict[str, Any]] = []

    def create(
        self, params: dict[str, Any], options: dict[str, Any] | None = None
    ) -> SimpleNamespace:
        self.calls.append(params)
        return SimpleNamespace(url=self.url)


def fake_client() -> tuple[SimpleNamespace, Recorder, Recorder]:
    checkout = Recorder("https://checkout.stripe.com/c/pay/cs_test_1")
    portal = Recorder("https://billing.stripe.com/p/session/test_1")
    client = SimpleNamespace(
        v1=SimpleNamespace(
            checkout=SimpleNamespace(sessions=checkout),
            billing_portal=SimpleNamespace(sessions=portal),
        )
    )
    return client, checkout, portal


def test_checkout_for_a_new_customer() -> None:
    client, checkout, _ = fake_client()
    url = StripeGateway("sk_test_dummy", SECRET, client).checkout_url(
        user_id=USER,
        customer_id=None,
        price_id="price_29",
        success_url="https://t.example/billing/return?session_id={CHECKOUT_SESSION_ID}",
        cancel_url="https://t.example/billing/cancelled",
    )
    assert url == "https://checkout.stripe.com/c/pay/cs_test_1"
    assert checkout.calls == [
        {
            "mode": "subscription",
            "line_items": [{"price": "price_29", "quantity": 1}],
            "client_reference_id": str(USER),
            "success_url": "https://t.example/billing/return?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": "https://t.example/billing/cancelled",
        }
    ]


def test_checkout_reuses_known_customer() -> None:
    client, checkout, _ = fake_client()
    StripeGateway("sk_test_dummy", SECRET, client).checkout_url(
        user_id=USER, customer_id="cus_1", price_id="p", success_url="s", cancel_url="c"
    )
    assert checkout.calls[0]["customer"] == "cus_1"


def test_portal_session() -> None:
    client, _, portal = fake_client()
    url = StripeGateway("sk_test_dummy", SECRET, client).portal_url(
        customer_id="cus_1", return_url="https://t.example/app/account"
    )
    assert url == "https://billing.stripe.com/p/session/test_1"
    assert portal.calls == [{"customer": "cus_1", "return_url": "https://t.example/app/account"}]
```

Run: `uv run pytest tests/unit/web/test_web_stripe_gateway.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.web.stripe_gateway'`.

- [ ] **Step 3: Implement the adapter**

`src/tutor/web/stripe_gateway.py`:

```python
"""Stripe adapter for the BillingGateway port (spec section 7). Card only (Ruling 2).

The verified payload is parsed with `json.loads` into plain dicts, so the mapping is
independent of stripe-python's object classes and is unit-tested with fixture dicts.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import stripe

from tutor.domain.dashboard.types import BillingEvent, BillingEventKind
from tutor.web.ports import InvalidSignature

_KINDS = {kind.value: kind for kind in BillingEventKind}


class StripeGateway:
    def __init__(self, api_key: str, webhook_secret: str, client: Any | None = None) -> None:
        self._client: Any = client if client is not None else stripe.StripeClient(api_key)
        self._secret = webhook_secret

    def checkout_url(
        self,
        *,
        user_id: UUID,
        customer_id: str | None,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": str(user_id),
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if customer_id:
            params["customer"] = customer_id
        session = self._client.v1.checkout.sessions.create(params=params)
        return str(session.url)

    def portal_url(self, *, customer_id: str, return_url: str) -> str:
        session = self._client.v1.billing_portal.sessions.create(
            params={"customer": customer_id, "return_url": return_url}
        )
        return str(session.url)

    def parse_event(self, payload: bytes, signature: str) -> BillingEvent | None:
        try:
            self._client.construct_event(payload, signature, self._secret)
        except (stripe.SignatureVerificationError, ValueError) as exc:
            raise InvalidSignature("stripe signature") from exc
        raw: dict[str, Any] = json.loads(payload)
        return event_from_payload(raw)


def _id(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) and value else None


def _day(timestamp: Any) -> date | None:
    if isinstance(timestamp, int) and timestamp > 0:
        return datetime.fromtimestamp(timestamp, UTC).date()
    return None


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def _invoice_subscription(invoice: dict[str, Any]) -> str | None:
    parent = invoice.get("parent")
    if not isinstance(parent, dict) or parent.get("type") != "subscription_details":
        return None
    details = parent.get("subscription_details")
    return _id(details.get("subscription")) if isinstance(details, dict) else None


def _latest(timestamps: list[Any]) -> date | None:
    days = [d for d in (_day(t) for t in timestamps) if d is not None]
    return max(days) if days else None


def _invoice_period_end(invoice: dict[str, Any]) -> date | None:
    lines = (invoice.get("lines") or {}).get("data") or []
    return _latest([(line.get("period") or {}).get("end") for line in lines])


def event_from_payload(raw: dict[str, Any]) -> BillingEvent | None:
    kind = _KINDS.get(str(raw.get("type")))
    if kind is None:
        return None
    obj: dict[str, Any] = raw["data"]["object"]
    customer = _id(obj.get("customer"))
    if customer is None:
        return None
    created = datetime.fromtimestamp(int(raw["created"]), UTC)
    event_id = str(raw["id"])
    match kind:
        case BillingEventKind.CHECKOUT_COMPLETED:
            if obj.get("mode") != "subscription":
                return None
            return BillingEvent(
                event_id=event_id,
                kind=kind,
                created=created,
                customer_id=customer,
                subscription_ref=_id(obj.get("subscription")),
                user_id=_uuid(obj.get("client_reference_id")),
                paid=obj.get("payment_status") == "paid",
                period_end=None,
            )
        case BillingEventKind.INVOICE_PAID | BillingEventKind.PAYMENT_FAILED:
            subscription = _invoice_subscription(obj)
            if subscription is None:
                return None
            return BillingEvent(
                event_id=event_id,
                kind=kind,
                created=created,
                customer_id=customer,
                subscription_ref=subscription,
                user_id=None,
                paid=kind is BillingEventKind.INVOICE_PAID,
                period_end=_invoice_period_end(obj),
            )
        case BillingEventKind.SUBSCRIPTION_DELETED:
            items = (obj.get("items") or {}).get("data") or []
            ended = _day(obj.get("ended_at")) or _latest(
                [item.get("current_period_end") for item in items]
            )
            return BillingEvent(
                event_id=event_id,
                kind=kind,
                created=created,
                customer_id=customer,
                subscription_ref=_id(obj.get("id")),
                user_id=None,
                paid=False,
                period_end=ended,
            )
```

- [ ] **Step 4: Run the tests, lint and types**

Run: `uv run pytest tests/unit/web/test_web_stripe_gateway.py -q` then `uv run just lint`
Expected: PASS (all mapping, signature and client tests); no lint or mypy errors. If `construct_event` rejects the `stale` case only above a different tolerance, keep the test at 3600 s (well past the 300 s default).

- [ ] **Step 5: Commit**

```bash
git add src/tutor/web/stripe_gateway.py tests/unit/web/test_web_stripe_gateway.py
git commit -m "feat(web): Stripe gateway adapter (checkout, portal, verified webhook mapping)"
```

### Task 17: Stripe webhook route

**Files:**
- Create: `src/tutor/web/routes/billing.py`
- Modify: `src/tutor/web/routes/__init__.py` (append the billing routers)
- Test: `tests/unit/web/test_web_webhook.py`

**Interfaces:**
- Consumes: `BillingGateway.parse_event`, `InvalidSignature`, `SubscriptionStore` (`link_customer`, `user_for_customer`, `apply_event`), `UserDirectory.find_user` (Task 7); `get_deps` (Task 11).
- Produces: `POST /webhooks/stripe` on `webhook_router` (no `APP_ROUTER_DEPS`: this route and the test-only `POST /auth/test-login` are the only CSRF exemptions, which Task 29's sweep lists by name); `MAX_WEBHOOK_BYTES = 64 * 1024`; `billing.routers() -> list[APIRouter]` (Task 18 extends it); logger name `tutor.web.billing`.

Responses: 413 when the body exceeds 64 KB; 400 for an invalid signature (Stripe retries; a real attacker gets nothing); 200 for everything else, including duplicates, ignored types and unknown customers. An unknown customer is logged with a hashed id only, never an email, token or secret; answering 200 stops Stripe retrying an event that will never match.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_webhook.py`:

```python
import json
import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import SubStatus, Tier
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

from .conftest import NOW


def body(**kw: object) -> bytes:
    raw: dict[str, object] = {
        "event_id": "evt_1",
        "kind": "checkout.session.completed",
        "created": NOW.isoformat(),
        "customer_id": "cus_new",
        "subscription_ref": "sub_new",
        "user_id": None,
        "paid": True,
        "period_end": None,
    }
    raw.update(kw)
    return json.dumps(raw).encode()


def post(client: TestClient, payload: bytes, signature: str = "valid") -> int:
    response = client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": signature, "content-type": "application/json"},
    )
    return response.status_code


def test_paid_checkout_activates_annual_and_links_customer(
    client: TestClient, backend: MemoryBackend, demo: DemoUsers
) -> None:
    assert post(client, body(user_id=str(demo.ana))) == 200  # no CSRF token needed
    sub = backend.subscription(demo.ana)
    assert (sub.tier, sub.status) == (Tier.ANNUAL, SubStatus.ACTIVE)
    assert backend.customer_id(demo.ana) == "cus_new"


def test_duplicate_delivery_is_200_and_changes_nothing(
    client: TestClient, backend: MemoryBackend, demo: DemoUsers
) -> None:
    payload = body(user_id=str(demo.ana))
    assert post(client, payload) == 200
    first = backend.subscription(demo.ana)
    assert post(client, payload) == 200
    assert backend.subscription(demo.ana) == first
    assert backend.seen_events == {"evt_1"}


def test_out_of_order_delivery_converges(
    client: TestClient, backend: MemoryBackend, demo: DemoUsers
) -> None:
    deleted = body(
        event_id="evt_del",
        kind="customer.subscription.deleted",
        customer_id="cus_demo_beto",
        subscription_ref="sub_demo",
        created=NOW.isoformat(),
        paid=False,
        period_end=NOW.date().isoformat(),
    )
    stale_paid = body(
        event_id="evt_old_paid",
        kind="invoice.paid",
        customer_id="cus_demo_beto",
        subscription_ref="sub_demo",
        created=(NOW - timedelta(hours=1)).isoformat(),
        period_end=(NOW.date() + timedelta(days=365)).isoformat(),
    )
    assert post(client, deleted) == 200
    assert post(client, stale_paid) == 200
    sub = backend.subscription(demo.beto)
    assert (sub.tier, sub.status) == (Tier.FREE, SubStatus.LAPSED)


def test_unknown_customer_is_logged_hashed_and_acknowledged(
    client: TestClient,
    backend: MemoryBackend,
    demo: DemoUsers,
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = dict(backend.subs)
    with caplog.at_level(logging.WARNING, logger="tutor.web.billing"):
        status = post(client, body(event_id="evt_g", kind="invoice.paid", customer_id="cus_ghost"))
    assert status == 200
    assert backend.subs == before
    assert "unknown customer" in caplog.text
    assert "cus_ghost" not in caplog.text


def test_checkout_for_a_missing_user_falls_back_to_customer_lookup(
    client: TestClient, backend: MemoryBackend, demo: DemoUsers
) -> None:
    payload = body(user_id="00000000-0000-0000-0000-000000000000", customer_id="cus_x")
    assert post(client, payload) == 200
    assert backend.user_for_customer("cus_x") is None


def test_ignored_event_type_is_200(client: TestClient) -> None:
    assert post(client, body(kind="customer.created")) == 200


def test_bad_signature_is_400(client: TestClient, backend: MemoryBackend, demo: DemoUsers) -> None:
    assert post(client, body(user_id=str(demo.ana)), signature="forged") == 400
    assert backend.subscription(demo.ana).tier is Tier.FREE


def test_oversized_payload_is_413(client: TestClient) -> None:
    assert post(client, b"x" * (64 * 1024 + 1)) == 413
```

Run: `uv run pytest tests/unit/web/test_web_webhook.py -q`
Expected: FAIL (404 on `/webhooks/stripe`).

- [ ] **Step 2: Implement the route**

`src/tutor/web/routes/billing.py`:

```python
"""Payments: the Stripe webhook (this task) and the billing pages (Task 18)."""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response

from tutor.web.deps import get_deps
from tutor.web.ports import InvalidSignature

MAX_WEBHOOK_BYTES = 64 * 1024
log = logging.getLogger("tutor.web.billing")

webhook_router = APIRouter()


def _hashed(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


@webhook_router.post("/webhooks/stripe")
async def stripe_webhook(request: Request) -> Response:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
        return Response(status_code=413)
    payload = await request.body()
    if len(payload) > MAX_WEBHOOK_BYTES:
        return Response(status_code=413)
    deps = get_deps(request)
    try:
        event = deps.billing.parse_event(payload, request.headers.get("stripe-signature", ""))
    except InvalidSignature:
        return Response(status_code=400)
    if event is None:
        return Response(status_code=200)
    user_id = event.user_id
    if user_id is not None and deps.users.find_user(user_id) is not None:
        deps.subscriptions.link_customer(user_id, event.customer_id)
    else:
        user_id = deps.subscriptions.user_for_customer(event.customer_id)
    if user_id is None:
        log.warning(
            "stripe event for unknown customer event=%s kind=%s customer=%s",
            event.event_id,
            event.kind.value,
            _hashed(event.customer_id),
        )
        return Response(status_code=200)
    applied = deps.subscriptions.apply_event(user_id, event)
    log.info(
        "stripe event applied=%s event=%s kind=%s user=%s",
        applied,
        event.event_id,
        event.kind.value,
        _hashed(str(user_id)),
    )
    return Response(status_code=200)


def routers() -> list[APIRouter]:
    return [webhook_router]
```

In `src/tutor/web/routes/__init__.py`, import `billing` next to the other route modules and append its routers:

```python
    from tutor.web.routes import auth, billing, public

    routers: list[APIRouter] = [public.router, *auth.routers(config), *billing.routers()]
```

(If later tasks already added other modules to this function, keep them; only add `billing` to the import and `*billing.routers()` to the list.)

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/unit/web/test_web_webhook.py -q` then `uv run just check-fast`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/tutor/web/routes tests/unit/web/test_web_webhook.py
git commit -m "feat(web): idempotent Stripe webhook route"
```

### Task 18: Pricing, checkout, portal and return pages

**Files:**
- Modify: `src/tutor/web/routes/billing.py` (pages)
- Create: `src/tutor/web/templates/pages/pricing.html`
- Create: `src/tutor/web/templates/pages/checkout.html`
- Create: `src/tutor/web/templates/pages/billing_return.html`
- Create: `src/tutor/web/templates/pages/billing_cancelled.html`
- Create: `src/tutor/web/templates/partials/billing_status.html`
- Create: `src/tutor/web/templates/partials/refund.html`
- Modify: `src/tutor/web/static/css/app.css` (append the pricing styles)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Test: `tests/unit/web/test_web_billing_pages.py`

**Interfaces:**
- Consumes: `render`, `public_url`, base context `subscription`, `config`, `csrf_token` (Task 10); `current_user`, `optional_user`, `APP_ROUTER_DEPS`, `get_deps`, `get_config` (Task 11); `BillingGateway`, `SubscriptionStore` (Task 7); `webhook_router`, `routers()` (Task 17); motion hooks `data-celebrate`, `data-copy` (Task 15).
- Produces: routes `GET /pricing` (public), `GET /billing/checkout`, `POST /billing/checkout`, `POST /billing/portal`, `GET /billing/return`, `GET /billing/status?n=<0..15>`, `GET /billing/cancelled`; constant `MAX_STATUS_TRIES = 15`; partials `partials/billing_status.html` (context `active: bool`, `celebrate: bool`, `gave_up: bool`, `n: int`) and `partials/refund.html` (uses `config.support_email`; Cuenta in Task 26 includes it too); page classes `pricing`, `pricing__plans`, `pricing__plan`, `pricing__plan--annual`, `price`, `feature-list`, `feature-list--plain`.

Behaviour (spec section 7):
- Public pricing shows Free and Annual features from requirements section 3 **without** the Annual price (spec Q1 proposal: the cohort price appears after login), what the product does not do, card only, taxes at checkout, no CFDI, 14-day refund. Logged-out "Elegir Annual" goes to `/login?next=/billing/checkout`.
- `GET /billing/checkout` is the pre-pay summary with the learner's cohort price (`Subscription.price_cents`). An Annual learner sees "Ya tienes Annual" with the renewal date and "Gestionar pago" instead.
- `POST /billing/checkout` redirects (303) to Stripe Checkout with success URL `{base_url}/billing/return?session_id={CHECKOUT_SESSION_ID}` and cancel URL `{base_url}/billing/cancelled`; an Annual learner is sent back to the summary and no session is created.
- `POST /billing/portal` redirects (303) to the Customer Portal (return URL `{base_url}/app/account`); without a Stripe customer it goes to `/billing/checkout`.
- The return page never trusts `session_id`; it reads the subscription the webhook wrote. While not active, the status partial polls every 2 s up to 15 times (30 s), then shows "Seguimos confirmando" without `hx-trigger`, which stops the polling. The poll response that first sees Annual active carries `data-celebrate`; a full-page load of `/billing/return` never does (no celebration on reload, spec section 10).
- No dark patterns: no countdowns, no pre-checked boxes, a plain "Ahora no" next to "Pagar".

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_billing_pages.py`:

```python
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang, SubStatus, Tier
from tutor.web.demo import DemoUsers
from tutor.web.memory import FakeBilling, MemoryBackend

from .conftest import csrf_of

Login = Callable[[UUID], TestClient]


def activate(backend: MemoryBackend, user_id: UUID) -> None:
    backend.subs[user_id] = replace(
        backend.subscription(user_id), tier=Tier.ANNUAL, status=SubStatus.ACTIVE
    )


# --- public pricing ---------------------------------------------------------


def test_public_pricing_states_limits_and_hides_price(client: TestClient) -> None:
    page = client.get("/pricing")
    assert page.status_code == 200
    for text in (
        "3 sesiones por semana",
        "Hasta 50 elementos en tu glosario",
        "Reporte semanal cada lunes",
        "No emitimos CFDI por ahora.",
        "14 días",
        "tarjeta",
        "certificados de nivel",
    ):
        assert text in page.text
    assert "29.00" not in page.text
    assert 'href="/login?next=%2Fbilling%2Fcheckout&amp;lang=es"' in page.text


def test_public_pricing_in_english(client: TestClient) -> None:
    page = client.get("/pricing?lang=en")
    assert "No CFDI invoices for now." in page.text and "Choose Annual" in page.text


def test_logged_in_pricing_links_to_checkout(login: Login, demo: DemoUsers) -> None:
    page = login(demo.ana).get("/pricing")
    assert 'href="/billing/checkout"' in page.text


# --- pre-pay summary and checkout -------------------------------------------


def test_summary_shows_cohort_price_and_refund(login: Login, demo: DemoUsers) -> None:
    page = login(demo.ana).get("/billing/checkout")
    assert page.status_code == 200
    assert "29.00" in page.text and "Pagar" in page.text and "Ahora no" in page.text
    assert 'data-copy="soporte@example.test"' in page.text
    assert 'action="/billing/checkout"' in page.text


def test_first_day_learner_can_open_summary(login: Login, demo: DemoUsers) -> None:
    page = login(demo.nuevo).get("/billing/checkout")
    assert page.status_code == 200 and "Pagar" in page.text


def test_summary_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    page = login(demo.ana).get("/billing/checkout")
    assert "Pay " in page.text and "per year" in page.text


def test_annual_learner_sees_manage_instead_of_pay(login: Login, demo: DemoUsers) -> None:
    page = login(demo.beto).get("/billing/checkout")
    assert "Ya tienes Annual" in page.text and "Pagar" not in page.text


def test_start_checkout_redirects_to_stripe(
    login: Login, demo: DemoUsers, billing: FakeBilling
) -> None:
    c = login(demo.ana)
    response = c.post("/billing/checkout", data={"csrf_token": csrf_of(c)})
    assert response.status_code == 303
    assert response.headers["location"] == f"https://checkout.stripe.test/c/{demo.ana}"
    assert billing.checkouts == [
        {
            "user_id": demo.ana,
            "customer_id": None,
            "price_id": "price_29",
            "success_url": "https://testserver/billing/return?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": "https://testserver/billing/cancelled",
        }
    ]


def test_checkout_reuses_known_customer(
    login: Login, demo: DemoUsers, backend: MemoryBackend, billing: FakeBilling
) -> None:
    backend.link_customer(demo.ana, "cus_old")
    c = login(demo.ana)
    c.post("/billing/checkout", data={"csrf_token": csrf_of(c)})
    assert billing.checkouts[0]["customer_id"] == "cus_old"


def test_annual_learner_cannot_start_a_second_checkout(
    login: Login, demo: DemoUsers, billing: FakeBilling
) -> None:
    c = login(demo.beto)
    response = c.post("/billing/checkout", data={"csrf_token": csrf_of(c)})
    assert response.headers["location"] == "/billing/checkout"
    assert billing.checkouts == []


def test_checkout_needs_csrf_and_login(client: TestClient, login: Login, demo: DemoUsers) -> None:
    assert login(demo.ana).post("/billing/checkout").status_code == 403
    assert client.get("/billing/checkout").status_code == 303


# --- portal -----------------------------------------------------------------


def test_portal_needs_a_customer(login: Login, demo: DemoUsers) -> None:
    c = login(demo.ana)
    response = c.post("/billing/portal", data={"csrf_token": csrf_of(c)})
    assert response.headers["location"] == "/billing/checkout"


def test_portal_redirects_annual_learner(login: Login, demo: DemoUsers) -> None:
    c = login(demo.beto)
    response = c.post("/billing/portal", data={"csrf_token": csrf_of(c)})
    assert response.status_code == 303
    assert response.headers["location"] == "https://billing.stripe.test/p/cus_demo_beto"


# --- return page and status polling -------------------------------------------


def test_return_page_polls_while_pending(login: Login, demo: DemoUsers) -> None:
    page = login(demo.ana).get("/billing/return?session_id=cs_test_ignored")
    assert "Confirmando tu pago…" in page.text
    assert 'hx-get="/billing/status?n=1"' in page.text and 'hx-trigger="every 2s"' in page.text
    assert "cs_test_ignored" not in page.text


def test_status_increments_counter(login: Login, demo: DemoUsers) -> None:
    partial = login(demo.ana).get("/billing/status?n=3", headers={"hx-request": "true"})
    assert 'hx-get="/billing/status?n=4"' in partial.text and "<html" not in partial.text


def test_status_gives_up_after_fifteen_tries(login: Login, demo: DemoUsers) -> None:
    partial = login(demo.ana).get("/billing/status?n=15", headers={"hx-request": "true"})
    assert "Seguimos confirmando" in partial.text and "hx-trigger" not in partial.text


def test_status_rejects_out_of_range_counter(login: Login, demo: DemoUsers) -> None:
    assert login(demo.ana).get("/billing/status?n=16").status_code == 422
    assert login(demo.ana).get("/billing/status?n=-1").status_code == 422


def test_first_active_poll_celebrates(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    activate(backend, demo.ana)
    partial = c.get("/billing/status?n=2", headers={"hx-request": "true"})
    assert "Tu plan Annual está activo" in partial.text
    assert "data-celebrate" in partial.text and "hx-trigger" not in partial.text


def test_reloading_return_page_when_active_does_not_celebrate(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    activate(backend, demo.ana)
    page = login(demo.ana).get("/billing/return")
    assert "Tu plan Annual está activo" in page.text and "data-celebrate" not in page.text


def test_cancelled_page(login: Login, demo: DemoUsers) -> None:
    page = login(demo.ana).get("/billing/cancelled")
    assert page.status_code == 200 and "No se hizo ningún cargo" in page.text
```

Run: `uv run pytest tests/unit/web/test_web_billing_pages.py -q`
Expected: FAIL (404 on `/pricing` and `/billing/*`).

- [ ] **Step 2: Implement the routes**

In `src/tutor/web/routes/billing.py`, extend the imports:

```python
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.types import SubStatus, Tier, User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_config, get_deps, optional_user
from tutor.web.ports import InvalidSignature, WebDeps
from tutor.web.views import render
```

Add after `webhook_router` (and before `routers()`):

```python
MAX_STATUS_TRIES = 15

public_router = APIRouter()
router = APIRouter(dependencies=APP_ROUTER_DEPS)


def _annual_active(deps: WebDeps, user: User) -> bool:
    sub = deps.subscriptions.subscription(user.id)
    return sub.tier is Tier.ANNUAL and sub.status is SubStatus.ACTIVE


@public_router.get("/pricing", response_class=HTMLResponse, dependencies=[Depends(optional_user)])
async def pricing(request: Request) -> HTMLResponse:
    return render(request, "pages/pricing.html")


@router.get("/billing/checkout", response_class=HTMLResponse)
async def checkout_summary(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    return render(request, "pages/checkout.html")


@router.post("/billing/checkout")
async def start_checkout(request: Request, user: User = Depends(current_user)) -> Response:
    deps, config = get_deps(request), get_config(request)
    sub = deps.subscriptions.subscription(user.id)
    if sub.tier is Tier.ANNUAL:
        return RedirectResponse("/billing/checkout", status_code=303)
    url = deps.billing.checkout_url(
        user_id=user.id,
        customer_id=deps.subscriptions.customer_id(user.id),
        price_id=sub.price_id,
        success_url=f"{config.base_url}/billing/return?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{config.base_url}/billing/cancelled",
    )
    return RedirectResponse(url, status_code=303)


@router.post("/billing/portal")
async def open_portal(request: Request, user: User = Depends(current_user)) -> Response:
    deps, config = get_deps(request), get_config(request)
    customer = deps.subscriptions.customer_id(user.id)
    if customer is None:
        return RedirectResponse("/billing/checkout", status_code=303)
    url = deps.billing.portal_url(customer_id=customer, return_url=f"{config.base_url}/app/account")
    return RedirectResponse(url, status_code=303)


@router.get("/billing/return", response_class=HTMLResponse)
async def checkout_return(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    active = _annual_active(get_deps(request), user)
    ctx = {"active": active, "celebrate": False, "gave_up": False, "n": 0}
    return render(request, "pages/billing_return.html", ctx)


@router.get("/billing/status", response_class=HTMLResponse)
async def checkout_status(
    request: Request,
    user: User = Depends(current_user),
    n: int = Query(0, ge=0, le=MAX_STATUS_TRIES),
) -> HTMLResponse:
    active = _annual_active(get_deps(request), user)
    ctx = {
        "active": active,
        "celebrate": active,  # only polling reaches this route, so this is the first sighting
        "gave_up": not active and n >= MAX_STATUS_TRIES,
        "n": n,
    }
    return render(request, "partials/billing_status.html", ctx)


@router.get("/billing/cancelled", response_class=HTMLResponse)
async def checkout_cancelled(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    return render(request, "pages/billing_cancelled.html")
```

Replace `routers()` with:

```python
def routers() -> list[APIRouter]:
    return [webhook_router, public_router, router]
```

- [ ] **Step 3: Write the templates**

`src/tutor/web/templates/pages/pricing.html`:

```html
{% extends "layouts/public.html" %}
{% block title %}{{ _("Precios") }} · English Tutor{% endblock %}
{% block content %}
<section class="pricing">
  <h1>{{ _("Precios") }}</h1>
  <div class="pricing__plans">
    <article class="card pricing__plan">
      <h2>{{ _("Free") }}</h2>
      <p class="price">{{ _("Gratis") }}</p>
      <ul class="feature-list">
        <li>{{ _("1 plan activo") }}</li>
        <li>{{ _("3 sesiones por semana") }}</li>
        <li>{{ _("Hasta 50 elementos en tu glosario") }}</li>
        <li>{{ _("Sin reporte semanal") }}</li>
      </ul>
    </article>
    <article class="card pricing__plan pricing__plan--annual">
      <h2>{{ _("Annual") }}</h2>
      <p class="price">{{ _("Pago anual; ves tu precio al iniciar sesión") }}</p>
      <ul class="feature-list">
        <li>{{ _("Sesiones ilimitadas") }}</li>
        <li>{{ _("Glosario ilimitado") }}</li>
        <li>{{ _("Reporte semanal cada lunes") }}</li>
        <li>{{ _("Hasta 3 dominios: tecnología, día a día y negocios") }}</li>
      </ul>
      <a class="btn btn--primary btn--block" href="{{ '/billing/checkout' if user else public_url('/login?next=%2Fbilling%2Fcheckout') }}">{{ _("Elegir Annual") }}</a>
    </article>
  </div>
  <section class="card card--wide prose">
    <h2>{{ _("Lo que no hace") }}</h2>
    <p>{{ _("No evalúa pronunciación ni audio y no emite certificados de nivel.") }}</p>
    <h2>{{ _("Pagos") }}</h2>
    <ul class="feature-list">
      <li>{{ _("Pago con tarjeta a través de Stripe.") }}</li>
      <li>{{ _("Los impuestos se calculan al pagar.") }}</li>
      <li>{{ _("No emitimos CFDI por ahora.") }}</li>
      <li>{{ _("Reembolso completo si lo pides dentro de los primeros 14 días.") }}</li>
    </ul>
  </section>
</section>
{% endblock %}
```

`src/tutor/web/templates/pages/checkout.html`:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "account" %}
{% block title %}{{ _("Plan Annual") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head"><h1>{{ _("Plan Annual") }}</h1></header>
{% if subscription.tier == "annual" %}
<section class="card card--narrow">
  <h2>{{ _("Ya tienes Annual") }}</h2>
  {% if subscription.period_end %}
  <p>{% trans when=subscription.period_end|day("long") %}Tu plan se renueva el {{ when }}.{% endtrans %}</p>
  {% endif %}
  <form method="post" action="/billing/portal">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="btn btn--ghost">{{ _("Gestionar pago") }}</button>
  </form>
</section>
{% else %}
<section class="card card--narrow">
  <p class="price">{% trans price=subscription.price_cents|money %}{{ price }} al año{% endtrans %}</p>
  <h2>{{ _("Lo que incluye") }}</h2>
  <ul class="feature-list">
    <li>{{ _("Sesiones ilimitadas") }}</li>
    <li>{{ _("Glosario ilimitado") }}</li>
    <li>{{ _("Reporte semanal cada lunes") }}</li>
    <li>{{ _("Hasta 3 dominios: tecnología, día a día y negocios") }}</li>
  </ul>
  <ul class="feature-list feature-list--plain">
    <li>{{ _("Pago con tarjeta a través de Stripe.") }}</li>
    <li>{{ _("Los impuestos se calculan al pagar.") }}</li>
    <li>{{ _("No emitimos CFDI por ahora.") }}</li>
  </ul>
  {% include "partials/refund.html" %}
  <form method="post" action="/billing/checkout">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="btn btn--primary btn--block">{% trans price=subscription.price_cents|money %}Pagar {{ price }}{% endtrans %}</button>
  </form>
  <a class="btn btn--ghost btn--block" href="/app/">{{ _("Ahora no") }}</a>
</section>
{% endif %}
{% endblock %}
```

`src/tutor/web/templates/partials/refund.html`:

```html
<div>
  <p>{{ _("Si Annual no te sirve, pide el reembolso completo dentro de los primeros 14 días escribiendo a:") }}</p>
  <p class="code-copy">
    <span>{{ config.support_email }}</span>
    <button type="button" class="btn btn--ghost btn--small copy" data-copy="{{ config.support_email }}">{{ _("Copiar correo") }}</button>
  </p>
</div>
```

`src/tutor/web/templates/pages/billing_return.html`:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "account" %}
{% block title %}{{ _("Confirmando tu pago") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head"><h1>{{ _("Tu pago") }}</h1></header>
{% include "partials/billing_status.html" %}
{% endblock %}
```

`src/tutor/web/templates/partials/billing_status.html`:

```html
<section id="billing-status" class="card card--narrow"{% if not active and not gave_up %} hx-get="/billing/status?n={{ n + 1 }}" hx-trigger="every 2s" hx-swap="outerHTML"{% endif %}>
  {% if active %}
    {% if celebrate %}<div class="celebrate" data-celebrate aria-hidden="true"></div>{% endif %}
    <h2>{{ _("Tu plan Annual está activo") }}</h2>
    <p>{{ _("Ya tienes sesiones y glosario ilimitados. Tu próxima sesión en el chat ya lo sabe.") }}</p>
    <a class="btn btn--primary" href="/app/">{{ _("Ir a Inicio") }}</a>
  {% elif gave_up %}
    <h2>{{ _("Seguimos confirmando") }}</h2>
    <p>{{ _("Puedes cerrar esta página; tu plan se activará solo en cuanto Stripe nos confirme el pago.") }}</p>
    <a class="btn btn--ghost" href="/app/">{{ _("Ir a Inicio") }}</a>
  {% else %}
    <p role="status">{{ _("Confirmando tu pago…") }}</p>
    <div class="skeleton" aria-hidden="true"></div>
  {% endif %}
</section>
```

`src/tutor/web/templates/pages/billing_cancelled.html`:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "account" %}
{% block title %}{{ _("Pago cancelado") }} · English Tutor{% endblock %}
{% block content %}
<section class="card card--narrow">
  <h1>{{ _("No se hizo ningún cargo") }}</h1>
  <p>{{ _("Cancelaste el pago. Sigues en Free con todo lo que ya tienes.") }}</p>
  <a class="btn btn--ghost" href="/billing/checkout">{{ _("Ver Annual") }}</a>
  <a class="btn btn--ghost" href="/app/">{{ _("Ir a Inicio") }}</a>
</section>
{% endblock %}
```

- [ ] **Step 4: Add the English translations**

Append to `src/tutor/web/locale/en/LC_MESSAGES/messages.po` (strings already translated by Task 10 — "Precios", "Free", "Annual", "Lo que no hace", "Gestionar pago", "Ver Annual", "Ir a Inicio" — are reused and must not be added again):

```po
msgid "Gratis"
msgstr "Free of charge"

msgid "1 plan activo"
msgstr "1 active plan"

msgid "3 sesiones por semana"
msgstr "3 sessions per week"

msgid "Hasta 50 elementos en tu glosario"
msgstr "Up to 50 items in your glossary"

msgid "Sin reporte semanal"
msgstr "No weekly report"

msgid "Pago anual; ves tu precio al iniciar sesión"
msgstr "Paid yearly; you see your price after signing in"

msgid "Sesiones ilimitadas"
msgstr "Unlimited sessions"

msgid "Glosario ilimitado"
msgstr "Unlimited glossary"

msgid "Reporte semanal cada lunes"
msgstr "Weekly report every Monday"

msgid "Hasta 3 dominios: tecnología, día a día y negocios"
msgstr "Up to 3 domains: tech, everyday life and business"

msgid "Elegir Annual"
msgstr "Choose Annual"

msgid "No evalúa pronunciación ni audio y no emite certificados de nivel."
msgstr "It does not score pronunciation or audio and issues no level certificates."

msgid "Pagos"
msgstr "Payments"

msgid "Pago con tarjeta a través de Stripe."
msgstr "Card payment through Stripe."

msgid "Los impuestos se calculan al pagar."
msgstr "Taxes are calculated at checkout."

msgid "No emitimos CFDI por ahora."
msgstr "No CFDI invoices for now."

msgid "Reembolso completo si lo pides dentro de los primeros 14 días."
msgstr "Full refund if you ask within the first 14 days."

msgid "Plan Annual"
msgstr "Annual plan"

msgid "Ya tienes Annual"
msgstr "You already have Annual"

msgid "Tu plan se renueva el %(when)s."
msgstr "Your plan renews on %(when)s."

msgid "%(price)s al año"
msgstr "%(price)s per year"

msgid "Lo que incluye"
msgstr "What's included"

msgid "Pagar %(price)s"
msgstr "Pay %(price)s"

msgid "Si Annual no te sirve, pide el reembolso completo dentro de los primeros 14 días escribiendo a:"
msgstr "If Annual doesn't work for you, ask for a full refund within the first 14 days by writing to:"

msgid "Copiar correo"
msgstr "Copy email"

msgid "Confirmando tu pago"
msgstr "Confirming your payment"

msgid "Tu pago"
msgstr "Your payment"

msgid "Tu plan Annual está activo"
msgstr "Your Annual plan is active"

msgid "Ya tienes sesiones y glosario ilimitados. Tu próxima sesión en el chat ya lo sabe."
msgstr "You now have unlimited sessions and glossary. Your next chat session already knows."

msgid "Seguimos confirmando"
msgstr "Still confirming"

msgid "Puedes cerrar esta página; tu plan se activará solo en cuanto Stripe nos confirme el pago."
msgstr "You can close this page; your plan activates by itself as soon as Stripe confirms the payment."

msgid "Confirmando tu pago…"
msgstr "Confirming your payment…"

msgid "Pago cancelado"
msgstr "Payment cancelled"

msgid "No se hizo ningún cargo"
msgstr "You were not charged"

msgid "Cancelaste el pago. Sigues en Free con todo lo que ya tienes."
msgstr "You cancelled the payment. You're still on Free with everything you already have."
```

- [ ] **Step 5: Append the pricing styles**

Append to `src/tutor/web/static/css/app.css` (uses the spec 4 tokens `--ink`, `--leaf`, `--card` that Task 13 defines; if Task 13 named them differently, use its names):

```css
/* Pricing and checkout (Task 18) */
.pricing__plans { display: grid; gap: 1rem; margin-block: 1rem; }
@media (min-width: 900px) { .pricing__plans { grid-template-columns: 1fr 1fr; } }
.pricing__plan--annual { border: 2px solid var(--leaf); }
.price { font-family: "Baloo 2", system-ui, sans-serif; font-size: 1.5rem; font-weight: 700; color: var(--ink); margin: .25rem 0 1rem; }
.feature-list { padding-left: 1.25rem; margin: 0 0 1rem; }
.feature-list li { margin: .35rem 0; }
.feature-list--plain { list-style: none; padding-left: 0; }
```

- [ ] **Step 6: Run the tests and the translation check**

Run: `uv run pytest tests/unit/web/test_web_billing_pages.py tests/unit/web/test_web_i18n.py -q` then `uv run just check-fast`
Expected: PASS. If `test_every_template_string_has_an_english_translation` lists a msgid, copy it exactly as printed into the catalog.

- [ ] **Step 7: Commit**

```bash
git add src/tutor/web tests/unit/web/test_web_billing_pages.py
git commit -m "feat(web): pricing, pre-pay summary, checkout, portal and return pages"
```

### Task 19: Real-device check and the dashboard ADR (manual)

Spec 8.4 and Ruling 4: before the pages are built, prove on real phones that Google login and the Stripe Checkout return both complete **inside the installed app**. This task needs Tasks 12 (login), 14 (installable PWA) and 18 (checkout) done. It adds one small wiring module so the check runs with real Google and Stripe test mode over the in-memory demo data (the Postgres adapters do not exist yet); everything else is manual and recorded in an ADR.

**Files:**
- Create: `src/tutor/web/device_check_server.py`
- Create: `tests/unit/web/test_web_device_check.py`
- Create: `docs/adr/NNNN-dashboard-stack-and-pwa.md` (NNNN = next free number: run `ls docs/adr` and add one to the highest)

**Interfaces:**
- Consumes: `create_app` (Task 10), `WebConfig.from_env` (Task 7), `GoogleOidcLogin` (Task 12), `StripeGateway` (Task 16), `MemoryBackend`, `memory_deps` (Task 7), `seed_demo` (Task 8).
- Produces: `build(environ: Mapping[str, str]) -> FastAPI` and `build_from_env() -> FastAPI` in `tutor.web.device_check_server` (refuses anything but Stripe test-mode keys).

- [ ] **Step 1: Write the failing test**

`tests/unit/web/test_web_device_check.py`:

```python
import pytest

from tutor.web.device_check_server import build

ENV = {
    "TUTOR_ENV": "dev",
    "TUTOR_BASE_URL": "https://dash-test.example.com",
    "TUTOR_MCP_URL": "https://dash-test.example.com/mcp",
    "TUTOR_SUPPORT_EMAIL": "soporte@example.com",
    "STRIPE_SECRET_KEY": "sk_test_dummy",
    "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
    "STRIPE_PRICE_ID": "price_test_annual",
    "STRIPE_PRICE_CENTS": "2900",
    "GOOGLE_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "client-secret",
}


def test_builds_with_test_mode_keys() -> None:
    app = build(ENV)
    assert app.state.config.base_url == "https://dash-test.example.com"
    assert not app.state.config.test_login


@pytest.mark.parametrize("key", ["sk_live_dummy", "", "rk_test_dummy"])
def test_refuses_anything_but_stripe_test_keys(key: str) -> None:
    with pytest.raises(RuntimeError, match="test mode"):
        build({**ENV, "STRIPE_SECRET_KEY": key})
```

Run: `uv run pytest tests/unit/web/test_web_device_check.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Implement the wiring module**

`src/tutor/web/device_check_server.py`:

```python
"""Real Google login and Stripe test mode over in-memory demo data, for the real-device
check (Task 19). Not production: no database, data is lost on restart."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime

from fastapi import FastAPI

from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import seed_demo
from tutor.web.google import GoogleOidcLogin
from tutor.web.memory import MemoryBackend, memory_deps
from tutor.web.stripe_gateway import StripeGateway


def build(environ: Mapping[str, str]) -> FastAPI:
    secret = environ.get("STRIPE_SECRET_KEY", "")
    if not secret.startswith("sk_test_"):
        raise RuntimeError("the device check runs on Stripe test mode only (sk_test_ keys)")
    backend = MemoryBackend(
        price_id=environ["STRIPE_PRICE_ID"],
        price_cents=int(environ.get("STRIPE_PRICE_CENTS", "2900")),
    )
    seed_demo(backend, datetime.now(UTC).date())
    deps = memory_deps(
        backend,
        lambda: datetime.now(UTC),
        billing=StripeGateway(secret, environ["STRIPE_WEBHOOK_SECRET"]),
        google=GoogleOidcLogin(environ["GOOGLE_CLIENT_ID"], environ["GOOGLE_CLIENT_SECRET"]),
    )
    return create_app(deps, WebConfig.from_env(environ))


def build_from_env() -> FastAPI:
    return build(os.environ)
```

Run: `uv run pytest tests/unit/web/test_web_device_check.py -q` then `uv run just check-fast`
Expected: PASS. (If the secret-detection pre-commit hook flags the placeholder strings in the test, keep them low-entropy as written; never put real keys in tests.)

Commit:

```bash
git add src/tutor/web/device_check_server.py tests/unit/web/test_web_device_check.py
git commit -m "feat(web): device-check server wiring (Stripe test mode, real Google login)"
```

- [ ] **Step 3: Prepare Google, Stripe and the tunnel (author, manual)**

1. **Google:** in Google Cloud Console, create an OAuth client of type "Web application" in the existing Testing-mode project (only listed test users can sign in). Authorized redirect URI: `https://<dash-host>/auth/callback`, where `<dash-host>` is a new subdomain such as `dash-test.<your-domain>`.
2. **Stripe (test mode):**
   - Product "English Tutor Annual", recurring yearly Price of USD 29.00 → copy the `price_…` id.
   - Settings → Payment methods: only cards enabled for subscriptions.
   - Customer Portal (test): allow updating the payment method, viewing invoices and cancelling.
   - Developers → Webhooks → add endpoint `https://<dash-host>/webhooks/stripe` with exactly `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.deleted` → copy the signing secret `whsec_…`.
3. **Tunnel:** add an ingress rule for `<dash-host>` → `http://127.0.0.1:8780` to the existing `cloudflared` config (same pattern as the spike's ingress), then `cloudflared tunnel route dns <tunnel-name> <dash-host>`. Cache bypass applies as in the spike runbook.
4. **Start the server** in a PowerShell window (secrets live only in this session's environment):

```powershell
$env:TUTOR_ENV = "dev"
$env:TUTOR_BASE_URL = "https://<dash-host>"
$env:TUTOR_MCP_URL = "https://<dash-host>/mcp"
$env:TUTOR_SUPPORT_EMAIL = "<support address>"
$env:STRIPE_SECRET_KEY = "<sk_test_…>"
$env:STRIPE_WEBHOOK_SECRET = "<whsec_…>"
$env:STRIPE_PRICE_ID = "<price_…>"
$env:STRIPE_PRICE_CENTS = "2900"
$env:GOOGLE_CLIENT_ID = "<client id>"
$env:GOOGLE_CLIENT_SECRET = "<client secret>"
uv run uvicorn tutor.web.device_check_server:build_from_env --factory --host 127.0.0.1 --port 8780
```

In a second window: `cloudflared tunnel run <tunnel-name>`. Open `https://<dash-host>/login` on a laptop first and sign in once to confirm the setup.

- [ ] **Step 4: Run the two flows on real phones (author, manual)**

For each phone: **iPhone** (Safari, latest iOS available) and **Android** (Chrome, latest).

1. Open `https://<dash-host>/login`. Install: iPhone → Compartir → Agregar a pantalla de inicio; Android → the browser's install prompt or the "Instalar" button.
2. Open the installed app from the home screen. Confirm it opens standalone (no browser address bar).
3. **Flow A, login:** tap "Entrar con Google", complete Google sign-in. Record: did the Google page open inside the app, in an in-app browser sheet, or in the separate browser? Did the app end on `/app/` signed in, without an address bar?
4. **Flow B, Stripe return:** go to `/billing/checkout`, tap "Pagar", pay with test card `4242 4242 4242 4242` (any future date, any CVC). Record: did Checkout open inside the app? After paying, did the return page open inside the installed app, show "Confirmando tu pago…", then "Tu plan Annual está activo" within 30 s?
5. Also record: Customer Portal opens and "return to site" lands back in the app; logout followed by the back button shows the login page.
6. Take screenshots of each end state; store them under `docs/adr/NNNN-assets/` with no personal data (crop email addresses).

Results table (copy into the ADR):

| Device | OS / browser version | Flow | Ends inside installed app? | What happened | Screenshot |
| --- | --- | --- | --- | --- | --- |
| iPhone | | A login | | | |
| iPhone | | B Stripe return | | | |
| iPhone | | Portal return | | | |
| Android | | A login | | | |
| Android | | B Stripe return | | | |
| Android | | Portal return | | | |

- [ ] **Step 5: Decide the fallback if a flow breaks**

If every row is "yes", no fallback is needed. If iOS (or Android) breaks a flow, choose per spec 8.4 and record the choice in the ADR:
- **Option 1, login in the browser:** the installed app detects standalone mode and shows "Abre English Tutor en Safari para entrar"; the session cookie set in Safari is not shared with the installed app on iOS, so this option works only if Flow A breaks on Android, or if the author accepts that iOS users use the site in Safari instead of the installed app.
- **Option 2, one-time code handoff:** the browser finishes login and shows a 6-digit, single-use, 2-minute code that the installed app exchanges for a session. This is security-sensitive: it needs its own spec section, a review by the `security-reviewer` agent, and rate limiting, before any code.
- Prefer the option with the least new code that makes both flows complete on both phones; if neither is acceptable, the dashboard ships as a normal website for iOS (installable on Android and desktop) and the ADR says so.

- [ ] **Step 6: Write the ADR**

`docs/adr/NNNN-dashboard-stack-and-pwa.md`:

```markdown
# NNNN — Dashboard stack, installable web app and payments

Status: accepted (results section filled on <date of the device check>)
Date: 2027-01-<day>
Spec: docs/superpowers/specs/2026-10-04-dashboard-design.md
Plan: docs/superpowers/plans/2026-10-04-dashboard-web-1-foundation.md and docs/superpowers/plans/2026-10-04-dashboard-web-2-pages.md

## Context

Requirements section 1 puts a minimal web dashboard (login, plan, progress, glossary,
billing) in scope for v1 and native mobile apps out of scope. Section 4 fixes the
dashboard stack as server-rendered Jinja2 + HTMX. The author wants the dashboard
installable from the phone's browser, without app-store publishing, and with a
motion layer. Two flows are at risk in an installed web app, especially on iOS, where
an installed app keeps cookies separate from Safari: Google login and the return from
Stripe Checkout.

## Decision

1. The dashboard is a `tutor.web` package of FastAPI routes, Jinja2 templates and HTMX
   partials, with no JavaScript build step and no Node. Routes reach the rest of the
   system only through typed ports (`tutor.web.ports`).
2. It is an installable web app shell: manifest, icons, standalone display, a service
   worker that caches only static assets and an offline page. No personal data is
   stored on the device; no push notifications; no app store.
3. One cookie, `__Host-tutor_session`, backed by server-side sessions; Authlib's OAuth
   state lives in that session, so login needs no second cookie.
4. Payments use Stripe Checkout (hosted) and the Customer Portal. Webhooks are the only
   writer of subscription state; events are idempotent by id and older events never
   override newer state.
5. Rulings taken while planning (2026-10-04), with evidence:
   - No Motion library: its bundle (≈ 47.5 KB gzip) plus HTMX (≈ 16.8 KB) breaks the
     50 KB JavaScript budget. Motion uses the Web Animations API and CSS in our own
     `app.js`.
   - Card only: Stripe Checkout does not support OXXO for subscriptions; the
     pending-payment page is not built. A one-time "Annual prepaid" product is the
     documented way to add OXXO later.
   - es-MX numbers use a decimal point ("3.1").
6. The real-device check below is the acceptance test for decision 2.

## Results of the real-device check

<paste the results table from Task 19 Step 4>

Fallback chosen (if any): <none | option 1 | option 2 | website-only on iOS>, because <one sentence>.

## Consequences

- One Python codebase serves MCP, REST and the dashboard; the dashboard is testable
  without a database through the in-memory ports.
- Learners without a card cannot buy Annual in v1.
- Animations are hand-written (≈ 8 KB); richer physics would need a library and a
  budget change.
- If a fallback was chosen, its follow-up work is: <link to the spec section or "none">.
```

- [ ] **Step 7: Stop the check and commit the ADR**

Stop uvicorn and `cloudflared`. Delete the Stripe test webhook endpoint (or leave it disabled) and remove the `<dash-host>` ingress rule if it should not stay public. Then:

```bash
git add docs/adr
git commit -m "docs(adr): dashboard stack, installable web app and real-device results"
```

If a flow failed and option 2 was chosen, stop and ask the author before continuing with Part E: the fallback needs its own spec section first.

## Part E — Pages

Every task in this part follows "Conventions for page tasks". Catalog rule for every `.po` step: append the entries shown; if an entry with the same `msgid` already exists in `src/tutor/web/locale/en/LC_MESSAGES/messages.po` (another task added it first), keep the existing entry and skip the duplicate. CSS rule for every CSS step: append the block to the end of `src/tutor/web/static/css/app.css`; it styles only page-specific classes (vocabulary classes belong to Task 13).

### Task 20: Conectar (onboarding) and lenient query parsing

**Files:**
- Create: `src/tutor/web/query.py`
- Create: `src/tutor/web/routes/connect.py`
- Create: `src/tutor/web/templates/pages/connect.html`
- Create: `src/tutor/web/templates/partials/connect_steps.html`
- Create: `src/tutor/web/templates/partials/connect_status.html`
- Modify: `src/tutor/web/routes/__init__.py` (append `connect.router`)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `src/tutor/web/static/css/app.css`
- Test: `tests/unit/web/test_web_query.py`, `tests/unit/web/test_web_connect.py`

**Interfaces:**
- Consumes: `APP_ROUTER_DEPS`, `current_user`, `get_deps`, `get_config` (Task 11); `render`, `is_htmx` (Task 10); `DashboardReader.has_any_session` (Task 7); fixtures `login`, `demo`, `backend` (Task 10).
- Produces:
  - `tutor.web.query`: `enum_or_none[E: StrEnum](cls: type[E], raw: str | None) -> E | None`; `bounded_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int`; `parse_uuid(raw: str) -> UUID | None`. Bad input falls back; it never raises.
  - `tutor.web.routes.connect`: `POLL_LIMIT = 60`; `connect_context(request: Request, user: User, n: int = 0) -> dict[str, Any]` returning `mcp_url: str`, `connected: bool`, `n: int`, `gave_up: bool` (Task 21 reuses it).
  - Routes `GET /app/connect`, `GET /app/connect/status?n=` (HTMX only; plain requests get 303 to `/app/connect`).
  - Partials `partials/connect_steps.html` (context: `mcp_url`) and `partials/connect_status.html` (context: `connected`, `n`, `gave_up`; root element `#connect-status`).
  - Copy buttons carry only `data-copy`; Task 15's handler swaps the button text for the word in `#live`'s `data-copied`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_query.py`:

```python
from uuid import uuid4

from tutor.domain.dashboard.types import Mode
from tutor.web.query import bounded_int, enum_or_none, parse_uuid


def test_enum_or_none_accepts_values_and_ignores_the_rest() -> None:
    assert enum_or_none(Mode, "voice") is Mode.VOICE
    assert enum_or_none(Mode, "fax") is None
    assert enum_or_none(Mode, "") is None
    assert enum_or_none(Mode, None) is None


def test_bounded_int_clamps_and_falls_back() -> None:
    assert bounded_int("5", default=0, minimum=1, maximum=10) == 5
    assert bounded_int(" 7 ", default=0, minimum=1, maximum=10) == 7
    assert bounded_int("50", default=0, minimum=1, maximum=10) == 10
    assert bounded_int("0", default=0, minimum=1, maximum=10) == 1
    assert bounded_int("-3", default=4, minimum=1, maximum=10) == 4
    assert bounded_int("abc", default=4, minimum=1, maximum=10) == 4
    assert bounded_int(None, default=4, minimum=1, maximum=10) == 4
    assert bounded_int("9" * 5000, default=4, minimum=1, maximum=10) == 4


def test_parse_uuid() -> None:
    value = uuid4()
    assert parse_uuid(str(value)) == value
    assert parse_uuid("not-a-uuid") is None
```

`tests/unit/web/test_web_connect.py`:

```python
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

HX = {"hx-request": "true"}
Login = Callable[[UUID], TestClient]


def test_connect_page_shows_address_command_and_first_phrase(login: Login, demo: DemoUsers) -> None:
    page = login(demo.nuevo).get("/app/connect")
    assert page.status_code == 200
    assert 'data-copy="https://testserver/mcp"' in page.text
    assert "claude mcp add --transport http tutor https://testserver/mcp" in page.text
    assert 'data-copy="start my lesson"' in page.text
    assert 'href="https://claude.ai"' in page.text


def test_new_user_waits_with_polling(login: Login, demo: DemoUsers) -> None:
    page = login(demo.nuevo).get("/app/connect")
    assert 'hx-get="/app/connect/status?n=1"' in page.text
    assert 'hx-trigger="every 10s"' in page.text
    assert "Esperando tu primera sesión…" in page.text


def test_connected_user_gets_a_link_and_no_polling(login: Login, demo: DemoUsers) -> None:
    page = login(demo.ana).get("/app/connect")
    assert "Tu tutor ya registró tu primera sesión." in page.text
    assert "every 10s" not in page.text


def test_status_partial_counts_and_stops_after_the_limit(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    partial = c.get("/app/connect/status?n=5", headers=HX)
    assert partial.status_code == 200 and "<html" not in partial.text
    assert 'hx-get="/app/connect/status?n=6"' in partial.text
    done = c.get("/app/connect/status?n=60", headers=HX)
    assert "every 10s" not in done.text and "Revisar de nuevo" in done.text
    assert "Revisar de nuevo" in c.get("/app/connect/status?n=999999", headers=HX).text
    assert 'status?n=1"' in c.get("/app/connect/status?n=-3", headers=HX).text


def test_status_without_htmx_redirects_to_the_page(login: Login, demo: DemoUsers) -> None:
    response = login(demo.nuevo).get("/app/connect/status?n=2")
    assert response.status_code == 303 and response.headers["location"] == "/app/connect"


def test_status_flips_when_the_first_session_arrives(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.nuevo)
    backend.session_rows[demo.nuevo] = backend.session_rows[demo.ana][:1]
    assert "Ir a Inicio" in c.get("/app/connect/status?n=3", headers=HX).text


def test_connect_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.nuevo] = replace(backend.users[demo.nuevo], lang=Lang.EN)
    assert "Connect your tutor" in login(demo.nuevo).get("/app/connect").text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/web/test_web_query.py tests/unit/web/test_web_connect.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.web.query'`.

- [ ] **Step 3: Implement `query.py` and the routes**

`src/tutor/web/query.py`:

```python
"""Lenient query-string parsing: a bad value falls back instead of breaking the page."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

_MAX_DIGITS = 9  # int() refuses very long digit strings; nothing here needs more


def enum_or_none[E: StrEnum](cls: type[E], raw: str | None) -> E | None:
    if not raw:
        return None
    try:
        return cls(raw)
    except ValueError:
        return None


def bounded_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    text = (raw or "").strip()
    if not text.isdecimal() or len(text) > _MAX_DIGITS:
        return default
    return min(max(int(text), minimum), maximum)


def parse_uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None
```

`src/tutor/web/routes/connect.py`:

```python
"""Conectar: how to add the tutor to Claude, then a short wait for the first session."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.types import User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_config, get_deps
from tutor.web.query import bounded_int
from tutor.web.views import is_htmx, render

router = APIRouter(dependencies=APP_ROUTER_DEPS)
POLL_LIMIT = 60  # one check every 10 s: ten minutes, then a "check again" button


def connect_context(request: Request, user: User, n: int = 0) -> dict[str, Any]:
    connected = get_deps(request).reader.has_any_session(user.id)
    return {
        "mcp_url": get_config(request).mcp_url,
        "connected": connected,
        "n": n,
        "gave_up": not connected and n >= POLL_LIMIT,
    }


@router.get("/app/connect", response_class=HTMLResponse)
async def connect_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    return render(request, "pages/connect.html", connect_context(request, user))


@router.get("/app/connect/status")
async def connect_status(request: Request, user: User = Depends(current_user)) -> Response:
    if not is_htmx(request):
        return RedirectResponse("/app/connect", status_code=303)
    n = bounded_int(request.query_params.get("n"), default=0, minimum=0, maximum=POLL_LIMIT)
    return render(request, "partials/connect_status.html", connect_context(request, user, n))
```

In `src/tutor/web/routes/__init__.py`, add `connect` to the `from tutor.web.routes import …` line inside `all_routers` and append `connect.router` to `routers`.

- [ ] **Step 4: Write the templates**

`src/tutor/web/templates/partials/connect_steps.html`:

```html
<section class="card card--wide connect" aria-labelledby="connect-title">
  <h2 id="connect-title" class="card__title">{{ _("Conecta tu tutor") }}</h2>
  <p>{{ _("Tu tutor vive dentro de Claude. Conéctalo una vez; después solo di la frase de inicio.") }}</p>
  <ol class="steps">
    <li>
      <h3>{{ _("Copia la dirección de tu tutor") }}</h3>
      <div class="code-copy">
        <code>{{ mcp_url }}</code>
        <button type="button" class="btn btn--ghost btn--small copy" data-copy="{{ mcp_url }}">{{ _("Copiar") }}</button>
      </div>
    </li>
    <li>
      <h3>{{ _("Agrégalo a Claude") }}</h3>
      <p>{{ _("En Claude web, escritorio o celular: Configuración → Conectores → Agregar conector personalizado. Pega la dirección y entra con tu cuenta de Google.") }}</p>
      <p>{{ _("En Claude Code, ejecuta:") }}</p>
      <div class="code-copy">
        <code>claude mcp add --transport http tutor {{ mcp_url }}</code>
        <button type="button" class="btn btn--ghost btn--small copy" data-copy="claude mcp add --transport http tutor {{ mcp_url }}">{{ _("Copiar") }}</button>
      </div>
    </li>
    <li>
      <h3>{{ _("Empieza tu primera lección") }}</h3>
      <p>{{ _("En un chat nuevo, di:") }}</p>
      <div class="code-copy">
        <code lang="en">start my lesson</code>
        <button type="button" class="btn btn--ghost btn--small copy" data-copy="start my lesson">{{ _("Copiar") }}</button>
      </div>
    </li>
  </ol>
  <a class="btn btn--primary" href="https://claude.ai" target="_blank" rel="noopener noreferrer">{{ _("Abrir Claude") }}</a>
</section>
```

`src/tutor/web/templates/partials/connect_status.html` (the live region is set only on the final states, so screen readers are not re-told "waiting" every 10 s):

```html
<div id="connect-status" class="card connect-status"{% if connected or gave_up %} role="status"{% else %} hx-get="/app/connect/status?n={{ n + 1 }}" hx-trigger="every 10s" hx-swap="outerHTML"{% endif %}>
  {% if connected %}
  <p><svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#check"></use></svg> {{ _("Tu tutor ya registró tu primera sesión.") }}</p>
  <a class="btn btn--primary" href="/app/">{{ _("Ir a Inicio") }}</a>
  {% elif gave_up %}
  <p>{{ _("Aún no vemos tu primera sesión. Cuando termines una lección en Claude, revisa de nuevo.") }}</p>
  <a class="btn btn--ghost" href="/app/connect" hx-get="/app/connect/status?n=0" hx-target="#connect-status" hx-swap="outerHTML">{{ _("Revisar de nuevo") }}</a>
  {% else %}
  <p><span class="skeleton skeleton--dot" aria-hidden="true"></span> {{ _("Esperando tu primera sesión…") }}</p>
  {% endif %}
</div>
```

`src/tutor/web/templates/pages/connect.html`:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "connect" %}
{% block title %}{{ _("Conectar") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head"><h1>{{ _("Conectar") }}</h1></header>
{% include "partials/connect_steps.html" %}
{% include "partials/connect_status.html" %}
{% endblock %}
```

- [ ] **Step 5: Add the English translations**

```po
msgid "Conectar"
msgstr "Connect"

msgid "Conecta tu tutor"
msgstr "Connect your tutor"

msgid "Tu tutor vive dentro de Claude. Conéctalo una vez; después solo di la frase de inicio."
msgstr "Your tutor lives inside Claude. Connect it once; after that, just say the start phrase."

msgid "Copia la dirección de tu tutor"
msgstr "Copy your tutor's address"

msgid "Copiar"
msgstr "Copy"

msgid "Agrégalo a Claude"
msgstr "Add it to Claude"

msgid "En Claude web, escritorio o celular: Configuración → Conectores → Agregar conector personalizado. Pega la dirección y entra con tu cuenta de Google."
msgstr "In Claude on the web, desktop or phone: Settings → Connectors → Add custom connector. Paste the address and sign in with your Google account."

msgid "En Claude Code, ejecuta:"
msgstr "In Claude Code, run:"

msgid "Empieza tu primera lección"
msgstr "Start your first lesson"

msgid "En un chat nuevo, di:"
msgstr "In a new chat, say:"

msgid "Abrir Claude"
msgstr "Open Claude"

msgid "Tu tutor ya registró tu primera sesión."
msgstr "Your tutor has recorded your first session."

msgid "Aún no vemos tu primera sesión. Cuando termines una lección en Claude, revisa de nuevo."
msgstr "We don't see your first session yet. When you finish a lesson in Claude, check again."

msgid "Revisar de nuevo"
msgstr "Check again"

msgid "Esperando tu primera sesión…"
msgstr "Waiting for your first session…"
```

- [ ] **Step 6: Add the page CSS**

```css
/* Conectar (Task 20) */
.steps h3 { margin: 0 0 .25rem; font-size: 1rem; }
.connect-status { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; margin-top: 1rem; }
.connect-status p { margin: 0; }
.skeleton--dot { display: inline-block; width: .75rem; height: .75rem; border-radius: 50%; background: var(--leaf); vertical-align: middle; }
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/unit/web -q` then `uv run just lint`
Expected: PASS, including `test_every_template_string_has_an_english_translation`.

- [ ] **Step 8: Commit**

```bash
git add src/tutor/web tests/unit/web
git commit -m "feat(web): Conectar page with first-session wait and lenient query parsing"
```

### Task 21: Inicio

**Files:**
- Create: `src/tutor/web/routes/home.py`
- Create: `src/tutor/web/templates/pages/home.html`
- Create: `src/tutor/web/templates/partials/trail.html`
- Create: `src/tutor/web/templates/macros/labels.html`
- Modify: `src/tutor/web/routes/__init__.py` (append `home.router`)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `src/tutor/web/static/css/app.css`
- Test: `tests/unit/web/test_web_home.py`

**Interfaces:**
- Consumes: `build_week_trail`, `should_celebrate` (Task 2); `HomeData`, `TrailNode` (Task 1); `connect_context` and the two connect partials (Task 20); `partials/install.html` (Task 14, context `install_dismissed: bool`); `AccountService.mark_celebrated`, `DashboardReader.home`, `DashboardReader.account` (Task 7); `today_for`, `render` (Task 10).
- Produces:
  - Route `GET /app/`.
  - `tutor.web.routes.home`: `TRAIL_POINTS` (seven `(x, y)` points in a 320 × 96 box) and `trail_view(trail: Sequence[TrailNode]) -> dict[str, Any]` with keys `nodes` (list of `{"x", "y", "node"}`), `path`, `walked` (SVG path data up to today).
  - `partials/trail.html` (context: `trail` from `trail_view`).
  - `macros/labels.html`, imported as `{% import "macros/labels.html" as labels %}`, with macros `mode_label(mode)`, `client_label(client)`, `result_label(result)`, `status_chip(summary)`, `trail_state(state)`, `kpi(value, label, decimals=0)`. Tasks 22–24 append more macros to this file; Task 25 (Progreso) and Task 27 (Reportes) should use `labels.kpi` rather than defining their own.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_home.py`:

```python
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.home import build_week_trail
from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import FixedClock, MemoryBackend, empty_home
from tutor.web.routes.home import trail_view

from .conftest import TODAY

Login = Callable[[UUID], TestClient]


def test_inicio_shows_a_normal_week(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    html = login(demo.ana).get("/app/").text
    assert "Pedir aclaraciones en una revisión de código" in html
    assert 'data-copy="start my lesson"' in html
    assert html.count('class="trail__label') == 7
    assert 'data-count-to="4"' in html  # streak
    assert 'class="stamp stamp--used"' in html and "on track" in html
    last = backend.home(demo.ana, TODAY).last_session
    assert last is not None and f'href="/app/sessions/{last.id}"' in html
    assert "repasos esperan en tu próxima lección" in html
    assert "vt-today" in html and "vt-last-session" in html
    assert "minutos hablando" not in html  # Free: no weekly report preview


def test_annual_sees_the_weekly_report_preview(login: Login, demo: DemoUsers) -> None:
    html = login(demo.beto).get("/app/").text
    assert "minutos hablando" in html and 'data-count-to="45"' in html


def test_first_day_shows_connect_steps(login: Login, demo: DemoUsers) -> None:
    html = login(demo.nuevo).get("/app/").text
    assert "Conecta tu tutor" in html and 'hx-trigger="every 10s"' in html
    assert "trail__label" not in html


def test_connected_without_plan_asks_for_the_diagnostic(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    backend.homes[demo.nuevo] = replace(empty_home(TODAY), has_connected=True)
    html = login(demo.nuevo).get("/app/").text
    assert "Haz tu diagnóstico en el chat" in html


def test_celebrates_a_new_session_once(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    assert "data-celebrate" in c.get("/app/").text
    assert "data-celebrate" not in c.get("/app/").text
    assert backend.celebrated[demo.ana] == backend.homes[demo.ana].newest_closed_session_id


def test_today_follows_the_learner_timezone(
    login: Login, demo: DemoUsers, backend: MemoryBackend, clock: FixedClock
) -> None:
    clock.now = datetime(2027, 1, 13, 3, 30, tzinfo=UTC)  # 21:30 on Jan 12 in Mexico City
    html = login(demo.ana).get("/app/").text
    assert re.search(r'data-day="2027-01-12"[^>]*aria-current="date"', html)
    backend.users[demo.ana] = replace(backend.users[demo.ana], timezone="UTC")
    html_utc = login(demo.ana).get("/app/").text
    assert re.search(r'data-day="2027-01-13"[^>]*aria-current="date"', html_utc)


def test_inicio_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    assert "Your week" in login(demo.ana).get("/app/").text


def test_trail_view_walks_up_to_today(backend: MemoryBackend, demo: DemoUsers) -> None:
    home = backend.home(demo.ana, TODAY)
    view = trail_view(build_week_trail(home.week_start, home.planned_days, (), TODAY))
    assert len(view["nodes"]) == 7
    assert view["walked"] == "M20 70 L66 46"  # Monday and Tuesday (today)
    assert view["path"].startswith("M20 70 L66 46 L113 60")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/web/test_web_home.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.web.routes.home'`.

- [ ] **Step 3: Implement the route**

`src/tutor/web/routes/home.py`:

```python
"""Inicio: today's practice, the week trail and what is pending (spec 6.3)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from tutor.domain.dashboard.home import build_week_trail, should_celebrate
from tutor.domain.dashboard.types import TrailNode, User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.routes.connect import connect_context
from tutor.web.views import render, today_for

router = APIRouter(dependencies=APP_ROUTER_DEPS)

# One point per weekday, Monday first, inside a 320 × 96 viewBox.
TRAIL_POINTS: tuple[tuple[int, int], ...] = (
    (20, 70),
    (66, 46),
    (113, 60),
    (160, 34),
    (206, 52),
    (253, 28),
    (300, 44),
)


def _path(points: Sequence[tuple[int, int]]) -> str:
    return " ".join(f"{'M' if i == 0 else 'L'}{x} {y}" for i, (x, y) in enumerate(points))


def trail_view(trail: Sequence[TrailNode]) -> dict[str, Any]:
    nodes = [
        {"x": x, "y": y, "node": node} for (x, y), node in zip(TRAIL_POINTS, trail, strict=True)
    ]
    today_index = next((i for i, node in enumerate(trail) if node.is_today), len(trail) - 1)
    return {
        "nodes": nodes,
        "path": _path(TRAIL_POINTS),
        "walked": _path(TRAIL_POINTS[: today_index + 1]),
    }


@router.get("/app/", response_class=HTMLResponse)
async def home_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    deps = get_deps(request)
    today = today_for(request)
    home = deps.reader.home(user.id, today)
    celebrate = should_celebrate(home.newest_closed_session_id, home.last_celebrated_session_id)
    if celebrate and home.newest_closed_session_id is not None:
        # Marked before rendering so a reload never celebrates the same session twice.
        deps.account.mark_celebrated(user.id, home.newest_closed_session_id)
    trail = build_week_trail(home.week_start, home.planned_days, home.session_marks, today)
    return render(
        request,
        "pages/home.html",
        {
            **connect_context(request, user),
            "home": home,
            "trail": trail_view(trail),
            "celebrate": celebrate,
            "install_dismissed": deps.reader.account(user.id).install_prompt_dismissed,
        },
    )
```

In `src/tutor/web/routes/__init__.py`, add `home` to the import line inside `all_routers` and append `home.router` to `routers`.

- [ ] **Step 4: Write the label macros**

`src/tutor/web/templates/macros/labels.html`:

```html
{# Labels and chips shared by the pages. Import with: import "macros/labels.html" as labels #}
{% macro mode_label(mode) %}{% if mode == "voice" %}{{ _("Voz") }}{% else %}{{ _("Texto") }}{% endif %}{% endmacro %}

{% macro client_label(client) %}{% if client == "chatgpt" %}ChatGPT{% elif client == "code" %}Claude Code{% else %}Claude{% endif %}{% endmacro %}

{% macro result_label(result) %}{% if result == "achieved" %}{{ _("Logrado") }}{% elif result == "partial" %}{{ _("Parcial") }}{% elif result == "not_achieved" %}{{ _("No logrado") }}{% else %}—{% endif %}{% endmacro %}

{% macro status_chip(s) %}{% if s.status == "incomplete" %}<span class="chip chip--muted">{{ _("Incompleta") }}</span>{% elif s.status == "open" %}<span class="chip chip--muted">{{ _("En curso") }}</span>{% elif s.low_trust %}<span class="chip chip--warn">{{ _("Baja confianza") }}</span>{% else %}<span class="chip chip--ok">{{ _("Completa") }}</span>{% endif %}{% endmacro %}

{% macro trail_state(state) %}{% if state == "done" %}{{ _("Hecho") }}{% elif state == "today" %}{{ _("Hoy") }}{% elif state == "upcoming" %}{{ _("Próximo") }}{% elif state == "missed" %}{{ _("Sin sesión") }}{% else %}{{ _("Descanso") }}{% endif %}{% endmacro %}

{% macro kpi(value, label, decimals=0) %}
<div class="kpi">
  <p class="kpi__value"><span aria-hidden="true" data-count-to="{{ value }}" data-count-decimals="{{ decimals }}">{{ value|decimal(decimals) }}</span><span class="sr-only">{{ value|decimal(decimals) }}</span></p>
  <p class="kpi__label">{{ label }}</p>
</div>
{% endmacro %}
```

- [ ] **Step 5: Write the trail partial and the page**

`src/tutor/web/templates/partials/trail.html` (the SVG is decorative; the list below it carries the same information as text):

```html
{% import "macros/labels.html" as labels %}
<div class="trail">
  <svg class="trail__svg" viewBox="0 0 320 96" aria-hidden="true" focusable="false">
    <path class="trail__path" d="{{ trail.path }}" fill="none"></path>
    <path class="trail__path trail__path--walked" d="{{ trail.walked }}" fill="none" data-draw></path>
    {% for n in trail.nodes %}
    <circle class="trail__node trail__node--{{ n.node.state }}{% if n.node.is_today %} trail__node--is-today{% endif %}" cx="{{ n.x }}" cy="{{ n.y }}" r="{{ 11 if n.node.is_today else 8 }}"></circle>
    {% if n.node.extra_sessions %}<circle class="trail__extra" cx="{{ n.x + 12 }}" cy="{{ n.y - 12 }}" r="4"></circle>{% endif %}
    {% endfor %}
  </svg>
  <ol class="trail__days">
    {% for n in trail.nodes %}
    <li class="trail__label trail__label--{{ n.node.state }}" data-day="{{ n.node.day.isoformat() }}"{% if n.node.is_today %} aria-current="date"{% endif %}>
      <span class="trail__day">{{ n.node.day|day("short") }}</span>
      <span class="trail__state">{{ labels.trail_state(n.node.state) }}</span>
      {% if n.node.title %}<span class="sr-only">{{ n.node.title }}</span>{% endif %}
      {% if n.node.extra_sessions %}<span class="trail__extra-label">{% trans extra=n.node.extra_sessions %}+{{ extra }} prep{% endtrans %}</span>{% endif %}
    </li>
    {% endfor %}
  </ol>
</div>
```

`src/tutor/web/templates/pages/home.html`:

```html
{% extends "layouts/app.html" %}
{% import "macros/labels.html" as labels %}
{% set active_nav = "home" %}
{% block title %}{{ _("Inicio") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head">
  <h1>{% trans name=user.display_name %}Hola, {{ name }}{% endtrans %}</h1>
  <p class="muted">{{ today|day("weekday") }}</p>
</header>
{% if celebrate %}
<p class="celebrate" data-celebrate role="status"><svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#check"></use></svg> {{ _("Sesión guardada. Buen trabajo.") }}</p>
{% endif %}
{% if not home.has_connected %}
  {% include "partials/connect_steps.html" %}
  {% include "partials/connect_status.html" %}
{% else %}
<div class="grid">
  {% if not home.has_plan %}
  <section class="card card--wide today" aria-labelledby="next-title">
    <h2 id="next-title" class="card__title">{{ _("Tu siguiente paso") }}</h2>
    <p>{% trans %}Haz tu diagnóstico en el chat: di <code lang="en">start my lesson</code>.{% endtrans %}</p>
    <a class="btn btn--primary" href="https://claude.ai" target="_blank" rel="noopener noreferrer">{{ _("Abrir Claude") }}</a>
  </section>
  {% else %}
  <section class="card card--wide today vt-today" aria-labelledby="today-title">
    {% if home.today %}
    <p class="muted">{{ _("Práctica de hoy") }}</p>
    <h2 id="today-title" class="card__title">{{ home.today.title }}</h2>
    <p>{{ home.today.scenario_hint }}</p>
    {% else %}
    <h2 id="today-title" class="card__title">{{ _("Hoy no hay práctica en tu plan") }}</h2>
    <p>{{ _("Si tienes una reunión pronto, prepárala igual.") }}</p>
    {% endif %}
    <div class="code-copy">
      <code lang="en">start my lesson</code>
      <button type="button" class="btn btn--primary btn--small copy" data-copy="start my lesson">{{ _("Copiar frase") }}</button>
    </div>
    <p class="muted">{% trans %}¿Tienes una reunión? Di <code lang="en">prepare me for tomorrow's demo</code> y la lección se arma alrededor de ella.{% endtrans %}</p>
    <div class="card__foot">
      <a class="btn btn--ghost" href="https://claude.ai" target="_blank" rel="noopener noreferrer">{{ _("Abrir Claude") }}</a>
      <a href="/app/plan">{{ _("Ver plan") }}</a>
    </div>
  </section>

  <section class="card card--wide" aria-labelledby="week-title">
    <h2 id="week-title" class="card__title">{{ _("Tu semana") }}</h2>
    <p class="streak"><span class="streak__num" aria-hidden="true" data-count-to="{{ home.streak }}" data-count-decimals="0">{{ home.streak|integer }}</span> <span aria-hidden="true">{{ _("día seguido") if home.streak == 1 else _("días seguidos") }}</span><span class="sr-only">{% trans n=home.streak|integer %}Días seguidos: {{ n }}{% endtrans %}</span></p>
    {% include "partials/trail.html" %}
  </section>

  <section class="card" aria-labelledby="stamps-title">
    <h2 id="stamps-title" class="card__title">{{ _("Frases de la semana") }}</h2>
    {% if home.stamps %}
    <ul class="stamps" data-pop>
      {% for s in home.stamps %}
      <li class="stamp{% if s.used %} stamp--used{% endif %}"><span lang="en">{{ s.text }}</span> <span class="sr-only">{{ _("usada en una sesión") if s.used else _("aún sin usar") }}</span></li>
      {% endfor %}
    </ul>
    <p class="muted">{{ _("Se marcan cuando las usas en una sesión.") }}</p>
    {% else %}
    <p class="empty">{{ _("Las frases de tu próxima lección aparecerán aquí.") }}</p>
    {% endif %}
  </section>
  {% endif %}

  {% if home.last_session %}
  {% set s = home.last_session %}
  <section class="card vt-last-session" aria-labelledby="last-title">
    <h2 id="last-title" class="card__title">{{ _("Última sesión") }}</h2>
    <p>{{ s.label }}</p>
    <p class="muted">{{ s.started_at|day("weekday") }} · {{ labels.mode_label(s.mode) }} · {% trans minutes=s.duration_min %}{{ minutes }} min{% endtrans %}</p>
    <p>{{ labels.status_chip(s) }}{% if s.words_per_min is not none %} <span>{% trans wpm=s.words_per_min|decimal(0) %}{{ wpm }} palabras por minuto{% endtrans %}</span>{% endif %}</p>
    <a class="btn btn--ghost btn--small" href="/app/sessions/{{ s.id }}">{{ _("Ver detalle") }}</a>
  </section>
  {% endif %}

  <section class="card" aria-labelledby="pending-title">
    <h2 id="pending-title" class="card__title">{{ _("Pendiente") }}</h2>
    <ul class="pending">
      <li><strong>{{ home.reviews_due|integer }}</strong> {{ _("repasos esperan en tu próxima lección") }}</li>
      <li><strong>{{ home.provisional_items|integer }}</strong> {{ _("frases por confirmar") }}</li>
    </ul>
    {% if home.provisional_items %}<p class="muted">{{ _("Se te preguntará al iniciar tu próxima sesión.") }}</p>{% endif %}
    <a href="/app/glossary">{{ _("Ver glosario") }}</a>
  </section>

  {% if home.latest_report and subscription.tier == "annual" %}
  {% set r = home.latest_report %}
  <section class="card card--wide" aria-labelledby="report-title">
    <h2 id="report-title" class="card__title">{% trans when=r.week_start|day("long") %}Reporte de la semana del {{ when }}{% endtrans %}</h2>
    <div class="kpis">
      {{ labels.kpi(r.sessions_done, _("sesiones hechas de %(planned)s planeadas", planned=r.sessions_planned)) }}
      {{ labels.kpi(r.minutes_spoken, _("minutos hablando")) }}
      {{ labels.kpi(r.errors_stopped, _("errores que dejaste de cometer")) }}
      {{ labels.kpi(r.chunks_activated, _("frases activadas")) }}
    </div>
    <a href="/app/reports">{{ _("Ver reportes") }}</a>
  </section>
  {% endif %}
</div>
{% endif %}
{% include "partials/install.html" %}
{% endblock %}
```

- [ ] **Step 6: Add the English translations**

```po
msgid "Hola, %(name)s"
msgstr "Hi, %(name)s"

msgid "Sesión guardada. Buen trabajo."
msgstr "Session saved. Nice work."

msgid "Tu siguiente paso"
msgstr "Your next step"

msgid "Haz tu diagnóstico en el chat: di <code lang=\"en\">start my lesson</code>."
msgstr "Take your diagnostic in the chat: say <code lang=\"en\">start my lesson</code>."

msgid "Práctica de hoy"
msgstr "Today's practice"

msgid "Hoy no hay práctica en tu plan"
msgstr "No practice planned for today"

msgid "Si tienes una reunión pronto, prepárala igual."
msgstr "If you have a meeting soon, prepare for it anyway."

msgid "Copiar frase"
msgstr "Copy phrase"

msgid "¿Tienes una reunión? Di <code lang=\"en\">prepare me for tomorrow's demo</code> y la lección se arma alrededor de ella."
msgstr "Got a meeting? Say <code lang=\"en\">prepare me for tomorrow's demo</code> and the lesson is built around it."

msgid "Ver plan"
msgstr "See plan"

msgid "Tu semana"
msgstr "Your week"

msgid "día seguido"
msgstr "day in a row"

msgid "días seguidos"
msgstr "days in a row"

msgid "Días seguidos: %(n)s"
msgstr "Days in a row: %(n)s"

msgid "Frases de la semana"
msgstr "This week's phrases"

msgid "usada en una sesión"
msgstr "used in a session"

msgid "aún sin usar"
msgstr "not used yet"

msgid "Se marcan cuando las usas en una sesión."
msgstr "They're marked when you use them in a session."

msgid "Las frases de tu próxima lección aparecerán aquí."
msgstr "Your next lesson's phrases will appear here."

msgid "Última sesión"
msgstr "Last session"

msgid "%(minutes)s min"
msgstr "%(minutes)s min"

msgid "%(wpm)s palabras por minuto"
msgstr "%(wpm)s words per minute"

msgid "Ver detalle"
msgstr "See details"

msgid "Pendiente"
msgstr "To do"

msgid "repasos esperan en tu próxima lección"
msgstr "reviews waiting in your next lesson"

msgid "frases por confirmar"
msgstr "phrases to confirm"

msgid "Se te preguntará al iniciar tu próxima sesión."
msgstr "You'll be asked when your next session starts."

msgid "Ver glosario"
msgstr "See glossary"

msgid "Reporte de la semana del %(when)s"
msgstr "Report for the week of %(when)s"

msgid "sesiones hechas de %(planned)s planeadas"
msgstr "sessions done of %(planned)s planned"

msgid "minutos hablando"
msgstr "minutes speaking"

msgid "errores que dejaste de cometer"
msgstr "mistakes you stopped making"

msgid "frases activadas"
msgstr "phrases activated"

msgid "Ver reportes"
msgstr "See reports"

msgid "+%(extra)s prep"
msgstr "+%(extra)s prep"

msgid "Hecho"
msgstr "Done"

msgid "Hoy"
msgstr "Today"

msgid "Próximo"
msgstr "Upcoming"

msgid "Sin sesión"
msgstr "No session"

msgid "Descanso"
msgstr "Rest"

msgid "Voz"
msgstr "Voice"

msgid "Texto"
msgstr "Text"

msgid "Logrado"
msgstr "Achieved"

msgid "Parcial"
msgstr "Partial"

msgid "No logrado"
msgstr "Not achieved"

msgid "Incompleta"
msgstr "Incomplete"

msgid "En curso"
msgstr "In progress"

msgid "Baja confianza"
msgstr "Low trust"

msgid "Completa"
msgstr "Complete"
```

- [ ] **Step 7: Add the page CSS**

```css
/* Inicio (Task 21) */
.today .code-copy { margin-top: .75rem; }
.streak__num { font-family: "Baloo 2", system-ui, sans-serif; font-size: 1.25rem; }
.trail__svg { display: block; width: 100%; height: auto; max-height: 7rem; }
.trail__path--walked { stroke: var(--leaf); }
.trail__node--is-today { stroke: var(--coral); stroke-width: 3; }
.trail__days { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: .25rem; margin: .5rem 0 0; padding: 0; list-style: none; text-align: center; font-size: .75rem; }
.trail__label { display: flex; flex-direction: column; gap: .1rem; min-width: 0; }
.trail__label[aria-current="date"] .trail__day { font-weight: 700; color: var(--ink); }
.trail__state { overflow-wrap: anywhere; }
.trail__extra-label { color: var(--leaf); font-weight: 600; }
.pending { display: grid; gap: .35rem; margin: 0 0 .5rem; padding: 0; list-style: none; }
.pending strong { margin-right: .25rem; font-family: "Baloo 2", system-ui, sans-serif; font-size: 1.25rem; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr)); gap: .75rem; margin-bottom: .75rem; }
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/unit/web -q` then `uv run just lint`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/tutor/web tests/unit/web
git commit -m "feat(web): Inicio with today card, week trail, stamps and celebration"
```

### Task 22: Sesiones (list and detail)

**Files:**
- Create: `src/tutor/web/routes/sessions.py`
- Create: `src/tutor/web/templates/pages/sessions.html`
- Create: `src/tutor/web/templates/partials/sessions_table.html`
- Create: `src/tutor/web/templates/pages/session_detail.html`
- Modify: `src/tutor/web/templates/macros/labels.html` (append macros)
- Modify: `src/tutor/web/routes/__init__.py` (append `sessions.router`)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `src/tutor/web/static/css/app.css`
- Test: `tests/unit/web/test_web_sessions_pages.py`

**Interfaces:**
- Consumes: `SessionFilter`, `SessionPage`, `SessionDetail`, `Mode`, `SessionStatus` (Task 1); `DashboardReader.sessions`, `DashboardReader.session_detail` (Task 7); `enum_or_none`, `bounded_int`, `parse_uuid` (Task 20); `labels` macros (Task 21).
- Produces:
  - Routes `GET /app/sessions` (query `mode`, `status`, `page`; HTMX returns `partials/sessions_table.html`, root `#sessions-table`) and `GET /app/sessions/{session_id}` (404 for an unknown, malformed or other user's id).
  - `tutor.web.routes.sessions`: `PER_PAGE = 20`, `page_url(f: SessionFilter, page: int) -> str`.
  - Macros appended to `labels`: `category_label(category)`, `confidence_label(confidence)`.
  - CSS class `table--stack` (tables become stacked cards below 600 px, using each cell's `data-label`); Task 23 reuses it.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_sessions_pages.py`:

```python
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

HX = {"hx-request": "true"}
Login = Callable[[UUID], TestClient]


def test_list_is_newest_first_with_status_chips(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/sessions").text
    assert html.index("Dar una actualización de estado en el standup") < html.index(
        "Explicar un bloqueo técnico"
    )
    assert 'chip chip--muted">Incompleta<' in html
    assert 'chip chip--ok">Completa<' in html


def test_filter_by_mode_returns_the_table_partial(login: Login, demo: DemoUsers) -> None:
    response = login(demo.ana).get("/app/sessions?mode=text", headers=HX)
    assert response.status_code == 200 and "<html" not in response.text
    assert 'id="sessions-table"' in response.text
    assert "Explicar un bloqueo técnico" in response.text
    assert "standup" not in response.text


def test_bad_filters_are_ignored(login: Login, demo: DemoUsers) -> None:
    response = login(demo.ana).get("/app/sessions?mode=fax&status=x&page=abc")
    assert response.status_code == 200
    assert "Explicar un bloqueo técnico" in response.text


def test_pagination_keeps_filters(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    rows = backend.session_rows[demo.ana]
    rows.extend(replace(d, summary=replace(d.summary, id=uuid4())) for d in rows * 10)
    c = login(demo.ana)
    assert "/app/sessions?mode=voice&amp;page=2" in c.get("/app/sessions?mode=voice").text
    assert "/app/sessions?mode=voice&amp;page=1" in c.get("/app/sessions?mode=voice&page=2").text


def test_detail_shows_corrections_opinion_and_turns(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    detail = backend.session_rows[demo.ana][0]
    html = login(demo.ana).get(f"/app/sessions/{detail.summary.id}").text
    assert "I have went to the meeting" in html and "I went to the meeting" in html
    assert "data-strike" in html
    assert "opinión del modelo" in html and "B1+" in html
    assert "we are on track" in html  # evidence phrase
    assert "3 de 5" in html
    assert "Yesterday I have went to the meeting with the client." in html


def test_incomplete_session_says_it_does_not_count(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    incomplete = backend.session_rows[demo.ana][2]
    html = login(demo.ana).get(f"/app/sessions/{incomplete.summary.id}").text
    assert "Sesión incompleta" in html


def test_other_users_or_malformed_ids_are_404(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    beto_session = backend.session_rows[demo.beto][0].summary.id
    c = login(demo.ana)
    assert c.get(f"/app/sessions/{beto_session}").status_code == 404
    assert c.get("/app/sessions/not-a-uuid").status_code == 404


def test_user_turns_are_escaped(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    detail = backend.session_rows[demo.ana][0]
    backend.session_rows[demo.ana][0] = replace(detail, user_turns=("<script>alert(1)</script>",))
    html = login(demo.ana).get(f"/app/sessions/{detail.summary.id}").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html


def test_first_day_has_an_empty_state(login: Login, demo: DemoUsers) -> None:
    assert "Aún no tienes sesiones" in login(demo.nuevo).get("/app/sessions").text


def test_sessions_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    assert "Voice" in login(demo.ana).get("/app/sessions").text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/web/test_web_sessions_pages.py -q`
Expected: FAIL (the routes return 404).

- [ ] **Step 3: Implement the routes**

`src/tutor/web/routes/sessions.py`:

```python
"""Sesiones: the filtered list and the detail of one session (spec 6.6)."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from tutor.domain.dashboard.types import Mode, SessionFilter, SessionStatus, User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.query import bounded_int, enum_or_none, parse_uuid
from tutor.web.views import is_htmx, render

router = APIRouter(dependencies=APP_ROUTER_DEPS)
PER_PAGE = 20
MAX_PAGE = 10_000


def page_url(f: SessionFilter, page: int) -> str:
    pairs = [(k, str(v)) for k, v in (("mode", f.mode), ("status", f.status)) if v is not None]
    return "/app/sessions?" + urlencode([*pairs, ("page", str(page))])


@router.get("/app/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    params = request.query_params
    f = SessionFilter(
        mode=enum_or_none(Mode, params.get("mode")),
        status=enum_or_none(SessionStatus, params.get("status")),
    )
    page = bounded_int(params.get("page"), default=1, minimum=1, maximum=MAX_PAGE)
    result = get_deps(request).reader.sessions(user.id, f, page, PER_PAGE)
    ctx = {
        "result": result,
        "f": f,
        "prev_url": page_url(f, page - 1) if page > 1 else None,
        "next_url": page_url(f, page + 1) if result.has_next else None,
    }
    template = "partials/sessions_table.html" if is_htmx(request) else "pages/sessions.html"
    return render(request, template, ctx)


@router.get("/app/sessions/{session_id}", response_class=HTMLResponse)
async def session_detail(
    session_id: str, request: Request, user: User = Depends(current_user)
) -> HTMLResponse:
    sid = parse_uuid(session_id)
    detail = get_deps(request).reader.session_detail(user.id, sid) if sid else None
    if detail is None:
        raise HTTPException(status_code=404)
    return render(request, "pages/session_detail.html", {"d": detail})
```

In `src/tutor/web/routes/__init__.py`, add `sessions` to the import line inside `all_routers` and append `sessions.router` to `routers`.

- [ ] **Step 4: Append the macros**

Append to `src/tutor/web/templates/macros/labels.html`:

```html

{% macro category_label(category) %}{% if category == "grammar" %}{{ _("Gramática") }}{% elif category == "lexis" %}{{ _("Vocabulario") }}{% elif category == "word_order" %}{{ _("Orden de palabras") }}{% elif category == "register" %}{{ _("Registro") }}{% else %}{{ _("Otro") }}{% endif %}{% endmacro %}

{% macro confidence_label(confidence) %}{% if confidence == "high" %}{{ _("confianza alta") }}{% elif confidence == "medium" %}{{ _("confianza media") }}{% else %}{{ _("confianza baja") }}{% endif %}{% endmacro %}
```

- [ ] **Step 5: Write the templates**

`src/tutor/web/templates/pages/sessions.html`:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "sessions" %}
{% block title %}{{ _("Sesiones") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head"><h1>{{ _("Sesiones") }}</h1></header>
<form class="filters" method="get" action="/app/sessions" hx-get="/app/sessions" hx-target="#sessions-table" hx-swap="outerHTML" hx-push-url="true" hx-trigger="change">
  <label class="form-row">{{ _("Modo") }}
    <select name="mode">
      <option value="">{{ _("Todos") }}</option>
      <option value="voice"{% if f.mode == "voice" %} selected{% endif %}>{{ _("Voz") }}</option>
      <option value="text"{% if f.mode == "text" %} selected{% endif %}>{{ _("Texto") }}</option>
    </select>
  </label>
  <label class="form-row">{{ _("Estado") }}
    <select name="status">
      <option value="">{{ _("Todos") }}</option>
      <option value="closed"{% if f.status == "closed" %} selected{% endif %}>{{ _("Completa") }}</option>
      <option value="incomplete"{% if f.status == "incomplete" %} selected{% endif %}>{{ _("Incompleta") }}</option>
    </select>
  </label>
  <button class="btn btn--ghost btn--small">{{ _("Filtrar") }}</button>
</form>
{% include "partials/sessions_table.html" %}
{% endblock %}
```

`src/tutor/web/templates/partials/sessions_table.html`:

```html
{% import "macros/labels.html" as labels %}
<div id="sessions-table">
  {% if result.items %}
  <div class="table-wrap">
    <table class="table table--stack">
      <caption class="sr-only">{{ _("Tus sesiones, de la más reciente a la más antigua") }}</caption>
      <thead>
        <tr>
          <th scope="col">{{ _("Fecha") }}</th>
          <th scope="col">{{ _("Práctica") }}</th>
          <th scope="col">{{ _("Modo") }}</th>
          <th scope="col">{{ _("Cliente") }}</th>
          <th scope="col">{{ _("Duración") }}</th>
          <th scope="col">{{ _("Palabras/min") }}</th>
          <th scope="col">{{ _("Resultado") }}</th>
          <th scope="col">{{ _("Estado") }}</th>
        </tr>
      </thead>
      <tbody>
        {% for s in result.items %}
        <tr>
          <td data-label="{{ _('Fecha') }}"><a href="/app/sessions/{{ s.id }}">{{ s.started_at|day("short") }}</a></td>
          <td data-label="{{ _('Práctica') }}">{{ s.label }}</td>
          <td data-label="{{ _('Modo') }}">{{ labels.mode_label(s.mode) }}</td>
          <td data-label="{{ _('Cliente') }}">{{ labels.client_label(s.client) }}</td>
          <td data-label="{{ _('Duración') }}">{% trans minutes=s.duration_min %}{{ minutes }} min{% endtrans %}</td>
          <td data-label="{{ _('Palabras/min') }}">{{ s.words_per_min|decimal(0) if s.words_per_min is not none else "—" }}</td>
          <td data-label="{{ _('Resultado') }}">{{ labels.result_label(s.task_result) }}</td>
          <td data-label="{{ _('Estado') }}">{{ labels.status_chip(s) }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <nav class="pager" aria-label="{{ _('Páginas') }}">
    {% if prev_url %}<a class="btn btn--ghost btn--small" href="{{ prev_url }}" hx-get="{{ prev_url }}" hx-target="#sessions-table" hx-swap="outerHTML" hx-push-url="true">{{ _("Más recientes") }}</a>{% endif %}
    {% if next_url %}<a class="btn btn--ghost btn--small" href="{{ next_url }}" hx-get="{{ next_url }}" hx-target="#sessions-table" hx-swap="outerHTML" hx-push-url="true">{{ _("Anteriores") }}</a>{% endif %}
  </nav>
  {% elif f.mode or f.status %}
  <p class="empty">{{ _("Ninguna sesión coincide con estos filtros.") }}</p>
  {% else %}
  <p class="empty">{{ _("Aún no tienes sesiones. Cuando termines tu primera lección en Claude, aparecerá aquí.") }} <a href="/app/connect">{{ _("Cómo conectar") }}</a></p>
  {% endif %}
</div>
```

`src/tutor/web/templates/pages/session_detail.html`:

```html
{% extends "layouts/app.html" %}
{% import "macros/labels.html" as labels %}
{% set active_nav = "sessions" %}
{% block title %}{{ _("Sesión") }} · English Tutor{% endblock %}
{% block content %}
{% set s = d.summary %}
<p><a href="/app/sessions">← {{ _("Todas las sesiones") }}</a></p>
<header class="page-head vt-last-session">
  <h1>{{ s.label }}</h1>
  <p class="muted">{{ s.started_at|day("weekday") }} · {{ labels.mode_label(s.mode) }} · {{ labels.client_label(s.client) }} · {% trans minutes=s.duration_min %}{{ minutes }} min{% endtrans %}</p>
  <p>{{ labels.status_chip(s) }}</p>
</header>
{% if s.status == "incomplete" %}<div class="banner banner--info" role="note"><p>{{ _("Sesión incompleta: no cuenta para tus tendencias.") }}</p></div>{% endif %}
{% if s.low_trust %}<div class="banner banner--info" role="note"><p>{{ _("Evidencia poco confiable: guardamos tus palabras, pero no los errores de esta sesión.") }}</p></div>{% endif %}
<div class="grid">
  <section class="card card--wide" aria-labelledby="errors-title">
    <h2 id="errors-title" class="card__title">{{ _("Correcciones") }}</h2>
    {% if d.errors %}
    <ol class="errors">
      {% for e in d.errors %}
      <li class="errors__item" data-strike>
        <p class="said"><span class="errors__tag">{{ _("dijiste") }}</span> <span lang="en">{{ e.said }}</span></p>
        <p class="correct"><span class="errors__tag">{{ _("mejor") }}</span> <span lang="en">{{ e.correct }}</span></p>
        <p><span class="chip chip--muted">{{ labels.category_label(e.category) }}</span>{% if e.taken_up %} <span class="chip chip--ok">{{ _("La usaste después en la sesión") }}</span>{% endif %}</p>
      </li>
      {% endfor %}
    </ol>
    {% else %}
    <p class="empty">{{ _("Sin correcciones en esta sesión.") }}</p>
    {% endif %}
  </section>

  <section class="card" aria-labelledby="result-title">
    <h2 id="result-title" class="card__title">{{ _("Resultado") }}</h2>
    <dl class="facts">
      <dt>{{ _("Tarea") }}</dt><dd>{{ labels.result_label(s.task_result) }}</dd>
      <dt>{{ _("Pistas") }}</dt><dd>{{ d.hints_given|integer }}</dd>
      <dt>{{ _("Palabras por minuto") }}</dt><dd>{{ s.words_per_min|decimal(0) if s.words_per_min is not none else "—" }}</dd>
      <dt>{{ _("Tu confianza") }}</dt><dd>{% if d.confidence_1_5 %}{% trans n=d.confidence_1_5 %}{{ n }} de 5{% endtrans %}{% else %}—{% endif %}</dd>
    </dl>
  </section>

  <section class="card" aria-labelledby="chunks-title">
    <h2 id="chunks-title" class="card__title">{{ _("Frases") }}</h2>
    {% if d.chunks_offered %}
    <ul class="stamps">
      {% for c in d.chunks_offered %}
      <li class="stamp{% if c in d.chunks_used %} stamp--used{% endif %}"><span lang="en">{{ c }}</span> <span class="sr-only">{{ _("usada en una sesión") if c in d.chunks_used else _("aún sin usar") }}</span></li>
      {% endfor %}
    </ul>
    {% else %}
    <p class="empty">{{ _("Esta sesión no tuvo frases propuestas.") }}</p>
    {% endif %}
  </section>

  {% if d.cefr %}
  <section class="card" aria-labelledby="cefr-title">
    <h2 id="cefr-title" class="card__title">{{ _("Nivel estimado") }}</h2>
    <p class="muted">{{ _("Es la opinión del modelo, no una certificación.") }}</p>
    <p class="cefr"><strong>{{ d.cefr.level }}</strong> · {{ labels.confidence_label(d.cefr.confidence) }}</p>
    {% if d.cefr.evidence %}
    <p>{{ _("Lo basó en:") }}</p>
    <ul class="evidence">{% for ev in d.cefr.evidence %}<li lang="en">{{ ev }}</li>{% endfor %}</ul>
    {% endif %}
  </section>
  {% endif %}

  <section class="card card--wide" aria-labelledby="turns-title">
    <h2 id="turns-title" class="card__title">{{ _("Lo que dijiste") }}</h2>
    <ol class="turns">{% for t in d.user_turns %}<li lang="en">{{ t }}</li>{% endfor %}</ol>
  </section>
</div>
{% endblock %}
```

- [ ] **Step 6: Add the English translations**

```po
msgid "Modo"
msgstr "Mode"

msgid "Todos"
msgstr "All"

msgid "Estado"
msgstr "Status"

msgid "Filtrar"
msgstr "Filter"

msgid "Tus sesiones, de la más reciente a la más antigua"
msgstr "Your sessions, newest first"

msgid "Fecha"
msgstr "Date"

msgid "Práctica"
msgstr "Practice"

msgid "Cliente"
msgstr "Client"

msgid "Duración"
msgstr "Duration"

msgid "Palabras/min"
msgstr "Words/min"

msgid "Resultado"
msgstr "Result"

msgid "Páginas"
msgstr "Pages"

msgid "Más recientes"
msgstr "Newer"

msgid "Anteriores"
msgstr "Older"

msgid "Ninguna sesión coincide con estos filtros."
msgstr "No session matches these filters."

msgid "Aún no tienes sesiones. Cuando termines tu primera lección en Claude, aparecerá aquí."
msgstr "You have no sessions yet. When you finish your first lesson in Claude, it will appear here."

msgid "Cómo conectar"
msgstr "How to connect"

msgid "Sesión"
msgstr "Session"

msgid "Todas las sesiones"
msgstr "All sessions"

msgid "Sesión incompleta: no cuenta para tus tendencias."
msgstr "Incomplete session: it doesn't count towards your trends."

msgid "Evidencia poco confiable: guardamos tus palabras, pero no los errores de esta sesión."
msgstr "Unreliable evidence: we kept your words but not this session's mistakes."

msgid "Correcciones"
msgstr "Corrections"

msgid "dijiste"
msgstr "you said"

msgid "mejor"
msgstr "better"

msgid "La usaste después en la sesión"
msgstr "You used it later in the session"

msgid "Sin correcciones en esta sesión."
msgstr "No corrections in this session."

msgid "Tarea"
msgstr "Task"

msgid "Pistas"
msgstr "Hints"

msgid "Palabras por minuto"
msgstr "Words per minute"

msgid "Tu confianza"
msgstr "Your confidence"

msgid "%(n)s de 5"
msgstr "%(n)s of 5"

msgid "Frases"
msgstr "Phrases"

msgid "Esta sesión no tuvo frases propuestas."
msgstr "This session had no suggested phrases."

msgid "Nivel estimado"
msgstr "Estimated level"

msgid "Es la opinión del modelo, no una certificación."
msgstr "It's the model's opinion, not a certification."

msgid "Lo basó en:"
msgstr "Based on:"

msgid "Lo que dijiste"
msgstr "What you said"

msgid "Gramática"
msgstr "Grammar"

msgid "Vocabulario"
msgstr "Vocabulary"

msgid "Orden de palabras"
msgstr "Word order"

msgid "Registro"
msgstr "Register"

msgid "Otro"
msgstr "Other"

msgid "confianza alta"
msgstr "high confidence"

msgid "confianza media"
msgstr "medium confidence"

msgid "confianza baja"
msgstr "low confidence"
```

- [ ] **Step 7: Add the page CSS**

```css
/* Sesiones (Task 22) */
@media (max-width: 599px) {
  .table--stack thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  .table--stack, .table--stack tbody, .table--stack tr, .table--stack td, .table--stack th { display: block; width: 100%; }
  .table--stack tr { padding: .75rem 0; border-bottom: 1px solid var(--paper); }
  .table--stack [data-label]::before { content: attr(data-label); display: block; font-size: .75rem; font-weight: 600; opacity: .75; }
}
.pager { display: flex; justify-content: space-between; gap: .5rem; margin-top: .75rem; }
.errors { display: grid; gap: .75rem; margin: 0; padding: 0; list-style: none; }
.errors__item { padding: .75rem; border-radius: .75rem; background: var(--paper); }
.errors__item p { margin: .15rem 0; overflow-wrap: anywhere; }
.errors__tag { display: inline-block; min-width: 4.5rem; font-size: .75rem; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
.facts { display: grid; grid-template-columns: auto 1fr; gap: .35rem 1rem; margin: 0; }
.facts dt { font-weight: 600; }
.facts dd { margin: 0; }
.turns, .evidence { display: grid; gap: .5rem; padding-left: 1.25rem; }
.turns li, .evidence li { overflow-wrap: anywhere; }
.cefr strong { font-family: "Baloo 2", system-ui, sans-serif; font-size: 1.5rem; }
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/unit/web -q` then `uv run just lint`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/tutor/web tests/unit/web
git commit -m "feat(web): Sesiones list with filters and session detail"
```

### Task 23: Glosario (filters, inline edit, CSV)

**Files:**
- Create: `src/tutor/web/routes/glossary.py`
- Create: `src/tutor/web/templates/pages/glossary.html`
- Create: `src/tutor/web/templates/pages/glossary_edit.html`
- Create: `src/tutor/web/templates/partials/glossary_table.html`
- Create: `src/tutor/web/templates/partials/glossary_row.html`
- Create: `src/tutor/web/templates/partials/glossary_edit.html`
- Create: `src/tutor/web/templates/partials/glossary_saved.html`
- Modify: `src/tutor/web/templates/macros/labels.html` (append macros)
- Modify: `src/tutor/web/routes/__init__.py` (append `glossary.router`)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `src/tutor/web/static/css/app.css`
- Test: `tests/unit/web/test_web_glossary.py`

**Interfaces:**
- Consumes: `clean_user_text`, `TextError`, `MEANING_MAX`, `CONTEXT_MAX`, `glossary_csv` (Task 5); `GlossaryFilter`, `GlossaryRow`, `GlossaryKind`, `GlossaryStatus`, `DueFilter` (Task 1); `DashboardReader.glossary`, `DashboardReader.glossary_domains`, `GlossaryEditor.update_glossary_text` (Task 7); `enum_or_none`, `parse_uuid` (Task 20); `table--stack` (Task 22); `csrf_of` (Task 10).
- Produces:
  - Routes `GET /app/glossary` (query `kind`, `domain`, `status`, `due`, `q`; HTMX returns `partials/glossary_table.html`, root `#glossary-table`), `GET /app/glossary.csv` (same filters), `GET /app/glossary/{item_id}/edit`, `GET /app/glossary/{item_id}/row`, `POST /app/glossary/{item_id}` (form `meaning`, `context_sentence`; 200 row + out-of-band `#live` "Guardado" for HTMX, 303 to `/app/glossary` otherwise, 422 with field errors, 404 for unknown or other users' items).
  - `tutor.web.routes.glossary`: `glossary_filter(params: QueryParams) -> GlossaryFilter`, `filter_query(f: GlossaryFilter) -> str`.
  - Macros appended to `labels`: `kind_label(kind)`, `domain_label(domain)`, `glossary_status_label(status)`, `glossary_chips(row, today)`.
  - The edit form carries `data-esc-cancel` and its cancel link `data-cancel`; Task 15 should make Escape inside `form[data-esc-cancel]` click the `[data-cancel]` link (htmx trigger filters need eval, which is disabled).

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_glossary.py`:

```python
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import GlossaryRow, Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

from .conftest import csrf_of

HX = {"hx-request": "true"}
Login = Callable[[UUID], TestClient]


def row_of(backend: MemoryBackend, user_id: UUID, text: str) -> GlossaryRow:
    return next(r for r in backend.glossaries[user_id] if r.text == text)


def test_page_lists_items_labels_and_free_counter(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/glossary").text
    assert "7 de 50 elementos" in html
    assert html.count('<tr id="g-') == 7
    assert "Por confirmar hasta el" in html  # provisional item
    assert "Te cuesta" in html  # leech
    assert "En pausa" in html  # archived
    assert "Toca repasar" in html  # due today


def test_annual_has_no_counter(login: Login, demo: DemoUsers) -> None:
    assert "de 50 elementos" not in login(demo.beto).get("/app/glossary").text


def test_filters_return_the_table_partial_with_matching_export_link(
    login: Login, demo: DemoUsers
) -> None:
    response = login(demo.ana).get("/app/glossary?kind=term", headers=HX)
    assert "<html" not in response.text and 'id="glossary-table"' in response.text
    assert "trade-off" in response.text and "blocker" in response.text
    assert "on track" not in response.text
    assert 'href="/app/glossary.csv?kind=term"' in response.text


def test_search_ignores_accents(login: Login, demo: DemoUsers) -> None:
    response = login(demo.ana).get("/app/glossary?q=rapido", headers=HX)
    assert "a quick heads-up" in response.text and "trade-off" not in response.text


def test_edit_form_partial(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    response = login(demo.ana).get(f"/app/glossary/{row.id}/edit", headers=HX)
    assert response.status_code == 200 and "<html" not in response.text
    assert 'name="meaning"' in response.text and 'value="compromiso"' in response.text
    assert "data-esc-cancel" in response.text


def test_save_updates_the_row_and_announces(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    c = login(demo.ana)
    response = c.post(
        f"/app/glossary/{row.id}",
        data={
            "csrf_token": csrf_of(c),
            "meaning": "  concesión ",
            "context_sentence": "The trade-off is cost.",
        },
        headers=HX,
    )
    assert response.status_code == 200
    assert "concesión" in response.text
    assert 'hx-swap-oob="true"' in response.text and "Guardado" in response.text
    assert row_of(backend, demo.ana, "trade-off").meaning == "concesión"


def test_plain_form_post_redirects(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    c = login(demo.ana)
    response = c.post(
        f"/app/glossary/{row.id}",
        data={"csrf_token": csrf_of(c), "meaning": "x", "context_sentence": "Fine."},
    )
    assert response.status_code == 303 and response.headers["location"] == "/app/glossary"


def test_invalid_edit_is_422_and_changes_nothing(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    c = login(demo.ana)
    response = c.post(
        f"/app/glossary/{row.id}",
        data={"csrf_token": csrf_of(c), "meaning": "x" * 201, "context_sentence": "   "},
        headers=HX,
    )
    assert response.status_code == 422
    assert "Máximo 200 caracteres." in response.text
    assert "Escribe la oración donde apareció la frase." in response.text
    assert 'aria-invalid="true"' in response.text
    assert row_of(backend, demo.ana, "trade-off").meaning == "compromiso"


def test_save_without_csrf_is_403(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    response = login(demo.ana).post(
        f"/app/glossary/{row.id}", data={"meaning": "x", "context_sentence": "y"}
    )
    assert response.status_code == 403


def test_other_users_items_are_404(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    beto_row = backend.glossaries[demo.beto][0]
    c = login(demo.ana)
    assert c.get(f"/app/glossary/{beto_row.id}/edit", headers=HX).status_code == 404
    assert c.get(f"/app/glossary/{beto_row.id}/row", headers=HX).status_code == 404
    response = c.post(
        f"/app/glossary/{beto_row.id}",
        data={"csrf_token": csrf_of(c), "meaning": "x", "context_sentence": "y"},
    )
    assert response.status_code == 404
    assert backend.glossaries[demo.beto][0].meaning == beto_row.meaning
    assert c.get("/app/glossary/zzz/edit").status_code == 404


def test_html_in_an_edit_stays_text(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    row = row_of(backend, demo.ana, "trade-off")
    c = login(demo.ana)
    payload = "<img src=x onerror=alert(1)>"
    response = c.post(
        f"/app/glossary/{row.id}",
        data={"csrf_token": csrf_of(c), "meaning": payload, "context_sentence": "Fine."},
        headers=HX,
    )
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text
    assert "<img src=x" not in response.text
    page = c.get("/app/glossary").text
    assert "&lt;img src=x onerror=alert(1)&gt;" in page and "<img src=x" not in page


def test_csv_export_is_a_filtered_attachment(login: Login, demo: DemoUsers) -> None:
    response = login(demo.ana).get("/app/glossary.csv?kind=term")
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="glosario.csv"'
    assert response.text.startswith("﻿")
    assert len(response.text.strip().splitlines()) == 3  # header + trade-off + blocker


def test_empty_glossary(login: Login, demo: DemoUsers) -> None:
    assert "Tu glosario está vacío" in login(demo.nuevo).get("/app/glossary").text


def test_glossary_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    assert "Search" in login(demo.ana).get("/app/glossary").text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/web/test_web_glossary.py -q`
Expected: FAIL (the routes return 404).

- [ ] **Step 3: Implement the routes**

`src/tutor/web/routes/glossary.py`:

```python
"""Glosario: filters, inline edit of meaning and context, CSV export (section 10)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import QueryParams

from tutor.domain.dashboard.glossary import (
    CONTEXT_MAX,
    MEANING_MAX,
    TextError,
    clean_user_text,
    glossary_csv,
)
from tutor.domain.dashboard.types import (
    DueFilter,
    GlossaryFilter,
    GlossaryKind,
    GlossaryRow,
    GlossaryStatus,
    User,
)
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.query import enum_or_none, parse_uuid
from tutor.web.views import is_htmx, render, today_for

router = APIRouter(dependencies=APP_ROUTER_DEPS)
Q_MAX = 100


def glossary_filter(params: QueryParams) -> GlossaryFilter:
    return GlossaryFilter(
        kind=enum_or_none(GlossaryKind, params.get("kind")),
        domain=params.get("domain") or None,
        status=enum_or_none(GlossaryStatus, params.get("status")),
        due=enum_or_none(DueFilter, params.get("due")),
        q=(params.get("q") or "")[:Q_MAX],
    )


def filter_query(f: GlossaryFilter) -> str:
    pairs = (
        ("kind", f.kind),
        ("domain", f.domain),
        ("status", f.status),
        ("due", f.due),
        ("q", f.q),
    )
    return urlencode([(k, str(v)) for k, v in pairs if v])


def _find(request: Request, user: User, item_id: str) -> GlossaryRow:
    uid = parse_uuid(item_id)
    if uid is not None:
        rows = get_deps(request).reader.glossary(user.id, GlossaryFilter(), today_for(request))
        for row in rows:
            if row.id == uid:
                return row
    raise HTTPException(status_code=404)


def _edit_ctx(
    row: GlossaryRow, meaning: str, context: str, errors: dict[str, str]
) -> dict[str, Any]:
    return {
        "row": row,
        "values": {"meaning": meaning, "context_sentence": context},
        "errors": errors,
        "meaning_max": MEANING_MAX,
        "context_max": CONTEXT_MAX,
    }


@router.get("/app/glossary", response_class=HTMLResponse)
async def glossary_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    deps = get_deps(request)
    f = glossary_filter(request.query_params)
    query = filter_query(f)
    ctx = {
        "rows": deps.reader.glossary(user.id, f, today_for(request)),
        "f": f,
        "domains": deps.reader.glossary_domains(user.id),
        "csv_url": "/app/glossary.csv" + (f"?{query}" if query else ""),
    }
    template = "partials/glossary_table.html" if is_htmx(request) else "pages/glossary.html"
    return render(request, template, ctx)


@router.get("/app/glossary.csv")
async def glossary_export(request: Request, user: User = Depends(current_user)) -> Response:
    rows = get_deps(request).reader.glossary(
        user.id, glossary_filter(request.query_params), today_for(request)
    )
    return Response(
        glossary_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="glosario.csv"'},
    )


@router.get("/app/glossary/{item_id}/edit", response_class=HTMLResponse)
async def glossary_edit_form(
    item_id: str, request: Request, user: User = Depends(current_user)
) -> HTMLResponse:
    row = _find(request, user, item_id)
    template = "partials/glossary_edit.html" if is_htmx(request) else "pages/glossary_edit.html"
    return render(request, template, _edit_ctx(row, row.meaning, row.context_sentence, {}))


@router.get("/app/glossary/{item_id}/row")
async def glossary_row(
    item_id: str, request: Request, user: User = Depends(current_user)
) -> Response:
    row = _find(request, user, item_id)
    if not is_htmx(request):
        return RedirectResponse("/app/glossary", status_code=303)
    return render(request, "partials/glossary_row.html", {"row": row})


@router.post("/app/glossary/{item_id}")
async def glossary_save(
    item_id: str,
    request: Request,
    user: User = Depends(current_user),
    meaning: str = Form(""),
    context_sentence: str = Form(""),
) -> Response:
    row = _find(request, user, item_id)  # ownership first: 404 before any validation detail
    errors: dict[str, str] = {}
    clean_meaning = clean_context = ""
    try:
        clean_meaning = clean_user_text(meaning, max_len=MEANING_MAX, required=False)
    except TextError as exc:
        errors["meaning"] = exc.code
    try:
        clean_context = clean_user_text(context_sentence, max_len=CONTEXT_MAX, required=True)
    except TextError as exc:
        errors["context_sentence"] = exc.code
    if errors:
        template = "partials/glossary_edit.html" if is_htmx(request) else "pages/glossary_edit.html"
        ctx = _edit_ctx(row, meaning, context_sentence, errors)
        return render(request, template, ctx, status_code=422)
    updated = get_deps(request).glossary.update_glossary_text(
        user.id, row.id, clean_meaning, clean_context
    )
    if updated is None:
        raise HTTPException(status_code=404)
    if not is_htmx(request):
        return RedirectResponse("/app/glossary", status_code=303)
    return render(request, "partials/glossary_saved.html", {"row": updated})
```

`/app/glossary.csv` is declared before `/app/glossary/{item_id}/…`; the paths do not overlap, but keep that order for readability. In `src/tutor/web/routes/__init__.py`, add `glossary` to the import line inside `all_routers` and append `glossary.router` to `routers`.

- [ ] **Step 4: Append the macros**

Append to `src/tutor/web/templates/macros/labels.html`:

```html

{% macro kind_label(kind) %}{% if kind == "correction" %}{{ _("Corrección") }}{% elif kind == "chunk" %}{{ _("Frase") }}{% else %}{{ _("Término") }}{% endif %}{% endmacro %}

{% macro domain_label(domain) %}{% if domain == "it" %}{{ _("TI") }}{% elif domain == "daily" %}{{ _("Día a día") }}{% elif domain == "business" %}{{ _("Negocios") }}{% else %}{{ domain }}{% endif %}{% endmacro %}

{% macro glossary_status_label(status) %}{% if status == "provisional" %}{{ _("Por confirmar") }}{% elif status == "archived" %}{{ _("En pausa") }}{% else %}{{ _("Confirmada") }}{% endif %}{% endmacro %}

{% macro glossary_chips(row, today) %}{% if row.status == "provisional" %}<span class="chip chip--warn">{% if row.expires_on %}{{ _("Por confirmar hasta el %(when)s", when=row.expires_on|day("short")) }}{% else %}{{ _("Por confirmar") }}{% endif %}</span>{% elif row.status == "archived" %}<span class="chip chip--muted">{{ _("En pausa") }}</span>{% else %}{% set due = row.due_on and row.due_on <= today %}{% if due %}<span class="chip chip--ok">{{ _("Toca repasar") }}</span>{% endif %}{% if row.leech %} <span class="chip chip--warn">{{ _("Te cuesta") }}</span>{% endif %}{% if not due and not row.leech %}<span class="chip chip--muted">{{ _("Confirmada") }}</span>{% endif %}{% endif %}{% endmacro %}
```

- [ ] **Step 5: Write the templates**

`src/tutor/web/templates/pages/glossary.html`:

```html
{% extends "layouts/app.html" %}
{% import "macros/labels.html" as labels %}
{% set active_nav = "glossary" %}
{% block title %}{{ _("Glosario") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head">
  <h1>{{ _("Glosario") }}</h1>
  {% if usage %}<p class="muted">{% trans used=usage.glossary_items, cap=usage.glossary_cap %}{{ used }} de {{ cap }} elementos{% endtrans %}</p>{% endif %}
</header>
<p class="muted">{{ _("Tus repasos ocurren dentro de las lecciones; aquí puedes revisar y corregir tus frases.") }}</p>
<form class="filters" method="get" action="/app/glossary" role="search" hx-get="/app/glossary" hx-target="#glossary-table" hx-swap="outerHTML" hx-push-url="true" hx-trigger="change, input changed delay:300ms from:#glossary-q">
  <label class="form-row">{{ _("Buscar") }}
    <input id="glossary-q" type="search" name="q" value="{{ f.q }}" maxlength="100" autocomplete="off">
  </label>
  <label class="form-row">{{ _("Tipo") }}
    <select name="kind">
      <option value="">{{ _("Todos") }}</option>
      {% for value in ("correction", "chunk", "term") %}<option value="{{ value }}"{% if f.kind == value %} selected{% endif %}>{{ labels.kind_label(value) }}</option>{% endfor %}
    </select>
  </label>
  <label class="form-row">{{ _("Tema") }}
    <select name="domain">
      <option value="">{{ _("Todos") }}</option>
      {% for d in domains %}<option value="{{ d }}"{% if f.domain == d %} selected{% endif %}>{{ labels.domain_label(d) }}</option>{% endfor %}
    </select>
  </label>
  <label class="form-row">{{ _("Estado") }}
    <select name="status">
      <option value="">{{ _("Todos") }}</option>
      {% for value in ("provisional", "confirmed", "archived") %}<option value="{{ value }}"{% if f.status == value %} selected{% endif %}>{{ labels.glossary_status_label(value) }}</option>{% endfor %}
    </select>
  </label>
  <label class="form-row">{{ _("Repaso") }}
    <select name="due">
      <option value="">{{ _("Cualquier fecha") }}</option>
      <option value="today"{% if f.due == "today" %} selected{% endif %}>{{ _("Para hoy") }}</option>
      <option value="week"{% if f.due == "week" %} selected{% endif %}>{{ _("Esta semana") }}</option>
    </select>
  </label>
  <button class="btn btn--ghost btn--small">{{ _("Filtrar") }}</button>
</form>
{% include "partials/glossary_table.html" %}
{% endblock %}
```

`src/tutor/web/templates/partials/glossary_table.html`:

```html
<div id="glossary-table">
  <p class="table-meta">
    <span>{% trans n=rows|length %}Resultados: {{ n }}{% endtrans %}</span>
    <a class="btn btn--ghost btn--small" href="{{ csv_url }}" download>{{ _("Exportar CSV") }}</a>
  </p>
  {% if rows %}
  <div class="table-wrap">
    <table class="table table--stack table--glossary">
      <caption class="sr-only">{{ _("Tu glosario") }}</caption>
      <thead>
        <tr>
          <th scope="col">{{ _("Frase") }}</th>
          <th scope="col">{{ _("Tipo") }}</th>
          <th scope="col">{{ _("Significado") }}</th>
          <th scope="col">{{ _("Contexto") }}</th>
          <th scope="col">{{ _("Tema") }}</th>
          <th scope="col">{{ _("Estado") }}</th>
          <th scope="col"><span class="sr-only">{{ _("Acciones") }}</span></th>
        </tr>
      </thead>
      <tbody>
        {% for row in rows %}{% include "partials/glossary_row.html" %}{% endfor %}
      </tbody>
    </table>
  </div>
  {% elif f.kind or f.domain or f.status or f.due or f.q %}
  <p class="empty">{{ _("Nada coincide con estos filtros.") }}</p>
  {% else %}
  <p class="empty">{{ _("Tu glosario está vacío. Las frases que confirmes en tus lecciones aparecerán aquí.") }}</p>
  {% endif %}
</div>
```

`src/tutor/web/templates/partials/glossary_row.html`:

```html
{% import "macros/labels.html" as labels %}
<tr id="g-{{ row.id }}">
  <th scope="row" data-label="{{ _('Frase') }}" lang="en">{{ row.text }}</th>
  <td data-label="{{ _('Tipo') }}">{{ labels.kind_label(row.kind) }}</td>
  <td data-label="{{ _('Significado') }}">{{ row.meaning or "—" }}</td>
  <td data-label="{{ _('Contexto') }}" lang="en">{{ row.context_sentence }}</td>
  <td data-label="{{ _('Tema') }}">{{ labels.domain_label(row.domain) }}</td>
  <td data-label="{{ _('Estado') }}">{{ labels.glossary_chips(row, today) }}</td>
  <td><a class="btn btn--ghost btn--small" href="/app/glossary/{{ row.id }}/edit" hx-get="/app/glossary/{{ row.id }}/edit" hx-target="#g-{{ row.id }}" hx-swap="outerHTML">{{ _("Editar") }}<span class="sr-only"> {{ row.text }}</span></a></td>
</tr>
```

`src/tutor/web/templates/partials/glossary_edit.html`:

```html
<tr id="g-{{ row.id }}" class="row--editing">
  <td colspan="7">
    <form class="edit-form" method="post" action="/app/glossary/{{ row.id }}" hx-post="/app/glossary/{{ row.id }}" hx-target="#g-{{ row.id }}" hx-swap="outerHTML" data-esc-cancel>
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <p class="edit-form__text"><strong lang="en">{{ row.text }}</strong></p>
      <div class="form-row">
        <label for="meaning-{{ row.id }}">{{ _("Significado") }}</label>
        <input id="meaning-{{ row.id }}" name="meaning" value="{{ values.meaning }}" maxlength="{{ meaning_max }}"{% if errors.get("meaning") %} aria-invalid="true" aria-describedby="meaning-error-{{ row.id }}"{% endif %}>
        {% if errors.get("meaning") %}<p class="field-error" id="meaning-error-{{ row.id }}">{% trans n=meaning_max %}Máximo {{ n }} caracteres.{% endtrans %}</p>{% endif %}
      </div>
      <div class="form-row">
        <label for="context-{{ row.id }}">{{ _("Oración de contexto") }}</label>
        <textarea id="context-{{ row.id }}" name="context_sentence" rows="2" maxlength="{{ context_max }}" lang="en" required{% if errors.get("context_sentence") %} aria-invalid="true" aria-describedby="context-error-{{ row.id }}"{% endif %}>{{ values.context_sentence }}</textarea>
        {% if errors.get("context_sentence") == "empty" %}<p class="field-error" id="context-error-{{ row.id }}">{{ _("Escribe la oración donde apareció la frase.") }}</p>
        {% elif errors.get("context_sentence") == "too_long" %}<p class="field-error" id="context-error-{{ row.id }}">{% trans n=context_max %}Máximo {{ n }} caracteres.{% endtrans %}</p>{% endif %}
      </div>
      <div class="edit-form__actions">
        <button class="btn btn--primary btn--small">{{ _("Guardar") }}</button>
        <a class="btn btn--ghost btn--small" href="/app/glossary" hx-get="/app/glossary/{{ row.id }}/row" hx-target="#g-{{ row.id }}" hx-swap="outerHTML" data-cancel>{{ _("Cancelar") }}</a>
      </div>
    </form>
  </td>
</tr>
```

`src/tutor/web/templates/partials/glossary_saved.html` (the row plus an out-of-band update of the page's polite live region from `base.html`):

```html
{% include "partials/glossary_row.html" %}
<div id="live" class="sr-only" aria-live="polite" hx-swap-oob="true">{{ _("Guardado") }}</div>
```

`src/tutor/web/templates/pages/glossary_edit.html` (used without JavaScript):

```html
{% extends "layouts/app.html" %}
{% set active_nav = "glossary" %}
{% block title %}{{ _("Editar frase") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head"><h1>{{ _("Editar frase") }}</h1></header>
<div class="table-wrap">
  <table class="table table--glossary"><tbody>{% include "partials/glossary_edit.html" %}</tbody></table>
</div>
{% endblock %}
```

- [ ] **Step 6: Add the English translations**

```po
msgid "%(used)s de %(cap)s elementos"
msgstr "%(used)s of %(cap)s items"

msgid "Tus repasos ocurren dentro de las lecciones; aquí puedes revisar y corregir tus frases."
msgstr "Your reviews happen inside lessons; here you can check and correct your phrases."

msgid "Buscar"
msgstr "Search"

msgid "Tipo"
msgstr "Type"

msgid "Tema"
msgstr "Topic"

msgid "Repaso"
msgstr "Review"

msgid "Cualquier fecha"
msgstr "Any date"

msgid "Para hoy"
msgstr "Due today"

msgid "Esta semana"
msgstr "This week"

msgid "Resultados: %(n)s"
msgstr "Results: %(n)s"

msgid "Exportar CSV"
msgstr "Export CSV"

msgid "Tu glosario"
msgstr "Your glossary"

msgid "Frase"
msgstr "Phrase"

msgid "Significado"
msgstr "Meaning"

msgid "Contexto"
msgstr "Context"

msgid "Acciones"
msgstr "Actions"

msgid "Nada coincide con estos filtros."
msgstr "Nothing matches these filters."

msgid "Tu glosario está vacío. Las frases que confirmes en tus lecciones aparecerán aquí."
msgstr "Your glossary is empty. Phrases you confirm in your lessons will appear here."

msgid "Editar"
msgstr "Edit"

msgid "Oración de contexto"
msgstr "Context sentence"

msgid "Máximo %(n)s caracteres."
msgstr "At most %(n)s characters."

msgid "Escribe la oración donde apareció la frase."
msgstr "Write the sentence where the phrase appeared."

msgid "Guardar"
msgstr "Save"

msgid "Cancelar"
msgstr "Cancel"

msgid "Guardado"
msgstr "Saved"

msgid "Editar frase"
msgstr "Edit phrase"

msgid "Corrección"
msgstr "Correction"

msgid "Término"
msgstr "Term"

msgid "TI"
msgstr "IT"

msgid "Día a día"
msgstr "Everyday"

msgid "Negocios"
msgstr "Business"

msgid "Por confirmar"
msgstr "To confirm"

msgid "En pausa"
msgstr "Parked"

msgid "Confirmada"
msgstr "Confirmed"

msgid "Por confirmar hasta el %(when)s"
msgstr "To confirm by %(when)s"

msgid "Toca repasar"
msgstr "Review due"

msgid "Te cuesta"
msgstr "Tricky for you"
```

- [ ] **Step 7: Add the page CSS**

```css
/* Glosario (Task 23) */
.table-meta { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .5rem; margin: .75rem 0; }
.table--glossary td, .table--glossary th { overflow-wrap: anywhere; vertical-align: top; }
.row--editing td { background: var(--paper); }
.edit-form { display: grid; gap: .75rem; }
.edit-form__text { margin: 0; }
.edit-form input, .edit-form textarea { width: 100%; min-height: 2.75rem; font: inherit; }
.edit-form__actions { display: flex; flex-wrap: wrap; gap: .5rem; }
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/unit/web -q` then `uv run just lint`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/tutor/web tests/unit/web
git commit -m "feat(web): Glosario with filters, inline edit and CSV export"
```

### Task 24: Plan (read-only, with version history)

**Files:**
- Create: `src/tutor/web/routes/plan.py`
- Create: `src/tutor/web/templates/pages/plan.html`
- Modify: `src/tutor/web/templates/macros/labels.html` (append macros)
- Modify: `src/tutor/web/routes/__init__.py` (append `plan.router`)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `src/tutor/web/static/css/app.css`
- Test: `tests/unit/web/test_web_plan.py`

**Interfaces:**
- Consumes: `diff_plan`, `PlanDiff` (Task 4); `PlanPage`, `PlanVersion`, `PlanItemView` (Task 1); `DashboardReader.plan` (Task 7); `labels.domain_label` (Task 23).
- Produces:
  - Route `GET /app/plan`.
  - `tutor.web.routes.plan`: `WEEKS_SHOWN = 4`; frozen dataclasses `WeekView(number: int, is_current: bool, items: tuple[PlanItemView, ...])` and `HistoryEntry(version: PlanVersion, diff: PlanDiff | None)` (`None` for the first version); `plan_weeks(plan: PlanPage) -> list[WeekView]`; `plan_history(plan: PlanPage) -> list[HistoryEntry]` (newest first).
  - Macros appended to `labels`: `skill_label(skill)`, `item_status_chip(status)`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_plan.py`:

```python
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend
from tutor.web.routes.plan import plan_history, plan_weeks

Login = Callable[[UUID], TestClient]


def test_plan_shows_weeks_rationale_and_history(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/plan").text
    assert "Tu diagnóstico mostró" in html
    assert "Semana 1" in html and "Semana 4" in html
    assert "esta semana" in html
    assert 'chip chip--ok">Hecho<' in html
    assert "Nuevo: Presentar una demo al cliente (semana 2)" in html
    assert "Movido: Explicar un bloqueo técnico (semana 1 → semana 2)" in html
    assert "Plan inicial." in html
    assert 'data-copy="regenerate my plan"' in html


def test_realism_message_is_shown(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.plans[demo.ana] = replace(
        backend.plans[demo.ana],
        realism_message="B2 en junio no es realista; proponemos B1+ con conversaciones seguras.",
    )
    assert "no es realista" in login(demo.ana).get("/app/plan").text


def test_no_plan_yet(login: Login, demo: DemoUsers) -> None:
    html = login(demo.nuevo).get("/app/plan").text
    assert "Tu plan aparecerá después de tu diagnóstico" in html


def test_plan_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    assert "Week 1" in login(demo.ana).get("/app/plan").text


def test_weeks_and_history_helpers(backend: MemoryBackend, demo: DemoUsers) -> None:
    plan = backend.plans[demo.ana]
    weeks = plan_weeks(plan)
    assert [w.number for w in weeks] == [1, 2, 3, 4]
    assert weeks[0].is_current and not weeks[1].is_current
    assert [i.order_no for i in weeks[0].items] == [1, 2]
    assert weeks[2].items == ()
    history = plan_history(plan)
    assert [h.version.version for h in history] == [2, 1]
    assert history[0].diff is not None and history[1].diff is None
    single = replace(plan, versions=plan.versions[1:])
    assert len(plan_history(single)) == 1 and plan_history(single)[0].diff is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/web/test_web_plan.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.web.routes.plan'`.

- [ ] **Step 3: Implement the route**

`src/tutor/web/routes/plan.py`:

```python
"""Plan: the next four weeks and how the plan changed (requirements section 9). Read-only;
changes happen in chat through `update_plan`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from tutor.domain.dashboard.plan_diff import PlanDiff, diff_plan
from tutor.domain.dashboard.types import PlanItemView, PlanPage, PlanVersion, User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.views import render

router = APIRouter(dependencies=APP_ROUTER_DEPS)
WEEKS_SHOWN = 4


@dataclass(frozen=True)
class WeekView:
    number: int
    is_current: bool
    items: tuple[PlanItemView, ...]


@dataclass(frozen=True)
class HistoryEntry:
    version: PlanVersion
    diff: PlanDiff | None  # None for the first version


def plan_weeks(plan: PlanPage) -> list[WeekView]:
    items = plan.versions[0].items
    first = plan.current_week_no
    return [
        WeekView(
            number,
            number == first,
            tuple(sorted((i for i in items if i.week_no == number), key=lambda i: i.order_no)),
        )
        for number in range(first, first + WEEKS_SHOWN)
    ]


def plan_history(plan: PlanPage) -> list[HistoryEntry]:
    versions = plan.versions
    entries = [
        HistoryEntry(newer, diff_plan(older.items, newer.items))
        for newer, older in zip(versions, versions[1:], strict=False)
    ]
    entries.append(HistoryEntry(versions[-1], None))
    return entries


@router.get("/app/plan", response_class=HTMLResponse)
async def plan_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    plan = get_deps(request).reader.plan(user.id)
    ctx: dict[str, Any] = {"plan": plan, "weeks": [], "history": []}
    if plan is not None:
        ctx["weeks"] = plan_weeks(plan)
        ctx["history"] = plan_history(plan)
    return render(request, "pages/plan.html", ctx)
```

In `src/tutor/web/routes/__init__.py`, add `plan` to the import line inside `all_routers` and append `plan.router` to `routers`.

- [ ] **Step 4: Append the macros**

Append to `src/tutor/web/templates/macros/labels.html`:

```html

{% macro skill_label(skill) %}{% if skill == "speaking" %}{{ _("Hablar") }}{% elif skill == "writing" %}{{ _("Escribir") }}{% else %}{{ _("Escuchar") }}{% endif %}{% endmacro %}

{% macro item_status_chip(status) %}{% if status == "done" %}<span class="chip chip--ok">{{ _("Hecho") }}</span>{% elif status == "skipped" %}<span class="chip chip--muted">{{ _("Saltado") }}</span>{% else %}<span class="chip chip--muted">{{ _("Pendiente") }}</span>{% endif %}{% endmacro %}
```

- [ ] **Step 5: Write the template**

`src/tutor/web/templates/pages/plan.html`:

```html
{% extends "layouts/app.html" %}
{% import "macros/labels.html" as labels %}
{% set active_nav = "plan" %}
{% block title %}{{ _("Plan") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head"><h1>{{ _("Tu plan") }}</h1></header>
{% if plan is none %}
<section class="card card--narrow">
  <p class="empty">{{ _("Tu plan aparecerá después de tu diagnóstico. Para hacerlo, di en el chat:") }}</p>
  <div class="code-copy">
    <code lang="en">start my lesson</code>
    <button type="button" class="btn btn--ghost btn--small copy" data-copy="start my lesson">{{ _("Copiar") }}</button>
  </div>
</section>
{% else %}
{% if plan.realism_message %}<div class="banner banner--info" role="note"><p>{{ plan.realism_message }}</p></div>{% endif %}
<section class="card card--wide" aria-labelledby="why-title">
  <h2 id="why-title" class="card__title">{{ _("Por qué este plan") }}</h2>
  <p>{{ plan.rationale }}</p>
</section>

<div class="plan-weeks">
  {% for week in weeks %}
  <section class="card plan-week{% if week.is_current %} plan-week--current{% endif %}" aria-labelledby="week-{{ week.number }}">
    <h2 id="week-{{ week.number }}" class="card__title">{% trans n=week.number %}Semana {{ n }}{% endtrans %}{% if week.is_current %} <span class="chip chip--ok">{{ _("esta semana") }}</span>{% endif %}</h2>
    {% if week.items %}
    <ol class="plan-items">
      {% for item in week.items %}
      <li class="plan-item">
        <p class="plan-item__title">{{ item.title }}</p>
        <p class="muted">{{ labels.skill_label(item.skill) }} · {{ labels.domain_label(item.domain) }} · {{ item.scenario_hint }}</p>
        {{ labels.item_status_chip(item.status) }}
      </li>
      {% endfor %}
    </ol>
    {% else %}
    <p class="empty">{{ _("Sin práctica planeada.") }}</p>
    {% endif %}
  </section>
  {% endfor %}
</div>

<section class="card card--wide" aria-labelledby="chat-title">
  <h2 id="chat-title" class="card__title">{{ _("Cambia tu plan desde el chat") }}</h2>
  <p>{{ _("Dile a tu tutor lo que necesitas. Por ejemplo:") }}</p>
  <ul class="phrases">
    {% for phrase in ("skip today's item", "add interview prep next week", "regenerate my plan") %}
    <li class="code-copy">
      <code lang="en">{{ phrase }}</code>
      <button type="button" class="btn btn--ghost btn--small copy" data-copy="{{ phrase }}">{{ _("Copiar") }}</button>
    </li>
    {% endfor %}
  </ul>
</section>

<section class="card card--wide" aria-labelledby="history-title">
  <h2 id="history-title" class="card__title">{{ _("Historial de versiones") }}</h2>
  {% for entry in history %}
  <details class="plan-version"{% if loop.first %} open{% endif %}>
    <summary>{% trans v=entry.version.version, when=entry.version.generated_at|day("long") %}Versión {{ v }} · {{ when }}{% endtrans %}</summary>
    {% if entry.diff is none %}
    <p class="muted">{{ _("Plan inicial.") }}</p>
    {% elif entry.diff.empty %}
    <p class="muted">{{ _("Sin cambios en los temas.") }}</p>
    {% else %}
    <ul class="diff">
      {% for item in entry.diff.added %}<li class="diff--added">{% trans title=item.title, n=item.week_no %}Nuevo: {{ title }} (semana {{ n }}){% endtrans %}</li>{% endfor %}
      {% for item in entry.diff.removed %}<li class="diff--removed">{% trans title=item.title %}Quitado: {{ title }}{% endtrans %}</li>{% endfor %}
      {% for before, after in entry.diff.moved %}<li class="diff--moved">{% trans title=after.title, a=before.week_no, b=after.week_no %}Movido: {{ title }} (semana {{ a }} → semana {{ b }}){% endtrans %}</li>{% endfor %}
    </ul>
    {% endif %}
  </details>
  {% endfor %}
</section>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Add the English translations**

```po
msgid "Tu plan"
msgstr "Your plan"

msgid "Tu plan aparecerá después de tu diagnóstico. Para hacerlo, di en el chat:"
msgstr "Your plan will appear after your diagnostic. To take it, say in the chat:"

msgid "Por qué este plan"
msgstr "Why this plan"

msgid "Semana %(n)s"
msgstr "Week %(n)s"

msgid "esta semana"
msgstr "this week"

msgid "Sin práctica planeada."
msgstr "No practice planned."

msgid "Cambia tu plan desde el chat"
msgstr "Change your plan from the chat"

msgid "Dile a tu tutor lo que necesitas. Por ejemplo:"
msgstr "Tell your tutor what you need. For example:"

msgid "Historial de versiones"
msgstr "Version history"

msgid "Versión %(v)s · %(when)s"
msgstr "Version %(v)s · %(when)s"

msgid "Plan inicial."
msgstr "First plan."

msgid "Sin cambios en los temas."
msgstr "No changes to the topics."

msgid "Nuevo: %(title)s (semana %(n)s)"
msgstr "New: %(title)s (week %(n)s)"

msgid "Quitado: %(title)s"
msgstr "Removed: %(title)s"

msgid "Movido: %(title)s (semana %(a)s → semana %(b)s)"
msgstr "Moved: %(title)s (week %(a)s → week %(b)s)"

msgid "Hablar"
msgstr "Speaking"

msgid "Escribir"
msgstr "Writing"

msgid "Escuchar"
msgstr "Listening"

msgid "Saltado"
msgstr "Skipped"
```

- [ ] **Step 7: Add the page CSS**

```css
/* Plan (Task 24) */
.plan-weeks { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 1rem; margin: 1rem 0; }
.plan-week--current { outline: 3px solid var(--leaf); outline-offset: -3px; }
.plan-items { display: grid; gap: .75rem; margin: 0; padding: 0; list-style: none; }
.plan-item { padding-bottom: .75rem; border-bottom: 1px solid var(--paper); }
.plan-item:last-child { border-bottom: 0; }
.plan-item__title { margin: 0 0 .25rem; font-weight: 600; overflow-wrap: anywhere; }
.plan-version { padding: .5rem 0; border-bottom: 1px solid var(--paper); }
.plan-version summary { display: flex; align-items: center; min-height: 2.75rem; font-weight: 600; cursor: pointer; }
.phrases { display: grid; gap: .5rem; margin: 0; padding: 0; list-style: none; }
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/unit/web -q` then `uv run just lint`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/tutor/web tests/unit/web
git commit -m "feat(web): read-only Plan page with weeks and version diff"
```

### Task 25: Progreso

**Files:**
- Create: `src/tutor/domain/dashboard/progress.py`
- Create: `src/tutor/web/routes/progress.py`
- Create: `src/tutor/web/templates/macros/charts.html`
- Create: `src/tutor/web/templates/pages/progress.html`
- Modify: `src/tutor/web/views.py` (Jinja globals)
- Modify: `src/tutor/web/routes/__init__.py` (register the router)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `src/tutor/web/static/css/app.css` (append)
- Test: `tests/unit/domain/test_dashboard_progress.py`, `tests/unit/web/test_web_progress.py`

**Interfaces:**
- Consumes: `Series`, `ProgressData`, `Checkpoint` (Task 1); `line_chart`, `LineChart`, `cefr_label`, `meter_fraction` (Task 3); `DashboardReader.progress` (Task 7); `render`, `Views` (Task 10); `APP_ROUTER_DEPS`, `current_user`, `get_deps` (Task 11); fixtures `login`, `demo`, `backend` (Task 10).
- Produces:
  - `tutor.domain.dashboard.progress`: `TrendChart` (frozen: `chart: LineChart`, `excluded_dots: tuple[tuple[float, float], ...]`, `ticks: tuple[tuple[float, str], ...]`); `trend_chart(series: Series, excluded: Sequence[date] = (), *, cefr_ticks: bool = False, height: int = 120, pad: int = 8) -> TrendChart`; `latest_value(series: Series) -> float | None`.
  - Jinja globals in every language environment: `trend_chart`, `latest_value`, `meter_fraction`, `cefr_label`.
  - Macros in `templates/macros/charts.html` (import with `{% import "macros/charts.html" as charts %}`): `charts.line(series, label, unit, target_label="", fmt="decimal1", excluded=(), cefr=false, id="")`, `charts.kpi(value, fmt, label, target="", count=true)`, `charts.fmtv(value, fmt)`. `fmt` is one of `decimal0`, `decimal1`, `percent`, `integer`, `cefr`. Task 27 and any later page reuse them.
  - Route `GET /app/progress`. The page has no HTMX partial: nothing on it changes without a navigation.

Excluded CEFR points (jumps of a full level waiting for the next diagnostic, requirements section 11) are kept out of the line, which bridges over them, and drawn as hollow markers on the same scale.

- [ ] **Step 1: Write the failing domain tests**

`tests/unit/domain/test_dashboard_progress.py`:

```python
from datetime import date, timedelta

from tutor.domain.dashboard.charts import line_chart
from tutor.domain.dashboard.progress import latest_value, trend_chart
from tutor.domain.dashboard.types import Series

D0 = date(2027, 1, 1)


def days(n: int) -> tuple[date, ...]:
    return tuple(D0 + timedelta(days=i) for i in range(n))


def test_latest_value_skips_gaps() -> None:
    assert latest_value(Series(days(3), (1.0, 2.0, None))) == 2.0
    assert latest_value(Series(days(2), (None, None))) is None
    assert latest_value(Series((), ())) is None


def test_without_exclusions_matches_line_chart() -> None:
    series = Series(days(2), (10.0, 20.0), 25.0)
    trend = trend_chart(series)
    assert trend.chart == line_chart(series.y, target=25.0)
    assert trend.excluded_dots == () and trend.ticks == ()


def test_excluded_point_is_bridged_and_marked_on_the_same_scale() -> None:
    series = Series(days(3), (1.0, 5.0, 1.0))
    trend = trend_chart(series, excluded=(days(3)[1],))
    assert trend.chart.path == "M8 93.1 L312 93.1"
    assert trend.chart.dots == ((8, 93.1), (312, 93.1))
    assert trend.excluded_dots == ((160, 17.5),)


def test_gaps_still_lift_the_pen() -> None:
    trend = trend_chart(Series(days(3), (1.0, None, 1.0)))
    assert trend.chart.path.count("M") == 2


def test_cefr_ticks_cover_whole_levels_inside_the_scale() -> None:
    trend = trend_chart(Series(days(2), (3.0, 3.5)), cefr_ticks=True)
    assert [label for _, label in trend.ticks] == ["A1", "A2", "B1"]
    assert all(8 <= y <= 112 for y, _ in trend.ticks)


def test_empty_series_draws_nothing() -> None:
    trend = trend_chart(Series((), ()), cefr_ticks=True)
    assert trend.chart.path == "" and trend.ticks == () and trend.excluded_dots == ()
```

Run: `uv run pytest tests/unit/domain/test_dashboard_progress.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tutor.domain.dashboard.progress'`.

- [ ] **Step 2: Implement `progress.py`**

`src/tutor/domain/dashboard/progress.py`:

```python
"""Progreso: trend charts with excluded points and CEFR ticks (requirements section 11)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from tutor.domain.dashboard.charts import LineChart, cefr_label, line_chart
from tutor.domain.dashboard.types import Series


@dataclass(frozen=True, slots=True)
class TrendChart:
    chart: LineChart  # line without excluded points, on the full series' scale
    excluded_dots: tuple[tuple[float, float], ...]
    ticks: tuple[tuple[float, str], ...]  # (y, label) for whole CEFR levels


def latest_value(series: Series) -> float | None:
    for value in reversed(series.y):
        if value is not None:
            return value
    return None


def trend_chart(
    series: Series,
    excluded: Sequence[date] = (),
    *,
    cefr_ticks: bool = False,
    height: int = 120,
    pad: int = 8,
) -> TrendChart:
    full = line_chart(series.y, target=series.target, height=height, pad=pad)
    present = [i for i, v in enumerate(series.y) if v is not None]
    dot_at = dict(zip(present, full.dots, strict=True))
    excluded_days = set(excluded)
    skip = {i for i, x in enumerate(series.x) if x in excluded_days}
    parts: list[str] = []
    kept: list[tuple[float, float]] = []
    pen_down = False
    for i in range(len(series.y)):
        if i not in dot_at:
            pen_down = False  # a real gap lifts the pen
            continue
        if i in skip:
            continue  # an excluded point is bridged over
        px, py = dot_at[i]
        parts.append(f"{'L' if pen_down else 'M'}{px:g} {py:g}")
        kept.append((px, py))
        pen_down = True
    ticks: tuple[tuple[float, str], ...] = ()
    if cefr_ticks and present:
        inner_h = height - 2 * pad
        ticks = tuple(
            (round(pad + inner_h * (1 - level / full.y_max), 1), cefr_label(level))
            for level in range(1, 7)
            if level <= full.y_max
        )
    return TrendChart(
        chart=replace(full, path=" ".join(parts), dots=tuple(kept)),
        excluded_dots=tuple(dot_at[i] for i in sorted(skip) if i in dot_at),
        ticks=ticks,
    )
```

Run: `uv run pytest tests/unit/domain/test_dashboard_progress.py -q` then `uv run mypy`
Expected: PASS; no mypy errors.

- [ ] **Step 3: Write the failing page tests**

`tests/unit/web/test_web_progress.py`:

```python
import re
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend


def test_progress_shows_every_metric(login: Callable[[UUID], TestClient], demo: DemoUsers) -> None:
    response = login(demo.ana).get("/app/progress")
    assert response.status_code == 200
    text = response.text
    for expected in (
        "palabras por minuto en voz",
        "palabras por minuto en texto",
        "Errores por cada 100 palabras",
        "Frases usadas de las ofrecidas",
        "Minutos hablando por semana",
        "Sesiones hechas vs planeadas",
        "opinión del modelo",
        "tu autoevaluación",
        "Tu tutor estimó B1+; la prueba muestra B1+.",
        "Las sesiones de voz no tienen transcripción",
        "Ver como tabla",
    ):
        assert expected in text, expected
    assert re.search(r'data-count-to="27(\.0)?"', text)
    assert "data-draw" in text and "chart__dot--excluded" in text
    assert ">B1</text>" in text
    assert '<li lang="en">since three years</li>' in text
    assert '<li lang="en">I went to the meeting</li>' in text
    assert "<td>—</td>" in text  # the gap in the voice series


def test_first_day_progress_is_an_empty_state(
    login: Callable[[UUID], TestClient], demo: DemoUsers
) -> None:
    response = login(demo.nuevo).get("/app/progress")
    assert response.status_code == 200
    assert "Tu progreso aparece después de tu primera sesión completa" in response.text
    assert "chart__line" not in response.text


def test_progress_in_english(
    login: Callable[[UUID], TestClient], demo: DemoUsers, backend: MemoryBackend
) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    text = login(demo.ana).get("/app/progress").text
    assert "<h1>Progress</h1>" in text and "model's opinion" in text
```

Run: `uv run pytest tests/unit/web/test_web_progress.py -q`
Expected: FAIL (404: the route does not exist).

- [ ] **Step 4: Register the Jinja globals**

In `src/tutor/web/views.py`, add the imports:

```python
from tutor.domain.dashboard.charts import cefr_label, meter_fraction
from tutor.domain.dashboard.progress import latest_value, trend_chart
```

and at the end of `Views._env`, before `return env`:

```python
        env.globals.update(
            trend_chart=trend_chart,
            latest_value=latest_value,
            meter_fraction=meter_fraction,
            cefr_label=cefr_label,
        )
```

If an earlier page task already registered `meter_fraction` or `cefr_label`, keep a single registration.

- [ ] **Step 5: Write the route and register it**

`src/tutor/web/routes/progress.py`:

```python
"""Progreso: every number computed by the server (requirements section 11)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from tutor.domain.dashboard.types import User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.views import render

router = APIRouter(dependencies=APP_ROUTER_DEPS)


@router.get("/app/progress", response_class=HTMLResponse)
async def progress_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    data = get_deps(request).reader.progress(user.id)
    return render(request, "pages/progress.html", {"p": data})
```

In `src/tutor/web/routes/__init__.py`, add `progress` to the `from tutor.web.routes import …` line inside `all_routers` and append `progress.router` to the `routers` list.

- [ ] **Step 6: Write the chart macros**

`src/tutor/web/templates/macros/charts.html`:

```html
{# Server-drawn charts with a text alternative (spec 6.5 and 12). Imported, not included. #}

{% macro fmtv(value, fmt) -%}
{%- if value is none -%}—
{%- elif fmt == "percent" -%}{{ value|percent }}
{%- elif fmt == "integer" -%}{{ value|round|int|integer }}
{%- elif fmt == "cefr" -%}{{ cefr_label(value) }}
{%- elif fmt == "decimal0" -%}{{ value|decimal(0) }}
{%- else -%}{{ value|decimal(1) }}
{%- endif -%}
{%- endmacro %}

{% macro kpi(value, fmt, label, target="", count=true) %}
<div class="card kpi">
  {% if value is none %}
  <p class="kpi__value">—</p>
  {% elif count %}
  <p class="kpi__value"><span aria-hidden="true" data-count-to="{{ value }}" data-count-decimals="{{ 0 if fmt in ('decimal0', 'integer') else 1 }}">{{ fmtv(value, fmt) }}</span><span class="sr-only">{{ fmtv(value, fmt) }}</span></p>
  {% else %}
  <p class="kpi__value">{{ fmtv(value, fmt) }}</p>
  {% endif %}
  <p class="kpi__label">{{ label }}</p>
  {% if target %}<p class="kpi__target">{{ target }}</p>{% endif %}
</div>
{% endmacro %}

{% macro line(series, label, unit, target_label="", fmt="decimal1", excluded=(), cefr=false, id="") %}
{% set trend = trend_chart(series, excluded, cefr_ticks=cefr) %}
{% set c = trend.chart %}
<figure class="card chart">
  <figcaption class="card__title">{{ label }}</figcaption>
  {% if c.path %}
  <svg viewBox="0 0 {{ c.width }} {{ c.height }}" role="img" aria-labelledby="{{ id }}-t {{ id }}-d">
    <title id="{{ id }}-t">{{ label }}</title>
    <desc id="{{ id }}-d">{% trans n=series.y|reject("none")|list|length, last=fmtv(latest_value(series), fmt), unit=unit %}{{ n }} puntos; último valor: {{ last }} {{ unit }}.{% endtrans %}</desc>
    {% for y, text in trend.ticks %}
    <line class="chart__tick" x1="0" x2="{{ c.width }}" y1="{{ y }}" y2="{{ y }}"></line>
    <text class="chart__ticklabel" x="2" y="{{ y - 2 }}">{{ text }}</text>
    {% endfor %}
    {% if c.target_y is not none %}
    <line class="chart__target" x1="0" x2="{{ c.width }}" y1="{{ c.target_y }}" y2="{{ c.target_y }}"></line>
    {% endif %}
    <path class="chart__line" d="{{ c.path }}" data-draw></path>
    {% for x, y in c.dots %}<circle class="chart__dot" cx="{{ x }}" cy="{{ y }}" r="3"></circle>{% endfor %}
    {% for x, y in trend.excluded_dots %}<circle class="chart__dot chart__dot--excluded" cx="{{ x }}" cy="{{ y }}" r="4"></circle>{% endfor %}
  </svg>
  {% if target_label or trend.excluded_dots %}
  <p class="chart__legend">
    {% if target_label %}<span class="chart__key chart__key--target" aria-hidden="true"></span>{{ target_label }}{% endif %}
    {% if trend.excluded_dots %}<span class="chart__key chart__key--excluded" aria-hidden="true"></span>{{ _("Punto excluido: salto de nivel sin confirmar") }}{% endif %}
  </p>
  {% endif %}
  <details class="as-table">
    <summary>{{ _("Ver como tabla") }}</summary>
    <table class="table">
      <thead><tr><th scope="col">{{ _("Fecha") }}</th><th scope="col">{{ label }}</th></tr></thead>
      <tbody>
        {% for x in series.x %}
        <tr><td>{{ x|day }}</td><td>{{ fmtv(series.y[loop.index0], fmt) }}{% if x in excluded %} ({{ _("excluido") }}){% endif %}</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </details>
  {% else %}
  <p class="empty">{{ _("Aún no hay datos para esta gráfica.") }}</p>
  {% endif %}
</figure>
{% endmacro %}
```

The empty-value cell renders exactly `<td>—</td>` because `fmtv` strips its whitespace; the page test relies on it.

- [ ] **Step 7: Write the page template**

`src/tutor/web/templates/pages/progress.html`:

```html
{% extends "layouts/app.html" %}
{% import "macros/charts.html" as charts %}
{% set active_nav = "progress" %}
{% block title %}{{ _("Progreso") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head">
  <h1>{{ _("Progreso") }}</h1>
  {% if p.sessions_counted %}<p class="muted">{% trans n=p.sessions_counted %}Sesiones completas contadas: {{ n }}.{% endtrans %}</p>{% endif %}
</header>

{% if not p.sessions_counted %}
<section class="card empty">
  <h2>{{ _("Tu progreso aparece después de tu primera sesión completa") }}</h2>
  <p>{{ _("Cada sesión que cierras suma palabras, errores corregidos y frases usadas. Aquí verás cómo cambian.") }}</p>
  <a class="btn btn--primary" href="/app/connect">{{ _("Conectar mi tutor") }}</a>
</section>
{% else %}

<section class="grid kpis" aria-label="{{ _('Resumen') }}">
  {{ charts.kpi(latest_value(p.wpm_voice), "decimal0", _("palabras por minuto en voz"), _("meta: 25 o más")) }}
  {{ charts.kpi(latest_value(p.wpm_text), "decimal0", _("palabras por minuto en texto"), _("meta: 12 o más")) }}
  {{ charts.kpi(latest_value(p.errors_per_100w), "decimal1", _("errores por cada 100 palabras"), _("menos es mejor")) }}
  {{ charts.kpi(latest_value(p.activation_rate), "percent", _("frases usadas de las ofrecidas"), _("meta: 40 %"), count=false) }}
  {{ charts.kpi(latest_value(p.minutes_spoken), "integer", _("minutos hablando esta semana")) }}
</section>

<div class="grid">
  {{ charts.line(p.wpm_voice, _("Palabras por minuto en voz"), _("palabras por minuto"), _("Meta: 25"), "decimal0", id="wpm-voice") }}
  {{ charts.line(p.wpm_text, _("Palabras por minuto en texto"), _("palabras por minuto"), _("Meta: 12"), "decimal0", id="wpm-text") }}
  {{ charts.line(p.errors_per_100w, _("Errores por cada 100 palabras"), "", "", "decimal1", id="errors") }}
  {{ charts.line(p.activation_rate, _("Frases usadas de las ofrecidas"), "", _("Meta: 40 %"), "percent", id="activation") }}
  {{ charts.line(p.minutes_spoken, _("Minutos hablando por semana"), _("minutos"), "", "integer", id="minutes") }}

  <section class="card">
    <h2 class="card__title">{{ _("Sesiones hechas vs planeadas") }}</h2>
    <ul class="weeks">
      {% for week in p.sessions_done.x %}
      {% set done = (p.sessions_done.y[loop.index0] or 0)|int %}
      {% set planned = (p.sessions_planned.y[loop.index0] or 0)|int %}
      <li class="weeks__row">
        <span>{% trans when=week|day %}Semana del {{ when }}{% endtrans %}</span>
        <span>{% trans done=done, planned=planned %}{{ done }} de {{ planned }}{% endtrans %}</span>
        <svg class="meter" viewBox="0 0 100 6" aria-hidden="true">
          <rect class="meter__track" width="100" height="6" rx="3"></rect>
          <rect class="meter__fill" data-grow width="{{ (100 * meter_fraction(done, planned))|round(1) }}" height="6" rx="3"></rect>
        </svg>
      </li>
      {% endfor %}
    </ul>
  </section>

  {{ charts.line(p.cefr_trend, _("Nivel de speaking, opinión del modelo"), "", "", "cefr", p.cefr_excluded, true, id="cefr") }}
  {{ charts.line(p.confidence_trend, _("Tu confianza, tu autoevaluación"), _("de 5"), "", "integer", id="confidence") }}
</div>

<p class="muted">{{ _("El nivel es la opinión del modelo sobre tus últimas 10 sesiones, no un nivel certificado. Las estimaciones con confianza baja cuentan la mitad.") }}</p>

{% if p.checkpoint %}
<section class="card">
  <h2 class="card__title">{{ _("Prueba mensual") }}</h2>
  {% if p.checkpoint.model_trend_level %}
  <p>{% trans model=p.checkpoint.model_trend_level, measured=p.checkpoint.measured_level %}Tu tutor estimó {{ model }}; la prueba muestra {{ measured }}.{% endtrans %}</p>
  {% else %}
  <p>{% trans measured=p.checkpoint.measured_level %}La prueba muestra {{ measured }}.{% endtrans %}</p>
  {% endif %}
  <p class="muted">{% trans when=p.checkpoint.taken_on|day("long") %}Prueba del {{ when }}. Es el único lugar donde afirmamos un nivel.{% endtrans %}</p>
</section>
{% endif %}

<div class="grid">
  <section class="card">
    <h2 class="card__title">{{ _("Errores recurrentes que dejaron de aparecer") }}</h2>
    {% if p.recurring_stopped %}
    <ul class="list">{% for form in p.recurring_stopped %}<li lang="en">{{ form }}</li>{% endfor %}</ul>
    {% else %}<p class="empty">{{ _("Todavía ninguno. Aparecen aquí cuando un error frecuente deja de salir.") }}</p>{% endif %}
  </section>
  <section class="card">
    <h2 class="card__title">{{ _("Frases en pausa") }}</h2>
    {% if p.parked_leeches %}
    <ul class="list">{% for item in p.parked_leeches %}<li lang="en">{{ item }}</li>{% endfor %}</ul>
    <p class="muted">{{ _("Fallaron varias veces seguidas; las dejamos descansar y tu plan puede incluir una práctica enfocada.") }}</p>
    {% else %}<p class="empty">{{ _("Ninguna frase en pausa.") }}</p>{% endif %}
  </section>
</div>

<p class="muted">{{ _("Las sesiones de voz no tienen transcripción; suponemos que su evidencia es tan precisa como la de texto hasta poder comprobarla.") }}</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 8: Add the English translations**

Append to `src/tutor/web/locale/en/LC_MESSAGES/messages.po` (skip any msgid that an earlier task already added; a msgid must appear once):

```po
msgid "%(n)s puntos; último valor: %(last)s %(unit)s."
msgstr "%(n)s points; latest value: %(last)s %(unit)s."

msgid "Punto excluido: salto de nivel sin confirmar"
msgstr "Excluded point: unconfirmed level jump"

msgid "Ver como tabla"
msgstr "View as table"

msgid "excluido"
msgstr "excluded"

msgid "Aún no hay datos para esta gráfica."
msgstr "No data for this chart yet."

msgid "Sesiones completas contadas: %(n)s."
msgstr "Complete sessions counted: %(n)s."

msgid "Tu progreso aparece después de tu primera sesión completa"
msgstr "Your progress appears after your first complete session"

msgid "Cada sesión que cierras suma palabras, errores corregidos y frases usadas. Aquí verás cómo cambian."
msgstr "Every session you finish adds words, corrected mistakes and phrases used. You'll see them change here."

msgid "Conectar mi tutor"
msgstr "Connect my tutor"

msgid "Resumen"
msgstr "Summary"

msgid "palabras por minuto en voz"
msgstr "words per minute in voice"

msgid "meta: 25 o más"
msgstr "target: 25 or more"

msgid "palabras por minuto en texto"
msgstr "words per minute in text"

msgid "meta: 12 o más"
msgstr "target: 12 or more"

msgid "errores por cada 100 palabras"
msgstr "mistakes per 100 words"

msgid "menos es mejor"
msgstr "lower is better"

msgid "frases usadas de las ofrecidas"
msgstr "offered phrases you used"

msgid "meta: 40 %"
msgstr "target: 40%"

msgid "minutos hablando esta semana"
msgstr "minutes speaking this week"

msgid "Palabras por minuto en voz"
msgstr "Words per minute in voice"

msgid "palabras por minuto"
msgstr "words per minute"

msgid "Meta: 25"
msgstr "Target: 25"

msgid "Palabras por minuto en texto"
msgstr "Words per minute in text"

msgid "Meta: 12"
msgstr "Target: 12"

msgid "Errores por cada 100 palabras"
msgstr "Mistakes per 100 words"

msgid "Frases usadas de las ofrecidas"
msgstr "Offered phrases you used"

msgid "Meta: 40 %"
msgstr "Target: 40%"

msgid "Minutos hablando por semana"
msgstr "Minutes speaking per week"

msgid "minutos"
msgstr "minutes"

msgid "Sesiones hechas vs planeadas"
msgstr "Sessions done vs planned"

msgid "Semana del %(when)s"
msgstr "Week of %(when)s"

msgid "%(done)s de %(planned)s"
msgstr "%(done)s of %(planned)s"

msgid "Nivel de speaking, opinión del modelo"
msgstr "Speaking level, the model's opinion"

msgid "Tu confianza, tu autoevaluación"
msgstr "Your confidence, your self-assessment"

msgid "de 5"
msgstr "out of 5"

msgid "El nivel es la opinión del modelo sobre tus últimas 10 sesiones, no un nivel certificado. Las estimaciones con confianza baja cuentan la mitad."
msgstr "The level is the model's opinion of your last 10 sessions, not a certified level. Low-confidence estimates count half."

msgid "Prueba mensual"
msgstr "Monthly checkpoint"

msgid "Tu tutor estimó %(model)s; la prueba muestra %(measured)s."
msgstr "Your tutor estimated %(model)s; the test shows %(measured)s."

msgid "La prueba muestra %(measured)s."
msgstr "The test shows %(measured)s."

msgid "Prueba del %(when)s. Es el único lugar donde afirmamos un nivel."
msgstr "Checkpoint of %(when)s. This is the only place we state a level."

msgid "Errores recurrentes que dejaron de aparecer"
msgstr "Recurring mistakes that stopped appearing"

msgid "Todavía ninguno. Aparecen aquí cuando un error frecuente deja de salir."
msgstr "None yet. They show up here when a frequent mistake stops appearing."

msgid "Frases en pausa"
msgstr "Parked phrases"

msgid "Fallaron varias veces seguidas; las dejamos descansar y tu plan puede incluir una práctica enfocada."
msgstr "They failed several times in a row; we let them rest and your plan may include focused practice."

msgid "Ninguna frase en pausa."
msgstr "No parked phrases."

msgid "Las sesiones de voz no tienen transcripción; suponemos que su evidencia es tan precisa como la de texto hasta poder comprobarla."
msgstr "Voice sessions have no transcript; we assume their evidence is as accurate as text until we can check it."
```

The English test looks for "model's opinion": it comes from "Speaking level, the model's opinion". Jinja autoescapes the apostrophe in translated strings only when they pass through `{{ }}` as plain strings; `_()` with `newstyle=True` returns `Markup`-safe text, so the apostrophe stays literal. If the rendered page shows `model&#39;s`, change the test assertion to `"model&#39;s opinion"` rather than marking anything safe.

- [ ] **Step 9: Append the page CSS**

Append to `src/tutor/web/static/css/app.css`:

```css
/* --- Progreso (Task 25) --- */
.kpis { grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr)); }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.chart__tick { stroke: color-mix(in srgb, var(--ink) 12%, transparent); stroke-width: 1; }
.chart__ticklabel { fill: var(--ink); font-size: 9px; opacity: .7; }
.chart__dot--excluded { fill: var(--card); stroke: var(--coral); stroke-width: 2; }
.chart__legend { display: flex; flex-wrap: wrap; gap: .25rem 1rem; align-items: center; font-size: .875rem; margin: .5rem 0 0; }
.chart__key { display: inline-block; width: 1.25rem; height: 0; border-top: 2px dashed var(--ink); margin-inline-end: .375rem; vertical-align: middle; }
.chart__key--excluded { width: .625rem; height: .625rem; border: 2px solid var(--coral); border-radius: 50%; }
.weeks { list-style: none; margin: 0; padding: 0; display: grid; gap: .75rem; }
.weeks__row { display: grid; grid-template-columns: 1fr auto; gap: .25rem .75rem; align-items: center; }
.weeks__row .meter { grid-column: 1 / -1; width: 100%; height: .375rem; }
.list { margin: 0; padding-inline-start: 1.25rem; display: grid; gap: .375rem; overflow-wrap: anywhere; }
```

- [ ] **Step 10: Run tests, lint and coverage**

Run: `uv run pytest tests/unit/domain/test_dashboard_progress.py tests/unit/web/test_web_progress.py tests/unit/web/test_web_i18n.py -q` then `uv run just check`
Expected: PASS; domain coverage stays ≥ 90%.

- [ ] **Step 11: Commit**

```bash
git add src/tutor/domain/dashboard/progress.py src/tutor/web tests/unit/domain/test_dashboard_progress.py tests/unit/web/test_web_progress.py
git commit -m "feat(web): Progreso page with server-drawn trend charts"
```

### Task 26: Cuenta

**Files:**
- Create: `src/tutor/web/ratelimit.py`
- Create: `src/tutor/web/routes/account.py`
- Create: `src/tutor/web/templates/pages/account.html`
- Create: `src/tutor/web/templates/pages/deletion_requested.html`
- Modify: `src/tutor/web/app.py` (export limiter on `app.state`)
- Modify: `src/tutor/web/routes/auth.py` (delete the temporary `account_stub` route from Task 12)
- Modify: `src/tutor/web/routes/__init__.py`
- Modify: `src/tutor/web/templates/partials/error.html` (429 branch)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `src/tutor/web/static/css/app.css` (append)
- Test: `tests/unit/web/test_web_account.py`, `tests/unit/web/test_web_ratelimit.py`

**Interfaces:**
- Consumes: `AccountData`, `Preferences`, `Lang`, `ConnectedClient` (Task 1); `DashboardReader.account`, `AccountService.*`, `SubscriptionStore.customer_id` (Task 7); `render`, `today_for` (Task 10); `current_user`, `APP_ROUTER_DEPS`, `get_deps` (Task 11); `partials/install.html` (Task 14; reads `install_dismissed: bool`); `POST /billing/portal` (Task 18).
- Produces:
  - `tutor.web.ratelimit.DailyLimiter(limit: int)` with `hit(user_id: UUID, day: date) -> bool`.
  - `app.state.export_limiter: DailyLimiter` (5 per learner per local day). Production replaces it with the shared rate limiter from requirements section 5 when the product app is assembled (follow-up plan); the in-process limiter is per worker process.
  - Routes: `GET /app/account`, `POST /app/account/preferences`, `POST /app/account/clients/{client_id}/revoke`, `GET /app/account/export`, `POST /app/account/delete`.
  - Labels the browser tests rely on (Task 30): link "Descargar mis datos"; input labelled "Escribe ELIMINAR para confirmar"; button "Eliminar mi cuenta"; button "Cerrar sesión" (also on this page, because phones have no sidebar); confirmation heading "Solicitud recibida".
  - Confirmation words: `ELIMINAR` (Spanish UI) or `DELETE` (English UI), case-insensitive, surrounding spaces ignored.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_ratelimit.py`:

```python
from datetime import date
from uuid import uuid4

from tutor.web.ratelimit import DailyLimiter


def test_limit_per_user_per_day() -> None:
    limiter = DailyLimiter(2)
    ana, beto = uuid4(), uuid4()
    day = date(2027, 1, 12)
    assert limiter.hit(ana, day) and limiter.hit(ana, day)
    assert not limiter.hit(ana, day)
    assert limiter.hit(beto, day)
    assert limiter.hit(ana, date(2027, 1, 13))
```

`tests/unit/web/test_web_account.py`:

```python
import json
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import ConnectedClient, Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

from .conftest import csrf_of

Login = Callable[[UUID], TestClient]


def test_account_shows_profile_clients_plan_and_csrf(login: Login, demo: DemoUsers) -> None:
    text = login(demo.ana).get("/app/account").text
    assert '<meta name="csrf-token"' in text
    assert "cámbialo desde el chat" in text
    assert "Desconectar Claude" in text
    assert "Ver Annual" in text and "Gestionar pago" not in text
    assert "Descargar mis datos" in text
    assert "Escribe ELIMINAR para confirmar" in text
    assert text.count("Cerrar sesión") >= 2  # sidebar and page (phones have no sidebar)


def test_annual_user_sees_renewal_and_portal(login: Login, demo: DemoUsers) -> None:
    text = login(demo.beto).get("/app/account").text
    assert "Activo hasta el" in text and "Gestionar pago" in text


def test_first_day_account(login: Login, demo: DemoUsers) -> None:
    text = login(demo.nuevo).get("/app/account").text
    assert "Tu perfil se crea con tu diagnóstico" in text
    assert "Ningún cliente conectado." in text


def test_preferences_are_saved(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    c = login(demo.ana)
    response = c.post(
        "/app/account/preferences",
        data={"csrf_token": csrf_of(c), "lang": "en", "reduce_motion": "on"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/app/account?saved=prefs"
    user = backend.users[demo.ana]
    assert user.lang is Lang.EN and user.reduce_motion
    assert backend.email_prefs[demo.ana] == (False, False)
    page = c.get("/app/account?saved=prefs").text
    assert "Preferences saved." in page and "data-reduce-motion" in page


def test_unknown_language_is_rejected(login: Login, demo: DemoUsers) -> None:
    c = login(demo.ana)
    response = c.post("/app/account/preferences", data={"csrf_token": csrf_of(c), "lang": "fr"})
    assert response.status_code == 422


def test_revoke_only_own_clients(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.clients[demo.beto].append(ConnectedClient("beto-only", "Claude Code", None))
    c = login(demo.ana)
    token = csrf_of(c)
    other = c.post("/app/account/clients/beto-only/revoke", data={"csrf_token": token})
    assert other.status_code == 404
    assert [x.id for x in backend.clients[demo.beto]][-1] == "beto-only"
    own = c.post("/app/account/clients/claude-web/revoke", data={"csrf_token": token})
    assert own.status_code == 303 and backend.clients[demo.ana] == []


def test_export_is_a_json_download_with_a_daily_limit(login: Login, demo: DemoUsers) -> None:
    c = login(demo.ana)
    first = c.get("/app/account/export")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("application/json")
    assert first.headers["content-disposition"] == 'attachment; filename="mis-datos.json"'
    data = json.loads(first.text)
    assert data["user"]["display_name"] == "Ana" and data["glossary"]
    for _ in range(4):
        assert c.get("/app/account/export").status_code == 200
    limited = c.get("/app/account/export")
    assert limited.status_code == 429 and "Llegaste al límite de hoy" in limited.text


def test_delete_needs_the_confirmation_word(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.ana)
    response = c.post("/app/account/delete", data={"csrf_token": csrf_of(c), "confirm": "borrar"})
    assert response.status_code == 422
    assert "Escribe la palabra ELIMINAR exactamente para confirmar." in response.text
    assert backend.users[demo.ana].deletion_requested_at is None


def test_delete_account_logs_out(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    c = login(demo.ana)
    response = c.post(
        "/app/account/delete", data={"csrf_token": csrf_of(c), "confirm": " eliminar "}
    )
    assert response.status_code == 200 and "Solicitud recibida" in response.text
    assert backend.users[demo.ana].deletion_requested_at is not None
    assert c.get("/app/account").status_code == 303


def test_account_in_english_accepts_delete(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    c = login(demo.ana)
    text = c.get("/app/account").text
    assert "<h1>Account</h1>" in text and "Type DELETE to confirm" in text
    response = c.post("/app/account/delete", data={"csrf_token": csrf_of(c), "confirm": "DELETE"})
    assert response.status_code == 200
```

Run: `uv run pytest tests/unit/web/test_web_ratelimit.py tests/unit/web/test_web_account.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'tutor.web.ratelimit'`; the account tests fail on the stub page).

- [ ] **Step 2: Implement the limiter and wire it**

`src/tutor/web/ratelimit.py`:

```python
"""Per-learner daily limit for expensive actions (data export).

In-process and per worker: production swaps in the shared rate limiter from
requirements section 5 when the product app is assembled.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID


class DailyLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._counts: dict[tuple[UUID, date], int] = {}

    def hit(self, user_id: UUID, day: date) -> bool:
        for key in [k for k in self._counts if k[1] < day]:
            del self._counts[key]
        count = self._counts.get((user_id, day), 0)
        if count >= self.limit:
            return False
        self._counts[(user_id, day)] = count + 1
        return True
```

In `src/tutor/web/app.py`, import `from tutor.web.ratelimit import DailyLimiter` and add in `create_app`, after `app.state.views = …`:

```python
    app.state.export_limiter = DailyLimiter(5)
```

- [ ] **Step 3: Remove the temporary account stub**

Delete the `account_stub` function (and its decorator) that Task 12 added at the end of `src/tutor/web/routes/auth.py`. Leave every other route there unchanged. `csrf_of` in `tests/unit/web/conftest.py` keeps working because the real page below renders the same `<meta name="csrf-token">` through `base.html`.

- [ ] **Step 4: Write the routes**

`src/tutor/web/routes/account.py`:

```python
"""Cuenta: profile, preferences, clients, plan, export and deletion (spec 6.9)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.types import Lang, Preferences, User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.ratelimit import DailyLimiter
from tutor.web.views import render, today_for

router = APIRouter(dependencies=APP_ROUTER_DEPS)
CONFIRM_WORDS = frozenset({"ELIMINAR", "DELETE"})


def _context(request: Request, user: User, **extra: Any) -> dict[str, Any]:
    deps = get_deps(request)
    data = deps.reader.account(user.id)
    return {
        "a": data,
        "has_customer": deps.subscriptions.customer_id(user.id) is not None,
        "install_dismissed": data.install_prompt_dismissed,
        "saved": request.query_params.get("saved", ""),
        "delete_error": False,
        **extra,
    }


@router.get("/app/account", response_class=HTMLResponse)
async def account_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    return render(request, "pages/account.html", _context(request, user))


@router.post("/app/account/preferences")
async def save_preferences(
    request: Request,
    user: User = Depends(current_user),
    lang: str = Form(...),
    reduce_motion: str | None = Form(None),
    email_weekly: str | None = Form(None),
    email_reminders: str | None = Form(None),
) -> Response:
    if lang not in {item.value for item in Lang}:
        raise HTTPException(status_code=422)
    prefs = Preferences(
        Lang(lang), reduce_motion == "on", email_weekly == "on", email_reminders == "on"
    )
    get_deps(request).account.set_preferences(user.id, prefs)
    return RedirectResponse("/app/account?saved=prefs", status_code=303)


@router.post("/app/account/clients/{client_id}/revoke")
async def revoke_client(
    request: Request, client_id: str, user: User = Depends(current_user)
) -> Response:
    if not get_deps(request).account.revoke_client(user.id, client_id):
        raise HTTPException(status_code=404)
    return RedirectResponse("/app/account?saved=client", status_code=303)


@router.get("/app/account/export")
async def export_data(request: Request, user: User = Depends(current_user)) -> Response:
    limiter: DailyLimiter = request.app.state.export_limiter
    if not limiter.hit(user.id, today_for(request)):
        raise HTTPException(status_code=429)
    body = json.dumps(
        get_deps(request).account.export_data(user.id), default=str, ensure_ascii=False, indent=2
    )
    return Response(
        body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="mis-datos.json"'},
    )


@router.post("/app/account/delete", response_class=HTMLResponse)
async def delete_account(
    request: Request, user: User = Depends(current_user), confirm: str = Form("")
) -> HTMLResponse:
    if confirm.strip().upper() not in CONFIRM_WORDS:
        return render(
            request,
            "pages/account.html",
            _context(request, user, delete_error=True),
            status_code=422,
        )
    deps = get_deps(request)
    deps.account.request_deletion(user.id, deps.clock())
    request.state.web.logout()
    request.state.user = None  # render the confirmation as a logged-out page
    return render(request, "pages/deletion_requested.html")
```

In `src/tutor/web/routes/__init__.py`, add `account` to the import line inside `all_routers` and append `account.router` to `routers`.

- [ ] **Step 5: Write the templates**

`src/tutor/web/templates/pages/account.html`:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "account" %}
{% block title %}{{ _("Cuenta") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head">
  <h1>{{ _("Cuenta") }}</h1>
  <form method="post" action="/auth/logout" class="page-head__actions">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="btn btn--ghost btn--small">{{ _("Cerrar sesión") }}</button>
  </form>
</header>

<div class="grid">
  <section class="card">
    <h2 class="card__title">{{ _("Perfil") }}</h2>
    {% if a.profile %}
    <dl class="facts">
      <dt>{{ _("Nombre") }}</dt><dd>{{ a.user.display_name }}</dd>
      <dt>{{ _("Áreas") }}</dt><dd>{{ a.profile.domains|join(", ") }}</dd>
      <dt>{{ _("Ritmo") }}</dt><dd>{% trans m=a.profile.minutes_per_day, d=a.profile.days_per_week %}{{ m }} minutos al día, {{ d }} días a la semana{% endtrans %}</dd>
      <dt>{{ _("Meta") }}</dt><dd>{% if a.profile.target_level %}{{ a.profile.target_level }}{% if a.profile.target_date %} · {{ a.profile.target_date|day("long") }}{% endif %}{% else %}—{% endif %}</dd>
    </dl>
    <p class="muted">{{ _("Para cambiar tu perfil, cámbialo desde el chat; por ejemplo: «change my pace to 3 days a week».") }}</p>
    {% else %}
    <p>{{ _("Tu perfil se crea con tu diagnóstico en el chat.") }}</p>
    {% endif %}
  </section>

  <form method="post" action="/app/account/preferences" class="card">
    <h2 class="card__title">{{ _("Preferencias") }}</h2>
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="form-row">
      <label for="pref-lang">{{ _("Idioma de la interfaz") }}</label>
      <select id="pref-lang" name="lang">
        <option value="es_MX"{% if a.prefs.lang == "es_MX" %} selected{% endif %} lang="es">Español</option>
        <option value="en"{% if a.prefs.lang == "en" %} selected{% endif %} lang="en">English</option>
      </select>
    </div>
    <div class="form-row form-row--check">
      <input type="checkbox" id="pref-motion" name="reduce_motion"{% if a.prefs.reduce_motion %} checked{% endif %}>
      <label for="pref-motion">{{ _("Reducir animaciones") }}</label>
    </div>
    <div class="form-row form-row--check">
      <input type="checkbox" id="pref-weekly" name="email_weekly"{% if a.prefs.email_weekly %} checked{% endif %}>
      <label for="pref-weekly">{{ _("Recibir el reporte semanal por correo (Annual)") }}</label>
    </div>
    <div class="form-row form-row--check">
      <input type="checkbox" id="pref-reminders" name="email_reminders"{% if a.prefs.email_reminders %} checked{% endif %}>
      <label for="pref-reminders">{{ _("Recibir recordatorios por correo") }}</label>
    </div>
    <button class="btn btn--primary">{{ _("Guardar preferencias") }}</button>
    {% if saved == "prefs" %}<p class="muted" role="status">{{ _("Preferencias guardadas.") }}</p>{% endif %}
  </form>

  {% include "partials/install.html" %}

  <section class="card">
    <h2 class="card__title">{{ _("Clientes conectados") }}</h2>
    {% if a.clients %}
    <ul class="list list--plain">
      {% for client in a.clients %}
      <li class="list__row">
        <span>{{ client.name }}</span>
        <span class="muted">{% if client.last_used_at %}{% trans when=client.last_used_at.date()|day %}Último uso: {{ when }}{% endtrans %}{% else %}{{ _("Sin uso todavía") }}{% endif %}</span>
        <form method="post" action="/app/account/clients/{{ client.id|urlencode }}/revoke" class="inline-form">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button class="btn btn--ghost btn--small">{% trans name=client.name %}Desconectar {{ name }}{% endtrans %}</button>
        </form>
      </li>
      {% endfor %}
    </ul>
    {% else %}
    <p>{{ _("Ningún cliente conectado.") }} <a href="/app/connect">{{ _("Conectar mi tutor") }}</a></p>
    {% endif %}
    {% if saved == "client" %}<p class="muted" role="status">{{ _("Cliente desconectado. Tendrá que volver a iniciar sesión.") }}</p>{% endif %}
  </section>

  <section class="card" id="plan">
    <h2 class="card__title">{{ _("Tu plan") }}</h2>
    {% set s = a.subscription %}
    {% if s.tier == "annual" %}
    <p><span class="chip chip--annual">{{ _("Annual") }}</span>
      {% if s.status == "past_due" %}{{ _("No pudimos cobrar tu renovación.") }}
      {% elif s.period_end %}{% trans when=s.period_end|day("long") %}Activo hasta el {{ when }}; se renueva solo.{% endtrans %}{% endif %}
    </p>
    {% else %}
    <p><span class="chip chip--free">{{ _("Free") }}</span>
      {% if s.status == "lapsed" %}{{ _("Tu plan volvió a Free; no se borró nada.") }}
      {% elif usage %}{% trans sessions=usage.session_cap, items=usage.glossary_cap %}{{ sessions }} sesiones por semana y {{ items }} elementos de glosario.{% endtrans %}{% endif %}
    </p>
    <a class="btn btn--primary" href="/billing/checkout">{{ _("Ver Annual") }}</a>
    {% endif %}
    {% if has_customer %}
    <form method="post" action="/billing/portal" class="inline-form">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button class="btn btn--ghost">{{ _("Gestionar pago") }}</button>
    </form>
    {% endif %}
    <p class="muted">{{ _("¿Pagaste hace menos de 14 días y no te convenció? Te devolvemos el pago completo. Escríbenos a:") }}</p>
    <p class="code-copy">
      <span>{{ config.support_email }}</span>
      <button type="button" class="btn btn--ghost btn--small copy" data-copy="{{ config.support_email }}">
        <svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#copy"></use></svg><span>{{ _("Copiar") }}</span>
      </button>
    </p>
  </section>

  <section class="card">
    <h2 class="card__title">{{ _("Tus datos") }}</h2>
    <p>{{ _("Descarga todo lo que guardamos de ti en un archivo JSON.") }}</p>
    <a class="btn btn--ghost" href="/app/account/export" download>{{ _("Descargar mis datos") }}</a>
  </section>

  <section class="card card--danger" id="delete">
    <h2 class="card__title">{{ _("Eliminar mi cuenta") }}</h2>
    <p>{{ _("Se borran tu perfil, plan, sesiones, glosario y reportes. Si tienes Annual, se cancela sin renovación; el reembolso aplica solo dentro de los primeros 14 días. El borrado se completa en 24 horas y no se puede deshacer.") }}</p>
    <form method="post" action="/app/account/delete">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <div class="form-row">
        <label for="confirm-delete">{{ _("Escribe ELIMINAR para confirmar") }}</label>
        <input id="confirm-delete" name="confirm" autocomplete="off" autocapitalize="characters"{% if delete_error %} aria-invalid="true" aria-describedby="confirm-error"{% endif %}>
        {% if delete_error %}<p class="field-error" id="confirm-error">{{ _("Escribe la palabra ELIMINAR exactamente para confirmar.") }}</p>{% endif %}
      </div>
      <button class="btn btn--danger">{{ _("Eliminar mi cuenta") }}</button>
    </form>
  </section>
</div>
{% endblock %}
```

`src/tutor/web/templates/pages/deletion_requested.html`:

```html
{% extends "layouts/public.html" %}
{% block title %}{{ _("Solicitud recibida") }} · English Tutor{% endblock %}
{% block content %}
<section class="card card--narrow">
  <h1>{{ _("Solicitud recibida") }}</h1>
  <p>{{ _("Cerramos tu sesión. Borraremos todos tus datos en un máximo de 24 horas.") }}</p>
  <p>{% trans email=config.support_email %}Si fue un error, escríbenos hoy a {{ email }}.{% endtrans %}</p>
</section>
{% endblock %}
```

In `src/tutor/web/templates/partials/error.html`, add this branch right after the `{% elif status == 403 %}` branch:

```html
  {% elif status == 429 %}
    <h1>{{ _("Llegaste al límite de hoy") }}</h1>
    <p>{{ _("Puedes descargar tus datos hasta 5 veces al día. Intenta de nuevo mañana.") }}</p>
```

- [ ] **Step 6: Add the English translations**

Append (skip msgids already present; "Cuenta", "Cerrar sesión", "Annual", "Free", "Ver Annual", "Gestionar pago", "Tu plan volvió a Free; no se borró nada.", "Conectar mi tutor" and "Si fue un error, escríbenos hoy a %(email)s." already exist from Tasks 10, 12 and 25):

```po
msgid "Perfil"
msgstr "Profile"

msgid "Nombre"
msgstr "Name"

msgid "Áreas"
msgstr "Areas"

msgid "Ritmo"
msgstr "Pace"

msgid "%(m)s minutos al día, %(d)s días a la semana"
msgstr "%(m)s minutes a day, %(d)s days a week"

msgid "Meta"
msgstr "Goal"

msgid "Para cambiar tu perfil, cámbialo desde el chat; por ejemplo: «change my pace to 3 days a week»."
msgstr "To change your profile, change it from the chat; for example: “change my pace to 3 days a week”."

msgid "Tu perfil se crea con tu diagnóstico en el chat."
msgstr "Your profile is created by your diagnostic in the chat."

msgid "Preferencias"
msgstr "Preferences"

msgid "Idioma de la interfaz"
msgstr "Interface language"

msgid "Reducir animaciones"
msgstr "Reduce animations"

msgid "Recibir el reporte semanal por correo (Annual)"
msgstr "Get the weekly report by email (Annual)"

msgid "Recibir recordatorios por correo"
msgstr "Get reminders by email"

msgid "Guardar preferencias"
msgstr "Save preferences"

msgid "Preferencias guardadas."
msgstr "Preferences saved."

msgid "Clientes conectados"
msgstr "Connected clients"

msgid "Último uso: %(when)s"
msgstr "Last used: %(when)s"

msgid "Sin uso todavía"
msgstr "Not used yet"

msgid "Desconectar %(name)s"
msgstr "Disconnect %(name)s"

msgid "Ningún cliente conectado."
msgstr "No clients connected."

msgid "Cliente desconectado. Tendrá que volver a iniciar sesión."
msgstr "Client disconnected. It will have to sign in again."

msgid "No pudimos cobrar tu renovación."
msgstr "We couldn't charge your renewal."

msgid "Activo hasta el %(when)s; se renueva solo."
msgstr "Active until %(when)s; it renews automatically."

msgid "%(sessions)s sesiones por semana y %(items)s elementos de glosario."
msgstr "%(sessions)s sessions a week and %(items)s glossary items."

msgid "¿Pagaste hace menos de 14 días y no te convenció? Te devolvemos el pago completo. Escríbenos a:"
msgstr "Paid less than 14 days ago and not convinced? We refund the full payment. Write to us at:"

msgid "Tus datos"
msgstr "Your data"

msgid "Descarga todo lo que guardamos de ti en un archivo JSON."
msgstr "Download everything we store about you as a JSON file."

msgid "Descargar mis datos"
msgstr "Download my data"

msgid "Eliminar mi cuenta"
msgstr "Delete my account"

msgid "Se borran tu perfil, plan, sesiones, glosario y reportes. Si tienes Annual, se cancela sin renovación; el reembolso aplica solo dentro de los primeros 14 días. El borrado se completa en 24 horas y no se puede deshacer."
msgstr "Your profile, plan, sessions, glossary and reports are deleted. If you have Annual, it is cancelled without renewal; refunds apply only within the first 14 days. Deletion completes within 24 hours and cannot be undone."

msgid "Escribe ELIMINAR para confirmar"
msgstr "Type DELETE to confirm"

msgid "Escribe la palabra ELIMINAR exactamente para confirmar."
msgstr "Type the word DELETE exactly to confirm."

msgid "Solicitud recibida"
msgstr "Request received"

msgid "Cerramos tu sesión. Borraremos todos tus datos en un máximo de 24 horas."
msgstr "We signed you out. We'll delete all your data within 24 hours."

msgid "Llegaste al límite de hoy"
msgstr "You've reached today's limit"

msgid "Puedes descargar tus datos hasta 5 veces al día. Intenta de nuevo mañana."
msgstr "You can download your data up to 5 times a day. Try again tomorrow."
```

- [ ] **Step 7: Append the page CSS**

```css
/* --- Cuenta (Task 26) --- */
.facts { display: grid; grid-template-columns: max-content 1fr; gap: .375rem 1rem; margin: 0 0 .75rem; }
.facts dt { font-weight: 600; }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.form-row--check { display: flex; align-items: center; gap: .5rem; min-height: 44px; }
.form-row--check input { width: 1.25rem; height: 1.25rem; accent-color: var(--leaf); }
.list--plain { list-style: none; padding: 0; }
.list__row { display: flex; flex-wrap: wrap; align-items: center; gap: .25rem .75rem; justify-content: space-between; }
.card--danger { border: 2px solid var(--coral); }
```

- [ ] **Step 8: Run tests and commit**

Run: `uv run pytest tests/unit/web -q` then `uv run just check`
Expected: PASS (every earlier test that used `csrf_of` still passes against the real page).

```bash
git add src/tutor/web tests/unit/web
git commit -m "feat(web): Cuenta with preferences, clients, export and deletion"
```

### Task 27: Reportes

**Files:**
- Create: `src/tutor/web/routes/reports.py`
- Create: `src/tutor/web/templates/pages/reports.html`
- Modify: `src/tutor/web/routes/__init__.py`
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `src/tutor/web/static/css/app.css` (append)
- Test: `tests/unit/web/test_web_reports.py`

**Interfaces:**
- Consumes: `ReportsPage`, `WeeklyReport`, `ReportNumbers`, `Checkpoint` (Task 1); `DashboardReader.reports` (Task 7); `subscription` in the base context (Task 10); msgids "Semana del %(when)s", "%(done)s de %(planned)s", "Tu tutor estimó %(model)s; la prueba muestra %(measured)s.", "La prueba muestra %(measured)s." (Task 25).
- Produces: route `GET /app/reports` (no HTMX partial).

Decision: a Free learner who has older reports (for example after a lapsed Annual) still sees them, because they are the learner's data; the Annual offer is shown above them. Free learners without reports see only the description and the offer, with no example numbers.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_reports.py`:

```python
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import Lang
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

Login = Callable[[UUID], TestClient]


def test_annual_learner_sees_weekly_reports_and_checkpoints(login: Login, demo: DemoUsers) -> None:
    text = login(demo.beto).get("/app/reports").text
    assert "Semana del" in text and "3 de 4" in text
    assert "Pedir aclaraciones sin disculparte de más" in text
    assert '<span lang="en">since three years</span>' in text
    assert "Tu tutor estimó B1+; la prueba muestra B1+." in text
    assert "El reporte semanal es parte de Annual" not in text


def test_free_learner_sees_the_offer_without_fake_numbers(login: Login, demo: DemoUsers) -> None:
    text = login(demo.ana).get("/app/reports").text
    assert "El reporte semanal es parte de Annual" in text
    assert 'href="/billing/checkout"' in text
    assert "report__numbers" not in text


def test_first_day_reports(login: Login, demo: DemoUsers) -> None:
    response = login(demo.nuevo).get("/app/reports")
    assert response.status_code == 200 and "El reporte semanal es parte de Annual" in response.text


def test_free_learner_keeps_old_reports(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    backend.report_pages[demo.ana] = backend.report_pages[demo.beto]
    text = login(demo.ana).get("/app/reports").text
    assert "El reporte semanal es parte de Annual" in text and "report__numbers" in text


def test_reports_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.beto] = replace(backend.users[demo.beto], lang=Lang.EN)
    text = login(demo.beto).get("/app/reports").text
    assert "<h1>Reports</h1>" in text and "Week of" in text
```

Run: `uv run pytest tests/unit/web/test_web_reports.py -q`
Expected: FAIL (404).

- [ ] **Step 2: Write the route and register it**

`src/tutor/web/routes/reports.py`:

```python
"""Reportes: weekly reports and monthly checkpoints (spec 6.8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from tutor.domain.dashboard.types import User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.views import render

router = APIRouter(dependencies=APP_ROUTER_DEPS)


@router.get("/app/reports", response_class=HTMLResponse)
async def reports_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    return render(request, "pages/reports.html", {"r": get_deps(request).reader.reports(user.id)})
```

Register `reports.router` in `all_routers` as in Task 25 Step 5.

- [ ] **Step 3: Write the template**

`src/tutor/web/templates/pages/reports.html`:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "reports" %}
{% block title %}{{ _("Reportes") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head"><h1>{{ _("Reportes") }}</h1></header>

{% if subscription.tier == "free" %}
<section class="card">
  <h2 class="card__title">{{ _("El reporte semanal es parte de Annual") }}</h2>
  <p>{{ _("Cada lunes a las 7:00 recibes cuatro números: sesiones hechas contra planeadas, minutos hablando, errores recurrentes que dejaron de aparecer y frases que activaste. Después, tres líneas: el enfoque de la semana, una frase que se te resiste y un logro.") }}</p>
  <a class="btn btn--primary" href="/billing/checkout">{{ _("Ver Annual") }}</a>
</section>
{% elif not r.weekly %}
<p class="empty">{{ _("Tu primer reporte llega el próximo lunes a las 7:00.") }}</p>
{% endif %}

{% for w in r.weekly %}
<article class="card report">
  <h2 class="card__title">{% trans when=w.numbers.week_start|day("long") %}Semana del {{ when }}{% endtrans %}</h2>
  <dl class="report__numbers">
    <div><dt>{{ _("Sesiones") }}</dt><dd>{% trans done=w.numbers.sessions_done, planned=w.numbers.sessions_planned %}{{ done }} de {{ planned }}{% endtrans %}</dd></div>
    <div><dt>{{ _("Minutos hablando") }}</dt><dd>{{ w.numbers.minutes_spoken|integer }}</dd></div>
    <div><dt>{{ _("Errores que dejaron de aparecer") }}</dt><dd>{{ w.numbers.errors_stopped|integer }}</dd></div>
    <div><dt>{{ _("Frases activadas") }}</dt><dd>{{ w.numbers.chunks_activated|integer }}</dd></div>
  </dl>
  <p><strong>{{ _("Enfoque:") }}</strong> {{ w.focus }}</p>
  {% if w.leech %}<p><strong>{{ _("Se te resiste:") }}</strong> <span lang="en">{{ w.leech }}</span></p>{% endif %}
  {% if w.win %}<p><strong>{{ _("Logro:") }}</strong> {{ w.win }}</p>{% endif %}
</article>
{% endfor %}

{% if r.checkpoints %}
<section class="card">
  <h2 class="card__title">{{ _("Pruebas mensuales") }}</h2>
  <ul class="list list--plain">
    {% for cp in r.checkpoints %}
    <li class="list__row">
      <span>{{ cp.taken_on|day("long") }}</span>
      <span>{% if cp.model_trend_level %}{% trans model=cp.model_trend_level, measured=cp.measured_level %}Tu tutor estimó {{ model }}; la prueba muestra {{ measured }}.{% endtrans %}{% else %}{% trans measured=cp.measured_level %}La prueba muestra {{ measured }}.{% endtrans %}{% endif %}</span>
    </li>
    {% endfor %}
  </ul>
</section>
{% endif %}
{% endblock %}
```

`3 de 4` in the test comes from Beto's first report (`sessions_done=3`, `sessions_planned=4`).

- [ ] **Step 4: Add the English translations**

```po
msgid "El reporte semanal es parte de Annual"
msgstr "The weekly report is part of Annual"

msgid "Cada lunes a las 7:00 recibes cuatro números: sesiones hechas contra planeadas, minutos hablando, errores recurrentes que dejaron de aparecer y frases que activaste. Después, tres líneas: el enfoque de la semana, una frase que se te resiste y un logro."
msgstr "Every Monday at 7:00 you get four numbers: sessions done against planned, minutes speaking, recurring mistakes that stopped appearing and phrases you activated. Then three lines: the week's focus, one phrase that resists you and one win."

msgid "Tu primer reporte llega el próximo lunes a las 7:00."
msgstr "Your first report arrives next Monday at 7:00."

msgid "Minutos hablando"
msgstr "Minutes speaking"

msgid "Errores que dejaron de aparecer"
msgstr "Mistakes that stopped appearing"

msgid "Frases activadas"
msgstr "Phrases activated"

msgid "Enfoque:"
msgstr "Focus:"

msgid "Se te resiste:"
msgstr "Still resisting:"

msgid "Logro:"
msgstr "Win:"

msgid "Pruebas mensuales"
msgstr "Monthly checkpoints"
```

- [ ] **Step 5: Append the page CSS**

```css
/* --- Reportes (Task 27) --- */
.report__numbers { display: grid; grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr)); gap: .75rem; margin: 0 0 1rem; }
.report__numbers div { background: var(--paper); border-radius: .75rem; padding: .75rem; }
.report__numbers dt { font-size: .875rem; }
.report__numbers dd { margin: .25rem 0 0; font-family: "Baloo 2", system-ui, sans-serif; font-size: 1.5rem; font-weight: 700; }
```

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest tests/unit/web/test_web_reports.py tests/unit/web/test_web_i18n.py -q` then `uv run just check`
Expected: PASS.

```bash
git add src/tutor/web tests/unit/web/test_web_reports.py
git commit -m "feat(web): Reportes page"
```

### Task 28: Admin settings

**Files:**
- Create: `src/tutor/web/routes/admin.py`
- Create: `src/tutor/web/templates/pages/admin_settings.html`
- Create: `src/tutor/web/templates/partials/admin_setting_row.html`
- Modify: `src/tutor/web/routes/__init__.py`
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Test: `tests/unit/web/test_web_admin.py`

**Interfaces:**
- Consumes: `Setting`, `SettingType` (Task 1); `parse_setting`, `SettingError` (Task 6); `SettingsStore.list_settings`, `SettingsStore.update_setting` (Task 7); `render`, `is_htmx` (Task 10); `require_admin`, `APP_ROUTER_DEPS`, `get_deps` (Task 11).
- Produces: `GET /admin/settings`, `POST /admin/settings/{key}` (form field `value`).

Decisions: an anonymous visitor gets the normal login redirect (303, or 401 with `HX-Redirect` for HTMX), because `require_admin` depends on `current_user`; a logged-in learner gets 404, so the page's existence is not revealed. The page uses the app layout with `active_nav = "admin"` and no extra styling beyond the shared table classes.

- [ ] **Step 1: Write the failing tests**

`tests/unit/web/test_web_admin.py`:

```python
from collections.abc import Callable
from uuid import UUID

from fastapi.testclient import TestClient

from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

from .conftest import csrf_of

Login = Callable[[UUID], TestClient]
KEY = "free_sessions_per_week"


def test_anonymous_is_sent_to_login(client: TestClient) -> None:
    response = client.get("/admin/settings")
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_learner_gets_404(login: Login, demo: DemoUsers) -> None:
    c = login(demo.ana)
    assert c.get("/admin/settings").status_code == 404
    assert (
        c.post(f"/admin/settings/{KEY}", data={"csrf_token": csrf_of(c), "value": "9"}).status_code
        == 404
    )


def test_admin_sees_every_setting(login: Login, demo: DemoUsers) -> None:
    text = login(demo.admin).get("/admin/settings").text
    assert "<table" in text and KEY in text and "fsrs_retention_target" in text
    assert 'aria-current="page"' in text


def test_admin_updates_a_setting(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    c = login(demo.admin)
    response = c.post(f"/admin/settings/{KEY}", data={"csrf_token": csrf_of(c), "value": " 4 "})
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/settings?saved={KEY}"
    assert backend.settings_rows[KEY].value == "4"
    assert backend.audit[-1] == ("setting_changed", demo.admin, KEY)


def test_invalid_value_is_422_and_unchanged(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    c = login(demo.admin)
    response = c.post(f"/admin/settings/{KEY}", data={"csrf_token": csrf_of(c), "value": "1.5"})
    assert response.status_code == 422 and "Valor no válido para el tipo int." in response.text
    assert backend.settings_rows[KEY].value == "3"


def test_htmx_row_swap(login: Login, demo: DemoUsers) -> None:
    c = login(demo.admin)
    token = csrf_of(c)
    ok = c.post(
        f"/admin/settings/{KEY}",
        data={"value": "5"},
        headers={"hx-request": "true", "x-csrf-token": token},
    )
    assert ok.status_code == 200 and "<html" not in ok.text and "Guardado." in ok.text
    bad = c.post(
        f"/admin/settings/{KEY}",
        data={"value": "x"},
        headers={"hx-request": "true", "x-csrf-token": token},
    )
    assert bad.status_code == 422 and "<html" not in bad.text


def test_unknown_key_is_404(login: Login, demo: DemoUsers) -> None:
    c = login(demo.admin)
    response = c.post("/admin/settings/nope", data={"csrf_token": csrf_of(c), "value": "1"})
    assert response.status_code == 404
```

Run: `uv run pytest tests/unit/web/test_web_admin.py -q`
Expected: FAIL (404 for the admin page too, but `test_admin_sees_every_setting` and the update tests fail).

- [ ] **Step 2: Write the routes and register them**

`src/tutor/web/routes/admin.py`:

```python
"""Author-only settings page (requirements section 14)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tutor.domain.dashboard.settings import SettingError, parse_setting
from tutor.domain.dashboard.types import Setting, User
from tutor.web.deps import APP_ROUTER_DEPS, get_deps, require_admin
from tutor.web.views import is_htmx, render

router = APIRouter(dependencies=APP_ROUTER_DEPS)


def _page(request: Request, errors: dict[str, str], status_code: int = 200) -> HTMLResponse:
    return render(
        request,
        "pages/admin_settings.html",
        {
            "settings": get_deps(request).settings.list_settings(),
            "saved": request.query_params.get("saved", ""),
            "errors": errors,
        },
        status_code=status_code,
    )


def _find(request: Request, key: str) -> Setting:
    for setting in get_deps(request).settings.list_settings():
        if setting.key == key:
            return setting
    raise HTTPException(status_code=404)


@router.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: User = Depends(require_admin)) -> HTMLResponse:
    return _page(request, {})


@router.post("/admin/settings/{key}")
async def update_setting(
    request: Request, key: str, user: User = Depends(require_admin), value: str = Form("")
) -> Response:
    current = _find(request, key)
    try:
        canonical = parse_setting(current.type, value)
    except SettingError:
        if is_htmx(request):
            ctx = {"s": current, "error": True, "raw": value, "just_saved": False}
            return render(request, "partials/admin_setting_row.html", ctx, status_code=422)
        return _page(request, {key: value}, status_code=422)
    deps = get_deps(request)
    updated = deps.settings.update_setting(key, canonical, user.id, deps.clock())
    if updated is None:
        raise HTTPException(status_code=404)
    if is_htmx(request):
        ctx = {"s": updated, "error": False, "raw": updated.value, "just_saved": True}
        return render(request, "partials/admin_setting_row.html", ctx)
    return RedirectResponse(f"/admin/settings?saved={quote(key)}", status_code=303)
```

Register `admin.router` in `all_routers` as in Task 25 Step 5.

- [ ] **Step 3: Write the templates**

`src/tutor/web/templates/pages/admin_settings.html`:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "admin" %}
{% block title %}{{ _("Ajustes") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head">
  <h1>{{ _("Ajustes") }}</h1>
  <p class="muted">{{ _("Límites y parámetros del producto. Cada cambio queda en el registro de auditoría.") }}</p>
</header>
<div class="table-wrap">
  <table class="table">
    <caption class="sr-only">{{ _("Ajustes del producto") }}</caption>
    <thead>
      <tr>
        <th scope="col">{{ _("Clave") }}</th>
        <th scope="col">{{ _("Tipo") }}</th>
        <th scope="col">{{ _("Valor") }}</th>
        <th scope="col">{{ _("Actualizado") }}</th>
      </tr>
    </thead>
    <tbody>
      {% for s in settings %}
      {% set error = s.key in errors %}
      {% set raw = errors.get(s.key, s.value) %}
      {% set just_saved = saved == s.key %}
      {% include "partials/admin_setting_row.html" %}
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

`src/tutor/web/templates/partials/admin_setting_row.html`:

```html
<tr id="setting-{{ s.key }}">
  <th scope="row"><code>{{ s.key }}</code><br><span class="muted">{{ s.description }}</span></th>
  <td>{{ s.type }}</td>
  <td>
    <form method="post" action="/admin/settings/{{ s.key|urlencode }}" hx-post="/admin/settings/{{ s.key|urlencode }}" hx-target="#setting-{{ s.key }}" hx-swap="outerHTML" class="inline-form">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <label class="sr-only" for="v-{{ s.key }}">{% trans key=s.key %}Valor de {{ key }}{% endtrans %}</label>
      <input id="v-{{ s.key }}" name="value" value="{{ raw }}"{% if error %} aria-invalid="true" aria-describedby="e-{{ s.key }}"{% endif %}>
      <button class="btn btn--small btn--primary">{{ _("Guardar") }}</button>
      {% if error %}<p class="field-error" id="e-{{ s.key }}">{% trans kind=s.type %}Valor no válido para el tipo {{ kind }}.{% endtrans %}</p>{% endif %}
      {% if just_saved %}<p class="muted" role="status">{{ _("Guardado.") }}</p>{% endif %}
    </form>
  </td>
  <td>{{ s.updated_at.date()|day }}</td>
</tr>
```

Jinja passes loop variables and `{% set %}` values from inside the `for` body to the included template, so the page and the HTMX response share this one row template. Setting keys are `[a-z0-9_]` (the seed and the follow-up migration define them), which keeps them valid in element ids.

- [ ] **Step 4: Add the English translations**

Skip "Ajustes", which Task 10 added; skip "Guardar" and "Guardado." if Task 23 already added them.

```po
msgid "Límites y parámetros del producto. Cada cambio queda en el registro de auditoría."
msgstr "Product limits and parameters. Every change is written to the audit log."

msgid "Ajustes del producto"
msgstr "Product settings"

msgid "Clave"
msgstr "Key"

msgid "Valor"
msgstr "Value"

msgid "Actualizado"
msgstr "Updated"

msgid "Valor de %(key)s"
msgstr "Value of %(key)s"

msgid "Valor no válido para el tipo %(kind)s."
msgstr "Invalid value for type %(kind)s."

msgid "Guardado."
msgstr "Saved."
```

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest tests/unit/web/test_web_admin.py -q` then `uv run just check`
Expected: PASS.

```bash
git add src/tutor/web tests/unit/web/test_web_admin.py
git commit -m "feat(web): author-only settings page"
```

## Part F — Verification

### Task 29: Route sweep (login, CSRF, per-user isolation, template safety)

**Files:**
- Test: `tests/unit/web/test_web_sweep.py`

**Interfaces:**
- Consumes: every router registered in `all_routers` (Tasks 10–28); fixtures `app`, `client`, `login`, `demo`, `backend` and `csrf_of` (Task 10); `ConnectedClient` (Task 1). Path parameter names used by earlier tasks: `{session_id}` (Task 22), `{item_id}` (Task 23), `{client_id}` (Task 26), `{key}` (Task 28). If an earlier task named a parameter differently, the sweep fails with "add an isolation case for <path>"; add the name to `_other_users_ids` rather than renaming the route.
- Produces: the guard every future route must pass. Task 13's `test_web_template_safety.py` already rejects inline scripts, `<style>`, `style=""`, `hx-on`, `on*=` attributes, `|safe` and `Markup(` without a `safe: static` comment; this task does not repeat those checks.

Classification rules the sweep enforces:
- Public routes are exactly: `/`, `/login`, `/privacy`, `/terms`, `/pricing`, `/offline`, `/manifest.webmanifest`, `/sw.js`, `/auth/google`, `/auth/callback`, `/auth/test-login` (test only), `/webhooks/stripe`.
- Everything under `/app`, `/admin` and `/billing` needs login, including the checkout return pages (the learner is logged in when Stripe sends them back, and the pages show their subscription).
- Every non-GET route needs the CSRF token, except `POST /webhooks/stripe` (signature-verified) and `POST /auth/test-login` (test only).

- [ ] **Step 1: Write the sweep**

`tests/unit/web/test_web_sweep.py`:

```python
"""Every route, checked for login, CSRF and per-user isolation (spec 14.1).

New routes are covered automatically; a route with an unknown path parameter fails
until an isolation case is added below.
"""

import re
from collections.abc import Callable
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import ConnectedClient
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend

from .conftest import csrf_of

PUBLIC = {
    "/",
    "/login",
    "/privacy",
    "/terms",
    "/pricing",
    "/offline",
    "/manifest.webmanifest",
    "/sw.js",
    "/auth/google",
    "/auth/callback",
    "/auth/test-login",
    "/webhooks/stripe",
}
PROTECTED_PREFIXES = ("/app", "/admin", "/billing")
CSRF_EXEMPT = {("POST", "/webhooks/stripe"), ("POST", "/auth/test-login")}
NOT_USER_OWNED = {"key"}  # admin setting keys are global and guarded by require_admin
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PARAM = re.compile(r"{(\w+)(?::\w+)?}")

Login = Callable[[UUID], TestClient]


def _routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _fill(path: str, values: dict[str, str]) -> str:
    return PARAM.sub(lambda m: values.get(m.group(1), str(uuid4())), path)


def _other_users_ids(backend: MemoryBackend, demo: DemoUsers) -> dict[str, str]:
    """Ids that belong to Beto only; Ana must get 404 for each."""
    backend.clients[demo.beto].append(ConnectedClient("beto-only", "Claude Code", None))
    return {
        "session_id": str(backend.session_rows[demo.beto][0].summary.id),
        "item_id": str(backend.glossaries[demo.beto][0].id),
        "client_id": "beto-only",
    }


def test_every_route_is_classified(app: FastAPI) -> None:
    for route in _routes(app):
        public = route.path in PUBLIC
        protected = route.path.startswith(PROTECTED_PREFIXES)
        assert public != protected, (
            f"classify {route.path}: list it in PUBLIC or move it under /app, /admin or /billing"
        )


def test_protected_pages_redirect_anonymous_visitors(app: FastAPI, client: TestClient) -> None:
    checked = 0
    for route in _routes(app):
        if "GET" not in route.methods or not route.path.startswith(PROTECTED_PREFIXES):
            continue
        path = _fill(route.path, {})
        page = client.get(path)
        assert page.status_code == 303, f"GET {path} -> {page.status_code}"
        assert page.headers["location"].startswith("/login?next="), path
        partial = client.get(path, headers={"hx-request": "true"})
        assert partial.status_code == 401, f"HTMX GET {path} -> {partial.status_code}"
        assert partial.headers["hx-redirect"].startswith("/login?next="), path
        checked += 1
    assert checked >= 15


def test_unsafe_methods_need_the_csrf_token(app: FastAPI, login: Login, demo: DemoUsers) -> None:
    c = login(demo.ana)
    checked = 0
    for route in _routes(app):
        for method in sorted(route.methods & UNSAFE_METHODS):
            if (method, route.path) in CSRF_EXEMPT:
                continue
            response = c.request(method, _fill(route.path, {}))
            assert response.status_code == 403, (
                f"{method} {route.path} answered {response.status_code} without a CSRF token"
            )
            checked += 1
    assert checked >= 8


def test_another_users_ids_are_not_found(
    app: FastAPI, login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    beto = _other_users_ids(backend, demo)
    c = login(demo.ana)
    token = csrf_of(c)
    form = {"csrf_token": token, "meaning": "x", "context_sentence": "Una frase.", "value": "1"}
    checked = 0
    for route in _routes(app):
        names = PARAM.findall(route.path)
        if not names:
            continue
        unknown = [n for n in names if n not in beto and n not in NOT_USER_OWNED]
        assert not unknown, (
            f"add an isolation case for {route.path} (parameters {unknown}) "
            "in _other_users_ids in test_web_sweep.py"
        )
        if any(n in NOT_USER_OWNED for n in names):
            continue
        path = _fill(route.path, beto)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            data = None if method == "GET" else form
            response = c.request(method, path, data=data)
            assert response.status_code == 404, (
                f"{method} {path} answered {response.status_code} for another learner's id"
            )
            checked += 1
    assert backend.clients[demo.beto][-1].id == "beto-only"
    assert checked >= 4


def test_own_ids_still_work(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    c = login(demo.ana)
    own_session = backend.session_rows[demo.ana][0].summary.id
    assert c.get(f"/app/sessions/{own_session}").status_code == 200
```

- [ ] **Step 2: Run the sweep**

Run: `uv run pytest tests/unit/web/test_web_sweep.py -q`
Expected: PASS. A failure names the route and what is missing; fix the route (add `APP_ROUTER_DEPS`, `current_user`, or a per-user lookup that returns 404), never the expectation. If a glossary POST answers 422 instead of 404, the route validates the form before checking ownership; make it look up the item first, or add the missing field name to `form` above if the route needs a field the sweep does not send.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/web/test_web_sweep.py
git commit -m "test(web): route sweep for login, CSRF, isolation and safe markup"
```

### Task 30: Browser tests, accessibility, budgets and CI

**Files:**
- Modify: `pyproject.toml` (`e2e` marker, mypy override)
- Modify: `justfile` (exclude `e2e` from `test`, `check-fast`, `check`; add `test-e2e`)
- Modify: `src/tutor/web/demo_server.py` (public `backend` and `demo`, `reset()`)
- Modify: `.github/workflows/ci.yml` (append the `e2e` job)
- Create: `tests/e2e/__init__.py`, `tests/e2e/conftest.py`
- Create: `tests/e2e/test_e2e_flows.py`, `tests/e2e/test_e2e_accessibility.py`, `tests/e2e/test_e2e_motion.py`, `tests/e2e/test_e2e_performance.py`

**Interfaces:**
- Consumes: `tutor.web.demo_server:app` (Task 12); the browser fixture `browser` from pytest-playwright; texts and labels from earlier tasks (listed below). If an earlier task chose a different label, change the selector here and note it in the ledger; do not change user-facing copy to fit a test.
  - Task 20: `/app/connect` shows "Esperando tu primera sesión"; Inicio for a first-day learner links to `/app/connect`.
  - Task 21: Inicio renders `.trail` with seven `.trail__node`, the streak in `.streak`, and `[data-celebrate]` only on the first visit after a new closed session (the route calls `mark_celebrated`).
  - Task 22: session list links to `/app/sessions/<id>`; the detail page renders `.said` items.
  - Task 23: a `type="search"` input (role `searchbox`); each table row has a button whose name starts with "Editar"; the edit form has a field labelled "Significado" and a "Guardar" button; a link whose name contains "CSV".
  - Task 14: `<link rel="manifest" href="/manifest.webmanifest…">`; the service worker serves `/offline`, which shows "Sin conexión".
  - Task 18: `/billing/checkout` has a "Pagar" button; the return page shows "Confirmando tu pago" while waiting and "Tu plan Annual está activo" when active.
  - Task 26: "Descargar mis datos", "Escribe ELIMINAR para confirmar", "Eliminar mi cuenta", "Cerrar sesión", "Solicitud recibida".
- Produces: `uv run just test-e2e`; CI job `e2e`.

Every browser context below uses `reduced_motion="reduce"` unless a test is about motion, so screenshots and timings are stable. Axe contexts use `bypass_csp=True` because axe is injected as a script, which the page's CSP (correctly) forbids; nothing else bypasses the CSP.

- [ ] **Step 1: Register the marker and keep e2e out of the fast gates**

In `pyproject.toml`, add to `markers` under `[tool.pytest.ini_options]`:

```toml
    "e2e: Playwright browser tests against the demo server (just test-e2e)",
```

and append:

```toml
# axe-playwright-python ships no type information.
[[tool.mypy.overrides]]
module = ["axe_playwright_python", "axe_playwright_python.*"]
ignore_missing_imports = true
```

In `justfile`, make these exact replacements:

- In `test`: `uv run pytest -m "not integration and not eval" -q` → `uv run pytest -m "not integration and not eval and not e2e" -q`
- In `check-fast`: `uv run pytest -m "not integration and not eval" -q -x` → `uv run pytest -m "not integration and not eval and not e2e" -q -x`
- In `check`: `uv run pytest -m "not eval" -q --cov=tutor --cov-report=term` → `uv run pytest -m "not eval and not e2e" -q --cov=tutor --cov-report=term`

and append the recipe:

```just
# Browser tests (Playwright + axe) against the in-memory demo; installs Chromium if missing
test-e2e:
    uv run playwright install chromium
    uv run pytest -m e2e -q
```

`playwright install chromium` is a no-op when the pinned Chromium is already installed, so the recipe is safe to run repeatedly.

Run: `uv run just check-fast`
Expected: PASS (no e2e tests exist yet).

- [ ] **Step 2: Make the demo server resettable**

Replace the body of `src/tutor/web/demo_server.py` with:

```python
"""`just dashboard-demo` and the browser tests: the dashboard over in-memory demo data.
No Google, no Stripe. `backend` and `demo` are public so e2e tests can change state."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import DemoUsers, seed_demo
from tutor.web.memory import MemoryBackend, memory_deps
from tutor.web.ratelimit import DailyLimiter

backend = MemoryBackend()
demo: DemoUsers = seed_demo(backend, datetime.now(UTC).date())
app = create_app(
    memory_deps(backend, lambda: datetime.now(UTC)),
    WebConfig(
        env="test",
        base_url="http://localhost:8780",
        mcp_url="http://localhost:8780/mcp",
        support_email="soporte@example.test",
        test_login=True,
    ),
)


def reset() -> DemoUsers:
    """Fresh demo data in the same backend object; the running server keeps serving it."""
    global demo
    fresh = MemoryBackend()
    for f in fields(MemoryBackend):
        setattr(backend, f.name, getattr(fresh, f.name))
    demo = seed_demo(backend, datetime.now(UTC).date())
    app.state.export_limiter = DailyLimiter(5)
    return demo
```

- [ ] **Step 3: Write the e2e fixtures**

`tests/e2e/__init__.py`: empty file (gives the e2e `conftest` its own package name next to `tests/unit/web/conftest.py`).

`tests/e2e/conftest.py`:

```python
"""Starts the demo server once per session; each test gets fresh demo data."""

import re
import socket
import threading
import time
from collections.abc import Callable, Iterator

import pytest
import uvicorn
from playwright.sync_api import Browser, BrowserContext, Page

from tutor.web import demo_server

VIEWPORTS = {"phone": {"width": 390, "height": 844}, "desktop": {"width": 1280, "height": 800}}
APP_PAGES = (
    "/app/",
    "/app/plan",
    "/app/progress",
    "/app/sessions",
    "/app/glossary",
    "/app/reports",
    "/app/account",
    "/app/connect",
)
PUBLIC_PAGES = ("/login", "/pricing", "/privacy", "/terms")

MakePage = Callable[..., Page]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def server() -> Iterator[str]:
    port = _free_port()
    srv = uvicorn.Server(
        uvicorn.Config(demo_server.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("demo server did not start")
        time.sleep(0.05)
    # localhost (not 127.0.0.1) so the Secure cookie and the service worker get a secure context
    yield f"http://localhost:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def fresh_demo() -> None:
    demo_server.reset()


def login_as(page: Page, server: str, name: str) -> None:
    page.goto(f"{server}/auth/test-login")
    page.get_by_role("button", name=re.compile(rf"^{name} ·")).click()
    page.wait_for_url(f"{server}/app/")


@pytest.fixture
def make_page(browser: Browser, server: str) -> Iterator[MakePage]:
    contexts: list[BrowserContext] = []

    def _make(
        viewport: str = "phone",
        motion: str = "reduce",
        user: str | None = "Ana",
        bypass_csp: bool = False,
    ) -> Page:
        context = browser.new_context(
            viewport=VIEWPORTS[viewport],  # type: ignore[arg-type]
            reduced_motion=motion,  # type: ignore[arg-type]
            locale="es-MX",
            accept_downloads=True,
            bypass_csp=bypass_csp,
        )
        contexts.append(context)
        page = context.new_page()
        if user is not None:
            login_as(page, server, user)
        return page

    yield _make
    for context in contexts:
        context.close()
```

- [ ] **Step 4: Write the flow tests**

`tests/e2e/test_e2e_flows.py`:

```python
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tutor.domain.dashboard.types import BillingEvent, BillingEventKind
from tutor.web import demo_server

from .conftest import APP_PAGES, PUBLIC_PAGES, MakePage

pytestmark = pytest.mark.e2e


def test_first_day_learner_is_sent_to_connect(make_page: MakePage, server: str) -> None:
    page = make_page(user="Nuevo")
    expect(page.locator('a[href="/app/connect"]').first).to_be_visible()
    page.goto(f"{server}/app/connect")
    expect(page.get_by_text("Esperando tu primera sesión")).to_be_visible()


def test_home_shows_the_week(make_page: MakePage) -> None:
    page = make_page()
    expect(page.locator(".trail .trail__node")).to_have_count(7)
    expect(page.locator(".streak")).to_contain_text("4")


def test_glossary_filter_edit_and_csv(make_page: MakePage, server: str) -> None:
    page = make_page()
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(f"{server}/app/glossary")
    page.get_by_role("searchbox").fill("trade")
    table = page.locator("table")
    expect(table).not_to_contain_text("on track")
    expect(table).to_contain_text("trade-off")

    hostile = "<img src=x onerror=alert(1)>"
    row = page.get_by_role("row").filter(has_text="trade-off")
    row.get_by_role("button", name=re.compile("^Editar")).click()
    page.get_by_label("Significado").fill(hostile)
    page.get_by_role("button", name="Guardar").click()
    expect(table).to_contain_text(hostile)  # Review Focus 1: shown as text
    assert dialogs == []

    with page.expect_download() as download:
        page.get_by_role("link", name=re.compile("CSV")).click()
    content = Path(download.value.path()).read_text(encoding="utf-8")
    assert content.startswith("﻿text,kind")


def test_session_detail(make_page: MakePage, server: str) -> None:
    page = make_page()
    page.goto(f"{server}/app/sessions")
    page.locator('a[href^="/app/sessions/"]').first.click()
    expect(page.locator(".said").first).to_be_visible()


def test_checkout_return_waits_for_the_webhook(make_page: MakePage, server: str) -> None:
    page = make_page()
    back = f"{server}/billing/return?session_id=cs_test_e2e"
    page.route(
        "https://checkout.stripe.test/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=f'<a id="back" href="{back}">Stripe test checkout</a>',
        ),
    )
    page.goto(f"{server}/billing/checkout")
    page.get_by_role("button", name="Pagar").click()
    page.locator("#back").click()
    expect(page.get_by_text("Confirmando tu pago")).to_be_visible()
    demo_server.backend.apply_event(
        demo_server.demo.ana,
        BillingEvent(
            "evt_e2e",
            BillingEventKind.CHECKOUT_COMPLETED,
            datetime.now(UTC),
            "cus_e2e",
            "sub_e2e",
            demo_server.demo.ana,
            True,
            None,
        ),
    )
    expect(page.get_by_text("Tu plan Annual está activo")).to_be_visible(timeout=10_000)


def test_export_download(make_page: MakePage, server: str) -> None:
    page = make_page()
    page.goto(f"{server}/app/account")
    with page.expect_download() as download:
        page.get_by_role("link", name="Descargar mis datos").click()
    data = json.loads(Path(download.value.path()).read_text(encoding="utf-8"))
    assert data["user"]["display_name"] == "Ana"


def test_account_deletion(make_page: MakePage, server: str) -> None:
    page = make_page()
    page.goto(f"{server}/app/account")
    page.get_by_label("Escribe ELIMINAR para confirmar").fill("ELIMINAR")
    page.get_by_role("button", name="Eliminar mi cuenta").click()
    expect(page.get_by_role("heading", name="Solicitud recibida")).to_be_visible()
    page.goto(f"{server}/app/")
    expect(page).to_have_url(re.compile(r"/login"))


def test_installable_shell_and_offline_page(make_page: MakePage, server: str) -> None:
    page = make_page()
    href = page.locator('link[rel="manifest"]').get_attribute("href")
    assert href is not None and href.startswith("/manifest.webmanifest")
    assert page.evaluate("navigator.serviceWorker.ready.then(r => r.active !== null)")
    page.context.set_offline(True)
    try:
        page.goto(f"{server}/app/glossary")
        expect(page.get_by_text("Sin conexión")).to_be_visible()
        assert "trade-off" not in page.content()
    finally:
        page.context.set_offline(False)


def test_back_button_after_logout_shows_no_personal_data(make_page: MakePage, server: str) -> None:
    page = make_page()
    page.goto(f"{server}/app/glossary")
    expect(page.locator("table")).to_contain_text("trade-off")
    page.goto(f"{server}/app/account")
    page.get_by_role("button", name="Cerrar sesión").click()
    expect(page).to_have_url(re.compile(r"/login"))
    page.go_back()
    expect(page).to_have_url(re.compile(r"/login"))
    page.go_back()
    expect(page).to_have_url(re.compile(r"/login"))
    assert "trade-off" not in page.content()


@pytest.mark.parametrize("viewport", ["phone", "desktop"])
@pytest.mark.parametrize("path", [*APP_PAGES, *PUBLIC_PAGES])
def test_no_horizontal_scroll(make_page: MakePage, server: str, viewport: str, path: str) -> None:
    page: Page = make_page(viewport=viewport, user=None if path in PUBLIC_PAGES else "Ana")
    page.goto(f"{server}{path}")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), path
```

In the logout test the phone viewport hides the sidebar, so exactly one "Cerrar sesión" button (on the Cuenta page) is visible; role queries ignore hidden elements.

- [ ] **Step 5: Write the accessibility tests**

`tests/e2e/test_e2e_accessibility.py`:

```python
import pytest
from axe_playwright_python.sync_playwright import Axe

from .conftest import APP_PAGES, PUBLIC_PAGES, MakePage

pytestmark = pytest.mark.e2e
AXE = Axe()
WCAG_AA = {"runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]}}


def _check(page_factory: MakePage, server: str, viewport: str, user: str | None, path: str) -> None:
    page = page_factory(viewport=viewport, user=user, bypass_csp=True)
    page.goto(f"{server}{path}")
    page.wait_for_load_state("networkidle")
    results = AXE.run(page, options=WCAG_AA)
    assert results.violations_count == 0, f"{path} ({viewport}):\n{results.generate_report()}"


@pytest.mark.parametrize("viewport", ["phone", "desktop"])
@pytest.mark.parametrize("path", APP_PAGES)
def test_app_pages(make_page: MakePage, server: str, viewport: str, path: str) -> None:
    _check(make_page, server, viewport, "Ana", path)


@pytest.mark.parametrize("viewport", ["phone", "desktop"])
@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_pages(make_page: MakePage, server: str, viewport: str, path: str) -> None:
    _check(make_page, server, viewport, None, path)


@pytest.mark.parametrize("viewport", ["phone", "desktop"])
def test_first_day_and_admin(make_page: MakePage, server: str, viewport: str) -> None:
    _check(make_page, server, viewport, "Nuevo", "/app/")
    _check(make_page, server, viewport, "Admin", "/admin/settings")


@pytest.mark.parametrize("viewport", ["phone", "desktop"])
def test_session_detail(make_page: MakePage, server: str, viewport: str) -> None:
    page = make_page(viewport=viewport, bypass_csp=True)
    page.goto(f"{server}/app/sessions")
    href = page.locator('a[href^="/app/sessions/"]').first.get_attribute("href")
    assert href is not None
    page.goto(f"{server}{href}")
    page.wait_for_load_state("networkidle")
    results = AXE.run(page, options=WCAG_AA)
    assert results.violations_count == 0, results.generate_report()
```

Before writing this file, confirm the installed `axe-playwright-python` API (`Axe().run(page, options=...)`, `violations_count`, `generate_report()`) by reading the package source under `.venv/Lib/site-packages/axe_playwright_python/`. If it differs, adapt the three calls. If the package is unusable, fall back without a new dependency: download `axe.min.js` from the pinned `axe-core` npm release into `tests/e2e/vendor/axe.min.js` (record version and SHA-256 in `tests/e2e/vendor/VENDORED.md`), then in `_check` call `page.add_script_tag(path=...)` and `page.evaluate("axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}})")` and assert the returned `violations` list is empty.

- [ ] **Step 6: Write the motion tests**

`tests/e2e/test_e2e_motion.py`:

```python
from dataclasses import replace

import pytest
from playwright.sync_api import expect

from tutor.web import demo_server

from .conftest import MakePage

pytestmark = pytest.mark.e2e
RUNNING = "document.getAnimations().filter(a => a.playState === 'running').length"


@pytest.mark.parametrize("path", ["/app/", "/app/progress", "/app/glossary"])
def test_reduced_motion_runs_no_animation(make_page: MakePage, server: str, path: str) -> None:
    page = make_page(motion="reduce")
    page.goto(f"{server}{path}")
    page.wait_for_load_state("networkidle")
    assert page.evaluate(RUNNING) == 0


def test_server_side_reduce_motion_preference_wins(make_page: MakePage, server: str) -> None:
    ana = demo_server.demo.ana
    demo_server.backend.users[ana] = replace(demo_server.backend.users[ana], reduce_motion=True)
    page = make_page(motion="no-preference")
    page.goto(f"{server}/app/progress")
    page.wait_for_load_state("networkidle")
    assert page.evaluate(RUNNING) == 0


def test_counters_end_on_the_real_value(make_page: MakePage, server: str) -> None:
    page = make_page(motion="no-preference")
    page.goto(f"{server}/app/progress")
    page.wait_for_timeout(2500)
    mismatches = page.evaluate(
        """() => [...document.querySelectorAll('[data-count-to]')]
             .filter(el => el.textContent.trim() !==
                     el.parentElement.querySelector('.sr-only').textContent.trim())
             .map(el => el.outerHTML)"""
    )
    assert mismatches == []


def test_ambient_motion_stops_within_five_seconds(make_page: MakePage, server: str) -> None:
    page = make_page(motion="no-preference")
    page.wait_for_timeout(5500)
    assert page.evaluate(RUNNING) == 0


def test_celebration_fires_once(make_page: MakePage, server: str) -> None:
    page = make_page(motion="no-preference")
    expect(page.locator("[data-celebrate]")).to_have_count(1)
    page.reload()
    expect(page.locator("[data-celebrate]")).to_have_count(0)
```

- [ ] **Step 7: Write the performance test**

`tests/e2e/test_e2e_performance.py`:

```python
import pytest

from .conftest import MakePage

pytestmark = pytest.mark.e2e

LCP = """() => new Promise((resolve) => {
  let lcp = 0;
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) lcp = entry.startTime;
  }).observe({type: 'largest-contentful-paint', buffered: true});
  setTimeout(() => resolve(lcp), 1000);
})"""

CLS = """() => new Promise((resolve) => {
  let cls = 0;
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) if (!entry.hadRecentInput) cls += entry.value;
  }).observe({type: 'layout-shift', buffered: true});
  setTimeout(() => resolve(cls), 1000);
})"""


def test_home_meets_lcp_and_cls_budgets_on_a_slow_phone(make_page: MakePage, server: str) -> None:
    page = make_page(viewport="phone")
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send(
        "Network.emulateNetworkConditions",
        {
            "offline": False,
            "latency": 150,
            "downloadThroughput": 1_600_000 / 8,
            "uploadThroughput": 750_000 / 8,
        },
    )
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
    page.goto(f"{server}/app/")
    page.wait_for_load_state("networkidle")
    lcp_ms = page.evaluate(LCP)
    cls = page.evaluate(CLS)
    assert lcp_ms <= 2500, f"LCP {lcp_ms:.0f} ms"
    assert cls <= 0.1, f"CLS {cls:.3f}"
```

Playwright's `evaluate` runs through the DevTools protocol, so the page's CSP does not block these observers.

- [ ] **Step 8: Run the browser suite locally**

Run: `uv run just test-e2e`
Expected: PASS. On a failure, read the assertion: a label or text mismatch with Tasks 14–26 is fixed in the selector (ledger the deviation); an axe violation, horizontal scroll, running animation under reduced motion or a budget miss is a product defect and is fixed in the page, CSS or `app.js`.

- [ ] **Step 9: Add the CI job**

Append to `.github/workflows/ci.yml`, under `jobs:` at the same indentation as `check:`:

```yaml
  e2e:
    # Browser tests run on pull requests that touch the dashboard.
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
      - name: Detect dashboard changes
        id: changes
        run: |
          if git diff --name-only "origin/${{ github.base_ref }}...HEAD" | grep -qE '^src/tutor/(web|domain/dashboard)/|^tests/e2e/'; then
            echo "run=true" >> "$GITHUB_OUTPUT"
          else
            echo "run=false" >> "$GITHUB_OUTPUT"
          fi
      - uses: astral-sh/setup-uv@v10
        if: steps.changes.outputs.run == 'true'
        with:
          enable-cache: true
      - if: steps.changes.outputs.run == 'true'
        run: uv sync --locked
      - if: steps.changes.outputs.run == 'true'
        run: uv run playwright install --with-deps chromium
      - if: steps.changes.outputs.run == 'true'
        run: uv run just test-e2e
```

The `check` job is unchanged and, through the `justfile` edit in Step 1, no longer collects e2e tests.

- [ ] **Step 10: Run the full gate and commit**

Run: `uv run just check` then `uv run just test-e2e`
Expected: both PASS.

```bash
git add pyproject.toml justfile .github/workflows/ci.yml src/tutor/web/demo_server.py tests/e2e
git commit -m "test(web): browser flows, accessibility, motion and performance checks in CI"
```
