# Requirements edits for core loop v0

`docs/requirements.md` is hook-protected, so only the author can change it. This list holds every edit that core loop v0 implies. It covers spec section 17 (items 17.1–17.8), corrected by the spec's Amendments, plus the places where the build diverges from the requirements text.

For each edit:
- **Find** is quoted exactly from `docs/requirements.md` as of 2026-10-05 (search for it; line numbers are only a hint).
- **Replace with** is the paste-ready text.

Spec: `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md` (section 17 and "Amendments", cited as A1–A46).

Paste at least E1–E2 before the `phase-auditor` runs for v0. Until then it judges the old v0 row ("a plain page… No dashboard") and flags the website as out of scope. Also set the `CLAUDE.md` "Current phase" line to `Core loop v0 (section 16)`.

## Section 16: milestones (spec 17.1, 17.2)

### E1. v0 acceptance row (17.1)

Find (about line 522):

```text
| Core loop v0 (Oct 12–Nov 6) | A Free Claude account can connect via OAuth, run `start_lesson` (plan item or `prep` event) → scenario → feedback → `save_glossary` → `end_session`; FSRS reviews appear in the next session; metrics computed from evidence; a plain page lists glossary and sessions; 90% test coverage on FSRS, metrics, validation. No dashboard, billing or reports |
```

Replace with:

```text
| Core loop v0 (Oct 12–Nov 13) | A Free Claude account can connect via OAuth, complete onboarding in chat or on the web, get a starter plan from the IT track, run `start_lesson` (plan item or `prep` event) → scenario → feedback → `save_glossary` → `end_session`; FSRS reviews appear in the next session; metrics computed from evidence; the website shows profile, plan-lite, sessions and glossary on the approved dashboard design; 90% test coverage on FSRS, metrics, validation. No diagnostic, billing or reports |
```

### E2. Gate, decision and plan-engine rows (17.2)

Find (about lines 523–525):

```text
| Validation gate (Nov 9–27) |
```

```text
| Gate decision (Nov 30) |
```

```text
| Plan engine, metrics, eval tests (Dec 1–23) |
```

Replace with, in the same order (keep the rest of each row as it is):

```text
| Validation gate (Nov 16–Dec 4) |
```

```text
| Gate decision (Dec 7) |
```

```text
| Plan engine, metrics, eval tests (re-estimated at the gate) |
```

### E3. Gantt chart (17.2)

Find:

```text
    Core loop v0: sessions, glossary       :core, 2026-10-12, 2026-11-06
```

```text
    15 real sessions, 3 users              :gate, 2026-11-09, 2026-11-27
    Gate decision: continue or stop        :milestone, crit, gatedecision, 2026-11-30, 0d
```

```text
    Plan engine, metrics, eval tests       :planengine, 2026-12-01, 2026-12-23
```

Replace with:

```text
    Core loop v0: sessions, glossary       :core, 2026-10-12, 2026-11-13
```

```text
    15 real sessions, 3 users              :gate, 2026-11-16, 2026-12-04
    Gate decision: continue or stop        :milestone, crit, gatedecision, 2026-12-07, 0d
```

```text
    Plan engine (re-estimated at the gate) :planengine, 2026-12-08, 2026-12-23
```

Spec 17.2 only says the plan engine is "re-estimated at the gate". The gantt needs dates, so `2026-12-08 → 2026-12-23` is a placeholder that keeps the bar after the decision. Change it if you prefer.

### E4. Gate decision date in the prose (17.2)

Find:

```text
Nothing in the plan engine, dashboard, billing or reporting is started before the gate decision on November 30,
```

Replace with:

```text
Nothing in the plan engine, dashboard, billing or reporting is started before the gate decision on December 7,
```

## Section 4: architecture and stack (spec 17.3, A2, A12, A31)

### E5. Auth row (17.3)

Find:

```text
| Auth | Authlib (OIDC with Google) + own OAuth 2.1 authorization server with PKCE and dynamic client registration | Required by MCP spec for remote servers; Claude registers as a client dynamically |
```

Replace with:

```text
| Auth | FastMCP `GoogleProvider` OAuth proxy for MCP clients (ADR 0002); Authlib OIDC with Google for the website. Own authorization server reconsidered before the private beta | Required by MCP spec for remote servers; Claude registers as a client dynamically |
```

### E6. Website and worker in v0 (new divergence; A2, A31, spec D8)

Find:

```text
The client talks only to the MCP endpoint; the dashboard talks only to the REST API; both share one OAuth session and one database. The worker runs scheduled jobs (FSRS due-date recalculation, weekly reports, provisional-data cleanup) and is the only component that sends email.
```

Replace with:

```text
The client talks only to the MCP endpoint; the dashboard talks only to the REST API; both share one OAuth session and one database. The worker runs scheduled jobs (FSRS due-date recalculation, weekly reports, provisional-data cleanup) and is the only component that sends email.

In core loop v0 there is no REST API and no worker. The website is rendered by the same process and calls the same service functions; it has its own Google login and cookie, and both logins resolve to the same `users` row by Google `sub`. Provisional and declined glossary items are purged inside `start_lesson`. Expired web sessions are purged by the app at startup and then hourly. No email is sent.
```

## Section 5: authentication, authorization and security (spec 17.4, A5, A8, A22, A26, A41, A45)

### E7. Login flow note (17.4)

Find:

```text
5. Dashboard uses a separate session cookie (HttpOnly, Secure, SameSite=Lax) from the same Google login.
```

Replace with:

```text
5. Dashboard uses a separate session cookie (HttpOnly, Secure, SameSite=Lax) from the same Google login.

In v0 the OAuth proxy issues and refreshes tokens; see ADR 0002 for which rules the library meets. Refresh tokens last 30 days from the first login (each rotation keeps the original expiry), so a learner re-authorizes at most every 30 days.
```

### E8. Database roles and tenant-safe references (new divergence; A5, A26, A42)

Find:

```text
- Every table with user data has `user_id`; every query is filtered by the token's subject. Row-level security in Postgres is enabled as a second layer.
```

Replace with:

```text
- Every table with user data has `user_id`; every query is filtered by the token's subject. Row-level security in Postgres is enabled as a second layer.
- Three database roles: the migration owner (migrations and backups only); the app's login role (no superuser, no BYPASSRLS, owns nothing, member of the NOLOGIN `tutor_app` role only, which every transaction switches to; the server refuses to start otherwise); and a read-only `tutor_report` role for the gate report (BYPASSRLS, `SELECT` on three tables). References between user tables are composite `(id, user_id)` foreign keys, so a row can never point at another user's row.
```

### E9. Plan limits not enforced in v0 (17.8, second half)

Find:

```text
- Plan limits (section 3) are enforced server-side per tool call, never by the client.
```

Replace with:

```text
- Plan limits (section 3) are enforced server-side per tool call, never by the client. They are not enforced before billing ships (core loop v0 and the gate).
```

### E10. Rate limits (new divergence; A41, A45)

Find:

```text
- Rate limits per user and per IP on every endpoint (default 60 tool calls/minute, 10 session starts/day).
```

Replace with:

```text
- Rate limits per user and per IP on every endpoint (default 60 tool calls/minute, 10 session starts/day). In v0, limits are per user inside the app: 60 MCP calls/minute, 10 lesson starts per local day, 30 website writes/minute, and 10 profile saves/minute. Per-IP limits apply at the Cloudflare edge to the unauthenticated OAuth and login endpoints.
```

### E11. Audit log (new divergence; A22, ADR 0002)

Find:

```text
- Audit log of login, token issuance, plan changes and data deletion.
```

Replace with:

```text
- Audit log of login, token issuance, plan changes and data deletion. v0 events: `user_created`, `web_login`, `mcp_first_use`, `profile_saved`, `plan_generated`, `session_closed`, `glossary_saved` (ids and enums only), and `user_deleted`. Token issuance inside the OAuth proxy is not audited in v0, an accepted gap (ADR 0002).
```

### E12. Export and deletion before the dashboard ships (new divergence; A19, A23)

Find:

```text
- Data export (JSON) and full account deletion from the dashboard, completed within 24 h; required for Mexican LFPDPPP and useful for GDPR-style users.
```

Replace with:

```text
- Data export (JSON) and full account deletion from the dashboard, completed within 24 h; required for Mexican LFPDPPP and useful for GDPR-style users. Until the dashboard ships them (core loop v0 and the gate), copies and deletion are by email request to the author, using the documented deletion script.
```

## Section 6: data model (spec 17.5 as amended: A3, A4, A7, A26)

Spec 17.5 listed `mcp_clients_seen` and `profiles.timezone`. The build replaced them: `users.mcp_first_seen_at` (A3), and the UI preferences and timezone on `users` (A4). It also added `review_logs.state_before` (A7). The rows below are what migrations 0001–0005 create.

### E13. Table count

Find:

```text
Eleven tables carry v1; raw evidence lives in JSONB so metrics can be recomputed when the formulas change.
```

Replace with:

```text
These tables carry v1 (core loop v0 adds `track_items`, `track_chunks`, `session_errors` and `web_sessions`); raw evidence lives in JSONB so metrics can be recomputed when the formulas change.
```

### E14. `users`

Find:

```text
| `users` | id, google_sub, email, display_name, native_lang, created_at, deleted_at | Soft delete, then purge job |
```

Replace with:

```text
| `users` | id, google_sub, email, display_name, native_lang, role (learner/admin), lang, timezone, reduce_motion, install_prompt_dismissed_at, last_celebrated_session_id, deletion_requested_at, email_weekly, email_reminders, mcp_first_seen_at, created_at, deleted_at | UI preferences and timezone live here because the website reads them before onboarding; `mcp_first_seen_at` marks the first MCP tool call. Soft delete, then purge job (v0: deletion by request, by script) |
```

### E15. `profiles`

Find:

```text
| `profiles` | user_id, domains[] (it, daily, business…), goal_text, minutes_per_day, days_per_week, current_level_speaking, current_level_writing, target_level, target_date | Levels stored as CEFR + numeric 1.0–6.0 |
```

Replace with:

```text
| `profiles` | user_id, domains[] (it, daily, business…), use_cases[], goal_text, minutes_per_day, days_per_week, self_level, current_level_speaking, current_level_writing, target_level, target_date, onboarded_at, updated_at | Levels stored as CEFR + numeric 1.0–6.0. `self_level` is the onboarding self-rating; `current_level_*` arrive with the diagnostic, so a self-rating is never stored as a measured level |
```

### E16. `plan_items`

Find:

```text
| `plan_items` | plan_id, week_no, order_no, cefr_descriptor (can-do id), skill (speaking/writing/listening), domain, scenario_hint, status (pending/done/skipped), done_session_id | The unit the lesson skill consumes |
```

Replace with:

```text
| `plan_items` | plan_id, user_id, week_no, order_no, cefr_descriptor (can-do id), skill (speaking/writing/listening), domain, scenario_hint, status (pending/done/skipped), done_session_id | The unit the lesson skill consumes. v0 (plan-lite) stores `track_item_id` and `variant` (base/complication) instead of the descriptor, skill, domain and hint, which come from the track item |
```

### E17. New tables after `cefr_descriptors`

Find:

```text
| `cefr_descriptors` | id, level, skill, text_en, text_es | Static seed: CEFR can-do statements B1–C1 |
```

Replace with:

```text
| `cefr_descriptors` | id, level, skill, text_en, text_es | Static seed: CEFR can-do statements B1–C1 |
| `track_items` | id (`it-07`), domain, order_no, cefr, can_do_en, can_do_es, skill, interaction_type, use_cases[], character, objective, obstacle, scenario_hint | Static seed (v0 IT starter track, 24 items); stands in for `cefr_descriptors` until the plan engine |
| `track_chunks` | id (`it-07-c3`), track_item_id, position (1–5), text, example | Static seed; the ids `chunks_used[]` refers to |
```

### E18. `sessions`

Find:

```text
| `sessions` | id, user_id, plan_item_id, started_at, ended_at, status (open/closed/incomplete), mode (voice/text), client (claude/chatgpt/code), task_result, hints_given, cefr_estimate_speaking, confidence_1_5, raw_evidence JSONB | `raw_evidence` = the full `end_session` payload |
```

Replace with:

```text
| `sessions` | id, user_id, plan_item_id, track_item_id, prep_text, started_at, ended_at, status (open/closed/incomplete), mode (voice/text), client (claude/chatgpt/code), low_trust, brief_variant (base/complication/simpler), chunks_offered[], task_result, hints_given, cefr_estimate_speaking, cefr_confidence, cefr_excluded, confidence_1_5, raw_evidence JSONB, result JSONB | `raw_evidence` = the full `end_session` payload; `result` = the stored `end_session` response for idempotent repeats |
```

### E19. `session_metrics` and the new `session_errors`

Find:

```text
| `session_metrics` | session_id, user_words, assistant_words, user_ratio, turns, unique_lemmas, words_per_turn, l1_switches, errors_total, errors_by_category JSONB, recurring_errors, chunks_offered, chunks_used, uptake_count | Computed by the server from `raw_evidence` |
```

Replace with:

```text
| `session_metrics` | session_id, user_id, user_words, assistant_words_estimate, user_ratio, turns, words_per_turn, duration_min, user_words_per_min, unique_lemmas, lexical_diversity, l1_switches, errors_total, errors_rejected, errors_by_category JSONB, errors_per_100w, recurring_errors, uptake_count, chunks_offered, chunks_used, chunks_rejected, activation_rate | Computed by the server from `raw_evidence`; `unique_lemmas`, `lexical_diversity` and `l1_switches` stay empty in v0 |
| `session_errors` | id, session_id, user_id, said, correct, correct_norm, category, turn_index | Validated errors only; makes recurring errors a query |
```

### E20. `glossary_items` (17.5, `declined`)

Find:

```text
| `glossary_items` | id, user_id, kind (correction/chunk/term), text, meaning, context_sentence, domain, origin_session_id, status (provisional/confirmed/archived), seen_count, last_seen_session_id | Unique on (user_id, normalized text) |
```

Replace with:

```text
| `glossary_items` | id, user_id, kind (correction/chunk/term), text, text_norm, meaning, context_sentence, domain, origin_session_id, status (provisional/confirmed/declined/archived), seen_count, last_seen_session_id, leech (bool), provisional_expires_at, created_at, updated_at | Unique on (user_id, normalized text). `declined` = proposed and dropped by the user: never scheduled, purged after 30 days, hidden on the website |
```

### E21. `review_states`

Find:

```text
| `review_states` | glossary_item_id, stability, difficulty, due_at, last_review_at, reps, lapses, leech (bool) | FSRS-4.5 state per item |
```

Replace with:

```text
| `review_states` | glossary_item_id, user_id, stability, difficulty, due_at, last_review_at, reps, lapses, last_ratings (last 2) | FSRS-4.5 state per item; `leech` lives on `glossary_items` |
```

### E22. `review_logs` (A7)

Find:

```text
| `review_logs` | id, glossary_item_id, session_id, rating (1–4), reviewed_at, elapsed_days | Append-only; feeds FSRS optimizer later |
```

Replace with:

```text
| `review_logs` | id, glossary_item_id, user_id, session_id, rating (1–4), reviewed_at, elapsed_days, state_before JSONB | Append-only, except that `end_session` may raise `rating` from 3 to 4 (FSRS replayed from `state_before`); unique (session_id, glossary_item_id); feeds FSRS optimizer later |
```

### E23. `audit_log` and the new `web_sessions`

Find:

```text
| `audit_log` | user_id, event, meta JSONB, at | Security events (section 5) |
```

Replace with:

```text
| `audit_log` | user_id, event, meta JSONB, at | Security events (section 5); append-only for the app |
| `web_sessions` | token_hash (SHA-256 of the cookie token), user_id (null before login), csrf_token, data JSONB, created_at, last_seen_at | Website sessions: 14 days idle, 30 days absolute, 10 minutes before login |
```

### E24. Integrity rule: tenant-safe references (A26)

Find:

```text
- One `open` session per user; `start_lesson` closes any previous open session as `incomplete` before creating a new one.
```

Replace with:

```text
- One `open` session per user; `start_lesson` closes any previous open session as `incomplete` before creating a new one.
- Every reference between user-owned tables is a composite `(id, user_id)` foreign key (Postgres checks foreign keys without row-level security).
```

## Section 7: MCP surface (spec 17.6, A16, A32)

### E25. Tool count and the v0 subset (17.6)

Find:

```text
Nine tools, three prompts and one `instructions` string make up the whole MCP contract.
```

Replace with:

```text
Ten tools, three prompts and one `instructions` string make up the whole MCP contract. Core loop v0 ships six of the tools (`get_profile`, `save_profile`, `start_lesson`, `record_review`, `save_glossary`, `end_session`), the `/start-lesson` prompt and `instructions`; the rest come after the gate.
```

### E26. `get_profile` and the new `save_profile` row (17.6)

Find:

```text
| `get_profile` | — | profile, plan summary, streak, open-session flag, provisional items count | Read |
```

Replace with:

```text
| `get_profile` | — | profile, or `onboarding_needed` with the onboarding questions; plan summary, streak, open-session id, provisional items count, due reviews count | Read |
| `save_profile` | `self_level`, `domains`, `use_cases`, `minutes_per_day`, `days_per_week`, `target_level`, `target_date?`, `goal_text?` | profile, plan summary, feasibility | Identical answers are a no-op; changed plan inputs write a new plan version |
```

### E27. `start_lesson` row (17.6, `prep_use_case`)

Find:

```text
| `start_lesson` | `mode` (voice/text), `domain?`, `minutes?`, `prep?` (free-text event, e.g. "standup tomorrow about the outage") | `session_id`, today's plan item (can-do), 5 chunks with examples, scenario brief, due reviews (≤ 8), provisional items to confirm, `response_rules` | Closes any open session as `incomplete`; enforces plan limits |
```

Replace with:

```text
| `start_lesson` | `mode` (voice/text), `domain?`, `minutes?`, `prep?` (free-text event, e.g. "standup tomorrow about the outage"), `prep_use_case?` (sent together with `prep`) | `session_id`, today's plan item (can-do), 5 chunks with examples, scenario brief, due reviews (≤ 8, each with its review format), provisional items to confirm, `response_rules` | Closes any open session as `incomplete`; 10 starts per local day; enforces plan limits (none before billing ships) |
```

### E28. `record_review` row (A32, new divergence)

Find:

```text
| `record_review` | `results[]` of `{item_id, rating 1–4}` | updated due dates | Idempotent per (session, item) |
```

Replace with:

```text
| `record_review` | `session_id`, `results[]` of `{item_id, rating 1–4}` (≤ 8) | per item: next due date and `recorded` or `already_recorded` | Idempotent per (session, item); an item is graded at most once per local day, so a second grade the same day returns `already_recorded` and changes nothing |
```

### E29. `save_glossary` row (17.6 `declined`, A16 `promoted`)

Find:

```text
| `save_glossary` | `session_id`, `status` (confirmed/provisional), `items[]` of `{kind, text, meaning, context_sentence, domain}` | `{new, reinforced, rejected[]}` | Dedup by normalized text; max 10 per call |
```

Replace with:

```text
| `save_glossary` | `session_id`, `status` (confirmed/provisional/declined), `items[]` of `{kind, text, meaning, context_sentence, domain}` | `{new, reinforced, promoted, rejected[]}` | Dedup by normalized text; max 10 per call; provisional or declined → confirmed is `promoted`; confirmed → provisional or declined is rejected |
```

## Section 10: glossary and spaced repetition (spec 17.7, A6, A7, A32)

### E30. States: `declined` (17.7)

Find:

```text
`end_session` cannot write to the glossary. Untouched items stay `provisional` for 7 days, then deleted.
```

Replace with:

```text
`end_session` cannot write to the glossary. Untouched items stay `provisional` for 7 days, then deleted. If the user drops a proposed item, the LLM calls `save_glossary(status=declined)`; declined items are never scheduled and are purged after 30 days.
```

### E31. Leech threshold (A6, new divergence)

Find:

```text
A third reappearance within 30 days flags `leech = true`.
```

Replace with:

```text
A third appearance (`seen_count` reaches 3) within 30 days of the item's first save flags `leech = true`.
```

The current text, read literally, means a fourth sighting. The build implements the spec's "third appearance" (spec 10.2, A6). If you meant four, say so: it is a one-constant change.

### E32. Rating 4 (spec 10.3 and A7, new divergence)

Find:

```text
4 = produced correctly and used spontaneously later in the session (set automatically when the item appears in `chunks_used`).
```

Replace with:

```text
4 = produced correctly and used spontaneously later in the session (set by the server at `end_session` when a reviewed item rated 3 appears in at least two distinct `user_turns`, the drill and a later turn; FSRS is recomputed from the state stored before the review; only when the lesson closes as complete, and never for an item the same lesson saved to the glossary again). An item is graded at most once per local day.
```

`chunks_used` holds the ids of today's five chunks, not glossary items, so the requirement could not be implemented as written. Spec 10.3 replaced it with the `user_turns` heuristic.

### E33. Where the review format is sent

Find:

```text
**Review formats** (the server picks one per item and sends it in `get_due_reviews`)
```

Replace with:

```text
**Review formats** (the server picks one per item and sends it in `get_due_reviews`; in v0, in `start_lesson`'s `due_reviews`)
```

## Section 3: plans (spec 17.8)

### E34. Free caps not enforced before billing

Find:

```text
Monthly billing at USD 1–2 is rejected: Stripe fees (~USD 0.30 + 2.9%) consume 25–35% of each charge.
```

Replace with:

```text
Monthly billing at USD 1–2 is rejected: Stripe fees (~USD 0.30 + 2.9%) consume 25–35% of each charge.

Free-plan caps are not enforced before billing ships.
```

## Section 14: non-functional (new, small)

### E35. Retention of declined items and web sessions

Find:

```text
| Data retention | Raw evidence 12 months, metrics indefinitely, provisional items 7 days, deleted accounts purged in 24 h |
```

Replace with:

```text
| Data retention | Raw evidence 12 months, metrics indefinitely, provisional items 7 days, declined glossary items 30 days, website sessions 30 days at most, deleted accounts purged in 24 h |
```

## Checked, no edit needed

- **Section 12, the Spanish rule.** The build applies "Spanish is used only for a meaning check on request, never for explanations longer than one sentence" in every phase (A36). The two exceptions are already in the requirements: the warm-up cue in section 8 ("the situation in Spanish or a paraphrase") and the onboarding prompts, which only exist in the spec.
- **Section 7, `end_session`.** The schema, the validation of `said` against `user_turns`, and "≥ 1 entry or `incomplete`" are implemented as written; `user_turns` may be empty and then closes `incomplete`.
- **Section 7, the payload cap.** "Payload size cap 64 KB per call" (section 5) holds for every request with a body, MCP and website (A21).
- **Section 11 metrics** that v0 does not compute (`unique_lemmas`, `lexical_diversity`, `l1_switches`), and the **section 14 rows** for Sentry, Prometheus, settings-table limits, object-storage backups and quarterly drills. These are v1 requirements that spec 3.2 postpones, and the v0 row (E1) does not claim them. v0 backups are nightly and local with an encrypted off-site copy; the restore drill runs monthly.
- **Section 15, risk "OAuth implementation bugs".** The mitigation stands as written: a maintained library (ADR 0002), the Inspector checks (`docs/v0/acceptance.md`), and the external review before the beta.
