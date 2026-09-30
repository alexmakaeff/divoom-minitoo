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
- Units: taken from macOS locale settings (`°C`/`°F`, km/h or m/s vs mph);
  overridable in config.
- Font: a bitmap/pixel font with Latin + Cyrillic under OFL or MIT, vendored in
  the app directory with its licence file.

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
  alerting; `Stop` → chilling; `SessionEnd` → remove.
- The daemon watches the sessions directory (poll ≤1 s) and reacts on change.

### Refresh cadence

| Data | Refresh |
| --- | --- |
| Weather | every 15 min |
| Calendar | every 60 s |
| Claude limits | read on every render (written by the status line) |
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
WIND_UNIT=ms             # ms | kmh | mph
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

## 9. Distribution

Everything lives in `apps/minitoo-dashboard/` with an English README and
preview screenshots. It is ready for a pull request to `bugzmanov/divoom-minitoo`
or for publishing from the owner's fork; the owner will choose later.

## 10. Follow-ups (out of scope)

- Joystick control (depends on spike item 6): e.g. peek at the dashboard for
  20 s while an alert is shown; macOS hotkey as a fallback.
- Night mode (screen off when the Mac is locked or at night).
