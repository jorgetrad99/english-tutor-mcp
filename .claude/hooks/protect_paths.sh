#!/usr/bin/env bash
# PreToolUse(Edit|Write|MultiEdit): block edits to .env*, docs/requirements.md
# and existing alembic/versions/* files. Fails closed if jq is missing.
set -euo pipefail

block() { echo "Blocked by protect_paths: $1" >&2; exit 2; }
command -v jq >/dev/null || block "jq not found, cannot inspect the edit."

file=$(jq -r '.tool_input.file_path // empty' | tr -d '\r')  # native jq.exe emits CRLF
[[ -n "$file" ]] || exit 0

# Forward slashes, lowercase (Windows paths are case-insensitive), relative to the repo root.
f=${file//\\//}; f=${f,,}
root=${CLAUDE_PROJECT_DIR:-$PWD}; root=${root//\\//}; root=${root,,}
rel=${f#"$root"/}; rel=${rel#./}

case "${rel##*/}" in
  .env*) block "$file holds secrets; edit .env files yourself." ;;
esac
[[ "$rel" == "docs/requirements.md" ]] &&
  block "requirements change only when the author edits them."
[[ "$rel" == alembic/versions/* && -e "$file" ]] &&
  block "existing migrations are immutable; create a new revision instead."
exit 0
