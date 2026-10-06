#!/usr/bin/env bash
# Uploads Codex's alert animation into Clauddy's "working" custom face (the
# dashboard no longer uses that slot) and records it as CLAUDDY_CLOCK_CODEX in
# the Clauddy config. The dashboard is paused during the upload (~20-40 s).
# Usage: upload-codex-face.sh [GIF]   (default: assets/codex-alerting.gif)
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
CLAUDDY_DIR="$(cd "$APP_DIR/../clauddy" && pwd -P)"
# shellcheck source=../../clauddy/lib/clauddy-lib.sh
. "$CLAUDDY_DIR/lib/clauddy-lib.sh"

gif="${1:-$APP_DIR/assets/codex-alerting.gif}"
config="$CLAUDDY_CONFIG_DEFAULT"
BIN="$APP_DIR/bin/minitoo-dashboard"
STATE="${MINITOO_DASHBOARD_HOME:-$HOME/.minitoo-dashboard}/state.json"

[ -f "$gif" ] || clauddy_die "missing GIF: $gif"
clauddy_require_macos
clauddy_require_python_pillow
clauddy_ensure_dv_app
clauddy_load_config "$config"
clock_id="$CLAUDDY_CLOCK_WORKING"
page_index="${CLAUDDY_PAGE_WORKING:-1}"
style_id="${CLAUDDY_STYLE_ALERTING:-798}"  # same frame decoration as Claude's alert

was_paused=0
[ -f "${MINITOO_DASHBOARD_HOME:-$HOME/.minitoo-dashboard}/paused" ] && was_paused=1
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/codex-face.XXXXXX")"
cleanup() {
  clauddy_stop_daemon
  rm -rf "$tmpdir"
  [ "$was_paused" = 1 ] || "$BIN" resume >/dev/null
}
trap cleanup EXIT

"$BIN" pause >/dev/null
for _ in $(seq 1 30); do  # the daemon lets go of the device on its next tick
  grep -q '"shown": "paused"' "$STATE" 2>/dev/null && break
  sleep 0.5
done

raw="$tmpdir/codex.raw"
file_id="codex-alerting-$(date +%s)"
python3 "$CLAUDDY_DIR/tools/encode-custom-raw.py" --input "$gif" --output "$raw" --file-id "$file_id" \
  --quality "${CLAUDDY_JPEG_QUALITY:-95}" --fit cover --resample nearest
clauddy_upload_custom codex "$CLAUDDY_MINITOO_MAC" "$file_id" "$raw" "$page_index" "$clock_id" "$style_id" \
  "$CLAUDDY_DEVICE_ID" 5

tmp="$config.tmp.$$"
grep -v '^CLAUDDY_CLOCK_CODEX=' "$config" > "$tmp" || true
printf 'CLAUDDY_CLOCK_CODEX=%s\n' "$clock_id" >> "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$config"
clauddy_note "Codex alert face uploaded (ClockId=$clock_id). The dashboard uses it from now on."
