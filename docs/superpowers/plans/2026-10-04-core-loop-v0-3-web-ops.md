# Core Loop v0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Free Claude account connects through OAuth, completes onboarding in chat or on the web, gets a deterministic starter plan from the IT track, and runs the full lesson loop with FSRS reviews, server-computed metrics and a small website on the approved dashboard design.

**Architecture:** One Python process. A pure `tutor.domain` holds every rule. `tutor.services` runs one use case per transaction through repository ports, which have an in-memory implementation for unit tests and a Postgres one with row-level security. FastMCP serves six tools behind its Google OAuth proxy. The dashboard web app is mounted beside it through a path dispatcher.

**Tech Stack:** Python 3.12, FastMCP 4.0.x (Google OAuth proxy), FastAPI + Jinja2 + HTMX (dashboard plan), SQLAlchemy 2 Core (sync) + psycopg 3, Alembic, PostgreSQL 16, PyYAML, tzdata, pytest + Hypothesis.

**Spec:** `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md` (approved 2026-10-04). Track content: `docs/content/track-it-v0.yaml`. ADR: `docs/adr/0002-mcp-auth-via-fastmcp-oauth-proxy.md`.

**Status:** waiting for the go decision. The build starts 2026-10-12 only if ADR 0001 (Oct 11) says go. If ADR 0001 changes the voice or `end_session` wording (spec section 16), apply it to Task 19's `rules.py` before dispatching Task 19.

**Plan files:** this plan has three files with the same header and contract.
- `2026-10-04-core-loop-v0-1-domain-services.md`: Tasks 1–13.
- `2026-10-04-core-loop-v0-2-platform.md`: Tasks 14–21.
- `2026-10-04-core-loop-v0-3-web-ops.md`: Tasks 22–28.

Execute them in order as one plan. The SDD ledger names the file that holds the next task (file 1 until Task 13 is complete, then file 2, then file 3).

## Before Task 1 (on 2026-10-12)

1. Confirm ADR 0001 is "go" and record any spike wording changes for Task 19.
2. Confirm the author reviewed `docs/content/track-it-v0.yaml`. Task 3 moves it into the package; review edits after that go to the moved file.
3. `git switch main && git pull`, then `uv run just check` passes, then create the branch `feat/core-loop-v0`.
4. Re-check every fact in the table below with Context7 or the installed source (CLAUDE.md requires Context7 before MCP SDK and OAuth code). If one changed, rule on it, ledger it, and fix the affected task before dispatching it.

| Fact (checked 2026-10-04 against fastmcp 4.0.10 source and Context7) | Used in |
| --- | --- |
| Pin `fastmcp>=4.0.10,<5` (mcp 2.3.0, py-key-value-aio 0.4.6, cryptography 50.x come with it). Context7 lists only 3.2.x, so 4.x details come from the installed source | Tasks 18–21 |
| `GoogleProvider(client_id=, client_secret=, base_url=, required_scopes=, jwt_signing_key=, client_storage=, allowed_client_redirect_uris=, redirect_path=, fallback_refresh_token_expiry_seconds=)`, all keyword-only; `redirect_path` defaults to `/auth/callback` | Task 18 |
| The proxy's access JWT lifetime mirrors Google's `expires_in` (3600 s). Refresh tokens are issued (`access_type=offline`, `prompt=consent`), rotate on every use, and default to 1 year unless `fallback_refresh_token_expiry_seconds` is set. `aud` = base_url + `/mcp`. PKCE is S256 only. DCR and CIMD are enabled. The consent screen is on by default | Task 18, ADR 0002 |
| In a tool, `fastmcp.server.dependencies.get_access_token()` returns an `AccessToken` whose `claims["sub"]` is the Google sub. `.client_id` is ALSO the Google sub, not the OAuth client | Tasks 18, 20 |
| `mcp.http_app(path="/mcp")` serves `/mcp`, `/authorize`, `/token`, `/register`, `/consent`, the proxy callback (`redirect_path`) and `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/mcp`; it exposes `.lifespan`, which must run | Task 21 |
| FastMCP rejects unknown tool arguments (top level and nested `extra="forbid"`), but strips `title` from input schemas; titles must be restored after registration (the spike's `_restore_titles`). Validation error text includes the input values, so a middleware must rewrite it | Task 19 |
| Sync tool functions run in a worker thread (`run_in_thread=True`) with contextvars propagated, so `get_access_token()` works inside them | Task 20 |
| `from fastmcp.exceptions import ToolError`; `raise ToolError(text)` gives `is_error=True` with that text. `@mcp.prompt(name="start-lesson")`. `FastMCP(name, instructions=...)`. `Middleware.on_call_tool(context, call_next)`; `context.message.name` | Tasks 19, 20 |
| Tests: `async with fastmcp.Client(mcp) as c: await c.call_tool(name, args, raise_on_error=False)` gives `.is_error`, `.structured_content`, `.content[0].text`; `await c.list_tools()` gives `.input_schema` | Task 20 |
| OAuth store: `key_value.aio.stores.filetree.FileTreeStore` (FastMCP's own default backend) wrapped in `key_value.aio.wrappers.encryption.FernetEncryptionWrapper(key_value, fernet=Fernet(key))`. Not `DiskStore`: it needs `diskcache`, which has CVE-2025-69872 with no fixed release, so `pip-audit` would fail | Task 18 |
| The dashboard plan's `create_app(deps, config)` builds its own FastAPI app with `ServerSessionMiddleware` and `SecurityHeadersMiddleware` (strict CSP, `no-store`); its routes are absolute (`/`, `/login`, `/app/...`, `/auth/google`, `/auth/callback`, …) | Tasks 21, 23 |

## Rulings against the spec

These were decided while planning, with evidence. The spec is amended in the same branch (its "Amendments" section, Task 28).

1. **The MCP proxy callback is `/oauth/callback`.** The dashboard's web login already owns `/auth/callback`, and the FastMCP proxy defaults to the same path. `GoogleProvider(redirect_path="/oauth/callback")`. Both URIs must be registered on the Google OAuth client. Cost if wrong: a one-line change plus a Google console edit.
2. **A path dispatcher instead of mounting.** Both apps own root paths. `tutor.app.PathDispatch` sends `/mcp`, `/authorize`, `/token`, `/register`, `/consent`, `/oauth/callback` and `/.well-known/*` to the MCP app and everything else to the web app. Each keeps its own middleware, so the web CSP and `no-store` never touch MCP. Cost if wrong: one small ASGI class.
3. **`mcp_clients_seen` becomes `users.mcp_first_seen_at`.** The token's `client_id` is the Google sub, so the OAuth client is not visible to tools. The audit event is `mcp_first_use`. Conectar's "connected" check already uses `has_any_session`. Cost if wrong: a per-client view would need FastMCP internals.
4. **UI preferences and timezone live on `users`.** The dashboard's `User(id, display_name, role, lang, timezone, reduce_motion, deletion_requested_at)` is read on every request, even before onboarding. So `lang`, `timezone`, `reduce_motion`, `install_prompt_dismissed_at`, `last_celebrated_session_id`, `deletion_requested_at`, `email_weekly` and `email_reminders` are `users` columns. `save_profile` writes `users.timezone` when the web form sends one. Cost if wrong: one migration moving columns.
5. **RLS through `SET LOCAL ROLE tutor_app`.** One `DATABASE_URL` (the owner); every unit of work runs `SET LOCAL ROLE tutor_app` and `set_config('app.user_id', …, true)`. `tutor_app` is a NOLOGIN role created by migration 0001 and granted to the owner. User resolution before `app.user_id` is known uses a second policy branch on `users`: `google_sub = current_setting('app.google_sub', true)`. Cost if wrong: SQL injection could `RESET ROLE`; all SQL is parameterized SQLAlchemy Core, and a separate login role can replace this before the beta.
6. **Leech rule.** "A third appearance within 30 days" is read as `seen_count` reaching 3 while `created_at` is within the last 30 days. Cost if wrong: a stored array of appearance times.
7. **The rating-4 upgrade replays FSRS.** `review_logs` stores the state before each review (`state_before` JSONB). At `end_session`, an upgraded item's state is recomputed from `state_before` with rating 4. Cost if wrong: analytics only.
8. **Refresh tokens: 30 days.** `fallback_refresh_token_expiry_seconds=30*24*3600` to meet requirements section 5 (the library default is 1 year).
9. **Archived glossary items** are never produced in v0 and are treated like `confirmed` by the save rules.
10. **The dashboard reader hides `declined` items.** The dashboard's `GlossaryStatus` has no `declined` value; declined rows never reach `GlossaryRow`.
11. **Postponed dashboard pages.** Task 24 runs dashboard Part 2 Tasks 20–23 and 29 with the v0 route list (no `/pricing`, no `/webhooks/stripe`, no `/billing`, no `/admin`). Task 25 trims the layout: nav, tab bar, "Más" menu, and the Free meter link to `/billing/checkout`.
12. **MCP tools are plain `def` functions.** FastMCP 4 runs sync tools in a worker thread (`run_in_thread=True`, contextvars propagated), which is what spec D7's `anyio.to_thread.run_sync` was for. Cost if wrong: wrapping six functions.
13. **Turn and evidence-string caps are enforced by the tool schema and the 64 KB body limit, not by database `CHECK`s,** because both live inside `raw_evidence` JSONB and `session_errors` text columns. Cost if wrong: one migration adding checks.

## Global Constraints

- Python 3.12; uv; every command runs as `uv run …`; recipes via `uv run just <recipe>`. The dev machine is Windows (PowerShell); recipes keep one command per line.
- Code blocks in this plan are not pre-formatted: run `uv run just fmt` before `uv run just lint` in every task.
- A task is done when `uv run just check` passes (lint, mypy, all non-eval tests, ≥ 90% coverage on `src/tutor/domain/*`, pip-audit).
- `tutor.domain` is pure: no I/O, no clock reads, no environment, no logging. Time and timezones are parameters. mypy strict applies to `tutor.domain.*`.
- Test markers: `@pytest.mark.unit` for no-I/O tests (they run in the Stop hook, `check-fast`, < 30 s total); `@pytest.mark.integration` for tests that need `db-test` (port 5433, `TEST_DATABASE_URL`).
- MCP contract (requirements sections 7 and 12): closed enums, unknown fields rejected, `title` and `description` on every field, tool descriptions ≤ 120 words, every result has `response_rules`, tool names stable. `mcp-contract-reviewer` reviews every diff under `src/tutor/mcp`.
- Every table with user data has `user_id` and `ENABLE` + `FORCE ROW LEVEL SECURITY`; every repository method runs inside `unit_of_work(user_id)`. `security-reviewer` reviews every diff under `src/tutor/auth`, `src/tutor/db`, `src/tutor/app.py`, `src/tutor/web/pg.py`.
- Never log or print tokens, authorization codes, secrets, emails or learner text. Logs carry `user_hash` (first 12 hex characters of SHA-256 of the user id), tool or route, outcome code and latency.
- Learner text is data: it never goes into `response_rules`, `instructions` or error messages, and is never marked safe in templates.
- Length caps (spec section 5): `goal_text` 300, `prep_text` 300, user turn 2,000, `said`/`correct` 300, glossary `text` 120, `meaning` 200, `context_sentence` 300, evidence strings 200 (≤ 5 of them); request body ≤ 64 KB; `raw_evidence` ≤ 20 KB serialized.
- Use Context7 (or the installed package source when Context7 lags) before writing code against FastMCP, the MCP SDK or OAuth.
- Hooks: agents cannot edit `.env*` files (env templates live at `deploy/tutor.env.example`) or existing files under `alembic/versions/` (create a new revision instead), and cannot edit `docs/requirements.md`.
- Never touch `spike/` (the `spike-instructions-v1` tag must keep matching the running spike server).
- Timestamps are timezone-aware UTC in Python and `timestamptz` in Postgres. "Local" always means the learner's `users.timezone` (IANA, default `America/Mexico_City`) through `zoneinfo` with the `tzdata` package installed.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never `--no-verify`.

## Review Focus

These are the inputs most likely to hurt a learner that the spec implies but no task's main tests exercise. Each line has a test in the owning task.

1. **Learner text with typographic characters** (curly apostrophes "I’m", accents, emoji, Spanish words, double spaces) in `said`, `user_turns` and glossary text must match the same text typed plainly. Tests in Tasks 1 and 8.
2. **Duplicate or late `end_session`:** a retry with the same payload, a different payload for an already closed session, and an `end_session` for a session replaced by a newer `start_lesson`. Expected: one set of metrics, no double streak, no plan item marked twice; `already_closed` or `session_closed`. Tests in Tasks 13 and 17.
3. **Local midnight and DST** for the streak, the 10-starts-per-day cap and "due tomorrow 04:00 local": `America/Mexico_City` (no DST since 2022) and `America/New_York` across the March and November changes. Tests in Tasks 6, 9 and 12.
4. **Profile edited mid-plan while a session is open:** the open session's plan item belongs to a superseded plan version. Expected: `end_session` still marks it done, and the new plan does not schedule that track item's base variant again. Tests in Tasks 4 and 13.
5. **Two `start_lesson` calls racing** (Claude retrying on a slow network). Expected: exactly one open session, no HTTP 500; the loser gets the winner's session or closes it as `incomplete` per the normal rule. Tests in Tasks 12 and 17.

---

## File structure

```
src/tutor/
  content/track_it_v0.yaml        # Task 3 (moved from docs/content/track-it-v0.yaml)
  domain/
    text.py                       # Task 1  normalize, count_words, find_turn
    levels.py                     # Task 2  CEFR levels, guided hours
    profile.py                    # Task 2  Profile, validate_profile, onboarding questions
    track.py                      # Task 3  TrackItem, parse_track, track_problems
    plan_lite.py                  # Task 4  build_plan_lite, feasibility
    fsrs/__init__.py              # Task 5  re-exports
    fsrs/scheduler.py             # Task 5  FSRS-4.5
    glossary.py                   # Task 6  plan_glossary_save, spontaneous_use, local_morning
    lesson.py                     # Task 7  choose_item, choose_variant, pick_due_reviews
    validation/__init__.py        # Task 8  re-exports
    validation/evidence.py        # Task 8  Evidence, validate_evidence
    metrics/__init__.py           # Task 9  re-exports
    metrics/session.py            # Task 9  compute_metrics
    metrics/summary.py            # Task 9  summary_text, streak_days
  services/
    __init__.py
    ports.py                      # Task 10 repository Protocols, row types, UnitOfWork
    errors.py                     # Task 10 ServiceError, ErrorCode
    memory.py                     # Task 10 MemoryStore, memory_uow
    context.py                    # Task 10 Services (uow factory, clock, track cache)
    views.py                      # Task 11 result dataclasses for MCP and web
    profile.py                    # Task 11 get_profile, save_profile
    lesson.py                     # Task 12 start_lesson, record_review
    glossary.py                   # Task 13 save_glossary
    session_end.py                # Task 13 end_session
  db/
    __init__.py
    engine.py                     # Task 14 make_engine
    uow.py                        # Task 14 scoped_connection, PgUnitOfWork, pg_uow_factory, PgIdentity
    tables.py                     # Task 14 SQLAlchemy Core tables
    seed.py                       # Task 15 load_track_rows
    repos/__init__.py
    repos/track.py                # Task 15
    repos/people.py               # Task 16 profiles, users, audit
    repos/plans.py                # Task 16
    repos/sessions.py             # Task 17
    repos/glossary.py             # Task 17 glossary + reviews
  settings.py                     # Task 18 Settings.from_env
  auth/
    __init__.py
    mcp_auth.py                   # Task 18 build_google_provider
    identity.py                   # Task 18 current_user_id
  mcp/
    __init__.py
    schemas.py                    # Task 19 Pydantic tool inputs/outputs
    rules.py                      # Task 19 response_rules strings
    instructions.py               # Task 19 INSTRUCTIONS, tool descriptions, prompt text
    errors.py                     # Task 19 error mapping, validation scrubbing middleware
    server.py                     # Task 20 build_mcp
    ratelimit.py                  # Task 20 SlidingWindowLimiter
    observe.py                    # Task 20 CallLogMiddleware
  app.py                          # Task 21 PathDispatch, build_app
  __main__.py                     # Task 21 `python -m tutor`
  web/...                         # dashboard plan Part 1 (Task 22) and Part 2 (Task 24)
  web/pg.py                       # Task 23 Postgres adapters for the dashboard ports
  web/routes/profile.py           # Task 25 Perfil page
  ops/__init__.py
  ops/gate_report.py              # Task 26
alembic.ini                       # Task 14
alembic/env.py                    # Task 14
alembic/versions/0001_core_schema.py      # Task 14
alembic/versions/0002_seed_track_it_v0.py # Task 15
deploy/Dockerfile, deploy/compose.prod.yml, deploy/tutor.env.example, deploy/backup.sh  # Task 27
docs/v0/runbook.md, docs/v0/acceptance.md # Tasks 27, 28
tests/unit/domain/…, tests/unit/services/…, tests/unit/mcp/…, tests/integration/…
```

## Interface contract

Every task must produce exactly these names and types; later tasks consume them as written. A task may add private helpers, but it must not rename or re-type anything here. If implementation proves a signature wrong, the controller rules on it and updates this section and every consuming task before continuing.

### Domain (Tasks 1–9), all frozen dataclasses with `slots=True` unless noted

```python
# tutor.domain.text (Task 1)
def normalize(text: str) -> str
    # NFKC; lowercase; ’ ‘ ʼ ` → '; “ ” → "; strip every character that is not a word
    # character, whitespace, apostrophe or hyphen; then drop apostrophes/hyphens that are not
    # between two word characters; collapse whitespace; strip.
def count_words(text: str) -> int          # matches of r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?"
def find_turn(needle: str, turns: Sequence[str], *, after: int = -1) -> int | None
    # first index i > after whose normalize(turns[i]) contains normalize(needle);
    # None when normalize(needle) == "" or nothing matches.

# tutor.domain.levels (Task 2)
CefrLevel = Literal["B1", "B1+", "B2", "B2+", "C1"]
CEFR_LEVELS: tuple[CefrLevel, ...]                # in that order
LEVEL_VALUE: Mapping[CefrLevel, float]           # 3.0, 3.5, 4.0, 4.5, 5.0
def hours_between(start: CefrLevel, end: CefrLevel) -> int
    # 0 if end <= start; each half-step below B2 costs 90 h, each from B2 up costs 100 h
def level_after_hours(start: CefrLevel, hours: float) -> CefrLevel | None
    # highest level above start whose cumulative cost <= hours; None if not even one step

# tutor.domain.profile (Task 2)
UseCase = Literal["standup", "code_review", "interview", "client_call", "demo", "incident",
                  "one_on_one", "async_writing"]
Domain = Literal["it"]
USE_CASES: tuple[UseCase, ...]; DOMAINS: tuple[Domain, ...]
MINUTES_CHOICES: tuple[int, ...] = (15, 20, 30)
DEFAULT_TIMEZONE = "America/Mexico_City"
class Profile:                      # validated
    self_level: CefrLevel; domains: tuple[Domain, ...]; use_cases: tuple[UseCase, ...]
    minutes_per_day: int; days_per_week: int; target_level: CefrLevel
    target_date: date | None; goal_text: str | None; timezone: str
class ProfileInput:                 # raw, from MCP or the web form (not slots-frozen requirement)
    self_level: str; domains: Sequence[str]; use_cases: Sequence[str]
    minutes_per_day: int; days_per_week: int; target_level: str
    target_date: date | None = None; goal_text: str | None = None; timezone: str | None = None
ProfileErrorCode = Literal["required", "invalid_choice", "too_few", "too_many", "out_of_range",
                           "below_current_level", "too_long", "invalid_timezone"]
class FieldError: field: str; code: ProfileErrorCode
def validate_profile(raw: ProfileInput, today: date, *, valid_timezones: frozenset[str],
                     current_timezone: str = DEFAULT_TIMEZONE) -> Profile | tuple[FieldError, ...]
    # use_cases 1–4 unique; days 2–7; minutes in MINUTES_CHOICES; target >= self;
    # target_date 28–364 days after today; goal_text stripped, empty→None, ≤ 300;
    # timezone None → current_timezone; otherwise must be in valid_timezones.
def plan_inputs_changed(old: Profile | None, new: Profile) -> bool
    # True if old is None or any of self_level, domains, use_cases, minutes_per_day,
    # days_per_week, target_level, target_date differ (goal_text and timezone do not count)
class OnboardingOption: value: str; label_en: str; label_es: str; description_en: str; description_es: str
class OnboardingQuestion: id: str; fields: tuple[str, ...]; prompt_en: str; prompt_es: str
    options: tuple[OnboardingOption, ...]; multi: bool; min_choices: int; max_choices: int
ONBOARDING_QUESTIONS: tuple[OnboardingQuestion, ...]   # ids: level, field, use_cases, time, goal

# tutor.domain.track (Task 3)
InteractionType = Literal["explain", "negotiate", "disagree", "ask_for_help", "give_feedback", "small_talk"]
Skill = Literal["speaking", "writing"]
TrackLevel = Literal["B1", "B2"]
class TrackChunk: id: str; position: int; text: str; example: str
class TrackItem: id: str; domain: Domain; order_no: int; cefr: TrackLevel; skill: Skill
    interaction_type: InteractionType; use_cases: tuple[UseCase, ...]; can_do_en: str
    can_do_es: str; character: str; objective: str; obstacle: str; scenario_hint: str
    chunks: tuple[TrackChunk, ...]
class TrackError(ValueError): problems: tuple[str, ...]
def parse_track(data: Mapping[str, Any]) -> tuple[TrackItem, ...]   # raises TrackError
def track_problems(items: Sequence[TrackItem]) -> tuple[str, ...]  # coverage rules, spec 7.1

# tutor.content (Task 3; package data + loader, not domain: it reads a file)
def load_track() -> tuple[TrackItem, ...]
    # functools.cache; importlib.resources.files("tutor.content") / "track_it_v0.yaml";
    # yaml.safe_load then parse_track; raises TrackError if track_problems() is non-empty

# tutor.domain.plan_lite (Task 4)
Variant = Literal["base", "complication"]
class PlannedItem: week_no: int; order_no: int; track_item_id: str; variant: Variant
FeasibilityMessage = Literal["reachable", "milestone", "confidence_only"]
class Feasibility: weeks: int; sessions_planned: int; hours_available: float; hours_needed: int
    reachable: bool; milestone_level: CefrLevel | None; message: FeasibilityMessage
class PlanLite: items: tuple[PlannedItem, ...]; feasibility: Feasibility
DEFAULT_WEEKS = 12
def horizon_weeks(target_date: date | None, today: date) -> int     # ceil(days/7) clamped 4..52
def order_track(profile: Profile, track: Sequence[TrackItem]) -> tuple[TrackItem, ...]
def feasibility(profile: Profile, weeks: int) -> Feasibility
def build_plan_lite(profile: Profile, track: Sequence[TrackItem], today: date,
                    done_base_ids: frozenset[str] = frozenset()) -> PlanLite
def rationale(f: Feasibility, profile: Profile) -> dict[str, Any]      # JSON-safe, for plans.rationale
def feasibility_text(f: Feasibility, lang: Literal["en", "es"]) -> str  # template sentence

# tutor.domain.fsrs (Task 5; exact formulas and weights from the research notes in Task 5)
Rating = Literal[1, 2, 3, 4]
TARGET_RETENTION = 0.85
DEFAULT_WEIGHTS: tuple[float, ...]          # 17 FSRS-4.5 weights
class FsrsState: stability: float | None; difficulty: float | None; reps: int; lapses: int
    last_review: datetime | None; due: datetime
def new_state(first_due: datetime) -> FsrsState                 # never reviewed
def review(state: FsrsState, rating: Rating, now: datetime, *,
           weights: Sequence[float] = DEFAULT_WEIGHTS,
           retention: float = TARGET_RETENTION) -> FsrsState
def state_to_json(state: FsrsState) -> dict[str, Any]
def state_from_json(data: Mapping[str, Any]) -> FsrsState

# tutor.domain.glossary (Task 6)
GlossaryKind = Literal["correction", "chunk", "term"]
GlossaryStatus = Literal["provisional", "confirmed", "declined", "archived"]
SaveStatus = Literal["confirmed", "provisional", "declined"]
RejectReason = Literal["empty", "too_long", "missing_context", "duplicate_in_call", "already_confirmed"]
PROVISIONAL_DAYS = 7; DECLINED_RETENTION_DAYS = 30; LEECH_WINDOW_DAYS = 30; LEECH_SEEN_COUNT = 3
class IncomingItem: kind: GlossaryKind; text: str; meaning: str; context_sentence: str; domain: Domain
class ExistingItem: id: UUID; kind: GlossaryKind; status: GlossaryStatus; seen_count: int
    leech: bool; created_at: datetime
class InsertItem: index: int; item: IncomingItem; text_norm: str; status: SaveStatus
    provisional_expires_at: datetime | None; first_due: datetime | None   # first_due only when confirmed
class Reinforce: index: int; item_id: UUID; kind: GlossaryKind; seen_count: int; leech: bool; due: datetime
class Promote: index: int; item_id: UUID; first_due: datetime        # provisional/declined → confirmed
class SetStatus: index: int; item_id: UUID; status: Literal["provisional", "declined"]
    provisional_expires_at: datetime | None
class Reject: index: int; reason: RejectReason
GlossaryAction = InsertItem | Reinforce | Promote | SetStatus | Reject
def local_morning(now: datetime, tz: ZoneInfo, *, days_ahead: int = 1, hour: int = 4) -> datetime
    # UTC datetime of `hour`:00 local on (local date of now + days_ahead)
def plan_glossary_save(items: Sequence[IncomingItem], status: SaveStatus,
                       existing: Mapping[str, ExistingItem], now: datetime,
                       tz: ZoneInfo) -> tuple[GlossaryAction, ...]   # one action per input index
def spontaneous_use(texts: Mapping[UUID, str], turns: Sequence[str]) -> frozenset[UUID]
    # ids whose normalized text appears in >= 2 distinct normalized turns

# tutor.domain.lesson (Task 7)
BriefVariant = Literal["base", "complication", "simpler"]
ReviewFormat = Literal["produce", "recall", "correct", "use"]
TaskResult = Literal["achieved", "partial", "not_achieved"]
class PendingPlanItem: plan_item_id: UUID; week_no: int; order_no: int; track_item_id: str; variant: Variant
class ItemChoice: plan_item_id: UUID | None; track_item_id: str; variant: Variant; plan_exhausted: bool
class RecentResult: task_result: TaskResult; hints_given: int          # newest first
class DueCandidate: item_id: UUID; kind: GlossaryKind; leech: bool; due: datetime
    last_ratings: tuple[int, ...]                                    # newest last, at most 2
class DueReview: item_id: UUID; format: ReviewFormat
MAX_DUE_REVIEWS = 8; MAX_DRILLED_REVIEWS = 4; STARTS_PER_DAY = 10
def choose_item(pending: Sequence[PendingPlanItem], track: Mapping[str, TrackItem],
                prep_use_case: UseCase | None, last_done: Mapping[str, datetime],
                last_plan_item: PendingPlanItem | None) -> ItemChoice
    # raises ValueError if pending is empty and last_plan_item is None
def choose_variant(plan_variant: Variant, recent: Sequence[RecentResult]) -> BriefVariant
def review_format(kind: GlossaryKind, leech: bool, last_ratings: Sequence[int]) -> ReviewFormat
def pick_due_reviews(candidates: Sequence[DueCandidate], now: datetime,
                     limit: int = MAX_DUE_REVIEWS) -> tuple[DueReview, ...]

# tutor.domain.validation (Task 8)
Category = Literal["grammar", "lexis", "word_order", "register", "other"]
Confidence = Literal["low", "medium", "high"]
SessionOutcome = Literal["closed", "incomplete"]
MIN_USER_WORDS = 30; LOW_TRUST_SHARE = 0.5
class ReportedError: said: str; correct: str; category: Category
class Evidence: user_turns: tuple[str, ...]; errors: tuple[ReportedError, ...]
    chunks_used: tuple[str, ...]; task_result: TaskResult; hints_given: int
    cefr_level: CefrLevel; cefr_confidence: Confidence; cefr_evidence: tuple[str, ...]
    confidence_1_5: int; assistant_words_estimate: int | None
class ValidError: said: str; correct: str; correct_norm: str; category: Category; turn_index: int
class ValidatedEvidence: status: SessionOutcome; low_trust: bool; user_words: int; turns: int
    errors: tuple[ValidError, ...]; errors_reported: int; errors_rejected: int
    chunks_used: tuple[str, ...]; chunks_rejected: int; cefr_excluded: bool
def validate_evidence(ev: Evidence, chunks_offered: Sequence[str], *,
                      previous_cefr: CefrLevel | None, self_level: CefrLevel) -> ValidatedEvidence

# tutor.domain.metrics (Task 9)
MAX_DURATION_MIN = 45.0
class SessionMetrics: user_words: int; assistant_words_estimate: int | None
    user_ratio: float | None; turns: int; words_per_turn: float; duration_min: float
    user_words_per_min: float; errors_total: int; errors_rejected: int
    errors_by_category: Mapping[str, int]; errors_per_100w: float; recurring_errors: int
    uptake_count: int; chunks_offered: int; chunks_used: int; chunks_rejected: int
    activation_rate: float
def uptake_count(errors: Sequence[ValidError], turns: Sequence[str]) -> int
def compute_metrics(ev: Evidence, v: ValidatedEvidence, *, chunks_offered: int,
                    started_at: datetime, ended_at: datetime,
                    recent_correct_norms: frozenset[str]) -> SessionMetrics
def metrics_to_json(m: SessionMetrics) -> dict[str, Any]
def streak_days(closed_ended_at: Sequence[datetime], now: datetime, tz: ZoneInfo) -> int
def summary_text(m: SessionMetrics, streak: int) -> str            # 4 lines joined by "\n"
```

### Services (Tasks 10–13)

```python
# tutor.services.errors (Task 10)
ErrorCode = Literal["onboarding_needed", "session_not_found", "session_closed", "rate_limited",
                    "validation_failed", "payload_too_large"]
class ServiceError(Exception):
    def __init__(self, code: ErrorCode, fields: tuple[str, ...] = ()) -> None  # .code, .fields

# tutor.services.ports (Task 10). Row types are frozen slotted dataclasses.
PlanItemStatus = Literal["pending", "done", "skipped"]
SessionStatus = Literal["open", "closed", "incomplete"]
Mode = Literal["voice", "text"]
ClientName = Literal["claude", "chatgpt", "code", "unknown"]
AuditEvent = Literal["user_created", "web_login", "mcp_first_use", "profile_saved",
                     "plan_generated", "session_closed", "glossary_saved"]
    # glossary_saved (Task 13): meta {"session_id": str, "status": SaveStatus, "items": [{"id": str,
    # "action": insert|reinforce|promote|set_status, "status": resulting GlossaryStatus}]};
    # ids and enums only, Reject actions omitted; inserted ids are read back with by_norms
    # (no port change). Task 26 replays these rows for the confirmation rate.
class PlanItemRow: id: UUID; week_no: int; order_no: int; track_item_id: str; variant: Variant
    status: PlanItemStatus; done_session_id: UUID | None
class ActivePlan: id: UUID; version: int; generated_at: datetime; rationale: Mapping[str, Any]
    items: tuple[PlanItemRow, ...]
class SessionRow: id: UUID; plan_item_id: UUID | None; track_item_id: str; prep_text: str | None
    mode: Mode; client: ClientName; started_at: datetime; ended_at: datetime | None
    status: SessionStatus; low_trust: bool; brief_variant: BriefVariant
    chunks_offered: tuple[str, ...]; result: Mapping[str, Any] | None
class GlossaryRowData: id: UUID; kind: GlossaryKind; text: str; text_norm: str; meaning: str
    context_sentence: str; domain: Domain; status: GlossaryStatus; seen_count: int; leech: bool
    created_at: datetime; provisional_expires_at: datetime | None
class ReviewLogRow: item_id: UUID; rating: int; reviewed_at: datetime; state_before: FsrsState

class UserRepo(Protocol):
    def timezone(self) -> str
    def set_timezone(self, tz: str) -> None
    def note_mcp_use(self, now: datetime) -> bool          # True only the first time
class ProfileRepo(Protocol):
    def get(self) -> Profile | None
    def upsert(self, profile: Profile, now: datetime) -> None   # also writes users.timezone
class TrackRepo(Protocol):
    def items(self, domain: Domain) -> tuple[TrackItem, ...]    # ordered by order_no
class PlanRepo(Protocol):
    def active(self) -> ActivePlan | None
    def create(self, items: Sequence[PlannedItem], rationale: Mapping[str, Any],
               now: datetime) -> ActivePlan               # version = max + 1; others superseded
    def mark_done(self, plan_item_id: UUID, session_id: UUID) -> bool  # any version; False if already done
    def done_base_track_ids(self) -> frozenset[str]
class SessionRepo(Protocol):
    def get(self, session_id: UUID) -> SessionRow | None
    def open_session(self) -> SessionRow | None
    def create(self, *, plan_item_id: UUID | None, track_item_id: str, prep_text: str | None,
               mode: Mode, client: ClientName, brief_variant: BriefVariant,
               chunks_offered: Sequence[str], now: datetime) -> SessionRow
               # raises OpenSessionExists if another open session exists (unique index)
    def mark_incomplete(self, session_id: UUID, now: datetime) -> None
    def close(self, session_id: UUID, *, status: SessionOutcome, low_trust: bool,
              ended_at: datetime, evidence: Evidence, raw_evidence: Mapping[str, Any],
              cefr_excluded: bool, result: Mapping[str, Any]) -> None
    def count_started_since(self, since: datetime) -> int
    def recent_results(self, limit: int) -> tuple[RecentResult, ...]     # closed, newest first
    def closed_ended_at(self, since: datetime) -> tuple[datetime, ...]   # status closed
    def previous_cefr(self) -> CefrLevel | None    # newest closed, cefr not excluded
    def last_done_by_track(self) -> Mapping[str, datetime]               # closed sessions
    def save_metrics(self, session_id: UUID, metrics: SessionMetrics) -> None
    def save_errors(self, session_id: UUID, errors: Sequence[ValidError]) -> None
    def recent_correct_norms(self, since: datetime, exclude: UUID) -> frozenset[str]
class OpenSessionExists(Exception): ...
class GlossaryRepo(Protocol):
    def by_norms(self, norms: Collection[str]) -> Mapping[str, GlossaryRowData]
    def get_many(self, ids: Collection[UUID]) -> Mapping[UUID, GlossaryRowData]
    def apply(self, actions: Sequence[GlossaryAction], items: Sequence[IncomingItem], *,
              session_id: UUID, now: datetime) -> None     # Reject actions are ignored
    def due_candidates(self, now: datetime) -> tuple[DueCandidate, ...]   # confirmed, due <= now
    def provisional(self, limit: int) -> tuple[GlossaryRowData, ...]      # oldest first
    def count_provisional(self) -> int
    def count_due(self, now: datetime) -> int
    def purge(self, now: datetime) -> int     # expired provisional + declined older than 30 days
class ReviewRepo(Protocol):
    def state(self, item_id: UUID) -> FsrsState | None
    def save_state(self, item_id: UUID, state: FsrsState) -> None
    def log(self, session_id: UUID, item_id: UUID, rating: int, now: datetime,
            state_before: FsrsState) -> bool       # False if (session, item) already logged
    def session_logs(self, session_id: UUID) -> tuple[ReviewLogRow, ...]
    def set_log_rating(self, session_id: UUID, item_id: UUID, rating: int) -> None
class AuditRepo(Protocol):
    def record(self, event: AuditEvent, meta: Mapping[str, Any], now: datetime) -> None
class UnitOfWork(Protocol):
    user_id: UUID
    users: UserRepo; profiles: ProfileRepo; track: TrackRepo; plans: PlanRepo
    sessions: SessionRepo; glossary: GlossaryRepo; reviews: ReviewRepo; audit: AuditRepo
UowFactory = Callable[[UUID], AbstractContextManager[UnitOfWork]]   # commits on clean exit
class ResolvedUser: id: UUID; created: bool
class IdentityResolver(Protocol):
    def resolve(self, google_sub: str, email: str | None, display_name: str | None,
                now: datetime) -> ResolvedUser       # find-or-create by sub, never by email

# tutor.services.memory (Task 10)
class MemoryStore: ...            # all tables as dicts; one instance per test
def memory_uow(store: MemoryStore) -> UowFactory  # snapshot/rollback on exception
class MemoryIdentity(IdentityResolver): def __init__(self, store: MemoryStore) -> None

# tutor.services.context (Task 10)
@dataclass(frozen=True)
class Services:
    uow: UowFactory; clock: Callable[[], datetime]; valid_timezones: frozenset[str]

# tutor.services.views (Task 11; Tasks 12–13 append their result types here)
class PlanItemView: plan_item_id: UUID; week_no: int; order_no: int; track_item_id: str
    can_do_en: str; can_do_es: str; skill: Skill; interaction_type: InteractionType
    variant: Variant; status: PlanItemStatus
class PlanSummary: version: int; current_week_no: int; week_items: tuple[PlanItemView, ...]
    next_item: PlanItemView | None; feasibility: Feasibility; weeks: int; sessions_planned: int
    sessions_done: int
class ProfileView: onboarding_needed: bool; profile: Profile | None; plan: PlanSummary | None
    streak: int; open_session_id: UUID | None; provisional_count: int; due_reviews_count: int
class SaveProfileResult: profile: Profile; plan: PlanSummary; plan_changed: bool
class StartLessonRequest: mode: Mode; prep: str | None = None; prep_use_case: UseCase | None = None
    minutes: int | None = None; domain: Domain = "it"; client: ClientName = "claude"
class DueReviewView: item_id: UUID; kind: GlossaryKind; text: str; meaning: str
    context_sentence: str; format: ReviewFormat
class ProvisionalView: item_id: UUID; kind: GlossaryKind; text: str; meaning: str
class LessonStart: session_id: UUID; mode: Mode; item: TrackItem; variant: BriefVariant
    prep_text: str | None; due_reviews: tuple[DueReviewView, ...]
    provisional_items: tuple[ProvisionalView, ...]; plan_exhausted: bool; replaced_session: bool
class ReviewResultView: item_id: UUID; next_due: date; outcome: Literal["recorded", "already_recorded"]
class RejectedView: index: int; reason: RejectReason
class GlossarySaveResult: new: int; reinforced: int; promoted: int; rejected: tuple[RejectedView, ...]
class EndSessionResult: status: SessionOutcome; low_trust: bool; metrics: SessionMetrics
    summary_text: str; streak: int; already_closed: bool; errors_rejected: int; chunks_rejected: int
    def to_json(self) -> dict[str, Any]       # stored in sessions.result for idempotent repeats
    @classmethod
    def from_json(cls, data: Mapping[str, Any], *, already_closed: bool) -> EndSessionResult

# Use cases (Tasks 11–13). Every one opens exactly one unit of work.
def get_profile(svc: Services, user_id: UUID) -> ProfileView                       # Task 11
def save_profile(svc: Services, user_id: UUID, raw: ProfileInput) -> SaveProfileResult  # Task 11
    # raises ServiceError("validation_failed", fields=…)
def plan_summary(uow: UnitOfWork, plan: ActivePlan, profile: Profile) -> PlanSummary  # Task 11
def start_lesson(svc: Services, user_id: UUID, req: StartLessonRequest) -> LessonStart  # Task 12
def record_review(svc: Services, user_id: UUID, session_id: UUID,
                  results: Sequence[tuple[UUID, Rating]]) -> tuple[ReviewResultView, ...]  # Task 12
def save_glossary(svc: Services, user_id: UUID, session_id: UUID, status: SaveStatus,
                  items: Sequence[IncomingItem]) -> GlossarySaveResult           # Task 13
def end_session(svc: Services, user_id: UUID, session_id: UUID, evidence: Evidence,
                raw_evidence: Mapping[str, Any]) -> EndSessionResult            # Task 13
```

### Repository contract tests (Task 10 writes them; Tasks 16–17 run them on Postgres)

`tests/repo_contract.py` defines `class RepoContract:` whose `test_*` methods exercise every
repository method in `tutor.services.ports` through a `UnitOfWork`, including per-user isolation.
It is not collected directly (file name does not start with `test_`). It uses only these fixtures,
which each subclass's module provides:

- `uow_factory: UowFactory`
- `identity: IdentityResolver`
- `user_id: UUID` and `other_user_id: UUID`: two users already resolved through `identity`
- `now: datetime`: a fixed aware UTC datetime, 2026-10-14 15:00 UTC (a Wednesday)

The track is available through `uow.track.items("it")` (memory: loaded from `tutor.content.load_track()`;
Postgres: seeded by migration 0002). Subclasses:
- `tests/unit/services/test_memory_contract.py`: `class TestMemoryRepos(RepoContract)`, marked unit (Task 10)
- `tests/integration/test_pg_contract.py`: `class TestPgRepos(RepoContract)`, marked integration;
  Task 16 creates it and runs the people/plans subset, Task 17 removes the skip list so all run.
  Task 17 also adds Postgres-only tests: RLS isolation by raw SQL, the one-open-session unique index
  under two concurrent connections, migrations down/up.

### Test layout (settled in review; every task follows it)

- `pyproject.toml` (Task 10): `[tool.mypy] mypy_path = "src,tests"` and `[tool.pytest.ini_options]
  pythonpath = ["tests"]`. No later task adds or changes either line.
- A test directory that needs a `conftest.py` is a package (empty `__init__.py` + `conftest.py`,
  tests import it as `from .conftest import …`): `tests/unit/services/` (Task 10, module names
  `services.*`), `tests/integration/` (Task 14, `integration.*`), and from the dashboard plan
  `tests/unit/web/` (Task 22, same pattern). `tests/e2e/` belongs to dashboard Task 30, which
  v0 does not run. Every other test directory
  (`tests/unit/domain`, `db`, `auth`, `mcp`, `app`, …) has no `__init__.py` and no `conftest.py`,
  so test file basenames must be unique across `tests/` and `evals/`. Never add `__init__.py` to
  `tests/unit/mcp` (with `tests/unit` on `sys.path` it would shadow the `mcp` SDK), and never add
  a `tests/conftest.py` or a package-less `conftest.py` (mypy "Duplicate module named conftest").
- Shared non-collected helpers live at `tests/` top level with unique names: `repo_contract.py`
  (Task 10), `mcp_lesson.py` (Task 20) and `web_pg_support.py` (Task 23); import them as
  `from repo_contract import …`. `evals/` (no `__init__.py`) is a second pytest test path from
  Task 26; its package `evals/fidelity/` is imported as `fidelity`.

### Platform (Tasks 14–27)

```python
# tutor.db.engine (Task 14)
def make_engine(database_url: str) -> sqlalchemy.Engine        # psycopg 3, pool_pre_ping
# tutor.db.uow (Task 14; repos attached in Tasks 15–17)
def scoped_connection(engine: Engine, *, user_id: UUID | None = None, google_sub: str | None = None,
                      web_session: str | None = None) -> ContextManager[Connection]
    # Task 14 CONTRACT NOTE: one transaction as tutor_app with app.user_id, app.google_sub and
    # app.web_session set (unset = '' = matches nothing); Task 23's PgWebBackend uses it for
    # every query (web_sessions policy: token_hash = app.web_session OR user_id = app.user_id)
def pg_uow_factory(engine: Engine) -> UowFactory
    # BEGIN; SET LOCAL ROLE tutor_app; set_config('app.user_id', str(uid), true); commit/rollback
class PgIdentity(IdentityResolver): def __init__(self, engine: Engine) -> None
    # BEGIN; SET LOCAL ROLE tutor_app; set_config('app.google_sub', sub, true); select/insert users
# tutor.db.seed (Task 15)
def track_rows(items: Sequence[TrackItem]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]
    # (track_items rows, track_chunks rows) for the seed migration

# tutor.settings (Task 18)
@dataclass(frozen=True)
class Settings:
    env: Literal["dev", "test", "prod"]; base_url: str; database_url: str
    google_client_id: str; google_client_secret: str; jwt_signing_key: str
    oauth_storage_key: str; oauth_storage_dir: Path; web_session_secret: str
    port: int = 8000
    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings   # SystemExit naming missing keys
# Env keys: TUTOR_ENV, TUTOR_BASE_URL, DATABASE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
# TUTOR_JWT_SIGNING_KEY (>= 32 chars), TUTOR_OAUTH_STORAGE_KEY (Fernet key),
# TUTOR_OAUTH_STORAGE_DIR, TUTOR_WEB_SESSION_SECRET, TUTOR_PORT, plus the dashboard's
# TUTOR_MCP_URL and TUTOR_SUPPORT_EMAIL (WebConfig.from_env).

# tutor.auth.mcp_auth (Task 18)
MCP_CALLBACK_PATH = "/oauth/callback"
CLAUDE_REDIRECT_URIS = ("https://claude.ai/api/mcp/auth_callback",)
REFRESH_TOKEN_SECONDS = 30 * 24 * 3600
def build_google_provider(settings: Settings, *, client_storage: AsyncKeyValue | None = None) -> GoogleProvider
# tutor.auth.identity (Task 18)
def token_identity() -> tuple[str, str | None, str | None] | None
    # (sub, email, name) from fastmcp get_access_token(), imported lazily inside the function
def current_user_id(identity: IdentityResolver, svc: Services) -> UUID
    # resolves the token's sub; on first creation audits user_created; on first MCP use audits
    # mcp_first_use; raises ServiceError("session_not_found") never: raises PermissionError if no token

# tutor.mcp.server (Task 20)
def build_mcp(svc: Services, identity: IdentityResolver, *, auth: AuthProvider | None,
              limiter: SlidingWindowLimiter | None = None,
              user_resolver: Callable[[], UUID] | None = None) -> FastMCP
    # user_resolver overrides token resolution in tests
# tutor.mcp.ratelimit (Task 20)
class SlidingWindowLimiter:
    def __init__(self, limit: int = 60, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None
    def allow(self, key: str) -> bool

# tutor.app (Task 21)
MCP_PATHS: frozenset[str]          # "/mcp", "/authorize", "/token", "/register", "/consent", "/oauth/callback"
class PathDispatch:                # ASGI; lifespan delegated to the MCP app
    def __init__(self, mcp_app: ASGIApp, web_app: ASGIApp | None) -> None
def build_app(settings: Settings, *, engine: Engine | None = None,
              web_config: WebConfig | None = None) -> PathDispatch
    # Task 21 builds MCP only (web_app None → 404 for non-MCP paths); Task 23 adds the web app
    # and the `web_config` keyword (default WebConfig.from_env(os.environ)); both branches are
    # RequestLog(BodySizeGuard(...)). Task 23 also adds RequestLog(app, clock) and log_path(path).

# tutor.web.pg (Task 23)
class PgWebBackend:                # implements UserDirectory, WebSessionStore, DashboardReader,
                                   # GlossaryEditor, AccountService; SubscriptionStore=V0Subscriptions
    def __init__(self, engine: Engine, clock: Callable[[], datetime]) -> None
def pg_web_deps(engine: Engine, settings: Settings, config: WebConfig) -> WebDeps
# also FREE: Subscription, V0Subscriptions, DisabledBilling, BillingDisabled, V0Settings;
# tutor.web.ports.UNCAPPED = 2**31 - 1 (Task 23); tutor.web.profile (Task 25): PROFILE_PATH,
# ProfilePort, ServicesProfiles(svc), MemoryProfiles(clock, store=None), install_profiles,
# get_profiles, optional_profiles

# tutor.ops.gate_report (Task 26)
def main(argv: Sequence[str] | None = None) -> int
```

## Task index

| # | Task | File | Depends on |
| --- | --- | --- | --- |
| 1 | Text normalization and word counts | 1 | — |
| 2 | Levels, profile validation and onboarding questions | 1 | 1 |
| 3 | Starter track model, parser and coverage checks | 1 | 2 |
| 4 | Plan-lite: ordering, schedule and feasibility | 1 | 2, 3 |
| 5 | FSRS-4.5 scheduler | 1 | — |
| 6 | Glossary rules | 1 | 1, 5 |
| 7 | Lesson composer | 1 | 3, 4, 6 |
| 8 | Evidence validation | 1 | 1, 2, 7 |
| 9 | Session metrics, summary and streak | 1 | 1, 8 |
| 10 | Service ports, errors and the in-memory unit of work | 1 | 1–9 |
| 11 | Profile services: `get_profile`, `save_profile` | 1 | 10 |
| 12 | Lesson services: `start_lesson`, `record_review` | 1 | 11 |
| 13 | Glossary and `end_session` services | 1 | 12 |
| 14 | Database foundation: schema, RLS, unit of work | 2 | 10 |
| 15 | Track seed migration and track repository | 2 | 3, 14 |
| 16 | Postgres repositories: users, profiles, plans, audit | 2 | 15 |
| 17 | Postgres repositories: sessions, glossary, reviews | 2 | 16 |
| 18 | Settings and MCP auth | 2 | 14 |
| 19 | MCP contract: schemas, rules, instructions, errors | 2 | 13 |
| 20 | MCP server: tools, prompt, rate limit, call log | 2 | 18, 19 |
| 21 | Product app, dispatcher and end-to-end lesson | 2 | 17, 20 |
| 22 | Dashboard foundation (runs dashboard plan Part 1) | 3 | 21 |
| 23 | Web adapters on Postgres and app wiring | 3 | 22 |
| 24 | Dashboard pages (runs dashboard plan Part 2 Tasks 20–23, 29) | 3 | 23 |
| 25 | Perfil page and v0 navigation | 3 | 24 |
| 26 | Gate report and evals tooling | 3 | 17 |
| 27 | Deployment: Docker, backups, runbook | 3 | 21–25 |
| 28 | Acceptance: manual checks, spec amendments, ADR 0002 | 3 | 27 |

---

Numbering: inside the dashboard plan files, "Task N" means that plan's task. This part writes this plan's tasks as "core Task N" whenever both could be meant.

### Task 22: Dashboard foundation (runs dashboard plan Part 1)

The controller runs `superpowers:subagent-driven-development` on `docs/superpowers/plans/2026-10-04-dashboard-web-1-foundation.md`, Tasks 1–15, on this branch (`feat/core-loop-v0`). That plan is executed as written except for the rulings below, which the controller pastes into every dashboard-task dispatch. Spec section 12 requires all fifteen tasks (later tasks consume earlier types), so none is skipped.

**Files:** exactly the files of dashboard Part 1 Tasks 1–15 (`src/tutor/domain/dashboard/*`, `src/tutor/web/**`, `scripts/make_icons.py`, `tests/unit/domain/test_dashboard_*.py`, `tests/unit/web/**`), plus:
- Modify: `pyproject.toml`, `uv.lock` (dashboard Task 7 Step 1, as amended by V4)
- Modify: `justfile` (dashboard Task 12: `dashboard-demo`)

**Interfaces:**
- Consumes: the core packages from Tasks 1–21 (nothing in Part 1 imports them; Part 1 only adds `tutor.domain.dashboard` and `tutor.web`).
- Produces (used by core Tasks 23–25): `tutor.domain.dashboard.types` (all view types), `banners_for`, `local_today`, `week_start`, `build_week_trail`, `should_celebrate`, `filter_glossary`, `glossary_csv`; `tutor.web.ports` (`WebSession`, `GoogleIdentity`, `LoginFailed`, `Clock`, the nine Protocols, `WebDeps`); `tutor.web.config.WebConfig` with `from_env`; `tutor.web.memory` (`FixedClock`, `MemoryBackend`, `FakeBilling`, `FakeGoogle`, `memory_deps`, `empty_home`); `tutor.web.app.create_app(deps, config) -> FastAPI`; `tutor.web.views` (`render`, `is_htmx`, `request_lang`, `today_for`); `tutor.web.deps` (`current_user`, `APP_ROUTER_DEPS`, `get_deps`, `get_config`); `tutor.web.sessions` (`COOKIE`, `hash_token`); `tutor.web.google.GoogleOidcLogin`; fixtures in `tests/unit/web/conftest.py` (`NOW`, `TODAY`, `BASE`, `backend`, `demo`, `clock`, `billing`, `google`, `config`, `app`, `client`, `login`, `csrf_of`).

- [ ] **Step 1: Run the v0 checklist (replaces dashboard Part 1 "Before Task 1 (on 2027-01-05)")**

1. Core Task 21 is committed on `feat/core-loop-v0` and `uv run just check` passes there. Do not create a branch; dashboard commits go on this branch.
2. Ignore the dashboard plan's "Status: parked" line and its 2027 dates: spec D2 and section 12 put Part 1 in v0.
3. Re-verify with Context7 (CLAUDE.md requires it for OAuth) the rows Part 1 uses: HTMX 2.0.11 and `htmx-config` keys, `HX-Redirect`, the Authlib Starlette client (`/authlib/authlib`: `authorize_access_token` returns claims under `token["userinfo"]`), `Clear-Site-Data`. Skip the Stripe, OXXO, Motion and axe rows (Part 2 payments and Task 30 are not in v0). Ledger any change and fix the affected dashboard task before dispatching it.
4. Record in the SDD ledger: "core Task 22 = dashboard Part 1 Tasks 1–15; rulings V1–V18".

- [ ] **Step 2: Apply these rulings to dashboard Part 1 (paste them into every dispatch)**

- **V1. Numbering.** "Task N" in the dashboard plan means that plan's task; "core Task N" means this plan's.
- **V2. Rulings of the dashboard plan.** Its rulings 1 (no Motion library) and 3 (es-MX decimal point) apply. Rulings 2 (card-only payments) and 4 (device check as Task 19) belong to Part 2 payments and do not apply in v0.
- **V3. Gate per task.** Every dashboard task ends with `uv run just fmt` then `uv run just check` (lint, mypy, all non-eval tests including integration, domain coverage, pip-audit), even where the dashboard step says `check-fast` or `lint`. Every dashboard commit adds `-m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"`.
- **V4. Dependencies (dashboard Task 7 Step 1).** Run exactly:

  ```bash
  uv add "fastapi>=0.142.2" "jinja2>=3.1" "babel>=2.18" "python-multipart>=0.0.32" "authlib>=1.8.0" "httpx>=0.28.1" "stripe>=16.0.0"
  uv remove --dev httpx
  ```

  `uvicorn` is skipped (core Task 21 added `uvicorn>=0.35`). `httpx` moves to the runtime group because Authlib's Starlette client imports it at runtime; the dev entry from core Task 21 is removed so it is declared once. The Playwright line (`playwright`, `pytest-playwright`, `axe-playwright-python`) is skipped: dashboard Task 30 is not in v0, and `pytest-playwright` would register a pytest plugin nobody uses. Never lower an existing floor (`pydantic`, `cryptography`, `uvicorn` stay as core Tasks 18–21 set them). If `uv add` cannot resolve against `fastmcp>=4.0.10,<5`'s pins, stop and ledger the conflict for a ruling. Append the Authlib `[[tool.mypy.overrides]]` block after the **last** existing overrides block.
- **V5. pyproject lines owned by core Task 10.** Do not touch `mypy_path`, `pythonpath`, `testpaths` or the marker list. Part 1 adds no marker.
- **V6. Test layout.** `tests/unit/web/__init__.py` (empty) plus `tests/unit/web/conftest.py`, exactly as dashboard Task 10 writes them, match the contract's test layout (package `web`, imported as `from .conftest import …`). Never create `tests/__init__.py`, `tests/unit/__init__.py`, `tests/conftest.py` or a package-less `conftest.py`. Dashboard domain tests stay in `tests/unit/domain/` (no `__init__.py`) as `test_dashboard_*.py`. Before each commit, check that module basenames stay unique (packages' `__init__.py` and `conftest.py` are exempt): `git ls-files tests evals | grep '\.py$' | grep -v -e '/__init__\.py$' -e '/conftest\.py$' | xargs -n1 basename | sort | uniq -d` prints nothing (run in Git Bash). Web tests stay unmarked: they match `-m "not integration and not eval"`, so `test`, `check-fast` and `check` run them.
- **V7. POSIX shell blocks.** The shell blocks in dashboard Tasks 10 (`touch`, `mkdir -p`, `printf`), 13 (font `curl` downloads, `LATIN=…`, `uv run --with fonttools …`), 14 (`uv run --with pillow python scripts/make_icons.py`) and 15 (`curl` plus the Python heredoc that verifies HTMX) run in Git Bash (the Bash tool). In PowerShell `curl` is an alias of `Invoke-WebRequest` and the flags differ. `build/` is already in `.gitignore`.
- **V8. `/auth/callback` versus ruling 1.** Keep dashboard Task 12's `GET /auth/callback` route and its `f"{config.base_url}/auth/callback"` redirect URI unchanged. The MCP proxy uses `/oauth/callback` (core Task 18), and `PathDispatch` sends `/auth/*` to the web app (pinned by core Task 21's routing test). The Google OAuth client registers both URIs (core Task 27 runbook).
- **V9. Temporary `/app/account` route.** Dashboard Task 12 Step 3 adds an `account_stub` route "deleted in Task 26". Dashboard Task 26 never runs in v0, so the stub and the `csrf_of` helper that loads it stay until core Task 25 deletes the stub and points `csrf_of` at `/app/connect`.
- **V10. Adapters.** Dashboard Task 7's "follow-up plan" for Postgres adapters is core Task 23. The `stripe` package is installed by V4; nothing in v0 calls it (spec 12).
- **V11. `dashboard-demo`.** Append the recipe as written (port 8780; no clash with `serve` on 8000).
- **V12. Merge criterion.** The dashboard header's "a branch is ready to merge when `uv run just test-e2e` also passes" does not apply: there is no `test-e2e` in v0. The merge criterion is `uv run just check` plus core Task 28.
- **V13. Domain purity and coverage.** `tutor.domain.dashboard` falls under the mypy strict override and the 90% `src/tutor/domain/*` coverage gate. Dashboard Tasks 3, 4 and 6 produce functions no v0 page renders; they are built and tested as written (spec 12).
- **V14. Commits in dashboard plan steps** that list `git add src/tutor/web tests/unit/web justfile` also add `pyproject.toml uv.lock` when the task changed them.
- **V15. Manual browser checks** in dashboard Tasks 12–15 (`just dashboard-demo` in Chrome) are done as written. The implementer records each check's outcome in the ledger line of that dashboard task.
- **V16. pip-audit.** If a new dependency has a known vulnerability, stop and rule (upgrade or pin a fixed version). Never pass `--ignore-vuln`.
- **V17. Stop-hook budget.** `uv run just check-fast` must stay under 30 s. If the web tests push it over, ledger the timing; do not mark web tests `integration` to hide them.
- **V18. No spec or requirements edits.** The dashboard plan's "spec is amended in the same branch" refers to the dashboard spec's own Amendments section; Part 1 Tasks 1–15 write none. `docs/requirements.md` is never edited (hook-protected).

- [ ] **Step 3: Execute dashboard Part 1 Tasks 1–15**

The controller dispatches one implementer per dashboard task in order (1 → 15), each with the dashboard task text plus V1–V18, and runs the two-stage review (spec compliance, then code quality) that `superpowers:subagent-driven-development` prescribes. Dispatch `security-reviewer` on dashboard Tasks 10–12 (headers, sessions, CSRF, Google login). The ledger names the dashboard Part 1 file while these run.

- [ ] **Step 4: Verify the whole of Part 1**

Run: `uv run pytest tests/unit/domain -k dashboard -q` then `uv run pytest tests/unit/web -q`
Expected: PASS (every test the dashboard Part 1 tasks wrote).

Run: `uv run just fmt` then `uv run just check`
Expected: PASS, with `src/tutor/domain/*` coverage at or above 90% and pip-audit clean.

Run: `git log --format=%B -15` and check that each of the 15 dashboard commits ends with the `Co-Authored-By` trailer.

- [ ] **Step 5: Record the result in the ledger**

No extra commit: each dashboard task committed its own work. Add one ledger line:
`core Task 22 (dashboard Part 1 Tasks 1–15): DONE at <HEAD sha>; uv run just check PASS (<N> passed, domain coverage <P>%); rulings V1–V18 applied; manual checks: <one line per dashboard Task 12–15 check>.`
The SDD ledger then returns to this plan's file 2 for core Task 23.

---

### Task 23: Web adapters on Postgres and app wiring

Postgres adapters for every dashboard port the v0 pages use, the v0 stand-ins for billing, subscriptions and settings, and the product app now serving the website through `PathDispatch`. Every query goes through `scoped_connection`, so row-level security applies to the web exactly as to MCP. Controller ruling 3 adds one request-log line per HTTP request.

> RULING (Inicio week trail): plan-lite has no calendar dates; the learner chose days per week, not days. `HomeData.planned_days` therefore holds only (a) each local day of this week on which the learner closed an on-plan session, titled with that session's item, and (b) today, titled with the next pending plan item (the item `start_lesson` would offer today), unless (a) already covers today. No future or past date is invented, so the trail never shows "sin sesión" (missed) in v0; days without practice show as rest. `session_marks` are this week's sessions on their local start day.

> RULING (Free counters): `usage()` returns real counts with `session_cap = glossary_cap = UNCAPPED` (2**31 − 1). `banners_for` then raises no `session_limit` or `glossary_limit` banner, and `render` passes `usage=None` to templates when the cap is `UNCAPPED`, so no "N de M" counter appears (spec 12: "`usage` returns no caps, so the Free counters are hidden").

> RULING (64 KB on the web): the web app is wrapped in the same `BodySizeGuard` as MCP (spec 5: request body ≤ 64 KB), and both apps are wrapped in `RequestLog`, so `build_app` still returns `PathDispatch`.

**Files:**
- Create: `src/tutor/web/pg.py`
- Modify: `src/tutor/web/ports.py` (append `UNCAPPED`)
- Modify: `src/tutor/web/views.py` (`render` hides uncapped counters)
- Modify: `src/tutor/app.py` (`RequestLog`, `log_path`, `build_app` builds the web app)
- Create: `tests/web_pg_support.py` (shared helper for Postgres web tests; unique basename)
- Test: `tests/unit/web/test_web_v0.py`, `tests/unit/app/test_app_request_log.py`, `tests/integration/test_pg_web.py`
- Modify: `tests/unit/app/test_app_http.py` (the `build_app` test now sees the website)

**Interfaces:**
- Consumes: `scoped_connection`, `PgIdentity`, `PgUnitOfWork` (`tutor.db.uow`, Tasks 14–17); `PgAuditRepo` (`tutor.db.repos.people`, Task 16); tables (`tutor.db.tables`); `current_streak` (`tutor.services.context`, Task 10); `PlanItemRow` (`tutor.services.ports`); `find_turn` (Task 1); `user_hash` (`tutor.mcp.observe`, Task 20); `PathDispatch`, `BodySizeGuard`, `MCP_PATH`, `build_app` (Task 21); `Settings` (Task 18); everything listed under core Task 22 "Produces".
- Produces: `class PgWebBackend(engine, clock)` implementing `UserDirectory`, `WebSessionStore`, `DashboardReader`, `GlossaryEditor`, `AccountService`; `FREE: Subscription`; `V0Subscriptions`, `DisabledBilling`, `BillingDisabled(RuntimeError)`, `V0Settings`; `pg_web_deps(engine: Engine, settings: Settings, config: WebConfig) -> WebDeps`; `tutor.web.ports.UNCAPPED = 2**31 - 1`; `tutor.app.RequestLog(app, clock=time.perf_counter)`, `tutor.app.log_path(path) -> str`; `build_app(settings, *, engine=None, web_config=None) -> PathDispatch` serving the website; helper module `tests/web_pg_support.py` (`BASE`, `WEB_CONFIG`, `pg_settings()`, `pg_web_app(engine, google)`, `google_login(client, google, identity, next_path="/app/")`, `csrf_token(engine, client)`).

- [ ] **Step 1a: Write the shared helper and the failing unit tests**

`tests/web_pg_support.py`:

```python
"""Dashboard app on PgWebBackend with a fake Google login (core Tasks 23 and 25)."""

from dataclasses import replace

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from tutor.db.tables import web_sessions
from tutor.settings import Settings
from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.memory import FakeGoogle
from tutor.web.pg import pg_web_deps
from tutor.web.ports import GoogleIdentity
from tutor.web.sessions import COOKIE, hash_token

BASE = "https://testserver"
WEB_CONFIG = WebConfig(
    env="test", base_url=BASE, mcp_url=f"{BASE}/mcp", support_email="soporte@example.test"
)


def pg_settings() -> Settings:
    return Settings.from_env(
        {
            "TUTOR_BASE_URL": BASE,
            "DATABASE_URL": "postgresql://tutor:tutor@localhost:5433/tutor_test",
            "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
            "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
            "TUTOR_JWT_SIGNING_KEY": "j" * 40,
            "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
            "TUTOR_WEB_SESSION_SECRET": "w" * 40,
        }
    )


def pg_web_app(engine: Engine, google: FakeGoogle) -> FastAPI:
    """The production web deps with Google replaced by FakeGoogle."""
    deps = replace(pg_web_deps(engine, pg_settings(), WEB_CONFIG), google=google)
    return create_app(deps, WEB_CONFIG)


def google_login(
    client: TestClient, google: FakeGoogle, identity: GoogleIdentity, next_path: str = "/app/"
) -> str:
    """Run /auth/google -> /auth/callback; return where the callback redirects."""
    google.next_identity = identity
    start = client.get(f"/auth/google?next={next_path}")
    assert start.status_code == 302, start.text
    callback = client.get(start.headers["location"].replace(BASE, ""))
    assert callback.status_code == 303, callback.text
    return callback.headers["location"]


def csrf_token(engine: Engine, client: TestClient) -> str:
    """The logged-in session's CSRF token, read as the owner from web_sessions."""
    token = client.cookies.get(COOKIE)
    assert token, "no session cookie"
    with engine.connect() as conn:
        value = conn.execute(
            select(web_sessions.c.csrf_token).where(web_sessions.c.token_hash == hash_token(token))
        ).scalar_one()
    return str(value)
```

`tests/unit/web/test_web_v0.py`:

```python
"""v0 stand-ins for billing, subscriptions and settings; uncapped Free usage (core Task 23)."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from web_pg_support import WEB_CONFIG, pg_settings

from tutor.domain.dashboard.billing import banners_for
from tutor.domain.dashboard.types import FreeUsage, SubStatus, Subscription, Tier, User
from tutor.web.config import WebConfig
from tutor.web.deps import current_user
from tutor.web.demo import DemoUsers
from tutor.web.google import GoogleOidcLogin
from tutor.web.memory import MemoryBackend
from tutor.web.pg import (
    FREE,
    BillingDisabled,
    DisabledBilling,
    PgWebBackend,
    V0Settings,
    V0Subscriptions,
    pg_web_deps,
)
from tutor.web.ports import UNCAPPED
from tutor.web.views import render

from .conftest import TODAY

pytestmark = pytest.mark.unit

Login = Callable[[UUID], TestClient]
FREE_SUB = Subscription(Tier.FREE, SubStatus.NONE, "price_29", 2900)


def _probe(app: FastAPI) -> None:
    @app.get("/app/v0-probe", response_class=HTMLResponse)
    async def probe(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
        return render(request, "layouts/app.html", {"active_nav": "home"})


def test_v0_subscription_is_always_free_and_raises_no_banner() -> None:
    subs = V0Subscriptions()
    sub = subs.subscription(uuid4())
    assert sub == FREE
    assert (sub.tier, sub.status) == (Tier.FREE, SubStatus.NONE)
    usage = FreeUsage(500, UNCAPPED, 5000, UNCAPPED, date(2027, 1, 18))
    assert banners_for(sub, usage, TODAY) == ()
    assert subs.customer_id(uuid4()) is None
    assert subs.user_for_customer("cus_1") is None
    with pytest.raises(NotImplementedError):
        subs.link_customer(uuid4(), "cus_1")


def test_billing_is_disabled() -> None:
    billing = DisabledBilling()
    with pytest.raises(BillingDisabled):
        billing.checkout_url(
            user_id=uuid4(), customer_id=None, price_id="p", success_url="/", cancel_url="/"
        )
    with pytest.raises(BillingDisabled):
        billing.portal_url(customer_id="cus_1", return_url="/")
    with pytest.raises(BillingDisabled):
        billing.parse_event(b"{}", "sig")


def test_v0_settings_are_empty() -> None:
    settings = V0Settings()
    assert settings.list_settings() == ()
    assert settings.update_setting("k", "1", uuid4(), datetime(2026, 10, 14, tzinfo=UTC)) is None


def test_uncapped_usage_shows_no_banner_and_no_counter(
    app: FastAPI, login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    _probe(app)
    backend.subs[demo.ana] = FREE_SUB
    backend.session_cap = backend.glossary_cap = UNCAPPED
    backend.sessions_this_week[demo.ana] = 50
    html = login(demo.ana).get("/app/v0-probe").text
    assert 'class="banner' not in html
    assert "sesiones esta semana" not in html


def test_capped_usage_still_reaches_the_banners(
    app: FastAPI, login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    _probe(app)
    backend.subs[demo.ana] = FREE_SUB
    backend.sessions_this_week[demo.ana] = backend.session_cap
    assert 'class="banner' in login(demo.ana).get("/app/v0-probe").text


def test_pg_web_deps_wires_the_v0_adapters() -> None:
    deps = pg_web_deps(sqlalchemy.create_engine("sqlite://"), pg_settings(), WEB_CONFIG)
    assert isinstance(deps.users, PgWebBackend)
    assert deps.users is deps.sessions is deps.reader is deps.glossary is deps.account
    assert isinstance(deps.subscriptions, V0Subscriptions)
    assert isinstance(deps.billing, DisabledBilling)
    assert isinstance(deps.settings, V0Settings)
    assert isinstance(deps.google, GoogleOidcLogin)
    assert deps.clock().tzinfo is not None


def test_pg_web_deps_refuses_the_test_login() -> None:
    config = WebConfig(
        env="test",
        base_url="https://testserver",
        mcp_url="https://testserver/mcp",
        support_email="soporte@example.test",
        test_login=True,
    )
    with pytest.raises(ValueError, match="test login"):
        pg_web_deps(sqlalchemy.create_engine("sqlite://"), pg_settings(), config)
```

`tests/unit/app/test_app_request_log.py`:

```python
"""One JSON line per HTTP request, without query strings or ids (spec 13; controller ruling 3)."""

import json
import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tutor.app import Message, RequestLog, Scope, log_path
from tutor.mcp.observe import user_hash

pytestmark = pytest.mark.unit


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _drop(message: Message) -> None:
    return None


def _ticks(*values: float) -> Any:
    it = iter(values)
    return lambda: next(it)


def _records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "tutor.http"]


def test_log_path_hides_ids_and_never_sees_a_query() -> None:
    sid = uuid4()
    assert log_path(f"/app/sessions/{sid}") == "/app/sessions/:id"
    assert log_path(f"/app/glossary/{sid}/edit") == "/app/glossary/:id/edit"
    assert log_path("/oauth/callback") == "/oauth/callback"


@pytest.mark.asyncio
async def test_one_line_with_status_latency_and_user_hash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    uid = uuid4()

    async def inner(scope: Scope, receive: Any, send: Any) -> None:
        scope.setdefault("state", {})["user"] = SimpleNamespace(id=uid)
        await send({"type": "http.response.start", "status": 303, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    caplog.set_level(logging.INFO, logger="tutor.http")
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": f"/app/sessions/{uuid4()}",
        "query_string": b"code=secret-code&state=s",
        "headers": [],
    }
    await RequestLog(inner, clock=_ticks(1.0, 1.25))(scope, _receive, _drop)
    assert _records(caplog) == [
        {
            "kind": "http",
            "method": "GET",
            "path": "/app/sessions/:id",
            "status": 303,
            "ms": 250.0,
            "user_hash": user_hash(uid),
        }
    ]
    assert "secret-code" not in caplog.text


@pytest.mark.asyncio
async def test_a_crash_is_logged_as_500_and_still_raised(caplog: pytest.LogCaptureFixture) -> None:
    async def inner(scope: Scope, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    caplog.set_level(logging.INFO, logger="tutor.http")
    scope: Scope = {"type": "http", "method": "POST", "path": "/token", "headers": []}
    with pytest.raises(RuntimeError):
        await RequestLog(inner, clock=_ticks(0.0, 0.002))(scope, _receive, _drop)
    [line] = _records(caplog)
    assert (line["status"], line["user_hash"], line["path"]) == (500, None, "/token")


@pytest.mark.asyncio
async def test_lifespan_passes_through_unlogged(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    caplog.set_level(logging.INFO, logger="tutor.http")
    await RequestLog(inner)({"type": "lifespan"}, _receive, _drop)
    assert (seen, _records(caplog)) == (["lifespan"], [])
```

In `tests/unit/app/test_app_http.py`, add to the imports:

```python
import logging

from tutor.web.config import WebConfig
from tutor.web.security import CSP
```

add after `BASE_URL`:

```python
WEB_CONFIG = WebConfig(
    env="test", base_url=BASE_URL, mcp_url=f"{BASE_URL}/mcp", support_email="soporte@example.test"
)
```

and replace `test_build_app_wires_auth_and_dispatch` with:

```python
@pytest.mark.asyncio
async def test_build_app_serves_mcp_and_the_website_with_separate_headers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="tutor.http")
    app = build_app(
        settings(tmp_path), engine=sqlalchemy.create_engine("sqlite://"), web_config=WEB_CONFIG
    )
    async with serving(app) as client:
        denied = await client.post("/mcp", json=INIT, headers=HEADERS)
        metadata = await client.get("/.well-known/oauth-authorization-server")
        home = await client.get("/app/")
        login = await client.get("/login")
        await client.get("/oauth/callback?code=secret-code&state=s")
        big = await client.post(
            "/app/lang",
            content=b"x" * 70_000,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert denied.status_code == 401
    assert "content-security-policy" not in denied.headers
    assert "content-security-policy" not in metadata.headers
    assert (home.status_code, home.headers["location"]) == (303, "/login?next=%2Fapp%2F")
    assert login.status_code == 200
    assert login.headers["content-security-policy"] == CSP
    assert login.headers["cache-control"] == "no-store"
    assert big.status_code == 413
    assert (tmp_path / "oauth").is_dir()
    logged = [r.getMessage() for r in caplog.records if r.name == "tutor.http"]
    assert any('"path": "/login"' in line for line in logged)
    assert not any("secret-code" in line for line in logged)
```

- [ ] **Step 1b: Write the failing integration tests**

`tests/integration/test_pg_web.py`:

```python
"""PgWebBackend on db-test: every port method v0 pages call, seeded through the core services."""

import zoneinfo
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from web_pg_support import BASE, csrf_token, google_login, pg_web_app

from tutor.db.tables import audit_log, glossary_items, users, web_sessions
from tutor.domain.dashboard import types as dash
from tutor.domain.dashboard.billing import banners_for
from tutor.domain.glossary import IncomingItem
from tutor.domain.profile import ProfileInput
from tutor.domain.text import count_words
from tutor.domain.validation import Evidence, ReportedError
from tutor.services.context import Services
from tutor.services.glossary import save_glossary
from tutor.services.lesson import start_lesson
from tutor.services.ports import IdentityResolver, UowFactory
from tutor.services.profile import get_profile, save_profile
from tutor.services.session_end import end_session
from tutor.services.views import LessonStart, StartLessonRequest
from tutor.web.memory import FakeGoogle, FixedClock
from tutor.web.pg import FREE, PgWebBackend
from tutor.web.ports import UNCAPPED, GoogleIdentity, WebSession
from tutor.web.sessions import COOKIE, hash_token

pytestmark = pytest.mark.integration

TODAY = date(2026, 10, 14)  # NOW is 15:00 UTC, 09:00 in Mexico City
TURNS = (
    "Yesterday I goed to the standup and I explained the login bug to the whole team.",
    "Then I went to the review and we talked about the payments API for a long time.",
    "Tomorrow I will keep you posted about the trade-off and the rollback plan we chose.",
)
ERROR = ReportedError(said="I goed", correct="I went", category="grammar")
PROFILE = ProfileInput(
    self_level="B1",
    domains=["it"],
    use_cases=["standup", "code_review"],
    minutes_per_day=20,
    days_per_week=3,
    target_level="B2",
)


def _evidence(
    chunks_used: Sequence[str],
    *,
    turns: Sequence[str] = TURNS,
    errors: Sequence[ReportedError] = (ERROR,),
) -> Evidence:
    return Evidence(
        user_turns=tuple(turns),
        errors=tuple(errors),
        chunks_used=tuple(chunks_used),
        task_result="achieved",
        hints_given=1,
        cefr_level="B1",
        cefr_confidence="medium",
        cefr_evidence=("Explains a blocker.",),
        confidence_1_5=4,
        assistant_words_estimate=None,
    )


def _raw(ev: Evidence) -> dict[str, Any]:
    """The JSON the MCP layer stores as sessions.raw_evidence."""
    return {
        "user_turns": list(ev.user_turns),
        "errors": [
            {"said": e.said, "correct": e.correct, "category": e.category} for e in ev.errors
        ],
        "chunks_used": list(ev.chunks_used),
        "task_result": ev.task_result,
        "hints_given": ev.hints_given,
        "cefr_estimate": {
            "speaking": ev.cefr_level,
            "confidence": ev.cefr_confidence,
            "evidence": list(ev.cefr_evidence),
        },
        "confidence_1_5": ev.confidence_1_5,
        "assistant_words_estimate": ev.assistant_words_estimate,
    }


def _item(text: str) -> IncomingItem:
    return IncomingItem(
        kind="chunk",
        text=text,
        meaning="Undo a release.",
        context_sentence=f"We had to {text} on Friday.",
        domain="it",
    )


def _events(engine: Engine, uid: UUID) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(select(audit_log.c.event).where(audit_log.c.user_id == uid))
        return sorted(rows.scalars())


@pytest.fixture
def clock(now: datetime) -> FixedClock:
    return FixedClock(now)


@pytest.fixture
def svc(uow_factory: UowFactory, clock: FixedClock) -> Services:
    return Services(
        uow=uow_factory, clock=clock, valid_timezones=frozenset(zoneinfo.available_timezones())
    )


@pytest.fixture
def backend(engine: Engine, clock: FixedClock) -> PgWebBackend:
    return PgWebBackend(engine, clock)


@pytest.fixture
def seeded(svc: Services, clock: FixedClock, user_id: UUID) -> LessonStart:
    """User A: onboarded, one closed 12-minute voice lesson today (2 phrases used, one error
    taken up later) and three glossary saves: confirmed, provisional and declined."""
    save_profile(svc, user_id, PROFILE)
    lesson = start_lesson(svc, user_id, StartLessonRequest(mode="voice"))
    save_glossary(svc, user_id, lesson.session_id, "confirmed", [_item("roll back")])
    save_glossary(svc, user_id, lesson.session_id, "provisional", [_item("heads-up")])
    save_glossary(svc, user_id, lesson.session_id, "declined", [_item("blocker")])
    clock.advance(timedelta(minutes=12))
    ev = _evidence([c.id for c in lesson.item.chunks][:2])
    end_session(svc, user_id, lesson.session_id, ev, _raw(ev))
    return lesson


# --- UserDirectory -----------------------------------------------------------


def test_sign_in_creates_a_user_with_defaults_and_audits(
    engine: Engine, backend: PgWebBackend, now: datetime
) -> None:
    ident = GoogleIdentity("g-nora", "nora@example.com", "Nora", True)
    first = backend.sign_in(ident, now)
    again = backend.sign_in(ident, now)
    assert again.id == first.id
    assert (first.display_name, first.role, first.lang) == (
        "Nora",
        dash.Role.LEARNER,
        dash.Lang.ES_MX,
    )
    assert (first.timezone, first.reduce_motion, first.deletion_requested_at) == (
        "America/Mexico_City",
        False,
        None,
    )
    assert _events(engine, first.id) == ["user_created", "web_login", "web_login"]


def test_sign_in_matches_by_sub_never_by_email(backend: PgWebBackend, now: datetime) -> None:
    a = backend.sign_in(GoogleIdentity("g-1", "same@example.com", "One", True), now)
    b = backend.sign_in(GoogleIdentity("g-2", "same@example.com", "Two", True), now)
    assert a.id != b.id


def test_sign_in_finds_the_user_mcp_created_and_fills_its_name(
    engine: Engine, backend: PgWebBackend, identity: IdentityResolver, now: datetime
) -> None:
    created = identity.resolve("sub-mcp", None, None, now)
    user = backend.sign_in(GoogleIdentity("sub-mcp", "mia@example.com", "Mia", True), now)
    assert user.id == created.id
    assert user.display_name == "Mia"
    assert _events(engine, user.id) == ["web_login"]


def test_sign_in_of_a_deletion_pending_user_records_no_login(
    engine: Engine, backend: PgWebBackend, now: datetime
) -> None:
    user = backend.sign_in(GoogleIdentity("g-del", "del@example.com", "Del", True), now)
    with engine.begin() as conn:
        conn.execute(update(users).where(users.c.id == user.id).values(deletion_requested_at=now))
    again = backend.sign_in(GoogleIdentity("g-del", "del@example.com", "Del", True), now)
    assert again.deletion_requested_at == now
    assert _events(engine, user.id) == ["user_created", "web_login"]


def test_find_user(backend: PgWebBackend, user_id: UUID) -> None:
    found = backend.find_user(user_id)
    assert found is not None and found.id == user_id
    assert backend.find_user(uuid4()) is None


# --- WebSessionStore ---------------------------------------------------------


def test_web_sessions_round_trip_by_hash_and_end_per_user(
    backend: PgWebBackend, user_id: UUID, other_user_id: UUID, now: datetime
) -> None:
    anon = WebSession(
        token_hash="a" * 64,
        user_id=None,
        csrf_token="csrf-a",
        created_at=now,
        last_seen_at=now,
        data={"_state_google_x": {"data": {"redirect_uri": f"{BASE}/auth/callback"}}},
    )
    backend.save_session(anon)
    assert backend.load_session("a" * 64) == anon
    assert backend.load_session("b" * 64) is None
    later = replace(anon, user_id=user_id, last_seen_at=now + timedelta(minutes=6), data={})
    backend.save_session(later)
    assert backend.load_session("a" * 64) == later
    backend.save_session(WebSession("c" * 64, other_user_id, "csrf-c", now, now, {}))
    backend.save_session(WebSession("d" * 64, user_id, "csrf-d", now, now, {}))
    backend.delete_user_sessions(user_id)
    assert backend.load_session("a" * 64) is None
    assert backend.load_session("d" * 64) is None
    assert backend.load_session("c" * 64) is not None
    backend.delete_session("c" * 64)
    assert backend.load_session("c" * 64) is None


# --- DashboardReader ---------------------------------------------------------


def test_home_of_a_new_learner_is_empty(backend: PgWebBackend, user_id: UUID) -> None:
    home = backend.home(user_id, TODAY)
    assert (home.has_connected, home.has_plan, home.today) == (False, False, None)
    assert (home.planned_days, home.session_marks, home.stamps) == ((), (), ())
    assert (home.streak, home.reviews_due, home.provisional_items) == (0, 0, 0)
    assert (home.last_session, home.latest_report, home.newest_closed_session_id) == (
        None,
        None,
        None,
    )
    assert home.week_start == date(2026, 10, 12)


def test_home_after_one_lesson(
    backend: PgWebBackend, seeded: LessonStart, svc: Services, user_id: UUID
) -> None:
    home = backend.home(user_id, TODAY)
    plan = get_profile(svc, user_id).plan
    assert plan is not None and plan.next_item is not None
    title = seeded.item.can_do_es
    assert (home.has_connected, home.has_plan, home.streak) == (True, True, 1)
    assert home.today is not None
    assert home.today.title == plan.next_item.can_do_es
    assert home.today.skill.value == plan.next_item.skill
    assert home.planned_days == (dash.PlannedDay(TODAY, title),)
    assert home.session_marks == (dash.SessionMark(TODAY, True, dash.SessionStatus.CLOSED),)
    chunks = seeded.item.chunks
    assert {s.text: s.used for s in home.stamps} == {c.text: i < 2 for i, c in enumerate(chunks)}
    last = home.last_session
    assert last is not None and last.id == seeded.session_id
    assert (last.label, last.mode, last.duration_min) == (title, dash.Mode.VOICE, 12)
    words = sum(count_words(t) for t in TURNS)
    assert last.words_per_min == pytest.approx(words / 12)
    assert (home.reviews_due, home.provisional_items) == (0, 1)
    assert home.newest_closed_session_id == seeded.session_id
    assert home.last_celebrated_session_id is None
    backend.mark_celebrated(user_id, seeded.session_id)
    assert backend.home(user_id, TODAY).last_celebrated_session_id == seeded.session_id


def test_home_today_is_the_next_item_when_nothing_closed_today(
    backend: PgWebBackend, svc: Services, user_id: UUID
) -> None:
    save_profile(svc, user_id, PROFILE)
    plan = get_profile(svc, user_id).plan
    assert plan is not None and plan.next_item is not None
    home = backend.home(user_id, TODAY)
    assert home.planned_days == (dash.PlannedDay(TODAY, plan.next_item.can_do_es),)
    assert home.has_plan and not home.has_connected


def test_usage_counts_without_caps(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID
) -> None:
    usage = backend.usage(user_id, TODAY)
    assert usage == dash.FreeUsage(1, UNCAPPED, 2, UNCAPPED, date(2026, 10, 19))
    assert banners_for(FREE, usage, TODAY) == ()


def test_sessions_filter_and_page(
    backend: PgWebBackend,
    seeded: LessonStart,
    svc: Services,
    clock: FixedClock,
    user_id: UUID,
) -> None:
    clock.advance(timedelta(minutes=1))
    short = start_lesson(svc, user_id, StartLessonRequest(mode="text"))
    clock.advance(timedelta(minutes=1))
    ev = _evidence([], turns=("Short answer only.",), errors=())
    end_session(svc, user_id, short.session_id, ev, _raw(ev))
    first = backend.sessions(user_id, dash.SessionFilter(), 1, 1)
    assert [s.id for s in first.items] == [short.session_id]
    assert (first.items[0].status, first.items[0].mode, first.has_next) == (
        dash.SessionStatus.INCOMPLETE,
        dash.Mode.TEXT,
        True,
    )
    second = backend.sessions(user_id, dash.SessionFilter(), 2, 1)
    assert ([s.id for s in second.items], second.has_next) == ([seeded.session_id], False)
    voice = backend.sessions(user_id, dash.SessionFilter(mode=dash.Mode.VOICE), 1, 20)
    assert [s.id for s in voice.items] == [seeded.session_id]
    closed = backend.sessions(user_id, dash.SessionFilter(status=dash.SessionStatus.CLOSED), 1, 20)
    assert [s.task_result for s in closed.items] == [dash.TaskResult.ACHIEVED]


def test_session_detail(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID, other_user_id: UUID
) -> None:
    detail = backend.session_detail(user_id, seeded.session_id)
    assert detail is not None
    assert detail.errors == (dash.ErrorView("I goed", "I went", dash.ErrorCategory.GRAMMAR, True),)
    texts = [c.text for c in seeded.item.chunks]
    assert set(detail.chunks_offered) == set(texts)
    assert set(detail.chunks_used) == set(texts[:2])
    assert detail.cefr == dash.CefrOpinion("B1", dash.Confidence.MEDIUM, ("Explains a blocker.",))
    assert (detail.hints_given, detail.confidence_1_5, detail.user_turns) == (1, 4, TURNS)
    assert backend.session_detail(other_user_id, seeded.session_id) is None
    assert backend.session_detail(user_id, uuid4()) is None


def test_glossary_hides_declined_and_uses_local_dates(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID, other_user_id: UUID
) -> None:
    rows = backend.glossary(user_id, dash.GlossaryFilter(), TODAY)
    assert {r.text: (r.status, r.due_on, r.expires_on) for r in rows} == {
        "roll back": (dash.GlossaryStatus.CONFIRMED, date(2026, 10, 15), None),
        "heads-up": (dash.GlossaryStatus.PROVISIONAL, None, date(2026, 10, 21)),
    }
    only = backend.glossary(
        user_id, dash.GlossaryFilter(status=dash.GlossaryStatus.PROVISIONAL), TODAY
    )
    assert [r.text for r in only] == ["heads-up"]
    assert backend.glossary_domains(user_id) == ("it",)
    assert backend.glossary(other_user_id, dash.GlossaryFilter(), TODAY) == ()
    assert backend.glossary_domains(other_user_id) == ()


def test_update_glossary_text_is_scoped_to_the_owner(
    engine: Engine,
    backend: PgWebBackend,
    seeded: LessonStart,
    user_id: UUID,
    other_user_id: UUID,
) -> None:
    target = next(
        r for r in backend.glossary(user_id, dash.GlossaryFilter(), TODAY) if r.text == "roll back"
    )
    updated = backend.update_glossary_text(
        user_id, target.id, "deshacer un despliegue", "We had to roll back on Friday."
    )
    assert updated is not None
    assert (updated.meaning, updated.due_on) == ("deshacer un despliegue", target.due_on)
    assert backend.update_glossary_text(other_user_id, target.id, "x", "y") is None
    with engine.connect() as conn:
        declined = conn.execute(
            select(glossary_items.c.id).where(glossary_items.c.text == "blocker")
        ).scalar_one()
    assert backend.update_glossary_text(user_id, declined, "x", "y") is None


def test_has_any_session(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID, other_user_id: UUID
) -> None:
    assert backend.has_any_session(user_id)
    assert not backend.has_any_session(other_user_id)


def test_account_preferences_and_install_prompt(
    backend: PgWebBackend, seeded: LessonStart, user_id: UUID, other_user_id: UUID, now: datetime
) -> None:
    account = backend.account(user_id)
    assert account.profile == dash.Profile(("it",), 20, 3, "B2", None)
    assert account.prefs == dash.Preferences(dash.Lang.ES_MX, False, True, True)
    assert (account.clients, account.subscription, account.install_prompt_dismissed) == (
        (),
        FREE,
        False,
    )
    assert backend.account(other_user_id).profile is None
    backend.set_preferences(user_id, dash.Preferences(dash.Lang.EN, True, False, False))
    backend.dismiss_install_prompt(user_id, now)
    after = backend.account(user_id)
    assert after.prefs == dash.Preferences(dash.Lang.EN, True, False, False)
    assert after.install_prompt_dismissed
    user = backend.find_user(user_id)
    assert user is not None and (user.lang, user.reduce_motion) == (dash.Lang.EN, True)


def test_postponed_methods_raise(backend: PgWebBackend, user_id: UUID, now: datetime) -> None:
    for call in (
        lambda: backend.plan(user_id),
        lambda: backend.progress(user_id),
        lambda: backend.reports(user_id),
        lambda: backend.revoke_client(user_id, "c"),
        lambda: backend.export_data(user_id),
        lambda: backend.request_deletion(user_id, now),
    ):
        with pytest.raises(NotImplementedError):
            call()


# --- HTTP on Postgres ----------------------------------------------------------


def test_login_csrf_and_logout_through_the_session_store(engine: Engine) -> None:
    google = FakeGoogle()
    app = pg_web_app(engine, google)
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        target = google_login(c, google, GoogleIdentity("g-web", "web@example.com", "Wen", True))
        assert target == "/app/"
        token = c.cookies.get(COOKIE)
        assert token
        with engine.connect() as conn:
            row = conn.execute(
                select(web_sessions.c.user_id).where(web_sessions.c.token_hash == hash_token(token))
            ).one()
            sessions_total = conn.execute(select(func.count()).select_from(web_sessions))
            assert sessions_total.scalar_one() == 1  # the anonymous OAuth session was replaced
        assert row.user_id is not None
        dismissed = c.post(
            "/app/install/dismiss", data={"csrf_token": csrf_token(engine, c), "back": "/app/"}
        )
        assert dismissed.status_code == 303
        out = c.post("/auth/logout", data={"csrf_token": csrf_token(engine, c)})
        assert out.status_code == 303
    with engine.connect() as conn:
        dismissed_at = conn.execute(
            select(users.c.install_prompt_dismissed_at).where(users.c.id == row.user_id)
        ).scalar_one()
        left = conn.execute(select(func.count()).select_from(web_sessions)).scalar_one()
    assert dismissed_at is not None
    assert left == 0
    assert _events(engine, row.user_id) == ["user_created", "web_login"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/web/test_web_v0.py tests/unit/app/test_app_request_log.py tests/unit/app/test_app_http.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'tutor.web.pg'` (and `ImportError` for `RequestLog`).

Run: `uv run just test-int`
Expected: FAIL at collection of `tests/integration/test_pg_web.py` with the same `ModuleNotFoundError`.

- [ ] **Step 3a: Add `UNCAPPED` and hide uncapped counters**

Append to `src/tutor/web/ports.py`:

```python
UNCAPPED = 2**31 - 1
"""A FreeUsage cap at this value means "no cap" (core loop v0: caps are not enforced, spec 3.2).
banners_for raises no limit banner for it, and render() hides the Free counters."""
```

In `src/tutor/web/views.py`, change the import line `from tutor.web.ports import WebDeps` to:

```python
from tutor.web.ports import UNCAPPED, WebDeps
```

and in `render`, replace the line

```python
        "usage": usage if subscription and subscription.tier is Tier.FREE else None,
```

with

```python
        "usage": (
            usage
            if subscription
            and subscription.tier is Tier.FREE
            and usage is not None
            and usage.session_cap < UNCAPPED
            else None
        ),
```

- [ ] **Step 3b: Write the adapters**

`src/tutor/web/pg.py`:

```python
"""Postgres adapters for the dashboard ports (core loop v0 spec section 12; plan Task 23).

Every query runs inside `tutor.db.uow.scoped_connection`, so row-level security applies:
learner data is read with `app.user_id`, web sessions with `app.web_session` (the cookie token's
SHA-256), which the session middleware knows before it knows the user.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Connection, Engine, Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping

from tutor.db.repos.people import PgAuditRepo
from tutor.db.tables import (
    glossary_items,
    profiles,
    review_states,
    session_errors,
    session_metrics,
    sessions,
    track_chunks,
    track_items,
    users,
    web_sessions,
)
from tutor.db.uow import PgIdentity, PgUnitOfWork, scoped_connection
from tutor.domain.dashboard import types as dash
from tutor.domain.dashboard.glossary import filter_glossary
from tutor.domain.dashboard.home import local_today, week_start
from tutor.domain.text import find_turn
from tutor.services.context import current_streak
from tutor.services.ports import PlanItemRow
from tutor.settings import Settings
from tutor.web.config import WebConfig
from tutor.web.google import GoogleOidcLogin
from tutor.web.ports import UNCAPPED, Clock, GoogleIdentity, WebDeps, WebSession

FREE = dash.Subscription(dash.Tier.FREE, dash.SubStatus.NONE, price_id="", price_cents=0)

_SESSION_COLUMNS = (
    sessions.c.id,
    sessions.c.plan_item_id,
    sessions.c.track_item_id,
    sessions.c.prep_text,
    sessions.c.mode,
    sessions.c.client,
    sessions.c.started_at,
    sessions.c.ended_at,
    sessions.c.status,
    sessions.c.low_trust,
    sessions.c.task_result,
    sessions.c.hints_given,
    sessions.c.cefr_estimate_speaking,
    sessions.c.cefr_confidence,
    sessions.c.confidence_1_5,
    sessions.c.chunks_offered,
    sessions.c.raw_evidence,
    session_metrics.c.duration_min,
    session_metrics.c.user_words_per_min,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _midnight(day: date, tz_name: str) -> datetime:
    """UTC instant of 00:00 on `day` in the learner's zone (DST-aware)."""
    return datetime.combine(day, time.min, tzinfo=_zone(tz_name)).astimezone(UTC)


def _user(m: RowMapping) -> dash.User:
    return dash.User(
        id=m["id"],
        display_name=m["display_name"],
        role=dash.Role(m["role"]),
        lang=dash.Lang(m["lang"]),
        timezone=m["timezone"],
        reduce_motion=m["reduce_motion"],
        deletion_requested_at=m["deletion_requested_at"],
    )


def _me(conn: Connection, user_id: UUID) -> RowMapping:
    return conn.execute(select(users).where(users.c.id == user_id)).mappings().one()


def _track(conn: Connection) -> dict[str, RowMapping]:
    return {r["id"]: r for r in conn.execute(select(track_items)).mappings().all()}


def _title(item: RowMapping | None, track_item_id: str, lang: str) -> str:
    """The can-do statement in the learner's UI language (seed data, not learner text)."""
    if item is None:
        return track_item_id
    return str(item["can_do_en"] if lang == dash.Lang.EN.value else item["can_do_es"])


def _chunk_texts(conn: Connection, ids: Sequence[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = conn.execute(
        select(track_chunks.c.id, track_chunks.c.text).where(track_chunks.c.id.in_(list(ids)))
    ).all()
    return {str(r.id): str(r.text) for r in rows}


def _valid_used(raw: Mapping[str, Any] | None, offered: Sequence[str]) -> frozenset[str]:
    """Spec 11.1 step 4: a reported chunk id counts only if the session offered it."""
    reported = (raw or {}).get("chunks_used") or ()
    return frozenset(c for c in reported if isinstance(c, str)) & frozenset(offered)


def _client(value: str) -> dash.Client:
    # The dashboard has no "unknown" client; v0 sessions come from Claude connectors.
    known = {c.value for c in dash.Client}
    return dash.Client(value) if value in known else dash.Client.CLAUDE


def _session_query(user_id: UUID) -> Select[Any]:
    return (
        select(*_SESSION_COLUMNS)
        .select_from(
            sessions.outerjoin(session_metrics, session_metrics.c.session_id == sessions.c.id)
        )
        .where(sessions.c.user_id == user_id)
    )


def _summary(m: RowMapping, track: Mapping[str, RowMapping], lang: str) -> dash.SessionSummary:
    duration = m["duration_min"]
    return dash.SessionSummary(
        id=m["id"],
        started_at=m["started_at"],
        mode=dash.Mode(m["mode"]),
        client=_client(m["client"]),
        label=m["prep_text"] or _title(track.get(m["track_item_id"]), m["track_item_id"], lang),
        duration_min=round(duration) if duration is not None else 0,
        words_per_min=m["user_words_per_min"],
        task_result=dash.TaskResult(m["task_result"]) if m["task_result"] else None,
        status=dash.SessionStatus(m["status"]),
        low_trust=m["low_trust"],
    )


def _glossary_query(user_id: UUID) -> Select[Any]:
    """Every glossary row of the user except declined ones (plan ruling 10)."""
    return (
        select(
            glossary_items.c.id,
            glossary_items.c.kind,
            glossary_items.c.text,
            glossary_items.c.meaning,
            glossary_items.c.context_sentence,
            glossary_items.c.domain,
            glossary_items.c.status,
            glossary_items.c.leech,
            glossary_items.c.provisional_expires_at,
            review_states.c.due_at,
        )
        .select_from(
            glossary_items.outerjoin(
                review_states, review_states.c.glossary_item_id == glossary_items.c.id
            )
        )
        .where(glossary_items.c.user_id == user_id, glossary_items.c.status != "declined")
    )


def _glossary_row(m: RowMapping, tz_name: str) -> dash.GlossaryRow:
    status = dash.GlossaryStatus(m["status"])
    provisional = status is dash.GlossaryStatus.PROVISIONAL
    due = m["due_at"]
    expires = m["provisional_expires_at"]
    return dash.GlossaryRow(
        id=m["id"],
        kind=dash.GlossaryKind(m["kind"]),
        text=m["text"],
        meaning=m["meaning"],
        context_sentence=m["context_sentence"],
        domain=m["domain"],
        status=status,
        due_on=None if provisional or due is None else local_today(due, tz_name),
        leech=m["leech"],
        expires_on=local_today(expires, tz_name) if provisional and expires is not None else None,
    )


def _planned_days(
    closed: Sequence[RowMapping],
    track: Mapping[str, RowMapping],
    lang: str,
    tz_name: str,
    today: date,
    next_item: PlanItemRow | None,
) -> tuple[dash.PlannedDay, ...]:
    """Ruling (Task 23): only days with a closed on-plan session, plus today when an item is
    pending. Plan-lite has no calendar, so no other date is invented."""
    days: dict[date, str] = {}
    for m in closed:
        if m["plan_item_id"] is not None:
            day = local_today(m["started_at"], tz_name)
            days.setdefault(day, _title(track.get(m["track_item_id"]), m["track_item_id"], lang))
    if next_item is not None:
        item_id = next_item.track_item_id
        days.setdefault(today, _title(track.get(item_id), item_id, lang))
    return tuple(dash.PlannedDay(day, title) for day, title in sorted(days.items()))


class PgWebBackend:
    """UserDirectory, WebSessionStore, DashboardReader, GlossaryEditor and AccountService."""

    def __init__(self, engine: Engine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock
        self._identity = PgIdentity(engine)

    # --- UserDirectory -----------------------------------------------------

    def sign_in(self, identity: GoogleIdentity, now: datetime) -> dash.User:
        """Find or create by Google sub (never by email); audit user_created and web_login."""
        resolved = self._identity.resolve(identity.sub, identity.email, identity.name or None, now)
        with scoped_connection(self._engine, user_id=resolved.id) as conn:
            row = _me(conn, resolved.id)
            fill: dict[str, Any] = {}
            if not row["display_name"]:
                fill["display_name"] = (identity.name or identity.email.split("@", 1)[0])[:200]
            if row["email"] is None:
                fill["email"] = identity.email
            if fill:
                conn.execute(update(users).where(users.c.id == resolved.id).values(**fill))
                row = _me(conn, resolved.id)
            audit = PgAuditRepo(conn, resolved.id)
            if resolved.created:
                audit.record("user_created", {"via": "web"}, now)
            if row["deletion_requested_at"] is None:
                audit.record("web_login", {}, now)
        return _user(row)

    def find_user(self, user_id: UUID) -> dash.User | None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            row = conn.execute(select(users).where(users.c.id == user_id)).mappings().one_or_none()
        return None if row is None else _user(row)

    # --- WebSessionStore ---------------------------------------------------

    def load_session(self, token_hash: str) -> WebSession | None:
        with scoped_connection(self._engine, web_session=token_hash) as conn:
            m = (
                conn.execute(select(web_sessions).where(web_sessions.c.token_hash == token_hash))
                .mappings()
                .one_or_none()
            )
        if m is None:
            return None
        return WebSession(
            token_hash=m["token_hash"],
            user_id=m["user_id"],
            csrf_token=m["csrf_token"],
            created_at=m["created_at"],
            last_seen_at=m["last_seen_at"],
            data=dict(m["data"]),
        )

    def save_session(self, session: WebSession) -> None:
        values: dict[str, Any] = {
            "user_id": session.user_id,
            "csrf_token": session.csrf_token,
            "data": dict(session.data),
            "last_seen_at": session.last_seen_at,
        }
        with scoped_connection(
            self._engine, user_id=session.user_id, web_session=session.token_hash
        ) as conn:
            conn.execute(
                pg_insert(web_sessions)
                .values(token_hash=session.token_hash, created_at=session.created_at, **values)
                .on_conflict_do_update(index_elements=[web_sessions.c.token_hash], set_=values)
            )

    def delete_session(self, token_hash: str) -> None:
        with scoped_connection(self._engine, web_session=token_hash) as conn:
            conn.execute(delete(web_sessions).where(web_sessions.c.token_hash == token_hash))

    def delete_user_sessions(self, user_id: UUID) -> None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            conn.execute(delete(web_sessions).where(web_sessions.c.user_id == user_id))

    # --- DashboardReader ---------------------------------------------------

    def home(self, user_id: UUID, today: date) -> dash.HomeData:
        now = self._clock()
        start = week_start(today)
        with scoped_connection(self._engine, user_id=user_id) as conn:
            me = _me(conn, user_id)
            tz, lang = str(me["timezone"]), str(me["lang"])
            track = _track(conn)
            uow = PgUnitOfWork(conn, user_id)
            plan = uow.plans.active()
            pending = sorted(
                (i for i in (plan.items if plan is not None else ()) if i.status == "pending"),
                key=lambda i: (i.week_no, i.order_no),
            )
            next_item = pending[0] if pending else None
            week = (
                conn.execute(
                    _session_query(user_id)
                    .where(
                        sessions.c.started_at >= _midnight(start, tz),
                        sessions.c.started_at < _midnight(start + timedelta(days=7), tz),
                    )
                    .order_by(sessions.c.started_at)
                )
                .mappings()
                .all()
            )
            latest = (
                conn.execute(
                    _session_query(user_id).order_by(sessions.c.started_at.desc()).limit(1)
                )
                .mappings()
                .one_or_none()
            )
            newest_closed = conn.execute(
                select(sessions.c.id)
                .where(sessions.c.user_id == user_id, sessions.c.status == "closed")
                .order_by(sessions.c.ended_at.desc().nulls_last())
                .limit(1)
            ).scalar_one_or_none()
            streak = current_streak(uow, now, _zone(tz))
            reviews_due = uow.glossary.count_due(now)
            provisional = uow.glossary.count_provisional()
            closed = [m for m in week if m["status"] == "closed"]
            offered: list[str] = []
            used: set[str] = set()
            for m in closed:
                offered += [c for c in m["chunks_offered"] if c not in offered]
                used |= _valid_used(m["raw_evidence"], m["chunks_offered"])
            texts = _chunk_texts(conn, offered)
        today_item = None
        if next_item is not None:
            item = track[next_item.track_item_id]
            today_item = dash.TodayItem(
                title=_title(item, next_item.track_item_id, lang),
                scenario_hint=item["scenario_hint"],
                skill=dash.Skill(item["skill"]),
                domain=item["domain"],
            )
        return dash.HomeData(
            has_connected=latest is not None,
            has_plan=plan is not None,
            today=today_item,
            week_start=start,
            planned_days=_planned_days(closed, track, lang, tz, today, next_item),
            session_marks=tuple(
                dash.SessionMark(
                    local_today(m["started_at"], tz),
                    m["plan_item_id"] is not None,
                    dash.SessionStatus(m["status"]),
                )
                for m in week
            ),
            streak=streak,
            stamps=tuple(dash.Stamp(texts.get(c, c), c in used) for c in offered),
            last_session=_summary(latest, track, lang) if latest is not None else None,
            reviews_due=reviews_due,
            provisional_items=provisional,
            latest_report=None,
            newest_closed_session_id=newest_closed,
            last_celebrated_session_id=me["last_celebrated_session_id"],
        )

    def usage(self, user_id: UUID, today: date) -> dash.FreeUsage:
        """Real counts, no caps (ruling: Free counters, Task 23)."""
        start = week_start(today)
        with scoped_connection(self._engine, user_id=user_id) as conn:
            tz = str(_me(conn, user_id)["timezone"])
            started = conn.execute(
                select(func.count())
                .select_from(sessions)
                .where(
                    sessions.c.user_id == user_id,
                    sessions.c.started_at >= _midnight(start, tz),
                )
            ).scalar_one()
            items = conn.execute(
                select(func.count())
                .select_from(glossary_items)
                .where(glossary_items.c.user_id == user_id, glossary_items.c.status != "declined")
            ).scalar_one()
        return dash.FreeUsage(int(started), UNCAPPED, int(items), UNCAPPED, start + timedelta(7))

    def plan(self, user_id: UUID) -> dash.PlanPage | None:
        raise NotImplementedError("v0 has no Plan page (plan ruling 11); Perfil shows plan-lite")

    def progress(self, user_id: UUID) -> dash.ProgressData:
        raise NotImplementedError("v0 has no Progreso page (spec 3.2)")

    def sessions(
        self, user_id: UUID, f: dash.SessionFilter, page: int, per_page: int
    ) -> dash.SessionPage:
        query = _session_query(user_id)
        if f.mode is not None:
            query = query.where(sessions.c.mode == f.mode.value)
        if f.status is not None:
            query = query.where(sessions.c.status == f.status.value)
        query = (
            query.order_by(sessions.c.started_at.desc(), sessions.c.id)
            .offset((page - 1) * per_page)
            .limit(per_page + 1)
        )
        with scoped_connection(self._engine, user_id=user_id) as conn:
            lang = str(_me(conn, user_id)["lang"])
            track = _track(conn)
            rows = conn.execute(query).mappings().all()
        items = tuple(_summary(m, track, lang) for m in rows[:per_page])
        return dash.SessionPage(items, page, len(rows) > per_page)

    def session_detail(self, user_id: UUID, session_id: UUID) -> dash.SessionDetail | None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            m = (
                conn.execute(_session_query(user_id).where(sessions.c.id == session_id))
                .mappings()
                .one_or_none()
            )
            if m is None:
                return None
            lang = str(_me(conn, user_id)["lang"])
            track = _track(conn)
            errors = conn.execute(
                select(
                    session_errors.c.said,
                    session_errors.c.correct,
                    session_errors.c.category,
                    session_errors.c.turn_index,
                )
                .where(
                    session_errors.c.session_id == session_id,
                    session_errors.c.user_id == user_id,
                )
                .order_by(session_errors.c.turn_index, session_errors.c.said)
            ).all()
            offered = [str(c) for c in m["chunks_offered"]]
            texts = _chunk_texts(conn, offered)
        raw: Mapping[str, Any] = m["raw_evidence"] or {}
        turns = tuple(t for t in (raw.get("user_turns") or ()) if isinstance(t, str))
        used = _valid_used(raw, offered)
        cefr = None
        if m["cefr_estimate_speaking"] is not None:
            estimate = raw.get("cefr_estimate") or {}
            evidence = tuple(str(e) for e in (estimate.get("evidence") or ()))
            confidence = dash.Confidence(m["cefr_confidence"] or "low")
            cefr = dash.CefrOpinion(m["cefr_estimate_speaking"], confidence, evidence)
        return dash.SessionDetail(
            summary=_summary(m, track, lang),
            errors=tuple(
                dash.ErrorView(
                    said=e.said,
                    correct=e.correct,
                    category=dash.ErrorCategory(e.category),
                    taken_up=find_turn(e.correct, turns, after=e.turn_index) is not None,
                )
                for e in errors
            ),
            hints_given=m["hints_given"] or 0,
            chunks_offered=tuple(texts.get(c, c) for c in offered),
            chunks_used=tuple(texts.get(c, c) for c in offered if c in used),
            cefr=cefr,
            confidence_1_5=m["confidence_1_5"],
            user_turns=turns,
        )

    def glossary(
        self, user_id: UUID, f: dash.GlossaryFilter, today: date
    ) -> tuple[dash.GlossaryRow, ...]:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            tz = str(_me(conn, user_id)["timezone"])
            rows = conn.execute(_glossary_query(user_id)).mappings().all()
        return filter_glossary([_glossary_row(m, tz) for m in rows], f, today)

    def glossary_domains(self, user_id: UUID) -> tuple[str, ...]:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            domains = conn.execute(
                select(glossary_items.c.domain)
                .where(glossary_items.c.user_id == user_id, glossary_items.c.status != "declined")
                .distinct()
                .order_by(glossary_items.c.domain)
            ).scalars()
            return tuple(str(d) for d in domains)

    def reports(self, user_id: UUID) -> dash.ReportsPage:
        raise NotImplementedError("v0 has no Reportes page (spec 3.2)")

    def account(self, user_id: UUID) -> dash.AccountData:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            me = _me(conn, user_id)
            p = (
                conn.execute(select(profiles).where(profiles.c.user_id == user_id))
                .mappings()
                .one_or_none()
            )
        user = _user(me)
        profile = None
        if p is not None:
            profile = dash.Profile(
                domains=tuple(p["domains"]),
                minutes_per_day=p["minutes_per_day"],
                days_per_week=p["days_per_week"],
                target_level=p["target_level"],
                target_date=p["target_date"],
            )
        return dash.AccountData(
            user=user,
            profile=profile,
            prefs=dash.Preferences(
                user.lang, user.reduce_motion, me["email_weekly"], me["email_reminders"]
            ),
            clients=(),
            subscription=FREE,
            install_prompt_dismissed=me["install_prompt_dismissed_at"] is not None,
        )

    def has_any_session(self, user_id: UUID) -> bool:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            found = conn.execute(
                select(sessions.c.id).where(sessions.c.user_id == user_id).limit(1)
            ).scalar_one_or_none()
        return found is not None

    # --- GlossaryEditor ----------------------------------------------------

    def update_glossary_text(
        self, user_id: UUID, item_id: UUID, meaning: str, context_sentence: str
    ) -> dash.GlossaryRow | None:
        now = self._clock()
        with scoped_connection(self._engine, user_id=user_id) as conn:
            changed = conn.execute(
                update(glossary_items)
                .where(
                    glossary_items.c.id == item_id,
                    glossary_items.c.user_id == user_id,
                    glossary_items.c.status != "declined",
                )
                .values(meaning=meaning, context_sentence=context_sentence, updated_at=now)
                .returning(glossary_items.c.id)
            ).scalar_one_or_none()
            if changed is None:
                return None
            tz = str(_me(conn, user_id)["timezone"])
            row = (
                conn.execute(_glossary_query(user_id).where(glossary_items.c.id == item_id))
                .mappings()
                .one()
            )
        return _glossary_row(row, tz)

    # --- AccountService ----------------------------------------------------

    def set_preferences(self, user_id: UUID, prefs: dash.Preferences) -> None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            conn.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(
                    lang=prefs.lang.value,
                    reduce_motion=prefs.reduce_motion,
                    email_weekly=prefs.email_weekly,
                    email_reminders=prefs.email_reminders,
                )
            )

    def revoke_client(self, user_id: UUID, client_id: str) -> bool:
        raise NotImplementedError("v0 lists no MCP clients (plan ruling 3)")

    def export_data(self, user_id: UUID) -> dict[str, Any]:
        raise NotImplementedError("v0: export is by request to the author (spec 3.2)")

    def request_deletion(self, user_id: UUID, now: datetime) -> None:
        raise NotImplementedError("v0: deletion is by request to the author (spec 13)")

    def dismiss_install_prompt(self, user_id: UUID, now: datetime) -> None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            conn.execute(
                update(users)
                .where(users.c.id == user_id, users.c.install_prompt_dismissed_at.is_(None))
                .values(install_prompt_dismissed_at=now)
            )

    def mark_celebrated(self, user_id: UUID, session_id: UUID) -> None:
        with scoped_connection(self._engine, user_id=user_id) as conn:
            conn.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(last_celebrated_session_id=session_id)
            )


class V0Subscriptions:
    """SubscriptionStore for v0: everyone is Free and nothing is billed (spec 12)."""

    def subscription(self, user_id: UUID) -> dash.Subscription:
        return FREE

    def customer_id(self, user_id: UUID) -> str | None:
        return None

    def user_for_customer(self, customer_id: str) -> UUID | None:
        return None

    def link_customer(self, user_id: UUID, customer_id: str) -> None:
        raise NotImplementedError("v0 has no billing (spec 3.2)")

    def apply_event(self, user_id: UUID, event: dash.BillingEvent) -> bool:
        raise NotImplementedError("v0 has no billing (spec 3.2)")


class BillingDisabled(RuntimeError):
    """Raised by every DisabledBilling method; no v0 route reaches billing."""


class DisabledBilling:
    """BillingGateway for v0: raises if anything calls it (spec 12)."""

    def checkout_url(
        self,
        *,
        user_id: UUID,
        customer_id: str | None,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        raise BillingDisabled("checkout")

    def portal_url(self, *, customer_id: str, return_url: str) -> str:
        raise BillingDisabled("portal")

    def parse_event(self, payload: bytes, signature: str) -> dash.BillingEvent | None:
        raise BillingDisabled("webhook")


class V0Settings:
    """SettingsStore for v0: no admin settings exist, so the defaults are the empty set."""

    def list_settings(self) -> tuple[dash.Setting, ...]:
        return ()

    def update_setting(
        self, key: str, value: str, actor_id: UUID, now: datetime
    ) -> dash.Setting | None:
        return None


def pg_web_deps(engine: Engine, settings: Settings, config: WebConfig) -> WebDeps:
    """Production wiring of the dashboard ports for v0."""
    if config.test_login:
        raise ValueError("test login needs the in-memory backend; never wire it to Postgres")
    backend = PgWebBackend(engine, _utc_now)
    return WebDeps(
        users=backend,
        sessions=backend,
        reader=backend,
        glossary=backend,
        account=backend,
        settings=V0Settings(),
        subscriptions=V0Subscriptions(),
        billing=DisabledBilling(),
        google=GoogleOidcLogin(settings.google_client_id, settings.google_client_secret),
        clock=_utc_now,
    )
```

- [ ] **Step 3c: Wire the website and the request log into the product app**

In `src/tutor/app.py`, extend the imports with:

```python
import logging
import os
import re
import time
from uuid import UUID

from tutor.mcp.observe import user_hash
from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.pg import pg_web_deps
```

add after `class PathDispatch` (before `_utc_now`):

```python
_UUID_SEGMENT = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=/|$)"
)
_request_log = logging.getLogger("tutor.http")


def log_path(path: str) -> str:
    """The path with ids replaced; query strings (OAuth codes) never reach the log."""
    return _UUID_SEGMENT.sub("/:id", path)[:200]


class RequestLog:
    """One JSON line per HTTP request (spec 13): method, path without query or ids, status,
    latency, and the learner's hash when the web session knows the user. Never bodies, tokens,
    emails or learner text."""

    def __init__(self, app: ASGIApp, clock: Callable[[], float] = time.perf_counter) -> None:
        self.app = app
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = self._clock()
        status = 500

        async def send_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        finally:
            user = scope.get("state", {}).get("user")
            uid = getattr(user, "id", None)
            record = {
                "kind": "http",
                "method": scope.get("method", ""),
                "path": log_path(scope.get("path", "")),
                "status": status,
                "ms": round((self._clock() - started) * 1000, 1),
                "user_hash": user_hash(uid) if isinstance(uid, UUID) else None,
            }
            _request_log.info(json.dumps(record))
```

add, above `build_app`:

```python
def web_config_from_env(environ: Mapping[str, str]) -> WebConfig:
    """WebConfig.from_env, but a missing key exits naming the key (never a value)."""
    try:
        return WebConfig.from_env(environ)
    except KeyError as missing:
        raise SystemExit(f"Missing settings: {missing.args[0]}") from None
```

(import `Mapping` from `collections.abc` if `tutor/app.py` does not already), and append to `tests/unit/web/test_web_v0.py`:

```python
def test_missing_web_setting_exits_naming_the_key() -> None:
    from tutor.app import web_config_from_env

    env = {
        "TUTOR_ENV": "test",
        "TUTOR_BASE_URL": "https://t.example",
        "TUTOR_MCP_URL": "https://t.example/mcp",
    }
    with pytest.raises(SystemExit, match="Missing settings: TUTOR_SUPPORT_EMAIL"):
        web_config_from_env(env)
```

Then replace `build_app` with:

```python
def build_app(
    settings: Settings, *, engine: Engine | None = None, web_config: WebConfig | None = None
) -> PathDispatch:
    """MCP + OAuth proxy and the website; both behind the 64 KB guard and the request log."""
    engine = engine if engine is not None else make_engine(settings.database_url)
    config = web_config if web_config is not None else web_config_from_env(os.environ)
    svc = Services(
        uow=pg_uow_factory(engine),
        clock=_utc_now,
        valid_timezones=frozenset(zoneinfo.available_timezones()),
    )
    mcp = build_mcp(svc, PgIdentity(engine), auth=build_google_provider(settings))
    web = create_app(pg_web_deps(engine, settings, config), config)
    return PathDispatch(
        RequestLog(BodySizeGuard(mcp.http_app(path=MCP_PATH))),
        web_app=RequestLog(BodySizeGuard(web)),
    )
```

The dashboard app defines no lifespan or startup handler (dashboard Tasks 10–15), so `PathDispatch` keeps sending the lifespan to the MCP app only; the Step 1a HTTP test proves `/login` renders through the dispatcher with only that lifespan run.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/web/test_web_v0.py tests/unit/app -q`
Expected: PASS (8 tests in `test_web_v0.py`, 4 in `test_app_request_log.py`, the rest of `tests/unit/app` unchanged).

Run: `uv run just test-int`
Expected: PASS, including 18 tests in `tests/integration/test_pg_web.py`.

- [ ] **Step 5: Run the gate, review and commit**

Run: `uv run just fmt` then `uv run just check`
Expected: PASS. Dispatch `security-reviewer` on the diff (`src/tutor/web/pg.py`, `src/tutor/app.py`) and fix every BLOCKER and MAJOR before committing.

```bash
git add src/tutor/web/pg.py src/tutor/web/ports.py src/tutor/web/views.py src/tutor/app.py tests/web_pg_support.py tests/unit/web/test_web_v0.py tests/unit/app/test_app_request_log.py tests/unit/app/test_app_http.py tests/integration/test_pg_web.py
git commit -m "feat(web): Postgres adapters for the dashboard ports and the website in the product app" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 24: Dashboard pages (runs dashboard plan Part 2 Tasks 20, 21, 22, 23 and 29)

The controller runs `superpowers:subagent-driven-development` on `docs/superpowers/plans/2026-10-04-dashboard-web-2-pages.md`, Tasks 20 (Conectar), 21 (Inicio), 22 (Sesiones), 23 (Glosario) and 29 (route sweep), in that order, on this branch. They are executed as written except for the rulings below (pasted into every dispatch). Dashboard Tasks 16–19, 24–28 and 30 are not run. Links and copy that point at postponed pages are left as written here and fixed in core Task 25, which owns the v0 layout.

**Files:** exactly the files of dashboard Part 2 Tasks 20–23 and 29 (`src/tutor/web/query.py`, `src/tutor/web/routes/{connect,home,sessions,glossary}.py`, their templates and partials, `macros/labels.html`, `routes/__init__.py`, `messages.po`, `app.css`, `tests/unit/web/test_web_{query,connect,home,sessions_pages,glossary,sweep}.py`).

**Interfaces:**
- Consumes: everything core Task 22 produced; `DashboardReader.home/usage/sessions/session_detail/glossary/glossary_domains/has_any_session/account`, `GlossaryEditor.update_glossary_text`, `AccountService.mark_celebrated` (implemented on Postgres in core Task 23).
- Produces: routes `GET /app/connect`, `GET /app/connect/status`, `GET /app/`, `GET /app/sessions`, `GET /app/sessions/{session_id}`, `GET /app/glossary`, `GET /app/glossary.csv`, `GET /app/glossary/{item_id}/edit`, `GET /app/glossary/{item_id}/row`, `POST /app/glossary/{item_id}`; `tutor.web.query` (`enum_or_none`, `bounded_int`, `parse_uuid`); `tutor.web.routes.connect.connect_context`; `tutor.web.routes.home.trail_view`; `tutor.web.routes.sessions.page_url`; `tutor.web.routes.glossary.glossary_filter`, `filter_query`; `macros/labels.html`; `tests/unit/web/test_web_sweep.py` with the v0 constants below (core Task 25 extends them).

- [ ] **Step 1: Run the v0 checklist (replaces Part 2 "Before Task 1 (on 2027-01-05; for this part: before Task 16, re-check the Stripe rows)")**

1. Core Task 23 is committed and `uv run just check` passes.
2. No Stripe, OXXO or axe row is re-checked: dashboard Tasks 16–19 and 30 are not run. The HTMX rows were checked in core Task 22.
3. Ledger: "core Task 24 = dashboard Part 2 Tasks 20, 21, 22, 23, 29; rulings P1–P10".

- [ ] **Step 2: Apply these rulings to the dashboard tasks (paste them into every dispatch)**

- **P1. Same execution rules as core Task 22:** V1 (numbering), V3 (each task ends with `uv run just fmt` then `uv run just check`; every commit carries the `Co-Authored-By` trailer), V5 (no pyproject test-config edits), V6 (test layout and unique basenames), V12 (no `test-e2e`), V16 (pip-audit), V17 (check-fast budget).
- **P2. Cross-references to tasks that do not run.** Dashboard Task 21's interface note ("Tasks 22–24 append more macros; Task 25 (Progreso) and Task 27 (Reportes) should use `labels.kpi`") refers to dashboard tasks; in v0 only dashboard Tasks 22 and 23 append macros. Dashboard Task 29 says it consumes "every router registered in `all_routers` (Tasks 10–28)": in v0 that is dashboard Tasks 10–14 and 20–23 (plus `/app/profile` after core Task 25). Its parameter list names `{client_id}` (dashboard Task 26) and `{key}` (dashboard Task 28): no such route exists in v0; keep `_other_users_ids` and `NOT_USER_OWNED` as written (the demo seed still has Beto's clients, so the helper's assertion holds).
- **P3. Dashboard Task 21 (Inicio) as written.** Its `home.html` links "Ver plan" to `/app/plan`, links `/app/reports` inside the Annual-only weekly report card, and its no-plan card says "Haz tu diagnóstico en el chat". Write them as the dashboard plan says so its tests pass; core Task 25 replaces those three spots and updates `test_connected_without_plan_asks_for_the_diagnostic`. The Annual-only card never renders in v0 (core Task 23: everyone is Free).
- **P4. Dashboard Task 23 (Glosario) as written.** Its "N de 50 elementos" counter and the `test_page_lists_items_labels_and_free_counter` / `test_annual_has_no_counter` tests run on the memory backend's caps. In production `render` hides the counter because v0 caps are `UNCAPPED` (core Task 23). No change.
- **P5. Dashboard Task 20 (Conectar) as written.** "Connected" is `has_any_session` (plan ruling 3). `TUTOR_MCP_URL` feeds `mcp_url` (core Task 27 env).
- **P6. Dashboard Task 29 constants.** In `tests/unit/web/test_web_sweep.py`, write these constants instead of the dashboard's `PUBLIC`, `PROTECTED_PREFIXES` and `CSRF_EXEMPT`:

  ```python
  PUBLIC = {
      "/",
      "/login",
      "/privacy",
      "/terms",
      "/offline",
      "/manifest.webmanifest",
      "/sw.js",
      "/auth/google",
      "/auth/callback",
      "/auth/logout",
      "/auth/test-login",
  }
  PROTECTED_PREFIXES = ("/app",)
  CSRF_EXEMPT = {("POST", "/auth/test-login")}
  # Core loop v0 (plan ruling 11): postponed pages are not registered at all.
  POSTPONED_PREFIXES = (
      "/pricing",
      "/webhooks",
      "/billing",
      "/admin",
      "/app/plan",
      "/app/progress",
      "/app/reports",
  )
  ```

  `/pricing` and `/webhooks/stripe` are dropped (not registered in v0). `/auth/logout` is added: it is CSRF-checked but needs no login (dashboard Task 12), sits under neither `/app` nor the dashboard's public list, and would fail `test_every_route_is_classified` as written. In that test, change the failure message to `f"classify {route.path}: list it in PUBLIC or move it under /app"`.
- **P7. Dashboard Task 29 thresholds.** v0 registers 10 protected GET routes (the 9 page routes above plus dashboard Task 12's temporary `GET /app/account`) and 4 CSRF-checked unsafe routes (`POST /auth/logout`, `POST /app/lang`, `POST /app/install/dismiss`, `POST /app/glossary/{item_id}`). Change `assert checked >= 15` to `assert checked >= 10` in `test_protected_pages_redirect_anonymous_visitors`, and `assert checked >= 8` to `assert checked >= 4` in `test_unsafe_methods_need_the_csrf_token`. `assert checked >= 4` in the isolation test stays.
- **P8. One extra sweep test.** Append to `tests/unit/web/test_web_sweep.py`:

  ```python
  def test_postponed_pages_are_not_registered(app: FastAPI) -> None:
      paths = [route.path for route in _routes(app)]
      assert [p for p in paths if p.startswith(POSTPONED_PREFIXES)] == []
  ```

  `/app/account` joins `POSTPONED_PREFIXES` in core Task 25, when its stub is deleted.
- **P9. No new adapters.** Every port method these pages call already exists on `PgWebBackend` (core Task 23). If a dashboard task needs a method or field that `PgWebBackend` lacks, stop and ledger it for a ruling rather than adding it to the memory backend only.
- **P10. Reviews.** Dispatch `security-reviewer` after dashboard Tasks 23 (inline edit, CSV) and 29 (sweep).

- [ ] **Step 3: Execute dashboard Part 2 Tasks 20, 21, 22, 23 and 29**

One implementer per dashboard task, in that order, each with the task text plus P1–P10, with the two-stage review of `superpowers:subagent-driven-development`.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/unit/web -q`
Expected: PASS, including `test_web_sweep.py` with the v0 constants and `test_postponed_pages_are_not_registered`.

Run: `uv run just fmt` then `uv run just check`
Expected: PASS.

Run: `uv run just test-int`
Expected: PASS (core Task 23's Postgres web tests still pass with the new routes registered).

- [ ] **Step 5: Record the result in the ledger**

No extra commit (each dashboard task committed). Ledger line:
`core Task 24 (dashboard Part 2 Tasks 20–23, 29): DONE at <HEAD sha>; uv run just check PASS; rulings P1–P10 applied.`

---

### Task 25: Perfil page and v0 navigation

The Perfil page (spec 6.3 and 12): the onboarding questions as a form that saves through the core `save_profile` service, and the current plan-lite. The layout is trimmed to the v0 pages (ruling 11), postponed links and copy are removed, and Entrar gets the short privacy note.

> RULING (how the route reaches core services): `WebDeps` and `memory_deps` stay unchanged. A `ProfilePort` Protocol (`view`, `save`) is installed on `app.state.profiles`. Production uses `ServicesProfiles`, which wraps the one core `Services` instance that `build_app` builds. Tests and `just dashboard-demo` use `MemoryProfiles`, which wraps core `memory_uow` over a `MemoryStore`.

> RULING (field errors): when `save_profile` raises `ServiceError("validation_failed", fields)`, the route calls the same pure `tutor.domain.profile.validate_profile` with the learner's local date, their current timezone and the IANA list, only to read each field's `ProfileErrorCode` for the es-MX message (controller ruling 2). The service decides validity; no rule is reimplemented. A field the service named but the domain did not explain gets that field's generic message.

> RULING (first login): a Google login whose user has no profile lands on `/app/profile`, whatever `next` says (spec 6.3). Later logins honour `next`.

> RULING (no Cuenta in v0): below 900 px the layout hides the language switch and logout (dashboard Task 13), and dashboard Task 26 (Cuenta) does not run. Perfil therefore carries the language switch, logout and the deletion contact. The privacy page and Entrar say that deletion is by request to the author (spec 12, 13).

> RULING (navigation): sidebar and tab bar show the same five links: Inicio, Perfil, Sesiones, Glosario, Conectar. The "Más" menu, the plan chip, the Free meter and the admin link are removed; there are no plans or admin pages in v0.

**Files:**
- Create: `src/tutor/web/profile.py`
- Create: `src/tutor/web/routes/profile.py`
- Create: `src/tutor/web/templates/pages/profile.html`
- Create: `src/tutor/web/templates/partials/profile_body.html`
- Modify: `src/tutor/web/routes/__init__.py` (register `profile.router`)
- Modify: `src/tutor/web/routes/auth.py` (first-login redirect; delete the temporary `/app/account` route)
- Modify: `src/tutor/web/templates/layouts/app.html` (v0 navigation; full replacement)
- Modify: `src/tutor/web/templates/layouts/public.html` (drop the `/pricing` link)
- Modify: `src/tutor/web/templates/pages/home.html` (three postponed spots)
- Modify: `src/tutor/web/templates/pages/login.html` (privacy note)
- Modify: `src/tutor/web/templates/pages/privacy.html` (v0 purposes and rights)
- Modify: `src/tutor/web/static/js/app.js` (browser timezone into the form)
- Modify: `src/tutor/web/static/css/app.css` (Perfil styles)
- Modify: `src/tutor/web/locale/en/LC_MESSAGES/messages.po`
- Modify: `src/tutor/web/demo_server.py` (install `MemoryProfiles`)
- Modify: `src/tutor/app.py` (install `ServicesProfiles`)
- Modify: `tests/unit/web/conftest.py` (`profiles` fixture; `csrf_of` loads `/app/connect`)
- Modify: `tests/unit/web/test_web_auth.py`, `tests/unit/web/test_web_home.py`, `tests/unit/web/test_web_sweep.py`, `tests/unit/app/test_app_http.py`
- Test: `tests/unit/web/test_web_profile.py`, `tests/unit/web/test_web_v0_nav.py`, `tests/integration/test_pg_profile_web.py`

**Interfaces:**
- Consumes: `get_profile`, `save_profile` (Task 11); `ProfileView`, `SaveProfileResult` (Task 11); `Services` (Task 10); `MemoryStore`, `UserRecord`, `memory_uow` (Task 10); `ServiceError` (Task 10); `ProfileInput`, `validate_profile`, `ONBOARDING_QUESTIONS`, `DEFAULT_TIMEZONE`, `TARGET_MIN_DAYS`, `TARGET_MAX_DAYS`, `GOAL_TEXT_MAX` (Task 2); `feasibility_text` (Task 4); `render`, `is_htmx`, `request_lang`, `today_for` (dashboard Task 10); `APP_ROUTER_DEPS`, `current_user` (dashboard Task 11); `build_app`, `RequestLog`, `BodySizeGuard` (core Tasks 21, 23); `tests/web_pg_support.py` (core Task 23).
- Produces: `tutor.web.profile`: `PROFILE_PATH = "/app/profile"`, `class ProfilePort(Protocol)` with `view(user_id: UUID) -> ProfileView` and `save(user_id: UUID, raw: ProfileInput) -> SaveProfileResult` (raises `ServiceError`), `ServicesProfiles(svc: Services)`, `MemoryProfiles(clock, store=None)` (attribute `store`), `install_profiles(app: FastAPI, port: ProfilePort) -> None`, `get_profiles(request) -> ProfilePort`, `optional_profiles(request) -> ProfilePort | None`; routes `GET /app/profile`, `POST /app/profile`; `active_nav` value `profile`; test fixture `profiles` (`MemoryProfiles`) in `tests/unit/web/conftest.py`.

- [ ] **Step 1a: Update the shared web fixtures and the dashboard tests whose v0 behaviour changes**

In `tests/unit/web/conftest.py`, add to the imports:

```python
from tutor.web.profile import MemoryProfiles, install_profiles
```

add this fixture before `app`:

```python
@pytest.fixture
def profiles(clock: FixedClock) -> MemoryProfiles:
    """Core in-memory profile services behind the Perfil port (core Task 25)."""
    return MemoryProfiles(clock)
```

replace the `app` fixture with:

```python
@pytest.fixture
def app(
    backend: MemoryBackend,
    demo: DemoUsers,
    clock: FixedClock,
    billing: FakeBilling,
    google: FakeGoogle,
    config: WebConfig,
    profiles: MemoryProfiles,
) -> FastAPI:
    application = create_app(memory_deps(backend, clock, billing=billing, google=google), config)
    install_profiles(application, profiles)
    return application
```

and in `csrf_of` replace `client.get("/app/account")` with `client.get("/app/connect")`.

In `tests/unit/web/test_web_auth.py`, add the imports:

```python
from tutor.domain.profile import ProfileInput
from tutor.web.profile import MemoryProfiles
```

add after the imports:

```python
ANA_ANSWERS = ProfileInput(
    self_level="B1",
    domains=["it"],
    use_cases=["standup"],
    minutes_per_day=20,
    days_per_week=3,
    target_level="B2",
)
```

and replace `test_google_round_trip_creates_user_and_lands_on_next` and `test_open_redirect_in_next_is_ignored` with:

```python
def test_google_round_trip_creates_user_and_lands_on_profile(
    client: TestClient, google: FakeGoogle, backend: MemoryBackend
) -> None:
    google.next_identity = GoogleIdentity("g-123", "carla@example.com", "Carla", True)
    start = client.get("/auth/google?next=/app/glossary")
    assert start.status_code == 302
    callback = client.get(start.headers["location"].replace("https://testserver", ""))
    # Core loop v0 (spec 6.3): a login without a profile goes to Perfil, whatever `next` says.
    assert callback.status_code == 303 and callback.headers["location"] == "/app/profile"
    assert any(u.display_name == "Carla" for u in backend.users.values())
    assert COOKIE in client.cookies


def test_onboarded_learner_lands_on_next(
    client: TestClient, google: FakeGoogle, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    profiles.save(demo.ana, ANA_ANSWERS)
    google.next_identity = GoogleIdentity("demo-ana", "ana@example.com", "Ana", True)
    start = client.get("/auth/google?next=/app/glossary")
    callback = client.get(start.headers["location"].replace("https://testserver", ""))
    assert callback.headers["location"] == "/app/glossary"


def test_open_redirect_in_next_is_ignored(
    client: TestClient, google: FakeGoogle, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    profiles.save(demo.ana, ANA_ANSWERS)
    google.next_identity = GoogleIdentity("demo-ana", "ana@example.com", "Ana", True)
    start = client.get("/auth/google?next=https://evil.example")
    callback = client.get(start.headers["location"].replace("https://testserver", ""))
    assert callback.headers["location"] == "/app/"
```

In `test_logout_clears_cookie_and_site_data`, replace `assert c.get("/app/account").status_code == 303` with `assert c.get("/app/connect").status_code == 303`.

In `tests/unit/web/test_web_home.py`, replace `test_connected_without_plan_asks_for_the_diagnostic` with:

```python
def test_connected_without_plan_points_to_the_profile(
    login: Login, demo: DemoUsers, backend: MemoryBackend
) -> None:
    backend.homes[demo.nuevo] = replace(empty_home(TODAY), has_connected=True)
    html = login(demo.nuevo).get("/app/").text
    assert "Responde tu perfil y armamos tu plan de práctica." in html
    assert 'href="/app/profile"' in html
    assert "diagnóstico" not in html
```

In `tests/unit/web/test_web_sweep.py`, add `"/app/account",` as the last entry of `POSTPONED_PREFIXES` (the stub is deleted below), and change `assert checked >= 4` in `test_unsafe_methods_need_the_csrf_token` to `assert checked >= 5` (`POST /app/profile` joins). The protected-GET threshold stays `>= 10` (`/app/account` leaves, `/app/profile` joins).

In `tests/unit/app/test_app_http.py`, add the imports:

```python
from fastapi import FastAPI

from tutor.app import RequestLog
from tutor.web.profile import ServicesProfiles
```

and append:

```python
def test_build_app_installs_the_profile_port(tmp_path: Path) -> None:
    app = build_app(
        settings(tmp_path), engine=sqlalchemy.create_engine("sqlite://"), web_config=WEB_CONFIG
    )
    logged = app.web_app
    assert isinstance(logged, RequestLog) and isinstance(logged.app, BodySizeGuard)
    web = logged.app.app
    assert isinstance(web, FastAPI)
    assert isinstance(web.state.profiles, ServicesProfiles)
```

- [ ] **Step 1b: Write the failing Perfil and navigation tests**

`tests/unit/web/test_web_profile.py`:

```python
"""Perfil: onboarding form, plan-lite and field errors (spec 6.3, 12; core Task 25)."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

import tutor.web
from tutor.domain.dashboard.types import Lang
from tutor.domain.plan_lite import feasibility_text
from tutor.domain.profile import ONBOARDING_QUESTIONS, ProfileInput
from tutor.web.demo import DemoUsers
from tutor.web.memory import MemoryBackend
from tutor.web.profile import MemoryProfiles

from .conftest import csrf_of

pytestmark = pytest.mark.unit

HX = {"hx-request": "true"}
Login = Callable[[UUID], TestClient]
SAVED = "Guardamos tu perfil."


def answers(token: str, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "csrf_token": token,
        "self_level": "B1",
        "domains": ["it"],
        "use_cases": ["standup", "code_review"],
        "minutes_per_day": "20",
        "days_per_week": "3",
        "target_level": "B2",
        "target_date": "",
        "goal_text": "Run the standup in English",
        "timezone": "America/Mexico_City",
    }
    data.update(changes)
    return data


def test_first_visit_shows_every_onboarding_question(login: Login, demo: DemoUsers) -> None:
    html = login(demo.nuevo).get("/app/profile").text
    for question in ONBOARDING_QUESTIONS:
        assert str(escape(question.prompt_es)) in html
    assert 'name="self_level"' in html and 'name="use_cases"' in html
    assert 'name="days_per_week"' in html and 'name="target_date"' in html
    assert "data-timezone" in html and 'name="timezone"' in html
    assert 'id="plan-title"' not in html
    assert 'href="/app/profile" aria-current="page"' in html


def test_saving_answers_builds_the_plan(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    c = login(demo.nuevo)
    response = c.post("/app/profile", data=answers(csrf_of(c)))
    assert response.status_code == 303
    assert response.headers["location"] == "/app/profile?saved=1"
    view = profiles.view(demo.nuevo)
    assert not view.onboarding_needed and view.plan is not None
    assert view.profile is not None and view.profile.use_cases == ("standup", "code_review")
    page = c.get(response.headers["location"]).text
    assert SAVED in page
    assert str(escape(feasibility_text(view.plan.feasibility, "es"))) in page
    assert f"{view.plan.weeks} semanas" in page
    assert page.count('class="plan-week__item"') == len(view.plan.week_items)


def test_htmx_save_returns_the_partial(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    response = c.post("/app/profile", data=answers(csrf_of(c)), headers=HX)
    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'id="profile-body"' in response.text and SAVED in response.text
    assert 'id="plan-title"' in response.text


def test_invalid_answers_are_422_with_a_message_per_field(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    c = login(demo.nuevo)
    bad = answers(
        csrf_of(c),
        self_level="B2",
        target_level="B1",
        use_cases=["standup", "code_review", "interview", "client_call", "demo"],
        minutes_per_day="45",
        days_per_week="9",
        target_date="2026-01-01",
    )
    response = c.post("/app/profile", data=bad, headers=HX)
    assert response.status_code == 422
    for message in (
        "Elige como máximo 4 situaciones.",
        "Elige 15, 20 o 30 minutos.",
        "Elige de 2 a 7 días.",
        "Tu meta debe ser igual o más alta que tu nivel de hoy.",
        "Elige una fecha entre 4 semanas y 1 año a partir de hoy, o déjala vacía.",
    ):
        assert message in response.text
    assert 'aria-invalid="true"' in response.text
    assert 'name="self_level" value="B2" checked' in response.text  # answers are kept
    assert profiles.view(demo.nuevo).onboarding_needed


def test_plain_invalid_post_renders_the_full_page(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    response = c.post("/app/profile", data=answers(csrf_of(c), self_level=""))
    assert response.status_code == 422
    assert "<html" in response.text and "Elige tu nivel de inglés de hoy." in response.text


def test_unknown_timezone_is_refused(login: Login, demo: DemoUsers) -> None:
    c = login(demo.nuevo)
    response = c.post("/app/profile", data=answers(csrf_of(c), timezone="Mars/Olympus"), headers=HX)
    assert response.status_code == 422
    assert "No reconocimos la zona horaria de tu navegador" in response.text


def test_browser_timezone_is_saved(login: Login, demo: DemoUsers, profiles: MemoryProfiles) -> None:
    c = login(demo.nuevo)
    c.post("/app/profile", data=answers(csrf_of(c), timezone="America/New_York"))
    view = profiles.view(demo.nuevo)
    assert view.profile is not None and view.profile.timezone == "America/New_York"


def test_form_is_prefilled_and_learner_text_stays_text(
    login: Login, demo: DemoUsers, profiles: MemoryProfiles
) -> None:
    profiles.save(
        demo.ana,
        ProfileInput(
            self_level="B1",
            domains=["it"],
            use_cases=["demo"],
            minutes_per_day=30,
            days_per_week=4,
            target_level="B2+",
            goal_text="<b>Lead</b> the demo",
        ),
    )
    html = login(demo.ana).get("/app/profile").text
    assert 'name="target_level" value="B2+" checked' in html
    assert 'name="use_cases" value="demo" checked' in html
    assert '<option value="4" selected>' in html
    assert "&lt;b&gt;Lead&lt;/b&gt; the demo" in html and "<b>Lead</b>" not in html


def test_perfil_in_english(login: Login, demo: DemoUsers, backend: MemoryBackend) -> None:
    backend.users[demo.ana] = replace(backend.users[demo.ana], lang=Lang.EN)
    html = login(demo.ana).get("/app/profile").text
    assert "Profile" in html
    assert str(escape(ONBOARDING_QUESTIONS[0].prompt_en)) in html


def test_saving_needs_the_csrf_token(login: Login, demo: DemoUsers) -> None:
    response = login(demo.nuevo).post("/app/profile", data=answers(""))
    assert response.status_code == 403


def test_perfil_offers_language_logout_and_deletion_contact(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/profile").text
    main = html.split('id="main"', 1)[1]
    assert 'action="/app/lang"' in main and 'action="/auth/logout"' in main
    assert "soporte@example.test" in main


def test_app_js_sends_the_browser_timezone() -> None:
    source = (Path(tutor.web.__file__).parent / "static" / "js" / "app.js").read_text("utf-8")
    assert "data-timezone" in source
    assert "resolvedOptions().timeZone" in source
```

`tests/unit/web/test_web_v0_nav.py`:

```python
"""v0 navigation and links: only the five v0 pages, nothing postponed (ruling 11, core Task 25)."""

import re
from collections.abc import Callable
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tutor.domain.dashboard.types import (
    PlanPage,
    ProgressData,
    ReportsPage,
    SubStatus,
    Subscription,
    Tier,
)
from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import DemoUsers, seed_demo
from tutor.web.memory import FixedClock, MemoryBackend, memory_deps
from tutor.web.ports import UNCAPPED
from tutor.web.profile import MemoryProfiles, install_profiles

from .conftest import BASE, TODAY

pytestmark = pytest.mark.unit

Login = Callable[[UUID], TestClient]
NAV = ["/app/", "/app/profile", "/app/sessions", "/app/glossary", "/app/connect"]
POSTPONED = (
    "/app/plan",
    "/app/progress",
    "/app/reports",
    "/app/account",
    "/billing",
    "/admin",
    "/pricing",
    "/webhooks",
)
LINK = re.compile(r'(?:href|action|hx-get|hx-post)="([^"]+)"')
PARAM = re.compile(r"{(\w+)(?::\w+)?}")


def test_sidebar_and_tab_bar_list_only_the_v0_pages(login: Login, demo: DemoUsers) -> None:
    html = login(demo.admin).get("/app/connect").text
    sidebar = html.split('class="sidebar__nav"', 1)[1].split("</nav>", 1)[0]
    tabbar = html.split('class="tabbar"', 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'<a class="nav-link[^"]*" href="([^"]+)"', sidebar) == NAV
    assert re.findall(r'<a class="tabbar__link[^"]*" href="([^"]+)"', tabbar) == NAV
    for gone in ("tabbar__more", "meter-link", "/admin/settings", "chip--free"):
        assert gone not in html


def test_the_current_page_is_marked_in_both_navs(login: Login, demo: DemoUsers) -> None:
    html = login(demo.ana).get("/app/connect").text
    assert html.count('href="/app/connect" aria-current="page"') == 2


def test_no_v0_page_links_to_a_postponed_page(
    login: Login, client: TestClient, demo: DemoUsers, backend: MemoryBackend
) -> None:
    for uid in (demo.ana, demo.beto, demo.nuevo, demo.admin):
        backend.subs[uid] = Subscription(Tier.FREE, SubStatus.NONE, "price_29", 2900)
    backend.session_cap = backend.glossary_cap = UNCAPPED
    session_id = backend.session_rows[demo.ana][0].summary.id
    item_id = backend.glossaries[demo.ana][0].id
    pages = [
        "/app/",
        "/app/profile",
        "/app/connect",
        "/app/sessions",
        "/app/glossary",
        f"/app/sessions/{session_id}",
        f"/app/glossary/{item_id}/edit",
    ]
    links: list[str] = []
    for uid in (demo.ana, demo.nuevo):
        c = login(uid)
        links += [link for page in pages for link in LINK.findall(c.get(page).text)]
    for page in ("/login", "/login?lang=en", "/privacy", "/terms", "/offline"):
        links += LINK.findall(client.get(page).text)
    assert [link for link in links if link.startswith(POSTPONED)] == []
    assert 'href="/app/profile">' in login(demo.ana).get("/app/").text  # "Ver plan"


class V0Backend(MemoryBackend):
    """Fails loudly if a v0 route reads a postponed page's data."""

    def plan(self, user_id: UUID) -> PlanPage | None:
        raise AssertionError("v0 routes never read the Plan page")

    def progress(self, user_id: UUID) -> ProgressData:
        raise AssertionError("v0 routes never read Progreso")

    def reports(self, user_id: UUID) -> ReportsPage:
        raise AssertionError("v0 routes never read Reportes")


def test_no_v0_route_reads_plan_progress_or_reports(
    clock: FixedClock, config: WebConfig, profiles: MemoryProfiles
) -> None:
    backend = V0Backend()
    demo = seed_demo(backend, TODAY)
    app: FastAPI = create_app(memory_deps(backend, clock), config)
    install_profiles(app, profiles)
    ids = {
        "session_id": str(backend.session_rows[demo.ana][0].summary.id),
        "item_id": str(backend.glossaries[demo.ana][0].id),
    }
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        assert c.post("/auth/test-login", data={"user_id": str(demo.ana)}).status_code == 303
        checked = 0
        for route in app.routes:
            if not isinstance(route, APIRoute) or "GET" not in route.methods:
                continue
            if not route.path.startswith("/app"):
                continue
            path = PARAM.sub(lambda m: ids[m.group(1)], route.path)
            for headers in ({}, {"hx-request": "true"}):
                assert c.get(path, headers=headers).status_code < 500, path
            checked += 1
    assert checked >= 10


def test_entrar_and_privacy_state_what_is_stored_and_how_to_delete(client: TestClient) -> None:
    login_page = client.get("/login").text
    assert "Tus lecciones pasan por el proveedor de tu asistente" in login_page
    assert "soporte@example.test" in login_page
    privacy = client.get("/privacy").text
    assert "desde Cuenta" not in privacy and "reporte semanal" not in privacy
    assert "lo hacemos a mano" in privacy
    assert "/pricing" not in client.get("/terms").text
```

`tests/integration/test_pg_profile_web.py`:

```python
"""Perfil on Postgres: the web form saves through the core service under row-level security."""

import re
import zoneinfo
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from web_pg_support import BASE, google_login, pg_web_app

from tutor.db.tables import plans, profiles, users
from tutor.services.context import Services
from tutor.services.ports import UowFactory
from tutor.web.memory import FakeGoogle
from tutor.web.ports import GoogleIdentity
from tutor.web.profile import ServicesProfiles, install_profiles

pytestmark = pytest.mark.integration

CSRF = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


def _utc_now() -> datetime:
    return datetime.now(UTC)


def test_first_login_onboards_through_perfil(engine: Engine, uow_factory: UowFactory) -> None:
    svc = Services(
        uow=uow_factory, clock=_utc_now, valid_timezones=frozenset(zoneinfo.available_timezones())
    )
    google = FakeGoogle()
    app = pg_web_app(engine, google)
    install_profiles(app, ServicesProfiles(svc))
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        identity = GoogleIdentity("g-perfil", "perfil@example.com", "Pia", True)
        assert google_login(c, google, identity, next_path="/app/glossary") == "/app/profile"
        page = c.get("/app/profile")
        assert page.status_code == 200
        token = CSRF.search(page.text)
        assert token
        saved = c.post(
            "/app/profile",
            data={
                "csrf_token": token.group(1),
                "self_level": "B1+",
                "domains": ["it"],
                "use_cases": ["incident", "standup"],
                "minutes_per_day": "15",
                "days_per_week": "5",
                "target_level": "B2",
                "target_date": "",
                "goal_text": "",
                "timezone": "America/New_York",
            },
        )
        assert saved.status_code == 303
        assert 'id="plan-title"' in c.get("/app/profile").text
    with engine.connect() as conn:
        uid, tz = conn.execute(
            select(users.c.id, users.c.timezone).where(users.c.google_sub == "g-perfil")
        ).one()
        use_cases = conn.execute(
            select(profiles.c.use_cases).where(profiles.c.user_id == uid)
        ).scalar_one()
        active = conn.execute(
            select(plans.c.version).where(plans.c.user_id == uid, plans.c.status == "active")
        ).scalar_one()
    assert (tz, sorted(use_cases), active) == ("America/New_York", ["incident", "standup"], 1)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/unit/web tests/unit/app -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'tutor.web.profile'`.

- [ ] **Step 3a: The profile port**

`src/tutor/web/profile.py`:

```python
"""How the web reaches the core profile services (core loop v0 spec 6.3; plan Task 25).

WebDeps stays as the dashboard plan defined it; the Perfil port lives on `app.state.profiles`.
"""

from __future__ import annotations

import functools
import zoneinfo
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, Request

from tutor.domain.profile import ProfileInput
from tutor.services.context import Services
from tutor.services.memory import MemoryStore, UserRecord, memory_uow
from tutor.services.profile import get_profile, save_profile
from tutor.services.views import ProfileView, SaveProfileResult

PROFILE_PATH = "/app/profile"


class ProfilePort(Protocol):
    def view(self, user_id: UUID) -> ProfileView: ...

    def save(self, user_id: UUID, raw: ProfileInput) -> SaveProfileResult:
        """Raises ServiceError("validation_failed", fields) on invalid answers."""
        ...


@functools.cache
def iana_zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


class ServicesProfiles:
    """Production: the core use cases over the Postgres unit of work."""

    def __init__(self, svc: Services) -> None:
        self._svc = svc

    def view(self, user_id: UUID) -> ProfileView:
        return get_profile(self._svc, user_id)

    def save(self, user_id: UUID, raw: ProfileInput) -> SaveProfileResult:
        return save_profile(self._svc, user_id, raw)


class MemoryProfiles:
    """Tests and `just dashboard-demo`: the core use cases over the in-memory store.

    Dashboard users are created by the web memory backend, so a user record is added to the core
    store the first time an id is seen (test adapter only; production users come from PgIdentity).
    """

    def __init__(self, clock: Callable[[], datetime], store: MemoryStore | None = None) -> None:
        self.store = store if store is not None else MemoryStore()
        self._clock = clock
        self._svc = Services(uow=memory_uow(self.store), clock=clock, valid_timezones=iana_zones())

    def _ensure_user(self, user_id: UUID) -> None:
        if user_id not in self.store.tables.users:
            self.store.tables.users[user_id] = UserRecord(
                id=user_id,
                google_sub=f"web:{user_id}",
                email=None,
                display_name=None,
                created_at=self._clock(),
            )

    def view(self, user_id: UUID) -> ProfileView:
        self._ensure_user(user_id)
        return get_profile(self._svc, user_id)

    def save(self, user_id: UUID, raw: ProfileInput) -> SaveProfileResult:
        self._ensure_user(user_id)
        return save_profile(self._svc, user_id, raw)


def install_profiles(app: FastAPI, port: ProfilePort) -> None:
    app.state.profiles = port


def optional_profiles(request: Request) -> ProfilePort | None:
    port: ProfilePort | None = getattr(request.app.state, "profiles", None)
    return port


def get_profiles(request: Request) -> ProfilePort:
    port = optional_profiles(request)
    if port is None:
        raise RuntimeError("no ProfilePort installed; call install_profiles(app, port)")
    return port
```

- [ ] **Step 3b: The route**

`src/tutor/web/routes/profile.py`:

```python
"""Perfil: the onboarding answers and the current plan-lite (core loop v0 spec 6.3 and 12)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import FormData

from tutor.domain.dashboard.types import Lang, User
from tutor.domain.plan_lite import feasibility_text
from tutor.domain.profile import (
    DEFAULT_TIMEZONE,
    GOAL_TEXT_MAX,
    ONBOARDING_QUESTIONS,
    TARGET_MAX_DAYS,
    TARGET_MIN_DAYS,
    ProfileInput,
    validate_profile,
)
from tutor.services.errors import ServiceError
from tutor.services.views import ProfileView
from tutor.web.deps import APP_ROUTER_DEPS, current_user
from tutor.web.profile import PROFILE_PATH, get_profiles, iana_zones
from tutor.web.views import is_htmx, render, request_lang, today_for

router = APIRouter(dependencies=APP_ROUTER_DEPS)
DAYS_CHOICES = tuple(range(2, 8))
QUESTIONS = {q.id: q for q in ONBOARDING_QUESTIONS}

# (field, ProfileErrorCode) -> message id; the template maps each id to an es-MX msgid.
MESSAGE_FOR: dict[tuple[str, str], str] = {
    ("self_level", "required"): "level",
    ("self_level", "invalid_choice"): "level",
    ("domains", "required"): "field",
    ("domains", "invalid_choice"): "field",
    ("use_cases", "too_few"): "use_cases_few",
    ("use_cases", "too_many"): "use_cases_many",
    ("use_cases", "invalid_choice"): "use_cases_choice",
    ("minutes_per_day", "invalid_choice"): "minutes",
    ("days_per_week", "out_of_range"): "days",
    ("target_level", "required"): "target_choice",
    ("target_level", "invalid_choice"): "target_choice",
    ("target_level", "below_current_level"): "target_below",
    ("target_date", "out_of_range"): "date",
    ("goal_text", "too_long"): "goal_long",
    ("timezone", "invalid_timezone"): "timezone",
}
FIELD_FALLBACK: dict[str, str] = {
    "self_level": "level",
    "domains": "field",
    "use_cases": "use_cases_choice",
    "minutes_per_day": "minutes",
    "days_per_week": "days",
    "target_level": "target_choice",
    "target_date": "date",
    "goal_text": "goal_long",
    "timezone": "timezone",
}


def _one(form: FormData, name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


def _many(form: FormData, name: str) -> list[str]:
    return [v for v in form.getlist(name) if isinstance(v, str)]


def _int(raw: str) -> int:
    text = raw.strip()
    return int(text) if text.isdecimal() and len(text) <= 3 else -1


def _date(raw: str) -> date | None:
    text = raw.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return date.min  # fails the 28-364 day rule, so the field shows its own message


def _form_values(form: FormData) -> dict[str, Any]:
    return {
        "self_level": _one(form, "self_level"),
        "domains": _many(form, "domains"),
        "use_cases": _many(form, "use_cases"),
        "minutes_per_day": _one(form, "minutes_per_day"),
        "days_per_week": _one(form, "days_per_week"),
        "target_level": _one(form, "target_level"),
        "target_date": _one(form, "target_date"),
        "goal_text": _one(form, "goal_text"),
        "timezone": _one(form, "timezone"),
    }


def _saved_values(view: ProfileView) -> dict[str, Any]:
    p = view.profile
    if p is None:
        return {
            "self_level": "",
            "domains": ["it"],
            "use_cases": [],
            "minutes_per_day": "",
            "days_per_week": "",
            "target_level": "",
            "target_date": "",
            "goal_text": "",
            "timezone": DEFAULT_TIMEZONE,
        }
    return {
        "self_level": p.self_level,
        "domains": list(p.domains),
        "use_cases": list(p.use_cases),
        "minutes_per_day": str(p.minutes_per_day),
        "days_per_week": str(p.days_per_week),
        "target_level": p.target_level,
        "target_date": p.target_date.isoformat() if p.target_date else "",
        "goal_text": p.goal_text or "",
        "timezone": p.timezone,
    }


def _to_input(values: Mapping[str, Any]) -> ProfileInput:
    return ProfileInput(
        self_level=values["self_level"].strip(),
        domains=list(values["domains"]),
        use_cases=list(values["use_cases"]),
        minutes_per_day=_int(values["minutes_per_day"]),
        days_per_week=_int(values["days_per_week"]),
        target_level=values["target_level"].strip(),
        target_date=_date(values["target_date"]),
        goal_text=values["goal_text"] or None,
        timezone=values["timezone"].strip() or None,
    )


def _messages(
    raw: ProfileInput, fields: tuple[str, ...], today: date, user: User
) -> dict[str, str]:
    """Message ids per field. Validity was decided by save_profile; validate_profile (the same
    domain rule) is called only to learn each field's error code (plan ruling, Task 25)."""
    checked = validate_profile(
        raw, today, valid_timezones=iana_zones(), current_timezone=user.timezone
    )
    errors: dict[str, str] = {}
    if isinstance(checked, tuple):
        for error in checked:
            errors.setdefault(error.field, MESSAGE_FOR.get((error.field, error.code), "field"))
    for name in fields:
        errors.setdefault(name, FIELD_FALLBACK.get(name, "field"))
    return errors


def _context(
    request: Request,
    view: ProfileView,
    values: Mapping[str, Any],
    errors: Mapping[str, str],
    *,
    saved: bool,
) -> dict[str, Any]:
    today = today_for(request)
    plan = view.plan
    lang = "en" if request_lang(request) is Lang.EN else "es"
    return {
        "view": view,
        "plan": plan,
        "q": QUESTIONS,
        "values": values,
        "errors": errors,
        "saved": saved,
        "days_choices": DAYS_CHOICES,
        "date_min": (today + timedelta(days=TARGET_MIN_DAYS)).isoformat(),
        "date_max": (today + timedelta(days=TARGET_MAX_DAYS)).isoformat(),
        "goal_max": GOAL_TEXT_MAX,
        "feasibility": feasibility_text(plan.feasibility, lang) if plan is not None else None,
    }


@router.get(PROFILE_PATH, response_class=HTMLResponse)
async def profile_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    view = get_profiles(request).view(user.id)
    saved = request.query_params.get("saved") == "1"
    ctx = _context(request, view, _saved_values(view), {}, saved=saved)
    template = "partials/profile_body.html" if is_htmx(request) else "pages/profile.html"
    return render(request, template, ctx)


@router.post(PROFILE_PATH)
async def profile_save(request: Request, user: User = Depends(current_user)) -> Response:
    profiles = get_profiles(request)
    values = _form_values(await request.form())
    raw = _to_input(values)
    try:
        profiles.save(user.id, raw)
    except ServiceError as exc:
        if exc.code != "validation_failed":
            raise
        errors = _messages(raw, exc.fields, today_for(request), user)
        ctx = _context(request, profiles.view(user.id), values, errors, saved=False)
        template = "partials/profile_body.html" if is_htmx(request) else "pages/profile.html"
        return render(request, template, ctx, status_code=422)
    if not is_htmx(request):
        return RedirectResponse(f"{PROFILE_PATH}?saved=1", status_code=303)
    view = profiles.view(user.id)
    ctx = _context(request, view, _saved_values(view), {}, saved=True)
    return render(request, "partials/profile_body.html", ctx)
```

In `src/tutor/web/routes/__init__.py`, add `profile` to the `from tutor.web.routes import …` line inside `all_routers` and append `profile.router` to `routers`.

In `src/tutor/web/routes/auth.py`, add the import:

```python
from tutor.web.profile import PROFILE_PATH, optional_profiles
```

replace, in `google_callback`,

```python
    target = safe_next(request.session.pop(_NEXT_KEY, None))
    request.state.web.login(user.id)
    return RedirectResponse(target, status_code=303)
```

with

```python
    target = safe_next(request.session.pop(_NEXT_KEY, None))
    profiles = optional_profiles(request)
    if profiles is not None and profiles.view(user.id).onboarding_needed:
        target = PROFILE_PATH  # core loop v0 spec 6.3: no profile yet, so Perfil first
    request.state.web.login(user.id)
    return RedirectResponse(target, status_code=303)
```

and delete the temporary route that dashboard Task 12 Step 3 added:

```python
@app_router.get("/app/account", response_class=HTMLResponse)
async def account_stub(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    return render(request, "layouts/app.html", {"active_nav": "account"})
```

- [ ] **Step 3c: Templates**

`src/tutor/web/templates/pages/profile.html`:

```html
{% extends "layouts/app.html" %}
{% set active_nav = "profile" %}
{% block title %}{{ _("Perfil") }} · English Tutor{% endblock %}
{% block content %}
<header class="page-head">
  <h1>{{ _("Perfil") }}</h1>
  {% if view.onboarding_needed %}
  <p class="muted">{{ _("Responde estas preguntas y armamos tu plan de práctica. También puedes hacerlo en el chat con tu tutor.") }}</p>
  {% endif %}
</header>
{% include "partials/profile_body.html" %}
<section class="card" aria-labelledby="account-title">
  <h2 id="account-title" class="card__title">{{ _("Tu cuenta") }}</h2>
  <form method="post" action="/app/lang" class="inline-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <input type="hidden" name="back" value="/app/profile">
    <button class="btn btn--ghost btn--small" name="lang" value="{{ 'en' if lang == 'es_MX' else 'es_MX' }}">{{ "English" if lang == "es_MX" else "Español" }}</button>
  </form>
  <form method="post" action="/auth/logout" class="inline-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="btn btn--ghost btn--small">{{ _("Cerrar sesión") }}</button>
  </form>
  <p class="muted">{% trans email=config.support_email %}Para borrar tu cuenta y tus datos, escríbenos a {{ email }}.{% endtrans %}</p>
</section>
{% endblock %}
```

`src/tutor/web/templates/partials/profile_body.html`:

```html
{# Perfil body: the onboarding form and the plan-lite. The page includes it; HTMX swaps it. #}
{% set es = lang == "es_MX" %}
{% set messages = {
  "level": _("Elige tu nivel de inglés de hoy."),
  "field": _("Elige tu área."),
  "use_cases_few": _("Elige al menos 1 situación."),
  "use_cases_many": _("Elige como máximo 4 situaciones."),
  "use_cases_choice": _("Elige situaciones de la lista."),
  "minutes": _("Elige 15, 20 o 30 minutos."),
  "days": _("Elige de 2 a 7 días."),
  "target_choice": _("Elige el nivel que quieres alcanzar."),
  "target_below": _("Tu meta debe ser igual o más alta que tu nivel de hoy."),
  "date": _("Elige una fecha entre 4 semanas y 1 año a partir de hoy, o déjala vacía."),
  "goal_long": _("Máximo 300 caracteres."),
  "timezone": _("No reconocimos la zona horaria de tu navegador. Recarga la página e intenta de nuevo."),
} %}
<div id="profile-body" class="grid">
  {% if saved %}<p class="banner banner--info card--wide" role="status">{{ _("Guardamos tu perfil.") }}</p>{% endif %}
  <section class="card card--wide" aria-labelledby="answers-title">
    <h2 id="answers-title" class="card__title">{{ _("Tus respuestas") }}</h2>
    <form id="profile-form" class="profile-form" method="post" action="/app/profile" hx-post="/app/profile" hx-target="#profile-body" hx-swap="outerHTML">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <input type="hidden" name="timezone" value="{{ values.timezone }}" data-timezone>

      {% set question = q.level %}
      <fieldset{% if "self_level" in errors %} aria-describedby="err-self_level"{% endif %}>
        <legend>{{ question.prompt_es if es else question.prompt_en }}</legend>
        {% for o in question.options %}
        <label class="choice"><input type="radio" name="self_level" value="{{ o.value }}"{% if values.self_level == o.value %} checked{% endif %}{% if "self_level" in errors %} aria-invalid="true"{% endif %}> <span>{{ o.label_es if es else o.label_en }}</span> <span class="choice__hint muted">{{ o.description_es if es else o.description_en }}</span></label>
        {% endfor %}
        {% if "self_level" in errors %}<p class="field-error" id="err-self_level">{{ messages[errors.self_level] }}</p>{% endif %}
      </fieldset>

      {% set question = q.field %}
      <fieldset{% if "domains" in errors %} aria-describedby="err-domains"{% endif %}>
        <legend>{{ question.prompt_es if es else question.prompt_en }}</legend>
        {% for o in question.options %}
        <label class="choice"><input type="checkbox" name="domains" value="{{ o.value }}"{% if o.value in values.domains %} checked{% endif %}{% if "domains" in errors %} aria-invalid="true"{% endif %}> <span>{{ o.label_es if es else o.label_en }}</span> <span class="choice__hint muted">{{ o.description_es if es else o.description_en }}</span></label>
        {% endfor %}
        {% if "domains" in errors %}<p class="field-error" id="err-domains">{{ messages[errors.domains] }}</p>{% endif %}
      </fieldset>

      {% set question = q.use_cases %}
      <fieldset{% if "use_cases" in errors %} aria-describedby="err-use_cases"{% endif %}>
        <legend>{{ question.prompt_es if es else question.prompt_en }}</legend>
        {% for o in question.options %}
        <label class="choice"><input type="checkbox" name="use_cases" value="{{ o.value }}"{% if o.value in values.use_cases %} checked{% endif %}{% if "use_cases" in errors %} aria-invalid="true"{% endif %}> <span>{{ o.label_es if es else o.label_en }}</span> <span class="choice__hint muted">{{ o.description_es if es else o.description_en }}</span></label>
        {% endfor %}
        {% if "use_cases" in errors %}<p class="field-error" id="err-use_cases">{{ messages[errors.use_cases] }}</p>{% endif %}
      </fieldset>

      {% set question = q.time %}
      <fieldset{% if "minutes_per_day" in errors %} aria-describedby="err-minutes_per_day"{% endif %}>
        <legend>{{ question.prompt_es if es else question.prompt_en }}</legend>
        {% for o in question.options %}
        <label class="choice"><input type="radio" name="minutes_per_day" value="{{ o.value }}"{% if values.minutes_per_day == o.value %} checked{% endif %}{% if "minutes_per_day" in errors %} aria-invalid="true"{% endif %}> <span>{{ o.label_es if es else o.label_en }}</span> <span class="choice__hint muted">{{ o.description_es if es else o.description_en }}</span></label>
        {% endfor %}
        {% if "minutes_per_day" in errors %}<p class="field-error" id="err-minutes_per_day">{{ messages[errors.minutes_per_day] }}</p>{% endif %}
      </fieldset>
      <label class="form-row">{{ _("Días a la semana") }}
        <select name="days_per_week"{% if "days_per_week" in errors %} aria-invalid="true" aria-describedby="err-days_per_week"{% endif %}>
          <option value=""{% if not values.days_per_week %} selected{% endif %}>—</option>
          {% for n in days_choices %}<option value="{{ n }}"{% if values.days_per_week == n|string %} selected{% endif %}>{{ n }}</option>{% endfor %}
        </select>
      </label>
      {% if "days_per_week" in errors %}<p class="field-error" id="err-days_per_week">{{ messages[errors.days_per_week] }}</p>{% endif %}

      {% set question = q.goal %}
      <fieldset{% if "target_level" in errors %} aria-describedby="err-target_level"{% endif %}>
        <legend>{{ question.prompt_es if es else question.prompt_en }}</legend>
        {% for o in question.options %}
        <label class="choice"><input type="radio" name="target_level" value="{{ o.value }}"{% if values.target_level == o.value %} checked{% endif %}{% if "target_level" in errors %} aria-invalid="true"{% endif %}> <span>{{ o.label_es if es else o.label_en }}</span></label>
        {% endfor %}
        {% if "target_level" in errors %}<p class="field-error" id="err-target_level">{{ messages[errors.target_level] }}</p>{% endif %}
      </fieldset>
      <label class="form-row">{{ _("Fecha meta (opcional)") }}
        <input type="date" name="target_date" value="{{ values.target_date }}" min="{{ date_min }}" max="{{ date_max }}"{% if "target_date" in errors %} aria-invalid="true" aria-describedby="err-target_date"{% endif %}>
      </label>
      {% if "target_date" in errors %}<p class="field-error" id="err-target_date">{{ messages[errors.target_date] }}</p>{% endif %}
      <label class="form-row">{{ _("Para qué lo quieres, en una frase (opcional)") }}
        <textarea name="goal_text" rows="2" maxlength="{{ goal_max }}"{% if "goal_text" in errors %} aria-invalid="true" aria-describedby="err-goal_text"{% endif %}>{{ values.goal_text }}</textarea>
      </label>
      {% if "goal_text" in errors %}<p class="field-error" id="err-goal_text">{{ messages[errors.goal_text] }}</p>{% endif %}
      {% if "timezone" in errors %}<p class="field-error" id="err-timezone">{{ messages[errors.timezone] }}</p>{% endif %}

      <button class="btn btn--primary">{{ _("Guardar perfil") }}</button>
    </form>
  </section>

  {% if plan %}
  <section class="card card--wide" aria-labelledby="plan-title">
    <h2 id="plan-title" class="card__title">{{ _("Tu plan") }}</h2>
    <p>{% trans weeks=plan.weeks, planned=plan.sessions_planned, done=plan.sessions_done %}{{ weeks }} semanas · {{ planned }} sesiones planeadas · {{ done }} hechas{% endtrans %}</p>
    <p class="muted">{{ feasibility }}</p>
    <h3>{% trans n=plan.current_week_no %}Semana {{ n }}{% endtrans %}</h3>
    <ol class="plan-week">
      {% for item in plan.week_items %}
      <li class="plan-week__item">
        <span>{{ item.can_do_es if es else item.can_do_en }}</span>
        <span class="muted">{{ _("Hablar") if item.skill == "speaking" else _("Escribir") }}</span>
        {% if item.status == "done" %}<span class="chip chip--ok">{{ _("Hecha") }}</span>{% elif item.status == "skipped" %}<span class="chip chip--muted">{{ _("Saltada") }}</span>{% else %}<span class="chip chip--muted">{{ _("Pendiente") }}</span>{% endif %}
      </li>
      {% endfor %}
    </ol>
  </section>
  {% endif %}
</div>
```

The radio and checkbox inputs keep the attribute order `type`, `name`, `value`, `checked`; the tests match on `name="…" value="…" checked`.

`src/tutor/web/templates/layouts/app.html` (full replacement):

```html
{% extends "base.html" %}
{# Core loop v0 navigation (plan ruling 11): the five v0 pages; no plan chip, meter or admin link. #}
{% set nav = [
  ("home", "/app/", _("Inicio"), "home"),
  ("profile", "/app/profile", _("Perfil"), "account"),
  ("sessions", "/app/sessions", _("Sesiones"), "sessions"),
  ("glossary", "/app/glossary", _("Glosario"), "glossary"),
  ("connect", "/app/connect", _("Conectar"), "connect"),
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
    </nav>
    <div class="sidebar__foot">
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
    {% for key, href, label, icon in nav %}
    <a class="tabbar__link{% if active_nav == key %} tabbar__link--active{% endif %}" href="{{ href }}"{% if active_nav == key %} aria-current="page"{% endif %}>
      <svg class="icon" aria-hidden="true"><use href="{{ asset('icons/sprite.svg') }}#{{ icon }}"></use></svg><span>{{ label }}</span>
    </a>
    {% endfor %}
  </nav>
</div>
{% endblock %}
```

The icons `account` and `connect` already exist in the dashboard sprite (dashboard Task 13).

In `src/tutor/web/templates/layouts/public.html`, delete the line:

```html
  <a href="{{ public_url('/pricing') }}">{{ _("Precios") }}</a>
```

In `src/tutor/web/templates/pages/home.html`, replace:

```html
    <p>{% trans %}Haz tu diagnóstico en el chat: di <code lang="en">start my lesson</code>.{% endtrans %}</p>
    <a class="btn btn--primary" href="https://claude.ai" target="_blank" rel="noopener noreferrer">{{ _("Abrir Claude") }}</a>
```

with

```html
    <p>{{ _("Responde tu perfil y armamos tu plan de práctica.") }}</p>
    <a class="btn btn--primary" href="/app/profile">{{ _("Ir a tu perfil") }}</a>
```

replace `      <a href="/app/plan">{{ _("Ver plan") }}</a>` with `      <a href="/app/profile">{{ _("Ver plan") }}</a>`, and delete the line `    <a href="/app/reports">{{ _("Ver reportes") }}</a>`.

In `src/tutor/web/templates/pages/login.html`, insert before the paragraph that starts `<p class="muted">{% trans privacy=public_url('/privacy')`:

```html
  <p class="muted">{% trans email=config.support_email %}Guardamos tu perfil, tu plan, lo que dices en cada lección (como lo reporta tu asistente), tus errores y tu glosario; nunca audio. Tus lecciones pasan por el proveedor de tu asistente, por ejemplo Claude. Para borrar tus datos, escribe a {{ email }}.{% endtrans %}</p>
```

In `src/tutor/web/templates/pages/privacy.html`, replace:

```html
  <p>{{ _("Para continuar cada sesión donde quedó la anterior, calcular tu progreso y enviarte el reporte semanal si lo activas.") }}</p>
```

with

```html
  <p>{{ _("Para continuar cada sesión donde quedó la anterior y calcular tu progreso.") }}</p>
```

and replace:

```html
  <p>{{ _("Puedes descargar todos tus datos o eliminar tu cuenta desde Cuenta. La eliminación se completa en 24 horas.") }}</p>
```

with

```html
  <p>{% trans email=config.support_email %}En esta versión de prueba pides una copia de tus datos o el borrado de tu cuenta escribiendo a {{ email }}; lo hacemos a mano y te confirmamos por correo.{% endtrans %}</p>
```

- [ ] **Step 3d: Translations, script and styles**

In `src/tutor/web/locale/en/LC_MESSAGES/messages.po`, delete the four entries whose msgids are no longer used: `"Haz tu diagnóstico en el chat: di <code lang=\"en\">start my lesson</code>."`, `"Ver reportes"`, `"Para continuar cada sesión donde quedó la anterior, calcular tu progreso y enviarte el reporte semanal si lo activas."` and `"Puedes descargar todos tus datos o eliminar tu cuenta desde Cuenta. La eliminación se completa en 24 horas."` ("Precios" stays: dashboard Task 10's other templates may still use it; an unused entry is harmless). Then append:

```po
msgid "Perfil"
msgstr "Profile"

msgid "Responde estas preguntas y armamos tu plan de práctica. También puedes hacerlo en el chat con tu tutor."
msgstr "Answer these questions and we'll build your practice plan. You can also do it in the chat with your tutor."

msgid "Tu cuenta"
msgstr "Your account"

msgid "Para borrar tu cuenta y tus datos, escríbenos a %(email)s."
msgstr "To delete your account and your data, write to us at %(email)s."

msgid "Elige tu nivel de inglés de hoy."
msgstr "Choose your English level today."

msgid "Elige tu área."
msgstr "Choose your field."

msgid "Elige al menos 1 situación."
msgstr "Choose at least 1 situation."

msgid "Elige como máximo 4 situaciones."
msgstr "Choose at most 4 situations."

msgid "Elige situaciones de la lista."
msgstr "Choose situations from the list."

msgid "Elige 15, 20 o 30 minutos."
msgstr "Choose 15, 20 or 30 minutes."

msgid "Elige de 2 a 7 días."
msgstr "Choose 2 to 7 days."

msgid "Elige el nivel que quieres alcanzar."
msgstr "Choose the level you want to reach."

msgid "Tu meta debe ser igual o más alta que tu nivel de hoy."
msgstr "Your goal must be your current level or higher."

msgid "Elige una fecha entre 4 semanas y 1 año a partir de hoy, o déjala vacía."
msgstr "Choose a date between 4 weeks and 1 year from today, or leave it empty."

msgid "Máximo 300 caracteres."
msgstr "300 characters at most."

msgid "No reconocimos la zona horaria de tu navegador. Recarga la página e intenta de nuevo."
msgstr "We didn't recognise your browser's time zone. Reload the page and try again."

msgid "Guardamos tu perfil."
msgstr "Your profile is saved."

msgid "Tus respuestas"
msgstr "Your answers"

msgid "Días a la semana"
msgstr "Days a week"

msgid "Fecha meta (opcional)"
msgstr "Target date (optional)"

msgid "Para qué lo quieres, en una frase (opcional)"
msgstr "Why you want it, in one sentence (optional)"

msgid "Guardar perfil"
msgstr "Save profile"

msgid "Tu plan"
msgstr "Your plan"

msgid "%(weeks)s semanas · %(planned)s sesiones planeadas · %(done)s hechas"
msgstr "%(weeks)s weeks · %(planned)s sessions planned · %(done)s done"

msgid "Semana %(n)s"
msgstr "Week %(n)s"

msgid "Hablar"
msgstr "Speaking"

msgid "Escribir"
msgstr "Writing"

msgid "Hecha"
msgstr "Done"

msgid "Saltada"
msgstr "Skipped"

msgid "Responde tu perfil y armamos tu plan de práctica."
msgstr "Fill in your profile and we'll build your practice plan."

msgid "Ir a tu perfil"
msgstr "Go to your profile"

msgid "Guardamos tu perfil, tu plan, lo que dices en cada lección (como lo reporta tu asistente), tus errores y tu glosario; nunca audio. Tus lecciones pasan por el proveedor de tu asistente, por ejemplo Claude. Para borrar tus datos, escribe a %(email)s."
msgstr "We store your profile, your plan, what you say in each lesson (as your assistant reports it), your mistakes and your glossary; never audio. Your lessons pass through your assistant's provider, for example Claude. To delete your data, write to %(email)s."

msgid "Para continuar cada sesión donde quedó la anterior y calcular tu progreso."
msgstr "To continue each session where the last one ended and to compute your progress."

msgid "En esta versión de prueba pides una copia de tus datos o el borrado de tu cuenta escribiendo a %(email)s; lo hacemos a mano y te confirmamos por correo."
msgstr "In this trial version you ask for a copy of your data or for your account to be deleted by writing to %(email)s; we do it by hand and confirm by email."
```

`"Conectar"` (dashboard Task 20) and `"Pendiente"` (dashboard Task 21) already have entries, so the block above does not repeat them: `test_every_template_string_has_an_english_translation` and Babel both reject duplicates. Run `uv run python -c "from tutor.web.i18n import missing_translations; print(missing_translations())"` and expect `[]`.

Append to `src/tutor/web/static/js/app.js`:

```js
// Perfil (core loop v0): send the browser's IANA time zone with the profile form (spec 6.1).
function fillTimezones(root) {
  let zone = "";
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    zone = "";
  }
  if (!zone) return;
  for (const input of root.querySelectorAll("input[data-timezone]")) input.value = zone;
}
fillTimezones(document);
document.addEventListener("htmx:afterSwap", (event) => fillTimezones(event.target));
```

Append to `src/tutor/web/static/css/app.css`:

```css
/* Perfil (core loop v0 Task 25) */
.profile-form { display: grid; gap: 1.25rem; }
.profile-form fieldset { display: grid; gap: .35rem; margin: 0; padding: 0; border: 0; }
.profile-form legend { margin-bottom: .35rem; font-weight: 600; }
.choice { display: grid; grid-template-columns: auto 1fr; column-gap: .5rem; align-items: start; min-height: 44px; }
.choice__hint { grid-column: 2; font-size: .85rem; }
.profile-form select, .profile-form textarea, .profile-form input[type="date"] { display: block; width: 100%; max-width: 32rem; min-height: 44px; font: inherit; }
.plan-week { display: grid; gap: .5rem; margin: 0; padding-left: 1.25rem; }
.plan-week__item { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; }
```

- [ ] **Step 3e: Install the port in production and in the demo**

In `src/tutor/app.py`, add the import:

```python
from tutor.web.profile import ServicesProfiles, install_profiles
```

and in `build_app`, after `web = create_app(pg_web_deps(engine, settings, config), config)`, add:

```python
    install_profiles(web, ServicesProfiles(svc))  # Perfil saves through the same Services
```

In `src/tutor/web/demo_server.py`, add the import `from tutor.web.profile import MemoryProfiles, install_profiles` and append after the `app = create_app(...)` statement:

```python
install_profiles(app, MemoryProfiles(lambda: datetime.now(UTC)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/web tests/unit/app -q`
Expected: PASS, including 12 tests in `test_web_profile.py`, 5 in `test_web_v0_nav.py`, the updated auth, home and sweep tests, and `test_web_template_safety.py` / `test_web_look.py` / `test_web_js.py` from dashboard Tasks 13 and 15.

Run: `uv run just test-int`
Expected: PASS, including `tests/integration/test_pg_profile_web.py`.

Run: `uv run just dashboard-demo`, open `http://localhost:8780/auth/test-login`, log in as Nuevo, open Perfil at 390 × 844 and 1280 × 800 in Chrome, save valid answers, check the plan card and the five-link tab bar, and check in DevTools that the hidden `timezone` input holds the browser's zone. Stop the server and note the result in the ledger.

- [ ] **Step 5: Run the gate, review and commit**

Run: `uv run just fmt` then `uv run just check`
Expected: PASS. Dispatch `security-reviewer` on the diff (`src/tutor/web/routes/auth.py`, `src/tutor/web/routes/profile.py`, `src/tutor/app.py`) and fix every BLOCKER and MAJOR.

```bash
git add src/tutor/web src/tutor/app.py tests/unit/web tests/unit/app/test_app_http.py tests/integration/test_pg_profile_web.py
git commit -m "feat(web): Perfil page with onboarding form and plan-lite; v0 navigation and privacy note" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 26: Gate report and evals tooling

The gate numbers of spec section 14, computed by the server from stored evidence and read only, plus the spike's evidence-fidelity tooling copied into `evals/fidelity/` for v0 transcripts. `spike/` is never modified.

> RULING (confirmation rate survives purges; controller): spec 9.4 purges `provisional` items 7 days after they were saved, so the rate is not computed from `glossary_items`. It replays the `glossary_saved` audit rows (core Task 13; ids and enums only) up to the window's end, in time order, to each item id's final status, and counts the items first saved in the window (spec 10.4); `confirmed` is sticky (the domain never moves a confirmed item back). Rate = confirmed / (confirmed + provisional + declined); `archived` counts as confirmed (plan ruling 9). No caveat line.

> RULING (evals on the path): `pythonpath` and `mypy_path` stay as core Task 10 set them. `evals/fidelity/` is a package; `evals/` (no `__init__.py`) becomes a second test path, so pytest's prepend import mode puts `evals/` on `sys.path` when it collects `evals/test_*.py`, and mypy finds `fidelity` through the `evals` entry already in `[tool.mypy] files`. The tooling's unit tests (`evals/test_fidelity_tools.py`) are unmarked and run in `check`; the fidelity eval itself stays marked `eval`.

**Files:**
- Create: `src/tutor/ops/__init__.py`, `src/tutor/ops/gate_report.py`
- Create: `evals/fidelity/__init__.py`, `evals/fidelity/metrics.py`, `evals/fidelity/redact.py`, `evals/fidelity/annotate.py`, `evals/fidelity/report.py`
- Modify: `evals/test_evidence_fidelity.py` (real eval over v0 fixtures), `evals/README.md` (v0 workflow)
- Modify: `pyproject.toml` (`testpaths = ["tests", "evals"]`; ruff `S101` allowed in `evals/test_*.py`)
- Modify: `justfile` (`gate-report`, `eval-fidelity`, `eval`)
- Test: `tests/integration/test_gate_report.py`, `evals/test_fidelity_tools.py`

**Interfaces:**
- Consumes: tables `sessions`, `session_metrics`, `audit_log` (Task 14); the `glossary_saved` audit meta (`AuditEvent`, core Tasks 10 and 13); `psycopg_url` (Task 14); `user_hash` (`tutor.mcp.observe`, Task 20); `tutor.domain.text.normalize` (Task 1); for tests, the services (`save_profile`, `start_lesson`, `save_glossary`, `end_session`), `repo_contract.sample_evidence`, `DEFAULT_TURNS`, and the integration fixtures `engine`, `uow_factory`, `user_id`, `other_user_id`, `now`.
- Produces: `tutor.ops.gate_report.main(argv: Sequence[str] | None = None) -> int` (contract); also `read_only_engine(database_url) -> Engine`, `window(start, end, tz) -> tuple[datetime, datetime]`, `build_report(conn, start, end, tz_name, labels, author) -> GateReport`, `render(report) -> str`, `load_labels(path) -> dict[str, str]`, `Ratio`, `UserLine`, `GateReport`; `fidelity.metrics` (`Match`, `user_messages`, `turns_fidelity`, `errors_fidelity`, `chunks_fidelity`); `fidelity.redact` (`redact`, `redact_json`, `main`); `fidelity.annotate.main`; `fidelity.report` (`THRESHOLDS`, `SessionFidelity`, `measure`, `load_sessions`, `totals`, `failures`, `render`, `main`); recipes `gate-report`, `eval-fidelity`, `eval`.

- [ ] **Step 1a: Write the failing gate-report tests**

`tests/integration/test_gate_report.py`:

```python
"""Gate report on db-test, seeded through the core services (spec section 14)."""

import json
import zoneinfo
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from psycopg.errors import ReadOnlySqlTransaction
from repo_contract import DEFAULT_TURNS, sample_evidence
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tutor.domain.glossary import IncomingItem
from tutor.domain.profile import ProfileInput
from tutor.domain.text import count_words
from tutor.domain.validation import Evidence, ReportedError
from tutor.mcp.observe import user_hash
from tutor.ops.gate_report import build_report, main, read_only_engine, render
from tutor.services.context import Services
from tutor.services.glossary import save_glossary
from tutor.services.lesson import start_lesson
from tutor.services.ports import Mode, UowFactory
from tutor.services.profile import save_profile
from tutor.services.session_end import end_session
from tutor.services.views import StartLessonRequest
from tutor.web.memory import FixedClock

pytestmark = pytest.mark.integration

DAY = date(2026, 10, 14)
TZ = "America/Mexico_City"
PROFILE = ProfileInput(
    self_level="B1",
    domains=["it"],
    use_cases=["standup", "code_review"],
    minutes_per_day=20,
    days_per_week=3,
    target_level="B2",
)


def _raw(ev: Evidence) -> dict[str, object]:
    return {
        "user_turns": list(ev.user_turns),
        "errors": [
            {"said": e.said, "correct": e.correct, "category": e.category} for e in ev.errors
        ],
        "chunks_used": list(ev.chunks_used),
    }


def _item(text_: str) -> IncomingItem:
    return IncomingItem(
        kind="term",
        text=text_,
        meaning="Team word.",
        context_sentence=f"We said {text_} in the standup.",
        domain="it",
    )


def _lesson(
    svc: Services,
    clock: FixedClock,
    uid: UUID,
    mode: Mode,
    minutes: int,
    evidence: Evidence,
    *,
    used: int = 0,
    glossary: bool = False,
) -> None:
    lesson = start_lesson(svc, uid, StartLessonRequest(mode=mode))
    if glossary:
        both = [_item("roll back"), _item("heads-up")]
        save_glossary(svc, uid, lesson.session_id, "confirmed", both)
        save_glossary(svc, uid, lesson.session_id, "provisional", [_item("on track")])
        save_glossary(svc, uid, lesson.session_id, "declined", [_item("blocker")])
    chunks = tuple(c.id for c in lesson.item.chunks)[:used]
    ev = replace(evidence, chunks_used=chunks)
    clock.advance(timedelta(minutes=minutes))
    end_session(svc, uid, lesson.session_id, ev, _raw(ev))
    clock.advance(timedelta(minutes=1))


@pytest.fixture
def seeded(
    uow_factory: UowFactory, now: datetime, user_id: UUID, other_user_id: UUID
) -> dict[str, str]:
    """Author (user_id): two closed voice lessons (2 and 3 min) and one incomplete text lesson.
    Friend (other_user_id): one closed text lesson (6 min) whose only error is rejected."""
    clock = FixedClock(now)
    svc = Services(
        uow=uow_factory, clock=clock, valid_timezones=frozenset(zoneinfo.available_timezones())
    )
    save_profile(svc, user_id, PROFILE)
    save_profile(svc, other_user_id, PROFILE)
    valid = ReportedError(said="I am blocked", correct="I'm blocked", category="grammar")
    rejected = ReportedError(said="I goed there", correct="I went there", category="grammar")
    _lesson(svc, clock, user_id, "voice", 2, sample_evidence(errors=[valid]), used=2, glossary=True)
    _lesson(svc, clock, user_id, "voice", 3, sample_evidence(), used=1)
    _lesson(svc, clock, user_id, "text", 1, sample_evidence(turns=["Short answer only."]))
    _lesson(svc, clock, other_user_id, "text", 6, sample_evidence(errors=[rejected]))
    return {user_hash(user_id): "author", user_hash(other_user_id): "dev-1"}


def test_report_counts_the_gate_numbers(engine: Engine, seeded: dict[str, str]) -> None:
    with engine.connect() as conn:
        report = build_report(conn, DAY, DAY, TZ, seeded, "author")
    words = sum(count_words(t) for t in DEFAULT_TURNS)
    out = render(report)
    assert "| author | 2 | 0 | 1 | 2 |" in out
    assert "| dev-1 | 1 | 1 | 0 | 0 |" in out
    assert "| All | 3 | 1 | 1 | 2 |" in out
    assert "| Real sessions (closed, low trust included) | 3 | >= 15 |" in out
    assert "| Voice sessions (closed) | 2 | >= 5 |" in out
    assert "| Author sessions (author) | 2 | >= 10 in 3 weeks |" in out
    assert (
        f"| Median user_words_per_min, voice | {(words / 2 + words / 3) / 2:.1f} | >= 20 |" in out
    )
    assert f"| Median user_words_per_min, text | {words / 6:.1f} | - |" in out
    assert "| Glossary confirmation rate | 50% (2 of 4) | >= 60% |" in out
    assert "| Activation rate | 20% (3 of 15 phrases) | - |" in out
    assert "| end_session validity (closed vs incomplete) | 75% (3 of 4) | - |" in out
    assert "| Rejected-error share | 50% (1 of 2) | - |" in out
    assert "purged" not in out  # no caveat line: the rate comes from glossary_saved audit rows


def test_confirmation_rate_survives_the_provisional_purge(
    engine: Engine,
    seeded: dict[str, str],
    uow_factory: UowFactory,
    user_id: UUID,
    now: datetime,
) -> None:
    with uow_factory(user_id) as uow:
        assert uow.glossary.purge(now + timedelta(days=8)) == 1  # "on track" expired
    with engine.connect() as conn:
        report = build_report(conn, DAY, DAY, TZ, seeded, "author")
    assert "| Glossary confirmation rate | 50% (2 of 4) | >= 60% |" in render(report)


def test_an_empty_window_reads_not_available(engine: Engine, seeded: dict[str, str]) -> None:
    with engine.connect() as conn:
        report = build_report(conn, DAY + timedelta(days=1), DAY + timedelta(days=2), TZ, {}, None)
    out = render(report)
    assert "| Median user_words_per_min, voice | n/a | >= 20 |" in out
    assert "| Glossary confirmation rate | n/a | >= 60% |" in out
    assert "| Author sessions (pass --author) | n/a | >= 10 in 3 weeks |" in out


def test_main_prints_labels_never_emails_or_ids(
    engine: Engine,
    seeded: dict[str, str],
    user_id: UUID,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(seeded), encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    code = main(
        [
            "--from",
            "2026-10-14",
            "--to",
            "2026-10-14",
            "--labels",
            str(labels),
            "--author",
            "author",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "| author | 2 |" in out
    assert "ana@example.com" not in out and "beto@example.com" not in out
    assert str(user_id) not in out


def test_main_needs_a_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["--from", "2026-10-14"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_bad_labels_file_is_refused(tmp_path: Path, engine: Engine) -> None:
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"ana@example.com": "Ana"}), encoding="utf-8")
    url = engine.url.render_as_string(hide_password=False)
    with pytest.raises(SystemExit, match="12 hex"):
        main(["--from", "2026-10-14", "--labels", str(labels), "--database-url", url])


def test_the_report_connection_is_read_only(engine: Engine) -> None:
    report_engine = read_only_engine(engine.url.render_as_string(hide_password=False))
    try:
        with pytest.raises(DBAPIError) as err, report_engine.connect() as conn:
            conn.execute(text("CREATE TABLE gate_report_probe (id int)"))
        assert isinstance(err.value.orig, ReadOnlySqlTransaction)
    finally:
        report_engine.dispose()
```

`sample_evidence` gives 54 learner words over three turns (`DEFAULT_TURNS`), so the two voice lessons run at 27.0 and 18.0 words per minute and the friend's text lesson at 9.0; the test computes these from `count_words` rather than hard-coding them.

- [ ] **Step 1b: Write the failing tooling tests**

`evals/test_fidelity_tools.py`:

```python
"""Evidence-fidelity tooling (copied from spike/src/tutor_spike/analysis, adapted to
tutor.domain.text.normalize). Not an eval: no LLM; runs in `just check`."""

import json
import unicodedata
from pathlib import Path

import pytest
from fidelity.annotate import main as annotate_main
from fidelity.metrics import Match, chunks_fidelity, errors_fidelity, turns_fidelity, user_messages
from fidelity.redact import main as redact_main
from fidelity.redact import redact, redact_json
from fidelity.report import failures, load_sessions, main as report_main, totals

pytestmark = pytest.mark.unit

TRANSCRIPT = """U: Let's practise English for 15 minutes.
A: Sure! Which situation?
U: The standup, about the outage
that happened yesterday.
A: Great.
U: Yesterday I go to the office.
"""


def test_user_messages_joins_continuation_lines() -> None:
    assert user_messages(TRANSCRIPT) == [
        "Let's practise English for 15 minutes.",
        "The standup, about the outage that happened yesterday.",
        "Yesterday I go to the office.",
    ]


def test_turns_fidelity_counts_substring_matches_both_ways() -> None:
    payload = ["the standup about the outage", "Yesterday I go to the office", "I love Python"]
    match = turns_fidelity(payload, user_messages(TRANSCRIPT))
    assert (match.hit_ref, match.n_ref) == (2, 3)
    assert (match.hit_pred, match.n_pred) == (2, 3)


def test_curly_apostrophes_match_straight_ones() -> None:
    curly = "Let\N{RIGHT SINGLE QUOTATION MARK}s practise English for 15 minutes."
    assert turns_fidelity([curly], user_messages(TRANSCRIPT)).hit_pred == 1


def test_fuzzy_turns_accept_near_copies() -> None:
    transcript = ["We need two more days for the release"]
    strict = turns_fidelity(["We need two more day for the release"], transcript)
    fuzzy = turns_fidelity(["We need two more day for the release"], transcript, fuzzy=True)
    assert strict.recall == 0.0
    assert fuzzy.recall == 1.0


def test_errors_fidelity_matches_when_one_said_contains_the_other() -> None:
    match = errors_fidelity(
        payload_said=["I go to the office", "invented"],
        annotated_said=["Yesterday I go to the office", "I have 30 years"],
    )
    assert (match.recall, match.precision) == (0.5, 0.5)


def test_chunks_fidelity_compares_ids() -> None:
    match = chunks_fidelity(["it-01-c1", "it-01-c9"], ["it-01-c1", "it-01-c2"])
    assert (match.recall, match.precision) == (0.5, 0.5)


def test_match_is_none_when_there_is_nothing_to_compare() -> None:
    assert errors_fidelity([], []).recall is None
    assert errors_fidelity([], []).precision is None


def test_matches_add_up_as_micro_average() -> None:
    total = Match(1, 1, 1, 1) + Match(1, 3, 0, 3)
    assert (total.recall, total.precision) == (0.5, 0.25)


def test_redact_replaces_names_case_insensitively_and_emails() -> None:
    text = "Hi Mateo, mail mateo.x@example.com. MATEO said hi to Mateos."
    assert redact(text, ["Mateo"]) == "Hi [name], mail [email]. [name] said hi to Mateos."


def test_redact_handles_longer_names_first() -> None:
    assert redact("Ana Maria and Ana", ["Ana", "Ana Maria"]) == "[name] and [name]"


def test_redact_underscore_digit_possessive_and_letters() -> None:
    assert redact("Mateo_Perez", ["Mateo"]) == "[name]_Perez"
    assert redact("mateo2 _Mateo_1 xMateo", ["Mateo"]) == "[name]2 _[name]_1 xMateo"
    assert redact("Mateo's book", ["Mateo"]) == "[name]'s book"
    assert redact("Mateos", ["Mateo"]) == "Mateos"
    assert redact("a@b.co", ["Mateo"]) == "[email]"


def test_redact_accented_name_nfc_and_nfd() -> None:
    nfc = unicodedata.normalize("NFC", "Lucía")
    nfd = unicodedata.normalize("NFD", "Lucía")
    assert nfc != nfd
    assert redact(f"hi {nfc} and {nfd}", ["Lucía"]) == "hi [name] and [name]"
    assert redact(f"hi {nfc}", [nfd]) == "hi [name]"


def test_redact_folds_accents_on_both_sides() -> None:
    assert redact("Lucia met Lucía and LUCÍA.", ["Lucía"]) == "[name] met [name] and [name]."
    text = "¿Lucía Fernández? Sí, Lucia Fernandez, the Fernández family."
    expected = "¿[name]? Sí, [name], the [name] family."
    assert redact(text, ["Lucía Fernández", "Fernandez"]) == expected


def test_redact_json_touches_values_never_keys() -> None:
    data = {"Mateo": ["Mateo said hi", {"said": "mateo@example.com"}], "n": 3}
    assert redact_json(data, ["Mateo"]) == {
        "Mateo": ["[name] said hi", {"said": "[email]"}],
        "n": 3,
    }


def test_redact_cli_rewrites_files_in_place(tmp_path: Path) -> None:
    transcript = tmp_path / "s.md"
    payload = tmp_path / "s.payload.json"
    transcript.write_text("U: I am Mateo.\n", encoding="utf-8")
    payload.write_text(json.dumps({"user_turns": ["I am Mateo."]}), encoding="utf-8")
    assert redact_main(["--names", "Mateo", str(transcript), str(payload)]) == 0
    assert transcript.read_text(encoding="utf-8") == "U: I am [name].\n"
    assert json.loads(payload.read_text(encoding="utf-8")) == {"user_turns": ["I am [name]."]}


def test_redact_cli_needs_names(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="names"):
        redact_main(["--names", " ", str(tmp_path / "x.md")])


def test_annotate_prints_only_the_learner_turns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "t.md"
    path.write_text("U: Yesterday I go.\nA: You went.\nU: Sí, I went\nthere early.\n", "utf-8")
    assert annotate_main([str(path)]) == 0
    assert capsys.readouterr().out == "1. Yesterday I go.\n2. Sí, I went there early.\n"


def _fixtures(tmp_path: Path, *, said: str, chunks: list[str]) -> Path:
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "annotations").mkdir()
    (tmp_path / "transcripts" / "s1.md").write_text(TRANSCRIPT, encoding="utf-8")
    payload = {
        "user_turns": user_messages(TRANSCRIPT),
        "errors": [{"said": said, "correct": "I went", "category": "grammar"}],
        "chunks_used": chunks,
    }
    (tmp_path / "transcripts" / "s1.payload.json").write_text(json.dumps(payload), "utf-8")
    annotation = {"errors": [{"said": "I go to the office"}], "chunks_used": ["it-01-c1"]}
    (tmp_path / "annotations" / "s1.json").write_text(json.dumps(annotation), "utf-8")
    (tmp_path / "transcripts" / "orphan.md").write_text(TRANSCRIPT, encoding="utf-8")
    return tmp_path


def test_report_passes_good_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixtures = _fixtures(tmp_path, said="Yesterday I go to the office", chunks=["it-01-c1"])
    sessions = load_sessions(fixtures)
    assert [s.name for s in sessions] == ["s1"]  # the orphan transcript has no payload
    assert failures(totals(sessions)) == []
    assert report_main(["--fixtures", str(fixtures)]) == 0
    assert "| s1 |" in capsys.readouterr().out


def test_report_fails_below_the_thresholds(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path, said="invented words", chunks=[])
    problems = failures(totals(load_sessions(fixtures)))
    assert "errors recall 0% < 70%" in problems
    assert "chunks_used recall 0% < 80%" in problems
    assert report_main(["--fixtures", str(fixtures)]) == 1


def test_spike_list_annotations_still_load(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path, said="I go to the office", chunks=[])
    (fixtures / "annotations" / "s1.json").write_text(
        json.dumps([{"said": "I go to the office"}]), "utf-8"
    )
    [session] = load_sessions(fixtures)
    assert session.chunks is None
    assert session.errors.recall == 1.0


def test_report_without_sessions_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert report_main(["--fixtures", str(tmp_path)]) == 0
    assert "No annotated sessions" in capsys.readouterr().out
```

- [ ] **Step 2: Run them to verify they fail**

Add `"evals"` to the pytest test paths first: in `pyproject.toml`, change `testpaths = ["tests"]` to `testpaths = ["tests", "evals"]`. Under `[tool.ruff.lint.per-file-ignores]`, add this line after `"tests/**" = ["S101"]` (the evals tests use plain `assert`, which ruff's S101 rejects outside `tests/`):

```toml
"evals/test_*.py" = ["S101"]
```

The markers stay as they are: `test`, `check-fast` and `check` select `not eval` (and `not integration`), so `evals/test_fidelity_tools.py` (marked `unit`) runs in all three and `evals/test_evidence_fidelity.py` (marked `eval`) in none.

Run: `uv run pytest evals/test_fidelity_tools.py -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'fidelity'`.

Run: `uv run just test-int`
Expected: FAIL at collection of `tests/integration/test_gate_report.py` with `ModuleNotFoundError: No module named 'tutor.ops'`.

- [ ] **Step 3a: Implement the gate report**

`src/tutor/ops/__init__.py`:

```python
"""Operator tools: run by the author on the server, never by learners."""
```

`src/tutor/ops/gate_report.py`:

```python
"""Validation-gate numbers (core loop v0 spec section 14), read only.

Run on the server as the database owner (a role that bypasses row-level security):
    python -m tutor.ops.gate_report --from 2026-11-16 [--to 2026-12-04]
        [--labels labels.json] [--author NAME] [--tz America/Mexico_City] [--database-url URL]
`labels.json` maps user hashes (first 12 hex of SHA-256 of the user id, as in the logs) to names.
No email, display name or learner text is read.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, create_engine, select
from sqlalchemy.pool import NullPool

from tutor.db.engine import psycopg_url
from tutor.db.tables import audit_log, session_metrics, sessions
from tutor.mcp.observe import user_hash

DEFAULT_TZ = "America/Mexico_City"
_HASH = re.compile(r"^[0-9a-f]{12}$")
NOTE = "evidence_fidelity comes from `just eval-fidelity`, not from this report."
# Plan ruling 9: archived counts as confirmed (never produced in v0).
_CONFIRMED = frozenset({"confirmed", "archived"})
_PROPOSED = _CONFIRMED | {"provisional", "declined"}


@dataclass(frozen=True, slots=True)
class Ratio:
    hits: int
    total: int

    @property
    def value(self) -> float | None:
        return self.hits / self.total if self.total else None


@dataclass(frozen=True, slots=True)
class UserLine:
    label: str
    closed: int
    low_trust: int
    incomplete: int
    voice: int


@dataclass(frozen=True, slots=True)
class GateReport:
    start: date
    end: date
    tz: str
    users: tuple[UserLine, ...]
    author: str | None
    author_closed: int | None
    median_wpm_voice: float | None
    median_wpm_text: float | None
    confirmation: Ratio
    activation: Ratio
    validity: Ratio
    rejected_errors: Ratio

    @property
    def closed(self) -> int:
        return sum(u.closed for u in self.users)

    @property
    def voice(self) -> int:
        return sum(u.voice for u in self.users)


def read_only_engine(database_url: str) -> Engine:
    """Every transaction on this engine is READ ONLY; timestamps come back in UTC."""
    return create_engine(
        psycopg_url(database_url),
        poolclass=NullPool,
        connect_args={"options": "-c timezone=UTC -c default_transaction_read_only=on"},
    )


def window(start: date, end: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start 00:00, end + 1 day 00:00) in the given zone, as UTC instants."""
    low = datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC)
    high = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return low, high


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _glossary_final_statuses(conn: Connection, low: datetime, high: datetime) -> dict[str, str]:
    """Final status of each item first saved in [low, high) (spec 10.4), replaying the
    glossary_saved audit rows (ids and enums only) in time order. Audit rows outlive the
    provisional purge (spec 9.4); confirmed is sticky, as in the domain rules."""
    rows = conn.execute(
        select(audit_log.c.at, audit_log.c.meta)
        .where(audit_log.c.event == "glossary_saved", audit_log.c.at < high)
        .order_by(audit_log.c.at)
    ).all()
    first_at: dict[str, datetime] = {}
    final: dict[str, str] = {}
    for at, meta in rows:
        for item in meta.get("items", ()):
            item_id, status = str(item["id"]), str(item["status"])
            first_at.setdefault(item_id, at)
            if final.get(item_id) not in _CONFIRMED:
                final[item_id] = status
    return {item_id: s for item_id, s in final.items() if first_at[item_id] >= low}


def build_report(
    conn: Connection,
    start: date,
    end: date,
    tz_name: str,
    labels: Mapping[str, str],
    author: str | None,
) -> GateReport:
    low, high = window(start, end, ZoneInfo(tz_name))
    rows = conn.execute(
        select(
            sessions.c.user_id,
            sessions.c.status,
            sessions.c.low_trust,
            sessions.c.mode,
            session_metrics.c.user_words_per_min,
            session_metrics.c.chunks_offered,
            session_metrics.c.chunks_used,
            session_metrics.c.errors_total,
            session_metrics.c.errors_rejected,
        )
        .select_from(
            sessions.outerjoin(session_metrics, session_metrics.c.session_id == sessions.c.id)
        )
        .where(sessions.c.started_at >= low, sessions.c.started_at < high)
    ).all()
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    wpm: dict[str, list[float]] = {"voice": [], "text": []}
    offered = used = reported = rejected = 0
    for r in rows:
        line = counts[labels.get(user_hash(r.user_id), user_hash(r.user_id))]
        if r.errors_total is not None:
            reported += r.errors_total + r.errors_rejected
            rejected += r.errors_rejected
        if r.status == "incomplete":
            line[2] += 1
            continue
        if r.status != "closed":
            continue
        line[0] += 1
        line[1] += int(r.low_trust)
        line[3] += int(r.mode == "voice")
        if r.user_words_per_min is not None:
            wpm[r.mode].append(float(r.user_words_per_min))
            offered += r.chunks_offered
            used += r.chunks_used
    final = _glossary_final_statuses(conn, low, high)
    confirmed = sum(1 for s in final.values() if s in _CONFIRMED)
    proposed = sum(1 for s in final.values() if s in _PROPOSED)
    users = tuple(UserLine(label, *values) for label, values in sorted(counts.items()))
    closed = sum(u.closed for u in users)
    incomplete = sum(u.incomplete for u in users)
    author_closed = None
    if author is not None:
        author_closed = sum(u.closed for u in users if u.label == author)
    return GateReport(
        start=start,
        end=end,
        tz=tz_name,
        users=users,
        author=author,
        author_closed=author_closed,
        median_wpm_voice=_median(wpm["voice"]),
        median_wpm_text=_median(wpm["text"]),
        confirmation=Ratio(confirmed, proposed),
        activation=Ratio(used, offered),
        validity=Ratio(closed, closed + incomplete),
        rejected_errors=Ratio(rejected, reported),
    )


def _pct(ratio: Ratio, unit: str = "") -> str:
    value = ratio.value
    if value is None:
        return "n/a"
    return f"{value:.0%} ({ratio.hits} of {ratio.total}{unit})"


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def render(report: GateReport) -> str:
    """Markdown for docs/v0/acceptance.md and the gate decision (ASCII only)."""
    lines = [
        f"# Gate report, {report.start.isoformat()} to {report.end.isoformat()} ({report.tz})",
        "",
        "## Sessions per user",
        "",
        "| User | Closed | Low trust | Incomplete | Voice |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for u in report.users:
        lines.append(f"| {u.label} | {u.closed} | {u.low_trust} | {u.incomplete} | {u.voice} |")
    total = (
        report.closed,
        sum(u.low_trust for u in report.users),
        sum(u.incomplete for u in report.users),
        report.voice,
    )
    lines.append("| All | {} | {} | {} | {} |".format(*total))
    author_label = (
        f"Author sessions ({report.author})"
        if report.author
        else ("Author sessions (pass --author)")
    )
    author_value = "n/a" if report.author_closed is None else str(report.author_closed)
    lines += [
        "",
        "## Gate numbers",
        "",
        "| Metric | Value | Gate |",
        "| --- | --- | --- |",
        f"| Real sessions (closed, low trust included) | {report.closed} | >= 15 |",
        f"| Voice sessions (closed) | {report.voice} | >= 5 |",
        f"| {author_label} | {author_value} | >= 10 in 3 weeks |",
        f"| Median user_words_per_min, voice | {_number(report.median_wpm_voice)} | >= 20 |",
        f"| Median user_words_per_min, text | {_number(report.median_wpm_text)} | - |",
        f"| Glossary confirmation rate | {_pct(report.confirmation)} | >= 60% |",
        f"| Activation rate | {_pct(report.activation, ' phrases')} | - |",
        f"| end_session validity (closed vs incomplete) | {_pct(report.validity)} | - |",
        f"| Rejected-error share | {_pct(report.rejected_errors)} | - |",
        "",
        NOTE,
    ]
    return "\n".join(lines)


def load_labels(path: Path) -> dict[str, str]:
    """A JSON object of user hash -> name. Anything else stops the report."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read labels file {path}: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict):
        raise SystemExit("labels file must be a JSON object of user hash -> name")
    for key, value in data.items():
        if not _HASH.match(str(key)) or not isinstance(value, str):
            raise SystemExit("labels keys must be 12 hex user hashes and values must be names")
    return {str(k): v for k, v in data.items()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gate_report", description=__doc__)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--author")
    parser.add_argument("--tz", default=DEFAULT_TZ)
    parser.add_argument("--database-url", dest="database_url")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    url = args.database_url or os.environ.get("DATABASE_URL", "")
    if not url:
        print("Set DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2
    labels = load_labels(args.labels) if args.labels else {}
    end = args.end or datetime.now(ZoneInfo(args.tz)).date()
    engine = read_only_engine(url)
    try:
        with engine.connect() as conn:
            report = build_report(conn, args.start, end, args.tz, labels, args.author)
    finally:
        engine.dispose()
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3b: Copy and adapt the fidelity tooling**

`evals/fidelity/__init__.py`:

```python
"""Evidence-fidelity tooling for v0 text sessions (requirements section 11; spec section 14).

Copied from spike/src/tutor_spike/analysis (fidelity.py, annotate.py) and spike/src/tutor_spike
/redact.py, adapted to tutor.domain.text.normalize. The spike copy stays untouched.
"""
```

`evals/fidelity/metrics.py`:

```python
"""Recall and precision of the end_session evidence against a transcript and an annotation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from tutor.domain.text import normalize


def user_messages(transcript: str) -> list[str]:
    """Learner turns of a "U:" / "A:" transcript; continuation lines join the previous turn."""
    turns: list[tuple[str, str]] = []
    for line in transcript.splitlines():
        if line.startswith(("U:", "A:")):
            turns.append((line[0], line[2:].strip()))
        elif line.strip() and turns:
            speaker, text = turns[-1]
            turns[-1] = (speaker, f"{text} {line.strip()}")
    return [text for speaker, text in turns if speaker == "U"]


@dataclass(frozen=True)
class Match:
    hit_ref: int
    n_ref: int
    hit_pred: int
    n_pred: int

    @property
    def recall(self) -> float | None:
        return self.hit_ref / self.n_ref if self.n_ref else None

    @property
    def precision(self) -> float | None:
        return self.hit_pred / self.n_pred if self.n_pred else None

    def __add__(self, other: Match) -> Match:
        return Match(
            self.hit_ref + other.hit_ref,
            self.n_ref + other.n_ref,
            self.hit_pred + other.hit_pred,
            self.n_pred + other.n_pred,
        )


def _turn_matches(payload_turn: str, transcript_turn: str, fuzzy: bool) -> bool:
    p, t = normalize(payload_turn), normalize(transcript_turn)
    if not p:
        return False
    if p in t:
        return True
    return fuzzy and SequenceMatcher(None, p, t).ratio() >= 0.9


def turns_fidelity(
    payload_turns: Sequence[str], transcript_turns: Sequence[str], fuzzy: bool = False
) -> Match:
    return Match(
        hit_ref=sum(
            any(_turn_matches(p, t, fuzzy) for p in payload_turns) for t in transcript_turns
        ),
        n_ref=len(transcript_turns),
        hit_pred=sum(
            any(_turn_matches(p, t, fuzzy) for t in transcript_turns) for p in payload_turns
        ),
        n_pred=len(payload_turns),
    )


def _said_matches(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na and nb) and (na in nb or nb in na)


def errors_fidelity(payload_said: Sequence[str], annotated_said: Sequence[str]) -> Match:
    return Match(
        hit_ref=sum(any(_said_matches(p, a) for p in payload_said) for a in annotated_said),
        n_ref=len(annotated_said),
        hit_pred=sum(any(_said_matches(p, a) for a in annotated_said) for p in payload_said),
        n_pred=len(payload_said),
    )


def chunks_fidelity(payload_ids: Sequence[str], annotated_ids: Sequence[str]) -> Match:
    """v0 chunks have ids (it-07-c3), so they compare exactly."""
    payload, annotated = set(payload_ids), set(annotated_ids)
    return Match(
        hit_ref=len(annotated & payload),
        n_ref=len(annotated),
        hit_pred=len(payload & annotated),
        n_pred=len(payload),
    )
```

`evals/fidelity/redact.py`:

```python
"""Redact names and emails in fixture files before they are committed.

Usage (from evals/): uv run python -m fidelity.redact --names "Ana,Beto" FILE [FILE ...]
`.json` files are redacted value by value (keys untouched); other files as text. In place.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def _fold(text: str) -> str:
    """Accents folded: NFD, combining marks dropped, NFC ("Lucía" -> "Lucia")."""
    decomposed = unicodedata.normalize("NFD", text)
    kept = "".join(c for c in decomposed if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", kept)


def _folded_with_origin(text: str) -> tuple[str, list[int]]:
    """Folded text plus, for each folded character, the index of its source character."""
    folded: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        part = _fold(char)
        folded.append(part)
        origin += [index] * len(part)
    return "".join(folded), origin


def redact(text: str, names: Sequence[str]) -> str:
    text = _EMAIL.sub("[email]", unicodedata.normalize("NFC", text))
    folded_names = {_fold(n.strip()) for n in names if n.strip()}
    for name in sorted((n for n in folded_names if n), key=len, reverse=True):
        # Matched on accent-folded text, so "José" and "Jose" redact each other. Not preceded or
        # followed by a letter; digits and underscores do not protect a name.
        pattern = rf"(?<![^\W\d_]){re.escape(name)}(?![^\W\d_])"
        folded, origin = _folded_with_origin(text)
        spans = [
            (origin[m.start()], origin[m.end() - 1] + 1)
            for m in re.finditer(pattern, folded, flags=re.IGNORECASE)
        ]
        for start, end in reversed(spans):
            text = text[:start] + "[name]" + text[end:]
    return text


def redact_json(value: Any, names: Sequence[str]) -> Any:
    """Redact every string value (keys untouched), so escapes cannot hide a name."""
    if isinstance(value, str):
        return redact(value, names)
    if isinstance(value, list):
        return [redact_json(v, names) for v in value]
    if isinstance(value, dict):
        return {k: redact_json(v, names) for k, v in value.items()}
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names", required=True, help="Comma-separated names to redact")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    if not names:
        raise SystemExit("list at least one name in --names")
    for path in args.files:
        raw = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            redacted = json.dumps(redact_json(json.loads(raw), names), indent=2, ensure_ascii=False)
        else:
            redacted = redact(raw, names)
        path.write_text(redacted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`evals/fidelity/annotate.py`:

```python
"""Print a transcript's learner turns, numbered, for blind error annotation.

Usage (from evals/): uv run python -m fidelity.annotate fixtures/transcripts/<name>.md
Only the learner's messages are shown, so the tutor's feedback cannot steer the annotation.
"""

from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Sequence
from pathlib import Path

from fidelity.metrics import user_messages


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path)
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper) and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    text = args.transcript.read_text(encoding="utf-8")
    for number, message in enumerate(user_messages(text), 1):
        print(f"{number}. {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`evals/fidelity/report.py`:

```python
"""Evidence fidelity over the annotated v0 text sessions in evals/fixtures.

Layout: transcripts/<name>.md (redacted "U:"/"A:" transcript), transcripts/<name>.payload.json
(the session's sessions.raw_evidence), annotations/<name>.json (either the spike's list of
{"said": ...} or {"errors": [{"said": ...}], "chunks_used": ["it-07-c3", ...]}).
Usage (from evals/): uv run python -m fidelity.report [--fixtures fixtures]
Exit code 1 when a section 11 threshold is missed.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fidelity.metrics import (
    Match,
    chunks_fidelity,
    errors_fidelity,
    turns_fidelity,
    user_messages,
)

# Requirements section 11: (recall, precision); None means not required.
THRESHOLDS: dict[str, tuple[float | None, float | None]] = {
    "user_turns": (0.9, 0.9),
    "errors": (0.7, 0.8),
    "chunks_used": (0.8, None),
}


@dataclass(frozen=True)
class SessionFidelity:
    name: str
    turns: Match
    turns_fuzzy: Match
    errors: Match
    chunks: Match | None


def measure(
    name: str, transcript: str, payload: Mapping[str, Any], annotation: Any
) -> SessionFidelity:
    turns = user_messages(transcript)
    payload_turns = [str(t) for t in payload.get("user_turns", [])]
    said = [str(e["said"]) for e in payload.get("errors", [])]
    if isinstance(annotation, list):
        annotated_errors, annotated_chunks = annotation, None
    else:
        annotated_errors = annotation.get("errors", [])
        annotated_chunks = annotation.get("chunks_used")
    chunks = None
    if annotated_chunks is not None:
        chunks = chunks_fidelity(
            [str(c) for c in payload.get("chunks_used", [])], [str(c) for c in annotated_chunks]
        )
    return SessionFidelity(
        name=name,
        turns=turns_fidelity(payload_turns, turns),
        turns_fuzzy=turns_fidelity(payload_turns, turns, fuzzy=True),
        errors=errors_fidelity(said, [str(e["said"]) for e in annotated_errors]),
        chunks=chunks,
    )


def load_sessions(fixtures: Path) -> list[SessionFidelity]:
    """Every transcript that has both a payload and an annotation."""
    out: list[SessionFidelity] = []
    for transcript in sorted((fixtures / "transcripts").glob("*.md")):
        payload = transcript.with_name(f"{transcript.stem}.payload.json")
        annotation = fixtures / "annotations" / f"{transcript.stem}.json"
        if not payload.exists() or not annotation.exists():
            continue
        out.append(
            measure(
                transcript.stem,
                transcript.read_text(encoding="utf-8"),
                json.loads(payload.read_text(encoding="utf-8")),
                json.loads(annotation.read_text(encoding="utf-8")),
            )
        )
    return out


def totals(sessions: Sequence[SessionFidelity]) -> dict[str, Match]:
    """Micro-averages; chunks only over sessions whose annotation lists chunks."""
    empty = Match(0, 0, 0, 0)
    result = {"user_turns": empty, "errors": empty, "chunks_used": empty}
    for s in sessions:
        result["user_turns"] = result["user_turns"] + s.turns
        result["errors"] = result["errors"] + s.errors
        if s.chunks is not None:
            result["chunks_used"] = result["chunks_used"] + s.chunks
    return result


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def failures(total: Mapping[str, Match]) -> list[str]:
    problems: list[str] = []
    for field, (min_recall, min_precision) in THRESHOLDS.items():
        match = total[field]
        for label, value, floor in (
            ("recall", match.recall, min_recall),
            ("precision", match.precision, min_precision),
        ):
            if floor is not None and value is not None and value < floor:
                problems.append(f"{field} {label} {_pct(value)} < {_pct(floor)}")
    return problems


def render(sessions: Sequence[SessionFidelity]) -> str:
    lines = [
        "| Session | Turns R / P | Turns fuzzy R / P | Errors R / P | Chunks R / P |",
        "| --- | --- | --- | --- | --- |",
    ]

    def rp(m: Match | None) -> str:
        return "n/a" if m is None else f"{_pct(m.recall)} / {_pct(m.precision)}"

    for s in sessions:
        lines.append(
            f"| {s.name} | {rp(s.turns)} | {rp(s.turns_fuzzy)} | {rp(s.errors)} | {rp(s.chunks)} |"
        )
    total = totals(sessions)
    fuzzy = Match(0, 0, 0, 0)
    for s in sessions:
        fuzzy = fuzzy + s.turns_fuzzy
    lines.append(
        f"| All | {rp(total['user_turns'])} | {rp(fuzzy)} | {rp(total['errors'])} "
        f"| {rp(total['chunks_used'])} |"
    )
    problems = failures(total)
    lines += ["", "Thresholds met." if not problems else "Below threshold: " + "; ".join(problems)]
    lines.append("Voice sessions have no transcript; their fidelity is assumed equal to text.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("fixtures"))
    args = parser.parse_args(argv)
    sessions = load_sessions(args.fixtures)
    if not sessions:
        print(f"No annotated sessions in {args.fixtures}.")
        return 0
    print(render(sessions))
    return 1 if failures(totals(sessions)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`evals/test_evidence_fidelity.py` (replaces the placeholder):

```python
"""Evidence fidelity of real v0 text sessions against the section 11 thresholds."""

from pathlib import Path

import pytest
from fidelity.report import failures, load_sessions, totals

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.eval
def test_evidence_fidelity_meets_section_11_thresholds() -> None:
    sessions = load_sessions(FIXTURES)
    if not sessions:
        pytest.skip("no annotated v0 transcripts in evals/fixtures yet")
    assert failures(totals(sessions)) == []
```

- [ ] **Step 3c: Recipes and the evals README**

In `justfile`, add after the `serve` recipe:

```just
# Gate report (spec 14), read only, on DATABASE_URL: uv run just gate-report 2026-11-16
gate-report from:
    uv run python -m tutor.ops.gate_report --from {{from}}

# Evidence fidelity over the annotated transcripts in evals/fixtures (requirements 11)
[working-directory: 'evals']
eval-fidelity:
    uv run python -m fidelity.report --fixtures fixtures

# LLM evaluations (marked eval); never part of check
eval:
    uv run pytest -m eval -q
```

The production gate report runs inside the app container with labels and an author (core Task 27 runbook).

In `evals/README.md`, replace the paragraph under the title ("LLM-in-the-loop evaluations. They never run in hooks, … after the spike).") with:

```markdown
LLM-in-the-loop evaluations. They never run in hooks, `just check-fast` or `just check`: every eval is marked
`@pytest.mark.eval`, and those recipes select `not eval`. Run them with `uv run just eval`.
The tooling's own unit tests (`test_fidelity_tools.py`) are not evals and run in `just check`.
```

and replace the "## Planned structure" section (heading and the code block under it, up to the next "##") with:

~~~markdown
## Structure (core loop v0)

```
evals/
  fidelity/        tooling copied from the spike: metrics, annotate, redact, report
  fixtures/
    transcripts/   <name>.md (redacted text transcript) and <name>.payload.json (raw_evidence)
    annotations/   <name>.json: {"errors": [{"said": ...}], "chunks_used": ["it-07-c3", ...]}
  test_fidelity_tools.py     unit tests of the tooling (run in `just check`)
  test_evidence_fidelity.py  the eval (marked eval; `just eval`)
```

Workflow for one real text session (names like `2026-11-18-author-01`):

1. Copy the claude.ai conversation into `fixtures/transcripts/<name>.md` as `U:` / `A:` lines.
2. Export its payload on the server:
   `docker compose -f deploy/compose.prod.yml exec -T db psql -U tutor -d tutor -At -c "SELECT raw_evidence FROM sessions WHERE id = '<session id>'" > <name>.payload.json`
   (the session id is the last part of its Sesiones page URL).
3. From `evals/`: `uv run python -m fidelity.annotate fixtures/transcripts/<name>.md`, then write
   the annotation by hand, without looking at the payload.
4. From `evals/`: `uv run python -m fidelity.redact --names "<every name>" <the three files>`.
5. `uv run just eval-fidelity` prints recall and precision and exits 1 below a threshold.
~~~

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest evals/test_fidelity_tools.py -q`
Expected: PASS (21 tests).

Run: `uv run just test-int`
Expected: PASS, including 7 tests in `tests/integration/test_gate_report.py`.

Run: `uv run just eval-fidelity`
Expected: `No annotated sessions in fixtures.` (the fixtures folders hold only `.gitkeep` until the gate).

- [ ] **Step 5: Run the gate and commit**

Run: `uv run just fmt` then `uv run just check`
Expected: PASS. `spike/` is unchanged: `git status --short spike` prints nothing.

```bash
git add pyproject.toml justfile src/tutor/ops evals tests/integration/test_gate_report.py
git commit -m "feat(ops): read-only gate report and evidence-fidelity tooling for v0" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 27: Deployment: Docker, backups, runbook

One image, one Compose stack on the homelab behind its own Cloudflare Tunnel hostname (spec section 4, "Deployment"), nightly `pg_dump` with 30-day retention, and the runbook the author follows for first deploy, restore drills, deletion requests and key rotation.

> RULING (env files): `deploy/tutor.env.example` lists exactly the keys the process reads (`Settings.from_env`, `WebConfig.from_env`, `run_kwargs`) with empty values; the database and tunnel credentials live in their own example files (`db.env.example`, `tunnel.env.example`) so the app never sees `POSTGRES_PASSWORD` or the tunnel token. Docker `env_file` sets an empty line to `""`, which would override the image defaults, so the runbook gives a value for every key (only `TUTOR_TEST_LOGIN` stays empty).

> RULING (network): nothing is published on the host. `cloudflared` reaches `http://app:8000` over a Compose network with a fixed subnet, which is also `FORWARDED_ALLOW_IPS`, so uvicorn trusts `X-Forwarded-*` only from containers on that network.

**Files:**
- Create: `deploy/Dockerfile`, `deploy/entrypoint.sh`, `deploy/compose.prod.yml`, `deploy/tutor.env.example`, `deploy/db.env.example`, `deploy/tunnel.env.example`, `deploy/backup.sh`, `.dockerignore`
- Create: `docs/v0/runbook.md`
- Modify: `.gitignore` (add `data/` and the real `deploy/*.env` files; controller ruling 1)
- Test: `tests/unit/deploy/test_env_example.py`

**Interfaces:**
- Consumes: `Settings.from_env` (Task 18), `WebConfig.from_env` (core Task 22), `tutor.__main__.run_kwargs` and `main` (Task 21), `alembic.ini` + `alembic/` (Tasks 14–15, head `0002`), `tutor.ops.gate_report` (core Task 26), `MCP_CALLBACK_PATH` (Task 18).
- Produces: image `tutor` (entrypoint: `alembic upgrade head`, then `python -m tutor`, as uid 10001), stack `deploy/compose.prod.yml` (`db`, `app`, `cloudflared`; volumes `pgdata`, `oauth`), `deploy/backup.sh` (host cron), `docs/v0/runbook.md`.

- [ ] **Step 1: Write the failing test**

`tests/unit/deploy/test_env_example.py`:

```python
"""deploy/tutor.env.example lists exactly the keys the server reads, all empty (core Task 27)."""

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from tutor.__main__ import run_kwargs
from tutor.settings import Settings
from tutor.web.config import WebConfig

pytestmark = pytest.mark.unit

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
FULL = {
    "TUTOR_ENV": "prod",
    "TUTOR_BASE_URL": "https://tutor.example.com",
    "TUTOR_MCP_URL": "https://tutor.example.com/mcp",
    "TUTOR_SUPPORT_EMAIL": "soporte@example.com",
    "DATABASE_URL": "postgresql://tutor:secret@db:5432/tutor",
    "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "google-secret",
    "TUTOR_JWT_SIGNING_KEY": "j" * 48,
    "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
    "TUTOR_OAUTH_STORAGE_DIR": "/data/oauth",
    "TUTOR_WEB_SESSION_SECRET": "w" * 48,
    "TUTOR_PORT": "8000",
    "TUTOR_HOST": "0.0.0.0",  # noqa: S104 - the container listens on its own network only
    "FORWARDED_ALLOW_IPS": "172.30.10.0/24",
}


class Recording(Mapping[str, str]):
    """A mapping that remembers every key looked up (get() and [] both go through here)."""

    def __init__(self, data: Mapping[str, str]) -> None:
        self._data = dict(data)
        self.read: set[str] = set()

    def __getitem__(self, key: str) -> str:
        self.read.add(key)
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def _pairs(name: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in (DEPLOY / name).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        assert sep, f"not KEY=VALUE in {name}: {line}"
        pairs[key] = value
    return pairs


def test_example_lists_exactly_the_keys_the_server_reads() -> None:
    env = Recording(FULL)
    settings = Settings.from_env(env)
    WebConfig.from_env(env)
    run_kwargs(settings, env)
    assert set(_pairs("tutor.env.example")) == env.read


@pytest.mark.parametrize("name", ["tutor.env.example", "db.env.example", "tunnel.env.example"])
def test_examples_carry_no_values(name: str) -> None:
    assert all(value == "" for value in _pairs(name).values())


def test_db_and_tunnel_examples_name_their_keys() -> None:
    assert set(_pairs("db.env.example")) == {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}
    assert set(_pairs("tunnel.env.example")) == {"TUNNEL_TOKEN"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/deploy/test_env_example.py -q`
Expected: FAIL with `FileNotFoundError` for `deploy/tutor.env.example`.

- [ ] **Step 3a: Image and entrypoint**

`deploy/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1.7
# Product image (core loop v0). Build from the repository root:
#   docker build -f deploy/Dockerfile -t tutor:latest .
# The entrypoint applies migrations, then serves MCP, OAuth and the website on port 8000.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
# Keep the uv minor version equal to `uv --version` on the dev machine (uv_build is pinned <0.13).
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 tutor \
 && useradd --system --uid 10001 --gid tutor --home-dir /app --no-create-home \
      --shell /usr/sbin/nologin tutor \
 && mkdir -p /data/oauth \
 && chown tutor:tutor /data/oauth
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY --chmod=0755 deploy/entrypoint.sh /usr/local/bin/tutor-entrypoint
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TUTOR_HOST=0.0.0.0 \
    TUTOR_PORT=8000 \
    TUTOR_OAUTH_STORAGE_DIR=/data/oauth
USER tutor
EXPOSE 8000
ENTRYPOINT ["tutor-entrypoint"]
```

`deploy/entrypoint.sh` (`.gitattributes` already forces LF for `*.sh`):

```sh
#!/bin/sh
# Container entrypoint: apply migrations as the database owner, then serve (core Task 27).
set -eu
alembic -c /app/alembic.ini upgrade head
exec python -m tutor
```

`.dockerignore`:

```
.git
.github
.venv
**/__pycache__
.mypy_cache
.pytest_cache
.ruff_cache
.hypothesis
.coverage
.claude
.superpowers
.env
.env.*
build
data
docs
evals
media
spike
tests
deploy/*.env
```

- [ ] **Step 3b: Compose stack and env examples**

`deploy/compose.prod.yml`:

```yaml
# Production stack on the homelab (core loop v0; runbook: docs/v0/runbook.md).
# Run from deploy/: docker compose -f compose.prod.yml up -d --build
# Nothing is published on the host; cloudflared reaches the app over the tutor network.
name: tutor

services:
  db:
    image: postgres:16
    restart: unless-stopped
    env_file: db.env
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks: [tutor]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \"$${POSTGRES_USER}\" -d \"$${POSTGRES_DB}\""]
      interval: 10s
      timeout: 5s
      retries: 10

  app:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    image: tutor:latest
    restart: unless-stopped
    env_file: tutor.env
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - oauth:/data/oauth
    networks: [tutor]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]

  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    env_file: tunnel.env
    depends_on: [app]
    networks: [tutor]

networks:
  tutor:
    ipam:
      config:
        - subnet: 172.30.10.0/24

volumes:
  pgdata:
  oauth:
```

`deploy/tutor.env.example` (copy to `deploy/tutor.env` on the server and fill every value; see the runbook):

```
# Copy to deploy/tutor.env on the server. Every key needs a value except TUTOR_TEST_LOGIN.
# Runbook: docs/v0/runbook.md, "First deploy", step 3.

# prod
TUTOR_ENV=
# https://<tunnel hostname>, no trailing slash
TUTOR_BASE_URL=
# https://<tunnel hostname>/mcp
TUTOR_MCP_URL=
# The address learners write to for deletion and support
TUTOR_SUPPORT_EMAIL=
# postgresql://<POSTGRES_USER>:<POSTGRES_PASSWORD>@db:5432/<POSTGRES_DB> (same values as db.env)
DATABASE_URL=
# Google OAuth client (Web application) with both redirect URIs from the runbook
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
# At least 32 characters; rotating it signs every learner out of Claude
TUTOR_JWT_SIGNING_KEY=
# A Fernet key; rotating it forgets the OAuth store (learners reconnect)
TUTOR_OAUTH_STORAGE_KEY=
# /data/oauth (the oauth volume)
TUTOR_OAUTH_STORAGE_DIR=
# At least 32 random characters (kept for the beta; v0 web sessions use random tokens)
TUTOR_WEB_SESSION_SECRET=
# 8000
TUTOR_PORT=
# 0.0.0.0 (inside the container)
TUTOR_HOST=
# 172.30.10.0/24 (the compose network; only cloudflared sends X-Forwarded-*)
FORWARDED_ALLOW_IPS=
# Must stay empty in production
TUTOR_TEST_LOGIN=
```

`deploy/db.env.example`:

```
# Copy to deploy/db.env. POSTGRES_USER becomes the owner role DATABASE_URL uses (a superuser,
# which the read-only gate report needs to see every learner's rows).
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=
```

`deploy/tunnel.env.example`:

```
# Copy to deploy/tunnel.env. The token of this deployment's own Cloudflare Tunnel (not the spike's).
TUNNEL_TOKEN=
```

In `.gitignore`, add under `# Secrets`:

```
deploy/*.env
```

and add a new section at the end:

```
# Local OAuth store (default TUTOR_OAUTH_STORAGE_DIR=data/oauth) and other runtime data
data/
```

- [ ] **Step 3c: Nightly backup**

`deploy/backup.sh`:

```sh
#!/bin/sh
# Nightly logical backup of the tutor database with 30-day retention (spec section 4).
# Host cron (crontab -e as the deploy user):
#   30 3 * * * /opt/tutor/deploy/backup.sh >> /var/log/tutor-backup.log 2>&1
set -eu
cd "$(dirname "$0")"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/tutor}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
partial="$BACKUP_DIR/.tutor-$stamp.dump.partial"
docker compose -f compose.prod.yml exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$partial"
mv "$partial" "$BACKUP_DIR/tutor-$stamp.dump"
find "$BACKUP_DIR" -name 'tutor-*.dump' -type f -mtime +"$RETENTION_DAYS" -delete
echo "backup ok: $BACKUP_DIR/tutor-$stamp.dump"
```

- [ ] **Step 3d: The runbook**

`docs/v0/runbook.md`:

````markdown
# Core loop v0 runbook

The production stack for the core loop v0 (spec `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md`, section 4). One process serves MCP (`/mcp`), the OAuth proxy and the website. Commands run on the homelab from `/opt/tutor/deploy` unless stated otherwise. Placeholders in angle brackets are values you choose once and keep here.

| Thing | Value |
| --- | --- |
| Public hostname | `https://<host>` (its own Cloudflare Tunnel, separate from the spike) |
| Compose project | `tutor` (`deploy/compose.prod.yml`) |
| Volumes | `tutor_pgdata` (Postgres), `tutor_oauth` (encrypted OAuth store; losing it only forces reconnects) |
| Backups | `/var/backups/tutor/tutor-<UTC stamp>.dump`, nightly 03:30, 30 days |

## Google OAuth client

1. Google Cloud console → APIs & Services → Credentials → Create OAuth client ID → Web application.
2. Authorized redirect URIs, both required (plan ruling 1):
   - `https://<host>/auth/callback` (website login, Authlib)
   - `https://<host>/oauth/callback` (MCP OAuth proxy, `GoogleProvider(redirect_path="/oauth/callback")`)
3. OAuth consent screen: External, publishing status Testing; add the author and each invited tester as test users. Scopes: `openid`, `email`, `profile`.
4. Copy the client id and secret into `tutor.env`.

## Cloudflare Tunnel

1. Zero Trust → Networks → Tunnels → Create a tunnel (Cloudflared) named `tutor-v0`.
2. Public hostname `<host>` → service `HTTP`, URL `app:8000`.
3. Copy the tunnel token into `tunnel.env` as `TUNNEL_TOKEN`.
4. After the first `docker compose pull`, pin the image: replace `cloudflare/cloudflared:latest` in `compose.prod.yml` with the digest `docker image inspect cloudflare/cloudflared:latest --format '{{index .RepoDigests 0}}'` prints, and commit it.

## First deploy

1. Install Docker Engine with the Compose plugin; create the deploy user and `/opt/tutor`.
2. `git clone <repo> /opt/tutor && cd /opt/tutor && git checkout <release tag>`.
3. In `deploy/`, copy the three examples and fill them:
   - `cp tutor.env.example tutor.env && cp db.env.example db.env && cp tunnel.env.example tunnel.env && chmod 600 *.env`.
   - `db.env`: `POSTGRES_USER=tutor`, `POSTGRES_DB=tutor`, `POSTGRES_PASSWORD=` a 32-character random string.
   - `tutor.env`: `TUTOR_ENV=prod`, `TUTOR_BASE_URL=https://<host>`, `TUTOR_MCP_URL=https://<host>/mcp`, `TUTOR_SUPPORT_EMAIL=<address>`, `DATABASE_URL=postgresql://tutor:<POSTGRES_PASSWORD>@db:5432/tutor`, the Google id and secret, `TUTOR_OAUTH_STORAGE_DIR=/data/oauth`, `TUTOR_PORT=8000`, `TUTOR_HOST=0.0.0.0`, `FORWARDED_ALLOW_IPS=172.30.10.0/24`, `TUTOR_TEST_LOGIN=` (empty).
   - Secrets: `openssl rand -base64 48` for `TUTOR_JWT_SIGNING_KEY` and `TUTOR_WEB_SESSION_SECRET`; for `TUTOR_OAUTH_STORAGE_KEY` build first (step 4) and run `docker compose -f compose.prod.yml run --rm --no-deps --entrypoint python app -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
4. `docker compose -f compose.prod.yml build`.
5. `docker compose -f compose.prod.yml up -d`, then `docker compose -f compose.prod.yml logs app`: expect Alembic's `Running upgrade  -> 0001` and `0001 -> 0002`, then uvicorn listening on `0.0.0.0:8000`.
6. Smoke checks from any machine:
   - `curl -s https://<host>/.well-known/oauth-authorization-server` returns JSON whose `authorization_endpoint` is `https://<host>/authorize`.
   - `curl -si https://<host>/mcp -X POST` returns `401` with a `WWW-Authenticate` header naming `https://<host>/.well-known/oauth-protected-resource/mcp`.
   - `https://<host>/login` shows Entrar with the privacy note; "Entrar con Google" signs in and lands on Perfil.
7. Install the backup cron (see Backups) and run `./backup.sh` once by hand.

## Upgrades and migrations

- Deploy a new release: `git fetch && git checkout <tag>`, then `docker compose -f compose.prod.yml up -d --build`. The entrypoint runs `alembic upgrade head` before serving, as the owner role.
- Take a backup (`./backup.sh`) before any release that adds a migration.
- Inspect: `docker compose -f compose.prod.yml run --rm --no-deps --entrypoint alembic app -c /app/alembic.ini current`.
- Never edit an applied migration; a fix is a new revision (`0003_…`). Downgrade only to recover, and only right after a backup: `… --entrypoint alembic app -c /app/alembic.ini downgrade -1`.

## Backups and the restore drill

- Cron on the host, as the deploy user: `30 3 * * * /opt/tutor/deploy/backup.sh >> /var/log/tutor-backup.log 2>&1`.
- Restore drill (monthly, and once before inviting testers; record it in `docs/v0/acceptance.md`):
  1. `latest=$(ls -1 /var/backups/tutor/tutor-*.dump | tail -n 1)`
  2. `docker compose -f compose.prod.yml exec -T db sh -c 'createdb -U "$POSTGRES_USER" tutor_restore'`
  3. `docker compose -f compose.prod.yml exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d tutor_restore --no-owner' < "$latest"`
  4. Compare with live: run `SELECT count(*) FROM users; SELECT count(*) FROM sessions; SELECT max(started_at) FROM sessions;` with `psql -U "$POSTGRES_USER" -d tutor_restore` and with `-d tutor`; counts match up to sessions started after the dump.
  5. `docker compose -f compose.prod.yml exec -T db sh -c 'dropdb -U "$POSTGRES_USER" tutor_restore'`.
- Full restore (disaster): stop `app`, `dropdb tutor`, `createdb tutor`, `pg_restore -d tutor`, start `app`. The `tutor_app` role is cluster-wide and survives; migration 0001 recreates it if the cluster is new.

## Gate report

Inside the app container (it holds the owner `DATABASE_URL`; the report opens read-only transactions):

```sh
docker compose -f compose.prod.yml cp /opt/tutor/labels.json app:/tmp/labels.json
docker compose -f compose.prod.yml exec app python -m tutor.ops.gate_report --from 2026-11-16 --to 2026-12-04 --labels /tmp/labels.json --author author
```

`labels.json` maps user hashes to names (`{"a1b2c3d4e5f6": "author"}`). A hash is the first 12 hex characters of SHA-256 of the user id, the same `user_hash` the logs show: `SELECT id FROM users` as the owner, then `python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:12])" <id>`. Keep `labels.json` out of the repository.

## User deletion (spec 13)

On a learner's written request (from the email their Google account uses):

1. Ask them to remove the "English Tutor" connector in Claude first; otherwise their next tool call recreates an empty account from the same Google `sub`.
2. Find the id: `SELECT id FROM users WHERE email = '<their email>';` (as the owner, in `psql`).
3. Run, as the owner (`docker compose -f compose.prod.yml exec db psql -U tutor -d tutor`):

```sql
\set uid '<user id>'
BEGIN;
-- Keep a pseudonymous trace: the same 12-hex user_hash the logs use.
UPDATE audit_log
   SET meta = meta || jsonb_build_object(
         'deleted_user_hash', left(encode(sha256(convert_to(:'uid', 'UTF8')), 'hex'), 12))
 WHERE user_id = :'uid';
INSERT INTO audit_log (user_id, event, meta, at)
VALUES (NULL, 'user_deleted',
        jsonb_build_object('user_hash', left(encode(sha256(convert_to(:'uid', 'UTF8')), 'hex'), 12)),
        now());
-- Cascades to profiles, plans, plan_items, sessions, session_metrics, session_errors,
-- glossary_items, review_states, review_logs and web_sessions; audit_log.user_id becomes NULL.
DELETE FROM users WHERE id = :'uid';
COMMIT;
```

4. Check: `SELECT count(*) FROM sessions WHERE user_id = :'uid';` returns 0. Reply to the learner. Their refresh tokens in the OAuth store expire within 30 days (plan ruling 8); to cut them at once, rotate `TUTOR_OAUTH_STORAGE_KEY` (below), which signs everyone out of Claude.

## Rotating keys

| Key | How | Effect |
| --- | --- | --- |
| `GOOGLE_CLIENT_SECRET` | Add a new secret in the Google console, put it in `tutor.env`, `docker compose -f compose.prod.yml up -d app`, then delete the old secret | None for learners |
| `TUTOR_JWT_SIGNING_KEY` | New `openssl rand -base64 48`, restart `app` | Every Claude connection must reconnect |
| `TUTOR_OAUTH_STORAGE_KEY` | New Fernet key, restart `app`; optionally empty the volume: `docker compose -f compose.prod.yml run --rm --no-deps --entrypoint sh app -c 'rm -rf /data/oauth/*'` | Old entries read as misses; every Claude connection reconnects |
| `TUTOR_WEB_SESSION_SECRET` | New random value, restart `app` | None in v0 (web sessions are random tokens); end all web sessions with `DELETE FROM web_sessions;` |
| `POSTGRES_PASSWORD` | `ALTER ROLE tutor PASSWORD '<new>'` in `psql`, update `db.env` and `DATABASE_URL` in `tutor.env`, restart `app` | None |
| `TUNNEL_TOKEN` | Rotate in Zero Trust, update `tunnel.env`, restart `cloudflared` | A few seconds offline |

## Logs

`docker compose -f compose.prod.yml logs app` prints one JSON line per tool call (`tutor.mcp.calls`) and per HTTP request (`tutor.http`) with `user_hash`, never tokens, codes, emails or learner text. uvicorn's access log is off because it would print `/oauth/callback?code=…`.
````

- [ ] **Step 4: Run the test and verify the image by hand**

Run: `uv run pytest tests/unit/deploy/test_env_example.py -q`
Expected: PASS (5 tests).

Manual check (needs Docker; record the output in the ledger):

```bash
docker build -f deploy/Dockerfile -t tutor:dev .
docker run --rm --entrypoint id tutor:dev -u
docker run --rm --entrypoint python tutor:dev -c "import tutor.app, tutor.ops.gate_report; from tutor.content import load_track; print(len(load_track()))"
docker run --rm --entrypoint alembic tutor:dev -c /app/alembic.ini heads
docker run --rm --entrypoint sh tutor:dev -c "test -w /data/oauth && echo writable"
```

Expected: the build succeeds; then `10001`; then `24`; then `0002 (head)`; then `writable`. If the `ghcr.io/astral-sh/uv:0.12` tag does not exist, set `UV_IMAGE` to the minor version `uv --version` prints and note it in the ledger.

- [ ] **Step 5: Run the gate and commit**

Run: `uv run just fmt` then `uv run just check`
Expected: PASS.

```bash
git add deploy .dockerignore .gitignore docs/v0/runbook.md tests/unit/deploy/test_env_example.py
git commit -m "build(deploy): Docker image, production compose stack, nightly backup and v0 runbook" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 28: Acceptance: manual checks, spec amendments, ADR 0002

v0 is done when the section 16 v0 row holds and the manual checks are recorded under `docs/v0/` (spec section 15). This task runs the checks on the deployed stack, records them, writes the spec's Amendments section, accepts ADR 0002 and runs the `phase-auditor`.

**Files:**
- Create: `docs/v0/acceptance.md`
- Modify: `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md` (Status line; new "Amendments" section at the end)
- Modify: `docs/adr/0002-mcp-auth-via-fastmcp-oauth-proxy.md` (Status line)

**Interfaces:**
- Consumes: the deployed stack (core Task 27), `uv run just inspector`, `python -m tutor.ops.gate_report` (core Task 26), the runbook's restore drill, the `phase-auditor` agent.
- Produces: `docs/v0/acceptance.md` (filled), the spec amendments, ADR 0002 accepted.

- [ ] **Step 1: Preconditions (the author)**

1. Core Tasks 1–27 are committed and `uv run just check` passes on `feat/core-loop-v0`; the stack is deployed from that commit (runbook "First deploy").
2. The author has pasted the spec section 17 edits into `docs/requirements.md` (hook-protected, so only the author can): the section 16 v0 row (17.1), the gantt and gate dates (17.2), the section 4 Auth row (17.3), the section 5 login note (17.4), the section 6 table and column additions (17.5), the section 7 tool changes (17.6), the section 10 declined state (17.7) and the section 3 caps note (17.8). Paste 17.5 as amended (Step 3, amendments 3, 4 and 7): `users.mcp_first_seen_at` instead of a `mcp_clients_seen` table, `timezone` on `users` rather than `profiles`, and `review_logs.state_before`. Until 17.1 is pasted, the `phase-auditor` judges the old row ("a plain page… No dashboard") and will flag the website as out of scope.
3. The author has set the `CLAUDE.md` "Current phase" line to `Core loop v0 (section 16)`; the `phase-auditor` reads it.

- [ ] **Step 2: Write the acceptance record and fill it while running the checks**

`docs/v0/acceptance.md`:

```markdown
# Core loop v0 — acceptance

Status: in progress
Date(s): <first check> → <last check>
Spec: `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md` (section 15 "Manual" row and section 16)
Deployed commit: <git rev-parse --short HEAD on the server>
Host: https://<host>

## 1. MCP Inspector auth check

Procedure: `uv run just inspector`, transport Streamable HTTP, URL `https://<host>/mcp`, "Open Auth Settings" → "Quick OAuth Flow".

| Check | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Unauthenticated `POST /mcp` | 401 with `resource_metadata` pointing to `/.well-known/oauth-protected-resource/mcp` | | |
| Authorization-server metadata | `authorization_endpoint` `https://<host>/authorize`; `code_challenge_methods_supported` `["S256"]` | | |
| Dynamic client registration | `/register` returns a client id | | |
| Google consent and callback | Google redirects to `https://<host>/oauth/callback`, then to the Inspector | | |
| Token works | `tools/list` shows exactly `get_profile`, `save_profile`, `start_lesson`, `record_review`, `save_glossary`, `end_session` | | |
| Refresh | "Refresh token" in the Inspector issues a new access token; the old refresh token is refused (rotation) | | |
| Audit | `SELECT event FROM audit_log ORDER BY at DESC LIMIT 5` shows `user_created`, `mcp_first_use` for the Inspector's account | | |

## 2. Full session, Free Claude account, claude.ai web

| Step | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Add the connector (Settings → Connectors → Add custom connector, URL `https://<host>/mcp`) | Google sign-in, then "connected" | | |
| `/start-lesson` in a new chat | `get_profile` says `onboarding_needed`; Claude asks the five questions one at a time | | |
| Onboarding | `save_profile` called once; Perfil on the web shows the same answers and the plan | | |
| Lesson | `start_lesson` → warm-up with 5 phrases → scenario → feedback | | |
| Glossary | Claude proposes items; kept ones saved `confirmed`, dropped ones `declined` | | |
| `end_session` | Called once; `summary_text` read once; Sesiones shows the session with metrics | | |
| Next session | Confirmed items come back as due reviews after their due date (`record_review` called) | | |
| Website | Inicio, Perfil, Sesiones (detail), Glosario (edit, CSV), Conectar render; nav shows only those five | | |

## 3. Full session, Free Claude account, mobile voice

| Step | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Voice lesson on the Claude mobile app | Tools fire in voice (spec 16, spike #1 outcome); turns ≤ 40 words | | |
| `end_session` in voice | Session `closed` (or `incomplete` with a stated reason) | | |
| `user_words_per_min` | Recorded on Sesiones | | |

## 4. Gate report

Command (runbook "Gate report"), window = the dates of sections 2–3:

<paste the report output here>

## 5. Restore drill

| Step | Result |
| --- | --- |
| Latest dump file and size | |
| `pg_restore` into `tutor_restore` | |
| Row counts (users, sessions) restore vs live | |
| `tutor_restore` dropped | |

## 6. Phase audit

<paste the phase-auditor output verbatim>

## Verdict

<one paragraph: v0 accepted or not, and what blocks the tester invitations if not>
```

Run each check, fill in Result and Evidence (file name, screenshot name or log line; never tokens, codes or emails), and set "Status: complete" when every row is filled. A failed row is recorded as failed with its cause; fix it in a new task before the verdict.

- [ ] **Step 3: Write the spec amendments**

In `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md`, change the Status line to:

```markdown
- **Status:** approved 2026-10-04; amended during planning and implementation (see "Amendments")
```

and append at the end of the file:

```markdown
## Amendments

Decided while planning and building v0 (plan `docs/superpowers/plans/2026-10-04-core-loop-v0-*.md`), with evidence. Each states what changed against the sections above.

1. **MCP proxy callback is `/oauth/callback`** (sections 4, 13). The website's Google login owns `/auth/callback`, which is also FastMCP's default proxy path, so `GoogleProvider(redirect_path="/oauth/callback")`. The Google OAuth client registers both URIs.
2. **A path dispatcher instead of mounting** (section 4). Both apps own root paths, so `tutor.app.PathDispatch` sends `/mcp`, `/authorize`, `/token`, `/register`, `/consent`, `/oauth/callback` and `/.well-known/*` to the MCP app and everything else to the website. Each keeps its own middleware, so the web CSP and `no-store` never touch MCP. Only the MCP app's lifespan runs (the website has none).
3. **`users.mcp_first_seen_at` replaces `mcp_clients_seen`** (sections 5, 12, 13). The token's `client_id` is the Google `sub`, so the OAuth client is not visible to tools. The audit event is `mcp_first_use`; Conectar's "connected" check is "has any session".
4. **UI preferences and timezone live on `users`** (sections 5, 6.1). `lang`, `timezone`, `reduce_motion`, `install_prompt_dismissed_at`, `last_celebrated_session_id`, `deletion_requested_at`, `email_weekly` and `email_reminders` are `users` columns, read on every web request before onboarding; `profiles` has no `timezone` or `ui_lang`. `save_profile` writes `users.timezone`.
5. **Row-level security through `SET LOCAL ROLE tutor_app`** (section 4). One owner `DATABASE_URL`; every transaction runs `SET LOCAL ROLE tutor_app` and sets `app.user_id`, `app.google_sub` and `app.web_session` with `set_config(…, true)`. `tutor_app` is a NOLOGIN role created by migration 0001. User resolution by Google `sub` uses a second policy branch on `users` (`google_sub = app.google_sub`) instead of a `SECURITY DEFINER` function.
6. **Leech rule** (section 10.2). "A third appearance within 30 days" means `seen_count` reaches 3 while `created_at` is within the last 30 days.
7. **The rating-4 upgrade replays FSRS** (section 10.3). `review_logs` stores the state before each review (`state_before` JSONB); at `end_session` an upgraded item's state is recomputed from it with rating 4.
8. **Refresh tokens last 30 days** (section 13, ADR 0002): `fallback_refresh_token_expiry_seconds = 30 × 24 × 3600` (the library default is one year).
9. **Archived glossary items** are never produced in v0 and count as `confirmed` in the save rules and the gate report.
10. **The website hides `declined` items** (section 12). The dashboard's glossary status has no `declined` value; declined rows never reach the Glosario page.
11. **Postponed dashboard pages are not registered** (section 12). The website registers no `/pricing`, `/webhooks/stripe`, `/billing/*`, `/admin/*`, `/app/plan`, `/app/progress`, `/app/reports` or `/app/account`. Navigation (sidebar and tab bar) shows Inicio, Perfil, Sesiones, Glosario and Conectar; the "Más" menu, plan chip, Free meter and admin link are removed.
12. **MCP tools are plain `def` functions** (D7, section 4). FastMCP 4 runs sync tools in a worker thread with context variables propagated, which is what `anyio.to_thread.run_sync` was for.
13. **Turn and evidence-string caps are enforced by the tool schema and the 64 KB body limit** (section 5), not by database `CHECK`s, because they live inside `raw_evidence` JSONB; `raw_evidence` keeps a 64 KB `CHECK` as a backstop.
14. **The OAuth store is a `FileTreeStore` wrapped in Fernet encryption** (section 4, ADR 0002), not a `DiskStore`: `diskcache` carries CVE-2025-69872 with no fixed release, which `pip-audit` rejects.
15. **`web_sessions.token_hash` is the capability** (section 5). The policy is `token_hash = app.web_session OR user_id = app.user_id`: the session middleware loads a session by its SHA-256 token hash before it knows the user, and an anonymous session's OAuth state is visible only to the holder of its cookie.
16. **`save_glossary` also reports `promoted`** (section 8.1): items moved from `provisional` or `declined` to `confirmed`.
17. **Free usage has no caps in v0** (sections 3.2, 12). The website's usage port returns real counts with an "uncapped" sentinel, so no limit banner and no "N of M" counter appears.
18. **Inicio's week trail without a calendar** (section 12). Plan-lite has no dates, so a day is "planned" only when the learner closed an on-plan session that day, or today when a plan item is pending. No other date is invented, so no day reads "sin sesión".
19. **Perfil carries the language switch, logout and the deletion contact** (sections 6.3, 12). Cuenta is postponed and the narrow layout hides both controls. Entrar and the privacy page say what is stored, that lessons pass through the learner's LLM vendor, and that copies and deletion are by email to the author.
20. **A first login without a profile lands on Perfil**, whatever `next` says (section 6.3). Field errors use the domain's error codes for the es-MX messages; `save_profile` still decides validity.
21. **Request size and request log on the website too** (sections 5, 13). The website sits behind the same 64 KB body limit as MCP, and every HTTP request writes one JSON log line (method, path without query or ids, status, latency, `user_hash`).
22. **`glossary_saved` audit event; the confirmation rate survives purges** (sections 5, 10.4, 13, 14). Every `save_glossary` writes one `glossary_saved` audit row: the session id, the requested status and, per applied item, its id, action (`insert`, `reinforce`, `promote`, `set_status`) and resulting status; ids and enums only, never text. The gate report replays these rows to each item's final status, so provisional items purged after 7 days (section 9.4) stay in the denominator. The section 13 audit events are `user_created`, `web_login`, `mcp_first_use`, `profile_saved`, `plan_generated`, `session_closed` and `glossary_saved`.
23. **Deletion script** (section 13). The documented SQL (runbook) writes the 12-hex user hash into `audit_log.meta` and a `user_deleted` row before deleting the `users` row; other user tables cascade and `audit_log.user_id` becomes NULL.
24. **Environment variable names** (section 13). The secrets are `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `TUTOR_JWT_SIGNING_KEY`, `TUTOR_OAUTH_STORAGE_KEY`, `TUTOR_WEB_SESSION_SECRET` and `DATABASE_URL`; the website also reads `TUTOR_MCP_URL` and `TUTOR_SUPPORT_EMAIL` (`deploy/tutor.env.example` lists every key).
```

- [ ] **Step 4: Accept ADR 0002**

In `docs/adr/0002-mcp-auth-via-fastmcp-oauth-proxy.md`, replace the Status line with:

```markdown
- **Status:** accepted (core loop v0 acceptance, `docs/v0/acceptance.md`; compliance table verified in plan Task 18)
```

- [ ] **Step 5: Run the phase audit and record it**

Dispatch the `phase-auditor` agent with: "Phase: Core loop v0. Judge against the requirements section 16 v0 row as amended by spec section 17.1 and the spec's Amendments section; the spec's section 3.1 lists what v0 includes (website pages and the gate report are in scope). Report OUT OF SCOPE items for anything in the spec's section 3.2." Paste its output verbatim into section 6 of `docs/v0/acceptance.md`. Every MISSING or PARTIAL line either gets a follow-up task before the verdict or a one-line reason in the Verdict paragraph.

- [ ] **Step 6: Run the gate and commit**

Run: `uv run just check`
Expected: PASS.

```bash
git add docs/v0/acceptance.md docs/superpowers/specs/2026-10-04-core-loop-v0-design.md docs/adr/0002-mcp-auth-via-fastmcp-oauth-proxy.md
git commit -m "docs(v0): acceptance record, spec amendments and ADR 0002 accepted" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Then remind the author, in the hand-off message, of anything in Step 1 items 2–3 still undone, and that tester invitations wait for "Status: complete" in the acceptance record.

---

## Appendix: notes for the controller

Written by the plan-part writers and kept for the controller and reviewers. They are not task requirements; where a note conflicts with a task, the task wins.

### Notes from the writer of Tasks 22–28

> CONTRACT NOTE (additions, no rename): `build_app(settings, *, engine=None, web_config=None)` gains the
> keyword `web_config: WebConfig | None` (default `WebConfig.from_env(os.environ)`), so tests never depend on
> the process environment. `tutor.app` also exports `RequestLog` and `log_path` (controller ruling 3), and
> `tutor.web.ports` exports `UNCAPPED`. `tutor.web.pg` additionally exports `FREE`, `V0Subscriptions`,
> `DisabledBilling`, `BillingDisabled`, `V0Settings`. Task 25 adds `tutor.web.profile` (`ProfilePort`,
> `ServicesProfiles`, `MemoryProfiles`, `install_profiles`, `get_profiles`, `optional_profiles`,
> `PROFILE_PATH`). No contract name changes.

- CONTRACT NOTE: `build_app` gains keyword `web_config: WebConfig | None = None`; additions only (`RequestLog`, `log_path`, `UNCAPPED`, `FREE`, `V0Subscriptions`, `DisabledBilling`, `BillingDisabled`, `V0Settings`, `tutor.web.profile.*`, `tests/web_pg_support.py`). `build_app` still returns `PathDispatch`; both branches are now `RequestLog(BodySizeGuard(...))`, so core Task 21's `test_build_app_wires_auth_and_dispatch` is replaced in Task 23.
- Task 26 changes `testpaths` to `["tests", "evals"]` (not `pythonpath`/`mypy_path`) so `evals/fidelity` imports work; tooling tests are unmarked and run in `check`.
- Dashboard plan bugs ruled on: `/auth/logout` is unclassified in dashboard Task 29's sweep (added to `PUBLIC`); the `/app/account` stub from dashboard Task 12 would survive in v0 (Task 25 deletes it); privacy copy promised self-service deletion (Task 25 fixes).
- Dependencies: Task 22 moves `httpx` from dev to runtime (Authlib needs it) and skips Playwright and `uvicorn`.
- Resolved by the controller in review: the confirmation rate replays the `glossary_saved` audit rows (core Tasks 10, 13, 26), so purged provisional items stay in the denominator; no caveat line.
- Task 28's amendment list covers header rulings 1–13, FileTreeStore, the `web_sessions` policy, the review's additions (promoted, google_sub branch, deletion script), this part's rulings (17–21), the `glossary_saved` event (22) and the env names (24); add any ruling other parts made that is missing.
- Manual steps that need the author or Docker: Task 25 demo check, Task 27 `docker build`, all of Task 28.
