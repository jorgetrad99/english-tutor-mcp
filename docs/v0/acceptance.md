# Core loop v0 — acceptance

Status: in progress
Date(s): 2026-10-05 (local evidence only) → pending (author)
Spec: `docs/superpowers/specs/2026-10-04-core-loop-v0-design.md` (section 15 "Manual" row, section 16, "Amendments")
Deployed commit: pending (author; `git rev-parse --short HEAD` on the server)
Host: pending (author; `https://<host>`)

The build agents could not run anything that needs the deployed stack, the author's Google or Claude accounts, the phone, or the MCP Inspector (no Node on the dev machine). Every such row says "pending (author)". Section 0 holds the evidence the controller gathered locally on 2026-10-05. It shows the pieces work on the dev machine. It is **not** a substitute for any row in sections 1–5.

Before the author's run:
- paste the requirements edits in `docs/v0/requirements-edits.md`, at least E1–E2 (the section 16 v0 row and gate dates);
- set the `CLAUDE.md` "Current phase" line to `Core loop v0 (section 16)`.

Evidence rule: give a file name, screenshot name or log line, never tokens, codes, emails or learner text.

## 0. Local evidence (verified by the controller, 2026-10-05; not the deployed stack)

| Check | Where | Result | Evidence |
| --- | --- | --- | --- |
| Production image and compose stack run end to end | Local trial of `deploy/compose.prod.yml` (core Task 27 fix round), throwaway env files, project `tutor`, removed afterwards | Pass | Task 27 report, smoke item 14:<br>- `migrate` ran revisions 0001–0005, printed `roles ready` and exited 0;<br>- `app` reported `healthy`;<br>- `GET /login` with `Host: tutor.example.com` returned 200 HTML ("Entrar · English Tutor");<br>- the same request with a wrong `Host` returned 400;<br>- `GET /.well-known/oauth-protected-resource/mcp` returned 200 with `resource` `https://tutor.example.com/mcp` |
| Backups | Same local stack | Pass | `backup.sh` produced the dump, the roles file and the OAuth tarball; `check_backup.sh` reported ok |
| Role separation and network isolation | Same local stack | Pass | App login is non-superuser and non-BYPASSRLS; `tutor_report` has BYPASSRLS; the app container's environment holds no `MIGRATION_*`, `POSTGRES_*` or `REPORT_*` variables; `db` has no egress ("Network is unreachable"); the gate report ran as `tutor_report` from the app image |
| Full lesson on Postgres with the app's login role | `tests/integration/test_mcp_lesson_pg.py::test_scripted_lesson_on_postgres_with_the_login_role` (part of `uv run just check`) | Pass | The scripted lesson below runs through the real FastMCP server as the non-superuser login role. The same file checks the isolation of a second user, a call without a token, and the startup role checks |
| Website in a browser (Chrome, local demo on `localhost:8780`, in-memory backend), first round (dashboard Task D12) | Login page, test login, shell, language switch, logout, service worker | Pass | See "Browser round 1" below |
| Website in a browser, second round (core Task 25) | Desktop navigation, Inicio, Perfil | Pass | See "Browser round 2" below |

Scripted lesson steps (`tests/integration/test_mcp_lesson_pg.py`):

1. `get_profile` returns `onboarding_needed`.
2. `save_profile` creates a plan.
3. `start_lesson` (text) returns 5 chunks.
4. `save_glossary` saves one item as `confirmed`.
5. `end_session` closes the session with streak 1 and a 4-line summary; repeating it returns `already_closed`.
6. The next day, the item is due.
7. `start_lesson` (voice) lists the item, and `record_review` returns `recorded`.

Browser round 1 (dashboard Task D12):

- `/login` renders in the approved design, with no CSP violations or page errors in the console.
- Test login redirects to the app (303).
- The signed-in shell renders with the sidebar at ≥ 900 px.
- The language switch es → en works.
- After logout, a protected page redirects to `/login?next=…`.
- The service worker is active with scope `/`, and its cache holds only `/static/*` and `/offline`.
- `localStorage` is empty; `sessionStorage` holds only htmx's current-path key.

Browser round 2 (core Task 25), at desktop width 1280 px:

- The sidebar has exactly Inicio, Perfil, Sesiones, Glosario and Conectar, with no "Más", plan chip or meter.
- Inicio for a new user shows the Conectar steps, with the MCP URL and copy buttons.
- Perfil shows the onboarding questions in es-MX. The hidden timezone field is filled from the browser (`America/Mexico_City`).
- The forms post to `/app/profile`, `/app/lang` and `/auth/logout`.

Not yet verified in a browser (author): phone width 390 px (the Chrome window could not be resized), offline reload, installability prompt and reduced motion. The test login skips the first-login redirect to Perfil by design; only the Google callback applies it, so it is checked in section 2.

## 1. MCP Inspector auth check

Procedure: `uv run just inspector`, transport Streamable HTTP, URL `https://<host>/mcp`, "Open Auth Settings" → "Quick OAuth Flow".

| Check | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Unauthenticated `POST /mcp` | 401 with `resource_metadata` pointing to `/.well-known/oauth-protected-resource/mcp` | pending (author) | |
| Authorization-server metadata | `authorization_endpoint` `https://<host>/authorize`; `code_challenge_methods_supported` `["S256"]` | pending (author) | |
| Dynamic client registration | `/register` returns a client id | pending (author) | |
| Google consent and callback | The tutor's consent screen, then Google redirects to `https://<host>/oauth/callback`, then to the Inspector | pending (author) | |
| Token works | `tools/list` shows exactly `get_profile`, `save_profile`, `start_lesson`, `record_review`, `save_glossary`, `end_session` | pending (author) | |
| Refresh | "Refresh token" in the Inspector issues a new access token; the old refresh token is refused (rotation) | pending (author) | |
| Audit | `SELECT event FROM audit_log ORDER BY at DESC LIMIT 5` (as the owner, `PSQL_HISTORY=/dev/null`) shows `user_created`, `mcp_first_use` for the Inspector's account | pending (author) | |

## 2. Full session, Free Claude account, claude.ai web

| Step | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Add the connector (Settings → Connectors → Add custom connector, URL `https://<host>/mcp`) | Google sign-in, then "connected" | pending (author) | |
| `/start-lesson` in a new chat | `get_profile` says `onboarding_needed`; Claude asks the five questions one at a time | pending (author) | |
| Onboarding | `save_profile` called once; Perfil on the web shows the same answers and the plan | pending (author) | |
| First web login (Google, test user without a profile) | Lands on Perfil, whatever `next` says | pending (author) | |
| Lesson | `start_lesson` → warm-up with 5 phrases → scenario → feedback | pending (author) | |
| Glossary | Claude proposes items; kept ones saved `confirmed`, dropped ones `declined` | pending (author) | |
| `end_session` | Called once; `summary_text` read once; Sesiones shows the session with metrics | pending (author) | |
| Next session | Confirmed items come back as due reviews after their due date (`record_review` called) | pending (author) | |
| Website | Inicio, Perfil, Sesiones (detail), Glosario (edit, CSV), Conectar render; nav shows only those five | pending (author); desktop nav, Inicio and Perfil seen locally (section 0) | |
| Website at phone width | Tab bar at 390 px, offline reload, install prompt, reduced motion | pending (author) | |

## 3. Full session, Free Claude account, mobile voice

| Step | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Voice lesson on the Claude mobile app | Tools fire in voice (spec 16, spike #1 outcome); turns ≤ 40 words | pending (author) | |
| `end_session` in voice | Session `closed` (or `incomplete` with a stated reason) | pending (author) | |
| `user_words_per_min` | Recorded on Sesiones | pending (author) | |

## 4. Gate report

Command (runbook "Gate report", as `tutor_report` through `GATE_REPORT_DATABASE_URL`), window = the dates of sections 2–3:

pending (author): paste the report output here. User hashes are pseudonymous (the first 12 hex characters of SHA-256 of the user id). Never paste the labels file next to it.

## 5. Restore drill

| Step | Result |
| --- | --- |
| Latest dump file and size | pending (author) |
| `pg_restore` into `tutor_restore` | pending (author) |
| Row counts (users, sessions) restore vs live | pending (author) |
| `tutor_restore` dropped | pending (author) |

## 6. Phase audit

pending (controller, after the author pastes E1–E2 and updates `CLAUDE.md`): paste the `phase-auditor` output verbatim.

## Verdict

pending. Core loop v0 is not accepted yet. Tester invitations wait for "Status: complete", which needs sections 1–5 run by the author on the deployed stack, section 6 re-run against the pasted section 16 row, and ADR 0002 moved to accepted.
