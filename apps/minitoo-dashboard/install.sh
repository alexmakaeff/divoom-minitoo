#!/usr/bin/env bash
# Installs the MiniToo dashboard: config, calendar helper, launchd daemon,
# Claude Code hooks + status line, and the /dashboard-city command.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd -P)"
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd -P)"
HOME_DIR="${MINITOO_DASHBOARD_HOME:-$HOME/.minitoo-dashboard}"
CLAUDDY_CONFIG="${CLAUDDY_CONFIG:-$HOME/.clauddy/config}"
SETTINGS="$HOME/.claude/settings.json"
LABEL="local.minitoo.dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN="$APP_DIR/bin/minitoo-dashboard"
HOOK="$APP_DIR/bin/dashboard-hook.sh"
STATUSLINE="$APP_DIR/bin/statusline.py"
PY="$(command -v python3 || true)"

note() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
ask() { local reply; read -r -p "$1" reply || true; printf '%s' "${reply:-$2}"; }
settings_tool() { PYTHONPATH="$APP_DIR" "$PY" -m minitoo_dashboard.settings "$@"; }

[ "$(uname -s)" = Darwin ] || die "macOS only."
[ -n "$PY" ] || die "python3 is required."
"$PY" -c 'import PIL' 2>/dev/null || die "Pillow is required: python3 -m pip install --user Pillow"
command -v swiftc >/dev/null || die "Xcode Command Line Tools are required: xcode-select --install"
[ -f "$APP_DIR/fonts/PressStart2P-Regular.ttf" ] || die "missing font: $APP_DIR/fonts/PressStart2P-Regular.ttf"
case "$HOME_DIR" in *" "*) die "MINITOO_DASHBOARD_HOME must not contain spaces (the Bluetooth helper splits on spaces)." ;; esac
mkdir -p "$HOME_DIR/sessions" "$HOME_DIR/cache"

# 1. Device and Clauddy
mac=""
if [ -f "$CLAUDDY_CONFIG" ]; then
  mac="$(sed -n 's/^CLAUDDY_MINITOO_MAC=//p' "$CLAUDDY_CONFIG" | head -n 1 | tr -d "\"'")"
  note "Clauddy found: alerts will use its 'alerting' face."
else
  note "Clauddy is not installed: alerts will show as a red full-screen frame (slower)."
  note "For instant alert faces install apps/clauddy first."
fi
if [ -z "$mac" ]; then
  mac="$("$PY" "$REPO_ROOT/apps/clauddy/tools/detect-minitoo-mac.py" 2>/dev/null | head -n 1 | cut -f1 || true)"
fi
[ -n "$mac" ] || mac="$(ask 'Bluetooth MAC of your MiniToo (AA:BB:CC:DD:EE:FF): ' '')"
[[ "$mac" =~ ^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$ ]] || die "invalid Bluetooth MAC: $mac"

# 2. Helpers
if [ ! -x "$REPO_ROOT/core/divoom-send.app/Contents/MacOS/divoom-send" ]; then
  note "Building the Bluetooth helper..."
  (cd "$REPO_ROOT/core" && ./build.sh)
fi
note "Building the calendar helper..."
"$APP_DIR/calendar-helper/build.sh" >/dev/null
note "Requesting Calendar access — approve the macOS prompt for 'calendar-helper'."
open -n -W "$APP_DIR/calendar-helper/calendar-helper.app" --args --request-access --out "$HOME_DIR/cache/calendar-access.json" || true
cal_status="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status",""))' "$HOME_DIR/cache/calendar-access.json" 2>/dev/null || true)"
if [ "$cal_status" != ok ]; then
  note "Calendar access not granted; the calendar page stays hidden."
  note "Grant it later: System Settings > Privacy & Security > Calendars > calendar-helper."
fi

# 3. Config
lang="$(ask 'On-screen language [en/ru] (en): ' en)"
case "$lang" in en|ru) ;; *) lang=en ;; esac
unit=celsius
[ "$(defaults read -g AppleTemperatureUnit 2>/dev/null || true)" = Fahrenheit ] && unit=fahrenheit
cat <<'EOM'

Where should Claude limits come from?
  1) Status line only (default). Documented and safe, but limits refresh only
     while you use the terminal `claude` CLI (not the desktop app or VS Code).
  2) Also ask Anthropic directly every 5 minutes, like Claude Code's /usage.
     Works with any Claude client, but:
       - it uses an undocumented endpoint that may change or break at any time;
       - a small helper reads your Claude Code login token from the Keychain
         (only percentages leave it; the token is never printed or stored);
       - using a subscription token outside Claude Code is a grey area in
         Anthropic's terms. Your call.
     If it stops working, the dashboard falls back to the status line.
EOM
limits=statusline
if [ "$(ask 'Choose 1 or 2 [1]: ' 1)" = 2 ]; then
  "$APP_DIR/usage-helper/build.sh" >/dev/null
  note "macOS will ask to let 'usage-helper' use the 'Claude Code-credentials' Keychain item."
  note "Choose 'Always Allow' so the dashboard can check every 5 minutes."
  if "$APP_DIR/usage-helper/usage-helper" >/dev/null; then
    limits=direct
    note "Direct Claude limits: working ✓"
  else
    note "Direct Claude limits did not work (Keychain access denied or request failed); using the status line only."
    note "Switch later by setting CLAUDE_LIMITS=direct in $HOME_DIR/config."
  fi
fi
codex=off
if [ -d "${CODEX_HOME:-$HOME/.codex}/sessions" ]; then
  case "$(ask 'Codex (ChatGPT) found. Show its limits next to Claude? [Y/n] ' y)" in
    n|N|no|No) ;;
    *) codex=on ;;
  esac
fi
"$BIN" init --mac "$mac" --lang "$lang" --temp-unit "$unit" --claude-limits "$limits" --codex "$codex"
while true; do
  city="$(ask 'City for weather (empty to skip): ' '')"
  if [ -z "$city" ]; then
    note "Weather disabled. Set it later with: minitoo-dashboard city \"<name>\""
    break
  fi
  "$BIN" city "$city" && break
done

# 4. Claude Code settings
mkdir -p "$HOME/.claude"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
backup="$SETTINGS.bak-minitoo-dashboard.$(date +%Y%m%d%H%M%S)"
cp "$SETTINGS" "$backup"
note "Backed up Claude Code settings to $backup"
install_hooks=1
replace=()
if settings_tool has-clauddy --settings "$SETTINGS"; then
  answer="$(ask 'Replace Clauddy hooks with dashboard hooks? Both cannot drive the screen at once. [Y/n] ' y)"
  case "$answer" in
    n|N|no|No) install_hooks=0; note "Keeping Clauddy hooks. Dashboard hooks not installed; the badge stays grey." ;;
    *) replace=(--replace-clauddy) ;;
  esac
fi
if [ "$install_hooks" = 1 ]; then
  settings_tool install-hooks --settings "$SETTINGS" --hook "$HOOK" --saved "$HOME_DIR/clauddy-hooks.json" ${replace[@]+"${replace[@]}"}
fi
sl_state="$(settings_tool install-statusline --settings "$SETTINGS" --statusline "$STATUSLINE")"
if [ "$sl_state" = other ]; then
  cat <<EOM

You already have a status line. To feed Claude limits to the dashboard, add this
near the top of your status line script (after it reads stdin into \$input):

  printf '%s' "\$input" | "$STATUSLINE"

EOM
fi

# 5. Slash command and PATH
mkdir -p "$HOME/.claude/commands" "$HOME/.local/bin"
settings_tool render-template --src "$APP_DIR/commands/dashboard-city.md" \
  --dst "$HOME/.claude/commands/dashboard-city.md" --set "__BIN__=$BIN"
ln -sf "$BIN" "$HOME/.local/bin/minitoo-dashboard"

# 6. launchd
mkdir -p "$HOME/Library/LaunchAgents"
settings_tool render-template --src "$APP_DIR/launchd/$LABEL.plist.in" --dst "$PLIST" --xml \
  --set "__PYTHON__=$PY" --set "__APP__=$APP_DIR" --set "__HOME__=$HOME_DIR"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

note ""
note "MiniToo dashboard installed. Start a new Claude Code session so the hooks load."
note "Commands: minitoo-dashboard status | preview | city \"<name>\" | pause | resume | logs"
note "(~/.local/bin must be on your PATH for the short command.)"
sleep 3
"$BIN" status || true
