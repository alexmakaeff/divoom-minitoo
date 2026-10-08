#!/usr/bin/env bash
# Claude Code / Codex hook: records this session's state for the MiniToo dashboard.
# Usage: dashboard-hook.sh <working|alerting|chilling|end> [codex]   (hook JSON on stdin)
# Never fails and never blocks: the dashboard daemon reads these files.
set -u
state="${1:-}"
case "$state" in
  working|alerting|chilling|end) ;;
  *) exit 0 ;;
esac
agent=""
[ "${2:-}" = codex ] && agent=" codex"
input="$(cat 2>/dev/null || true)"
sid="$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p' | head -n 1)"
[ -n "$sid" ] || sid="unknown"
dir="${MINITOO_DASHBOARD_HOME:-$HOME/.minitoo-dashboard}/sessions"
mkdir -p "$dir" 2>/dev/null || exit 0
# Codex fires PermissionRequest also when its own reviewer (approvals_reviewer=auto_review)
# decides; nobody is asked then. The thread's rollout log has the latest setting.
if [ "$state" = alerting ] && [ -n "$agent" ]; then
  rollout="$(find "${CODEX_HOME:-$HOME/.codex}/sessions" -name "rollout-*-$sid.jsonl" -print 2>/dev/null | head -n 1)"
  if [ -n "$rollout" ] && [ "$(grep -o '"approvals_reviewer":"[a-z_]*"' "$rollout" 2>/dev/null | tail -n 1)" = '"approvals_reviewer":"auto_review"' ]; then
    exit 0
  fi
fi
if [ "$state" = "end" ]; then
  rm -f "$dir/$sid"
  exit 0
fi
tmp="$dir/.$sid.$$"
printf '%s %s%s\n' "$state" "$(date +%s)" "$agent" > "$tmp" 2>/dev/null && mv -f "$tmp" "$dir/$sid" 2>/dev/null
exit 0
