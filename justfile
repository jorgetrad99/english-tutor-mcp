# Task runner. Run as `uv run just <recipe>` (just is a uv dev dependency).
# One command per line so recipes work under both PowerShell (Windows) and sh (CI).
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

export TEST_DATABASE_URL := env("TEST_DATABASE_URL", "postgresql://tutor:tutor@localhost:5433/tutor_test")

# In CI the db-test Postgres is a job service, so compose is skipped there.
db_test_up := if env("CI", "") == "true" { "uv --version" } else { "docker compose up -d --wait db-test" }

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
    uv run pytest -m "not integration and not eval" -q

# Integration tests against db-test
test-int:
    {{db_test_up}}
    uv run pytest -m integration -q

# Fast gate used by the Stop hook (< 30 s)
check-fast:
    uv run ruff check .
    uv run mypy
    uv run pytest -m "not integration and not eval" -q -x

# Full gate: lint, all non-eval tests, coverage (>= 90% on tutor/domain), dependency audit
check: lint
    {{db_test_up}}
    uv run pytest -m "not eval" -q --cov=tutor --cov-report=term
    uv run coverage report --include="src/tutor/domain/*" --fail-under=90
    uv run pip-audit --skip-editable

# MCP Inspector (needs Node)
inspector:
    npx @modelcontextprotocol/inspector
