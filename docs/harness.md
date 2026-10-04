# Development harness

Tooling for building English Tutor MCP with Claude Code. Product rules live in `CLAUDE.md` and `docs/requirements.md`.

## Installed and why

| Piece | Where | Why |
| --- | --- | --- |
| uv, Python 3.12, `src/tutor` | `pyproject.toml`, `uv.lock` | One lockfile; subpackages mirror the architecture (section 4) |
| ruff, mypy (strict on `tutor.domain`), pyright, pytest(+cov, asyncio), hypothesis, pip-audit, pre-commit | dev group | Section 14: lint, types, ≥ 90% coverage on domain, dependency audit |
| just (`rust-just`, dev dep) | `justfile` | No `make` on Windows; `uv run just <recipe>` works the same locally and in CI |
| Postgres 16 `db` (5432, volume) + `db-test` (5433, tmpfs) | `docker-compose.yml` | Dev data persists; test DB is throwaway |
| Plugins: superpowers, pyright-lsp, commit-commands, pr-review-toolkit, security-guidance | `.claude/settings.json` (project scope) | Brainstorm → plan → TDD workflow, type-aware navigation, commits, PR review, security prompts |
| Context7 MCP | `.mcp.json` | Current docs for MCP SDK, Authlib, FastAPI, Alembic, Stripe |
| Hooks | `.claude/hooks/` | `format_on_edit` (ruff on edited `.py`), `protect_paths`, `guard_bash`, `stop_check` (check-fast must be green) |
| Agents | `.claude/agents/` | `mcp-contract-reviewer`, `security-reviewer`, `phase-auditor` (read-only) |
| Skills | `.claude/skills/` | `mcp-tool-authoring`, `spike-experiment` |
| pre-commit, CI | `.pre-commit-config.yaml`, `.github/workflows/ci.yml` | ruff, gitleaks, large files, EOF; one CI job running `just check` |

## Rejected

- **caveman / terse-output skills**: small savings on agentic work, and they risk degrading the English we write into tool descriptions, `instructions` and `response_rules`, which is product content (section 12).
- **graphify / codebase-graph tools**: the repo is empty; revisit when `src/` exceeds ~60 files.
- **"Mega packs" of agents/skills**: overlapping instructions fight with Superpowers and inflate context.
- **Playwright**: no frontend until after the validation gate.
- **Database MCP**: use `uv run` scripts and `docker compose exec db psql -U tutor`; fewer tool descriptions in context.

## Run

```
uv sync                      # install
uv run pre-commit install    # once per clone
docker compose up -d db      # dev database
uv run just check-fast       # lint + unit tests (< 30 s)
uv run just check            # full gate (starts db-test)
uv run just test-int         # integration tests only
```

## Windows notes and known limits

- Hooks run under Git Bash and need `jq` and `uv` on PATH; the blocking hooks fail closed without `jq`. Native `jq.exe` emits CRLF, so the scripts strip `\r`.
- `.gitattributes` forces LF on `*.sh` and `justfile`; `just` recipes run under PowerShell on Windows, so keep one command per line.
- `guard_bash` matches the whole command text, so a commit message mentioning a dot-env file is blocked too; reword it. It also runs on the PowerShell tool, but PowerShell-native deletes (`Remove-Item -Recurse`) are not inspected.
- `protect_paths` also blocks `.env.example`; edit it by hand.

## Revisit

- Per phase: update the "Current phase" line in `CLAUDE.md`.
- Add `just eval` when the first real eval lands (`evals/README.md`).
- graphify (or similar) when `src/` exceeds ~60 files.
- Playwright after the validation gate, when the dashboard starts.
