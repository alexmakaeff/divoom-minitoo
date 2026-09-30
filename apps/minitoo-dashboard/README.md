# MiniToo Dashboard

A glanceable desk dashboard for the Divoom MiniToo: **weather**, **today's next
calendar event** and **Claude subscription limits**, with a small Claude Code
status badge on every page. When Claude is waiting for you, the screen switches
to an alert.

It pairs with [Clauddy](../clauddy/README.md): the dashboard replaces Clauddy's
`working` and `chilling` faces and uses Clauddy's `alerting` face for alerts.

## What it shows

The device cycles through the pages by itself (8 s each by default):

| Weather | Next event today | Claude limits | Alert (without Clauddy) |
| :---: | :---: | :---: | :---: |
| ![weather](docs/page-1.png) | ![event](docs/page-2.png) | ![claude](docs/page-3.png) | ![alert](docs/alert.png) |

- **Weather**: current temperature, condition, today's high/low and when rain or
  snow is expected, for a city you choose. Data from [Open-Meteo](https://open-meteo.com/)
  (no key, no account).
- **Next event**: the next timed event from macOS Calendar that starts later
  today, or the one in progress. All-day events are ignored. Tomorrow's events
  appear after midnight. With nothing left today, the page is skipped.
- **Claude limits** (Pro/Max): 5-hour and weekly usage, and time until the
  5-hour window resets.
- **Status badge** (bottom right on every page): orange while Claude is
  working, grey when it is idle.
- **Alert**: when Claude needs you (permission prompt, waiting for input), the
  screen switches to Clauddy's `alerting` face instantly. Without Clauddy, a
  red full-screen alert frame is shown instead.

Screen text is English by default; Russian is available.

## Requirements

- macOS, a MiniToo paired in System Settings > Bluetooth
- Python 3 with Pillow (`python3 -m pip install --user Pillow`)
- Xcode Command Line Tools (`xcode-select --install`)
- Recommended: Clauddy installed first (`apps/clauddy/install.sh`) for the
  instant alert face

Close the Divoom phone app while the dashboard runs: the device accepts only
one Bluetooth client at a time.

## Install

```bash
cd apps/minitoo-dashboard
./install.sh
```

The installer asks for the on-screen language and the weather city, then:

1. writes `~/.minitoo-dashboard/config` (the device's Bluetooth MAC is taken
   from Clauddy's config or detected automatically);
2. builds a small calendar helper and asks macOS for Calendar access;
3. adds hooks to `~/.claude/settings.json` (after making a backup). If Clauddy
   hooks are present, it asks before replacing them;
4. installs a status line command that captures Claude limits. If you already
   have a status line, it prints one line to add to your script instead;
5. installs the `/dashboard-city` command for Claude Code;
6. starts the background daemon with launchd (`local.minitoo.dashboard`).

Start a new Claude Code session afterwards so the hooks load.

## Everyday use

```bash
minitoo-dashboard status           # what the dashboard is doing, data freshness
minitoo-dashboard city "Kazan"     # change the weather city
minitoo-dashboard preview          # render the current pages to PNG
minitoo-dashboard pause            # release the device (e.g. for the phone app)
minitoo-dashboard resume
minitoo-dashboard logs
```

In Claude Code: `/dashboard-city Kazan`. When a name matches several places,
the command lists them and asks which one you mean.

`~/.local/bin` must be on your `PATH` for the short command; otherwise run
`apps/minitoo-dashboard/bin/minitoo-dashboard`.

### Config

`~/.minitoo-dashboard/config` holds `KEY=value` lines. The daemon re-reads it
every second.

| Key | Default | Meaning |
| --- | --- | --- |
| `DEVICE_MAC` | from installer | MiniToo Bluetooth MAC |
| `CITY_NAME`, `CITY_LAT`, `CITY_LON` | from installer | weather location (use `minitoo-dashboard city`) |
| `TEMP_UNIT` | from macOS settings | `celsius` or `fahrenheit` |
| `LANG` | `en` | on-screen language: `en` or `ru` |
| `CALENDARS` | `all` | comma-separated calendar names to include |
| `PAGE_SECONDS` | `8` | seconds per page (2–60) |
| `SEND_DELAY_MS` | `20` | pause between Bluetooth chunks (0–200) |

## How it works

One background process owns the screen; everything else only writes files in
`~/.minitoo-dashboard/`:

```
Claude Code hooks ──► sessions/<session_id>   ┐
Status line script ─► cache/claude.json        │
Weather fetcher ────► cache/weather.json       ├─► daemon ─► core/dv ─► MiniToo
Calendar helper ────► cache/calendar.json      ┘  (launchd)
```

- Each Claude Code session reports its own state. If any session is waiting
  for you, the screen shows the alert. If any is working, the badge is orange.
  A session silent for 30 minutes is ignored.
- The pages are sent as one live animation (opcode `0x8B`, see
  [FINDINGS.md](../../FINDINGS.md) §8i); the device cycles them itself. A
  3-page upload takes under a second.

| Data | Refresh |
| --- | --- |
| Weather | every 15 min |
| Calendar | every minute |
| Claude limits | whenever Claude Code updates its status line |
| Screen | every minute and on any change; identical frames are not re-sent |

## Troubleshooting

- **The screen does not change.** Run `minitoo-dashboard status`. With the
  device off, out of range or held by the phone, the daemon retries after
  30 s, 1, 2 and 5 minutes, and recovers by itself. `minitoo-dashboard logs`
  shows the details.
- **No calendar page.** Nothing is left today, or Calendar access was not
  granted: System Settings > Privacy & Security > Calendars > `calendar-helper`.
  Reminders from the Reminders app are not events and are not shown.
- **Claude page says "no data yet" or shows an old "as of" time.** Limits come
  from Claude Code's status line, which runs in the terminal `claude` CLI. The
  Claude desktop app's Code tab does not run status line commands, so limits
  refresh only while you use a client that does.
- **You already had a status line.** Add
  `printf '%s' "$input" | "<path>/bin/statusline.py"` near the top of your
  script, after it reads stdin into `$input`.

## Uninstall

```bash
./uninstall.sh
```

It stops the daemon, removes the hooks and status line, restores Clauddy's
hooks if they were replaced, and asks before deleting `~/.minitoo-dashboard`.

## Credits

- Font: [Press Start 2P](https://fonts.google.com/specimen/Press+Start+2P) by
  CodeMan38, SIL Open Font License 1.1 (`fonts/OFL.txt`).
- Weather data: [Open-Meteo](https://open-meteo.com/).
