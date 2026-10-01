# Codex limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Codex (ChatGPT) 5-hour/week limits and Codex working status next to Claude's in the dashboard's limits zone.

**Architecture:** A new pure-ish source `sources/codex.py` reads the tail of recently modified Codex session logs and produces a Claude-format limits record plus a working flag; `Sources.refresh_codex` merges them into `cache/codex.json` every 5 s. The model gains `codex`/`codex_working`; with `CODEX=on` the renderer draws a two-column table instead of the Claude-only row.

**Tech Stack:** Python 3.9 stdlib + Pillow, `unittest`; bash installer.

**Spec:** `docs/superpowers/specs/2026-09-29-minitoo-dashboard-design.md`, section "Amendment (2026-10-01): Codex limits and status".

All commands run from `apps/minitoo-dashboard/`. Test command: `python3 -m unittest discover -s tests`.

## Global Constraints

- `CODEX=off|on`, default `off`; with `off` the screen is pixel-identical to today's.
- Session files chosen by mtime (last 30 min = 1800 s), never by name; `archived_sessions/` ignored.
- Read each file backwards in 64 KB chunks, at most 4 MB.
- Windows by `window_minutes`: 300 → `five_hour`, 10080 → `seven_day`. Only `limit_id` `"codex"` (or missing) counts.
- `captured_at` = the event's ISO `timestamp`; cache limits replaced only by a newer `captured_at`.
- Working = latest of `task_started`/`task_complete`/`turn_aborted` in any recent file is `task_started`; ignored if `checked_at` > 60 s old.
- Refresh every 5 s. Errors keep last data, log a warning, never break the screen.
- Codex colour teal `(16, 163, 127)`; status square = service colour when working, `GREY` otherwise. Staleness 10 min (existing `STALE_AFTER`).
- Codex home: `$CODEX_HOME` or `~/.codex`.

## Review Focus

- A live session file whose last line is half-written → that line is skipped, older lines still parsed.
- A recent file with no `token_count` at all (e.g. brand-new conversation) → does not wipe the cached limits.
- Newest `token_count` is `limit_id: "premium"` with null windows → skipped, the older `codex` record is used.
- `100%` in the Codex column → stays inside the screen (nothing lit at x ≥ 156).
- `~/.codex` missing entirely → no crash, column shows `--`, square grey.

---

### Task 1: Generic limits view

**Files:**
- Modify: `minitoo_dashboard/sources/claude.py` (rename `ClaudeView`→`LimitsView`, `claude_view`→`limits_view`, `_epoch`→`iso_epoch`)
- Modify: `minitoo_dashboard/render.py`, `minitoo_dashboard/collect.py` (callers)
- Test: `tests/test_claude.py`, `tests/test_render.py` (callers)

**Interfaces:**
- Produces: `claude.LimitsView(five: Window, week: Window, as_of: Optional[float], has_data: bool)`, `claude.limits_view(cache, now) -> LimitsView`, `claude.iso_epoch(value) -> Optional[float]`.

- [ ] **Step 1:** Rename with sed across code and tests:

```bash
grep -rl -E 'ClaudeView|claude_view|_epoch' minitoo_dashboard tests | xargs sed -i '' -E 's/ClaudeView/LimitsView/g; s/claude_view/limits_view/g; s/\b_epoch\b/iso_epoch/g'
```

- [ ] **Step 2:** Run `python3 -m unittest discover -s tests` → all 141 pass. `grep -rn "ClaudeView\|claude_view" minitoo_dashboard tests` → nothing.
- [ ] **Step 3:** Commit `Rename ClaudeView to LimitsView for reuse by Codex`.

### Task 2: Codex log reader

**Files:**
- Create: `minitoo_dashboard/sources/codex.py`
- Modify: `minitoo_dashboard/paths.py` (add `codex_sessions_dir()`)
- Test: `tests/test_codex.py`

**Interfaces:**
- Consumes: `claude.iso_epoch`.
- Produces: `codex.scan(root: Path, now: float, memo: dict) -> Tuple[Optional[dict], bool]`, `codex.merge(old, record, working, now) -> dict`, `codex.is_working(cache, now) -> bool`, `paths.codex_sessions_dir() -> Path`. Cache record keys: `captured_at`, `five_hour`, `seven_day` (`used_percentage`, `resets_at`), `working`, `checked_at`.

- [ ] **Step 1: Write the failing tests** `tests/test_codex.py`:

```python
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from minitoo_dashboard.sources import codex

NOW = 1_790_600_000.0


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def limits(at, h5=58.0, wk=21.0, limit_id="codex"):
    rl = {"limit_id": limit_id,
          "primary": {"used_percent": h5, "window_minutes": 300, "resets_at": NOW + 600},
          "secondary": {"used_percent": wk, "window_minutes": 10080, "resets_at": NOW + 86400}}
    return {"timestamp": iso(at), "type": "event_msg", "payload": {"type": "token_count", "info": None, "rate_limits": rl}}


def turn(kind, at):
    return {"timestamp": iso(at), "type": "event_msg", "payload": {"type": kind}}


class CodexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, rows, mtime=NOW - 10, tail=""):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows) + tail)
        os.utime(path, (mtime, mtime))
        return path

    def test_limits_and_working(self):
        self.write("2026/09/23/rollout-a.jsonl", [turn("task_started", NOW - 60), limits(NOW - 30)])
        record, working = codex.scan(self.root, NOW, {})
        self.assertEqual(record, {"captured_at": NOW - 30,
                                  "five_hour": {"used_percentage": 58.0, "resets_at": NOW + 600},
                                  "seven_day": {"used_percentage": 21.0, "resets_at": NOW + 86400}})
        self.assertTrue(working)

    def test_complete_and_aborted_mean_idle(self):
        self.write("a.jsonl", [turn("task_started", NOW - 60), turn("task_complete", NOW - 50)])
        self.write("b.jsonl", [turn("task_started", NOW - 60), turn("turn_aborted", NOW - 50)])
        self.assertEqual(codex.scan(self.root, NOW, {}), (None, False))

    def test_any_working_file_wins(self):
        self.write("a.jsonl", [turn("task_complete", NOW - 50)])
        self.write("b.jsonl", [turn("task_started", NOW - 50)])
        self.assertTrue(codex.scan(self.root, NOW, {})[1])

    def test_newest_record_by_event_time_not_file_name(self):
        self.write("2026/10/01/rollout-new-name.jsonl", [limits(NOW - 300, h5=10.0)])
        self.write("2026/09/23/rollout-old-name.jsonl", [limits(NOW - 20, h5=77.0)])
        self.assertEqual(codex.scan(self.root, NOW, {})[0]["five_hour"]["used_percentage"], 77.0)

    def test_old_files_ignored(self):
        self.write("a.jsonl", [turn("task_started", NOW - 4000), limits(NOW - 4000)], mtime=NOW - 1801)
        self.assertEqual(codex.scan(self.root, NOW, {}), (None, False))

    def test_week_only_record(self):
        row = limits(NOW - 30)
        row["payload"]["rate_limits"]["primary"] = {"used_percent": 5.0, "window_minutes": 10080, "resets_at": NOW + 9}
        row["payload"]["rate_limits"]["secondary"] = None
        self.write("a.jsonl", [row])
        self.assertEqual(codex.scan(self.root, NOW, {})[0],
                         {"captured_at": NOW - 30, "seven_day": {"used_percentage": 5.0, "resets_at": NOW + 9}})

    def test_premium_and_null_windows_skipped(self):
        premium = limits(NOW - 10, limit_id="premium")
        premium["payload"]["rate_limits"].update(primary=None, secondary=None)
        self.write("a.jsonl", [limits(NOW - 30, h5=40.0), premium])
        self.assertEqual(codex.scan(self.root, NOW, {})[0]["five_hour"]["used_percentage"], 40.0)

    def test_half_written_and_garbage_lines_skipped(self):
        self.write("a.jsonl", [limits(NOW - 30)], tail='not json\n{"timestamp": "2026-10-01T12:0')
        self.assertEqual(codex.scan(self.root, NOW, {})[0]["captured_at"], NOW - 30)

    def test_reads_long_file_from_the_end(self):
        filler = [{"timestamp": iso(NOW - 100), "type": "response_item", "payload": {"text": "x" * 1000}}] * 200
        self.write("a.jsonl", [limits(NOW - 500, h5=1.0)] + filler + [limits(NOW - 20, h5=99.0)] + filler)
        self.assertEqual(codex.scan(self.root, NOW, {})[0]["five_hour"]["used_percentage"], 99.0)

    def test_read_limit(self):
        filler = [{"timestamp": iso(NOW - 100), "type": "response_item", "payload": {"text": "x" * 1000}}] * 5000
        self.write("a.jsonl", [limits(NOW - 500)] + filler)
        self.assertIsNone(codex.scan(self.root, NOW, {})[0])

    def test_missing_root(self):
        self.assertEqual(codex.scan(self.root / "nope", NOW, {}), (None, False))

    def test_memo_skips_unchanged_files(self):
        path = self.write("a.jsonl", [limits(NOW - 30)])
        memo = {}
        codex.scan(self.root, NOW, memo)
        memo[str(path)] = (memo[str(path)][0], ({"captured_at": 1.0, "five_hour": {}}, None))
        self.assertEqual(codex.scan(self.root, NOW, memo)[0]["captured_at"], 1.0)

    def test_merge_keeps_newer_limits(self):
        old = {"captured_at": NOW - 10, "five_hour": {"used_percentage": 1.0, "resets_at": NOW}, "working": True}
        older = {"captured_at": NOW - 99, "seven_day": {"used_percentage": 2.0, "resets_at": NOW}}
        merged = codex.merge(old, older, False, NOW)
        self.assertEqual(merged, dict(old, working=False, checked_at=NOW))
        newer = {"captured_at": NOW - 1, "seven_day": {"used_percentage": 2.0, "resets_at": NOW}}
        self.assertEqual(codex.merge(old, newer, True, NOW), dict(newer, working=True, checked_at=NOW))
        self.assertEqual(codex.merge(None, None, False, NOW), {"working": False, "checked_at": NOW})

    def test_is_working(self):
        self.assertTrue(codex.is_working({"working": True, "checked_at": NOW - 59}, NOW))
        self.assertFalse(codex.is_working({"working": True, "checked_at": NOW - 61}, NOW))
        self.assertFalse(codex.is_working({"working": False, "checked_at": NOW}, NOW))
        self.assertFalse(codex.is_working(None, NOW))
```

- [ ] **Step 2:** Run `python3 -m unittest tests.test_codex` → FAIL (`cannot import name 'codex'`).
- [ ] **Step 3: Implement.** `paths.py`:

```python
def codex_sessions_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
```

`sources/codex.py`:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .claude import iso_epoch

ACTIVE_WITHIN = 1800  # same safety net as Claude session files
READ_LIMIT = 4 * 1024 * 1024
CHUNK = 64 * 1024
WORKING_TTL = 60  # the daemon rewrites the flag every 5 s; older means nobody is checking
WINDOWS = {300: "five_hour", 10080: "seven_day"}
TURN_EVENTS = {"task_started": True, "task_complete": False, "turn_aborted": False}
LIMIT_KEYS = ("captured_at", "five_hour", "seven_day")

Result = Tuple[Optional[dict], Optional[bool]]


def recent_files(root: Path, now: float, within: float = ACTIVE_WITHIN) -> List[Tuple[Path, os.stat_result]]:
    found = []
    for dirpath, _, names in os.walk(root):  # a missing root yields nothing
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            if now - st.st_mtime <= within:
                found.append((path, st))
    return found


def lines_backwards(path: Path, limit: int = READ_LIMIT, chunk: int = CHUNK) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        pos = fh.seek(0, os.SEEK_END)
        start = max(0, pos - limit)
        head = b""
        while pos > start:
            step = min(chunk, pos - start)
            pos -= step
            fh.seek(pos)
            parts = (fh.read(step) + head).split(b"\n")
            head = parts[0]  # may continue in the previous chunk
            for line in reversed(parts[1:]):
                if line.strip():
                    yield line
        if start == 0 and head.strip():
            yield head


def limits_record(rate_limits: Any, captured_at: float) -> Optional[dict]:
    if not isinstance(rate_limits, dict) or rate_limits.get("limit_id", "codex") != "codex":
        return None
    record: dict = {"captured_at": captured_at}
    for slot in ("primary", "secondary"):
        window = rate_limits.get(slot)
        if not isinstance(window, dict):
            continue
        key = WINDOWS.get(window.get("window_minutes"))
        pct, resets = window.get("used_percent"), window.get("resets_at")
        if key and isinstance(pct, (int, float)) and isinstance(resets, (int, float)):
            record[key] = {"used_percentage": float(pct), "resets_at": float(resets)}
    return record if len(record) > 1 else None


def scan_file(path: Path) -> Result:
    record: Optional[dict] = None
    working: Optional[bool] = None
    try:
        for raw in lines_backwards(path):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            payload = obj.get("payload")
            if obj.get("type") != "event_msg" or not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if working is None and kind in TURN_EVENTS:
                working = TURN_EVENTS[kind]
            elif record is None and kind == "token_count":
                at = iso_epoch(obj.get("timestamp"))
                record = limits_record(payload.get("rate_limits"), at) if at is not None else None
            if record is not None and working is not None:
                break
    except OSError:
        pass
    return record, working


def scan(root: Path, now: float, memo: Dict[str, Tuple[Tuple[float, int], Result]]) -> Tuple[Optional[dict], bool]:
    best: Optional[dict] = None
    working = False
    seen = set()
    for path, st in recent_files(root, now):
        key, stamp = str(path), (st.st_mtime, st.st_size)
        seen.add(key)
        if key not in memo or memo[key][0] != stamp:
            memo[key] = (stamp, scan_file(path))
        record, busy = memo[key][1]
        working = working or bool(busy)
        if record is not None and (best is None or record["captured_at"] > best["captured_at"]):
            best = record
    for key in set(memo) - seen:
        del memo[key]
    return best, working


def merge(old: Any, record: Optional[dict], working: bool, now: float) -> dict:
    old = old if isinstance(old, dict) else {}
    limits = {k: old[k] for k in LIMIT_KEYS if k in old}
    if record is not None and record["captured_at"] > float(limits.get("captured_at", 0)):
        limits = record
    return dict(limits, working=working, checked_at=now)


def is_working(cache: Any, now: float) -> bool:
    return (isinstance(cache, dict) and cache.get("working") is True
            and now - float(cache.get("checked_at", 0)) <= WORKING_TTL)
```

- [ ] **Step 4:** Run `python3 -m unittest tests.test_codex` → PASS; full suite → PASS.
- [ ] **Step 5:** Commit `Read Codex limits and working status from session logs`.

### Task 3: Config, collector and daemon wiring

**Files:**
- Modify: `minitoo_dashboard/config.py` (`codex: str = "off"`, parse `CODEX`, format line)
- Modify: `minitoo_dashboard/collect.py` (`codex_root`, `codex_memo`, `refresh_codex`, model fields)
- Modify: `minitoo_dashboard/render.py` (`DashboardModel.codex: Optional[LimitsView] = None`, `codex_working: bool = False` — fields only)
- Modify: `minitoo_dashboard/daemon.py` (`CODEX_EVERY = 5`, `next_codex`, wake reset)
- Test: `tests/test_config.py`, `tests/test_collect.py`, `tests/test_daemon.py`

**Interfaces:**
- Consumes: `codex.scan/merge/is_working`, `claude.limits_view`, `paths.codex_sessions_dir`.
- Produces: `Config.codex`, `Sources.refresh_codex(now)`, `Sources(codex_root=...)`, `DashboardModel.codex`, `DashboardModel.codex_working`.

- [ ] **Step 1: Failing tests.**

`tests/test_config.py`:
```python
    def test_codex_switch(self):
        self.assertEqual(config.Config().codex, "off")
        self.assertEqual(config.parse_config("CODEX=on").codex, "on")
        self.assertEqual(config.parse_config("CODEX=yes").codex, "off")
        self.assertIn("CODEX=on", config.format_config(config.Config(codex="on")))
```

`tests/test_collect.py` (add `import json, os`; `codex_root` under the temp dir):
```python
    def test_codex_off_by_default(self):
        m = self.sources().model(CFG, "working", NOW)
        self.assertIsNone(m.codex)
        self.assertFalse(m.codex_working)

    def test_refresh_codex_and_model(self):
        root = Path(self.tmp.name) / "codex"
        (root / "2026").mkdir(parents=True)
        path = root / "2026" / "rollout-x.jsonl"
        rows = [{"timestamp": "2026-09-28T12:53:20Z", "type": "event_msg", "payload": {"type": "task_started"}},
                {"timestamp": "2026-09-28T12:53:20Z", "type": "event_msg", "payload": {"type": "token_count",
                 "rate_limits": {"limit_id": "codex", "primary": {"used_percent": 58.0, "window_minutes": 300,
                                                                  "resets_at": NOW + 600}, "secondary": None}}}]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        os.utime(path, (NOW - 5, NOW - 5))
        src = self.sources(codex_root=root)
        src.refresh_codex(NOW)
        m = src.model(Config(codex="on"), "chilling", NOW)
        self.assertEqual(m.codex.five.pct, 58.0)
        self.assertTrue(m.codex_working)
        self.assertTrue(m.codex.has_data)

    def test_refresh_codex_without_codex_installed(self):
        src = self.sources(codex_root=Path(self.tmp.name) / "missing")
        src.refresh_codex(NOW)
        m = src.model(Config(codex="on"), "chilling", NOW)
        self.assertFalse(m.codex.has_data)
        self.assertFalse(m.codex_working)
```
(`2026-09-28T12:53:20Z` == `NOW` 1_790_600_000 → `captured_at` = NOW, fresh.)

`tests/test_daemon.py` — add to `FakeSources`:
```python
    codex_calls = 0

    def refresh_codex(self, now):
        self.codex_calls += 1
```
and:
```python
    def test_codex_polled_every_five_seconds_when_on(self):
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", codex="on"))
        for t in range(0, 10):
            dash.tick(T + t)
        self.assertEqual(self.sources.codex_calls, 2)

    def test_codex_off_never_polls(self):
        self.make().tick(T)
        self.assertEqual(self.sources.codex_calls, 0)
```

- [ ] **Step 2:** Run the three test modules → FAIL.
- [ ] **Step 3: Implement.**

config.py: field `codex: str = "off"`; in `parse_config`:
```python
        elif key == "CODEX" and value in ("on", "off"):
            cfg.codex = value
```
in `format_config` after `CLAUDE_LIMITS`: `f"CODEX={cfg.codex}",`.

render.py `DashboardModel` (append, defaults keep existing constructors working):
```python
    codex: Optional[LimitsView] = None  # None: CODEX=off, Claude-only layout
    codex_working: bool = False
```

collect.py:
```python
from .sources import calendar, claude, codex, weather
...
                 fetch_claude: Optional[Callable[[float], dict]] = None,
                 codex_root: Optional[Path] = None):
        ...
        self.codex_root = Path(codex_root or paths.codex_sessions_dir())
        self.codex_memo: dict = {}

    def refresh_codex(self, now: float) -> None:
        record, working = codex.scan(self.codex_root, now, self.codex_memo)
        path = self.cache_dir / "codex.json"
        store.write_json_atomic(path, codex.merge(store.read_json(path), record, working, now))
```
in `model`:
```python
        xcache = store.read_json(self.cache_dir / "codex.json") if cfg.codex == "on" else None
        ...
            codex=claude.limits_view(xcache, now) if cfg.codex == "on" else None,
            codex_working=codex.is_working(xcache, now),
```

daemon.py: `CODEX_EVERY = 5`; `self.next_weather = self.next_calendar = self.next_claude = self.next_codex = 0.0` in `__init__` and in the wake-jump reset; at the end of `_refresh`:
```python
        if cfg.codex == "on" and now >= self.next_codex:
            try:
                self.sources.refresh_codex(now)
            except Exception as exc:  # unreadable logs, format change: keep last data
                log.warning("Codex refresh failed: %s", exc)
            self.next_codex = now + CODEX_EVERY
```

- [ ] **Step 4:** Full suite → PASS.
- [ ] **Step 5:** Commit `Wire the Codex source into config, collector and daemon`.

### Task 4: Limits table on screen

**Files:**
- Modify: `minitoo_dashboard/render.py`, `minitoo_dashboard/i18n.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `DashboardModel.codex`, `codex_working`, `LimitsView`.
- Produces: `render.TEAL`, `render.demo_model(now, lang, status="working", codex=False)`.

- [ ] **Step 1: Failing tests** in `ScreenTest` (`has_color` already exists there):

```python
    def table(self, **kw):
        view = LimitsView(Window(58.0, 480, False), Window(21.0, 86400, False), None, True)
        return model(**dict({"codex": view, "codex_working": False}, **kw))

    def test_codex_off_keeps_badge(self):
        self.assertIsNone(model().codex)
        self.assertEqual(render.render_screen(model()).getpixel((151, 119)), render.ORANGE)

    def test_table_squares(self):
        img = render.render_screen(self.table())
        self.assertEqual(img.getpixel((79, 89)), render.ORANGE)   # Claude working
        self.assertEqual(img.getpixel((139, 89)), render.GREY)    # Codex idle
        img = render.render_screen(self.table(status="chilling", codex_working=True))
        self.assertEqual(img.getpixel((79, 89)), render.GREY)
        self.assertEqual(img.getpixel((139, 89)), render.TEAL)

    def test_table_bars_in_service_colours(self):
        img = render.render_screen(self.table())
        self.assertTrue(self.has_color(img, (24, 97, 54, 104), render.ORANGE))
        self.assertTrue(self.has_color(img, (92, 97, 122, 104), render.TEAL))
        self.assertNotEqual(img.getpixel((151, 119)), render.ORANGE)  # no bottom-right badge

    def test_table_full_limits_stay_on_screen(self):
        full = LimitsView(Window(100.0, 600, False), Window(100.0, 3600, False), None, True)
        img = render.render_screen(self.table(claude=full, codex=full))
        self.assertFalse(lit(img, (156, 86, 160, 128)))
        self.assertFalse(lit(img, (89, 97, 92, 115)))  # Claude "100%" stops before the Codex column

    def test_table_without_codex_data(self):
        empty = Window(None, None, False)
        img = render.render_screen(self.table(codex=LimitsView(empty, empty, None, False)))
        self.assertFalse(self.has_color(img, (92, 97, 122, 115), render.TEAL))
        self.assertTrue(lit(img, (124, 97, 156, 104)))  # "--"

    def test_table_stale_footer_fits(self):
        stale = LimitsView(Window(None, None, True), Window(55.0, 3600, False), NOW - 3600, True)
        for lang in ("en", "ru"):
            img = render.render_screen(model(lang, codex=stale, claude=stale))
            self.assertTrue(lit(img, (92, 119, 156, 127)))
            self.assertFalse(lit(img, (156, 119, 160, 128)))
```
(Change the import to `from minitoo_dashboard.sources.claude import LimitsView, Window` — already done by Task 1's sed.)

- [ ] **Step 2:** Run `python3 -m unittest tests.test_render` → FAIL.
- [ ] **Step 3: Implement.**

i18n.py: en add `"wk_short": "wk", "as_of_short": "@{t}"`; ru add `"wk_short": "нд", "as_of_short": "на {t}"`.

render.py:
```python
TEAL = (16, 163, 127)
...
def _limits_footer(v: LimitsView, lang: str, short: bool) -> str:
    if v.as_of is not None:
        return i18n.t(lang, "as_of_short" if short else "as_of", t=_hhmm(v.as_of))
    if v.five.is_reset:
        return i18n.t(lang, "reset")
    if v.five.reset_in is not None:
        left = i18n.duration(v.five.reset_in, lang)
        return left if short else i18n.t(lang, "reset_in", d=left)
    return ""
```
`_claude_row` footer becomes `footer = _limits_footer(c, lang, False)` (replacing the if/elif chain).

```python
def _limits_column(d, x: int, name: str, view: LimitsView, working: bool, color, lang: str) -> None:
    d.text((x, 86), name, font=font(8), fill=color)
    square = x + int(font(8).getlength(name)) + 4
    d.rectangle([square, 86, square + 7, 93], fill=color if working else GREY)
    for y, window in ((97, view.five), (108, view.week)):
        d.rectangle([x, y, x + 29, y + 6], fill=TRACK)
        if window.pct is not None:
            d.rectangle([x, y, x + round(29 * min(window.pct, 100) / 100), y + 6], fill=color)
        text = _pct(window)
        d.text((x + 64 - int(font(8).getlength(text)), y), text, font=font(8), fill=WHITE)
    footer = _limits_footer(view, lang, True) if view.has_data else ""
    d.text((x, 119), _fit(footer, font(8), 64), font=font(8), fill=DIM)


def _limits_table(d, m: DashboardModel) -> None:
    d.text((4, 97), i18n.t(m.lang, "h5"), font=font(8), fill=DIM)
    d.text((4, 108), i18n.t(m.lang, "wk_short"), font=font(8), fill=DIM)
    _limits_column(d, 24, "Claude", m.claude, m.status == "working", ORANGE, m.lang)
    _limits_column(d, 92, "Codex", m.codex, m.codex_working, TEAL, m.lang)
```
`render_screen`:
```python
    if m.codex is None:
        _claude_row(d, m)
        d.rectangle([148, 116, 155, 123], fill=ORANGE if m.status == "working" else GREY)
    else:
        _limits_table(d, m)
```
`demo_model(now, lang, status="working", codex=False)`: pass
`codex=LimitsView(Window(58.0, 480, False), Window(21.0, 3 * 86400, False), None, True) if codex else None`.

- [ ] **Step 4:** Full suite → PASS. Render a preview PNG with the owner's live data and look at it.
- [ ] **Step 5:** Commit `Draw Claude and Codex limits as a table`.

### Task 5: CLI, installer, docs

**Files:**
- Modify: `minitoo_dashboard/cli.py` (`init --codex`, `status` line, `preview --demo` honours `CODEX`)
- Modify: `install.sh` (ask when `${CODEX_HOME:-$HOME/.codex}/sessions` exists)
- Modify: `README.md` (config row + "Codex limits" section)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Failing tests** in `CliTest`:
```python
    def test_init_sets_codex(self):
        self.run_cli(["init", "--codex", "on"])
        self.assertEqual(config.load_config().codex, "on")

    def test_status_shows_codex(self):
        from minitoo_dashboard import store
        _, out = self.run_cli(["status"])
        self.assertIn("Codex limits:  off", out)
        config.save_config(config.Config(codex="on"))
        store.write_json_atomic(self.home / "cache" / "codex.json", {"captured_at": 1.0, "working": False, "checked_at": 1.0})
        _, out = self.run_cli(["status"])
        self.assertIn("Codex limits:  captured", out)
        self.assertIn("idle", out)
```
- [ ] **Step 2:** Run `python3 -m unittest tests.test_cli` → FAIL.
- [ ] **Step 3: Implement.** cli.py: `from .sources import codex as codex_src`; in `cmd_init`: `if args.codex: cfg.codex = args.codex`; parser: `p.add_argument("--codex", choices=("on", "off"))`; at the end of `cmd_status`:
```python
    if cfg.codex == "on":
        x = store.read_json(cache / "codex.json")
        captured = f"captured {_ago(x.get('captured_at'), now)}" \
            if isinstance(x, dict) and x.get("captured_at") else "no data yet"
        print(f"Codex limits:  {captured}   status: {'working' if codex_src.is_working(x, now) else 'idle'}")
    else:
        print("Codex limits:  off (set CODEX=on in the config to show them)")
```
`cmd_preview`: `model = render.demo_model(now, cfg.lang, codex=cfg.codex == "on")`.

install.sh, before the `init` call:
```bash
codex=off
if [ -d "${CODEX_HOME:-$HOME/.codex}/sessions" ]; then
  case "$(ask 'Codex (ChatGPT) found. Show its limits next to Claude? [Y/n] ' y)" in
    n|N|no|No) ;;
    *) codex=on ;;
  esac
fi
```
and append `--codex "$codex"` to the `"$BIN" init` line.

README: add a row `| \`CODEX\` | \`off\` | \`on\` shows Codex (ChatGPT) limits and status next to Claude's |` and a short "Codex limits" section: read from `~/.codex/sessions` logs written by the ChatGPT app and the VS Code extension, refreshed every 5 s, fresh only while you use Codex, no credentials or network.

- [ ] **Step 4:** Full suite → PASS; `bash -n install.sh`.
- [ ] **Step 5:** Commit `Add CODEX setting to CLI, installer and README`.

### Final verification

- [ ] Full suite passes; `git diff main --stat` shows only the files above.
- [ ] `MINITOO_DASHBOARD_HOME=<scratch> python3 -m minitoo_dashboard ...` preview with a real `refresh_codex` against `~/.codex/sessions` → eyeball PNG.
- [ ] Owner: set `CODEX=on`, `launchctl kickstart -k gui/$(id -u)/local.minitoo.dashboard`, check the device.
