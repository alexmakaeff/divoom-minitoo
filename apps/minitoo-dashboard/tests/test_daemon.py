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

    claude_calls = 0
    claude_fails = False

    def refresh_claude(self, now):
        self.claude_calls += 1
        if self.claude_fails:
            from minitoo_dashboard.sources.claude import DirectError
            raise DirectError("expired")

    codex_calls = 0

    def refresh_codex(self, now):
        self.codex_calls += 1

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

    def test_identical_frame_resent_periodically(self):
        dash = self.make()
        t = T
        dash.tick(t)
        while t + 20 < T + 300:  # last tick at T+280, before the resend is due
            t += 20
            dash.tick(t)
        self.assertEqual(len(self.sends()), 1)
        dash.tick(T + 310)
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

    def test_direct_limits_polled_every_five_minutes(self):
        cfg = Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct")
        dash = self.make(cfg=cfg)
        t = T
        while t <= T + 290:
            dash.tick(t)
            t += 10
        self.assertEqual(self.sources.claude_calls, 1)
        dash.tick(T + 300)
        self.assertEqual(self.sources.claude_calls, 2)

    def test_statusline_mode_never_polls(self):
        dash = self.make()
        dash.tick(T)
        self.assertEqual(self.sources.claude_calls, 0)

    def test_direct_failure_is_reported_and_retried(self):
        self.sources.claude_fails = True
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct"))
        dash.tick(T)
        self.assertIn("expired", store.read_json(self.home / "state.json")["limits_error"])
        self.assertEqual(len(self.sends()), 1)

    def test_codex_polled_every_five_seconds_when_on(self):
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", codex="on"))
        for t in range(0, 10):
            dash.tick(T + t)
        self.assertEqual(self.sources.codex_calls, 2)

    def test_codex_off_never_polls(self):
        self.make().tick(T)
        self.assertEqual(self.sources.codex_calls, 0)

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
