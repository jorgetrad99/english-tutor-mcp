#!/usr/bin/env bash
# PreToolUse(Bash|PowerShell): block force-push, hard reset, rm -rf outside the repo,
# compose volume deletion and reading .env files. Fails closed if jq is missing.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

block() { echo "Blocked by guard_bash: $1" >&2; exit 2; }
command -v jq >/dev/null || block "jq not found, cannot inspect the command."

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input" | tr -d '\r')  # native jq.exe emits CRLF
cwd=$(jq -r '.cwd // empty' <<<"$input" | tr -d '\r')
[[ -n "$cmd" ]] || exit 0

re_push='git[[:space:]]+push([[:space:]]+[^;&|]*)?[[:space:]](--force|--force-with-lease|-f)([[:space:]=]|$)'
[[ "$cmd" =~ $re_push ]] && block "git push --force is not allowed."
re_reset='git[[:space:]]+reset[^;&|]*[[:space:]]--hard'
[[ "$cmd" =~ $re_reset ]] && block "git reset --hard is not allowed."
re_down='docker(-|[[:space:]]+)compose[^;&|]*[[:space:]]down[^;&|]*[[:space:]](-v|--volumes)([[:space:]]|$)'
[[ "$cmd" =~ $re_down ]] && block "docker compose down -v deletes database volumes."
stripped=${cmd//.env.example/}
re_env='(^|[[:space:]/"'"'"'=<])\.env($|[[:space:]"'"'"'.;|&)>])'
[[ "$stripped" =~ $re_env ]] && block "commands must not read .env files."

posix() { if command -v cygpath >/dev/null; then cygpath -u "$1"; else printf '%s' "$1"; fi; }
root=$(realpath -m "$(posix "${CLAUDE_PROJECT_DIR:-$PWD}")")
base=$(posix "${cwd:-$PWD}")

# rm with both recursive and force flags: every target must resolve inside the repo.
while IFS= read -r seg; do
  read -r -a w <<<"$seg" || true
  [[ "${w[0]:-}" == "sudo" ]] && w=("${w[@]:1}")
  [[ "${w[0]:-}" == "rm" ]] || continue
  rec=0; force=0; targets=()
  for a in "${w[@]:1}"; do
    case "$a" in
      --recursive) rec=1 ;; --force) force=1 ;;
      --*) ;;
      -*) [[ "$a" == *[rR]* ]] && rec=1; [[ "$a" == *f* ]] && force=1 ;;
      *) targets+=("$a") ;;
    esac
  done
  (( rec && force )) || continue
  for t in "${targets[@]}"; do
    t=${t//\"/}; t=${t//\'/}
    [[ "$t" == *'$'* || "$t" == *'`'* ]] && block "rm -rf target '$t' cannot be verified."
    [[ "$t" == "~"* ]] && t="$HOME${t:1}"
    t=$(posix "$t"); [[ "$t" == /* ]] || t="$base/$t"
    p=$(realpath -m "$t")
    [[ "${p,,}" == "${root,,}/"* ]] || block "rm -rf outside the repo ($p)."
  done
done < <(tr ';&|\n' '\n\n\n\n' <<<"$cmd")
exit 0
