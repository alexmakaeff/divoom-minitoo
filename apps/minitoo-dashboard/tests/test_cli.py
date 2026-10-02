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

    def test_init_sets_limits_source(self):
        self.run_cli(["init", "--claude-limits", "direct"])
        self.assertEqual(config.load_config().claude_limits, "direct")

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
        store.write_json_atomic(self.home / "state.json", {"updated_at": 1.0, "codex_error": "disk full"})
        _, out = self.run_cli(["status"])
        self.assertIn("Codex error:   disk full", out)

    def test_status_hides_limits_error_older_than_data(self):
        from minitoo_dashboard import store
        store.write_json_atomic(self.home / "state.json",
                                {"updated_at": 1.0, "limits_error": "Keychain access needed", "limits_error_at": 100.0})
        store.write_json_atomic(self.home / "cache" / "claude.json", {"captured_at": 50.0, "source": "direct"})
        _, out = self.run_cli(["status"])
        self.assertIn("Limits error:  Keychain access needed", out)
        store.write_json_atomic(self.home / "cache" / "claude.json", {"captured_at": 200.0, "source": "direct"})
        _, out = self.run_cli(["status"])
        self.assertNotIn("Limits error", out)

    def test_grant_keychain_runs_helper_interactively_and_caches(self):
        from minitoo_dashboard import store
        calls = []

        def fetch(helper, now, interactive=False, **kw):
            calls.append(interactive)
            return {"captured_at": now, "source": "direct"}
        with mock.patch("minitoo_dashboard.sources.claude.fetch_direct", fetch):
            code, out = self.run_cli(["grant-keychain"])
        self.assertEqual((code, calls), (0, [True]))
        self.assertEqual(store.read_json(self.home / "cache" / "claude.json")["source"], "direct")

    def test_grant_keychain_reports_failure(self):
        from minitoo_dashboard.sources.claude import KeychainAccessError

        def fetch(helper, now, interactive=False, **kw):
            raise KeychainAccessError("Keychain access was not allowed")
        with mock.patch("minitoo_dashboard.sources.claude.fetch_direct", fetch):
            code, out = self.run_cli(["grant-keychain"])
        self.assertEqual(code, 1)
        self.assertIn("not allowed", out)

    def test_pause_resume(self):
        self.run_cli(["pause"])
        self.assertTrue((self.home / "paused").exists())
        self.run_cli(["resume"])
        self.assertFalse((self.home / "paused").exists())

    def test_status_without_daemon(self):
        code, out = self.run_cli(["status"])
        self.assertEqual(code, 0)
        self.assertIn("not running", out)

    def test_status_shows_reminders_access(self):
        from minitoo_dashboard import store
        store.write_json_atomic(self.home / "cache" / "calendar.json",
                                {"status": "ok", "events": [], "reminders_status": "denied", "reminders": []})
        _, out = self.run_cli(["status"])
        self.assertIn("Reminders:     denied", out)
        self.assertIn("Privacy & Security > Reminders", out)

    def test_preview_demo(self):
        out_dir = self.home / "preview"
        code, _ = self.run_cli(["preview", "--demo", "--out", str(out_dir)])
        self.assertEqual(code, 0)
        files = sorted(p.name for p in out_dir.iterdir())
        self.assertEqual(files, ["alert.png", "screen.png"])
        self.assertEqual(Image.open(out_dir / "screen.png").size, (480, 384))


if __name__ == "__main__":
    unittest.main()
