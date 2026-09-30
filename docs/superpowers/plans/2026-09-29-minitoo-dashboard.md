# MiniToo Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `apps/minitoo-dashboard/`: a launchd daemon that shows weather, today's next calendar event and Claude Pro limits on the Divoom MiniToo as a 3-page live animation, with a Claude status badge and Clauddy's alert face for `alerting`.

**Architecture:** Claude Code hooks, the status line script and two fetchers only write files under `~/.minitoo-dashboard/`. One Python daemon (`minitoo-dashboard run`, launchd) aggregates session states, renders pages with Pillow, encodes them as a `0x8B` live animation and sends them through the repo's existing `core/dv` Bluetooth daemon. Alerts switch to Clauddy's preloaded face by `ClockId`.

**Tech Stack:** Python 3.9+ (system `/usr/bin/python3`), Pillow, stdlib `unittest`, Swift + EventKit (calendar helper), bash, launchd, Open-Meteo HTTP APIs.

**Spec:** `docs/superpowers/specs/2026-09-29-minitoo-dashboard-design.md`

## Global Constraints

- macOS only; Python code must run on Python 3.9 (`from __future__ import annotations` in every module; no `match`, no runtime `X | Y` types).
- No new Python dependencies beyond Pillow; tests use stdlib `unittest` only.
- No changes to `core/` or `apps/clauddy/`; Clauddy's config is read-only.
- All state lives in `~/.minitoo-dashboard/` (override with env `MINITOO_DASHBOARD_HOME` — used by tests).
- Screen: 160×128; blob header `23 <frames> <speed_be16> 08 0a`; JPEG frames flagged `01` with big-endian u32 length; `0x8B` chunks are exactly 256 bytes, lengths little-endian (FINDINGS.md §8i).
- Pages in order: weather, calendar (only if an event qualifies), Claude (always). Frame delay = `PAGE_SECONDS` × 1000 ms, default 8 s.
- Badge: static 8×8 square at bottom-right, orange `(217,119,87)` for `working`, grey `(107,107,107)` for `chilling`.
- Calendar: only events that start later today or are in progress; all-day events ignored; none left → skip page.
- Staleness: weather hidden after 6 h, age marker after 1 h; Claude limits get "as of HH:MM" after 10 min; a window whose `resets_at` passed shows "reset".
- Session file older than 30 min is ignored; `SessionEnd` deletes it.
- Device backoff 30 s → 60 s → 120 s → 300 s; never block or fail a Claude hook.
- On-screen language `en` (default) or `ru`; README in English.
- Font: Press Start 2P (SIL OFL 1.1), vendored with its licence.
- Tests run from `apps/minitoo-dashboard/`: `python3 -m unittest discover -s tests -v`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Event titles with emoji or glyphs the font lacks** must render without boxes or crashes (characters are dropped) → test in Task 7.
2. **Very long event titles and city names** must be wrapped/ellipsized within 160 px, never overflow → test in Task 7.
3. **MiniToo off or held by the phone when the daemon starts** → daemon keeps running, backs off, recovers without restart → test in Task 10.
4. **Existing user hooks and status line in `~/.claude/settings.json`** are preserved; re-running the installer does not duplicate hooks → test in Task 12.
5. **Midnight and sleep/wake**: after wake or midnight the calendar switches to the new day's events immediately → tests in Task 6 (select across midnight) and Task 10 (wake jump forces refresh).

---

## File Structure

```
apps/minitoo-dashboard/
├── README.md                       # user docs (Task 13)
├── .gitignore
├── install.sh / uninstall.sh       # Task 12
├── bin/
│   ├── minitoo-dashboard           # CLI entry (Task 11)
│   ├── dashboard-hook.sh           # Claude Code hook (Task 3)
│   └── statusline.py               # status line command (Task 4)
├── calendar-helper/
│   ├── CalendarHelper.swift        # EventKit reader (Task 6)
│   └── build.sh
├── commands/dashboard-city.md      # Claude Code slash command template (Task 11)
├── launchd/local.minitoo.dashboard.plist.in   # (Task 12)
├── fonts/PressStart2P-Regular.ttf + OFL.txt   # (Task 7)
├── minitoo_dashboard/
│   ├── __init__.py
│   ├── paths.py        # locations (Task 2)
│   ├── store.py        # atomic JSON I/O (Task 2)
│   ├── config.py       # config + Clauddy config reader (Task 2)
│   ├── status.py       # session aggregation (Task 3)
│   ├── i18n.py         # strings + durations (Task 7)
│   ├── sources/__init__.py
│   ├── sources/claude.py    # rate-limit extract + view (Task 4)
│   ├── sources/weather.py   # Open-Meteo + view (Task 5)
│   ├── sources/calendar.py  # helper runner + selection (Task 6)
│   ├── render.py       # Pillow pages (Task 7)
│   ├── encode.py       # 0x8B blob + rawfile (Task 8)
│   ├── device.py       # dv wrapper (Task 9)
│   ├── collect.py      # Sources: refresh + model (Task 10)
│   ├── daemon.py       # Dashboard loop (Task 10)
│   ├── cli.py          # commands (Task 11)
│   └── settings.py     # settings.json merge + templates (Task 12)
└── tests/
    ├── __init__.py
    └── test_*.py
```

---

### Task 1: Device spike (throwaway) and decision gate

Answers spec §8 before any product code. Nothing from this task is committed except the findings in the spec.

**Files:**
- Create (scratch, not committed): `$SCRATCH/spike/spike_8b.py` where `$SCRATCH` is the session scratchpad directory
- Modify: `docs/superpowers/specs/2026-09-29-minitoo-dashboard-design.md` (append "Spike results" to §8)

**Interfaces:** Produces: decisions `SEND_DELAY_MS` default and go/no-go for the `0x8B` path.

The owner must watch the device during this task. Ask them to be at the desk before starting.

- [ ] **Step 1: Write the spike sender**

```python
#!/usr/bin/env python3
"""THROWAWAY spike: send a 3-page 0x8B live animation to the MiniToo.

usage: spike_8b.py <dv> <out.raw> <speed_ms> <delay_ms> [label]
"""
import io
import struct
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

dv, out, speed_ms, delay = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
label = sys.argv[5] if len(sys.argv) > 5 else time.strftime("%H:%M:%S")
frames = []
for i, color in enumerate([(200, 40, 40), (40, 160, 60), (40, 80, 200)]):
    im = Image.new("RGB", (160, 128), color)
    d = ImageDraw.Draw(im)
    d.text((10, 10), str(i + 1), fill=(255, 255, 255), font=ImageFont.load_default(size=48))
    d.text((10, 96), label, fill=(255, 255, 255), font=ImageFont.load_default(size=16))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90, subsampling=0)
    frames.append(buf.getvalue())
payload = bytes([0x23, len(frames), (speed_ms >> 8) & 0xFF, speed_ms & 0xFF, 8, 10])
payload += b"".join(b"\x01" + struct.pack(">I", len(j)) + j for j in frames)
lines = ["8b 00 " + struct.pack("<I", len(payload)).hex(" ")]
for idx in range((len(payload) + 255) // 256):
    chunk = payload[idx * 256:(idx + 1) * 256]
    lines.append((b"\x8b\x01" + struct.pack("<I", len(payload)) + struct.pack("<H", idx) + chunk).hex(" "))
out.write_text("\n".join(lines) + "\n")
print(f"payload={len(payload)}B chunks={len(lines) - 1}")
subprocess.run([str(dv), "rawfile", str(out), delay], check=True)
```

- [ ] **Step 2: Start the Bluetooth daemon**

Run (from repo root):
```bash
MAC="$(sed -n 's/^CLAUDDY_MINITOO_MAC=//p' ~/.clauddy/config)"; core/dv stop; core/dv start "$MAC"
```
Expected: `daemon started (pid …)`.

- [ ] **Step 3: Items 1 + 4 — persistence and 8 s frame delay**

Run: `python3 $SCRATCH/spike/spike_8b.py core/dv $SCRATCH/spike/a.raw 8000 20`
Ask the owner: do pages 1→2→3 change every ~8 s? Then wait 10 minutes (do not send anything): is the animation still showing? Record both answers.

- [ ] **Step 4: Item 2 + pacing — re-send every 60 s**

Run 5 times, 60 s apart, alternating delay 20 and 5:
`python3 $SCRATCH/spike/spike_8b.py core/dv $SCRATCH/spike/b.raw 8000 20 "send N"`
Ask the owner: any flicker, loading screen or blank between sends? After each send run `grep -c "8b 55 01" /tmp/divoom-send.log` — a growing count means the device re-requested chunks (lost data at that pacing). Pick the smallest delay with no re-requests and no glitches as `SEND_DELAY_MS`.

- [ ] **Step 5: Item 3 — alert face and back**

```bash
CLOCK="$(sed -n 's/^CLAUDDY_CLOCK_ALERTING=//p' ~/.clauddy/config)"; DEV="$(sed -n 's/^CLAUDDY_DEVICE_ID=//p' ~/.clauddy/config)"
core/dv json "{\"Command\":\"Channel/SetClockSelectId\",\"ClockId\":$CLOCK,\"DeviceId\":$DEV,\"ParentClockId\":0,\"ParentItemId\":\"\",\"PageIndex\":0,\"LcdIndependence\":0,\"LcdIndex\":0,\"Language\":\"en\"}"
```
Owner confirms the alerting face. Then re-run Step 3's command; owner confirms the dashboard pages return.

- [ ] **Step 6: Item 5 — does the desktop app run the status line?**

Changing `~/.claude/settings.json` needs the owner's approval. Ask them to approve adding
`"statusLine": {"type": "command", "command": "cat > /tmp/minitoo-sl-probe.json"}`, then open a new Claude desktop session, send one message, and check:
`python3 -c "import json;d=json.load(open('/tmp/minitoo-sl-probe.json'));print(d.get('rate_limits'))"`.
Record whether the file exists and contains `rate_limits`. Remove the probe `statusLine` afterwards (restore the file from the backup you made before editing).

- [ ] **Step 7: Item 6 — joystick reports**

Run `: > /tmp/divoom-send.log` then ask the owner to move the joystick left/right and press it, 5 s apart. Then `grep "rx" /tmp/divoom-send.log | grep -v "04 f7 55" | grep -v Tomato`. Record any new frames (if none: joystick is not reported).

- [ ] **Step 8: Decision gate**

If item 1 fails (animation does not stay) or item 2 fails (visible glitch every send): STOP. Report to the owner with the observations and do not continue with Task 2.
If item 5 fails: continue, but tell the owner the Claude page will only update from terminal `claude` sessions.

- [ ] **Step 9: Record results in the spec and commit**

Append under §8 of the spec:

```markdown
### Spike results (YYYY-MM-DD)

| # | Question | Result |
| --- | --- | --- |
| 1 | 0x8B stays on screen | <observed> |
| 2 | Re-send every 60 s | <observed>; chosen SEND_DELAY_MS=<n> |
| 3 | Dashboard ↔ alert face | <observed> |
| 4 | 8000 ms frame delay | <observed> |
| 5 | Desktop app runs status line | <observed> |
| 6 | Joystick reported to host | <observed> |
```

```bash
git add docs/superpowers/specs/2026-09-29-minitoo-dashboard-design.md
git commit -m "Record MiniToo dashboard device spike results"
```

---

### Task 2: Scaffold, paths, store, config

**Files:**
- Create: `apps/minitoo-dashboard/.gitignore`, `minitoo_dashboard/__init__.py`, `minitoo_dashboard/paths.py`, `minitoo_dashboard/store.py`, `minitoo_dashboard/config.py`, `minitoo_dashboard/sources/__init__.py`, `tests/__init__.py`, `tests/test_config.py`
- Modify: spec §2/§5 — remove wind units (the weather page shows no wind): delete `WIND_UNIT` from the config example and change "(`°C`/`°F`, km/h or m/s vs mph)" to "(`°C`/`°F`)".

**Interfaces:**
- Produces: `paths.APP_DIR`, `paths.REPO_ROOT`, `paths.CORE_DIR`, `paths.home()`, `paths.sessions_dir()`, `paths.cache_dir()`, `paths.config_path()`, `paths.log_path()`, `paths.state_path()`, `paths.paused_path()`, `paths.clauddy_config_path()`; `store.write_json_atomic(path, obj)`, `store.read_json(path) -> Any | None`; `config.Config` (fields `device_mac, city_name, city_lat, city_lon, temp_unit, lang, calendars, page_seconds, send_delay_ms`; property `has_city`; method `calendar_list() -> list[str] | None`), `config.parse_config(text)`, `config.format_config(cfg)`, `config.load_config(path=None)`, `config.save_config(cfg, path=None)`, `config.read_kv(path) -> dict[str,str]`, `config.clauddy_alert(path=None) -> tuple[int,int] | None`, `config.detect_temp_unit() -> str`.

All paths below are relative to `apps/minitoo-dashboard/`.

- [ ] **Step 1: Create scaffold files**

`.gitignore`:
```
__pycache__/
calendar-helper/calendar-helper.app/
```
`minitoo_dashboard/__init__.py`:
```python
"""MiniToo dashboard: weather, calendar and Claude limits on a Divoom MiniToo."""
```
`minitoo_dashboard/sources/__init__.py` and `tests/__init__.py`: empty files.

`minitoo_dashboard/paths.py`:
```python
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parent.parent
CORE_DIR = REPO_ROOT / "core"


def home() -> Path:
    return Path(os.environ.get("MINITOO_DASHBOARD_HOME", str(Path.home() / ".minitoo-dashboard")))


def sessions_dir() -> Path:
    return home() / "sessions"


def cache_dir() -> Path:
    return home() / "cache"


def config_path() -> Path:
    return home() / "config"


def log_path() -> Path:
    return home() / "dashboard.log"


def state_path() -> Path:
    return home() / "state.json"


def paused_path() -> Path:
    return home() / "paused"


def clauddy_config_path() -> Path:
    return Path(os.environ.get("CLAUDDY_CONFIG", str(Path.home() / ".clauddy" / "config")))
```

`minitoo_dashboard/store.py`:
```python
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
```

- [ ] **Step 2: Write the failing config tests**

`tests/test_config.py`:
```python
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import config


class ParseConfigTest(unittest.TestCase):
    def test_full_config(self):
        cfg = config.parse_config(
            "DEVICE_MAC=aa:bb:cc:dd:ee:01\n"
            'CITY_NAME="Moscow, Russia"\n'
            "CITY_LAT=55.7558\nCITY_LON=37.6173\n"
            "TEMP_UNIT=fahrenheit\nLANG=ru\nCALENDARS=Work, Home\n"
            "PAGE_SECONDS=10\nSEND_DELAY_MS=5\n"
        )
        self.assertEqual(cfg.device_mac, "AA:BB:CC:DD:EE:01")
        self.assertEqual(cfg.city_name, "Moscow, Russia")
        self.assertAlmostEqual(cfg.city_lat, 55.7558)
        self.assertAlmostEqual(cfg.city_lon, 37.6173)
        self.assertEqual(cfg.temp_unit, "fahrenheit")
        self.assertEqual(cfg.lang, "ru")
        self.assertEqual(cfg.page_seconds, 10)
        self.assertEqual(cfg.send_delay_ms, 5)
        self.assertTrue(cfg.has_city)
        self.assertEqual(cfg.calendar_list(), ["Work", "Home"])

    def test_defaults_and_invalid_values(self):
        cfg = config.parse_config("# comment\nLANG=de\nTEMP_UNIT=kelvin\nPAGE_SECONDS=abc\nCITY_LAT=north\nJUNK\n")
        self.assertEqual(cfg.lang, "en")
        self.assertEqual(cfg.temp_unit, "celsius")
        self.assertEqual(cfg.page_seconds, 8)
        self.assertIsNone(cfg.city_lat)
        self.assertFalse(cfg.has_city)
        self.assertIsNone(cfg.calendar_list())

    def test_page_seconds_clamped(self):
        self.assertEqual(config.parse_config("PAGE_SECONDS=100").page_seconds, 60)
        self.assertEqual(config.parse_config("PAGE_SECONDS=1").page_seconds, 2)

    def test_inline_comment(self):
        self.assertEqual(config.parse_config("TEMP_UNIT=fahrenheit  # US").temp_unit, "fahrenheit")

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            cfg = config.Config(device_mac="AA:BB:CC:DD:EE:FF", city_name="Kazan, Tatarstan, Russia",
                                city_lat=55.79, city_lon=49.12, lang="ru")
            config.save_config(cfg, path)
            self.assertEqual(config.load_config(path), cfg)

    def test_load_missing_file_returns_defaults(self):
        self.assertEqual(config.load_config(Path("/nonexistent/config")), config.Config())


class ClauddyAlertTest(unittest.TestCase):
    def test_reads_clock_and_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            path.write_text("# c\nCLAUDDY_DEVICE_ID=123456789\nCLAUDDY_CLOCK_ALERTING=988\n")
            self.assertEqual(config.clauddy_alert(path), (988, 123456789))

    def test_missing_returns_none(self):
        self.assertIsNone(config.clauddy_alert(Path("/nonexistent/clauddy")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd apps/minitoo-dashboard && python3 -m unittest discover -s tests -v`
Expected: FAIL — `ImportError: cannot import name 'config'`.

- [ ] **Step 4: Implement `minitoo_dashboard/config.py`**

```python
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths


@dataclass
class Config:
    device_mac: str = ""
    city_name: str = ""
    city_lat: Optional[float] = None
    city_lon: Optional[float] = None
    temp_unit: str = "celsius"
    lang: str = "en"
    calendars: str = "all"
    page_seconds: int = 8
    send_delay_ms: int = 20

    @property
    def has_city(self) -> bool:
        return self.city_lat is not None and self.city_lon is not None

    def calendar_list(self) -> Optional[List[str]]:
        if self.calendars.strip().lower() == "all":
            return None
        return [c.strip() for c in self.calendars.split(",") if c.strip()]


def _clean(value: str) -> str:
    value = value.strip()
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _float(value: str) -> Optional[float]:
    try:
        return float(value)
    except ValueError:
        return None


def _clamp_int(value: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except ValueError:
        return default


def read_kv(path: Path) -> Dict[str, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    result: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = _clean(value)
    return result


def parse_config(text: str) -> Config:
    cfg = Config()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), _clean(value)
        if key == "DEVICE_MAC":
            cfg.device_mac = value.upper()
        elif key == "CITY_NAME":
            cfg.city_name = value
        elif key == "CITY_LAT":
            cfg.city_lat = _float(value)
        elif key == "CITY_LON":
            cfg.city_lon = _float(value)
        elif key == "TEMP_UNIT" and value in ("celsius", "fahrenheit"):
            cfg.temp_unit = value
        elif key == "LANG" and value in ("en", "ru"):
            cfg.lang = value
        elif key == "CALENDARS" and value:
            cfg.calendars = value
        elif key == "PAGE_SECONDS":
            cfg.page_seconds = _clamp_int(value, 8, 2, 60)
        elif key == "SEND_DELAY_MS":
            cfg.send_delay_ms = _clamp_int(value, 20, 0, 200)
    return cfg


def format_config(cfg: Config) -> str:
    lines = [
        "# MiniToo dashboard config. Edit freely; the daemon re-reads it every second.",
        f"DEVICE_MAC={cfg.device_mac}",
        f"CITY_NAME={cfg.city_name}",
    ]
    if cfg.has_city:
        lines += [f"CITY_LAT={cfg.city_lat}", f"CITY_LON={cfg.city_lon}"]
    lines += [
        f"TEMP_UNIT={cfg.temp_unit}",
        f"LANG={cfg.lang}",
        f"CALENDARS={cfg.calendars}",
        f"PAGE_SECONDS={cfg.page_seconds}",
        f"SEND_DELAY_MS={cfg.send_delay_ms}",
    ]
    return "\n".join(line.replace("\n", " ") for line in lines) + "\n"


def load_config(path: Optional[Path] = None) -> Config:
    try:
        return parse_config(Path(path or paths.config_path()).read_text(encoding="utf-8"))
    except OSError:
        return Config()


def save_config(cfg: Config, path: Optional[Path] = None) -> None:
    target = Path(path or paths.config_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(format_config(cfg), encoding="utf-8")
    tmp.replace(target)


def clauddy_alert(path: Optional[Path] = None) -> Optional[Tuple[int, int]]:
    kv = read_kv(Path(path or paths.clauddy_config_path()))
    try:
        return int(kv["CLAUDDY_CLOCK_ALERTING"]), int(kv["CLAUDDY_DEVICE_ID"])
    except (KeyError, ValueError):
        return None


def detect_temp_unit() -> str:
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleTemperatureUnit"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "celsius"
    return "fahrenheit" if out == "Fahrenheit" else "celsius"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/minitoo-dashboard && python3 -m unittest discover -s tests -v`
Expected: 8 tests, OK.

- [ ] **Step 6: Amend the spec (wind units) and commit**

Apply the spec edit listed under **Files**, then:
```bash
git add apps/minitoo-dashboard docs/superpowers/specs/2026-09-29-minitoo-dashboard-design.md
git commit -m "Add minitoo-dashboard scaffold and config"
```

---

### Task 3: Session status — hook script and aggregation

**Files:**
- Create: `bin/dashboard-hook.sh`, `minitoo_dashboard/status.py`, `tests/test_status.py`

**Interfaces:**
- Consumes: `paths.sessions_dir()` (via env `MINITOO_DASHBOARD_HOME` in the hook).
- Produces: `status.STATES`, `status.SESSION_TTL = 1800`, `status.aggregate(entries: list[tuple[str, float]], now: float, ttl=SESSION_TTL) -> str`, `status.read_sessions(directory: Path) -> list[tuple[str, float]]`. Hook CLI: `dashboard-hook.sh <working|alerting|chilling|end>` with hook JSON on stdin; writes `sessions/<session_id>` = `"<state> <epoch>\n"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_status.py`:
```python
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import status

APP_DIR = Path(__file__).resolve().parent.parent
HOOK = APP_DIR / "bin" / "dashboard-hook.sh"
NOW = 1_000_000.0


class AggregateTest(unittest.TestCase):
    def test_empty_is_chilling(self):
        self.assertEqual(status.aggregate([], NOW), "chilling")

    def test_alerting_wins(self):
        entries = [("working", NOW), ("alerting", NOW - 10), ("chilling", NOW)]
        self.assertEqual(status.aggregate(entries, NOW), "alerting")

    def test_working_beats_chilling(self):
        self.assertEqual(status.aggregate([("chilling", NOW), ("working", NOW)], NOW), "working")

    def test_expired_sessions_ignored(self):
        self.assertEqual(status.aggregate([("alerting", NOW - 1801), ("chilling", NOW)], NOW), "chilling")

    def test_unknown_state_ignored(self):
        self.assertEqual(status.aggregate([("bogus", NOW)], NOW), "chilling")


class ReadSessionsTest(unittest.TestCase):
    def test_reads_files_and_skips_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a").write_text("working 123\n")
            (d / "b").write_text("garbage")
            (d / ".tmp").write_text("alerting 5\n")
            (d / "c").write_text("alerting 456\n")
            self.assertEqual(sorted(status.read_sessions(d)), [("alerting", 456.0), ("working", 123.0)])

    def test_missing_dir(self):
        self.assertEqual(status.read_sessions(Path("/nonexistent/sessions")), [])


class HookScriptTest(unittest.TestCase):
    def run_hook(self, home, state, payload):
        env = dict(os.environ, MINITOO_DASHBOARD_HOME=home)
        return subprocess.run(["bash", str(HOOK), state], input=payload, text=True,
                              env=env, capture_output=True, timeout=5)

    def test_writes_session_state(self):
        with tempfile.TemporaryDirectory() as home:
            r = self.run_hook(home, "chilling", '{"session_id":"abc-123","hook_event_name":"Stop"}')
            self.assertEqual(r.returncode, 0)
            content = (Path(home) / "sessions" / "abc-123").read_text()
            self.assertTrue(content.startswith("chilling "))

    def test_end_removes_session(self):
        with tempfile.TemporaryDirectory() as home:
            self.run_hook(home, "working", '{"session_id":"s1"}')
            self.run_hook(home, "end", '{"session_id":"s1"}')
            self.assertFalse((Path(home) / "sessions" / "s1").exists())

    def test_missing_session_id_uses_unknown(self):
        with tempfile.TemporaryDirectory() as home:
            self.run_hook(home, "working", "{}")
            self.assertTrue((Path(home) / "sessions" / "unknown").exists())

    def test_invalid_state_writes_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            r = self.run_hook(home, "exploding", '{"session_id":"s1"}')
            self.assertEqual(r.returncode, 0)
            self.assertFalse((Path(home) / "sessions").exists())

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as home:
            self.run_hook(home, "working", '{"session_id":"../../evil"}')
            self.assertEqual(os.listdir(Path(home) / "sessions"), ["unknown"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/minitoo-dashboard && python3 -m unittest tests.test_status -v`
Expected: FAIL — `cannot import name 'status'`.

- [ ] **Step 3: Implement**

`minitoo_dashboard/status.py`:
```python
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

STATES = ("chilling", "working", "alerting")
SESSION_TTL = 1800


def aggregate(entries: List[Tuple[str, float]], now: float, ttl: float = SESSION_TTL) -> str:
    live = {state for state, ts in entries if state in STATES and now - ts <= ttl}
    if "alerting" in live:
        return "alerting"
    if "working" in live:
        return "working"
    return "chilling"


def read_sessions(directory: Path) -> List[Tuple[str, float]]:
    try:
        files = [f for f in Path(directory).iterdir() if f.is_file() and not f.name.startswith(".")]
    except OSError:
        return []
    entries: List[Tuple[str, float]] = []
    for f in files:
        try:
            state, ts = f.read_text(encoding="utf-8").split()
            entries.append((state, float(ts)))
        except (OSError, ValueError):
            continue
    return entries
```

`bin/dashboard-hook.sh` (then `chmod +x`):
```bash
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
```

- [ ] **Step 4: Run tests**

Run: `cd apps/minitoo-dashboard && chmod +x bin/dashboard-hook.sh && python3 -m unittest tests.test_status -v`
Expected: 12 tests OK.

- [ ] **Step 5: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add dashboard session hook and status aggregation"
```

---

### Task 4: Claude limits — status line script and view

**Files:**
- Create: `minitoo_dashboard/sources/claude.py`, `bin/statusline.py`, `tests/test_claude.py`

**Interfaces:**
- Consumes: `store.write_json_atomic`, `paths.cache_dir()`.
- Produces: `claude.extract(data: dict, now: float) -> dict | None` (cache record `{"captured_at", "five_hour"?: {"used_percentage","resets_at"}, "seven_day"?: {...}}`), `claude.Window(pct, reset_in, is_reset)`, `claude.ClaudeView(five, week, as_of, has_data)`, `claude.STALE_AFTER = 600`, `claude.claude_view(cache, now) -> ClaudeView`. Cache file: `cache/claude.json`.

- [ ] **Step 1: Write the failing tests**

`tests/test_claude.py`:
```python
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard.sources import claude

APP_DIR = Path(__file__).resolve().parent.parent
NOW = 2_000_000.0
FULL = {"rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": NOW + 7800},
                        "seven_day": {"used_percentage": 41.2, "resets_at": NOW + 259200}}}


class ExtractTest(unittest.TestCase):
    def test_full(self):
        rec = claude.extract(FULL, NOW)
        self.assertEqual(rec["captured_at"], NOW)
        self.assertEqual(rec["five_hour"], {"used_percentage": 23.5, "resets_at": NOW + 7800})
        self.assertIn("seven_day", rec)

    def test_missing_rate_limits(self):
        self.assertIsNone(claude.extract({"model": {}}, NOW))

    def test_partial(self):
        rec = claude.extract({"rate_limits": {"five_hour": FULL["rate_limits"]["five_hour"]}}, NOW)
        self.assertNotIn("seven_day", rec)

    def test_bad_types(self):
        self.assertIsNone(claude.extract({"rate_limits": {"five_hour": {"used_percentage": "x"}}}, NOW))


class ViewTest(unittest.TestCase):
    def test_no_cache(self):
        view = claude.claude_view(None, NOW)
        self.assertFalse(view.has_data)

    def test_fresh(self):
        view = claude.claude_view(claude.extract(FULL, NOW - 60), NOW)
        self.assertTrue(view.has_data)
        self.assertEqual(view.five, claude.Window(23.5, 7800, False))
        self.assertIsNone(view.as_of)

    def test_stale_sets_as_of(self):
        view = claude.claude_view(claude.extract(FULL, NOW - 1200), NOW)
        self.assertEqual(view.as_of, NOW - 1200)

    def test_reset_passed(self):
        data = {"rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": NOW - 10},
                                "seven_day": FULL["rate_limits"]["seven_day"]}}
        view = claude.claude_view(claude.extract(data, NOW - 9000), NOW)
        self.assertEqual(view.five, claude.Window(None, None, True))
        self.assertFalse(view.week.is_reset)

    def test_missing_window(self):
        rec = claude.extract({"rate_limits": {"five_hour": FULL["rate_limits"]["five_hour"]}}, NOW)
        self.assertEqual(claude.claude_view(rec, NOW).week, claude.Window(None, None, False))


class StatuslineScriptTest(unittest.TestCase):
    def run_script(self, home, payload):
        env = dict(os.environ, MINITOO_DASHBOARD_HOME=home)
        return subprocess.run(["python3", str(APP_DIR / "bin" / "statusline.py")], input=payload,
                              text=True, env=env, capture_output=True, timeout=10)

    def test_writes_cache_and_prints_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            r = self.run_script(home, json.dumps(FULL))
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            data = json.loads((Path(home) / "cache" / "claude.json").read_text())
            self.assertIn("five_hour", data)

    def test_invalid_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            r = self.run_script(home, "not json")
            self.assertEqual(r.returncode, 0)
            self.assertFalse((Path(home) / "cache" / "claude.json").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/minitoo-dashboard && python3 -m unittest tests.test_claude -v` — Expected: import FAIL.

- [ ] **Step 3: Implement**

`minitoo_dashboard/sources/claude.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

STALE_AFTER = 600
WINDOWS = ("five_hour", "seven_day")


@dataclass
class Window:
    pct: Optional[float]
    reset_in: Optional[float]
    is_reset: bool


@dataclass
class ClaudeView:
    five: Window
    week: Window
    as_of: Optional[float]
    has_data: bool


def extract(data: Any, now: float) -> Optional[dict]:
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        return None
    record: dict = {"captured_at": now}
    for key in WINDOWS:
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        pct, resets = window.get("used_percentage"), window.get("resets_at")
        if isinstance(pct, (int, float)) and isinstance(resets, (int, float)):
            record[key] = {"used_percentage": float(pct), "resets_at": float(resets)}
    return record if len(record) > 1 else None


def _window(raw: Any, now: float) -> Window:
    if not isinstance(raw, dict):
        return Window(None, None, False)
    if raw["resets_at"] <= now:
        return Window(None, None, True)
    return Window(raw["used_percentage"], raw["resets_at"] - now, False)


def claude_view(cache: Any, now: float) -> ClaudeView:
    empty = Window(None, None, False)
    if not isinstance(cache, dict) or "captured_at" not in cache:
        return ClaudeView(empty, empty, None, False)
    captured = float(cache["captured_at"])
    as_of = captured if now - captured > STALE_AFTER else None
    return ClaudeView(_window(cache.get("five_hour"), now), _window(cache.get("seven_day"), now), as_of, True)
```

`bin/statusline.py` (then `chmod +x`):
```python
#!/usr/bin/env python3
"""Claude Code status line command: caches subscription rate limits for the
MiniToo dashboard. Prints nothing, so the status line row stays empty."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minitoo_dashboard import paths, store  # noqa: E402
from minitoo_dashboard.sources import claude  # noqa: E402


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    record = claude.extract(data, time.time())
    if record:
        try:
            store.write_json_atomic(paths.cache_dir() / "claude.json", record)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests** — `chmod +x bin/statusline.py && python3 -m unittest tests.test_claude -v` → 11 tests OK.

- [ ] **Step 5: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add Claude rate-limit status line capture and view"
```

---

### Task 5: Weather source (Open-Meteo)

**Files:**
- Create: `minitoo_dashboard/sources/weather.py`, `tests/test_weather.py`

**Interfaces:**
- Produces: `weather.http_get_json(url, params, timeout=10) -> dict`, `weather.Place(name, country, admin1, lat, lon)` with `.label()`, `weather.geocode(name, lang, get=http_get_json) -> list[Place]`, `weather.fetch_weather(lat, lon, temp_unit, now, get=http_get_json) -> dict` (cache record keys `fetched_at, lat, lon, temp, code, tmax, tmin, precip_from, precip_kind`), `weather.condition_key(code) -> str` (one of `clear, partly, cloudy, fog, rain, snow, storm`), `weather.WeatherView(temp, tmax, tmin, condition, precip_from, precip_kind, age_s)`, `weather.weather_view(cache, now) -> WeatherView | None`. `precip_from` is `"HH:MM"`, `"now"` or `None`; `precip_kind` is `"rain"`, `"snow"` or `None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_weather.py`:
```python
import unittest

from minitoo_dashboard.sources import weather

FORECAST = {
    "current": {"time": "2026-09-29T17:45", "temperature_2m": 11.6, "weather_code": 3},
    "hourly": {"time": ["2026-09-29T16:00", "2026-09-29T17:00", "2026-09-29T18:00", "2026-09-29T19:00"],
               "precipitation_probability": [90, 10, 60, 80], "weather_code": [61, 3, 71, 61]},
    "daily": {"temperature_2m_max": [15.2], "temperature_2m_min": [7.6]},
}


class FakeGet:
    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, url, params, timeout=10):
        self.calls.append((url, params))
        return self.response


class FetchWeatherTest(unittest.TestCase):
    def test_parses_forecast(self):
        get = FakeGet(FORECAST)
        rec = weather.fetch_weather(55.75, 37.61, "celsius", 1000.0, get=get)
        self.assertEqual(rec, {"fetched_at": 1000.0, "lat": 55.75, "lon": 37.61, "temp": 12, "code": 3,
                               "tmax": 15, "tmin": 8, "precip_from": "18:00", "precip_kind": "snow"})
        url, params = get.calls[0]
        self.assertEqual(url, weather.FORECAST_URL)
        self.assertEqual(params["temperature_unit"], "celsius")
        self.assertEqual(params["timezone"], "auto")

    def test_precip_now(self):
        data = {**FORECAST, "hourly": {"time": ["2026-09-29T17:00"], "precipitation_probability": [70],
                                       "weather_code": [61]}}
        rec = weather.fetch_weather(0, 0, "celsius", 0, get=FakeGet(data))
        self.assertEqual((rec["precip_from"], rec["precip_kind"]), ("now", "rain"))

    def test_no_precip(self):
        data = {**FORECAST, "hourly": {"time": ["2026-09-29T18:00"], "precipitation_probability": [None],
                                       "weather_code": [3]}}
        rec = weather.fetch_weather(0, 0, "celsius", 0, get=FakeGet(data))
        self.assertIsNone(rec["precip_from"])


class ConditionTest(unittest.TestCase):
    def test_codes(self):
        cases = {0: "clear", 2: "partly", 3: "cloudy", 45: "fog", 63: "rain", 81: "rain",
                 75: "snow", 86: "snow", 95: "storm", 1234: "cloudy"}
        for code, key in cases.items():
            self.assertEqual(weather.condition_key(code), key, code)


class ViewTest(unittest.TestCase):
    def rec(self, fetched_at):
        return weather.fetch_weather(1, 2, "celsius", fetched_at, get=FakeGet(FORECAST))

    def test_none(self):
        self.assertIsNone(weather.weather_view(None, 0))

    def test_fresh(self):
        view = weather.weather_view(self.rec(1000), 1000 + 600)
        self.assertEqual(view, weather.WeatherView(12, 15, 8, "cloudy", "18:00", "snow", None))

    def test_aged(self):
        self.assertEqual(weather.weather_view(self.rec(0), 7200).age_s, 7200)

    def test_too_old(self):
        self.assertIsNone(weather.weather_view(self.rec(0), 7 * 3600))


class GeocodeTest(unittest.TestCase):
    def test_labels(self):
        get = FakeGet({"results": [
            {"name": "Paris", "admin1": "Île-de-France", "country": "France", "latitude": 48.85, "longitude": 2.35},
            {"name": "Paris", "admin1": "Texas", "country": "United States", "latitude": 33.66, "longitude": -95.55},
            {"name": "Moscow", "admin1": "Moscow", "country": "Russia", "latitude": 55.75, "longitude": 37.61},
            {"name": "Broken"},
        ]})
        places = weather.geocode("Paris", "en", get=get)
        self.assertEqual([p.label() for p in places],
                         ["Paris, Île-de-France, France", "Paris, Texas, United States", "Moscow, Russia"])
        self.assertEqual(get.calls[0][1]["language"], "en")

    def test_no_results(self):
        self.assertEqual(weather.geocode("Nowhere", "en", get=FakeGet({})), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_weather -v` → import FAIL.

- [ ] **Step 3: Implement `minitoo_dashboard/sources/weather.py`**

```python
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
PRECIP_THRESHOLD = 50
MAX_AGE = 6 * 3600
AGE_MARKER_AFTER = 3600

Get = Callable[..., Any]


def http_get_json(url: str, params: dict, timeout: float = 10) -> Any:
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": "minitoo-dashboard"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def condition_key(code: int) -> str:
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if 95 <= code <= 99:
        return "storm"
    return "cloudy"


@dataclass
class Place:
    name: str
    country: str
    admin1: str
    lat: float
    lon: float

    def label(self) -> str:
        parts = [self.name]
        if self.admin1 and self.admin1 != self.name:
            parts.append(self.admin1)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


def geocode(name: str, lang: str, get: Get = http_get_json) -> List[Place]:
    data = get(GEOCODE_URL, {"name": name, "count": 5, "language": lang, "format": "json"})
    places = []
    for r in (data or {}).get("results") or []:
        if "latitude" in r and "longitude" in r:
            places.append(Place(r.get("name", ""), r.get("country", ""), r.get("admin1", ""),
                                float(r["latitude"]), float(r["longitude"])))
    return places


def fetch_weather(lat: float, lon: float, temp_unit: str, now: float, get: Get = http_get_json) -> dict:
    data = get(FORECAST_URL, {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code",
        "hourly": "precipitation_probability,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": temp_unit, "timezone": "auto", "forecast_days": 1,
    })
    current, hourly, daily = data["current"], data["hourly"], data["daily"]
    current_hour = current["time"][:13]
    precip_from: Optional[str] = None
    precip_kind: Optional[str] = None
    for t, prob, code in zip(hourly["time"], hourly["precipitation_probability"], hourly["weather_code"]):
        if t[:13] < current_hour or prob is None or prob < PRECIP_THRESHOLD:
            continue
        precip_from = "now" if t[:13] == current_hour else t[11:16]
        precip_kind = "snow" if condition_key(int(code)) == "snow" else "rain"
        break
    return {
        "fetched_at": now, "lat": lat, "lon": lon,
        "temp": round(current["temperature_2m"]), "code": int(current["weather_code"]),
        "tmax": round(daily["temperature_2m_max"][0]), "tmin": round(daily["temperature_2m_min"][0]),
        "precip_from": precip_from, "precip_kind": precip_kind,
    }


@dataclass
class WeatherView:
    temp: int
    tmax: int
    tmin: int
    condition: str
    precip_from: Optional[str]
    precip_kind: Optional[str]
    age_s: Optional[float]


def weather_view(cache: Any, now: float) -> Optional[WeatherView]:
    if not isinstance(cache, dict) or "fetched_at" not in cache:
        return None
    age = now - float(cache["fetched_at"])
    if age > MAX_AGE:
        return None
    return WeatherView(cache["temp"], cache["tmax"], cache["tmin"], condition_key(cache["code"]),
                       cache.get("precip_from"), cache.get("precip_kind"),
                       age if age > AGE_MARKER_AFTER else None)
```

- [ ] **Step 4: Run tests** — `python3 -m unittest tests.test_weather -v` → 10 tests OK.

- [ ] **Step 5: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add Open-Meteo weather source"
```

---

### Task 6: Calendar — Swift helper and event selection

**Files:**
- Create: `calendar-helper/CalendarHelper.swift`, `calendar-helper/build.sh`, `minitoo_dashboard/sources/calendar.py`, `tests/test_calendar.py`

**Interfaces:**
- Consumes: `store.read_json`, `paths.APP_DIR`.
- Produces: helper CLI `calendar-helper --out <path> [--request-access]` writing `{"status": "ok"|"denied"|"not_determined", "events": [{"title","start","end","calendar"}]}`; `calendar.HELPER_APP`, `calendar.Event(title, start, end, calendar)`, `calendar.parse_events(raw) -> list[Event]`, `calendar.select_event(events, now, calendars=None) -> Event | None`, `calendar.fetch_events(app_path, out_dir, run=subprocess.run, timeout=20) -> tuple[str, list[dict]]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_calendar.py`:
```python
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from minitoo_dashboard.sources import calendar

DAY = datetime(2026, 9, 29)


def ts(hour, minute=0, days=0):
    return (DAY + timedelta(days=days, hours=hour, minutes=minute)).timestamp()


PAST = calendar.Event("Standup", ts(9), ts(9, 15), "Work")
ONGOING = calendar.Event("Deep work", ts(19, 30), ts(21), "Work")
LATER = calendar.Event("Call", ts(22), ts(22, 30), "Home")
TOMORROW = calendar.Event("Breakfast", ts(9, days=1), ts(10, days=1), "Home")


class SelectEventTest(unittest.TestCase):
    def test_in_progress_first(self):
        self.assertEqual(calendar.select_event([LATER, PAST, ONGOING, TOMORROW], ts(20)), ONGOING)

    def test_next_upcoming(self):
        self.assertEqual(calendar.select_event([PAST, LATER, TOMORROW], ts(20)), LATER)

    def test_tomorrow_hidden_until_midnight(self):
        self.assertIsNone(calendar.select_event([PAST, TOMORROW], ts(23)))
        self.assertEqual(calendar.select_event([PAST, TOMORROW], ts(0, 5, days=1)), TOMORROW)

    def test_overnight_event_in_progress_after_midnight(self):
        overnight = calendar.Event("Deploy", ts(23), ts(1, days=1), "Work")
        self.assertEqual(calendar.select_event([overnight], ts(0, 30, days=1)), overnight)

    def test_calendar_filter(self):
        self.assertEqual(calendar.select_event([ONGOING, LATER], ts(20), ["Home"]), LATER)

    def test_parse_events_skips_malformed(self):
        raw = [{"title": "A", "start": 1, "end": 2, "calendar": "W"}, {"title": "B"}, "junk"]
        self.assertEqual(calendar.parse_events(raw), [calendar.Event("A", 1.0, 2.0, "W")])


class FetchEventsTest(unittest.TestCase):
    def fake_run(self, payload):
        def run(cmd, **kwargs):
            out = Path(cmd[cmd.index("--out") + 1])
            if payload is not None:
                out.write_text(json.dumps(payload))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return run

    def test_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"status": "ok", "events": [{"title": "A", "start": 1, "end": 2, "calendar": "W"}]}
            status, events = calendar.fetch_events(Path("/x.app"), Path(tmp), run=self.fake_run(payload))
            self.assertEqual((status, len(events)), ("ok", 1))

    def test_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, events = calendar.fetch_events(Path("/x.app"), Path(tmp), run=self.fake_run({"status": "denied"}))
            self.assertEqual((status, events), ("denied", []))

    def test_no_output_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(calendar.fetch_events(Path("/x.app"), Path(tmp), run=self.fake_run(None)), ("error", []))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_calendar -v` → import FAIL.

- [ ] **Step 3: Implement `minitoo_dashboard/sources/calendar.py`**

```python
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from .. import paths, store

HELPER_APP = paths.APP_DIR / "calendar-helper" / "calendar-helper.app"


@dataclass
class Event:
    title: str
    start: float
    end: float
    calendar: str


def parse_events(raw: Any) -> List[Event]:
    events = []
    for item in raw if isinstance(raw, list) else []:
        try:
            events.append(Event(str(item.get("title", "")), float(item["start"]), float(item["end"]),
                                str(item.get("calendar", ""))))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return events


def select_event(events: List[Event], now: float, calendars: Optional[List[str]] = None) -> Optional[Event]:
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = (midnight + timedelta(days=1)).timestamp()
    candidates = [e for e in events
                  if e.end > now and e.start < day_end and (calendars is None or e.calendar in calendars)]
    return min(candidates, key=lambda e: e.start) if candidates else None


def fetch_events(app_path: Path, out_dir: Path, run: Callable[..., Any] = subprocess.run,
                 timeout: float = 20) -> Tuple[str, list]:
    out = Path(out_dir) / "calendar-helper.json"
    try:
        out.unlink()
    except FileNotFoundError:
        pass
    run(["open", "-n", "-W", "-g", str(app_path), "--args", "--out", str(out)],
        capture_output=True, text=True, timeout=timeout)
    data = store.read_json(out)
    if not isinstance(data, dict):
        return "error", []
    return str(data.get("status", "error")), list(data.get("events") or [])
```

- [ ] **Step 4: Run tests** — `python3 -m unittest tests.test_calendar -v` → 9 tests OK.

- [ ] **Step 5: Write the Swift helper**

`calendar-helper/CalendarHelper.swift`:
```swift
import EventKit
import Foundation

// calendar-helper: writes today's timed (non-all-day) events as JSON for the
// MiniToo dashboard. Built as a .app bundle so macOS attributes Calendar access
// to this helper, not to Python or the terminal.
// Usage: calendar-helper --out <path> [--request-access]

func argValue(_ name: String) -> String? {
    let args = CommandLine.arguments
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}

func write(_ object: [String: Any], to path: String) {
    let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{\"status\":\"error\"}".utf8)
    try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
}

func hasAccess() -> Bool {
    let status = EKEventStore.authorizationStatus(for: .event)
    if #available(macOS 14.0, *) { return status == .fullAccess }
    return status == .authorized
}

func requestAccess(_ store: EKEventStore) -> Bool {
    let sema = DispatchSemaphore(value: 0)
    var granted = false
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { ok, _ in granted = ok; sema.signal() }
    } else {
        store.requestAccess(to: .event) { ok, _ in granted = ok; sema.signal() }
    }
    _ = sema.wait(timeout: .now() + 120)
    return granted
}

guard let outPath = argValue("--out") else {
    FileHandle.standardError.write(Data("usage: calendar-helper --out <path> [--request-access]\n".utf8))
    exit(64)
}

var allowed = hasAccess()
if !allowed && CommandLine.arguments.contains("--request-access") {
    allowed = requestAccess(EKEventStore())
}
guard allowed else {
    let status = EKEventStore.authorizationStatus(for: .event)
    write(["status": status == .notDetermined ? "not_determined" : "denied"], to: outPath)
    exit(0)
}

// A fresh store sees calendars granted during this run.
let store = EKEventStore()
let cal = Calendar.current
let start = cal.startOfDay(for: Date())
let end = cal.date(byAdding: .day, value: 1, to: start)!
let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
let events: [[String: Any]] = store.events(matching: predicate).filter { !$0.isAllDay }.map { ev in
    ["title": ev.title ?? "",
     "start": ev.startDate.timeIntervalSince1970,
     "end": ev.endDate.timeIntervalSince1970,
     "calendar": ev.calendar?.title ?? ""]
}
write(["status": "ok", "events": events], to: outPath)
```

`calendar-helper/build.sh` (then `chmod +x`):
```bash
#!/bin/bash
set -euo pipefail
# Builds calendar-helper as a minimal .app bundle so macOS can grant it Calendar access.
cd "$(dirname "$0")"
APP="calendar-helper.app"
BIN="calendar-helper"

swiftc -O -o "$BIN" CalendarHelper.swift
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mv "$BIN" "$APP/Contents/MacOS/$BIN"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>calendar-helper</string>
    <key>CFBundleIdentifier</key>
    <string>local.minitoo.calendar-helper</string>
    <key>CFBundleName</key>
    <string>calendar-helper</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSCalendarsFullAccessUsageDescription</key>
    <string>Show today's next event on the MiniToo dashboard.</string>
    <key>NSCalendarsUsageDescription</key>
    <string>Show today's next event on the MiniToo dashboard.</string>
</dict>
</plist>
PLIST

codesign --force --sign - --deep "$APP"
echo "Built: $PWD/$APP"
```

- [ ] **Step 6: Build and check the helper by hand (owner approves the prompt)**

Run: `apps/minitoo-dashboard/calendar-helper/build.sh && open -n -W apps/minitoo-dashboard/calendar-helper/calendar-helper.app --args --request-access --out /tmp/cal-test.json && cat /tmp/cal-test.json`
Expected: macOS asks for Calendar access for "calendar-helper"; after approval the file contains `{"status":"ok","events":[…]}` with today's timed events. If the prompt never appears, stop and report (TCC attribution problem) before continuing.

- [ ] **Step 7: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add calendar helper and today's-event selection"
```

---

### Task 7: Font, strings and page rendering

**Files:**
- Create: `fonts/PressStart2P-Regular.ttf`, `fonts/OFL.txt`, `minitoo_dashboard/i18n.py`, `minitoo_dashboard/render.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `WeatherView`, `Event`, `ClaudeView`/`Window`, `paths.APP_DIR`.
- Produces: `i18n.t(lang, key, **kw) -> str`, `i18n.duration(seconds, lang) -> str`; `render.W, render.H`, colour constants `ORANGE, GREY, RED`, `render.DashboardModel(now, status, lang, temp_unit, city_name, weather, event, claude)`, `render.render_pages(model) -> list[Image]`, `render.render_alert(lang) -> Image`, `render.demo_model(now, lang, status="working") -> DashboardModel`, `render.has_glyph(ch) -> bool`, `render.wrap(text, font, max_width, max_lines) -> list[str]`, `render.font(size)`.

- [ ] **Step 1: Vendor the font (ask the owner first)**

Downloading needs the owner's permission. Ask: "Download `PressStart2P-Regular.ttf` (~80 KB) and `OFL.txt` from github.com/google/fonts (`ofl/pressstart2p/`, SIL Open Font License)?" After a yes:
```bash
cd apps/minitoo-dashboard && mkdir -p fonts
curl -fsSL -o fonts/PressStart2P-Regular.ttf https://raw.githubusercontent.com/google/fonts/main/ofl/pressstart2p/PressStart2P-Regular.ttf
curl -fsSL -o fonts/OFL.txt https://raw.githubusercontent.com/google/fonts/main/ofl/pressstart2p/OFL.txt
```

- [ ] **Step 2: Write the failing tests**

`tests/test_render.py`:
```python
import unittest

from minitoo_dashboard import i18n, render
from minitoo_dashboard.sources.claude import ClaudeView, Window

NOW = 1_790_600_000.0


def model(**overrides):
    m = render.demo_model(NOW, "en")
    for key, value in overrides.items():
        setattr(m, key, value)
    return m


class GlyphTest(unittest.TestCase):
    def test_font_covers_needed_characters(self):
        for ch in "Жё°%+-:.":
            self.assertTrue(render.has_glyph(ch), ch)

    def test_emoji_not_covered(self):
        self.assertFalse(render.has_glyph("🎉"))


class PagesTest(unittest.TestCase):
    def test_demo_has_three_pages(self):
        pages = render.render_pages(model())
        self.assertEqual(len(pages), 3)
        for page in pages:
            self.assertEqual((page.size, page.mode), ((160, 128), "RGB"))

    def test_pages_skipped(self):
        self.assertEqual(len(render.render_pages(model(event=None))), 2)
        self.assertEqual(len(render.render_pages(model(event=None, weather=None))), 1)

    def test_claude_page_without_data(self):
        empty = Window(None, None, False)
        pages = render.render_pages(model(event=None, weather=None, claude=ClaudeView(empty, empty, None, False)))
        self.assertEqual(len(pages), 1)

    def test_badge_colour(self):
        self.assertEqual(render.render_pages(model())[0].getpixel((151, 119)), render.ORANGE)
        self.assertEqual(render.render_pages(model(status="chilling"))[0].getpixel((151, 119)), render.GREY)

    def test_emoji_and_long_title(self):
        m = model()
        m.event.title = "Встреча 🎉 с очень-очень длинным названием, которое не помещается в экран"
        pages = render.render_pages(m)
        self.assertEqual(len(pages), 3)

    def test_wrap_limits_lines(self):
        lines = render.wrap("one two three four five six seven eight nine ten eleven twelve", render.font(8), 148, 3)
        self.assertLessEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("..."))
        for line in lines:
            self.assertLessEqual(render.font(8).getlength(line), 148)

    def test_russian(self):
        self.assertEqual(len(render.render_pages(render.demo_model(NOW, "ru"))), 3)

    def test_deterministic(self):
        a = [p.tobytes() for p in render.render_pages(model())]
        b = [p.tobytes() for p in render.render_pages(model())]
        self.assertEqual(a, b)

    def test_alert_page(self):
        page = render.render_alert("en")
        self.assertEqual(page.size, (160, 128))
        self.assertEqual(page.getpixel((1, 1)), render.RED)


class I18nTest(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(i18n.duration(7800, "en"), "2h10m")
        self.assertEqual(i18n.duration(45 * 60, "ru"), "45м")
        self.assertEqual(i18n.duration(3 * 86400 + 3600, "en"), "3d1h")
        self.assertEqual(i18n.duration(10, "en"), "1m")

    def test_format(self):
        self.assertEqual(i18n.t("ru", "in_min", m=5), "через 5 мин")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify failure** — `python3 -m unittest tests.test_render -v` → import FAIL.

- [ ] **Step 4: Implement `minitoo_dashboard/i18n.py`**

```python
from __future__ import annotations

STRINGS = {
    "en": {
        "next": "NEXT", "now": "NOW", "until": "until {t}", "in_min": "in {m} min", "in_h": "in {h}h {m:02d}m",
        "five_hour": "5-hour", "week": "week", "reset": "reset", "as_of": "as of {t}", "no_data": "no data yet",
        "ago": "{d} ago", "rain_from": "Rain from {t}", "snow_from": "Snow from {t}",
        "rain_now": "Rain now", "snow_now": "Snow now", "alert": "Claude needs you",
        "cond_clear": "Clear", "cond_partly": "Partly cloudy", "cond_cloudy": "Cloudy", "cond_fog": "Fog",
        "cond_rain": "Rain", "cond_snow": "Snow", "cond_storm": "Storm",
    },
    "ru": {
        "next": "ДАЛЕЕ", "now": "СЕЙЧАС", "until": "до {t}", "in_min": "через {m} мин", "in_h": "через {h}ч {m:02d}м",
        "five_hour": "5 часов", "week": "неделя", "reset": "сброшен", "as_of": "на {t}", "no_data": "нет данных",
        "ago": "{d} назад", "rain_from": "Дождь с {t}", "snow_from": "Снег с {t}",
        "rain_now": "Идёт дождь", "snow_now": "Идёт снег", "alert": "Claude ждёт вас",
        "cond_clear": "Ясно", "cond_partly": "Переменная обл.", "cond_cloudy": "Облачно", "cond_fog": "Туман",
        "cond_rain": "Дождь", "cond_snow": "Снег", "cond_storm": "Гроза",
    },
}
UNITS = {"en": ("d", "h", "m"), "ru": ("д", "ч", "м")}


def t(lang: str, key: str, **kw) -> str:
    return STRINGS.get(lang, STRINGS["en"])[key].format(**kw)


def duration(seconds: float, lang: str) -> str:
    d, h, m = UNITS.get(lang, UNITS["en"])
    s = max(0, int(seconds))
    if s >= 86400:
        return f"{s // 86400}{d}{(s % 86400) // 3600}{h}"
    if s >= 3600:
        return f"{s // 3600}{h}{(s % 3600) // 60:02d}{m}"
    return f"{max(1, s // 60)}{m}"
```

- [ ] **Step 5: Implement `minitoo_dashboard/render.py`**

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from . import i18n, paths
from .sources.calendar import Event
from .sources.claude import ClaudeView, Window
from .sources.weather import WeatherView

W, H = 160, 128
BLACK = (0, 0, 0)
WHITE = (242, 242, 242)
DIM = (130, 130, 130)
BLUE = (111, 179, 255)
ORANGE = (217, 119, 87)
GREY = (107, 107, 107)
YELLOW = (255, 210, 74)
RED = (230, 57, 70)
TRACK = (51, 51, 51)
CLOUD = (200, 210, 222)
FONT_PATH = paths.APP_DIR / "fonts" / "PressStart2P-Regular.ttf"
MISSING = "\U000F0000"  # private-use plane: renders as the font's .notdef glyph

_fonts: Dict[int, ImageFont.FreeTypeFont] = {}
_glyphs: Dict[str, bool] = {}


@dataclass
class DashboardModel:
    now: float
    status: str
    lang: str
    temp_unit: str
    city_name: str
    weather: Optional[WeatherView]
    event: Optional[Event]
    claude: ClaudeView


def font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(str(FONT_PATH), size)
    return _fonts[size]


def _glyph_bitmap(ch: str) -> bytes:
    im = Image.new("1", (24, 24), 0)
    ImageDraw.Draw(im).text((0, 0), ch, font=font(8), fill=1)
    return im.tobytes()


def has_glyph(ch: str) -> bool:
    if ch not in _glyphs:
        _glyphs[ch] = ch == " " or _glyph_bitmap(ch) != _glyph_bitmap(MISSING)
    return _glyphs[ch]


def clean(text: str) -> str:
    return " ".join("".join(ch for ch in text if has_glyph(ch)).split())


def wrap(text: str, f: ImageFont.FreeTypeFont, max_width: int, max_lines: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if f.getlength(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        while f.getlength(current) > max_width:
            cut = len(current)
            while cut > 1 and f.getlength(current[:cut]) > max_width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and f.getlength(last.rstrip() + "...") > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    return lines


def _fit(text: str, f: ImageFont.FreeTypeFont, max_width: int) -> str:
    return (wrap(text, f, max_width, 1) or [""])[0]


def _canvas():
    img = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # no anti-aliasing: crisp pixels, stable JPEGs
    return img, draw


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _temp(value: int, unit: str) -> str:
    return f"{value:+d}°" if unit == "celsius" else f"{value}°"


def _cloud(d, x, y):
    d.ellipse([x + 2, y + 8, x + 16, y + 22], fill=CLOUD)
    d.ellipse([x + 10, y + 2, x + 26, y + 18], fill=CLOUD)
    d.ellipse([x + 18, y + 8, x + 32, y + 22], fill=CLOUD)
    d.rectangle([x + 9, y + 14, x + 25, y + 22], fill=CLOUD)


def _icon(d, condition: str, x: int, y: int) -> None:
    if condition == "clear":
        d.ellipse([x + 6, y, x + 26, y + 20], fill=YELLOW)
        return
    if condition == "fog":
        for i in range(4):
            d.rectangle([x + 2, y + 4 + i * 5, x + 30, y + 5 + i * 5], fill=CLOUD)
        return
    if condition == "partly":
        d.ellipse([x + 16, y - 2, x + 32, y + 14], fill=YELLOW)
    _cloud(d, x, y)
    if condition == "rain":
        for i in range(3):
            d.line([x + 9 + i * 8, y + 24, x + 6 + i * 8, y + 30], fill=BLUE, width=2)
    elif condition == "snow":
        for i in range(3):
            d.rectangle([x + 7 + i * 8, y + 25, x + 9 + i * 8, y + 27], fill=WHITE)
    elif condition == "storm":
        d.line([x + 17, y + 22, x + 13, y + 28, x + 19, y + 28, x + 15, y + 34], fill=YELLOW, width=2)


def _weather_page(m: DashboardModel) -> Image.Image:
    img, d = _canvas()
    w, lang = m.weather, m.lang
    _icon(d, w.condition, 6, 12)
    d.text((46, 10), _temp(w.temp, m.temp_unit), font=font(24), fill=WHITE)
    d.text((46, 40), _fit(clean(m.city_name.split(",")[0]), font(8), 110), font=font(8), fill=DIM)
    d.text((6, 58), _fit(i18n.t(lang, "cond_" + w.condition), font(8), 148), font=font(8), fill=WHITE)
    d.text((6, 72), f"{_temp(w.tmax, m.temp_unit)} / {_temp(w.tmin, m.temp_unit)}", font=font(8), fill=WHITE)
    if w.precip_from and w.precip_kind:
        key = f"{w.precip_kind}_now" if w.precip_from == "now" else f"{w.precip_kind}_from"
        d.text((6, 88), _fit(i18n.t(lang, key, t=w.precip_from), font(8), 148), font=font(8), fill=BLUE)
    if w.age_s:
        d.text((6, 104), i18n.t(lang, "ago", d=i18n.duration(w.age_s, lang)), font=font(8), fill=DIM)
    return img


def _calendar_page(m: DashboardModel) -> Image.Image:
    img, d = _canvas()
    e, lang = m.event, m.lang
    d.text((6, 6), i18n.t(lang, "next"), font=font(8), fill=DIM)
    if e.start <= m.now:
        d.text((6, 20), i18n.t(lang, "now"), font=font(24), fill=BLUE)
        small = i18n.t(lang, "until", t=_hhmm(e.end))
    else:
        d.text((6, 20), _hhmm(e.start), font=font(24), fill=BLUE)
        minutes = max(1, math.ceil((e.start - m.now) / 60))
        small = (i18n.t(lang, "in_min", m=minutes) if minutes < 60
                 else i18n.t(lang, "in_h", h=minutes // 60, m=minutes % 60))
    d.text((6, 50), _fit(small, font(8), 148), font=font(8), fill=BLUE)
    for i, line in enumerate(wrap(clean(e.title), font(8), 148, 3)):
        d.text((6, 68 + i * 12), line, font=font(8), fill=WHITE)
    return img


def _pct(window: Window) -> str:
    return f"{round(window.pct)}%" if window.pct is not None else "--"


def _claude_page(m: DashboardModel) -> Image.Image:
    img, d = _canvas()
    c, lang = m.claude, m.lang
    d.text((6, 6), "CLAUDE", font=font(8), fill=DIM)
    if not c.has_data:
        d.text((6, 56), i18n.t(lang, "no_data"), font=font(8), fill=WHITE)
        return img
    d.text((6, 20), _pct(c.five), font=font(24), fill=ORANGE)
    d.text((86, 22), i18n.t(lang, "five_hour"), font=font(8), fill=DIM)
    if c.five.is_reset:
        d.text((86, 34), _fit(i18n.t(lang, "reset"), font(8), 70), font=font(8), fill=DIM)
    elif c.five.reset_in is not None:
        d.text((86, 34), i18n.duration(c.five.reset_in, lang), font=font(8), fill=DIM)
    d.rectangle([6, 50, 153, 55], fill=TRACK)
    if c.five.pct is not None:
        d.rectangle([6, 50, 6 + round(147 * min(c.five.pct, 100) / 100), 55], fill=ORANGE)
    week_text = i18n.t(lang, "reset") if c.week.is_reset else _pct(c.week)
    d.text((6, 64), f"{i18n.t(lang, 'week')} {week_text}", font=font(8), fill=WHITE)
    d.rectangle([6, 76, 153, 79], fill=TRACK)
    if c.week.pct is not None:
        d.rectangle([6, 76, 6 + round(147 * min(c.week.pct, 100) / 100), 79], fill=ORANGE)
    if c.as_of is not None:
        d.text((6, 92), i18n.t(lang, "as_of", t=_hhmm(c.as_of)), font=font(8), fill=DIM)
    return img


def _decorate(img: Image.Image, index: int, total: int, status: str) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([148, 116, 155, 123], fill=ORANGE if status == "working" else GREY)
    if total > 1:
        x0 = (W - (total * 10 - 4)) // 2
        for i in range(total):
            d.rectangle([x0 + i * 10, 122, x0 + i * 10 + 5, 124], fill=WHITE if i == index else TRACK)


def render_pages(m: DashboardModel) -> List[Image.Image]:
    pages = []
    if m.weather is not None:
        pages.append(_weather_page(m))
    if m.event is not None:
        pages.append(_calendar_page(m))
    pages.append(_claude_page(m))
    for i, page in enumerate(pages):
        _decorate(page, i, len(pages), m.status)
    return pages


def render_alert(lang: str) -> Image.Image:
    img, d = _canvas()
    d.rectangle([0, 0, W - 1, H - 1], outline=RED, width=4)
    d.text(((W - 24) // 2, 24), "!", font=font(24), fill=RED)
    for i, line in enumerate(wrap(i18n.t(lang, "alert"), font(8), 140, 2)):
        x = (W - int(font(8).getlength(line))) // 2
        d.text((x, 76 + i * 12), line, font=font(8), fill=WHITE)
    return img


def demo_model(now: float, lang: str, status: str = "working") -> DashboardModel:
    return DashboardModel(
        now=now, status=status, lang=lang, temp_unit="celsius",
        city_name="Москва" if lang == "ru" else "Moscow",
        weather=WeatherView(12, 15, 8, "cloudy", "18:00", "rain", None),
        event=Event("Созвон с командой" if lang == "ru" else "Team sync", now + 25 * 60, now + 85 * 60, "Work"),
        claude=ClaudeView(Window(23.0, 7800, False), Window(41.0, 3 * 86400, False), None, True),
    )
```

- [ ] **Step 6: Run tests** — `python3 -m unittest tests.test_render -v` → 13 tests OK. If `test_font_covers_needed_characters` fails, stop and report which glyph is missing (fallback font decision for the owner: GNU Unifont, OFL).

- [ ] **Step 7: Look at the result**

Run: `python3 -c "import time; from minitoo_dashboard import render; [p.resize((480,384), 0).save(f'/tmp/page-{i}.png') for i,p in enumerate(render.render_pages(render.demo_model(time.time(),'ru')))]"` and open the PNGs; show them to the owner. Adjust coordinates only if text visibly overlaps.

- [ ] **Step 8: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add page rendering with pixel font and en/ru strings"
```

---

### Task 8: 0x8B encoding

**Files:**
- Create: `minitoo_dashboard/encode.py`, `tests/test_encode.py`

**Interfaces:**
- Produces: `encode.CHUNK = 256`, `encode.build_blob(pages: list[Image], speed_ms: int, quality: int = 90) -> bytes`, `encode.rawfile_lines(payload: bytes) -> list[str]`, `encode.write_rawfile(payload: bytes, path: Path) -> int` (returns chunk count).

- [ ] **Step 1: Write the failing tests**

`tests/test_encode.py`:
```python
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from minitoo_dashboard import encode


def pages(n):
    return [Image.new("RGB", (160, 128), (i * 40, 0, 0)) for i in range(n)]


class BlobTest(unittest.TestCase):
    def test_header(self):
        blob = encode.build_blob(pages(3), 8000)
        self.assertEqual(blob[:6], bytes([0x23, 3, 0x1F, 0x40, 8, 10]))

    def test_frames_are_flagged_jpegs(self):
        blob = encode.build_blob(pages(2), 1000)
        offset = 6
        for _ in range(2):
            self.assertEqual(blob[offset], 0x01)
            length = struct.unpack(">I", blob[offset + 1:offset + 5])[0]
            self.assertEqual(blob[offset + 5:offset + 7], b"\xff\xd8")
            offset += 5 + length
        self.assertEqual(offset, len(blob))

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            encode.build_blob([], 1000)
        with self.assertRaises(ValueError):
            encode.build_blob(pages(1), 70000)
        with self.assertRaises(ValueError):
            encode.build_blob([Image.new("RGB", (10, 10))], 1000)

    def test_deterministic(self):
        self.assertEqual(encode.build_blob(pages(2), 8000), encode.build_blob(pages(2), 8000))


class RawfileTest(unittest.TestCase):
    def test_lines(self):
        payload = bytes(range(256)) * 2 + b"\x01\x02"
        lines = encode.rawfile_lines(payload)
        self.assertEqual(lines[0], "8b 00 02 02 00 00")
        self.assertEqual(len(lines), 1 + 3)
        second = bytes.fromhex(lines[2])
        self.assertEqual(second[:8], b"\x8b\x01\x02\x02\x00\x00\x01\x00")
        self.assertEqual(len(second), 8 + 256)
        self.assertEqual(bytes.fromhex(lines[3])[8:], b"\x01\x02")

    def test_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "frame.raw"
            self.assertEqual(encode.write_rawfile(b"ALERT", path), 1)
            self.assertTrue(path.read_text().startswith("8b 00 05 00 00 00\n"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_encode -v` → import FAIL.

- [ ] **Step 3: Implement `minitoo_dashboard/encode.py`**

```python
from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import List

from PIL import Image

WIDTH, HEIGHT = 160, 128
CELL_ROWS, CELL_COLS = 8, 10
CHUNK = 256
OPCODE = 0x8B


def build_blob(pages: List[Image.Image], speed_ms: int, quality: int = 90) -> bytes:
    if not 1 <= len(pages) <= 255:
        raise ValueError(f"need 1..255 frames, got {len(pages)}")
    if not 1 <= speed_ms <= 0xFFFF:
        raise ValueError(f"speed must fit uint16 ms, got {speed_ms}")
    blob = bytes([0x23, len(pages), (speed_ms >> 8) & 0xFF, speed_ms & 0xFF, CELL_ROWS, CELL_COLS])
    for page in pages:
        if page.size != (WIDTH, HEIGHT):
            raise ValueError(f"page must be {WIDTH}x{HEIGHT}, got {page.size}")
        buf = io.BytesIO()
        page.convert("RGB").save(buf, format="JPEG", quality=quality, subsampling=0)
        jpeg = buf.getvalue()
        blob += b"\x01" + struct.pack(">I", len(jpeg)) + jpeg
    return blob


def rawfile_lines(payload: bytes) -> List[str]:
    total = struct.pack("<I", len(payload))
    lines = [(bytes([OPCODE, 0x00]) + total).hex(" ")]
    for index in range((len(payload) + CHUNK - 1) // CHUNK):
        chunk = payload[index * CHUNK:(index + 1) * CHUNK]
        lines.append((bytes([OPCODE, 0x01]) + total + struct.pack("<H", index) + chunk).hex(" "))
    return lines


def write_rawfile(payload: bytes, path: Path) -> int:
    lines = rawfile_lines(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="ascii")
    tmp.replace(path)
    return len(lines) - 1
```

- [ ] **Step 4: Run tests** — `python3 -m unittest tests.test_encode -v` → 6 tests OK.

- [ ] **Step 5: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add 0x8B live-animation encoder"
```

---

### Task 9: Device wrapper around `core/dv`

**Files:**
- Create: `minitoo_dashboard/device.py`, `tests/test_device.py`

**Interfaces:**
- Consumes: `paths.CORE_DIR`.
- Produces: `device.DeviceError`, `device.selector_json(clock_id, device_id) -> str`, `device.Device(mac, dv=None, fifo=DEFAULT_FIFO, run=subprocess.run)` with `daemon_running() -> bool`, `ensure_daemon()`, `send_rawfile(path, delay_ms)`, `select_clock(clock_id, device_id)`, `stop()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_device.py`:
```python
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import device


class FakeRun:
    def __init__(self, pgrep_rc=0, fail=None, timeout=None):
        self.calls, self.pgrep_rc, self.fail, self.timeout = [], pgrep_rc, fail, timeout

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(cmd, self.pgrep_rc, "", "")
        if self.timeout == cmd[1]:
            raise subprocess.TimeoutExpired(cmd, 1)
        rc = 1 if self.fail == cmd[1] else 0
        return subprocess.CompletedProcess(cmd, rc, "", "boom" if rc else "")


class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fifo = Path(self.tmp.name) / "divoom.fifo"

    def tearDown(self):
        self.tmp.cleanup()

    def dev(self, run):
        return device.Device("AA:BB:CC:DD:EE:FF", dv="/dv", fifo=self.fifo, run=run)

    def test_send_when_daemon_running(self):
        os.mkfifo(self.fifo)
        run = FakeRun()
        self.dev(run).send_rawfile(Path("/tmp/frame.raw"), 20)
        self.assertEqual([c for c in run.calls if c[0] == "/dv"], [["/dv", "rawfile", "/tmp/frame.raw", "20"]])

    def test_starts_daemon_when_missing(self):
        run = FakeRun()
        self.dev(run).send_rawfile(Path("/tmp/frame.raw"), 5)
        dv_calls = [c for c in run.calls if c[0] == "/dv"]
        self.assertEqual(dv_calls[0], ["/dv", "start", "AA:BB:CC:DD:EE:FF"])
        self.assertEqual(dv_calls[1][1], "rawfile")

    def test_stale_fifo_removed_before_start(self):
        os.mkfifo(self.fifo)
        run = FakeRun(pgrep_rc=1)
        self.dev(run).send_rawfile(Path("/tmp/frame.raw"), 5)
        self.assertFalse(self.fifo.exists())
        self.assertIn(["/dv", "start", "AA:BB:CC:DD:EE:FF"], run.calls)

    def test_start_failure_raises(self):
        with self.assertRaises(device.DeviceError):
            self.dev(FakeRun(fail="start")).send_rawfile(Path("/tmp/frame.raw"), 5)

    def test_timeout_raises(self):
        os.mkfifo(self.fifo)
        with self.assertRaises(device.DeviceError):
            self.dev(FakeRun(timeout="rawfile")).send_rawfile(Path("/tmp/frame.raw"), 5)

    def test_path_with_space_rejected(self):
        with self.assertRaises(ValueError):
            self.dev(FakeRun()).send_rawfile(Path("/tmp/my frame.raw"), 5)

    def test_select_clock(self):
        os.mkfifo(self.fifo)
        run = FakeRun()
        self.dev(run).select_clock(988, 123456789)
        cmd = [c for c in run.calls if c[0] == "/dv"][0]
        self.assertEqual(cmd[1], "json")
        body = json.loads(cmd[2])
        self.assertEqual((body["Command"], body["ClockId"], body["DeviceId"]),
                         ("Channel/SetClockSelectId", 988, 123456789))
        self.assertNotIn(" ", cmd[2])

    def test_stop_swallows_errors(self):
        self.dev(FakeRun(fail="stop")).stop()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_device -v` → import FAIL.

- [ ] **Step 3: Implement `minitoo_dashboard/device.py`**

```python
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, List, Optional

from . import paths

DEFAULT_FIFO = Path(os.environ.get("DIVOOM_FIFO", "/tmp/divoom.fifo"))


class DeviceError(Exception):
    pass


def selector_json(clock_id: int, device_id: int) -> str:
    # Same selector Clauddy sends (apps/clauddy/lib/clauddy-lib.sh). No spaces:
    # dv passes the FIFO line through a space tokenizer.
    return ('{"Command":"Channel/SetClockSelectId","ClockId":%d,"DeviceId":%d,"ParentClockId":0,'
            '"ParentItemId":"","PageIndex":0,"LcdIndependence":0,"LcdIndex":0,"Language":"en"}'
            % (clock_id, device_id))


class Device:
    def __init__(self, mac: str, dv: Optional[str] = None, fifo: Path = DEFAULT_FIFO,
                 run: Callable[..., Any] = subprocess.run):
        self.mac = mac
        self.dv = str(dv or paths.CORE_DIR / "dv")
        self.fifo = Path(fifo)
        self.run = run

    def _call(self, args: List[str], timeout: float) -> None:
        try:
            result = self.run([self.dv, *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise DeviceError(f"dv {args[0]} timed out")
        except OSError as exc:
            raise DeviceError(f"dv {args[0]}: {exc}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-300:]
            raise DeviceError(f"dv {args[0]} failed: {detail}")

    def daemon_running(self) -> bool:
        try:
            if not stat.S_ISFIFO(self.fifo.stat().st_mode):
                return False
        except OSError:
            return False
        result = self.run(["pgrep", "-f", "divoom-send"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0

    def ensure_daemon(self) -> None:
        if self.daemon_running():
            return
        try:
            self.fifo.unlink()  # a stale FIFO makes `dv start` refuse to run
        except FileNotFoundError:
            pass
        self._call(["start", self.mac], timeout=20)

    def send_rawfile(self, path: Path, delay_ms: int) -> None:
        if " " in str(path):
            raise ValueError("rawfile path must not contain spaces (dv splits FIFO lines on spaces)")
        self.ensure_daemon()
        self._call(["rawfile", str(path), str(delay_ms)], timeout=15)

    def select_clock(self, clock_id: int, device_id: int) -> None:
        self.ensure_daemon()
        self._call(["json", selector_json(clock_id, device_id)], timeout=15)

    def stop(self) -> None:
        try:
            self._call(["stop"], timeout=5)
        except DeviceError:
            pass
```

- [ ] **Step 4: Run tests** — `python3 -m unittest tests.test_device -v` → 8 tests OK.

- [ ] **Step 5: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add dv device wrapper"
```

---

### Task 10: Sources collector and the daemon loop

**Files:**
- Create: `minitoo_dashboard/collect.py`, `minitoo_dashboard/daemon.py`, `tests/test_collect.py`, `tests/test_daemon.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `collect.Sources(cache_dir, sessions_dir, fetch_weather=..., fetch_events=..., helper_app=...)` with `status(now)`, `refresh_weather(cfg, now)`, `refresh_calendar(cfg, now)`, `model(cfg, status, now) -> DashboardModel`; `daemon.Backoff`, `daemon.Dashboard(sources, device_for, load_config, clauddy_alert, dashboard_blob, alert_blob, home)` with `tick(now)`, `daemon.setup_logging()`, `daemon.run_forever()`. State file `state.json`: `{"updated_at","status","shown","paused","last_sent_at","last_error","retry_at"}`.

- [ ] **Step 1: Write the failing collector test**

`tests/test_collect.py`:
```python
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import store
from minitoo_dashboard.collect import Sources
from minitoo_dashboard.config import Config

NOW = 1_790_600_000.0
CFG = Config(city_name="Moscow, Russia", city_lat=55.75, city_lon=37.61)
WEATHER = {"fetched_at": NOW - 60, "lat": 55.75, "lon": 37.61, "temp": 12, "code": 3,
           "tmax": 15, "tmin": 8, "precip_from": None, "precip_kind": None}


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.cache, self.sessions = base / "cache", base / "sessions"
        self.sessions.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def sources(self, **kw):
        return Sources(self.cache, self.sessions, **kw)

    def test_model_from_caches(self):
        store.write_json_atomic(self.cache / "weather.json", WEATHER)
        store.write_json_atomic(self.cache / "calendar.json", {"fetched_at": NOW, "status": "ok", "events": [
            {"title": "Call", "start": NOW + 600, "end": NOW + 1200, "calendar": "Work"}]})
        m = self.sources().model(CFG, "working", NOW)
        self.assertEqual(m.weather.temp, 12)
        self.assertEqual(m.event.title, "Call")
        self.assertFalse(m.claude.has_data)
        self.assertEqual((m.status, m.city_name), ("working", "Moscow, Russia"))

    def test_weather_for_other_city_ignored(self):
        store.write_json_atomic(self.cache / "weather.json", {**WEATHER, "lat": 1.0})
        self.assertIsNone(self.sources().model(CFG, "chilling", NOW).weather)

    def test_denied_calendar_has_no_event(self):
        store.write_json_atomic(self.cache / "calendar.json", {"status": "denied", "events": []})
        self.assertIsNone(self.sources().model(CFG, "chilling", NOW).event)

    def test_refresh_writes_caches(self):
        src = self.sources(fetch_weather=lambda lat, lon, unit, now: dict(WEATHER, fetched_at=now),
                           fetch_events=lambda app, out: ("ok", []))
        src.refresh_weather(CFG, NOW)
        src.refresh_calendar(CFG, NOW)
        self.assertEqual(store.read_json(self.cache / "weather.json")["fetched_at"], NOW)
        self.assertEqual(store.read_json(self.cache / "calendar.json")["status"], "ok")

    def test_status_reads_sessions(self):
        (self.sessions / "s1").write_text(f"alerting {NOW}\n")
        self.assertEqual(self.sources().status(NOW), "alerting")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_collect -v` → import FAIL.

- [ ] **Step 3: Implement `minitoo_dashboard/collect.py`**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from . import render, status as status_mod, store
from .config import Config
from .sources import calendar, claude, weather


class Sources:
    def __init__(self, cache_dir: Path, sessions_dir: Path, *,
                 fetch_weather: Callable[..., dict] = weather.fetch_weather,
                 fetch_events: Optional[Callable[..., Any]] = None,
                 helper_app: Optional[Path] = None):
        self.cache_dir = Path(cache_dir)
        self.sessions_dir = Path(sessions_dir)
        self.fetch_weather = fetch_weather
        self.fetch_events = fetch_events or calendar.fetch_events
        self.helper_app = helper_app or calendar.HELPER_APP

    def status(self, now: float) -> str:
        return status_mod.aggregate(status_mod.read_sessions(self.sessions_dir), now)

    def refresh_weather(self, cfg: Config, now: float) -> None:
        record = self.fetch_weather(cfg.city_lat, cfg.city_lon, cfg.temp_unit, now)
        store.write_json_atomic(self.cache_dir / "weather.json", record)

    def refresh_calendar(self, cfg: Config, now: float) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        state, events = self.fetch_events(self.helper_app, self.cache_dir)
        store.write_json_atomic(self.cache_dir / "calendar.json",
                                {"fetched_at": now, "status": state, "events": events})

    def model(self, cfg: Config, status: str, now: float) -> render.DashboardModel:
        wcache = store.read_json(self.cache_dir / "weather.json")
        if isinstance(wcache, dict) and (wcache.get("lat"), wcache.get("lon")) != (cfg.city_lat, cfg.city_lon):
            wcache = None
        cal = store.read_json(self.cache_dir / "calendar.json")
        events = calendar.parse_events(cal.get("events")) if isinstance(cal, dict) and cal.get("status") == "ok" else []
        return render.DashboardModel(
            now=now, status=status, lang=cfg.lang, temp_unit=cfg.temp_unit, city_name=cfg.city_name,
            weather=weather.weather_view(wcache, now) if cfg.has_city else None,
            event=calendar.select_event(events, now, cfg.calendar_list()),
            claude=claude.claude_view(store.read_json(self.cache_dir / "claude.json"), now),
        )
```

- [ ] **Step 4: Run** — `python3 -m unittest tests.test_collect -v` → 5 tests OK.

- [ ] **Step 5: Write the failing daemon tests**

`tests/test_daemon.py`:
```python
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import store
from minitoo_dashboard.config import Config
from minitoo_dashboard.daemon import Dashboard
from minitoo_dashboard.device import DeviceError

T = 1_790_600_000.0
CFG = Config(device_mac="AA:BB:CC:DD:EE:FF", city_name="X", city_lat=1.0, city_lon=2.0)


class FakeSources:
    def __init__(self):
        self.state, self.version = "chilling", 0
        self.weather_calls = self.calendar_calls = 0
        self.weather_fails = False

    def status(self, now):
        return self.state

    def refresh_weather(self, cfg, now):
        self.weather_calls += 1
        if self.weather_fails:
            raise OSError("offline")

    def refresh_calendar(self, cfg, now):
        self.calendar_calls += 1

    def model(self, cfg, status, now):
        return (status, self.version)


class FakeDevice:
    def __init__(self):
        self.calls, self.fail = [], False

    def _record(self, *call):
        self.calls.append(call)
        if self.fail:
            raise DeviceError("offline")

    def send_rawfile(self, path, delay_ms):
        self._record("rawfile", Path(path).read_text().splitlines()[0])

    def select_clock(self, clock_id, device_id):
        self._record("clock", clock_id, device_id)

    def stop(self):
        self.calls.append(("stop",))


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.sources, self.device = FakeSources(), FakeDevice()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, alert=(988, 1), cfg=CFG):
        return Dashboard(sources=self.sources, device_for=lambda mac: self.device, load_config=lambda: cfg,
                         clauddy_alert=lambda: alert,
                         dashboard_blob=lambda model, c: repr(model).encode(),
                         alert_blob=lambda c: b"ALERT", home=self.home)

    def sends(self):
        return [c for c in self.device.calls if c[0] in ("rawfile", "clock")]

    def test_sends_once_until_model_changes(self):
        dash = self.make()
        dash.tick(T)
        dash.tick(T + 1)
        self.assertEqual(len(self.sends()), 1)
        self.sources.version = 1
        dash.tick(T + 2)
        self.assertEqual(len(self.sends()), 2)

    def test_alert_uses_clauddy_face_then_returns(self):
        dash = self.make()
        dash.tick(T)
        self.sources.state = "alerting"
        dash.tick(T + 1)
        dash.tick(T + 2)
        self.assertEqual(self.sends()[1:], [("clock", 988, 1)])
        self.sources.state = "chilling"
        dash.tick(T + 3)
        self.assertEqual(self.sends()[-1][0], "rawfile")

    def test_alert_without_clauddy_sends_alert_frame(self):
        dash = self.make(alert=None)
        self.sources.state = "alerting"
        dash.tick(T)
        self.assertEqual(self.sends(), [("rawfile", "8b 00 05 00 00 00")])

    def test_backoff_after_device_error(self):
        dash = self.make()
        self.device.fail = True
        dash.tick(T)
        dash.tick(T + 10)
        self.assertEqual(len(self.sends()), 1)
        self.device.fail = False
        dash.tick(T + 31)
        self.assertEqual(len(self.sends()), 2)
        self.assertIsNone(store.read_json(self.home / "state.json")["last_error"])

    def test_paused_stops_device_once(self):
        (self.home / "paused").touch()
        dash = self.make()
        dash.tick(T)
        dash.tick(T + 1)
        self.assertEqual(self.device.calls, [("stop",)])
        (self.home / "paused").unlink()
        dash.tick(T + 2)
        self.assertEqual(len(self.sends()), 1)

    def test_wake_jump_forces_refresh(self):
        dash = self.make()
        dash.tick(T)
        dash.tick(T + 1)
        self.assertEqual(self.sources.weather_calls, 1)
        dash.tick(T + 100)
        self.assertEqual(self.sources.weather_calls, 2)
        self.assertEqual(len(self.sends()), 2)

    def test_weather_failure_retries_later_and_still_sends(self):
        self.sources.weather_fails = True
        dash = self.make()
        dash.tick(T)
        self.assertEqual(len(self.sends()), 1)
        dash.tick(T + 20)
        self.assertEqual(self.sources.weather_calls, 1)
        dash.tick(T + 25)
        dash.tick(T + 50)  # jump of 25 s < WAKE_JUMP keeps schedule
        self.assertEqual(self.sources.weather_calls, 1)

    def test_no_device_configured(self):
        dash = self.make(cfg=Config())
        dash.tick(T)
        self.assertEqual(self.device.calls, [])
        self.assertIn("DEVICE_MAC", store.read_json(self.home / "state.json")["last_error"])

    def test_state_file(self):
        dash = self.make()
        self.sources.state = "working"
        dash.tick(T)
        state = store.read_json(self.home / "state.json")
        self.assertEqual((state["status"], state["shown"], state["paused"]), ("working", "dashboard", False))


if __name__ == "__main__":
    unittest.main()
```

Note on `test_weather_failure_retries_later_and_still_sends`: ticks are ≤25 s apart, so no wake jump; the retry is due at `T + 300`.

- [ ] **Step 6: Run to verify failure** — `python3 -m unittest tests.test_daemon -v` → import FAIL.

- [ ] **Step 7: Implement `minitoo_dashboard/daemon.py`**

```python
from __future__ import annotations

import logging
import logging.handlers
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from . import config as config_mod, encode, paths, render, store
from .collect import Sources
from .device import Device, DeviceError

log = logging.getLogger("minitoo_dashboard")

WEATHER_EVERY = 900
WEATHER_RETRY = 300
CALENDAR_EVERY = 60
WAKE_JUMP = 30
HEARTBEAT = 60
BACKOFF = (30, 60, 120, 300)


class Backoff:
    def __init__(self, schedule: Tuple[int, ...] = BACKOFF):
        self.schedule, self.failures, self.until = schedule, 0, 0.0

    def ready(self, now: float) -> bool:
        return now >= self.until

    def fail(self, now: float) -> int:
        delay = self.schedule[min(self.failures, len(self.schedule) - 1)]
        self.failures += 1
        self.until = now + delay
        return delay

    def ok(self) -> None:
        self.failures, self.until = 0, 0.0


def default_dashboard_blob(model: render.DashboardModel, cfg: config_mod.Config) -> bytes:
    return encode.build_blob(render.render_pages(model), cfg.page_seconds * 1000)


def default_alert_blob(cfg: config_mod.Config) -> bytes:
    return encode.build_blob([render.render_alert(cfg.lang)], 1000)


class Dashboard:
    def __init__(self, *, sources: Any, device_for: Callable[[str], Any],
                 load_config: Callable[[], config_mod.Config] = config_mod.load_config,
                 clauddy_alert: Callable[[], Optional[Tuple[int, int]]] = config_mod.clauddy_alert,
                 dashboard_blob: Callable[..., bytes] = default_dashboard_blob,
                 alert_blob: Callable[..., bytes] = default_alert_blob,
                 home: Optional[Path] = None):
        self.sources, self.device_for, self.load_config = sources, device_for, load_config
        self.clauddy_alert, self.dashboard_blob, self.alert_blob = clauddy_alert, dashboard_blob, alert_blob
        self.home = Path(home or paths.home())
        self.backoff = Backoff()
        self.device: Any = None
        self.device_mac: Optional[str] = None
        self.last_blob: Optional[bytes] = None
        self.alert_shown = False
        self.paused = False
        self.status = "chilling"
        self.last_tick: Optional[float] = None
        self.next_weather = self.next_calendar = 0.0
        self.weather_key: Any = None
        self.last_sent_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self._last_state: Any = None
        self._last_state_write = 0.0

    def _device(self, cfg: config_mod.Config) -> Any:
        if cfg.device_mac != self.device_mac:
            self.device_mac = cfg.device_mac
            self.device = self.device_for(cfg.device_mac) if cfg.device_mac else None
        return self.device

    def tick(self, now: float) -> None:
        cfg = self.load_config()
        if self.last_tick is not None and now - self.last_tick > WAKE_JUMP:
            log.info("clock jumped %.0fs (sleep/wake); refreshing everything", now - self.last_tick)
            self.next_weather = self.next_calendar = 0.0
            self.last_blob = None
            self.backoff.ok()
        self.last_tick = now
        device = self._device(cfg)

        if (self.home / "paused").exists():
            if not self.paused and device is not None:
                device.stop()
            self.paused, self.last_blob, self.alert_shown = True, None, False
            self._write_state(now)
            return
        self.paused = False

        self._refresh(cfg, now)
        self.status = self.sources.status(now)
        if device is None:
            self.last_error = "no device configured (DEVICE_MAC in config)"
        elif self.backoff.ready(now):
            try:
                if self.status == "alerting":
                    self._show_alert(cfg, device)
                else:
                    self._show_dashboard(cfg, device, now)
                self.backoff.ok()
            except DeviceError as exc:
                delay = self.backoff.fail(now)
                self.last_error, self.last_blob, self.alert_shown = str(exc), None, False
                log.warning("device error: %s (retry in %ss)", exc, delay)
        self._write_state(now)

    def _refresh(self, cfg: config_mod.Config, now: float) -> None:
        key = (cfg.city_lat, cfg.city_lon, cfg.temp_unit)
        if key != self.weather_key:
            self.weather_key, self.next_weather = key, 0.0
        if cfg.has_city and now >= self.next_weather:
            try:
                self.sources.refresh_weather(cfg, now)
                self.next_weather = now + WEATHER_EVERY
            except Exception as exc:  # network, JSON, API changes: keep last data
                log.warning("weather refresh failed: %s", exc)
                self.next_weather = now + WEATHER_RETRY
        if now >= self.next_calendar:
            try:
                self.sources.refresh_calendar(cfg, now)
            except Exception as exc:
                log.warning("calendar refresh failed: %s", exc)
            self.next_calendar = now + CALENDAR_EVERY

    def _show_alert(self, cfg: config_mod.Config, device: Any) -> None:
        if self.alert_shown:
            return
        target = self.clauddy_alert()
        if target:
            device.select_clock(*target)
        else:
            self._send(cfg, device, self.alert_blob(cfg))
        self.alert_shown, self.last_blob = True, None

    def _show_dashboard(self, cfg: config_mod.Config, device: Any, now: float) -> None:
        self.alert_shown = False
        blob = self.dashboard_blob(self.sources.model(cfg, self.status, now), cfg)
        if blob == self.last_blob:
            return
        self._send(cfg, device, blob)
        self.last_blob = blob

    def _send(self, cfg: config_mod.Config, device: Any, blob: bytes) -> None:
        path = self.home / "cache" / "frame.raw"
        encode.write_rawfile(blob, path)
        device.send_rawfile(path, cfg.send_delay_ms)
        self.last_sent_at, self.last_error = time.time(), None

    def _write_state(self, now: float) -> None:
        shown = "paused" if self.paused else ("alert" if self.alert_shown else
                                              ("dashboard" if self.last_blob else "none"))
        state = {"status": self.status, "shown": shown, "paused": self.paused,
                 "last_sent_at": self.last_sent_at, "last_error": self.last_error,
                 "retry_at": self.backoff.until or None}
        if state == self._last_state and now - self._last_state_write < HEARTBEAT:
            return
        self._last_state, self._last_state_write = state, now
        try:
            store.write_json_atomic(self.home / "state.json", dict(state, updated_at=now))
        except OSError as exc:
            log.warning("cannot write state: %s", exc)


def setup_logging() -> None:
    paths.home().mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(paths.log_path(), maxBytes=1_000_000, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def run_forever() -> None:
    setup_logging()
    sources = Sources(paths.cache_dir(), paths.sessions_dir())
    dashboard = Dashboard(sources=sources, device_for=lambda mac: Device(mac))
    log.info("minitoo-dashboard started")
    while True:
        try:
            dashboard.tick(time.time())
        except Exception:
            log.exception("tick failed")
        time.sleep(1)
```

- [ ] **Step 8: Run all tests** — `python3 -m unittest discover -s tests -v` → all OK (collect 5 + daemon 9 plus earlier).

- [ ] **Step 9: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add dashboard daemon loop and data collector"
```

---

### Task 11: CLI and `/dashboard-city`

**Files:**
- Create: `minitoo_dashboard/cli.py`, `bin/minitoo-dashboard`, `commands/dashboard-city.md`, `tests/test_cli.py`

**Interfaces:**
- Consumes: config, weather.geocode, render, store, daemon.run_forever.
- Produces: `cli.main(argv=None, geocode=None) -> int` with subcommands `run`, `init --mac --lang --temp-unit`, `city NAME... [--pick N]`, `status`, `preview [--demo] [--out DIR]`, `pause`, `resume`, `logs [-n N]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:
```python
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from minitoo_dashboard import cli, config
from minitoo_dashboard.sources.weather import Place

MOSCOW = Place("Moscow", "Russia", "Moscow", 55.75, 37.61)
PARIS_TX = Place("Paris", "United States", "Texas", 33.66, -95.55)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"MINITOO_DASHBOARD_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, argv, geocode=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch("sys.stdin", io.StringIO("")):
            code = cli.main(argv, geocode=geocode)
        return code, out.getvalue()

    def test_city_single_result(self):
        code, out = self.run_cli(["city", "Moscow"], geocode=lambda name, lang: [MOSCOW])
        self.assertEqual(code, 0)
        cfg = config.load_config()
        self.assertEqual((cfg.city_name, cfg.city_lat), ("Moscow, Russia", 55.75))

    def test_city_ambiguous_needs_pick(self):
        code, out = self.run_cli(["city", "Paris"], geocode=lambda name, lang: [MOSCOW, PARIS_TX])
        self.assertEqual(code, 2)
        self.assertIn("2. Paris, Texas, United States", out)
        self.assertIn("--pick", out)
        code, _ = self.run_cli(["city", "Paris", "--pick", "2"], geocode=lambda name, lang: [MOSCOW, PARIS_TX])
        self.assertEqual((code, config.load_config().city_lat), (0, 33.66))

    def test_city_not_found(self):
        code, _ = self.run_cli(["city", "Nowhere"], geocode=lambda name, lang: [])
        self.assertEqual(code, 1)

    def test_init_preserves_city(self):
        config.save_config(config.Config(city_name="Moscow, Russia", city_lat=55.75, city_lon=37.61))
        code, _ = self.run_cli(["init", "--mac", "aa:bb:cc:dd:ee:ff", "--lang", "ru", "--temp-unit", "fahrenheit"])
        cfg = config.load_config()
        self.assertEqual((code, cfg.device_mac, cfg.lang, cfg.temp_unit, cfg.city_lat),
                         (0, "AA:BB:CC:DD:EE:FF", "ru", "fahrenheit", 55.75))

    def test_pause_resume(self):
        self.run_cli(["pause"])
        self.assertTrue((self.home / "paused").exists())
        self.run_cli(["resume"])
        self.assertFalse((self.home / "paused").exists())

    def test_status_without_daemon(self):
        code, out = self.run_cli(["status"])
        self.assertEqual(code, 0)
        self.assertIn("not running", out)

    def test_preview_demo(self):
        out_dir = self.home / "preview"
        code, _ = self.run_cli(["preview", "--demo", "--out", str(out_dir)])
        self.assertEqual(code, 0)
        files = sorted(p.name for p in out_dir.iterdir())
        self.assertEqual(files, ["alert.png", "page-1.png", "page-2.png", "page-3.png"])
        self.assertEqual(Image.open(out_dir / "page-1.png").size, (480, 384))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_cli -v` → import FAIL.

- [ ] **Step 3: Implement `minitoo_dashboard/cli.py`**

```python
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from PIL import Image

from . import config, paths, render, store
from .collect import Sources
from .sources import weather


def _ago(ts: Optional[float], now: float) -> str:
    if not ts:
        return "never"
    seconds = int(now - ts)
    return f"{seconds}s ago" if seconds < 120 else f"{seconds // 60} min ago"


def cmd_init(args) -> int:
    cfg = config.load_config()
    if args.mac:
        cfg.device_mac = args.mac.replace("-", ":").upper()
    if args.lang:
        cfg.lang = args.lang
    if args.temp_unit:
        cfg.temp_unit = args.temp_unit
    config.save_config(cfg)
    print(f"Config written: {paths.config_path()}")
    return 0


def cmd_city(args, geocode: Callable) -> int:
    cfg = config.load_config()
    name = " ".join(args.name)
    try:
        places = geocode(name, cfg.lang)
    except OSError as exc:
        print(f"Could not reach the geocoding service: {exc}", file=sys.stderr)
        return 1
    if not places:
        print(f"No city found for '{name}'.", file=sys.stderr)
        return 1
    if len(places) == 1:
        chosen = places[0]
    elif args.pick:
        if not 1 <= args.pick <= len(places):
            print(f"--pick must be 1..{len(places)}", file=sys.stderr)
            return 2
        chosen = places[args.pick - 1]
    else:
        for i, place in enumerate(places, 1):
            print(f"{i}. {place.label()}")
        if not sys.stdin.isatty():
            print(f"Several places match. Re-run with --pick N, e.g.: minitoo-dashboard city \"{name}\" --pick 1")
            return 2
        answer = input(f"Choose 1-{len(places)}: ").strip()
        if not answer.isdigit() or not 1 <= int(answer) <= len(places):
            print("No city selected.", file=sys.stderr)
            return 2
        chosen = places[int(answer) - 1]
    cfg.city_name, cfg.city_lat, cfg.city_lon = chosen.label(), chosen.lat, chosen.lon
    config.save_config(cfg)
    print(f"Weather city set to {chosen.label()} ✓")
    return 0


def cmd_status(args) -> int:
    now = time.time()
    cfg = config.load_config()
    state = store.read_json(paths.state_path()) or {}
    alive = bool(state) and now - state.get("updated_at", 0) < 90
    cache = paths.cache_dir()
    print(f"Daemon:        {'running (' + _ago(state.get('updated_at'), now) + ')' if alive else 'not running'}")
    print(f"Paused:        {'yes' if paths.paused_path().exists() else 'no'}")
    print(f"Device:        {cfg.device_mac or 'not configured'}")
    print(f"Claude status: {state.get('status', '?')}   screen: {state.get('shown', '?')}")
    print(f"Last sent:     {_ago(state.get('last_sent_at'), now)}")
    if state.get("last_error"):
        print(f"Last error:    {state['last_error']}")
    w = store.read_json(cache / "weather.json")
    print(f"Weather:       {cfg.city_name or 'disabled'}"
          + (f" (updated {_ago(w.get('fetched_at'), now)})" if cfg.has_city and isinstance(w, dict) else ""))
    cal = store.read_json(cache / "calendar.json") or {}
    cal_status = cal.get("status", "unknown")
    hint = "  → allow calendar-helper in System Settings > Privacy & Security > Calendars" \
        if cal_status in ("denied", "not_determined") else ""
    print(f"Calendar:      {cal_status}{hint}")
    c = store.read_json(cache / "claude.json")
    print(f"Claude limits: {'captured ' + _ago(c.get('captured_at'), now) if isinstance(c, dict) else 'no data yet'}")
    return 0


def cmd_preview(args) -> int:
    now = time.time()
    cfg = config.load_config()
    if args.demo:
        model = render.demo_model(now, cfg.lang)
    else:
        sources = Sources(paths.cache_dir(), paths.sessions_dir())
        model = sources.model(cfg, sources.status(now), now)
    out = Path(args.out) if args.out else paths.home() / "preview"
    out.mkdir(parents=True, exist_ok=True)
    images = render.render_pages(model)
    for i, page in enumerate(images, 1):
        page.resize((render.W * 3, render.H * 3), Image.NEAREST).save(out / f"page-{i}.png")
    render.render_alert(cfg.lang).resize((render.W * 3, render.H * 3), Image.NEAREST).save(out / "alert.png")
    print(f"Wrote {len(images)} page(s) and alert.png to {out}")
    return 0


def cmd_pause(args) -> int:
    paths.home().mkdir(parents=True, exist_ok=True)
    paths.paused_path().touch()
    print("Paused: the dashboard released the device (e.g. for the phone app). Run 'minitoo-dashboard resume' to continue.")
    return 0


def cmd_resume(args) -> int:
    try:
        paths.paused_path().unlink()
    except FileNotFoundError:
        pass
    print("Resumed.")
    return 0


def cmd_logs(args) -> int:
    try:
        lines = paths.log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        print("No log yet.")
        return 0
    print("\n".join(lines[-args.n:]))
    return 0


def cmd_run(args) -> int:
    from .daemon import run_forever
    run_forever()
    return 0


def main(argv: Optional[List[str]] = None, geocode: Optional[Callable] = None) -> int:
    parser = argparse.ArgumentParser(prog="minitoo-dashboard", description="MiniToo dashboard")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the daemon (used by launchd)")
    p = sub.add_parser("init", help="write device/language/unit settings")
    p.add_argument("--mac")
    p.add_argument("--lang", choices=("en", "ru"))
    p.add_argument("--temp-unit", choices=("celsius", "fahrenheit"))
    p = sub.add_parser("city", help="set the weather city")
    p.add_argument("name", nargs="+")
    p.add_argument("--pick", type=int)
    sub.add_parser("status", help="show what the dashboard is doing")
    p = sub.add_parser("preview", help="render pages to PNG without a device")
    p.add_argument("--demo", action="store_true", help="use sample data")
    p.add_argument("--out")
    sub.add_parser("pause", help="stop driving the device")
    sub.add_parser("resume", help="resume driving the device")
    p = sub.add_parser("logs", help="show recent log lines")
    p.add_argument("-n", type=int, default=40)
    args = parser.parse_args(argv)

    if args.command == "city":
        return cmd_city(args, geocode or weather.geocode)
    return {"run": cmd_run, "init": cmd_init, "status": cmd_status, "preview": cmd_preview,
            "pause": cmd_pause, "resume": cmd_resume, "logs": cmd_logs}[args.command](args)
```

`bin/minitoo-dashboard` (then `chmod +x`):
```python
#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minitoo_dashboard.cli import main  # noqa: E402

sys.exit(main())
```

`commands/dashboard-city.md` (template; the installer replaces `__BIN__`):
```markdown
---
description: Set the city for the MiniToo dashboard weather page
argument-hint: <city name>
---
Set the MiniToo dashboard weather city to: $ARGUMENTS

Run `"__BIN__" city $ARGUMENTS`. If it prints a numbered list and asks for `--pick`, show the list to the user, ask which place they mean, then run `"__BIN__" city $ARGUMENTS --pick <number>`. Report the city that was saved.
```

- [ ] **Step 4: Run tests** — `chmod +x bin/minitoo-dashboard && python3 -m unittest discover -s tests -v` → all OK.

- [ ] **Step 5: Smoke-test the CLI**

Run: `MINITOO_DASHBOARD_HOME=/tmp/mdh bin/minitoo-dashboard preview --demo && ls /tmp/mdh/preview`
Expected: `alert.png page-1.png page-2.png page-3.png`.

- [ ] **Step 6: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add minitoo-dashboard CLI and /dashboard-city command"
```

---

### Task 12: Settings merge, installer, uninstaller, launchd

**Files:**
- Create: `minitoo_dashboard/settings.py`, `tests/test_settings.py`, `launchd/local.minitoo.dashboard.plist.in`, `install.sh`, `uninstall.sh`

**Interfaces:**
- Produces: `settings.HOOK_EVENTS`, `settings.has_clauddy(s)`, `settings.install_hooks(s, hook_path, replace_clauddy=False) -> (dict, list)`, `settings.uninstall_hooks(s, hook_path) -> dict`, `settings.restore_hooks(s, saved) -> dict`, `settings.statusline_state(s, path) -> "absent"|"ours"|"other"`, `settings.install_statusline(s, path) -> dict`, `settings.remove_statusline(s, path) -> dict`, `settings.render_template(src, dst, mapping, xml=False)`, `settings.main(argv)` with subcommands `has-clauddy`, `install-hooks`, `install-statusline`, `uninstall`, `render-template`.

- [ ] **Step 1: Write the failing tests**

`tests/test_settings.py`:
```python
import json
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import settings

HOOK = "/Users/me/Minitoo/apps/minitoo-dashboard/bin/dashboard-hook.sh"
SL = "/Users/me/Minitoo/apps/minitoo-dashboard/bin/statusline.py"
CLAUDDY = {"type": "command", "command": '"/x/apps/clauddy/clauddy-hook.sh" working'}
OTHER = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}


def commands(s, event):
    return [h["command"] for g in s.get("hooks", {}).get(event, []) for h in g["hooks"]]


class HooksTest(unittest.TestCase):
    def test_install_on_empty_is_idempotent(self):
        s, removed = settings.install_hooks({}, HOOK)
        s, _ = settings.install_hooks(s, HOOK)
        for event, arg in settings.HOOK_EVENTS:
            self.assertEqual(commands(s, event), [f'"{HOOK}" {arg}'])
        self.assertEqual(removed, [])

    def test_preserves_unrelated_hooks(self):
        s, _ = settings.install_hooks({"hooks": {"PreToolUse": [OTHER]}, "theme": "dark"}, HOOK)
        self.assertEqual(commands(s, "PreToolUse"), ["my-linter", f'"{HOOK}" working'])
        self.assertEqual(s["theme"], "dark")

    def test_replace_clauddy(self):
        base = {"hooks": {"Stop": [{"hooks": [CLAUDDY]}]}}
        kept, _ = settings.install_hooks(base, HOOK)
        self.assertIn(CLAUDDY["command"], commands(kept, "Stop"))
        replaced, removed = settings.install_hooks(base, HOOK, replace_clauddy=True)
        self.assertEqual(commands(replaced, "Stop"), [f'"{HOOK}" chilling'])
        self.assertEqual(removed, [["Stop", CLAUDDY]])
        self.assertTrue(settings.has_clauddy(base))
        self.assertFalse(settings.has_clauddy(replaced))

    def test_uninstall_and_restore(self):
        base = {"hooks": {"Stop": [{"hooks": [CLAUDDY]}], "PreToolUse": [OTHER]}}
        installed, removed = settings.install_hooks(base, HOOK, replace_clauddy=True)
        cleaned = settings.uninstall_hooks(installed, HOOK)
        self.assertEqual(cleaned["hooks"], {"PreToolUse": [OTHER]})
        restored = settings.restore_hooks(cleaned, removed)
        self.assertEqual(commands(restored, "Stop"), [CLAUDDY["command"]])
        self.assertEqual(settings.restore_hooks(restored, removed), restored)

    def test_uninstall_drops_empty_hooks_key(self):
        installed, _ = settings.install_hooks({}, HOOK)
        self.assertNotIn("hooks", settings.uninstall_hooks(installed, HOOK))


class StatuslineTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(settings.statusline_state({}, SL), "absent")
        ours = settings.install_statusline({}, SL)
        self.assertEqual(settings.statusline_state(ours, SL), "ours")
        other = {"statusLine": {"type": "command", "command": "~/my-line.sh"}}
        self.assertEqual(settings.statusline_state(other, SL), "other")
        self.assertEqual(settings.install_statusline(other, SL), other)
        self.assertEqual(settings.remove_statusline(ours, SL), {})
        self.assertEqual(settings.remove_statusline(other, SL), other)


class CliTest(unittest.TestCase):
    def test_install_and_uninstall_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            sfile, saved = Path(tmp) / "settings.json", Path(tmp) / "saved.json"
            sfile.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [CLAUDDY]}]}}))
            self.assertEqual(settings.main(["has-clauddy", "--settings", str(sfile)]), 0)
            settings.main(["install-hooks", "--settings", str(sfile), "--hook", HOOK,
                           "--saved", str(saved), "--replace-clauddy"])
            settings.main(["install-statusline", "--settings", str(sfile), "--statusline", SL])
            data = json.loads(sfile.read_text())
            self.assertEqual(commands(data, "Stop"), [f'"{HOOK}" chilling'])
            self.assertIn("statusLine", data)
            settings.main(["uninstall", "--settings", str(sfile), "--hook", HOOK,
                           "--statusline", SL, "--saved", str(saved)])
            data = json.loads(sfile.read_text())
            self.assertEqual(commands(data, "Stop"), [CLAUDDY["command"]])
            self.assertNotIn("statusLine", data)
            self.assertFalse(saved.exists())

    def test_render_template_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "t.in", Path(tmp) / "t.out"
            src.write_text("<string>__APP__</string>")
            settings.main(["render-template", "--src", str(src), "--dst", str(dst),
                           "--set", "__APP__=/a & b", "--xml"])
            self.assertEqual(dst.read_text(), "<string>/a &amp; b</string>")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_settings -v` → import FAIL.

- [ ] **Step 3: Implement `minitoo_dashboard/settings.py`**

```python
"""Merge MiniToo dashboard hooks/status line into Claude Code settings.json."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional
from xml.sax.saxutils import escape

from . import store

HOOK_EVENTS = (("UserPromptSubmit", "working"), ("PreToolUse", "working"), ("Notification", "alerting"),
               ("Stop", "chilling"), ("SessionEnd", "end"))
CLAUDDY_MARK = "clauddy-hook.sh"


def _commands(group: dict) -> List[str]:
    return [h.get("command", "") for h in group.get("hooks", []) if isinstance(h, dict)]


def has_clauddy(s: dict) -> bool:
    return any(CLAUDDY_MARK in c for groups in s.get("hooks", {}).values() for g in groups for c in _commands(g))


def _remove(s: dict, predicate: Callable[[str], bool]) -> list:
    removed: list = []
    hooks = s.get("hooks", {})
    for event in list(hooks):
        kept_groups = []
        for group in hooks[event]:
            kept = []
            for entry in group.get("hooks", []):
                if isinstance(entry, dict) and predicate(entry.get("command", "")):
                    removed.append([event, entry])
                else:
                    kept.append(entry)
            if kept:
                kept_groups.append(dict(group, hooks=kept))
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if "hooks" in s and not s["hooks"]:
        del s["hooks"]
    return removed


def install_hooks(settings: dict, hook_path: str, replace_clauddy: bool = False):
    s = copy.deepcopy(settings)
    removed = _remove(s, lambda c: CLAUDDY_MARK in c) if replace_clauddy else []
    hooks = s.setdefault("hooks", {})
    for event, arg in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if any(hook_path in c for g in groups for c in _commands(g)):
            continue
        groups.append({"hooks": [{"type": "command", "command": f'"{hook_path}" {arg}'}]})
    return s, removed


def uninstall_hooks(settings: dict, hook_path: str) -> dict:
    s = copy.deepcopy(settings)
    _remove(s, lambda c: hook_path in c)
    return s


def restore_hooks(settings: dict, saved: list) -> dict:
    s = copy.deepcopy(settings)
    hooks = s.setdefault("hooks", {})
    for event, entry in saved:
        groups = hooks.setdefault(event, [])
        if entry.get("command") not in [c for g in groups for c in _commands(g)]:
            groups.append({"hooks": [entry]})
    if not hooks:
        del s["hooks"]
    return s


def statusline_state(settings: dict, path: str) -> str:
    line = settings.get("statusLine")
    if not line:
        return "absent"
    return "ours" if path in str(line.get("command", "")) else "other"


def install_statusline(settings: dict, path: str) -> dict:
    s = copy.deepcopy(settings)
    if statusline_state(s, path) == "absent":
        s["statusLine"] = {"type": "command", "command": f'"{path}"'}
    return s


def remove_statusline(settings: dict, path: str) -> dict:
    s = copy.deepcopy(settings)
    if statusline_state(s, path) == "ours":
        del s["statusLine"]
    return s


def render_template(src: Path, dst: Path, mapping: Dict[str, str], xml: bool = False) -> None:
    text = Path(src).read_text(encoding="utf-8")
    for key, value in mapping.items():
        text = text.replace(key, escape(value) if xml else value)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(text, encoding="utf-8")


def _load(path: Path) -> dict:
    data = store.read_json(path)
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="minitoo_dashboard.settings")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("has-clauddy")
    p.add_argument("--settings", type=Path, required=True)
    p = sub.add_parser("install-hooks")
    for arg in ("--settings", "--saved"):
        p.add_argument(arg, type=Path, required=True)
    p.add_argument("--hook", required=True)
    p.add_argument("--replace-clauddy", action="store_true")
    p = sub.add_parser("install-statusline")
    p.add_argument("--settings", type=Path, required=True)
    p.add_argument("--statusline", required=True)
    p = sub.add_parser("uninstall")
    for arg in ("--settings", "--saved"):
        p.add_argument(arg, type=Path, required=True)
    p.add_argument("--hook", required=True)
    p.add_argument("--statusline", required=True)
    p = sub.add_parser("render-template")
    p.add_argument("--src", type=Path, required=True)
    p.add_argument("--dst", type=Path, required=True)
    p.add_argument("--set", action="append", default=[])
    p.add_argument("--xml", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "has-clauddy":
        return 0 if has_clauddy(_load(args.settings)) else 1
    if args.command == "install-hooks":
        data, removed = install_hooks(_load(args.settings), args.hook, args.replace_clauddy)
        if removed:
            previous = store.read_json(args.saved) or []
            store.write_json_atomic(args.saved, previous + removed)
        _save(args.settings, data)
        return 0
    if args.command == "install-statusline":
        data = _load(args.settings)
        print(statusline_state(data, args.statusline))
        _save(args.settings, install_statusline(data, args.statusline))
        return 0
    if args.command == "uninstall":
        data = remove_statusline(uninstall_hooks(_load(args.settings), args.hook), args.statusline)
        saved = store.read_json(args.saved)
        if isinstance(saved, list):
            data = restore_hooks(data, saved)
            args.saved.unlink()
        _save(args.settings, data)
        return 0
    mapping = dict(item.split("=", 1) for item in args.set)
    render_template(args.src, args.dst, mapping, args.xml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests** — `python3 -m unittest tests.test_settings -v` → 8 tests OK.

- [ ] **Step 5: Write the launchd template**

`launchd/local.minitoo.dashboard.plist.in`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>local.minitoo.dashboard</string>
    <key>ProgramArguments</key>
    <array>
        <string>__PYTHON__</string>
        <string>__APP__/bin/minitoo-dashboard</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>__HOME__/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>__HOME__/launchd.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
```

- [ ] **Step 6: Write `install.sh`** (then `chmod +x`)

```bash
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
"$BIN" init --mac "$mac" --lang "$lang" --temp-unit "$unit"
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
  cat <<EOF

You already have a status line. To feed Claude limits to the dashboard, add this
near the top of your status line script (after it reads stdin into \$input):

  printf '%s' "\$input" | "$STATUSLINE"

EOF
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
```

- [ ] **Step 7: Write `uninstall.sh`** (then `chmod +x`)

```bash
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
```

- [ ] **Step 8: Check scripts**

Run: `bash -n install.sh && bash -n uninstall.sh && python3 -m unittest discover -s tests -v`
Expected: no syntax errors; all tests OK.

- [ ] **Step 9: Commit**

```bash
git add apps/minitoo-dashboard
git commit -m "Add dashboard installer, uninstaller and settings merge"
```

---

### Task 13: README, owner install, hardware checklist

**Files:**
- Create: `apps/minitoo-dashboard/README.md`, `apps/minitoo-dashboard/docs/page-1.png`, `page-2.png`, `page-3.png`, `alert.png`
- Modify: root `README.md` — one line in "What's in here" under `apps/clauddy/`: ``apps/minitoo-dashboard/  Weather, today's next event and Claude limits as a desk dashboard; pairs with Clauddy.``

- [ ] **Step 1: Generate screenshots**

Run: `cd apps/minitoo-dashboard && MINITOO_DASHBOARD_HOME=/tmp/mdh-shots bin/minitoo-dashboard preview --demo --out docs`
Expected: four PNGs in `docs/`.

- [ ] **Step 2: Write `README.md`**

Sections, in this order, in English:
1. **What it shows** — the three pages and the badge/alert behaviour, with the four screenshots in a table.
2. **Requirements** — macOS, Python 3 + Pillow, Xcode Command Line Tools, a paired MiniToo; Clauddy recommended (instant alert face).
3. **Install** — `cd apps/minitoo-dashboard && ./install.sh`; list what it asks (language, city) and changes (config, calendar permission, launchd agent, hooks with backup, status line, `/dashboard-city`).
4. **Everyday use** — `minitoo-dashboard status | city "<name>" | preview | pause | resume | logs`; `/dashboard-city Kazan` in Claude Code; config keys table (`CITY_*`, `TEMP_UNIT`, `LANG`, `CALENDARS`, `PAGE_SECONDS`, `SEND_DELAY_MS`, `DEVICE_MAC`).
5. **How it works** — the architecture diagram from spec §3 and refresh cadence table.
6. **Troubleshooting** — device off / phone connected (`pause`, backoff), calendar permission, no Claude limits (status line; desktop-app caveat from the spike result), existing status line snippet.
7. **Uninstall** — `./uninstall.sh`.
8. **Credits** — Press Start 2P by CodeMan38, SIL OFL 1.1 (`fonts/OFL.txt`); weather by Open-Meteo.

- [ ] **Step 3: Run the full test suite**

Run: `cd apps/minitoo-dashboard && python3 -m unittest discover -s tests -v`
Expected: all tests OK. Paste the summary line in the report.

- [ ] **Step 4: Commit docs**

```bash
git add apps/minitoo-dashboard README.md
git commit -m "Document the MiniToo dashboard"
```

- [ ] **Step 5: Owner installs on the real machine**

The installer is interactive (MAC confirmation, language, city, Clauddy-hook replacement, Calendar prompt). Ask the owner to run in Terminal:
```bash
cd "/Users/shooreg/Claude code/Minitoo/apps/minitoo-dashboard" && ./install.sh
```

- [ ] **Step 6: Hardware checklist with the owner**

Run `minitoo-dashboard status` after each item; record pass/fail:
1. Within ~5 s the device shows the dashboard; pages cycle every 8 s; dots advance.
2. Start a new Claude session and send a prompt: badge turns orange; after the reply it turns grey.
3. Trigger a permission prompt: the Clauddy alerting face appears; after approving, the dashboard returns (<2 s).
4. Weather page shows the configured city; `minitoo-dashboard city "Kazan"` changes it within ~15 s.
5. Calendar page shows today's next event; the page is absent when nothing is left today.
6. Claude page shows 5-hour and weekly percentages (from a session where the status line runs).
7. Switch the MiniToo off for 2 minutes, then on: the dashboard reappears within 5 minutes without intervention (`logs` shows backoff).
8. `minitoo-dashboard pause` frees the device (phone app can connect); `resume` brings the dashboard back.
9. Close the Mac lid for 5 minutes and reopen: the dashboard refreshes immediately.
10. Send a message from Claude Code in VS Code; `minitoo-dashboard status` shows "Claude limits: captured …s ago" (records whether the VS Code extension runs the status line; update README troubleshooting either way).

- [ ] **Step 7: Fix anything that failed, re-run tests, commit, update handoff**

Update `HANDOFFS/minitoo.md` (outside the repo) per `WORKFLOW.md`: current state, verification results, remaining items (joystick and night mode follow-ups), release the claim.
