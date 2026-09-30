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
