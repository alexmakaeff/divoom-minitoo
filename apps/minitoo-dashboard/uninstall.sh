#!/usr/bin/env bash
# Removes the MiniToo dashboard and restores Clauddy hooks if they were replaced.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd -P)"
HOME_DIR="${MINITOO_DASHBOARD_HOME:-$HOME/.minitoo-dashboard}"
SETTINGS="$HOME/.claude/settings.json"
LABEL="local.minitoo.dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3)"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
if [ -f "$SETTINGS" ]; then
  cp "$SETTINGS" "$SETTINGS.bak-minitoo-dashboard-uninstall.$(date +%Y%m%d%H%M%S)"
  PYTHONPATH="$APP_DIR" "$PY" -m minitoo_dashboard.settings uninstall --settings "$SETTINGS" \
    --hook "$APP_DIR/bin/dashboard-hook.sh" --statusline "$APP_DIR/bin/statusline.py" \
    --saved "$HOME_DIR/clauddy-hooks.json"
fi
rm -f "$HOME/.claude/commands/dashboard-city.md" "$HOME/.local/bin/minitoo-dashboard"
read -r -p "Delete $HOME_DIR (config, caches, logs)? [y/N] " answer || true
case "${answer:-n}" in y|Y|yes) rm -rf "$HOME_DIR" ;; esac
echo "MiniToo dashboard removed. Start a new Claude Code session so the hook changes load."
