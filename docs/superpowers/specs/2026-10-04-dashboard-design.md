# Dashboard design: installable web dashboard with a motion layer

Status: draft, awaiting author approval. **Parked:** nothing here is built before the gate decision on 2026-11-30; implementation starts 2027-01-05 only if the gate passes, and scope is re-estimated at the gate (section 16).
Date: 2026-10-04
Scope: requirements section 1 ("minimal web dashboard: login, plan, progress, glossary, billing"), section 4 (dashboard stack), section 5 (dashboard session, export, deletion), section 10 (glossary table), section 11 (metrics, weekly report, monthly checkpoint), section 13 (billing), section 14 (accessibility, localization, admin settings) and the "Dashboard, billing, weekly report" row of section 16. The weekly email, the worker jobs and the plan engine have their own specs.

Mockups (reference only, not product code): `2026-10-04-dashboard-mockups/directions.html` (three visual directions; C was chosen) and `2026-10-04-dashboard-mockups/layout.html` (two layouts; A was chosen for desktop). Private claude.ai copies: https://claude.ai/artifact/DgJBErimcPxvEE6FPkebx9 and https://claude.ai/artifact/RujAAm7qdSTAgt9KDkyzn7.

## 1. Goal and success

A learner opens one page, on a phone or a laptop, and sees in under ten seconds: what to practise today, how the week is going, and whether the evidence says they are improving. They can manage their glossary, their subscription and their data without writing to support.

The dashboard is done when:

1. The section 16 acceptance items it owns pass: Stripe annual checkout and webhooks reconcile; free-plan caps are shown correctly; privacy notice, terms, export and deletion are live.
2. Every page in section 6 renders for the first-day, normal, Free-limit, payment-failed, lapsed and deletion-pending states.
3. It installs from the browser on Android (Chrome) and iPhone (Safari "Agregar a pantalla de inicio"), and on desktop Chrome and Edge, with no app store.
4. Google login and the Stripe Checkout return both complete inside the installed app on a real iPhone and a real Android phone.
5. Automated axe checks report no WCAG 2.1 AA violations, and the performance budgets in section 14 hold.
6. With animations off (reduced motion, or JavaScript blocked), every page is complete and correct.

## 2. Decisions at a glance

| Topic | Decision |
| --- | --- |
| Timing | Spec and plan now; build 2027-01-05 → 2027-01-30, only after the gate passes |
| Stack | FastAPI + Jinja2 + HTMX (section 4, unchanged). No SPA, no JavaScript build step, no Node |
| Motion | Plain CSS (keyframes, SVG stroke drawing), the browser's View Transitions, and Motion (motion.dev) for springs and counters. Remotion rejected: it renders videos, not interactive pages. GSAP and Lottie rejected to stay within the JavaScript budget |
| Visual direction | C, "Practice trail", with adult-tone guardrails (section 4) |
| Layout | Desktop and tablet: A, side menu with everything visible. Phone: bottom tab bar |
| App delivery | Installable PWA shell: manifest, icons, standalone mode, cached static assets, offline page. No personal data on the device, no push, no app store |
| Charts | SVG drawn by the server from points the server computes; no chart library |
| Payments | Stripe Checkout (hosted) + Stripe Customer Portal; webhooks are the only writer of subscription state |
| Language | es-MX first, English second; gettext through Jinja; locale-aware numbers and dates |
| Admin | One plain, author-only page to edit the settings table (section 14). Product analytics page deferred |
| Data access | Web routes call the same service layer as the REST API, filtered by the session's user; no SQL in routes or templates (section 9.1) |

## 3. Scope, build order and dependencies

### 3.1 Build order (Jan 5–30)

Ordered so the section 16 acceptance items land first. Motion is progressive enhancement from the first commit, never a prerequisite.

| Slice | Content | Cut if late? |
| --- | --- | --- |
| S0 | Real-device check of Google login and Stripe return in an installed PWA (section 8.4); Context7 checks (section 14.6); ADR for the dashboard stack and PWA | No |
| S1 | Shell, login and session cookie, CSRF and CSP, i18n, design tokens, manifest, service worker, offline page, Conectar page, empty states | No |
| S2 | Billing: pricing, checkout, webhooks, return and pending pages, Customer Portal, banners, Free-cap display | No |
| S3 | Cuenta: export, deletion, email preferences; privacy notice and terms; admin settings page | No |
| S4 | Inicio, Sesiones (list and detail), Glosario with CSV export | Inicio and Glosario no; session detail extras yes |
| S5 | Plan, Progreso, Reportes | Plan diff and Progreso secondary charts yes |
| S6 | Motion layer polish: trail, stamps, celebration, View Transitions | Yes, first to go |

Cut rule: if S4 is not done by 2027-01-22, S6 and the cut-allowed parts of S5 move to after the private beta. S0–S3 are never cut.

### 3.2 Dependencies on earlier phases

- Core loop (Oct 12–Nov 6): `sessions`, `session_metrics`, `glossary_items`, `review_states`, streak computation, OAuth server.
- Plan engine and metrics (Dec 1–23): `plans`, `plan_items`, plan versions and diffs, metric aggregates, monthly checkpoint.
- Worker (Jan, separate spec): `weekly_reports` rows, deletion purge job, nightly Stripe reconciliation.
- Data model additions this design needs are listed in section 9.7.

## 4. Visual direction and tone

Direction C, "Practice trail": the week is a path of practice days, and the real phrases the learner has used are stamped along it.

**Guardrails (requirements section 2, principle 4 and "no gamification beyond streak count"):**

- The streak is the only game-like element. No badges, points, levels, collections, leaderboards or sharing.
- Stamps show real chunk data only: a phrase the learner was offered, filled once they used it in a session. Nothing is earned, unlocked or collected.
- Celebrations only for real events: a finished session and an activated Annual plan (section 10).
- Missed days are shown plainly ("sin sesión"), never as loss or shame. No "perdiste tu racha".
- Adult copy in es-MX, using "tú", at most one exclamation mark per page. No mascots.
- The CEFR line is always labelled "opinión del modelo"; a level is stated only in the monthly checkpoint (section 11).

**Tokens (light theme, from the mockups):**

| Token | Value | Use |
| --- | --- | --- |
| `--paper` | `#e3f4ea` | Page background |
| `--ink` | `#11402c` | Text, sidebar, primary buttons |
| `--leaf` | `#2f9e66` | Done states, positive trends, focus ring |
| `--coral` | `#ff6f4f` | Today marker and accents; never body text |
| `--sun` | `#ffcc33` | Streak pill and highlights; never body text |
| `--card` | `#ffffff` | Cards |

A dark theme follows `prefers-color-scheme` with tokens chosen to pass the same contrast checks; there is no manual theme toggle. Every text and background pair must reach 4.5:1 (3:1 for large text and UI outlines); coral and sun carry no text unless a checked pair is defined.

**Type:** Baloo 2 (headings, numbers) and Public Sans (body, tables), both OFL, self-hosted as Latin-subset woff2. No Google Fonts requests.

## 5. Layout and navigation

- **Desktop and tablet (≥ 900 px):** layout A. A deep-ink sidebar with Inicio, Plan, Progreso, Sesiones, Glosario, Reportes and Cuenta; the plan chip (Free or Annual) and, on Free, the meter "2 de 3 sesiones esta semana". Content is a card grid.
- **Phone (< 900 px):** one column, today first. A bottom tab bar with Inicio, Plan, Progreso, Glosario and Más (Sesiones, Reportes, Cuenta). The last session card on Inicio also links to Sesiones.
- **Shell elements:** plan chip, ES/EN switch, logout. Admin link only for the author.
- **Touch and safe areas:** targets ≥ 44 × 44 px; `env(safe-area-inset-*)` padding under the tab bar and the iPhone notch; no hover-only controls.
- **One template set:** the same Jinja templates serve both layouts; CSS switches sidebar and tab bar at the breakpoint.

## 6. Page inventory

Each page lists what it shows and where the data comes from. Every number comes from the server; templates only format it.

### 6.1 Public pages (no login)

- **Login:** "Entrar con Google", a one-line value proposition and links to pricing, privacy and terms.
- **Precios:** Free vs Annual from section 3: limits and features. It also says what the product does not do (no audio scoring, no level certificate), that there is no CFDI invoicing in v1, and that there is a 14-day refund. Annual price: see open question Q1.
- **Aviso de privacidad** and **Términos:** static pages; the privacy notice says that lesson content passes through the learner's LLM vendor.

### 6.2 Conectar (onboarding, and later from Cuenta)

- The MCP server URL with a copy button.
- Steps per client: Claude web/desktop and mobile (custom connector), and Claude Code (`claude mcp add --transport http …`).
- An "Abrir Claude" button and the first phrase to say: `start my lesson`.
- A waiting state "Esperando tu primera sesión" that checks every 10 s for up to 10 minutes, then stops and offers a "Revisar de nuevo" button.

### 6.3 Inicio

- **Today card:** the next pending plan item (can-do text in Spanish, scenario hint). It has a copy button for `start my lesson`, and a prep hint with an example such as `prepare me for tomorrow's demo`.
- **Week trail:** one node per planned day. Each node is done (session closed), today, upcoming, or without a session. Off-plan prep sessions appear as small side nodes.
- **Streak pill:** the server's streak count.
- **Phrase stamps:** this week's offered chunks, each filled when used (see Q2).
- **Last session:** date, mode, duration, words per minute, task result, status chip; it links to the session detail.
- **Pendiente:** reviews due (count only; reviews happen in the lesson) and provisional items waiting for confirmation. Provisional items are read-only here, with the note "se te preguntará al iniciar tu próxima sesión".
- **Weekly report preview** (Annual): the four numbers of the latest report, linked to Reportes.

### 6.4 Plan (read-only)

- The current week and the next three weeks of `plan_items`, each with status (pending, done, skipped), skill, domain and scenario hint.
- The rationale and the realism message: when the target is unreachable, show the proposed milestone exactly as the planner wrote it.
- Version history with a diff between consecutive versions: items added, removed and moved.
- Changing the plan happens in chat. The page shows example phrases: `skip today's item`, `add interview prep next week`, `regenerate my plan`.

### 6.5 Progreso

All values computed server-side (section 11). Sessions with status `incomplete` are excluded from trends; `low_trust` sessions keep their words and turns but drop their errors.

- **Words per minute:** voice and text shown separately against their targets (≥ 25 voice, ≥ 12 text).
- **Errors per 100 words:** trend.
- **Recurring errors that stopped:** with the correct form.
- **Activation rate:** against the 40% target.
- **Minutes spoken:** per week.
- **Sessions done vs planned:** per week.
- **CEFR speaking trend:** 10-session moving trend, labelled "opinión del modelo". Low-confidence points count at half weight. Excluded jumps are marked, not drawn into the line.
- **Self-rated confidence trend:** labelled "tu autoevaluación".
- **Monthly checkpoint:** the measured level against the model's trend, in the section 11 wording. This is the only place a level is stated.
- **Parked leeches:** listed.
- **Fidelity note:** voice-session evidence is assumed as accurate as text until a transcript is available (section 11).

Each chart has a "Ver como tabla" disclosure with the same numbers.

### 6.6 Sesiones

- **List:** date, mode, client, plan item or prep event, duration, words per minute, task result and a status chip (completa, incompleta, baja confianza). Paginated, 20 per page, filterable by mode and status.
- **Detail:**
  - errors as "dijiste → mejor", each with its category;
  - uptake (corrections reused later in the session);
  - hints given and task result;
  - chunks offered and used;
  - the CEFR opinion with its confidence and evidence phrases;
  - the learner's turns, shown as escaped text.
  User text is data: it is never marked safe, never linkified and never interpreted.

### 6.7 Glosario

- A table filterable by kind, domain, status and due date, with text search (section 10).
- Labels: review due, leech, provisional (with its expiry date), archived ("parked").
- Inline edit of `meaning` and `context_sentence`. It saves through HTMX, and the server validates length and strips control characters.
- CSV export of the filtered view.
- On Free, a counter "34 de 50".
- No review UI in v1 (section 10).

### 6.8 Reportes

- **Annual:** the list of weekly reports from `weekly_reports`. Each report shows its four numbers and three lines, in the same layout as the email. Monthly checkpoints appear in the same list.
- **Free:** a plain description of what the weekly report contains, plus the Annual offer. No example numbers presented as if they were the learner's.

### 6.9 Cuenta

- **Profile:** name, domains, minutes per day, days per week, target. Read-only in v1 with the note "cámbialo desde el chat" (see Q5).
- **Interface language:** ES or EN.
- **"Reducir animaciones":** server-side preference that adds to the system setting.
- **Email preferences:** weekly report on or off; reminders on or off.
- **Connected clients:** the OAuth clients that hold tokens for this user, with last use and a "Desconectar" button that revokes that client's refresh tokens.
- **Subscription:** plan, status, renewal date, "Gestionar pago" (Customer Portal) and refund instructions with a copyable support email.
- **Export my data:** JSON download.
- **Delete account:** in-page confirmation by typing "ELIMINAR"; no browser dialog. Shows what will be deleted, that an active Annual plan is cancelled without renewal, the 14-day refund window, and that the purge completes within 24 h. After confirming: logout and a "solicitud recibida" page.

### 6.10 Admin (author only)

- A plain, unstyled-but-accessible table of the settings table (section 14): key, value, type and description.
- Edits are validated by type and written to `audit_log`.
- Reached only by users with the admin role; for everyone else the route returns 404, not 403.

## 7. Payments

The frontend displays subscription state; it never decides it.

### 7.1 Flow

1. **Entry points:**
   - Precios.
   - The Free meter in the shell.
   - Banners when a Free limit is reached: 3 of 3 sessions this week, 50 of 50 glossary items, a weekly report available on Annual.
   - Cuenta.
   No countdowns, pre-checked boxes, confirmshaming or hidden cancellation.
2. **Pre-pay summary page:**
   - the learner's cohort price;
   - what Annual unlocks (unlimited sessions and glossary, weekly report, three domains);
   - the 14-day refund;
   - Stripe Tax applies;
   - no CFDI in v1;
   - accepted methods: card, plus OXXO and SPEI only if enabled (Q3).
3. **Checkout:** "Pagar" posts to the server. The server creates a Checkout Session for the cohort's Price, using the learner's existing Stripe customer, and redirects (303) to Stripe Checkout.
4. **Return pages:**
   - **Success** (`/billing/return?session_id={CHECKOUT_SESSION_ID}`): shows "Confirmando tu pago…" and checks the subscription every 2 s for up to 30 s. When it is active: the celebration, then "Tu plan Annual está activo". After 30 s: "Seguimos confirmando; puedes cerrar esta página, tu plan se activará solo".
   - **Cancelled:** "No se hizo ningún cargo" and a way back.
   - **Pending** (OXXO or SPEI): the payment reference, amount and deadline, as returned by Stripe, with a copy button.
5. **Customer Portal:** "Gestionar pago" posts to the server, which creates a portal session and redirects. The portal handles card changes, invoices and cancellation.
6. **Banners driven by subscription status:**
   - Payment failed: "Actualiza tu método de pago".
   - Renewal in 14 days or less: date and amount.
   - Lapsed: "Tu plan volvió a Free; no se borró nada".

### 7.2 Backend rules

- **Webhooks:** `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed` and `customer.subscription.deleted` (section 13). They are verified by signature and idempotent by event id. Webhooks are the only writer of `subscriptions`; the return page only reads.
- **Reconciliation:** the worker's nightly job (separate spec).
- **Gateway port:** a narrow billing interface (create checkout, create portal session, parse event) with a Stripe adapter, so that Conekta or Mercado Pago can be added without touching templates (section 4).
- **Refunds:** manual in v1; the page only explains how to ask.
- **Context7:** used for the current Stripe Checkout, Portal and webhook APIs before any code (CLAUDE.md).

## 8. Installable web app (PWA)

### 8.1 Manifest

- `name` "English Tutor"; `short_name` "Tutor".
- `lang` "es-MX".
- `start_url` "/app/?source=pwa"; `scope` "/"; `display` "standalone".
- `theme_color` `#11402c`; `background_color` `#e3f4ea`.
- Icons 192 and 512 px, plus 512 px maskable.
- Apple touch icon and iOS splash images via `<link>` tags.

### 8.2 Service worker (`/sw.js`, hand-written, about 60 lines)

- **On install:** precaches the versioned static list: CSS, the two JavaScript files, fonts, icons and `/offline`. The cache name includes the asset hash, and old caches are deleted on activate.
- **Navigation requests:** network only; on network failure it serves `/offline`.
- **Never cached:** `/app/*`, `/api/*`, `/billing/*`, `/admin/*`, HTMX requests and any non-GET request. No page with personal data is ever stored on the device.
- **Updates:** `skipWaiting` and `clients.claim`; safe because only static assets are cached.

### 8.3 Install experience

- **Android and desktop:** capture `beforeinstallprompt` and show our own "Instalar" button in Cuenta and once on Inicio. It is dismissible, and the dismissal is remembered server-side.
- **iPhone:** a short guide, "Compartir → Agregar a pantalla de inicio", with three illustrations. It is shown only in Safari on iOS and not when already in standalone mode.
- **Logout:** sends `Clear-Site-Data: "cache", "storage"` and expires the cookie. Support for this header varies by browser; it is defence in depth, since the cache holds no personal data anyway.

### 8.4 Real-device check (slice S0, before anything else)

On a real iPhone (installed from Safari) and a real Android phone (installed from Chrome), check two flows:

1. Google login completes and returns into the installed app, not into a browser tab.
2. A Stripe test-mode checkout returns into the installed app.

Record the results in `docs/adr/` with the dashboard stack ADR.

If iOS breaks either flow, the fallback is decided then, among:

- running login in the browser, with the app showing "abre en Safari";
- a one-time code handoff from the browser to the app.

## 9. Architecture and data flow

### 9.1 Placement

- New package `src/tutor/web/`, mounted in the same FastAPI app as the MCP endpoint and the REST API.
- Structure:
  - `routes/` holds one module per page;
  - `templates/` has `base.html`, `partials/` and `pages/`;
  - `static/` has `css/`, `js/`, `fonts/` and `icons/`;
  - `locale/`;
  - `charts.py` is the SVG macros' view model;
  - `security.py` handles CSRF and CSP.
- Routes depend on a `current_user` dependency (from the session cookie) and call the service functions that also back the REST API. They never open a database session directly.

Requirement section 4 says "the dashboard talks only to the REST API". This design reads that as: the same service layer, the same per-user filtering and the same plan-limit checks, without an HTTP round trip to ourselves. If the author meant a literal HTTP boundary, the routes switch to an internal HTTP client with no change to templates (Q7).

### 9.2 Requests

- Full pages for navigation; HTMX partials for filters, pagination, inline edits, status checks and the Conectar waiting state.
- A partial route returns only its fragment when `HX-Request` is present, and the full page otherwise. Deep links and no-JavaScript visits therefore work.
- No JSON endpoints for the dashboard, except the data export download and the Stripe webhook (which belongs to the API).

### 9.3 Session and authentication

- **Login:** Authlib OIDC with Google (section 4). The user is linked by Google `sub`, never by email alone.
- **Session cookie:** `__Host-tutor_session`, a random server-side session id: HttpOnly, Secure, SameSite=Lax, Path=/.
- **Lifetime:** 14 days idle, 30 days absolute. Rotated on login.
- **CSRF:** a synchronizer token per session, placed in a `<meta>` tag and sent by HTMX as a header (`hx-headers`). Forms carry a hidden field. Every non-GET request is checked.
- **CORS:** not needed for the dashboard (same origin). The API keeps section 5's rule: CORS restricted to the dashboard origin.

### 9.4 Front-end assets (no build step)

- **Vendored files:** HTMX 2.x and Motion, pinned versions recorded in `static/js/VENDORED.md` with source URL, version and SHA-256. Our own code is `app.js`, under 8 KB, plain ES modules.
- **HTMX config:**
  - `allowEval: false`;
  - `includeIndicatorStyles: false`, because the CSP blocks injected styles and indicator styles live in our CSS.
- **Cache busting:** asset URLs get a content hash as a query string, computed at startup by a small Python helper. The same hash feeds the service worker's cache name.
- **Charts:** domain functions return point lists, axis ticks and target lines. The view model scales them into SVG coordinates, and Jinja macros draw the `<path>` with `<title>` and `<desc>`. Chart math lives in `tutor.domain` (mypy strict, 90% coverage).

### 9.5 Localization

- **Translations:** Babel and the Jinja i18n extension, with catalogs for `es_MX` and `en`.
- **Language choice:**
  - logged in: from the learner's profile;
  - logged out: from `Accept-Language`, with `?lang=` links. Nothing is stored in a cookie (section 14: cookie-free apart from the session cookie).
- **Formatting:** numbers and dates through Babel ("3,1" and "jueves 8 de oct." in es-MX; "3.1" and "Thu, Oct 8" in English).
- **Lesson content** (phrases, chunks, errors) is English and is never translated.

### 9.6 Security headers

- **CSP:**
  - `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'`;
  - `form-action 'self' https://checkout.stripe.com https://billing.stripe.com`;
  - `frame-ancestors 'none'; base-uri 'none'`.
  - No inline scripts or styles.
- **Other headers:** HSTS, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`.
- **Templates:** Jinja autoescape is on everywhere; a test fails the build on any `|safe` or `Markup(` in `src/tutor/web/` unless that line carries a `safe: static` comment naming the constant it marks.
- **Rate limits** (section 5) apply to web routes. Data export is limited to 5 per day per user.

### 9.7 Data model additions (to propose in section 6)

| Table | Addition | Why |
| --- | --- | --- |
| `users` | `role` (learner/admin) | Admin page |
| `profiles` | `ui_lang`, `reduce_motion`, `email_weekly`, `email_reminders`, `install_prompt_dismissed_at`, `last_celebrated_session_id` | Cuenta preferences, install button, celebration trigger |
| `settings` | key, value JSONB, type, description, updated_at | Section 14 already requires it; section 6 does not list it |
| `web_sessions` | id (hashed), user_id, created_at, last_seen_at, csrf_token | Server-side dashboard sessions |
| `stripe_events` | event_id, type, received_at, processed_at | Webhook idempotency |
| `subscriptions` | `cohort_price_id` | Cohort pricing test |

## 10. Motion system

Rules:

- **Information:** motion never carries information alone. Every animated number is also plain text, and every animated state change also changes text or shape.
- **Performance:** only `transform` and `opacity` are animated, plus SVG `stroke-dashoffset`. No layout properties.
- **Ambient loops** stop by themselves within 5 s (WCAG 2.2.2), and run at most once per page load.
- **Reduced motion:** with `prefers-reduced-motion: reduce` or the server-side "Reducir animaciones" preference, the page renders its final state with no movement, and View Transitions are skipped.
- **Unsupported browsers:** they get the final state too; no polyfills.

| Component | Trigger | Effect | Technique | Duration | Reduced motion |
| --- | --- | --- | --- | --- | --- |
| Week trail | Inicio first paint | Path draws up to today, then dashes march twice | SVG `stroke-dashoffset`, CSS keyframes | 900 ms draw; ≤ 4 s march | Full trail, static |
| Today node | First paint | Two soft pulses | CSS keyframes | ≤ 2 s | Static ring |
| Streak pill | First paint, value change | Count up, one bob | Motion `animate` | 600 ms | Final number |
| KPI numbers | Scrolled into view | Count up to value | Motion `animate`; animated span `aria-hidden`, real value in visually hidden text | 700 ms | Final number |
| Progress charts | Scrolled into view | Line draws, target line fades in | SVG dash + CSS | 800 ms | Static chart |
| Phrase stamps | Scrolled into view | Used stamps press in with a spring, 60 ms stagger | Motion spring | ≤ 900 ms total | Static, filled or outlined |
| Session errors | Scrolled into view | "dijiste" strikes through, "mejor" writes in | CSS `clip-path` | 500 ms each, staggered | Both shown, strike-through static |
| Card → page | Navigation from Inicio | Shared-element morph (last session card → detail, today card → Plan) | Cross-document View Transitions | 250–350 ms | Instant |
| HTMX swaps | Filter, page, inline save | 150 ms fade; saved row tints leaf once | `htmx-added` / `htmx-settling` CSS | 150–400 ms | Instant |
| Loading | Request > 300 ms | Placeholder shapes with a soft shimmer | CSS | Until loaded | Static grey shapes |
| Free meter | First paint | Fill grows to value | CSS `scaleX` | 600 ms | Static |
| Tab bar / sidebar | Page change | Active indicator slides | View Transitions / CSS | 200 ms | Instant |
| Copy buttons | Click | Label becomes "Copiado" with a check | CSS | 150 ms | Text change only |
| Celebration | (a) first Inicio visit after a new `closed` session; (b) Annual confirmed on the return page | Leaf shapes burst, check mark draws | SVG + Motion | ≤ 1.2 s, once | Check icon and text |

The celebration fires at most once per event: (a) uses `profiles.last_celebrated_session_id`; (b) fires only when the return page's status check moves from pending to active, not on a reload. It is never triggered by streak numbers, page visits or `incomplete` sessions.

## 11. States and errors

| State | What the learner sees |
| --- | --- |
| First day (no sessions) | Inicio shows the Conectar steps instead of the trail; other pages show one-line empty states pointing to Conectar |
| No plan yet (diagnostic pending) | Today card: "Haz tu diagnóstico en el chat: di `start my lesson`" |
| Loading | Placeholder shapes; HTMX requests show them only after 300 ms |
| Free limit reached | A banner with the reset date returned by the server, and the Annual offer. The page still works; limits are enforced in the MCP tools, not here |
| Payment failed / renewal / lapsed / pending | The banners in section 7.1 |
| Deletion pending | After confirming, the session ends. Login during the 24 h window shows "Tu cuenta se está eliminando" and nothing else |
| Session expired | Full page → login with return path. HTMX request → 401 with `HX-Redirect` to login |
| Validation error (inline edit) | The field stays in edit mode with the message beside it; nothing is lost |
| Server error | A friendly error page or partial with a "Reintentar" button and a short reference id; details go to Sentry, never to the page |
| Offline | Navigation → the cached offline page: "Sin conexión. Tu progreso está a salvo en el servidor." |
| JavaScript blocked | Every page and form works through full page loads; only motion and the install button disappear |

## 12. Accessibility (WCAG 2.1 AA)

- **Landmarks and focus:** semantic landmarks (`nav`, `main`), one `h1` per page, a skip link, and a visible focus ring in leaf at ≥ 3:1.
- **Keyboard:** reaches everything, including inline edit (Enter saves, Esc cancels), filters and the tab bar.
- **Charts:** `role="img"` with `<title>`/`<desc>`, plus the "Ver como tabla" disclosure.
- **Status chips and trail nodes** carry text, not colour alone.
- **Live updates** (saved, payment confirmed) use one polite `aria-live` region; counters do not announce while animating.
- **Text zoom:** layouts reflow at 320 px width and 200% text zoom with no horizontal scroll.
- **Language:** `lang="es-MX"` on the page and `lang="en"` on English lesson content, so screen readers switch voices.

## 13. Privacy

- Cookie-free apart from the session cookie (section 14); no analytics scripts, no third-party requests except the redirects to Google and Stripe.
- No personal data in the service-worker cache or in `localStorage`.
- Logs carry a hashed user id. Never logged: emails, tokens, authorization codes, Stripe secrets, or the learner's turns.
- Export (section 5) contains every user-data table in JSON. Deletion is soft, then purged within 24 h by the worker, and the Stripe customer link is removed.

## 14. Testing and budgets

### 14.1 Server tests (pytest, part of `just check`)

- Domain functions for chart points, aggregates and trail states: unit tests within the 90% coverage on `tutor/domain`.
- Every page and partial renders for the states in section 11 (fixtures per state).
- **Cross-user isolation:** a table of every web route, each called as user A against user B's ids, must return 404. It is generated from the router, so new routes are covered automatically.
- Logged-out requests redirect to login (pages) or return 401 with `HX-Redirect` (partials).
- CSRF: every non-GET without a token returns 403.
- Webhooks: signed fixture payloads for the four events, replayed twice to prove idempotency; bad signature returns 400.
- The admin route returns 404 for learners.
- The `|safe` / `Markup(` check from section 9.6.
- Manifest fields and the service-worker never-cache list.

### 14.2 Browser tests (Playwright for Python, new `just test-e2e` recipe, own CI job)

- Viewports 390 × 844 and 1280 × 800, with reduced motion emulated so screenshots are stable.
- A test-only login route, enabled only when `TUTOR_ENV=test`; startup fails if it is enabled in any other environment.
- Flows:
  - first day → Conectar;
  - normal week on Inicio;
  - glossary filter, inline edit and CSV export;
  - session detail;
  - checkout redirect and return, with Stripe in test mode and a stubbed redirect target;
  - export;
  - deletion;
  - offline page through the service worker;
  - the service worker registers and the manifest is linked.
- Accessibility: axe run on every page in both viewports, failing on any WCAG 2.1 AA violation (tool choice verified in S0).

`just check` stays as it is; `test-e2e` needs browsers installed and runs in CI and before merging dashboard branches.

### 14.3 Manual checks

- Real-device install and the section 8.4 flows on iPhone and Android.
- Keyboard-only and screen-reader passes: VoiceOver on iPhone, NVDA on Windows.

### 14.4 Budgets

| Budget | Limit |
| --- | --- |
| JavaScript, compressed, total | ≤ 50 KB |
| CSS, compressed | ≤ 30 KB |
| Fonts | 2 families, Latin subset, ≤ 120 KB total |
| Largest Contentful Paint, Inicio | ≤ 2.5 s with 4× CPU slowdown and 1.6 Mbps / 150 ms network (Playwright + Chrome DevTools Protocol throttling) |
| Cumulative Layout Shift | ≤ 0.1 |
| Server time for HTMX partials | p95 ≤ 300 ms |

### 14.5 Done criterion

A dashboard task is done when `uv run just check` passes. A branch is ready to merge when `just test-e2e` also passes.

### 14.6 Context7 before code (S0)

HTMX 2, Motion, View Transitions, Web App Manifest and service workers, Authlib with Google OIDC, Stripe Checkout, Customer Portal and webhooks, Babel/Jinja i18n, Playwright for Python and the axe integration. CLAUDE.md requires this for OAuth and Stripe; the rest are checked as well because their APIs change often.

## 15. Open questions

- **Q1, price before login.** The cohort pricing test (section 13) assigns a price per signup, but the dashboard is cookie-free for anonymous visitors. Proposal: the public pricing page shows features without the Annual price; the price appears after login, fixed per user. Author to confirm.
- **Q2, stamps data.** Phrase stamps need each session's offered chunks stored, not just counts. The core loop should persist offered chunk ids per session. If it does not, stamps fall back to chunks confirmed this week, filled when they appear in `chunks_used`.
- **Q3, OXXO and SPEI** for subscriptions with Stripe MX (section 15 open question). If unavailable, the pending page is not built and the pre-pay summary lists card only.
- **Q4, gateway.** Stripe vs Conekta or Mercado Pago for MXN is decided before S2; the billing port keeps templates unchanged either way.
- **Q5, profile editing** on the dashboard (domains, pace, target) would need re-plan rules. Read-only in v1; changes go through chat (`update_plan`).
- **Q6, confirming provisional glossary items** from the dashboard. Not in v1: principle 6 and section 10 ask at the next session start.
- **Q7, "talks only to the REST API"** (section 4): shared service layer, as read in section 9.1, or a literal HTTP boundary?
- **Q8, admin scope:** settings only in v1; product analytics later.

## 16. Requirement edits suggested to the author

Requirements change only when the author edits them. These are suggestions:

1. **Section 1, out of scope:** after "mobile apps" add "(native store apps; the dashboard is an installable web app, installed from the browser)".
2. **Section 6:** add the tables and fields in section 9.7.
3. **Section 14, Configuration:** add "the admin page is author-only and edits the settings table; product analytics stay in the database until after launch".

## 17. Out of scope

- Push notifications.
- Offline data or background sync.
- App store publishing.
- Review UI.
- Admin analytics.
- Credits (v1.1).
- CFDI.
- Social sharing.
- Manual theme toggle.
- Marketing site beyond the pricing page.
- Remotion or any video rendering.
- Any client-side computation of metrics, streaks, limits or prices.
