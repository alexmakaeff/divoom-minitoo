# MiniToo Dashboard — design

Date: 2026-09-29
Status: draft, awaiting owner review
App location: `apps/minitoo-dashboard/`

## 1. Goal

Turn the Divoom MiniToo into a glanceable desk dashboard that coexists with the
Claude Code status display (Clauddy):

- **Weather** for a user-chosen city.
- **Next calendar event today** from macOS Calendar.
- **Claude subscription limits** (Pro/Max): 5-hour and weekly usage.
- **Claude status** as a small badge on every dashboard page; a full-screen
  alert when Claude is waiting for the user.

The dashboard must stay readable during long Claude sessions, keep its data
fresh without user action, and be installable by other people from GitHub.

### Non-goals (for this version)

- Joystick-triggered dashboard (planned follow-up, see §10).
- Night mode, "on air" indicator, sounds, other data sources.
- Linux/Windows support (the Bluetooth transport is macOS-only).

## 2. Screen behaviour

Aggregated Claude status decides what the device shows:

| Status | Screen |
| --- | --- |
| `alerting` | Clauddy's `alerting` face (instant `ClockId` switch). Fallback when Clauddy is not installed: a full-screen alert frame with a red border, sent as a live animation. |
| `working` | Dashboard, status badge **orange**. |
| `chilling` | Dashboard, status badge **grey**. |

### Pages

The dashboard is one live animation (`0x8B`, FINDINGS.md §8i) with one frame
per page and a per-frame delay of `PAGE_SECONDS` (default 8). The device cycles
the pages on its own. Each page carries a static badge in the bottom-right
corner and page dots at the bottom.

1. **Weather**: condition icon, temperature, city, condition text with
   high/low, and a short precipitation hint when rain or snow is expected later today.
2. **Calendar**: start time, time remaining ("in 25 min"), and the event title
   (wrapped to two lines, ellipsized). An event in progress shows
   "now · until 15:00" until it ends.
3. **Claude**: 5-hour usage in percent, a bar, and the time until reset; weekly usage
   in percent and a bar.

A static badge (colour only) keeps the upload at one frame per page. A spinning
badge would need ~16 duplicated frames per page because the device honours only
one frame delay per upload.

### Calendar rules

- Only events that **start or are in progress today** (local time). Tomorrow's
  events appear after midnight.
- All-day events are ignored.
- An in-progress event is shown until its end; then the next one.
- No remaining events today → the calendar page is **skipped**.
- Calendars: all by default; configurable list.

### Language and units

- On-screen language: English by default, Russian optional (`LANG=en|ru`).
  Event titles are shown as written, so the font must cover Latin and Cyrillic.
- Units: taken from macOS locale settings (`°C`/`°F`);
  overridable in config.
- Font: a bitmap/pixel font with Latin + Cyrillic under OFL or MIT, vendored in
  the app directory with its licence file.

### Amendment (2026-09-30): single static screen

After living with the 3-page rotation the owner found that page flips pull the
eye and compete with the alert. The dashboard is now **one static screen**
(the brainstorming layout "A", three rows): weather (icon, temperature,
high/low, precipitation hint or condition; the city name is dropped), next
event (time and time left, or "NOW / until HH:MM", title on one line; "No events
left" when none), Claude (5-hour and weekly bars with percentages, reset time
or "as of"). The badge stays bottom-right. It is sent as a single-frame `0x8B`
animation; `PAGE_SECONDS` is removed. Sections above that describe pages and
page dots are superseded by this amendment.

### Amendment (2026-09-30): optional direct limits source

Neither the desktop app nor the VS Code extension runs status line commands
(verified by the owner). Owner decision: `CLAUDE_LIMITS=statusline|direct`,
chosen during install (default `statusline`, with the risks explained). With
`direct`, a separate `usage-helper` binary reads the Claude Code Keychain item
and calls the endpoint behind `/usage` every 5 minutes. Only
`five_hour`/`seven_day` utilization and reset times leave it, and it never
refreshes the token. It is a separate binary so that the user's "Always Allow"
Keychain grant covers only it, not `/usr/bin/security`. Failures keep the last
data and are shown by `status`. The status line stays installed as a fallback.
Verified on the owner's Pro account on 2026-09-30.

### Amendment (2026-10-01): reminders in the event row

The event row also shows open reminders from the Reminders app, read by the
calendar helper (separate Reminders permission). Priority, as the owner chose:
(a) the next timed item today, either an event or a timed reminder, earliest
first; (c) otherwise the oldest overdue reminder (a timed reminder past its
time, or a date-only reminder from an earlier day); (b) otherwise a date-only
reminder due today. "+N" counts the other open reminders of all three kinds and
is shown with events too. Reminders get a checkbox; "OVERDUE" is yellow, because
red belongs to the Claude alert. `CALENDARS` also filters Reminders lists.

### Amendment (2026-10-01): Codex limits and status

The owner uses a ChatGPT subscription through Codex (the ChatGPT desktop app and
the VS Code extension, never the CLI) as much as Claude and wants to see at a
glance where quota is left. With `CODEX=on` the limits zone becomes a table
(brainstorming layout "A"); with `CODEX=off` (the default) the screen is
unchanged.

**Data.** Every Codex client writes session logs to
`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`; the date in the path is when the
conversation *started*, and one file keeps growing for as long as the
conversation does, so files are chosen by mtime, never by name. Lines are JSON
objects `{"timestamp", "type", "payload"}`. Verified on the owner's Mac
(originators `Codex Desktop`, `codex_work_desktop`, `codex_vscode`):

- `event_msg` / `token_count` carries `rate_limits` with `primary` and
  `secondary`, each `{used_percent, window_minutes, resets_at (epoch s)}`.
  Windows are identified by `window_minutes` (300 → 5-hour, 10080 → week), not
  by position: some records carry only the week window, as `primary`. Only
  `limit_id` `"codex"` counts; others (seen: `"premium"` with null windows) are
  skipped and the search goes on to older lines.
- `event_msg` / `task_started`, `task_complete`, `turn_aborted` mark the start
  and end of every turn.

**`sources/codex.py`.** Every 5 s the daemon lists session files modified in the
last 30 minutes (`archived_sessions/` is ignored) and reads each one backwards
from the end, in chunks, until it has found what it needs or read 4 MB.

- Limits: the newest `token_count` with `rate_limits` across those files,
  written to `cache/codex.json` in the Claude cache-record format
  (`captured_at` = that event's `timestamp`, `five_hour`, `seven_day` with
  `used_percentage` and `resets_at`). The cache is only replaced by a reading
  with a newer `captured_at`; with no recent files, or none with `rate_limits`
  within the 4 MB read limit, it keeps the last record, so an old reading is
  still shown as "as of".
- Working: true when, in any of those files, the latest of
  `task_started` / `task_complete` / `turn_aborted` is `task_started`. The 30-minute
  mtime window is the same safety net as for Claude sessions. `cache/codex.json`
  also holds `working` and `checked_at`; the flag is ignored when `checked_at` is
  more than 60 s old (daemon not running). Results are memoised per file by
  (mtime, size), so unchanged files are not re-read. The whole history is
  walked every 60 s; in between only the files that were recent at the last
  check and today's/yesterday's date directories are stat-ed, so a resumed old
  conversation is noticed within a minute. `cache/codex.json` is rewritten only
  when something besides `checked_at` changed, or every 30 s.
- A missing `~/.codex`, unreadable files, malformed lines or unknown formats
  keep the last data silently; they never break the screen. A refresh that
  raises (e.g. unwritable cache) keeps the last data, is logged when the error
  first appears or changes and then at most every 10 min, logs "recovered" once,
  and is shown by `status` as "Codex error". Only `~/.codex` is
  read: `CODEX_HOME` from the user's shell does not reach the launchd daemon.
- After the first look at a file (last 4 MB), only bytes appended since then are
  read, starting at the end of the last complete line; a turn or limits record
  not found in the new bytes keeps the previous value, so a multi-megabyte tool
  output cannot hide a running turn. A file that shrank is read afresh.
- Codex has no alert screen: permission prompts are out of scope.

**Shared view.** `ClaudeView` becomes `LimitsView` and is used for both
services, with the same staleness (10 min → "as of HH:MM") and reset rules.

**Screen** (limits zone, y 84–127). A label column ("5ч"/"нд", "5h"/"wk") and
two columns, Claude (orange) and Codex (teal `#10A37F`). Each column: the name
with a status square after it (service colour when working, grey otherwise),
a 5-hour bar and a week bar with percentages right-aligned to the column edge
(so "100%" fits), and a footer with the 5-hour reset time ("2ч10м"), "на HH:MM" /
"@HH:MM" when the data is stale, or "reset" when the window has reset. The week
label is shortened to "нд" in Russian. Percentages are **used** for both services
(Codex logs `used_percent`), although the ChatGPT/Codex apps display the
**remaining** share; owner decision 2026-10-01: keep one direction on one screen
and explain it in the README. A column without data shows empty bars and "--". The bottom-right badge
is dropped in this layout; Claude's square takes its role. Amended 2026-10-01
(owner): the Codex column never shows "as of" — its data go stale after every
10 idle minutes, while `resets_at` stays exact — so it always shows the reset
timer (or "reset"); the Claude column keeps "as of", which there means the
source broke.

**Config.** `CODEX=off|on` in `~/.minitoo-dashboard/config`. `install.sh` asks
when `~/.codex` exists and otherwise leaves the setting alone. `status` reports when the Codex data was captured and
whether Codex is working.

### Amendment (2026-10-02): no background Keychain prompts

Incident on 2026-10-02: Claude Code rewrote its Keychain item at 07:13 and the
helper's grant stopped applying. Each helper run then waited 30 s for an
unanswered prompt, and with the device out of range `dv start` added ~20 s.
The daemon took the ~50 s tick for sleep/wake and refreshed everything, so the
helper ran (and prompted) every ~50 s for an hour, 66 runs in total. Fixes:

- Wake detection measures the gap from the end of the previous tick, so slow
  work inside a tick is never mistaken for sleep.
- `usage-helper` is non-interactive by default (`SecKeychainSetUserInteractionAllowed(false)`
  plus `kSecUseAuthenticationUIFail`; the login keychain is a file keychain).
  Missing access returns `needs_access` at once. Only `--interactive` may prompt,
  and only `install.sh` and `minitoo-dashboard grant-keychain` pass it.
- A Keychain access error retries every 30 min instead of 5 and is logged
  once per distinct message. `status` hints at `grant-keychain` and hides the
  error once newer limits are cached.
- Losing access shows one macOS notification (`osascript display notification`,
  never a dialog) per episode: again only after access came back and was lost
  again, or after a daemon restart.

### Amendment (2026-10-02): refused usage requests

After the owner's plan lapsed (2026-10-02 11:38) the endpoint answered 403, then
alternately 429, every 5 minutes; after renewing it kept refusing. Changes:
`usage-helper` reports the API error type/message (≤200 chars, no credentials)
and `Retry-After`. The daemon treats 403/429 as `RefusedError` and backs off
10/20/40/60 min (or `Retry-After` if longer); a wake does not cut the hold
short; success resets it. `minitoo-dashboard refresh-limits` drops a
`refresh-limits` file the daemon consumes to check at once, and reports the
result from `state.json` (`limits_checked_at`).

### Amendment (2026-10-05): limit alerts

When a Claude or Codex (with `CODEX=on`) 5-hour or weekly window reaches
`LIMIT_ALERT` % used (default 90, `off` disables), the daemon shows a
full-screen frame in the service colour (agent name, window, used %, time to
reset) for 10 s, counted from the end of the transfer, then resends the
dashboard. Checked every tick from the cached `claude.json` / `codex.json`, so
the status-line source works too.

- A window is announced once: `cache/limit-alerts.json` stores its `resets_at`,
  and it is not announced again until that time has passed (a `resets_at` that
  drifts by seconds is still the same window). A daemon restart does not repeat.
- Several crossings at once are shown one after another, Claude first.
- Priority: question alert > limit alert > dashboard. A question cancels the rest
  of a limit alert. Paused: nothing is shown; still-due windows follow `resume`.
  A device error leaves the window unannounced, retried with the normal backoff.
- `state.json` reports `shown: limit_alert`; `preview` writes `limit-alert.png`.

Why not the firmware notification (`0x50`, FINDINGS §8j): on 2026-10-05 it
showed only the app icon, never the text, both over the dashboard and over a
built-in clock face, ASCII or Cyrillic. Our own frame also allows Cyrillic and
the service colours.

### Amendment (2026-10-05): silent device

`dv` acknowledges at the FIFO, so a MiniToo that ignores the Mac looked healthy:
after the owner used the Divoom phone app (the phone kept the link even with the
app closed), the dashboard kept "sending" for 30 min to a clock face; a fresh
RFCOMM connection opened but got no reply either, and only a device restart
helped.

- The MiniToo answers each upload (`04 8b 55`) and broadcasts about once a second
  (`04 f7 55`). `Device.send_rawfile` returns the size of `/tmp/divoom-send.log`
  before the send; any later `rx[` line counts as a reply (a shorter log was
  truncated by `dv start`: read from the start).
- No reply 10 s after a send: unanswered, and the frame is sent again at once as a
  probe. Two unanswered in a row: silent. A pending check is not reset by newer
  sends, so frequent frames cannot hide silence.
- Silent: one warning in the log, one macOS notification per episode, `dv stop`
  so the next send reconnects, and the normal device backoff (30 s … 5 min).
  `state.json` has `device_silent_since`; `status` prints a "Device reply" hint.
  The first reply logs "device responding again" and the dashboard is resent.
- Only frame uploads are checked; the Clauddy face switch is not.
- Verified 2026-10-05 with the phone app connected: silent detected ~22 s after
  `resume`, notification shown. Disconnecting MiniToo in the phone's settings did
  not bring replies back; after a MiniToo restart the phone reconnected first;
  with the phone's Bluetooth off the Mac got the device ("device responding
  again" on the next backoff send). Hints say: phone Bluetooth off + restart.
  Whether the restart is needed once the phone's Bluetooth is off is untested.

## 3. Architecture

One long-running process owns the screen. Everything else only writes files.

```
Claude Code hooks ──► sessions/<session_id>   ┐
Status line script ─► cache/claude.json        │
Weather fetcher ────► cache/weather.json       ├─► dashboardd ─► dv (FIFO) ─► MiniToo
Calendar helper ────► cache/calendar.json      ┘      (launchd)
```

All state lives in `~/.minitoo-dashboard/`.

### Components

| Unit | Language | Responsibility |
| --- | --- | --- |
| `dashboard-hook.sh` | bash | Hook command. Reads the hook JSON from stdin, writes `<state> <epoch>` to `sessions/<session_id>`; on `SessionEnd` deletes it. Must return in milliseconds and never fail the hook. |
| `statusline.sh` | bash | Status line command. Extracts `rate_limits` from its stdin JSON and writes `cache/claude.json` (with capture time) atomically. Prints nothing, or a minimal line. |
| `calendar-helper.app` | Swift | EventKit reader built as a minimal `.app` bundle (same approach as `core/build.sh`) so macOS can grant Calendar access. Prints today's timed events as JSON. |
| `dashboardd.py` | Python | The daemon: schedules fetchers, aggregates status, renders, sends. |
| `status.py` | Python | Pure function: session files + now → `alerting / working / chilling`. |
| `sources/weather.py` | Python | Open-Meteo forecast fetch + normalisation. Geocoding for the `city` command. |
| `sources/calendar.py` | Python | Runs the helper, applies the calendar rules above. |
| `sources/claude.py` | Python | Reads `cache/claude.json`, applies staleness/reset rules. |
| `sources/codex.py` | Python | Reads recent Codex session logs → `cache/codex.json` and the Codex working flag. |
| `render.py` | Python | Pillow: model → list of 160×128 page images. Also used by `preview`. |
| `encode.py` | Python | Pages → `W2.c.r` blob (header `23 <n> <speed_be16> 08 0a`, JPEG frames) → `0x8B` chunk file. |
| `device.py` | Python | Talks to the `dv` daemon: ensure running, send rawfile, switch ClockId. Owns retry/backoff. |
| `minitoo-dashboard` | Python CLI | `city`, `status`, `preview`, `pause`, `resume`, `logs`. |

Units are small and independently testable. `status`, `calendar` rules,
staleness and `render` are pure functions with no I/O.

### Status aggregation

- Each session file holds its latest state and timestamp.
- Any session `alerting` → `alerting`; else any `working` → `working`; else
  `chilling`.
- A session file older than 30 minutes is ignored (safety net for sessions that
  ended without `SessionEnd`).
- Hooks: `UserPromptSubmit`, `PreToolUse` → working; `Notification` →
  alerting only for prompts that need the user (`permission_prompt`,
  `elicitation_dialog`, `elicitation_url_dialog`, `agent_needs_input`; not the
  `idle_prompt` sent ~60 s after every turn); `Stop` → chilling; `SessionEnd` →
  remove.
- The daemon watches the sessions directory (poll ≤1 s) and reacts on change.

### Refresh cadence

| Data | Refresh |
| --- | --- |
| Weather | every 15 min |
| Calendar | every 60 s |
| Claude limits | read on every render (written by the status line) |
| Codex limits + working | every 5 s when `CODEX=on` |
| Render + send | every 60 s (keeps "in N min" accurate), on status change, on data change |

Before sending, the daemon compares the new blob with the last one sent and
skips identical uploads.

### Device access

- The dashboard reuses the repo's `dv` daemon and `/tmp/divoom.fifo`, like
  Clauddy. `dashboardd` is the only writer while it runs.
- Alerting face: the `ClockId` from `~/.clauddy/config`, sent as the same
  selector JSON Clauddy uses (`clauddy_selector_json`). This is read-only
  reuse of Clauddy's config.
- `pause` stops sending and stops the `dv` daemon so the device is free (e.g.
  for the phone app); the pause survives daemon restarts until `resume`.

## 4. Error handling

Principle: never block Claude, never show errors on the device; show the last known
data or skip a page, and log the details to `~/.minitoo-dashboard/dashboard.log`.

| Situation | Behaviour |
| --- | --- |
| Device off / out of range / held by phone | Retry with backoff 30 s → 1 → 2 → 5 min; resend current frame on reconnect. |
| Mac wakes from sleep | Refresh all sources and resend immediately. |
| Daemon crash | launchd `KeepAlive` restarts it. |
| No network | Keep last weather with an age marker ("2h ago"); skip the weather page when older than 6 h. |
| Claude limits stale | Show with "as of HH:MM"; a window whose `resets_at` has passed shows "reset" instead of old numbers. |
| No Claude data yet | Claude page shows "no data yet"; it is never skipped, so the screen is never empty. |
| Calendar access denied | Skip calendar page; `status` and the installer explain how to grant access. |
| No city configured | Skip weather page. |
| Session closed while working | `SessionEnd` removes it; 30-min expiry as a fallback. |
| Clauddy not installed | Alert frame fallback (§2). |

## 5. Installation and configuration

### `apps/minitoo-dashboard/install.sh`

1. Check for Clauddy (`~/.clauddy/config`). If missing, explain the alert
   fallback and continue.
2. Check Python 3 + Pillow; build `calendar-helper.app`; trigger the Calendar
   permission prompt.
3. Ask for the city; geocode via Open-Meteo; confirm ("Moscow, Russia ✓");
   choose from a list when ambiguous. Empty answer → weather disabled.
4. Detect units from macOS locale; ask for the on-screen language (default
   English).
5. Write `~/.minitoo-dashboard/config`.
6. Install and load `~/Library/LaunchAgents/local.minitoo.dashboard.plist` (RunAtLoad,
   KeepAlive).
7. Merge hooks into `~/.claude/settings.json`: back up first, remain idempotent,
   and replace existing `clauddy-hook.sh` entries only after asking.
8. Status line: install ours when none is configured; otherwise print the one
   line the user must add to their own script.
9. Install the `/dashboard-city` command into `~/.claude/commands/`.

`uninstall.sh` reverses every step and restores Clauddy hooks if they were
replaced.

### Config: `~/.minitoo-dashboard/config`

`KEY=value` lines, same style as Clauddy:

```
CITY_NAME=Moscow, Russia
CITY_LAT=55.7558
CITY_LON=37.6173
TEMP_UNIT=celsius        # celsius | fahrenheit
LANG=en                  # en | ru
CALENDARS=all            # all | comma-separated calendar titles
PAGE_SECONDS=8
```

### Changing the city

- `minitoo-dashboard city "Kazan"`: geocode, confirm, save.
- `/dashboard-city Kazan` in Claude Code runs the same command.
- Editing the config by hand works too.
- The daemon picks up config changes on its next cycle.

## 6. Dependencies

- macOS, Python 3.9+ (system Python is enough), Pillow (already required by
  Clauddy), Xcode Command Line Tools (`swiftc`, already required by `core/`).
- Network: Open-Meteo forecast and geocoding APIs (no key, no account).
- No changes to `core/`. Clauddy is optional but recommended.

## 7. Testing

- **Unit tests** with stdlib `unittest` (no new dependencies), run by
  `python3 -m unittest discover apps/minitoo-dashboard/tests`:
  - status aggregation incl. expiry and `SessionEnd`;
  - calendar rules: today only, all-day skipped, in-progress, none left → skip;
  - staleness rules for weather and Claude limits, `resets_at` passed;
  - config parsing and defaults;
  - render: 160×128 frames, pages skipped correctly, Cyrillic text renders;
  - encode: header bytes, frame count, 256-byte chunking, BE/LE lengths.
- **`preview`** renders pages to PNG without a device (also used for README
  screenshots).
- **Hardware checklist** run with the owner after the spike (§8).

## 8. Spike before implementation

These unknowns decide whether the `0x8B` design holds. Test them on the device
first, with throwaway code:

1. Does a `0x8B` animation stay on screen indefinitely, or does the device fall
   back to the clock/gallery?
2. Does re-sending every 60 s flicker or show a loading screen?
3. Dashboard → Clauddy alerting face (`ClockId`) → dashboard: both directions work?
4. Is an 8000 ms frame delay honoured?
5. Does the Claude desktop app run the status line command (so `rate_limits`
   reach the cache)?
6. (For §10) Does the device report joystick presses to the host?

If (1) or (2) fails, stop and revisit the display path with the owner before
building the rest.

### Spike results (2026-09-30)

Run on the owner's MiniToo (firmware 2.4.0) with Clauddy hooks neutralised
(they switch faces on every Claude Code event and would otherwise interfere).

| # | Question | Result |
| --- | --- | --- |
| 1 | 0x8B stays on screen | Pass: stayed 20+ min with no fallback to clock/gallery. |
| 2 | Re-send every 60 s | Pass: 5 sends, no flicker/loading/corruption; 0 chunk re-requests at 20 ms and 5 ms pacing (~0.76 s per 3-page upload at 20 ms). Chosen `SEND_DELAY_MS=20`. |
| 3 | Dashboard ↔ alert face | Pass: `SetClockSelectId` to Clauddy's alerting face and back to a 0x8B animation both work. |
| 4 | 8000 ms frame delay | Pass: pages change every ~8 s. |
| 5 | Desktop app runs status line | **Fail**: with a probe `statusLine` configured, a new Code-tab session in the Claude desktop app never ran it. |
| 6 | Joystick reported to host | **Fail**: up/down (face carousel), left/right and press produced only keepalive frames; nothing is reported. |

Consequences: the 0x8B display path is confirmed. The joystick follow-up (§10)
is not possible as designed. Item 5 affects where Claude limits come from; see
the owner's decision below.

**Owner decision (2026-09-30), item 5:** keep the documented status-line source.
Verified in a terminal `claude` session (v2.1.241): `rate_limits.five_hour` and
`seven_day` arrive for the owner's Pro plan. Limits refresh only while a client
that runs the status line is in use; otherwise the Claude page shows the last
values with "as of HH:MM". Whether the VS Code extension runs the status line is
checked during the hardware checklist.

## 9. Distribution

Everything lives in `apps/minitoo-dashboard/` with an English README and
preview screenshots. It is ready for a pull request to `bugzmanov/divoom-minitoo`
or for publishing from the owner's fork; the owner will choose later.

## 10. Follow-ups (out of scope)

- Joystick control (depends on spike item 6): e.g. peek at the dashboard for
  20 s while an alert is shown; macOS hotkey as a fallback.
- Night mode (screen off when the Mac is locked or at night).
