# Core loop v0 design: onboarding, starter track, lesson loop and v0 pages

- **Status:** approved 2026-10-04; amended during planning and implementation (see "Amendments")
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

## Amendments

Decided while planning and building v0 (plans `docs/superpowers/plans/2026-10-04-core-loop-v0-*.md` and the controller's decision ledger), and checked against the code on `feat/core-loop-v0` (`src/`, `alembic/versions/0001`–`0005`, `deploy/`, `docs/v0/runbook.md`). Each states what changed against which section above, and why. Where an amendment and an earlier section disagree, the amendment wins. Q1–Q3 in section 18 are closed: Q1 by the author's go-ahead on 2026-10-05 (the track can still be edited, amendment 37), Q2 in ADR 0002, Q3 by amendment 12. The requirement edits these amendments imply are listed, paste-ready, in `docs/v0/requirements-edits.md`; section 17 above is the planning-time subset of that list.

### Architecture, auth and data

1. **MCP proxy callback is `/oauth/callback`** (sections 4, 13). The website's Google login owns `/auth/callback`, which is also FastMCP's default proxy path, so `GoogleProvider(redirect_path="/oauth/callback")`. The Google OAuth client registers both URIs (runbook "Google OAuth client").
2. **A path dispatcher instead of mounting** (section 4, which said the FastMCP app is mounted at `/mcp` with its lifespan passed to FastAPI). Both apps own root paths, so `tutor.app.PathDispatch` sends `/mcp`, `/authorize`, `/token`, `/register`, `/consent`, `/oauth/callback` and `/.well-known/*` to the MCP app and everything else to the website. Each keeps its own middleware, so the web CSP and `no-store` never touch MCP; both branches sit behind the same `RequestLog(BodySizeGuard(…))` (amendment 21). Lifespan events go only to the MCP app, which `PeriodicPurge` wraps (amendment 31); the website has no lifespan of its own.
3. **`users.mcp_first_seen_at` replaces `mcp_clients_seen`** (sections 5, 12, 13). The token's `client_id` is the Google `sub`, so the OAuth client is not visible to tools. The audit event is `mcp_first_use` (once per user, not per client); Conectar's "connected" check is "the user has any session".
4. **UI preferences and timezone live on `users`** (sections 5, 6.1). `lang`, `timezone`, `reduce_motion`, `install_prompt_dismissed_at`, `last_celebrated_session_id`, `deletion_requested_at`, `email_weekly` and `email_reminders` are `users` columns, read on every web request before onboarding; `profiles` has no `timezone` or `ui_lang`. `save_profile` writes `users.timezone`.
5. **Three database roles; row-level security through `SET LOCAL ROLE tutor_app`** (section 4 "Row-level security", sections 13 and 14). This supersedes the planning-time ruling of one owner `DATABASE_URL`, which would let a single injected `RESET ROLE` reach a role that bypasses or can alter the policies.
   - **Migration owner** (`MIGRATION_DATABASE_URL`): runs the one-shot `migrate` service (`alembic upgrade head`, then `tutor.ops.provision_roles`) and the backups. In prod the app refuses to start if `MIGRATION_DATABASE_URL` or `GATE_REPORT_DATABASE_URL` is in its environment.
   - **App login** (`DATABASE_URL`, default name `tutor_login`): `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`, granted `tutor_app WITH INHERIT FALSE, SET TRUE`, owning nothing. Every transaction runs `SET LOCAL ROLE tutor_app` and sets `app.user_id`, `app.google_sub` and `app.web_session` with `set_config(…, true)`. At startup `tutor.db.engine.check_app_role` refuses a login that is a superuser or BYPASSRLS, is a member of such a role, owns (or is a member of a role owning) user tables, owns any relation, function or schema, is a member of `pg_database_owner`, has any membership other than `tutor_app`, or is not a member of `tutor_app`; in prod `DATABASE_URL` must be a PostgreSQL URL.
   - **`tutor_report`** for the gate report (`GATE_REPORT_DATABASE_URL`, amendment 42).
   - `tutor_app` is a NOLOGIN role created by migration 0001; migration 0002 refuses to run if it is SUPERUSER or BYPASSRLS. User resolution by Google `sub` uses a second policy branch on `users` (`google_sub = app.google_sub`) instead of a `SECURITY DEFINER` function. `GRANT … WITH INHERIT FALSE, SET TRUE` and `pg_has_role(…, 'SET')` are why PostgreSQL 16 or later is required.
6. **Leech rule** (section 10.2). "A third appearance within 30 days" means `seen_count` reaches 3 while `created_at` is within the last 30 days.
7. **The rating-4 upgrade replays FSRS** (sections 5, 10.3). `review_logs` stores the state before each review (`state_before` JSONB); at `end_session` an upgraded item's state is recomputed from it with rating 4, and that log row's `rating` is set to 4. The upgrade runs only when the session closes as `closed` (never for `incomplete`), and skips an item whose review state changed after its review in the same session (a `save_glossary` reinforce or promote), so the replay never overwrites that write. So `review_logs` is append-only except `UPDATE (rating)`, the only column `tutor_app` may update there.
8. **Refresh tokens last 30 days from the first login** (section 13, ADR 0002): `fallback_refresh_token_expiry_seconds = 30 × 24 × 3600` (the library default is one year). Google sends no refresh expiry, so each rotation keeps the original expiry: after 30 days the learner signs in again. Access tokens are set explicitly to 3600 s.
9. **Archived glossary items** are never produced in v0 and count as `confirmed` in the save rules and the gate report.
10. **The website hides `declined` items** (section 12). The dashboard's glossary status has no `declined` value; every web glossary query filters `status <> 'declined'`.
11. **Postponed dashboard pages are not registered** (section 12). The website registers no `/pricing`, `/webhooks/stripe`, `/billing/*`, `/admin/*`, `/app/plan`, `/app/progress`, `/app/reports` or `/app/account`. Navigation (sidebar and tab bar) shows Inicio, Perfil, Sesiones, Glosario and Conectar; the "Más" menu, plan chip, Free meter and admin link are removed. The billing, settings and webhook ports stay unwired; the export and deletion adapters raise `NotImplementedError` and no route reaches them.
12. **MCP tools are plain `def` functions** (D7, section 4). FastMCP 4 runs sync tools in a worker thread with context variables propagated, which is what `anyio.to_thread.run_sync` was for. Only the identity middleware calls `anyio.to_thread.run_sync` (to resolve the user before the tool runs).
13. **Turn and evidence-string caps are enforced by the tool schema and the 64 KB body limit** (section 5), not by database `CHECK`s, because they live inside `raw_evidence` JSONB. The service enforces the 20 KB `raw_evidence` cap; the column keeps a 64 KB `CHECK` as a backstop. The other section 5 caps (`goal_text`, `prep_text`, `said`/`correct`, glossary `text`, `meaning`, `context_sentence`) are `CHECK`s as written.
14. **The OAuth store is a `FileTreeStore` wrapped in Fernet encryption** (section 4, ADR 0002), not a `DiskStore`: `diskcache` carries CVE-2025-69872 with no fixed release, which `pip-audit` rejects. The store is a named volume owned by the image's runtime user and is backed up nightly as an encrypted-entry tarball (amendment 46).
15. **`web_sessions.token_hash` is the capability, and a cookie cannot attach a session to a user** (section 5). The policy reads `token_hash = app.web_session OR user_id = app.user_id`: the session middleware loads a session by its SHA-256 token hash before it knows the user, and an anonymous session's OAuth state is visible only to the holder of its cookie. Migration 0002 tightened the write check to `(token_hash = app.web_session AND (user_id IS NULL OR user_id = app.user_id)) OR user_id = app.user_id`, so a cookie alone may create or touch an anonymous session but never bind it to someone else. Migration 0004 adds owner-only `SELECT`/`DELETE` policies for the purge function (amendment 31).
16. **`save_glossary` also reports `promoted`** (section 8.1): items moved from `provisional` or `declined` to `confirmed`. The output is `{new, reinforced, promoted, rejected[]}`.
17. **Free usage has no caps in v0** (sections 3.2, 12). The website's usage port returns real counts with an `UNCAPPED` sentinel, so no limit banner and no "N of M" counter appears.
18. **Inicio's week trail without a calendar** (section 12). Plan-lite has no dates, so a day is "planned" only when the learner closed an on-plan session that day, or today when a plan item is pending. No other date is invented, so no day reads "sin sesión".
19. **Perfil carries the language switch, logout and the deletion contact** (sections 6.3, 12). Cuenta is postponed and the narrow layout hides both controls. Entrar and the privacy page say what is stored, that lessons pass through the learner's LLM vendor, and that copies and deletion are by email to the author (`TUTOR_SUPPORT_EMAIL`).
20. **A first Google login without a profile lands on Perfil**, whatever `next` says (section 6.3). Field errors use the domain's error codes for the es-MX messages; `save_profile` still decides validity, and the web form sends the raw (stripped) date and timezone to it. The test-only login (`TUTOR_TEST_LOGIN`, refused outside `TUTOR_ENV=test` on a loopback URL) skips this redirect.
21. **Request size and request log on the website too** (sections 5, 13). The website sits behind the same 64 KB body limit as MCP (`BodySizeGuard`, every method with a body; the web 413 is plain JSON). One `RequestLog` writes a JSON line on `tutor.http` per request of the website and the OAuth and well-known routes: the matched route template from `tutor.app.route_label` (never the raw path, which can carry ids or anything a client types; unmatched paths log as `unmatched`), method, status, latency and `user_hash`. Never the query string (authorization codes travel in it), headers, body or exception text. `/mcp` is logged only by the tool-call log. The uvicorn access log is off.
22. **`glossary_saved` audit event; the confirmation rate survives purges** (sections 5, 10.4, 13, 14). Every `save_glossary` writes one `glossary_saved` audit row: the session id, the requested status and, per applied item, its id, action (`insert`, `reinforce`, `promote`, `set_status`) and resulting status; ids and enums only, never text. The gate report replays these rows to each item's final status, so provisional items purged after 7 days (section 9.4) stay in the denominator. The section 13 audit events are `user_created`, `web_login`, `mcp_first_use`, `profile_saved`, `plan_generated`, `session_closed` and `glossary_saved`, plus the `user_deleted` row the deletion script writes (amendment 23). There are no audit rows without a user in the app; failed web logins are logged, not audited.
23. **Deletion script** (section 13). The documented SQL (runbook "User deletion") writes the 12-hex user hash into `audit_log.meta` and a `user_deleted` row before deleting the `users` row; other user tables cascade and `audit_log.user_id` becomes NULL. Before it, the learner removes the connector in Claude **and** revokes the app at their Google account: the OAuth store has no per-user index, so Google revocation is what ends their tokens (ADR 0002 build notes).
24. **Environment variable names** (section 13). The app (`deploy/tutor.env.example`) reads `TUTOR_ENV`, `TUTOR_BASE_URL`, `TUTOR_MCP_URL`, `TUTOR_SUPPORT_EMAIL`, `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `TUTOR_JWT_SIGNING_KEY`, `TUTOR_OAUTH_STORAGE_KEY`, `TUTOR_OAUTH_STORAGE_DIR`, `TUTOR_PORT`, `TUTOR_HOST`, `FORWARDED_ALLOW_IPS` and `TUTOR_TEST_LOGIN` (empty in prod). The `migrate` service (`deploy/migrate.env.example`) reads `MIGRATION_DATABASE_URL`, `APP_DB_USER`, `APP_DB_PASSWORD` and `REPORT_DB_PASSWORD`; the database container `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`; the tunnel `TUNNEL_TOKEN`; the gate report `GATE_REPORT_DATABASE_URL` (or `--database-url`). The two app secrets (`TUTOR_JWT_SIGNING_KEY`, `TUTOR_OAUTH_STORAGE_KEY`) must each be present, at least 32 characters (the storage key a valid Fernet key) and different; `FORWARDED_ALLOW_IPS="*"` is refused in prod. Section 13's `WEB_SESSION_SECRET` is dropped: web sessions are random tokens stored as SHA-256 hashes (amendment 15), so no secret signs them. `TUTOR_ENV` is stripped by both settings readers. `TUTOR_MCP_URL` is optional: when empty it is `TUTOR_BASE_URL` + `/mcp`, and any other value stops startup (the message names the keys, never the values).
25. **`TUTOR_BASE_URL` is https unless the host is loopback** (sections 4, 13). It is parsed with `urlsplit`: a host is required, no user info, query or fragment, and the path is empty or `/`; `http` is accepted only for `localhost` or a loopback address, whatever `TUTOR_ENV` says. The website answers only to that host (`TrustedHostMiddleware`, other hosts get 400), so redirect targets cannot come from a forged `Host`.

### Tenancy and concurrency

26. **Composite tenant-safe foreign keys** (sections 5, 13). PostgreSQL checks foreign keys without row-level security, so a single-column FK would let a row scoped to user B point at user A's row. Migration 0002 adds `UNIQUE (id, user_id)` on `plans`, `plan_items`, `sessions` and `glossary_items` and makes every reference between user-owned tables composite `(parent_id, user_id) → parent (id, user_id)`; `sessions.plan_item_id` uses `ON DELETE SET NULL (plan_item_id)` (PostgreSQL 15+), so deleting a plan item never nulls `user_id`. 0002 also narrows the app's `INSERT` on `users` to `google_sub, email, display_name, created_at`.
27. **Foreign writes raise `LookupError`** (repository contract, `tutor.services.ports`). Writes that name another user's (or a nonexistent) session or glossary item (`save_metrics`, `save_errors`, `GlossaryRepo.apply`, `ReviewRepo.save_state`, `ReviewRepo.log`, `PlanRepo.mark_done` for its session) check ownership with a `SELECT` first and raise `LookupError`; `SessionRepo.create` raises it for a `plan_item_id` the user cannot see. `mark_done` returns `False` for a foreign or already done plan item. A foreign id is a caller bug, so it fails loudly instead of relying on FK checks that bypass RLS. The in-memory and Postgres stores pass the same `RepoContract` tests.
28. **One user's units of work run one at a time** (sections 4, 13). `PgUnitOfWork` takes `pg_advisory_xact_lock(hashtextextended(user_id::text, 0))` right after scoping, so concurrent tool calls of one user serialize (no lock-order deadlocks; two concurrent inserts of the same glossary text see one row). Different users never wait for each other.
29. **`end_session` locks the session row** (section 11.1). `PgSessionRepo.get` reads the session with `SELECT … FOR UPDATE`, so a concurrent second `end_session` waits for the first to commit, then sees `result` and returns `already_closed`; an integration test runs the race on two threads.
30. **`end_session` on a superseded plan also closes the active plan's base item** (sections 7.2, 7.3). If the profile changed while a session was open, the session's plan item belongs to a superseded version. `end_session` marks it done and, when the active plan still holds a `pending` `base` item for the same track item, marks that done with the same session id, so `start_lesson` never offers that lesson again.
31. **Expired web sessions are purged by the app, not by hand** (D8, section 12). D8's "no background jobs" keeps one exception: `PeriodicPurge` wraps the MCP app's lifespan and calls the purge at startup and then hourly, cancelling on `lifespan.shutdown`, with a 5 s statement timeout. Because RLS hides other people's sessions from `tutor_app`, migration 0004 adds a `SECURITY DEFINER` function `purge_expired_web_sessions`, and migration 0005 replaces it with a `STRICT` one that takes only `p_now` (capped by the server's `now()`) and fixed lifetimes of 14 days idle, 30 days absolute and 10 minutes for a session that never signed in. The app asserts at startup that these equal its `WebConfig`, so a compromised app role cannot end live sessions early.

### Services and MCP

32. **`record_review` grades an item at most once per local day** (sections 8.1, 10.3). If the item's FSRS `last_review` falls on today's local date (any session), the result is `already_recorded` with the current due date, and neither the log nor the state changes. Section 8.1 only promised idempotency per (session, item); this is stricter, so a second same-day drill of an item is not graded and gets no rating-4 replay. The tool description says so.
33. **`save_profile` computes `today` in the request's timezone** (sections 6.1, 6.4). When the web form sends a valid timezone it is used for the 28–364-day target-date check and the plan horizon in the same request; otherwise the stored `users.timezone`.
34. **MCP error mapping** (section 8.2). The six service codes stay the closed tool-error enum, with field paths such as `items[0].kind` and never values. Two layers are added:
   - Any other failure inside a tool call (a pydantic error in a tool body, a `ToolError` with a foreign cause, any `Exception`) becomes a fixed `internal_error` tool result with its own `response_rules` and no exception text; a cancelled call is logged as `cancelled`.
   - Framework failures stay protocol errors with fixed messages: an unknown or disabled tool is "Unknown tool", failed authorization or a missing token is "Not authorized" (logged as `unauthenticated`), any other `MCPError` is "Request failed"; no name or text is echoed.
   - FastMCP runs with `mask_error_details=True`. A `ScrubFilter` strips messages, arguments and tracebacks from the FastMCP, MCP SDK and `uvicorn.error` loggers (it keeps numeric arguments, so uvicorn's startup lines survive). The whole `fastmcp.server.auth` subtree is silenced because it logs authorization codes. The call log records `error_class` only for `internal_error`.
35. **MCP input and output schemas** (section 8). Every input model, nested ones included, sets `extra="forbid"`; output models carry `title`/`description` on every field and `Literal` types for closed sets; titles are restored on input and output schemas by one registration step that also checks for drift. FastMCP's `strict_input_validation` stays off, so a number sent as a string (`"1"`) is coerced; enums and unknown-field rejection still apply. `user_turns` has no minimum length, so an empty lesson closes as `incomplete` as section 11.1 says. `record_review`, `save_glossary` and `end_session` carry `destructive_hint=False`; `sessions.client` is always `claude` for MCP sessions.
36. **The Spanish rule applies in every phase** (sections 8.3, 8.4). Requirements section 12 ("Spanish only for a meaning check on request, never longer than one sentence") holds in every phase, feedback included. The only exceptions are the warm-up production cue (the situation given in Spanish or a paraphrase, requirements section 8) and the onboarding prompts and labels the server returns in the learner's language. The 60-word text limit applies in the conversation phase only. "In voice answer in 1–3 sentences" stays global, as requirements section 7 says.
37. **Track content is pinned by a hash** (section 7.1). A unit test pins the SHA-256 of the canonical seed rows. Until the first production deploy, author edits to `src/tutor/content/track_it_v0.yaml` update that hash; after it, every track change ships as a new seed revision (upsert or delete by id). The seed migration declares its tables inline, so later column changes cannot break it. Migrations never import `tutor.db.tables`.

### Website

38. **Web login: PKCE and a strict ID-token audience** (section 13, dashboard spec 9.3). The Authlib Google client uses `code_challenge_method=S256` (the verifier lives in the server-side session) and validates the ID token with mandatory issuer and `aud == GOOGLE_CLIENT_ID`. Sign-in resolves the user by Google `sub` through the `users` policy branch, never by email. `next` goes through `safe_next` on the way in and again on the callback.
39. **Web sessions: create and touch are separate, pre-login sessions are short** (section 12). `WebSessionStore.create_session` only inserts, and `touch_session` only updates (`UPDATE … WHERE token_hash = :h`, true only when exactly one row changed). A session deleted in flight is never revived; the response then clears the cookie. An anonymous (pre-login) session lives at most 10 minutes; signed-in sessions expire after 14 days idle or 30 days absolute. Login copies only allow-listed keys from the anonymous session (none today). Logout with an expired or missing session redirects to `/login` and clears the cookie without a CSRF check (nothing to forge).
40. **CSRF by default, checked at startup** (section 13). Every unsafe-method web route depends on `require_csrf` (token compared in constant time). At app creation `assert_csrf_everywhere` walks every route, including included routers, through `tutor.web.routing.iter_api_routes`. The walker fails closed: an unknown node, a mount other than `/static`, a low-priority route, or an include with its own prefix or dependencies stops startup. The only exemptions are `/auth/logout` (`require_csrf_if_session`) and `/auth/test-login` when the test login is enabled. `/webhooks/stripe` is not registered.
41. **Per-user write limits on the website** (section 13, which said "web routes as in the dashboard spec"). Every unsafe `/app/*` route shares a per-user sliding-window limit of 30 per minute. `POST /app/profile` has a tighter 10 per minute, because each save can write a plan version. The limit returns a fixed 429 page that echoes no input. Per-IP limits for the unauthenticated OAuth and login endpoints live at the Cloudflare edge (amendment 45). Limiters are in-process, which matches the single-process deploy. Web routes that call database ports run off the event loop.

### Operations

42. **The gate report runs as a least-privilege role** (sections 4 "`tutor.ops`" row, 14; the table said "Runs as the owner role"). Under `FORCE ROW LEVEL SECURITY` the owner sees no rows, and the app login sees only one user's rows. So the report connects as `tutor_report`: `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS`, `SELECT` only on `sessions`, `session_metrics` and `audit_log`, with `default_transaction_read_only` on. The `migrate` step provisions it with a password from `REPORT_DB_PASSWORD`. The report reads `GATE_REPORT_DATABASE_URL` (or `--database-url`), refuses a role that RLS would filter (`require_unrestricted_role`), starts every transaction with `SET TRANSACTION READ ONLY`, and exits with a fixed message on connection or parse errors. User hashes (first 12 hex of SHA-256 of the user id) are pseudonymous, not anonymous; `labels*.json` is git-ignored.
43. **Evals redaction guard** (section 14). `evals/fidelity/redact.py` also masks phones, handles and URLs. It writes redacted copies to a new path, never in place, and refuses inputs outside `raw/`. `evals/README.md` calls redaction best effort, needing a human read before commit. A test (`evals/test_fidelity_tools.py::test_committed_fixtures_hold_no_email_phone_or_uuid`) fails `just check` if committed fixtures contain an email, a phone number or a UUID.
44. **Deployment networks and hardening** (section 4 "Deployment"). Compose runs `db`, a one-shot `migrate`, `app` and `cloudflared`; nothing is published on the host. Two networks:
    - `backend` is `internal: true` (no egress) and holds `db`, `migrate` and `app`;
    - `edge` holds `app` and `cloudflared`, which has the fixed address `172.30.10.3` outside the dynamic `ip_range`. It is the only `FORWARDED_ALLOW_IPS` entry, and `*` is refused in prod.

    Third-party images are pinned by `@sha256:` digest. `db` drops all capabilities except the five it needs. The app runs read-only as uid 10001 with `server_header` off. Role passwords are sent as SCRAM verifiers. The image health check calls an MCP-side path, because the website rejects any other `Host` than the public one.
45. **Edge rules** (sections 4, 13). The Cloudflare rules in the runbook are part of the deployment:
    - Always Use HTTPS, TLS ≥ 1.2 and zone HSTS;
    - a per-IP rate limit on `/register`, `/authorize`, `/token`, `/consent`, `/oauth/callback`, `/auth/google` and `/auth/callback` (path-only on the Free plan);
    - an optional Skip rule for Anthropic's egress range `160.79.104.0/21`, so all learners' Claude calls do not count against one IP;
    - Bot Fight Mode off on Free (it would challenge Claude's connector calls).
46. **Backups include roles and the OAuth store** (sections 3.1, 4). The nightly `backup.sh` (umask 077) writes a custom-format `pg_dump` checked with `pg_restore --list`, `pg_dumpall --roles-only` (roles are cluster-level and not in the dump) and a tarball of the OAuth volume, each written as `.partial` and renamed, with 30-day retention. `check_backup.sh` alerts when the newest dump is older than 26 hours. Off-site copies are encrypted (age or gpg). The restore drill runs monthly and once before inviting testers.

### Final review fixes

47. **Provisional items come back ready to send** (sections 8.1, 8.4, 10.2). `start_lesson`'s `provisional_items` carry `item_id`, `kind`, `text`, `meaning`, `context_sentence` and `domain`. The `start_lesson` rule asks the model to send each one back to `save_glossary` exactly as returned (without `item_id`) with status `confirmed` or `declined`, and the `save_glossary` description allows items from `provisional_items` as well as from the current lesson. Without the context sentence the save was rejected as `missing_context`, so no provisional item could be decided in the next lesson.
48. **The 30-day declined purge counts from `created_at`** (sections 9.4, 10.2). In v0 a declined item is deleted 30 days after it was first saved, not 30 days after it was declined; a provisional item declined late is therefore kept for less than 30 days after the decision.
