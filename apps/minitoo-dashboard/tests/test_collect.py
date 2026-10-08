import json
import os
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

    def test_claude_defaults_to_direct_fetch(self):
        from minitoo_dashboard.sources import claude
        self.assertIs(self.sources().fetch_claude, claude.fetch_direct)

    def test_model_from_caches(self):
        store.write_json_atomic(self.cache / "weather.json", WEATHER)
        store.write_json_atomic(self.cache / "calendar.json", {"fetched_at": NOW, "status": "ok", "events": [
            {"title": "Call", "start": NOW + 600, "end": NOW + 1200, "calendar": "Work"}]})
        m = self.sources().model(CFG, "working", NOW)
        self.assertEqual(m.weather.temp, 12)
        self.assertEqual(m.event.title, "Call")
        self.assertFalse(m.claude.has_data)
        self.assertEqual((m.status, m.city_name), ("working", "Moscow, Russia"))

    def test_model_includes_reminders(self):
        store.write_json_atomic(self.cache / "calendar.json", {
            "status": "ok", "events": [], "reminders_status": "ok",
            "reminders": [{"title": "Bread", "due": NOW - 60, "all_day": False, "calendar": "R"}]})
        item = self.sources().model(CFG, "chilling", NOW).event
        self.assertEqual((item.kind, item.title), ("overdue", "Bread"))

    def test_weather_for_other_city_ignored(self):
        store.write_json_atomic(self.cache / "weather.json", {**WEATHER, "lat": 1.0})
        self.assertIsNone(self.sources().model(CFG, "chilling", NOW).weather)

    def test_denied_calendar_has_no_event(self):
        store.write_json_atomic(self.cache / "calendar.json", {"status": "denied", "events": []})
        self.assertIsNone(self.sources().model(CFG, "chilling", NOW).event)

    def test_refresh_writes_caches(self):
        src = self.sources(fetch_weather=lambda lat, lon, unit, now: dict(WEATHER, fetched_at=now),
                           fetch_events=lambda app, out: ("ok", [], "denied", []))
        src.refresh_weather(CFG, NOW)
        src.refresh_calendar(CFG, NOW)
        self.assertEqual(store.read_json(self.cache / "weather.json")["fetched_at"], NOW)
        self.assertEqual(store.read_json(self.cache / "calendar.json")["status"], "ok")
        self.assertEqual(store.read_json(self.cache / "calendar.json")["reminders_status"], "denied")

    def test_refresh_claude_writes_cache(self):
        rec = {"captured_at": NOW, "source": "direct",
               "five_hour": {"used_percentage": 62.0, "resets_at": NOW + 600}}
        self.sources(fetch_claude=lambda now: rec).refresh_claude(NOW)
        self.assertEqual(store.read_json(self.cache / "claude.json"), rec)

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
        self.assertIsNone(m.codex.as_of)

    def test_refresh_codex_skips_unchanged_writes(self):
        src = self.sources(codex_root=Path(self.tmp.name) / "missing")
        src.refresh_codex(NOW)
        src.refresh_codex(NOW + 5)
        self.assertEqual(store.read_json(self.cache / "codex.json")["checked_at"], NOW)
        src.refresh_codex(NOW + 30)
        self.assertEqual(store.read_json(self.cache / "codex.json")["checked_at"], NOW + 30)

    def test_refresh_codex_without_codex_installed(self):
        src = self.sources(codex_root=Path(self.tmp.name) / "missing")
        src.refresh_codex(NOW)
        m = src.model(Config(codex="on"), "chilling", NOW)
        self.assertFalse(m.codex.has_data)
        self.assertFalse(m.codex_working)

    def test_limit_caches(self):
        store.write_json_atomic(self.cache / "claude.json", {"captured_at": NOW})
        store.write_json_atomic(self.cache / "codex.json", {"captured_at": NOW - 1})
        self.assertEqual(self.sources().limit_caches(CFG), {"claude": {"captured_at": NOW}})
        on = Config(codex="on")
        self.assertEqual(self.sources().limit_caches(on)["codex"], {"captured_at": NOW - 1})

    def test_status_reads_sessions(self):
        (self.sessions / "s1").write_text(f"alerting {NOW}\n")
        self.assertEqual(self.sources().status(NOW), "alerting")


if __name__ == "__main__":
    unittest.main()
