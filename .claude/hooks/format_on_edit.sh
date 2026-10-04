#!/usr/bin/env bash
# PostToolUse(Edit|Write|MultiEdit): format + autofix the edited .py file only.
# PostToolUse cannot fail the tool call; exit 2 only shows remaining lint errors to Claude.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

file=$(jq -r '.tool_input.file_path // empty' | tr -d '\r')  # native jq.exe emits CRLF
[[ "$file" == *.py && -f "$file" ]] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}"

uv run --quiet ruff check --fix --quiet "$file" >/dev/null 2>&1 || true
uv run --quiet ruff format --quiet "$file" >/dev/null 2>&1 || true

if ! out=$(uv run --quiet ruff check --output-format concise "$file" 2>&1); then
  echo "ruff: lint errors remain in $file" >&2
  echo "$out" >&2
  exit 2
fi
