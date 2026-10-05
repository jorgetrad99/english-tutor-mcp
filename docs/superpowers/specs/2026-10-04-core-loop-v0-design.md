# Core loop v0 design: onboarding, starter track, lesson loop and v0 pages

- **Status:** draft for author review
- **Date:** 2026-10-04
- **Author:** Jorge (decisions); drafted with Claude
- **Phase:** Core loop v0 (requirements section 16), build Oct 12 → Nov 13, 2026
- **Builds on:** requirements sections 3–12, 14, 16; spike docs `docs/spike/01–04`; dashboard spec `2026-10-04-dashboard-design.md` and its plan Part 1
- **ADR:** `docs/adr/0002-mcp-auth-via-fastmcp-oauth-proxy.md`
- **Gate:** no product code before Oct 12 and the spike go decision (ADR 0001, Oct 11)

## 1. Goal and success

A Free Claude account connects through OAuth, answers a short onboarding, gets a deterministic starter plan, and runs the full loop:

`start_lesson` (plan item or `prep` event) → scenario → feedback → `save_glossary` → `end_session`.

Its confirmed glossary items come back as FSRS reviews in the next session. The server computes every metric from the evidence. A small website built on the approved dashboard design shows the profile, plan, sessions and glossary.

**Success is the validation gate** (section 16, moved to Nov 16 – Dec 4; see section 17):

- 15 real sessions: ≥ 10 by the author and ≥ 5 by two or three invited developers, at least 5 in voice.
- Author adherence: ≥ 10 sessions in 3 weeks.
- Median `user_words_per_min` in voice: ≥ 20.
- `evidence_fidelity` above the section 11 thresholds.
- ≥ 60% of proposed glossary items confirmed (measured as in section 10.4).
- Every tester says they would run a prep session before a real meeting.

## 2. Decisions at a glance

| # | Decision | Chosen | Rejected |
| --- | --- | --- | --- |
| D1 | Where lessons come from without the plan engine | A fixed IT starter track, ordered and scheduled by a deterministic plan-lite | Prep only; pulling the diagnostic and planner forward |
| D2 | v0 scope | The middle ground: onboarding, plan-lite and the v0 web pages on the approved design, about one extra week | Strict section 16; the full plan engine and dashboard now |
| D3 | Where onboarding happens | Both chat and web, through one service function | Chat only; web only |
| D4 | Track content | IT only, 24 items with 5 chunks each, drafted by Claude and reviewed by the author | All three domains; a 12-item seed |
| D5 | MCP authorization | FastMCP `GoogleProvider` OAuth proxy (ADR 0002) | Own Authlib authorization server; MCP SDK provider interfaces with own storage |
| D6 | Glossary confirmation evidence | New `declined` status in `save_glossary` | Model-reported proposal count |
| D7 | Database access | SQLAlchemy 2 **sync** with psycopg 3, one driver; MCP tools call services via `anyio.to_thread.run_sync` | Async SQLAlchemy with asyncpg, which conflicts with the synchronous web ports in dashboard plan Task 7 |
| D8 | Background jobs | None in v0; housekeeping runs inside `start_lesson` | An APScheduler worker |

## 3. Scope

### 3.1 In v0

- OAuth for Claude via the FastMCP proxy; Google login for the website; one `users` row per Google `sub`.
- Onboarding questions and `save_profile` (chat) plus the Perfil form (web).
- The IT starter track (24 items, 120 chunks) as seed data.
- Plan-lite: ordering, scheduling, feasibility, versioning.
- MCP tools: `get_profile`, `save_profile`, `start_lesson`, `record_review`, `save_glossary`, `end_session`; prompt `/start-lesson`; server `instructions`.
- Glossary with provisional, confirmed and declined states; FSRS-4.5 reviews inside lessons.
- `end_session` validation and the section 11 metrics listed in section 11.3 here.
- Web: dashboard plan Part 1 (Tasks 1–15) as written; Part 2 Tasks 20–23 and 29 as written; a new Perfil page; Postgres adapters for the ports those pages use.
- Gate report CLI; Docker Compose deployment on the homelab; nightly `pg_dump`.

### 3.2 Not in v0 (after the gate)

- Diagnostic, gap-based plan generation, weekly re-plan, `run_diagnostic`, `get_plan`, `update_plan`.
- `get_due_reviews`, `/review`, `/weekly`, MCP resources.
- Business and daily tracks (the profile already accepts them as data; the tool enum adds them later).
- `unique_lemmas`, lexical diversity, `l1_switches` (computed later from `raw_evidence`).
- Free-plan caps, subscriptions, Stripe, pricing and checkout (dashboard Part 2 Tasks 16–19).
- Plan (full), Progreso, Cuenta, Reportes, Admin pages (Part 2 Tasks 24–28); browser, accessibility and budget CI (Task 30).
- Weekly email, worker, Sentry, Prometheus, data export, self-service deletion, the full privacy notice and terms.

### 3.3 Build order (Oct 12 → Nov 13)

The MCP loop is gate-critical; the website is not. If the schedule slips, the web slips and the gate still runs on the MCP loop and the gate report.

| Week | Work |
| --- | --- |
| Oct 5–11 (during the spike, content only) | Draft the 24 track items and 120 chunks for author review (section 7.1) |
| 1 (Oct 12–16) | Domain: profile validation, plan-lite, lesson composer, normalization, glossary rules, FSRS, evidence validation, metrics |
| 2 (Oct 19–23) | Database schema and RLS, repositories, services, ADR 0002 verification, auth wiring |
| 3 (Oct 26–30) | MCP server, tools, prompt, instructions; contract tests; first real Claude run on a dev tunnel |
| 3–4 (Oct 26 – Nov 10) | Dashboard Part 1, then Part 2 Tasks 20–23 and 29, Perfil page, Postgres adapters |
| 4½ (Nov 11–13) | Deploy, gate report, Inspector auth check, real Claude Free session, tester invitations |

## 4. Architecture

```
Claude ──MCP Streamable HTTP, Bearer──▶ /mcp ──┐
                                               ├── FastAPI app ── tutor.services ── tutor.domain (pure)
Browser ──__Host-tutor_session cookie──▶ /…  ──┘          │
                                                     tutor.db (SQLAlchemy 2 sync, psycopg 3)
                                                          │
                                                     PostgreSQL 16 (RLS)
```

One process serves MCP, the website and the OAuth endpoints. The FastMCP app is mounted at `/mcp` with its lifespan passed to FastAPI, and the OAuth discovery routes are served at the root (`auth.get_well_known_routes(mcp_path=...)`), as in the FastMCP mounting docs.

| Module | Responsibility | Rules |
| --- | --- | --- |
| `tutor.domain` | Pure logic: profile validation, plan-lite, lesson composer, normalization, glossary rules, FSRS, evidence validation, metrics, streak, summary text | No I/O, no clock reads (time is passed in); mypy strict; ≥ 90% coverage |
| `tutor.services` | One function per use case. Loads through repositories, calls the domain, writes in one transaction, writes the audit log | MCP tools and web routes call these; no rule is implemented twice |
| `tutor.db` | Engine, `unit_of_work(user_id)` that opens a transaction and runs `SET LOCAL app.user_id`, repositories, Alembic migrations, seed loader | Every repository method takes `user_id` |
| `tutor.auth` | `GoogleProvider` construction (ADR 0002), `sub` → user resolution, the web `GoogleLogin` adapter (dashboard Task 12) | Never logs tokens, codes, secrets or emails |
| `tutor.mcp` | FastMCP server, tool schemas, `response_rules`, `instructions`, the prompt, error mapping | Section 7 and 12 contract; `mcp-contract-reviewer` on every diff |
| `tutor.web` | Dashboard Part 1 foundation, v0 pages, Postgres adapters for its ports | Dashboard spec rules (CSP, CSRF, autoescape, i18n) |
| `tutor.ops` | `gate_report` CLI | Runs as the owner role, read only |

**Database access (D7).** The dashboard ports are synchronous, so the whole server uses sync SQLAlchemy 2 with psycopg 3. FastAPI runs sync route dependencies in its thread pool; MCP tool handlers are `async def` and call services with `anyio.to_thread.run_sync`. At about four users this costs nothing measurable.

**Row-level security.** The app connects as a non-owner role `tutor_app`. Every table with `user_id` has `ENABLE` and `FORCE ROW LEVEL SECURITY` and a policy `user_id = current_setting('app.user_id')::uuid`. Seed tables (`track_items`, `track_chunks`) have no RLS and are read-only for `tutor_app`. User resolution (`users` by `google_sub`) runs through a `SECURITY DEFINER` function so it works before `app.user_id` is known.

**Deployment.** Docker Compose with `app` and `db` on the homelab, behind its own Cloudflare Tunnel hostname, separate from the spike. A nightly `pg_dump` goes to a local folder with 30-day retention. The FastMCP OAuth store lives on a named Docker volume; losing it only forces users to reconnect.

## 5. Data model (v0)

All ids are UUIDs unless stated. Timestamps are `timestamptz`, stored in UTC.

| Table | Fields | Notes |
| --- | --- | --- |
| `users` | id, google_sub (unique), email, display_name, native_lang (default `es`), role (`learner`/`admin`), created_at, deleted_at | `role` from dashboard spec 9.7 |
| `profiles` | user_id (PK), domains text[], use_cases text[], goal_text, minutes_per_day, days_per_week, self_level, target_level, target_date (nullable), timezone (IANA, default `America/Mexico_City`), ui_lang, reduce_motion, install_prompt_dismissed_at, last_celebrated_session_id, onboarded_at, updated_at | `current_level_speaking/writing` from section 6 are added with the diagnostic, so a self-rating is never stored as a measured level. UI fields from dashboard spec 9.7 |
| `track_items` | id text (`it-07`), domain, order_no, cefr (`B1`/`B2`), can_do_en, can_do_es, skill (`speaking`/`writing`), interaction_type, use_cases text[], character, objective, obstacle, scenario_hint | Seed; replaces `cefr_descriptors` until the plan engine |
| `track_chunks` | id text (`it-07-c3`), track_item_id, position (1–5), text, example | Seed; the ids `chunks_used[]` refers to |
| `plans` | id, user_id, version, status (`active`/`superseded`), generated_at, rationale JSONB | One active plan per user (partial unique index) |
| `plan_items` | id, plan_id, user_id, week_no, order_no, track_item_id, variant (`base`/`complication`), status (`pending`/`done`/`skipped`), done_session_id | `user_id` duplicated for RLS |
| `sessions` | id, user_id, plan_item_id (nullable), track_item_id, prep_text, mode, client, started_at, ended_at, status (`open`/`closed`/`incomplete`), low_trust, brief_variant (`base`/`complication`/`simpler`), chunks_offered text[], task_result, hints_given, cefr_estimate_speaking, cefr_confidence, cefr_excluded, confidence_1_5, raw_evidence JSONB, result JSONB | `result` stores the `end_session` response for idempotent repeats; partial unique index: one `open` per user |
| `session_metrics` | session_id (PK), user_id, user_words, assistant_words_estimate, user_ratio, turns, words_per_turn, duration_min, user_words_per_min, errors_total, errors_rejected, errors_by_category JSONB, errors_per_100w, recurring_errors, uptake_count, chunks_offered, chunks_used, chunks_rejected, activation_rate | `unique_lemmas`, `lexical_diversity`, `l1_switches` are nullable columns left empty in v0 |
| `session_errors` | id, session_id, user_id, said, correct, correct_norm, category, turn_index | Validated errors only; makes `recurring_errors` a query |
| `glossary_items` | id, user_id, kind, text, text_norm, meaning, context_sentence, domain, origin_session_id, status (`provisional`/`confirmed`/`declined`/`archived`), seen_count, last_seen_session_id, leech, provisional_expires_at, created_at, updated_at | Unique (user_id, text_norm) |
| `review_states` | glossary_item_id (PK), user_id, stability, difficulty, due_at, last_review_at, reps, lapses, last_ratings smallint[] (last 2) | `leech` lives on `glossary_items` |
| `review_logs` | id, glossary_item_id, user_id, session_id, rating, reviewed_at, elapsed_days | Append-only; unique (session_id, glossary_item_id) |
| `audit_log` | id, user_id, event, meta JSONB, at | Events in section 13 |
| `web_sessions` | id (hashed token), user_id, csrf_token, data JSONB, created_at, last_seen_at | Dashboard spec 9.3 / plan Task 11 |
| `mcp_clients_seen` | user_id, client_id, first_seen_at | Supports the `mcp_client_seen` audit event and the Conectar "connected" check |

**Length caps** (validated in the domain, enforced again by `CHECK` constraints): `goal_text` 300, `prep_text` 300, each user turn 2,000, `said`/`correct` 300, glossary `text` 120, `meaning` 200, `context_sentence` 300, evidence strings 200. Request body ≤ 64 KB; `raw_evidence` ≤ 20 KB serialized.

## 6. Onboarding and profile

### 6.1 Questions

Both channels ask the same questions, served by the server as closed options and validated by one domain function `validate_profile(raw, today) -> Profile | ProfileErrors`.

| # | Field | Question | Allowed values |
| --- | --- | --- | --- |
| 1 | `self_level` | Your English today | `B1`, `B1+`, `B2`, `B2+`, `C1`, each with a one-line plain description in es-MX and English |
| 2 | `domains` | Your field | `it` (v0) |
| 3 | `use_cases` | Where you need English at work | 1–4 of `standup`, `code_review`, `interview`, `client_call`, `demo`, `incident`, `one_on_one`, `async_writing` |
| 4 | `minutes_per_day`, `days_per_week` | How much time | 15, 20 or 30; 2–7 |
| 5 | `target_level`, `target_date`, `goal_text` | Your goal | Target ≥ self level; date optional, 28–364 days from today; goal optional, ≤ 300 characters, stored as data |
| — | `timezone` | Not asked in chat | Web form fills it from the browser (`Intl.DateTimeFormat().resolvedOptions().timeZone`), validated against the IANA list; chat keeps the default |

### 6.2 Chat flow

- `get_profile` with no profile returns `onboarding_needed: true` and `onboarding_questions` (ids, prompts in English, allowed values with their descriptions).
- Its `response_rules`: ask one question at a time in the user's preferred language; map each answer to an allowed value; if unsure, offer the options; read the five answers back in one sentence; then call `save_profile`.
- `start_lesson` before onboarding returns the error `onboarding_needed`.

### 6.3 Web flow

- After the first login with no profile, the website redirects to Perfil, which shows the same questions as a form.
- The form posts to the same `save_profile` service. Validation errors are shown per field in es-MX.

### 6.4 `save_profile` service

1. Validate (section 6.1).
2. Upsert `profiles`.
3. If the profile fields that affect the plan changed (or there is no plan), generate a plan-lite (section 7.2) as a new version; the previous version becomes `superseded`.
4. Audit `profile_saved` and, if a plan was made, `plan_generated`.
5. Return the profile, a plan summary (weeks, sessions planned, next item) and the feasibility result.

Saving identical answers is a no-op that returns the current state.

## 7. Starter track and plan-lite

### 7.1 Track content

- 24 IT items: 12 at B1 and 12 at B2, ordered as a progression. Every use case is covered by at least 3 items, and every interaction type (explain, negotiate, disagree, ask for help, give feedback, small talk) by at least 3. At least 4 items have `skill = writing` (`async_writing` and parts of `code_review`, `incident`).
- Each item has a can-do statement in English and Spanish, a character, an objective, an obstacle, a scenario hint, and 5 chunks with one example sentence each.
- Claude drafts it during the spike week as `docs/content/track-it-v0.yaml`; the author reviews it; the plan turns it into a seed migration. The content is English except `can_do_es`.

### 7.2 Plan-lite algorithm (`tutor.domain.plan_lite`)

**Input:** profile, track items, today. **Output:** ordered plan items and a feasibility result.

1. **Candidates:** track items in the profile's domains. If `self_level ≥ B2`, drop B1 items unless they share a use case with the profile.
2. **Sort** by (CEFR band, 0 if the item shares a use case with the profile else 1, `order_no`).
3. **Horizon:** `weeks = ceil((target_date − today).days / 7)` clamped to 4–52, or 12 without a date. `sessions_needed = days_per_week × weeks`.
4. **Fill:** repeat the sorted list until `sessions_needed` is reached. The first pass is `variant = base`, every later pass is `complication`.
5. **Interaction-type pass:** over the whole filled list, greedily take the first remaining item whose `interaction_type` differs from the previous one; if none differs, take the first remaining.
6. **Schedule:** item `i` gets `week_no = i // days_per_week + 1` and `order_no = i % days_per_week + 1`.
7. **Feasibility.** Levels map to half-steps: B1 = 3.0, B1+ = 3.5, B2 = 4.0, B2+ = 4.5, C1 = 5.0. Each half-step costs 90 h below B2 and 100 h from B2 to C1 (section 9 reference: B1→B2 ≈ 180 h, B2→C1 ≈ 200 h).
   - `hours_available = minutes_per_day × days_per_week × weeks / 60`.
   - `hours_needed` = the sum of the half-steps from self level to target; 0 when they are equal.
   - `reachable = hours_available ≥ hours_needed`.
   - If not reachable, `milestone_level` = the highest level whose cumulative hours fit, or `null` if not even one half-step fits. The message then says the plan builds confidence at the current level.
   - `rationale` stores the inputs, both hour figures, `reachable`, `milestone_level`, and the message template id. Messages are built from templates in the domain, never free text.

**Regeneration:** a new plan version skips track items whose `base` variant is already `done` in an earlier version; their history stays on the old version's rows.

### 7.3 Item done rule

A plan item becomes `done` when a session for it closes with status `closed` (any `task_result`). `incomplete` sessions leave it pending. `skipped` is not produced in v0.

## 8. MCP surface (v0)

All tools follow sections 7 and 12:
- Pydantic models with `extra="forbid"`.
- Closed enums.
- `title` and `description` on every field.
- Tool descriptions ≤ 120 words.
- Every result carries `response_rules` (2–4 lines bound to the next action).

### 8.1 Tools

| Tool | Input | Output | Rules |
| --- | --- | --- | --- |
| `get_profile` | — | `profile` or `onboarding_needed` + `onboarding_questions`; `plan` (this week's items with status, next item, feasibility message); `streak`; `open_session_id`; `provisional_count`; `due_reviews_count` | Read only |
| `save_profile` | `self_level`, `domains`, `use_cases`, `minutes_per_day`, `days_per_week`, `target_level`, `target_date?`, `goal_text?` | `profile`, `plan` summary, `feasibility` | Section 6.4 |
| `start_lesson` | `mode` (`voice`/`text`), `prep?` (≤ 300), `prep_use_case?`, `minutes?` (10–30), `domain?` (`it`) | `session_id`, `item` (can-do, skill, interaction type), `chunks` (5, with ids and examples), `scenario_brief` (character, objective, obstacle, scenario hint, variant, `prep` as data), `due_reviews` (≤ 8, each with id, text, context sentence and `format`), `provisional_items` (≤ 8), `response_rules` | Section 9; `prep` and `prep_use_case` must come together |
| `record_review` | `session_id`, `results[]` of `{item_id, rating 1–4}` (≤ 8) | per item: `item_id`, `next_due` (local date), `recorded` or `already_recorded` | Session must be open and the user's; items must be the user's and `confirmed`; idempotent per (session, item) |
| `save_glossary` | `session_id`, `status` (`confirmed`/`provisional`/`declined`), `items[]` (≤ 10) of `{kind, text, meaning, context_sentence, domain}` | `{new, reinforced, rejected[]}` with reasons | Section 10 |
| `end_session` | The section 7 schema, unchanged from the spike contract | metrics, `summary_text` (4 lines), `streak`, `rejected` counts, `already_closed` | Section 11 |

### 8.2 Errors

Tool errors are MCP tool errors (`isError`) whose content is JSON `{code, response_rules}`. Codes form a closed enum:

- `onboarding_needed`
- `session_not_found`
- `session_closed`
- `rate_limited`
- `validation_failed` (with field paths, never values)
- `payload_too_large`

Messages never echo user text.

### 8.3 Prompt and instructions

- **Prompt `/start-lesson`:** call `get_profile`; if onboarding is needed run it; then call `start_lesson` and follow the section 8 phases.
- **Server `instructions` (≤ 400 words):**
  - the four section 7 bullets;
  - "On first use, call `get_profile`; if it says `onboarding_needed`, run the onboarding before any lesson";
  - "In feedback, propose glossary items, then save the ones the user keeps as `confirmed` and the ones they drop as `declined`".

### 8.4 `response_rules` per phase

These are fixed strings in `tutor.mcp.rules`, chosen by the server from mode and state.

- **`start_lesson`:** confirm provisional items with one yes/no question first; state today's goal in one sentence; warm-up with the 5 chunks and up to 4 due reviews, then call `record_review`; play the character; voice turns ≤ 40 words, text ≤ 60; no corrections until the objective is reached or 12 minutes pass.
- **`save_glossary`:** confirm in one sentence; never read the JSON.
- **`end_session`:** read `summary_text` once, word for word; no lists.
- **Errors:** one sentence to the user; no retries beyond one.

## 9. Lesson composition (`tutor.domain.lesson`)

### 9.1 Choosing the item

- **No `prep`:** the first `pending` item of the active plan in (week_no, order_no) order. Missed days do not pile up.
- **With `prep`:** the first `pending` plan item whose track item lists `prep_use_case`. If none is pending, the track item with that use case that was done least recently, as an off-plan session (`plan_item_id` null, `track_item_id` set). `prep_text` goes into `scenario_brief.prep` as data.
- **Plan exhausted:** return the last item with `variant = complication` and `response_rules` telling the user to update their goal on the web or in chat.

### 9.2 Variant (difficulty adaptation, section 8)

Over the user's last 3 `closed` sessions:
- **2 or more `not_achieved`:** `simpler`.
- **Otherwise, all 3 `achieved` with `hints_given ≤ 1`:** `complication`.
- **Otherwise:** the plan item's variant.

### 9.3 Due reviews

1. Confirmed items with `due_at ≤ now`.
2. Ordered by group: correction or leech first, then chunk, then term. Within a group, the most overdue first.
3. Capped at 8.

| Format | When |
| --- | --- |
| `correct` | Leech correction |
| `use` | Last two ratings were 3 |
| `produce` | Correction or chunk |
| `recall` | Term |

### 9.4 Housekeeping in `start_lesson` (same transaction)

1. Close any `open` session as `incomplete`.
2. Delete `provisional` items past `provisional_expires_at` and `declined` items older than 30 days.
3. Enforce 10 starts per user per local day (`rate_limited`).

## 10. Glossary and FSRS

### 10.1 Normalization (`tutor.domain.text`)

- NFKC.
- Lowercase.
- Curly quotes and apostrophes → straight.
- Strip punctuation except inner apostrophes and hyphens.
- Collapse whitespace; trim.

The same function serves glossary dedup, `said` validation, uptake and recurring errors. It is ported from the spike's `normalize.py` with its tests and extended with Hypothesis properties: idempotent, and never longer than its input.

### 10.2 `save_glossary` rules

**Per item:**

| Case | Result |
| --- | --- |
| Empty after normalization, over a length cap, or a duplicate in the same call | `rejected` with a reason |
| New text | Inserted with the requested status |
| `provisional` | `provisional_expires_at = now + 7 days`; never scheduled |
| `confirmed` | A `review_states` row with FSRS initial state and `due_at` = tomorrow 04:00 local |
| `declined` | Stored, never scheduled; purged after 30 days |
| Existing text, status confirmed | `reinforced`: `seen_count += 1`, `due_at` = tomorrow, kind upgraded to `correction` if the new kind is a correction; a third appearance within 30 days sets `leech` |

**Status transitions:**
- `provisional` or `declined` → `confirmed` is allowed.
- `confirmed` → `declined` or `provisional` is `rejected` with reason `already_confirmed`.

**Sessions accepted:** the session must be the user's and be `open`, or closed within the last 24 hours (provisional saves after `end_session`).

### 10.3 FSRS-4.5 (`tutor.domain.fsrs`)

- Own pure implementation with the default FSRS-4.5 weights, target retention 0.85, and ratings 1–4 as in section 10.
- The `fsrs` PyPI package is not used: its current releases implement a newer FSRS version than the requirements specify, and the module needs strict typing and coverage.
- Before any code, the plan fetches the FSRS-4.5 formulas, default weights and reference test vectors from the official open-spaced-repetition sources (Context7 or the repository). Tests assert those vectors. Nothing is written from memory.
- **Rating 4 is set by the server:** a reviewed item with rating 3 is upgraded to 4 at `end_session` when its normalized text appears in ≥ 2 distinct `user_turns` (once in the drill, once later). This is a stated heuristic.

### 10.4 Confirmation rate (gate metric)

`confirmed / (confirmed + provisional + declined)` over items first saved in the gate window. It is computed by the gate report, not reported by the model. Items the model never saves at all are invisible; section 15 measures that gap against the annotated transcripts.

## 11. `end_session`, metrics and summary

### 11.1 Validation order

1. Schema (unknown fields rejected), size caps.
2. Session lookup:
   - not the user's → `session_not_found`;
   - already `closed` or `incomplete` with a stored `result` → return it with `already_closed: true`;
   - `incomplete` because a newer `start_lesson` replaced it → `session_closed`.
3. **Errors:** each `said` must be a normalized substring of some user turn, otherwise it is dropped and counted in `errors_rejected`. The matching turn index is kept.
4. **Chunks:** ids not in the session's `chunks_offered` are dropped and counted in `chunks_rejected`.
5. **Status:** `incomplete` if there are 0 turns or `user_words < 30`; otherwise `closed`. `low_trust` if more than 50% of reported errors were rejected (errors discarded, words and turns kept).
6. **CEFR:** stored with confidence and evidence (≤ 5 strings). `cefr_excluded = true` when it differs by ≥ 1.0 (numeric scale) from the previous non-excluded closed session's estimate, or from `self_level` for the first session.

### 11.2 Writes (one transaction)

- `sessions` (status, evidence, result)
- `session_metrics`
- `session_errors`
- the plan item `done` (section 7.3)
- rating-4 upgrades (section 10.3)
- audit `session_closed`

### 11.3 Metrics computed in v0

| Metric | Rule |
| --- | --- |
| `user_words`, `turns`, `words_per_turn` | Tokens matching `[A-Za-z0-9]+(?:['’][A-Za-z]+)?` across `user_turns` |
| `duration_min` | `ended_at − started_at`, capped at 45 |
| `user_words_per_min` | `user_words / max(duration_min, 1)` |
| `assistant_words_estimate`, `user_ratio` | From the optional estimate; labelled as estimate; secondary only |
| `errors_total`, `errors_by_category`, `errors_per_100w` | Valid errors only |
| `recurring_errors` | Valid errors whose `correct_norm` appears in another `closed` session of the user within 30 days |
| `uptake_count` | Valid errors whose `correct_norm` appears in a later user turn than the one containing `said` |
| `chunks_offered`, `chunks_used`, `activation_rate` | 5 offered; valid used; `used / offered` |
| `task_result`, `hints_given`, `confidence_1_5`, CEFR | Stored as reported; CEFR labelled as model opinion |

### 11.4 Summary text (English, 4 lines, from templates)

1. `You spoke {user_words} words in {duration} minutes ({wpm} per minute).`
2. `You fixed {uptake} of your {errors_total} mistakes during the session.` (or `No mistakes were recorded.`)
3. `You used {used} of today's 5 phrases.`
4. `Streak: {streak} days.`

### 11.5 Streak

The number of consecutive local days (profile timezone), ending today or yesterday, with at least one `closed` session. `low_trust` counts; `incomplete` does not.

## 12. Website (v0)

**Foundation.** Dashboard plan Part 1, Tasks 1–15, executed as written: the later tasks depend on the earlier tasks' types, so none is skipped. Tasks 3 (charts), 4 (plan diff) and 6 (admin settings) produce domain functions that v0 pages do not render yet. Task 7 installs the `stripe` package; nothing in v0 calls it.

**Pages.** Part 2 Tasks 20 (Conectar), 21 (Inicio), 22 (Sesiones), 23 (Glosario, including the inline edit and CSV export that task already specifies) and 29 (route sweep) are executed as written. Then:

- **Perfil (new):** the onboarding form (section 6.3) and the current plan-lite (weeks, items with status, feasibility message). It follows the plan's "Conventions for page tasks".
- **Navigation** shows only Inicio, Perfil, Sesiones, Glosario and Conectar. Routes for postponed pages are not registered.
- **Entrar** shows a short privacy note: what is stored, that lesson text passes through the user's LLM vendor, and that deletion is by request to the author.

**Adapters.** Postgres adapters for `UserDirectory`, `WebSessionStore`, `DashboardReader` (the methods v0 pages call: `home`, `usage`, `sessions`, `session_detail`, `glossary`, `glossary_domains`, `has_any_session`, `account`), `GlossaryEditor` and `AccountService` (`set_preferences`, `dismiss_install_prompt`).

| Port | v0 behaviour |
| --- | --- |
| `SubscriptionStore` | Always Free |
| `BillingGateway` | Disabled adapter that raises if called |
| `SettingsStore` | Returns defaults |
| `DashboardReader.plan`, `progress`, `reports` | Unused; raise `NotImplementedError` with a test proving no v0 route calls them |

`usage` returns no caps, so the Free counters are hidden. The in-memory backend and demo stay for tests and local work.

**Conectar "connected" check:** true when `mcp_clients_seen` has a row for the user.

## 13. Security and privacy

- **Authorization:** every service call runs in `unit_of_work(user_id)` with RLS. Every repository has a cross-user isolation integration test. No admin tools over MCP.
- **MCP auth:** ADR 0002. Tools resolve the user from `get_access_token().claims["sub"]`, never from email. An unknown `sub` creates the user (audit `user_created`).
- **Rate limits** (in-memory, per user, per process): 60 MCP calls per minute, 10 lesson starts per local day; web routes as in the dashboard spec.
- **Audit events:** `user_created`, `web_login`, `mcp_client_seen` (first tool call per user and OAuth client), `profile_saved`, `plan_generated`, `session_closed`.
- **Logs:** one JSON line per tool call and per request with hashed `user_id`, tool or route, outcome code and latency. Never tokens, codes, secrets, emails or user text.
- **Secrets:** `GOOGLE_CLIENT_ID`/`SECRET`, `JWT_SIGNING_KEY`, `OAUTH_STORAGE_ENCRYPTION_KEY`, `DATABASE_URL`, `WEB_SESSION_SECRET`, all from the environment.
- **User text is data:** stored and returned as fields, never placed in `response_rules` or `instructions`; Jinja autoescape everywhere.
- **Privacy:** no transcripts or audio; `raw_evidence` only. Deletion on request by the author with a documented SQL script that cascades and keeps a hashed id in `audit_log`.
- **Reviews:** `security-reviewer` on every `auth`, `db`, `api`/`web` diff; `mcp-contract-reviewer` on every `mcp` diff.

## 14. Gate report

`uv run python -m tutor.ops.gate_report --from 2026-11-16 [--to …]` prints:

- sessions per user (closed / incomplete / low_trust) and how many in voice;
- author sessions in the window;
- median `user_words_per_min` in voice and in text;
- confirmation rate (section 10.4);
- activation rate;
- `end_session` validity (closed or low_trust vs incomplete);
- rejected-error share.

Users are labelled by a `--labels` file mapping hashed ids to names; no emails are printed. `evidence_fidelity` comes from the spike annotation tooling, copied into `evals/` as part of this phase.

## 15. Testing and done criterion

| Layer | Tests |
| --- | --- |
| Domain | Unit tests and Hypothesis properties: normalization; plan-lite (no consecutive interaction type unless unavoidable, schedule shape, feasibility boundaries); composer (item choice, variant, review order); FSRS against reference vectors; evidence validation; metrics; streak across timezones and DST. **≥ 90% coverage on `tutor/domain`** |
| Services | With in-memory repositories: each use case's happy path, the error codes, idempotency (`record_review`, `end_session`, `save_profile`) |
| Database | Integration tests on `db-test`: migrations up and down, RLS isolation per table, one-open-session index, seed loads |
| MCP | FastMCP in-memory client: every tool's schema rejects unknown fields; every result has `response_rules`; descriptions ≤ 120 words; error mapping; an end-to-end text lesson with scripted calls |
| Web | As in the dashboard plan tasks executed, plus Perfil |
| Manual | MCP Inspector auth check; one full session from a Free Claude account on web and on mobile voice before invitations |

**A task is done when** `uv run just check` passes. **v0 is done when** the section 16 v0 row holds and the manual checks above are recorded in `docs/spike/` style notes under `docs/v0/`.

## 16. Decided on Oct 11 from the spike

| Spike result | If it passes | If it fails |
| --- | --- | --- |
| #1 tools in voice mode (≥ 4 of 5) | Voice rules as written | `start_lesson` voice `response_rules` add the section 8 text → voice → text handoff; onboarding copy states it; the gate's "5 in voice" means phase 3 in voice |
| #2 `end_session` validity (≥ 90% valid, ≥ 80% `said` valid) | As written | Add an `/end` prompt and an end-of-lesson reminder in `start_lesson`'s `response_rules`; below 70%, stop and rethink evidence capture before building |

Both outcomes change wording only; no tool or table changes. If ADR 0001 is a no-go on the MCP-first architecture, this spec is void.

## 17. Requirement edits for the author

`docs/requirements.md` is hook-protected; these are proposed texts to paste.

1. **Section 16, v0 row:**

   > Core loop v0 (Oct 12–Nov 13) | A Free Claude account can connect via OAuth, complete onboarding in chat or on the web, get a starter plan from the IT track, run `start_lesson` (plan item or `prep` event) → scenario → feedback → `save_glossary` → `end_session`; FSRS reviews appear in the next session; metrics computed from evidence; the website shows profile, plan-lite, sessions and glossary on the approved dashboard design; 90% test coverage on FSRS, metrics, validation. No diagnostic, billing or reports

2. **Section 16 gantt and gate row:**
   - Core loop v0: 2026-10-12 → 2026-11-13.
   - Gate: 2026-11-16 → 2026-12-04.
   - Gate decision: 2026-12-07.
   - Plan engine: re-estimated at the gate.
3. **Section 4 stack, Auth row:**

   > FastMCP `GoogleProvider` OAuth proxy for MCP clients (ADR 0002); Authlib OIDC with Google for the website. Own authorization server reconsidered before the private beta.

4. **Section 5, login flow:** add "In v0 the OAuth proxy issues and refreshes tokens; see ADR 0002 for which rules the library meets."
5. **Section 6:**
   - Add `track_items`, `track_chunks`, `session_errors`, `web_sessions`, `mcp_clients_seen`.
   - `profiles`: add `use_cases`, `self_level`, `timezone`.
   - `sessions`: add `prep_text`, `track_item_id`, `chunks_offered`, `low_trust`, `brief_variant`.
   - `glossary_items.status`: add `declined`.
6. **Section 7:** add `save_profile`; add `prep_use_case` to `start_lesson`; add `declined` to `save_glossary.status`; note that v0 ships 6 of the tools.
7. **Section 10, states:** add "If the user drops a proposed item, the LLM calls `save_glossary(status=declined)`; declined items are never scheduled and are purged after 30 days."
8. **Section 3, Plans:** add "Free-plan caps are not enforced before billing ships."

## 18. Open questions

- **Q1 (author, before Oct 12):** review the track draft. Are the eight use cases the right ones for your testers?
- **Q2 (plan time, Context7):** confirm the FastMCP proxy's refresh-token rotation, token lifetime and audience claim, and record them in ADR 0002's compliance table.
- **Q3 (plan time):** confirm how FastMCP runs `async` tools that call `anyio.to_thread.run_sync`, and the version floor for `get_well_known_routes`.
