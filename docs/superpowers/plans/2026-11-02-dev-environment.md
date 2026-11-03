# Local Development Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `uv run just dev` brings up Postgres, a migrated and seeded database and the app with hot reload in Docker, and prints an MCP URL and a bearer token that MCP Inspector, Claude Code and Claude Desktop can use without Google; the website signs in with a dev-only route. Nothing in it can run in production.

**Architecture:** a `TUTOR_DEV_AUTH` setting guarded to loopback dev/test; a FastMCP JWT verifier replaces the Google OAuth proxy when it is on; `tutor.ops.dev_token` mints tokens and `tutor.ops.dev_seed` creates two identities through the services layer; a `dev` Dockerfile target and a `dev` compose profile with `migrate-dev`, `app-dev` and `inspector`; `just` recipes over them.

**Tech Stack:** Python 3.12, uv, FastMCP 4.0.11 (`JWTVerifier` or `StaticTokenVerifier`, confirmed with Context7), authlib, FastAPI, Docker Compose v2, uvicorn with `--reload`.

**Spec:** `docs/superpowers/specs/2026-11-02-dev-environment-design.md` (the authority).

**Execution:** subagent-driven (CLAUDE.md workflow). Implementers never dispatch subagents; the review step marked **(controller)** runs after the implementer reports.

**Branch:** `feat/dev-environment`, from `main`.

## Before Task 1

1. `git switch main && git pull`, then `uv run just check-fast` passes, then `git switch -c feat/dev-environment`.
2. Context7 lookup (CLAUDE.md): in fastmcp 4.0.11, the token verifier classes under `fastmcp.server.auth` (`JWTVerifier` constructor: `public_key` or `jwks_uri`, `issuer`, `audience`, `algorithm`; whether HS256 with a shared secret is supported; else `StaticTokenVerifier`). Record the answer in the Task 2 commit message.
3. Read `src/tutor/settings.py`, `src/tutor/app.py`, `src/tutor/auth/mcp_auth.py`, `src/tutor/web/config.py`, `src/tutor/web/deps.py` (`pg_web_deps`), `src/tutor/web/routes/auth.py`, `deploy/Dockerfile`, `docker-compose.yml`, `justfile`, `deploy/tutor.env.example`, `tests/unit/deploy/test_env_example.py`, `tests/unit/deploy/test_coolify_compose.py`.

## Global Constraints

- Every command runs as `uv run …`; `uv run just fmt` before `uv run just lint`; `uv run just check-fast` green after every task; done when `uv run just check` passes.
- Hooks: no edits to `.env*`, `docs/requirements.md` or existing files under `alembic/versions/`; `guard_bash` blocks `docker compose down -v`, so `dev-reset` uses `docker volume rm` behind a `CONFIRM=1` guard.
- Secrets never in git: `deploy/dev.env` is generated and git-ignored; `deploy/dev.env.example` lists keys with placeholder values; `tests/unit/deploy/test_env_example.py` must cover it.
- The flag is refused outside loopback dev/test; the prod compose files never carry it; tests pin both.
- Commit messages: `type(scope): summary`, ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never `--no-verify`.

## Review Focus

1. **Flag set in prod by mistake:** `TUTOR_ENV=prod` with `TUTOR_DEV_AUTH=1` fails at startup with a clear message; `TUTOR_ENV=dev` with `TUTOR_BASE_URL=https://tutor.example.com` fails too. Tests in Task 1.
2. **Token from another key, wrong audience, expired:** rejected with 401 at `/mcp`; a valid one resolves `sub=dev-ana` through `PgIdentity`. Tests in Task 2 (unit with the in-process client) and Task 5 (integration).
3. **Dev login route absent when the flag is off:** the route is not registered, the sweep test and the CSRF walker are unchanged. Test in Task 3.
4. **`dev_seed` run twice:** no duplicate users or profiles. Integration test in Task 4.
5. **`just dev` on a fresh clone:** `deploy/dev.env` generated, `migrate-dev` exits 0, `app-dev` healthy, token printed. Manual check in Task 6, recorded in `docs/v0/local.md`.

## File structure

```
src/tutor/settings.py            # T1 dev_auth flag and guard
src/tutor/auth/dev_auth.py       # T2 verifier builder
src/tutor/app.py                 # T2 choose verifier vs Google provider
src/tutor/ops/dev_token.py       # T2 mint and print
src/tutor/ops/dev_seed.py        # T4 idempotent seed
src/tutor/web/config.py, web/deps.py, web/routes/auth.py  # T3 dev login
src/tutor/dev.py                 # T5 uvicorn factory
deploy/Dockerfile                # T5 dev target
docker-compose.yml               # T5 dev profile
deploy/dev.env.example, scripts/dev_env.py, .gitignore  # T5
justfile                         # T6 recipes
docs/v0/local.md                 # T6 how to connect each client
tests/unit/app/, tests/unit/auth/, tests/unit/web/, tests/unit/deploy/, tests/integration/  # per task
```

## Task index

| # | Task | Reviewers (controller) |
| --- | --- | --- |
| 1 | `TUTOR_DEV_AUTH` setting and guard | security-reviewer |
| 2 | Dev verifier, app wiring, `dev_token` | security-reviewer |
| 3 | Website dev login | security-reviewer |
| 4 | `dev_seed` | — |
| 5 | Dockerfile `dev` target, compose `dev` profile, generated env | security-reviewer |
| 6 | `just` recipes and `docs/v0/local.md` | — |

---

### Task 1: `TUTOR_DEV_AUTH` setting and guard

**Files:** modify `src/tutor/settings.py`, `deploy/tutor.env.example`; tests `tests/unit/app/test_settings*.py` (find the existing settings tests and extend).

**Interfaces:** `Settings.dev_auth: bool` (from `TUTOR_DEV_AUTH` in `1`, `true`, `yes`, case-insensitive; default `False`). When `True`: `google_client_id`/`google_client_secret` may be empty; `from_env` raises `SettingsError("TUTOR_DEV_AUTH requires TUTOR_ENV=dev|test and a loopback TUTOR_BASE_URL")` otherwise. Loopback = host in `localhost`, `127.0.0.1`, `[::1]`, or ending in `.localhost`.

- [ ] **Step 1:** failing tests: flag parsed; refused with prod env; refused with non-loopback URL; Google keys optional only with the flag.
- [ ] **Step 2:** implement; `uv run just fmt && uv run just check-fast`.
- [ ] **Step 3:** commit `feat(settings): TUTOR_DEV_AUTH flag guarded to loopback dev`.

### Task 2: Dev verifier, app wiring, `dev_token`

**Files:** create `src/tutor/auth/dev_auth.py`, `src/tutor/ops/dev_token.py`; modify `src/tutor/app.py`; tests `tests/unit/auth/test_dev_auth.py`.

**Interfaces:** `build_dev_verifier(settings) -> TokenVerifier` (FastMCP class per the Context7 answer); `mint_dev_token(settings, sub: Literal["dev-ana","dev-nuevo"], hours: int = 12) -> str` (claims `sub`, `email`, `name`, `iss` = base URL, `aud` = MCP URL, `iat`, `exp`); `python -m tutor.ops.dev_token [--sub] [--hours ≤ 24]` prints URL, token and the three client snippets from spec section 3; exits 2 without the flag. `build_app` picks the dev verifier when `settings.dev_auth`.

- [ ] **Step 1:** failing tests: in-process `Client` with a minted token calls `get_profile` and resolves `dev-ana`; wrong audience, wrong key, expired → auth error; `dev_token` refuses without the flag.
- [ ] **Step 2:** implement; `uv run just fmt && uv run just check-fast`.
- [ ] **Step 3:** commit `feat(auth): dev token verifier and tutor.ops.dev_token`.
- [ ] **Step 4 (controller):** `security-reviewer` on the diff.

### Task 3: Website dev login

**Files:** modify `src/tutor/web/config.py` (`dev_auth`), `src/tutor/web/deps.py` (`pg_web_deps` accepts dev auth), `src/tutor/web/routes/auth.py` (`GET /auth/dev-login?as=dev-ana|dev-nuevo`, registered only when the flag is on); tests `tests/unit/web/test_web_auth.py`, `tests/unit/web/test_web_sweep.py`.

- [ ] **Step 1:** failing tests: with the flag the route signs in and redirects to `/app`; `as` outside the enum → 422; without the flag → 404 and not in the route table.
- [ ] **Step 2:** implement; checks green.
- [ ] **Step 3:** commit `feat(web): dev login route behind TUTOR_DEV_AUTH`.

### Task 4: `dev_seed`

**Files:** create `src/tutor/ops/dev_seed.py`; tests `tests/integration/test_dev_seed.py`.

**Interfaces:** `seed(identity, services, now) -> SeedReport(created: tuple[str, ...], existing: tuple[str, ...])`; `dev-ana` gets a saved profile through `services.profile.save_profile`; `dev-nuevo` only the user row. Entry point `python -m tutor.ops.dev_seed` reads `DATABASE_URL` (login role) and refuses without the flag.

- [ ] **Step 1:** failing integration test on `db-test`: run twice, same counts, no duplicates.
- [ ] **Step 2:** implement; `uv run just test-int` green.
- [ ] **Step 3:** commit `feat(ops): idempotent dev seed`.

### Task 5: Dockerfile `dev` target, compose `dev` profile, generated env

**Files:** modify `deploy/Dockerfile` (target `dev`), `docker-compose.yml` (profile `dev`: `migrate-dev`, `app-dev`, `inspector`), `.gitignore` (`deploy/dev.env`); create `deploy/dev.env.example`, `scripts/dev_env.py`, `src/tutor/dev.py`; tests `tests/unit/deploy/test_env_example.py`, `tests/unit/deploy/test_dev_compose.py` (profile present, flag only under the dev profile, ports bound to 127.0.0.1, `db`/`db-test` untouched, prod files free of the flag).

- [ ] **Step 1:** failing tests.
- [ ] **Step 2:** implement; `docker compose --profile dev config` validates; `uv run just check-fast`.
- [ ] **Step 3:** commit `feat(deploy): dev compose profile with hot reload and inspector`.
- [ ] **Step 4 (controller):** `security-reviewer` on the compose and Dockerfile diff.

### Task 6: `just` recipes and `docs/v0/local.md`

**Files:** modify `justfile` (`dev`, `dev-token`, `dev-logs`, `dev-down`, `dev-reset` with `CONFIRM=1`, `inspector-dev`), `docs/v0/local.md` (new section "Dev profile" with the client table from spec section 3).

- [ ] **Step 1:** `uv run just dev` on this machine: `migrate-dev` exits 0, `app-dev` healthy, token printed; Inspector CLI `tools/list` returns the nine tools; record the commands and output in `docs/v0/local.md`.
- [ ] **Step 2:** `uv run just check` green; commit `docs(local): dev profile and client connection table`.

## Notes for the controller

- Task order 1 → 2 → 3 → 4 → 5 → 6; Tasks 3 and 4 may run after 2 in either order.
- The protected-resource metadata endpoint with the dev verifier: if `/.well-known/oauth-protected-resource` disappears or errors under dev auth, that is acceptable for v0 (clients use a bearer token directly); note it in `docs/v0/local.md`.
- Merge into `main` after the security review; the subjects branch rebases on it.
