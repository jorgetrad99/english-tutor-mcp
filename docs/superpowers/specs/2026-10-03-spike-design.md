# Spike design: voice-mode tool calls and `end_session` reliability

Status: draft, awaiting author approval
Date: 2026-10-03 (spike runs 2026-10-05 → 2026-10-11)
Scope: requirements section 15 (blocking unknowns 1 and 2, plus two "also measured" items) and the Spike row of section 16. Nothing from the core loop or later phases.

## 1. Goal and success

Answer, with evidence, the two blocking questions of section 15:

1. Do custom-connector tool calls run inside Claude's mobile voice mode?
2. Does Claude reliably call `end_session` with a valid payload at the end of an unscripted 15-minute conversation?

Also measured once: evidence fidelity (section 11) and grader reliability (section 9). Cross-client consistency is deferred (section 10 below).

The spike is done when the four experiment files in `docs/spike/` are `done`, the `phase-auditor` agent has run, and the go/no-go on the MCP-first architecture is recorded in `docs/adr/0001-mcp-first-go-no-go.md`.

## 2. Decisions at a glance

| Topic | Decision |
| --- | --- |
| Tools | `get_profile` (stub profile + spike-only `session_id`) and `end_session` (production schema verbatim) |
| Storage | Append-only JSONL files; no Postgres |
| Auth | Real OAuth: FastMCP 4 `GoogleProvider` (OAuth proxy, DCR toward Claude, Google login). Fallbacks in order: FastMCP `GitHubProvider`, then WorkOS AuthKit |
| Hosting | Cloudflare named tunnel on a subdomain of the author's domain, `cloudflared` on the author's Windows machine |
| Validity metric | Per run, "valid by final call"; "valid on first call" reported alongside |
| Testers | Author only, 25 runs; "varied users" gap stated in the write-up |
| Also measured | Evidence fidelity and grader reliability now; cross-client consistency deferred to the core-loop phase |
| Code | Throwaway, in `spike/` as its own uv project; spike data (redacted) is kept |

## 3. Spike server

### 3.1 Location and isolation

- `spike/` is a separate uv project (`spike/pyproject.toml`) depending on `fastmcp` 4.x and `uvicorn`. FastMCP never enters the product's dependencies, and `src/tutor` is not touched.
- The root `just check` is unaffected: mypy and pytest only cover `src`, `tests`, `evals`. Root `ruff check .` does reach `spike/`; spike code stays lint-clean rather than being excluded.
- No FastAPI: FastMCP's own ASGI app served by uvicorn is enough. The logging middleware is plain Starlette/ASGI.
- At the go/no-go the code is deleted from `main` (kept on a `spike/2026-10` tag for reference). Only redacted data and the write-ups remain.

### 3.2 Tools

Both tools follow the MCP rules in CLAUDE.md: closed enums, unknown fields rejected, `title`/`description` on every field, description ≤ 120 words, `response_rules` in every result.

**`get_profile`** — no input.
Returns a hardcoded profile: `display_name`, `level: "B1+"`, `domain: "software"`, `native_language: "es"`, `plan_summary: "spike: free practice"`, `streak: 0`, `open_session: false`, `provisional_items: 0`, plus the spike-only field `session_id` (fresh UUIDv4 per call, recorded as issued to the caller's tester label).
`response_rules`: greet the learner by name in one sentence and ask which situation they want to practise; do not read the profile aloud.

**`end_session`** — the section 7 schema verbatim: `session_id`, `user_turns[]` (≥ 1), `errors[]` (`said`, `correct`, `category` ∈ grammar|lexis|word_order|register|other), `chunks_used[]`, `task_result` ∈ achieved|partial|not_achieved, `hints_given`, `cefr_estimate` (`speaking` ∈ B1|B1+|B2|B2+|C1, `confidence` ∈ low|medium|high, `evidence[]`), `confidence_1_5`, `assistant_words_estimate?`.
Server behaviour:
- Validates with the strict model (`extra="forbid"` at every level) and checks that `session_id` was issued by `get_profile` to the same tester. Unknown `session_id` → error telling the model to call `get_profile` first.
- Applies the `errors[].said` rule (§8.3) and returns `accepted: true`, `errors_kept`, `errors_rejected`.
- On a validation error returns an MCP tool error listing the bad fields, with `response_rules`: fix the listed fields and call `end_session` again with the same `session_id`; do not mention this to the learner.
- On success `response_rules`: tell the learner in one sentence that the session is saved; do not read numbers aloud.
- Repeated calls for the same `session_id` are all logged and flagged as repeats (a retry is evidence), never deduplicated away.

**Deliberately left out:** `start_lesson`, chunks of the day, scenarios from the server, plans, glossary, FSRS, metrics computation, streaks, the 4-line summary, MCP prompts (the `/end` prompt is only built if decision rule 9.2 triggers the extension), Postgres, the REST API, the dashboard.

### 3.3 Server instructions (frozen v1)

Sent at `initialize`. This text is part of what experiment 2 measures, so it is frozen before run 1; any change starts a new run series with its own numbers.

> You are an English speaking tutor for a Spanish-speaking software professional. Speak English unless the learner asks otherwise. In voice sessions answer in 1–3 sentences.
> At the start of every practice session call `get_profile` and keep its `session_id`.
> Run the session in this order. Open: greet the learner and ask which work situation they want to practise. Scenario, about 10–12 minutes: play the other person, stay in character, keep your turns short, give at most 3 hints, and do not correct the learner during the scenario. Feedback: step out of character and give the 2–3 most useful corrections and one thing done well.
> At the end of the session, including when the learner says they have to go, call `end_session` with the `session_id` from `get_profile` and the evidence from this conversation: the learner's turns as they said them, their errors quoted exactly, and your level estimate with evidence. Never invent turns, errors, counts or levels that are not in the conversation.
> No markdown, lists or headings while in conversation.

## 4. Auth

- FastMCP 4 `GoogleProvider` (an `OAuthProxy`): Claude sees a standard MCP authorization server (401 with `WWW-Authenticate` → RFC 9728 protected-resource metadata → RFC 8414 metadata → DCR → PKCE S256); the user logs in with Google upstream.
- `allowed_client_redirect_uris = ["https://claude.ai/api/mcp/auth_callback"]`; scopes `openid` + `email` only.
- Identity: the Google `email` claim maps to a tester label through an allowlist in the gitignored `spike/.env`. Logs store the label, never the email. Non-allowlisted callers get a tool error and nothing is recorded as a run.
- The Free and the Pro Claude account log in to the connector with **different Google accounts** (`author-free`, `author-pro`), so the plan is visible in the server log. `runs.csv` is the backup.
- Persistence: FastMCP's encrypted disk client storage plus a fixed `jwt_signing_key`, so a server restart does not force a reconnect. Verified during setup by restarting the server and making a tool call.
- Google Cloud setup (once): project, OAuth web client with redirect URI `https://<spike-host>/auth/callback`, consent screen External in Testing mode with the two author Google accounts as test users. With only `openid`/`email` scopes, Testing-mode refresh tokens do not expire after 7 days.
- Implementation starts by checking the current `GoogleProvider` constructor, well-known route mounting and `base_url`/`mcp_path` handling in Context7 (`/websites/gofastmcp`). Setup acceptance: the protected-resource `resource` equals the connector URL exactly, path included.
- Fallback order if the proxy fails with Claude web or mobile: `GitHubProvider` (same proxy, GitHub OAuth App), then WorkOS AuthKit. A switch is recorded in the affected experiment files, never made silently.

## 5. Hosting

- Cloudflare named tunnel: `winget install Cloudflare.cloudflared`, then `cloudflared tunnel login`, `tunnel create tutor-spike`, `tunnel route dns tutor-spike <spike-host>`, with `<spike-host>` a subdomain of the author's domain. Connector URL: `https://<spike-host>/mcp`.
- Cloudflare settings for that hostname: proxied A record (connectors are IPv4-only), no caching, no Cloudflare Access, and a WAF skip rule (or Bot Fight Mode off) so challenges never hit Claude's egress range `160.79.104.0/21`.
- Streaming: prefer FastMCP's JSON-response mode for Streamable HTTP if 4.x supports it (verify in Context7); otherwise send SSE keepalives so Cloudflare's ~100 s idle timeout never cuts a stream.
- Setup acceptance: from Claude web, add the connector on both accounts, complete OAuth, call both tools; then the same from the phone.

**Pre-run checklist** (every run): laptop on mains power with sleep disabled; server and tunnel running; `curl https://<spike-host>/.well-known/oauth-protected-resource/mcp` returns 200; only this connector enabled in the chat; model shown by the client noted.

## 6. Instrumentation

### 6.1 Server log

One JSONL line per HTTP request, written by an outer ASGI middleware **before** SDK validation, to the gitignored `spike/data/raw/calls-YYYY-MM-DD.jsonl`:

```json
{
  "ts_in": "2026-10-06T14:03:11.204Z", "ts_out": "...", "latency_ms": 41,
  "request_id": "uuid", "server_sha": "abc1234", "tester": "author-free",
  "http": {"method": "POST", "path": "/mcp", "status": 200, "user_agent": "...",
           "mcp_session_id": "...", "mcp_protocol_version": "..."},
  "rpc": {"id": 7, "method": "tools/call", "tool": "end_session", "client_info": null,
          "params_raw": {}, "result_raw": {}, "error_raw": null},
  "end_session": {"valid": true, "errors_field": [], "session_id_known": true,
                  "repeat_of": null, "said_total": 4, "said_rejected": 1}
}
```

- `tester` is joined from the tool context to the request by `request_id`; `client_info` comes from `initialize` and is carried per MCP session.
- `tools/list` results are logged, so the exact schema Claude saw is on record.
- Auth routes (`/authorize`, `/token`, `/register`, `/auth/callback`, `.well-known/*`) are logged as method, path, status and latency only. Never logged anywhere: the `Authorization` header, tokens, authorization codes, client secrets, emails.

### 6.2 Run sheet

`spike/data/raw/runs.csv`, one row per run, filled by the author:
`run_id, date, account (free|pro), device, app_version, mode (voice|text), model_shown, situation_card, start_local, end_local, voice_stayed_active (y|n|na), recording_file, transcript_file, status (ok|aborted), abort_reason, notes`.
Rows are joined to log lines by tester label and the start/end window. Voice runs are screen-recorded on the phone; the recording is the evidence for "without leaving voice".

### 6.3 Transcripts

Right after each run the author opens the conversation on claude.ai web and copies it to `spike/data/raw/transcripts/run-NN.md`. For voice runs this also checks whether a text transcript exists (section 11 assumes it does not). A Settings → Export data at the end of the week is the backup for both accounts; whether it includes tool calls is unverified.

### 6.4 Analysis and redaction

- `spike/analyze.py` reads the JSONL, `runs.csv`, transcripts and annotations, and prints the per-run tables and aggregate numbers for each experiment file. No LLM calls.
- `spike/redact.py` copies evidence into `docs/spike/NN-data/` with names and identifying details replaced. Raw data never leaves `spike/data/raw/` (gitignored).
- The redacted text transcripts, their annotations and payloads are also copied to `evals/fixtures/` under the naming convention in `evals/README.md`.

## 7. Session protocol

- **Start:** new chat, connector enabled, author says only something like "Let's practise English for 15 minutes." Tools, saving and `end_session` are never mentioned.
- **Situation:** drawn from a deck of 12 cards covering section 8's six interaction types:
  explain (standup about last night's outage; a design to a PM) · negotiate (moving a deadline; cutting scope) · disagree (pushing back in a code review; an architecture choice) · ask for help (stuck on a bug, asking a senior; asking for access) · give feedback (a junior's PR; to your manager in a 1:1) · small talk (start of a client call; coffee chat with a new US teammate).
  Cards are drawn without replacement and the deck is reshuffled when empty.
- **During:** speak naturally; do not avoid or stage errors.
- **End:** at ~15 minutes on a phone timer, close the way a person would ("OK, I have to go, thanks"). No nudge to save. If `end_session` does not arrive after Claude's reply, the run is scored as missing.
- **After:** fill the `runs.csv` row, save the transcript, stop the screen recording.
- **Aborts:** our-side failure (no request reached the server: tunnel down, laptop asleep) or the Free plan's usage limit cutting the session → `status=aborted`, recorded and replaced. A request that reached the server followed by a Claude error or a drop out of voice is a fail, not an abort.

**Allocation (25 runs, all by the author):**

| Mode | Free | Pro | Total |
| --- | --- | --- | --- |
| Voice (experiments 1 and 2) | 3 | 2 | 5 |
| Text (experiment 2) | 10 | 10 | 20 |

Free/Pro and voice/text are interleaved across days to avoid order effects.

**Schedule:**

| Day | Work |
| --- | --- |
| Mon Oct 5 | Build the spike server, Google OAuth, tunnel; setup acceptance on web and phone; freeze instructions v1 |
| Tue Oct 6 – Fri Oct 9 | 5 voice runs early (Tue–Wed), 20 text runs (~5/day) |
| Sat Oct 10 | Blind error annotation of 10 text transcripts; run `analyze.py` |
| Sun Oct 11 | Write-ups, `phase-auditor`, ADR 0001 |

If rule 9.2's extension triggers, the 10 extra runs push the go/no-go past Oct 11 and the core loop start moves with it.

## 8. Measurement and scoring

All scoring is done by `spike/analyze.py`; nothing is graded by the model.

**Normalization** (used everywhere text is compared): Unicode NFKC, lowercase, punctuation stripped, whitespace collapsed.

- **8.1 Tools fire in voice (experiment 1).** A voice run passes when ≥ 1 `tools/call` reaches the server and gets a response, the recording shows voice stayed active throughout, and Claude continues the conversation using the result. Whether `end_session` also fired in voice is reported separately.
- **8.2 Valid payload (experiment 2).** Denominator: runs with `status=ok`. A run is valid when its **final** `end_session` call passes the strict model, has ≥ 1 `user_turns`, and carries a `session_id` issued by `get_profile` to the same tester in that run. No `end_session` call → invalid. Also reported: valid on first call, number of calls per run.
- **8.3 `errors[].said` validation (experiment 2).** Over all `errors` items in valid final payloads, pooled: an item passes when its normalized `said` is a substring of some normalized `user_turns` entry. Rate = passing / total.
- **8.4 Evidence fidelity (also measured).**
  - `user_turns`, all 20 text runs (and voice runs if transcripts exist): a payload turn matches when its normalized text is a substring of a transcript user message. Recall = transcript user messages with ≥ 1 matching payload turn / transcript user messages. Precision = matching payload turns / payload turns. A fuzzy variant (difflib ratio ≥ 0.9) is reported as secondary.
  - `errors`, 10 text runs (5 Free, 5 Pro): the author annotates the real errors (`said`, `correct`) from the transcript **before** seeing that run's payload. A payload error matches an annotated error when one normalized `said` contains the other. Recall = matched annotations / annotations; precision = matched payload errors / payload errors.
  - `chunks_used`: not measurable (the spike issues no chunks); any non-empty value is counted as a fabrication.
  - Section 11 thresholds are reported against: `user_turns` ≥ 0.9 / ≥ 0.9, `errors` ≥ 0.7 / ≥ 0.8.
- **8.5 Grader reliability (also measured).** `cefr_estimate.speaking` on an ordinal half-step scale (B1=0 … C1=4) across all valid payloads: distribution, mode, range, standard deviation in half-steps, share within ±1 half-step of the mode, jumps ≥ 1 full level; split by account (default model per plan), mode and `model_shown`. The speaker's level is constant across the week, so the spread is the grader's.

## 9. Decision rules

Fixed before run 1. Changing a rule mid-week means stopping and asking the author.

**9.1 Experiment 1 (5 voice runs):**
- ≥ 4 of 5 pass → PASS, voice-first stands.
- ≤ 3 of 5 → FAIL: the core loop ships the text→voice→text fallback (section 8) and onboarding states it.
- Reported per plan as well; if the Free account fails, the fallback applies, because the core loop targets Free accounts.

**9.2 Experiment 2 (25 runs):**
- Valid by final call ≥ 23/25 and `said` passing ≥ 80% → PASS.
- Valid 70–89%, or `said` < 80% → FAIL with fallback: the core loop adds the `/end` prompt and a reminder in `start_lesson`; validity is re-measured in the first core-loop sessions. (Production already discards failing `said` items, so a `said` failure degrades metrics rather than breaking them.)
- Valid < 70% → extension: add a spike `/end` prompt and run 10 more text runs. Still < 70% → NO-GO on evidence capture through a tool call; the core loop does not start until evidence capture is redesigned.

**9.3 Go/no-go (ADR 0001).** NO-GO on the MCP-first architecture only if rule 9.2 ends in NO-GO. An experiment 1 failure changes the session flow, not the architecture. The ADR also records: fallbacks adopted, FastMCP 4 as the spike's library (production library still undecided), the cross-client deferral, and the single-speaker limitation. `phase-auditor` runs before the ADR is written.

## 10. Deferred: cross-client consistency

Not measured in the spike. It needs ChatGPT connected to the same server and 10 + 10 scenarios, and section 15 still has the open question of whether ChatGPT's free plan allows custom MCP connectors. It moves to the core-loop phase, before the week-3 deadline for that question. ADR 0001 records the change. `docs/requirements.md` is author-edited only (protected by a hook), so the author updates section 16: the Spike row drops cross-client consistency from "measured once", and the Core loop row adds "cross-client consistency (section 15) measured once, before the week-3 ChatGPT open question is answered".

## 11. Threats to validity

- One speaker: the 25 runs say nothing about `end_session` behaviour across learners; re-checked at the validation gate with 2–3 other developers.
- Self-annotation by a B1–B2 speaker is weaker ground truth than a teacher's.
- The author knows the experiment; the protocol (no nudges, natural closing) limits but does not remove that bias.
- No `start_lesson`: the spike session is less structured than the real flow, and `get_profile` stands in as the opener.
- Model versions can change mid-week; `model_shown` is recorded per run and results are split by it.
- Free-plan usage limits can cut sessions; handled as aborts and replaced.

## 12. Artifacts

| Path | Content |
| --- | --- |
| `docs/spike/01-voice-mode-tool-calls.md` | Experiment 1 |
| `docs/spike/02-end-session-reliability.md` | Experiment 2 |
| `docs/spike/03-evidence-fidelity.md` | Also measured |
| `docs/spike/04-grader-reliability.md` | Also measured |
| `docs/spike/NN-data/` | Redacted transcripts, payloads, run sheets, screenshots |
| `evals/fixtures/` | Redacted text transcripts, annotations, payloads |
| `docs/adr/0001-mcp-first-go-no-go.md` | Go/no-go, written at the end |
| `spike/` | Throwaway code, deleted at the go/no-go |
