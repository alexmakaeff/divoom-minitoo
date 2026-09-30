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

    def test_refresh_claude_writes_cache(self):
        rec = {"captured_at": NOW, "source": "direct",
               "five_hour": {"used_percentage": 62.0, "resets_at": NOW + 600}}
        self.sources(fetch_claude=lambda now: rec).refresh_claude(NOW)
        self.assertEqual(store.read_json(self.cache / "claude.json"), rec)

    def test_status_reads_sessions(self):
        (self.sessions / "s1").write_text(f"alerting {NOW}\n")
        self.assertEqual(self.sources().status(NOW), "alerting")


if __name__ == "__main__":
    unittest.main()
