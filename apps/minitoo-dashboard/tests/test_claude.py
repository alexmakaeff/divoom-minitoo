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
        view = claude.limits_view(None, NOW)
        self.assertFalse(view.has_data)

    def test_fresh(self):
        view = claude.limits_view(claude.extract(FULL, NOW - 60), NOW)
        self.assertTrue(view.has_data)
        self.assertEqual(view.five, claude.Window(23.5, 7800, False))
        self.assertIsNone(view.as_of)

    def test_stale_sets_as_of(self):
        view = claude.limits_view(claude.extract(FULL, NOW - 1200), NOW)
        self.assertEqual(view.as_of, NOW - 1200)

    def test_reset_passed(self):
        data = {"rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": NOW - 10},
                                "seven_day": FULL["rate_limits"]["seven_day"]}}
        view = claude.limits_view(claude.extract(data, NOW - 9000), NOW)
        self.assertEqual(view.five, claude.Window(None, None, True))
        self.assertFalse(view.week.is_reset)

    def test_missing_window(self):
        rec = claude.extract({"rate_limits": {"five_hour": FULL["rate_limits"]["five_hour"]}}, NOW)
        self.assertEqual(claude.limits_view(rec, NOW).week, claude.Window(None, None, False))


class DirectUsageTest(unittest.TestCase):
    HELPER_OK = {"status": "ok",
                 "five_hour": {"utilization": 62.0, "resets_at": "2026-09-30T11:40:00.256658+00:00"},
                 "seven_day": {"utilization": 30.0, "resets_at": "2026-10-02T09:00:00+00:00"}}

    def test_parse_helper_output(self):
        rec = claude.parse_direct(self.HELPER_OK, 1000.0)
        self.assertEqual(rec["captured_at"], 1000.0)
        self.assertEqual(rec["source"], "direct")
        self.assertEqual(rec["five_hour"]["used_percentage"], 62.0)
        self.assertAlmostEqual(rec["five_hour"]["resets_at"], 1790768400.256658, places=3)
        self.assertEqual(rec["seven_day"]["used_percentage"], 30.0)

    def test_error_status_raises(self):
        with self.assertRaises(claude.DirectError) as ctx:
            claude.parse_direct({"status": "expired"}, 0)
        self.assertIn("expired", str(ctx.exception))

    def test_expired_names_the_terminal_command(self):
        # The Claude app signs in on its own; only `claude` in Terminal renews this token.
        with self.assertRaises(claude.DirectError) as ctx:
            claude.parse_direct({"status": "expired", "detail": "token expired"}, 0)
        self.assertIn("run 'claude' in Terminal", str(ctx.exception))

    def test_keychain_access_statuses_raise_access_error(self):
        for status in ("needs_access", "keychain_denied"):
            with self.assertRaises(claude.KeychainAccessError) as ctx:
                claude.parse_direct({"status": status}, 0)
            self.assertIn("minitoo-dashboard grant-keychain", str(ctx.exception))

    def test_rate_limit_carries_retry_after(self):
        with self.assertRaises(claude.RefusedError) as ctx:
            claude.parse_direct({"status": "http_429", "detail": "rate_limit_error: slow down", "retry_after": 120}, 0)
        self.assertEqual(ctx.exception.retry_after, 120)
        self.assertIn("slow down", str(ctx.exception))

    def test_forbidden_is_refused_with_plan_hint(self):
        with self.assertRaises(claude.RefusedError) as ctx:
            claude.parse_direct({"status": "http_403", "detail": "permission_error: no access"}, 0)
        self.assertIsNone(ctx.exception.retry_after)
        self.assertIn("subscription", str(ctx.exception))

    def test_missing_windows_raise(self):
        with self.assertRaises(claude.DirectError):
            claude.parse_direct({"status": "ok"}, 0)

    def test_null_resets_at_skips_window(self):
        data = {"status": "ok", "five_hour": {"utilization": 5.0, "resets_at": None},
                "seven_day": self.HELPER_OK["seven_day"]}
        self.assertNotIn("five_hour", claude.parse_direct(data, 0))

    def test_fetch_runs_helper_and_parses(self):
        import subprocess as sp
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return sp.CompletedProcess(cmd, 0, json.dumps(self.HELPER_OK), "")
        rec = claude.fetch_direct(Path("/x/usage-helper"), 5.0, run=run)
        self.assertEqual(calls, [["/x/usage-helper"]])
        self.assertEqual(rec["five_hour"]["used_percentage"], 62.0)

    def test_fetch_interactive_passes_flag(self):
        import subprocess as sp
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return sp.CompletedProcess(cmd, 0, json.dumps(self.HELPER_OK), "")
        claude.fetch_direct(Path("/x/usage-helper"), 5.0, run=run, interactive=True)
        self.assertEqual(calls, [["/x/usage-helper", "--interactive"]])

    def test_fetch_bad_output_raises(self):
        import subprocess as sp
        with self.assertRaises(claude.DirectError):
            claude.fetch_direct(Path("/x"), 0, run=lambda cmd, **kw: sp.CompletedProcess(cmd, 1, "garbage", ""))


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
