#!/usr/bin/env bash
# Claude Code hook: records this session's state for the MiniToo dashboard.
# Usage: dashboard-hook.sh <working|alerting|chilling|end>   (hook JSON on stdin)
# Never fails and never blocks: the dashboard daemon reads these files.
set -u
state="${1:-}"
case "$state" in
  working|alerting|chilling|end) ;;
  *) exit 0 ;;
esac
input="$(cat 2>/dev/null || true)"
sid="$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p' | head -n 1)"
[ -n "$sid" ] || sid="unknown"
dir="${MINITOO_DASHBOARD_HOME:-$HOME/.minitoo-dashboard}/sessions"
mkdir -p "$dir" 2>/dev/null || exit 0
if [ "$state" = "end" ]; then
  rm -f "$dir/$sid"
  exit 0
fi
tmp="$dir/.$sid.$$"
printf '%s %s\n' "$state" "$(date +%s)" > "$tmp" 2>/dev/null && mv -f "$tmp" "$dir/$sid" 2>/dev/null
exit 0
