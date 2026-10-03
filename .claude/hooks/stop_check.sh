#!/usr/bin/env bash
# Stop: if Python files changed since the last commit, keep working until check-fast is green.
set -euo pipefail

input=$(cat)
active=$(jq -r '.stop_hook_active // false' <<<"$input" | tr -d '\r')  # native jq.exe emits CRLF
[[ "$active" == "true" ]] && exit 0
cd "${CLAUDE_PROJECT_DIR:-.}"

changed=$(git status --porcelain --untracked-files=all)
grep -qE '\.py"?$' <<<"$changed" || exit 0

if ! out=$(uv run just check-fast 2>&1); then
  echo "check-fast is red; fix it before stopping. Last 40 lines:" >&2
  tail -n 40 <<<"$out" >&2
  exit 2
fi
