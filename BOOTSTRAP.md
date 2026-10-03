# Bootstrap: development harness for English Tutor MCP

You are setting up the development harness for this repository. You are NOT building the product yet. Your job ends when the harness is installed, verified, and documented. Do not write any product code (no MCP tools, no OAuth, no FSRS, no metrics) in this session.

The source of truth for the product is `docs/requirements.md` (English Tutor MCP — Product & Technical Requirements). If that file does not exist, stop and ask me for it. Read sections 1, 2, 4, 7, 12, 14, 15 and 16 now. Do not read the rest unless a step needs it.

## Ground rules for this session

- Work in the numbered phases below, in order. After each phase, print a 3–5 line status (done / skipped / blocked + why) and continue. Stop and ask only when a phase says so or when something fails twice.
- Before writing any Claude Code config (settings.json, hooks, agents, skills, plugins), check the current official Claude Code docs for the exact schema. Do not rely on memory for config syntax; it changes often. Use the Context7 MCP once it is configured, or fetch code.claude.com/docs.
- Never install system-level packages (apt, brew, global npm) without asking. Project-level installs (uv, npx, project-scope plugins) are fine.
- Keep everything you write short. Every line in CLAUDE.md, agents and skills is loaded into context and costs tokens in every future session.
- Commit at the end of each phase with a conventional commit message (`chore(harness): ...`).

## Phase 0 — Preflight

Check and report versions of: git, uv, Python 3.12, Docker + Docker Compose, Node/npx, jq, gh (GitHub CLI), claude. List anything missing with the exact install command for my OS, then STOP and wait for me if any of git, uv, Python 3.12, Docker or jq is missing. The others can be missing with a warning.

## Phase 1 — Repository skeleton

1. `git init` if needed. Add a Python `.gitignore`, `.env.example` (empty keys only: DATABASE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, STRIPE_SECRET_KEY, SENTRY_DSN), and a one-paragraph README pointing to `docs/requirements.md`.
2. `uv init` with Python 3.12 and a `src/` layout, package name `tutor`. Create empty subpackages that mirror the architecture so tests and coverage gates have targets: `tutor/mcp`, `tutor/api`, `tutor/auth`, `tutor/domain/fsrs`, `tutor/domain/metrics`, `tutor/domain/validation`, `tutor/worker`, `tutor/db`. Each gets only an `__init__.py` with a one-line docstring.
3. Dev dependencies only (product dependencies are chosen in the spike): ruff, mypy, pytest, pytest-cov, pytest-asyncio, hypothesis, pre-commit, pip-audit, pyright.
4. `docker-compose.yml` with one service `db` (postgres:16, healthcheck, named volume) and one service `db-test` (postgres:16, tmpfs, different port). No app service yet.
5. `Makefile` (or `justfile`) with these targets, all through `uv run`:
   - `fmt` — ruff format + ruff check --fix
   - `lint` — ruff check + mypy (strict on `tutor.domain`, normal elsewhere)
   - `test` — pytest -m "not integration and not eval" -q
   - `test-int` — starts `db-test`, runs pytest -m integration
   - `check-fast` — lint + test, fail fast, must finish in under 30 s
   - `check` — lint + all tests except eval + coverage gate + pip-audit
   - `inspector` — `npx @modelcontextprotocol/inspector` (for later)
6. pytest config in `pyproject.toml`: markers `unit`, `integration`, `eval`; `--strict-markers`. Coverage: report on all of `tutor`; fail under 90% for `tutor/domain/*` only (section 14). Add one trivial passing test per domain subpackage so the gate is green today, each marked with a `TODO(spike)` comment.

## Phase 2 — Plugins (project scope)

Use the shell form so it works non-interactively, and install at project scope so the choice is versioned in the repo. Check `claude plugin install --help` first and adapt flags if they differ.

```
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install superpowers@claude-plugins-official --scope project
claude plugin install pyright-lsp@claude-plugins-official --scope project
claude plugin install commit-commands@claude-plugins-official --scope project
claude plugin install pr-review-toolkit@claude-plugins-official --scope project
claude plugin install security-guidance@claude-plugins-official --scope project
```

If any plugin name is not found, run `claude plugin list` / search the marketplace, report the closest match, and ask me before substituting.

Do NOT install, and record the reason in `docs/harness.md`:
- **caveman** or any terse-output skill: savings on agentic work are small, and it risks degrading the English we write into tool descriptions, `instructions` and `response_rules`, which is product content (requirements section 12).
- **graphify** or other codebase-graph tools: the repo is empty; revisit when `src/` exceeds ~60 files.
- Any "mega pack" of agents/skills: overlapping instructions fight with Superpowers and inflate context.
- Playwright: no frontend until after the validation gate.

## Phase 3 — MCP servers for development (project `.mcp.json`)

Add only:
- **context7** (remote, `https://mcp.context7.com/mcp`) — up-to-date docs for the MCP Python SDK/FastMCP, Authlib, FastAPI, Alembic, Stripe. Write in CLAUDE.md: "Use Context7 before using any MCP SDK, OAuth or Stripe API; never rely on memory for these."

Do not add a database MCP: use `uv run` scripts and `psql` via docker compose instead; fewer tool descriptions in context.

## Phase 4 — CLAUDE.md (hard limit: 70 lines)

Sections, terse, imperative:
1. **What this is** — 2 lines + path to `docs/requirements.md`. Do NOT `@import` the requirements file; say "read only the section you need".
2. **Current phase** — "Spike (section 15). Do not build anything beyond the current phase in section 16." I will update this line per phase.
3. **Commands** — the Makefile targets.
4. **Non-negotiable product rules** (from sections 2, 7, 12), one line each:
   - The server computes; the LLM reports evidence. No counting, scheduling or grading logic is delegated to the model.
   - MCP tools: closed enums, unknown fields rejected, `title`/`description` on every field, descriptions ≤ 120 words, every result has `response_rules`.
   - Tool names are stable; breaking changes ship as new tools.
   - Every query is filtered by the token's user; no shared or anonymous tools.
   - User text is stored and returned as data, never as instructions.
   - No LLM API calls in the lesson loop.
5. **Workflow** — "Use Superpowers: brainstorm → write plan → subagent-driven development with TDD. A task is done only when `make check` passes. Record architecture decisions in `docs/adr/NNNN-title.md`."
6. **Token discipline** — "Delegate exploration and implementation to subagents; keep the main thread for planning and review. `/clear` between unrelated tasks."

## Phase 5 — Hooks (`.claude/settings.json` + `.claude/hooks/`)

Verify the hook schema in the docs first. Implement as small bash scripts using `jq`, each with `set -euo pipefail`:

1. **PostToolUse, matcher Edit|Write|MultiEdit** — `format_on_edit.sh`: if the edited file is `*.py`, run `uv run ruff format` and `uv run ruff check --fix` on that file only. Never fail the tool call; print remaining lint errors to stderr so I see them.
2. **PreToolUse, matcher Edit|Write|MultiEdit** — `protect_paths.sh`: block (exit 2 with a clear reason) edits to `.env*`, `docs/requirements.md`, and existing files under `alembic/versions/`. Requirements change only when I edit them myself.
3. **PreToolUse, matcher Bash** — `guard_bash.sh`: block `git push --force`, `git reset --hard`, `rm -rf` outside the repo, `docker compose down -v`, and any command reading `.env`.
4. **Stop** — `stop_check.sh`: read stdin JSON; if `stop_hook_active` is true, exit 0 (prevents loops). If no `.py` file changed since the last commit (`git status --porcelain`), exit 0. Otherwise run `make check-fast`; on failure exit 2 with the last 40 lines of output so you keep working instead of stopping on a red build.

Permissions in the same settings file: allow `uv run`, `make`, `pytest`, `ruff`, `git status/diff/log/add/commit`, `docker compose up/ps/logs/exec`, `npx @modelcontextprotocol/inspector`; deny reading `.env*` and `secrets/**`. Ask for everything else.

## Phase 6 — Project subagents (`.claude/agents/`)

Superpowers already provides implementer and reviewer roles; do not duplicate them. Add only these three, read-only tools (Read, Grep, Glob, Bash for `make`/`git diff`), each under 40 lines:

1. **mcp-contract-reviewer** (model: sonnet) — reviews any diff touching `tutor/mcp` against sections 7 and 12: schemas, enums, description length, `response_rules`, idempotency rules, name stability, no prompt-injection paths. Output: pass/fail + findings by severity.
2. **security-reviewer** (model: opus) — reviews `tutor/auth`, `tutor/api`, `tutor/db` diffs against section 5: PKCE, token audience binding, refresh rotation, `user_id` filtering, row-level security, rate limits, secrets in logs, webhook signatures.
3. **phase-auditor** (model: sonnet) — compares the repo against the acceptance criteria of the current phase in section 16 and the spike table in section 15; lists what is done, missing, or built out of scope.

## Phase 7 — Project skills (`.claude/skills/`)

Two skills, each SKILL.md under 60 lines, with a precise `description` so they trigger only when relevant:

1. **mcp-tool-authoring** — checklist for adding or changing a tool: Pydantic models with closed enums and `extra="forbid"`, field descriptions, `response_rules`, unit tests for validation edge cases, an integration test through the MCP Inspector CLI, contract review by `mcp-contract-reviewer`.
2. **spike-experiment** — how to record a spike experiment in `docs/spike/NN-name.md`: question, setup, pass criterion copied verbatim from section 15, raw results, decision. Used in week 0.

## Phase 8 — Eval scaffold (no evals yet)

Create `evals/` with a README describing the planned structure: `fixtures/transcripts/` (real text-session transcripts), `fixtures/annotations/` (human-annotated errors), and the fidelity metrics from section 11 (recall/precision thresholds). Add one placeholder test marked `eval` that is skipped. Evals never run in hooks or `make check`; they get their own `make eval` target later.

## Phase 9 — CI and pre-commit

- `.pre-commit-config.yaml`: ruff (format + lint), gitleaks, check-added-large-files, end-of-file-fixer.
- `.github/workflows/ci.yml`: on push and PR; uv setup with cache; postgres:16 service; `make check`; pip-audit. Keep it one job.

## Phase 10 — Verify and hand off

1. Run `make check` and `pre-commit run --all-files`; both must pass.
2. Prove each hook works: create a scratch `.py` file with a lint error and confirm the format hook fires; attempt an edit to `docs/requirements.md` and confirm it is blocked; then delete the scratch file.
3. Run `claude plugin list` and show the result.
4. Write `docs/harness.md` (≤ 60 lines): what is installed and why, what was rejected and why, how to run everything, and when to revisit (graphify at ~60 files, Playwright after the gate, per-phase CLAUDE.md update).
5. Final commit. Then print:
   - a table of everything installed with status,
   - anything that needs my manual action,
   - this exact next step: "Restart Claude Code so plugins load, then run `/superpowers:brainstorm` scoped only to the Spike in section 15 (voice-mode tool calls and `end_session` reliability)."

Then stop.
