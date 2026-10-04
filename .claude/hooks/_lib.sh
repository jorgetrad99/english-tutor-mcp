# Sourced by the hooks. Puts jq and uv on PATH when Claude Code was started with a stale
# PATH (common on Windows right after a winget install). Hooks still fail closed if jq is absent.
find_tool() {
  local name=$1 pkg=$2 base dir
  command -v "$name" >/dev/null && return 0
  [[ -n "${LOCALAPPDATA:-}" ]] || return 0
  base=$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || printf '%s' "$LOCALAPPDATA")
  for dir in "$base/Microsoft/WinGet/Links" "$base/Microsoft/WinGet/Packages/${pkg}"_* "$HOME/.local/bin"; do
    if [[ -x "$dir/$name.exe" || -x "$dir/$name" ]]; then
      PATH="$dir:$PATH"
      return 0
    fi
  done
}
find_tool jq jqlang.jq
find_tool uv astral-sh.uv
