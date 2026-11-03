# Task runner. Run as `uv run just <recipe>` (just is a uv dev dependency).
# One command per line so recipes work under both PowerShell (Windows) and sh (CI).
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

export TEST_DATABASE_URL := env("TEST_DATABASE_URL", "postgresql://tutor:tutor@localhost:5433/tutor_test")
# Owner URL for `migrate` (read from the environment by alembic/env.py, never on a command line).
export MIGRATION_DATABASE_URL := env("MIGRATION_DATABASE_URL", env("DATABASE_URL", "postgresql://tutor:tutor@localhost:5432/tutor"))

# In CI the db-test Postgres is a job service, so compose is skipped there.
db_test_up := if env("CI", "") == "true" { "uv --version" } else { "docker compose up -d --wait db-test" }

# Dev profile (docker-compose.yml): `dev-reset` deletes data only with CONFIRM=1, and only this
# compose project's pgdata volume, whose name compose itself resolves (scripts/dev_pgdata.py).
confirm := env("CONFIRM", "")

default:
    @just --list

# Format and autofix
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Lint and type-check (mypy strict on tutor.domain)
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy

# Unit tests only
test:
    uv run pytest -m "not integration and not eval" -q -n 3

# Integration tests against db-test
test-int:
    {{db_test_up}}
    uv run pytest -m integration -q

# Apply migrations to the dev database (compose `db`, or MIGRATION_DATABASE_URL / DATABASE_URL)
migrate:
    uv run alembic upgrade head

# Product server: MCP, OAuth and the website; reads TUTOR_* settings from the environment
serve:
    uv run python -m tutor

# Same server with settings from deploy/local.env (copy of deploy/local.env.example; docs/v0/local.md)
serve-local:
    uv run --env-file deploy/local.env python -m tutor

# Gate report (spec 14), read only, as the tutor_report role: GATE_REPORT_DATABASE_URL (needs BYPASSRLS)
gate-report from:
    uv run python -m tutor.ops.gate_report --from {{from}}

# Evidence fidelity over the annotated transcripts in evals/fixtures (requirements 11)
[working-directory: 'evals']
eval-fidelity:
    uv run python -m fidelity.report --fixtures fixtures

# Print the idempotent SQL that creates the tutor_report role (no password; deploy sets it)
report-role-sql:
    uv run python -m tutor.ops.report_role

# LLM evaluations (marked eval); never part of check
eval:
    uv run pytest -m eval -q

# Fast gate used by the Stop hook (< 30 s)
check-fast:
    uv run ruff check .
    uv run mypy
    uv run pytest -m "not integration and not eval" -q -n 3 -x

# Full gate: lint, all non-eval tests, coverage (>= 90% on tutor/domain), dependency audit
check: lint
    {{db_test_up}}
    uv run pytest -m "not eval" -q --cov=tutor --cov-report=term
    uv run coverage report --include="src/tutor/domain/*" --fail-under=90
    uv run pip-audit --skip-editable

# MCP Inspector (needs Node)
inspector:
    npx @modelcontextprotocol/inspector

# Dashboard over in-memory demo data (no Google, no Stripe); open http://localhost:8780/auth/test-login
dashboard-demo:
    uv run uvicorn tutor.web.demo_server:app --port 8780 --reload

# Local Docker stack for the real app (project tutor-local; docs/v0/local.md): build, migrate, start
up-local:
    docker compose -f deploy/compose.local.yml up -d --build --wait app

# Stop the local stack (keeps its database and OAuth volumes)
down-local:
    docker compose -f deploy/compose.local.yml down

# Follow the local app logs
logs-local:
    docker compose -f deploy/compose.local.yml logs -f --tail 100 app

# DESTRUCTIVE: stops the local stack and deletes its volumes (database, roles, OAuth registrations)
reset-local:
    docker compose -f deploy/compose.local.yml down --volumes

# --- Dev profile (docker-compose.yml `dev`; docs/v0/local.md, "Dev profile") -----------------------
# Postgres, migrations, the seeded dev users and the app with hot reload in Docker, no Google.
# `dev` generates deploy/dev.env (random secrets, git-ignored) on first run, then prints the MCP URL
# and a dev bearer token for MCP Inspector, Claude Code and Claude Desktop.

# Dev profile up (build, migrate, seed, app with hot reload), then print the MCP URL and a dev token
dev:
    uv run python scripts/dev_env.py
    docker compose --profile dev up -d --build --wait app-dev
    docker compose --profile dev exec -T app-dev python -m tutor.ops.dev_token

# Mint another dev token from the running app-dev (sub: dev-ana or dev-nuevo; hours: 1 to 24)
dev-token sub="dev-ana" hours="12":
    docker compose --profile dev exec -T app-dev python -m tutor.ops.dev_token --sub {{sub}} --hours {{hours}}

# Follow the dev app logs
dev-logs:
    docker compose --profile dev logs -f --tail 100 app-dev

# Stop the dev profile services (keeps db, db-test and the database volume)
dev-down:
    docker compose --profile dev down app-dev migrate-dev

# DESTRUCTIVE: stops this compose project (db included) and deletes its pgdata volume; needs CONFIRM=1
dev-reset:
    {{ if confirm == "1" { "docker compose --profile dev down --remove-orphans" } else { "echo dev-reset deletes the pgdata volume of this compose project. Print its name with: uv run python scripts/dev_pgdata.py. Re-run with CONFIRM=1 to proceed." } }}
    {{ if confirm == "1" { "uv run python scripts/dev_pgdata.py --remove" } else { "exit 1" } }}

# MCP Inspector on host Node against the dev profile: prints the token and the connection lines, then opens the UI
inspector-dev:
    docker compose --profile dev exec -T app-dev python -m tutor.ops.dev_token
    npx @modelcontextprotocol/inspector@latest
