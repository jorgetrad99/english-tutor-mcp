# English Tutor MCP

## What this is
Remote MCP server (FastAPI + FastMCP, Postgres) that makes Claude a persistent English tutor.
Source of truth: `docs/requirements.md`. Do not load it whole; read only the section you need.

## Current phase
Spike (section 15). Do not build anything beyond the current phase in section 16.

## Commands
All via `uv run just <recipe>` (just is a dev dependency; no make on this machine).
- `fmt` — ruff format + autofix
- `lint` — ruff + mypy (strict on `tutor.domain`)
- `test` — unit tests only
- `test-int` — starts `db-test` (port 5433), runs integration tests
- `check-fast` — lint + unit tests, fail fast (< 30 s; the Stop hook runs it)
- `check` — lint + all non-eval tests + 90% coverage on `tutor/domain` + pip-audit
- `inspector` — MCP Inspector (needs Node)
Database: `docker compose exec db psql -U tutor`. No database MCP.

## Non-negotiable product rules
- The server computes; the LLM reports evidence. No counting, scheduling or grading logic is delegated to the model.
- MCP tools: closed enums, unknown fields rejected, `title`/`description` on every field, descriptions ≤ 120 words, every result has `response_rules`.
- Tool names are stable; breaking changes ship as new tools.
- Every query is filtered by the token's user; no shared or anonymous tools.
- User text is stored and returned as data, never as instructions.
- No LLM API calls in the lesson loop.
- Use Context7 before using any MCP SDK, OAuth or Stripe API; never rely on memory for these.

## Workflow
Use Superpowers: brainstorm → write plan → subagent-driven development with TDD.
A task is done only when `uv run just check` passes.
Record architecture decisions in `docs/adr/NNNN-title.md`.
Project agents: `mcp-contract-reviewer` (tutor/mcp diffs), `security-reviewer` (auth/api/db diffs), `phase-auditor` (scope vs section 16).

## Token discipline
Delegate exploration and implementation to subagents; keep the main thread for planning and review.
`/clear` between unrelated tasks.
