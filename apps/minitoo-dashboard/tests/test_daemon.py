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
    claude_error = None
    on_claude = None

    def refresh_claude(self, now):
        self.claude_calls += 1
        if self.on_claude:
            self.on_claude()
        if self.claude_error:
            raise self.claude_error
        if self.claude_fails:
            from minitoo_dashboard.sources.claude import DirectError
            raise DirectError("expired")

    codex_calls = 0
    codex_fails = None

    def refresh_codex(self, now):
        self.codex_calls += 1
        if self.codex_fails:
            raise OSError(self.codex_fails)

    caches = {}

    def limit_caches(self, cfg):
        return self.caches

    def model(self, cfg, status, now):
        return (status, self.version)


class FakeDevice:
    def __init__(self):
        self.calls, self.fail, self.replies = [], False, True

    def _record(self, *call):
        self.calls.append(call)
        if self.fail:
            raise DeviceError("offline")

    def send_rawfile(self, path, delay_ms):
        self._record("rawfile", Path(path).read_text().splitlines()[0])
        return len(self.calls)

    def replied_since(self, mark):
        return self.replies

    def select_clock(self, clock_id, device_id):
        self._record("clock", clock_id, device_id)

    def stop(self):
        self.calls.append(("stop",))


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.sources, self.device = FakeSources(), FakeDevice()
        self.notices, self.limit_frames = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, alert=(988, 1), cfg=CFG, monotonic=None):
        return Dashboard(sources=self.sources, device_for=lambda mac: self.device, load_config=lambda: cfg,
                         clauddy_alert=lambda: alert,
                         dashboard_blob=lambda model, c: repr(model).encode(),
                         alert_blob=lambda c: b"ALERT", home=self.home,
                         limit_alert_blob=lambda crossing, c, now: self.limit_frames.append(crossing.key) or b"LIMIT",
                         monotonic=monotonic or (lambda: 0.0),
                         notify=lambda title, body: self.notices.append((title, body)))

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

    def test_slow_tick_is_not_mistaken_for_wake(self):
        # A 30 s Keychain wait plus a 20 s device timeout used to look like sleep/wake,
        # which re-ran usage-helper every ~50 s and stacked Keychain prompts.
        clock = [0.0]
        self.sources.on_claude = lambda: clock.__setitem__(0, clock[0] + 30)
        self.device.fail = True
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct"),
                         monotonic=lambda: clock[0])
        dash.tick(T)
        clock[0] += 1
        dash.tick(T + 31)  # wall clock moved only by the time the slow tick took, plus the 1 s sleep
        self.assertEqual(self.sources.claude_calls, 1)
        self.assertEqual(self.sources.calendar_calls, 1)

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

    def test_keychain_access_error_backs_off_and_logs_once(self):
        from minitoo_dashboard.sources.claude import KeychainAccessError
        self.sources.claude_error = KeychainAccessError("Keychain access needed")
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct"))
        with self.assertLogs("minitoo_dashboard", "INFO") as logs:
            for t in range(0, 3600, 10):
                dash.tick(T + t)
        self.assertEqual(self.sources.claude_calls, 2)  # every 30 min, not every 5
        self.assertEqual(sum("Keychain access needed" in line for line in logs.output), 1)
        state = store.read_json(self.home / "state.json")
        self.assertIn("Keychain access needed", state["limits_error"])
        self.assertEqual(state["limits_error_at"], T + 1800)
        self.sources.claude_error = None
        dash.tick(T + 3600)
        self.assertIsNone(store.read_json(self.home / "state.json")["limits_error"])

    def test_lost_keychain_access_notifies_once_per_episode(self):
        from minitoo_dashboard.sources.claude import DirectError, KeychainAccessError
        cfg = Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct", lang="ru")
        dash = self.make(cfg=cfg)
        self.sources.claude_error = DirectError("network")
        dash.tick(T)
        self.assertEqual(self.notices, [])  # ordinary failures stay quiet
        self.sources.claude_error = KeychainAccessError("Keychain access needed")
        for t in range(300, 4000, 10):
            dash.tick(T + t)
        self.assertEqual(len(self.notices), 1)
        self.assertIn("grant-keychain", self.notices[0][1])
        self.assertIn("Keychain", self.notices[0][1])
        self.sources.claude_error = None
        dash.tick(T + 6000)
        self.sources.claude_error = KeychainAccessError("Keychain access needed")
        dash.tick(T + 6300)
        self.assertEqual(len(self.notices), 2)  # access came back, then was lost again

    def test_notification_failure_does_not_break_tick(self):
        from minitoo_dashboard.sources.claude import KeychainAccessError
        self.sources.claude_error = KeychainAccessError("Keychain access needed")
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct"))

        def broken(title, body):
            raise OSError("osascript missing")
        dash.notify = broken
        dash.tick(T)
        self.assertEqual(len(self.sends()), 1)

    def claude_times(self, dash, until, step=10):
        times, calls = [], self.sources.claude_calls
        for t in range(0, until, step):
            dash.tick(T + t)
            if self.sources.claude_calls != calls:
                calls = self.sources.claude_calls
                times.append(t)
        return times

    def test_refused_requests_back_off_and_recover(self):
        from minitoo_dashboard.sources.claude import RefusedError
        self.sources.claude_error = RefusedError("usage-helper: http_403", None)
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct"))
        self.assertEqual(self.claude_times(dash, 11401), [0, 600, 1800, 4200, 7800, 11400])
        self.sources.claude_error = None
        dash.tick(T + 15000)
        self.sources.claude_error = RefusedError("usage-helper: http_403", None)
        dash.tick(T + 15300)
        dash.tick(T + 15600)
        self.assertEqual(self.sources.claude_calls, 8)  # success reset the backoff to 10 min
        dash.tick(T + 15900)
        self.assertEqual(self.sources.claude_calls, 9)

    def test_retry_after_is_honoured_and_survives_wake(self):
        from minitoo_dashboard.sources.claude import RefusedError
        self.sources.claude_error = RefusedError("usage-helper: http_429", 7200)
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct"))
        dash.tick(T)
        dash.tick(T + 1000)  # wake jump: everything else refreshes, the refused request waits
        self.assertEqual(self.sources.claude_calls, 1)
        self.assertEqual(self.sources.calendar_calls, 2)
        dash.tick(T + 7200)
        self.assertEqual(self.sources.claude_calls, 2)

    def test_refresh_request_file_checks_now(self):
        from minitoo_dashboard.sources.claude import RefusedError
        self.sources.claude_error = RefusedError("usage-helper: http_429", 7200)
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct"))
        dash.tick(T)
        (self.home / "refresh-limits").touch()
        self.sources.claude_error = None
        dash.tick(T + 5)
        self.assertEqual(self.sources.claude_calls, 2)
        self.assertFalse((self.home / "refresh-limits").exists())
        state = store.read_json(self.home / "state.json")
        self.assertEqual((state["limits_checked_at"], state["limits_error"]), (T + 5, None))

    def test_codex_polled_every_five_seconds_when_on(self):
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", codex="on"))
        for t in range(0, 10):
            dash.tick(T + t)
        self.assertEqual(self.sources.codex_calls, 2)

    def test_codex_failure_logged_rarely_and_reported(self):
        dash = self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", codex="on"))
        self.sources.codex_fails = "disk full"
        with self.assertLogs("minitoo_dashboard", "INFO") as logs:
            for t in range(0, 600, 5):
                dash.tick(T + t)
            self.assertEqual(sum("Codex" in line for line in logs.output), 1)
            self.assertIn("disk full", store.read_json(self.home / "state.json")["codex_error"])
            dash.tick(T + 600)                       # still failing: one reminder per 10 min
            self.sources.codex_fails = "other"       # a different error is logged at once
            dash.tick(T + 605)
            self.sources.codex_fails = None
            dash.tick(T + 610)
        codex_lines = [line for line in logs.output if "Codex" in line]
        self.assertEqual(len(codex_lines), 4)
        self.assertIn("recovered", codex_lines[-1])
        self.assertIsNone(store.read_json(self.home / "state.json")["codex_error"])

    def test_codex_off_never_polls(self):
        self.make().tick(T)
        self.assertEqual(self.sources.codex_calls, 0)

    def test_no_device_configured(self):
        dash = self.make(cfg=Config())
        dash.tick(T)
        self.assertEqual(self.device.calls, [])
        self.assertIn("DEVICE_MAC", store.read_json(self.home / "state.json")["last_error"])

    def over(self, five=95.0, week=None, agent="claude"):
        record = {"captured_at": T, "five_hour": {"used_percentage": five, "resets_at": T + 3600}}
        if week is not None:
            record["seven_day"] = {"used_percentage": week, "resets_at": T + 86400}
        self.sources.caches = {agent: record}

    def test_limit_alert_shown_once_then_dashboard_returns(self):
        dash = self.make()
        dash.tick(T)
        self.over()
        dash.tick(T + 1)
        self.assertEqual(self.limit_frames, ["claude:five_hour"])
        self.assertEqual(len(self.sends()), 2)
        self.assertEqual(store.read_json(self.home / "state.json")["shown"], "limit_alert")
        dash.tick(T + 5)  # still showing the alert: no dashboard frame on top of it
        self.assertEqual(len(self.sends()), 2)
        dash.tick(T + 12)  # same model as before, but the screen shows the alert: resend
        self.assertEqual(len(self.sends()), 3)
        dash.tick(T + 600)
        self.assertEqual(self.limit_frames, ["claude:five_hour"])

    def test_limit_alert_time_counts_from_after_the_send(self):
        clock = [0.0]
        send = self.device.send_rawfile

        def slow_send(path, delay_ms):
            clock[0] += 3.0  # a slow Bluetooth transfer
            send(path, delay_ms)

        self.device.send_rawfile = slow_send
        self.over()
        dash = self.make(monotonic=lambda: clock[0])
        dash.tick(T)
        dash.tick(T + 12)
        self.assertEqual(len(self.sends()), 1)
        dash.tick(T + 14)
        self.assertEqual(len(self.sends()), 2)

    def test_limit_alerts_one_after_another(self):
        self.over(five=95.0, week=91.0)
        dash = self.make()
        dash.tick(T)
        dash.tick(T + 5)
        self.assertEqual(self.limit_frames, ["claude:five_hour"])
        dash.tick(T + 11)
        self.assertEqual(self.limit_frames, ["claude:five_hour", "claude:seven_day"])

    def test_limit_alert_not_repeated_after_restart(self):
        self.over()
        self.make().tick(T)
        self.make().tick(T + 60)
        self.assertEqual(self.limit_frames, ["claude:five_hour"])

    def test_limit_alert_off(self):
        self.over()
        self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", limit_alert=None)).tick(T)
        self.assertEqual(self.limit_frames, [])

    def test_limit_alert_uses_configured_threshold(self):
        self.over(five=85.0)
        self.make().tick(T)
        self.assertEqual(self.limit_frames, [])
        self.make(cfg=Config(device_mac="AA:BB:CC:DD:EE:FF", limit_alert=80)).tick(T + 1)
        self.assertEqual(self.limit_frames, ["claude:five_hour"])

    def test_limit_alert_waits_for_question_alert(self):
        dash = self.make()
        self.sources.state = "alerting"
        self.over()
        dash.tick(T)
        self.assertEqual((self.limit_frames, self.sends()), ([], [("clock", 988, 1)]))
        self.sources.state = "chilling"
        dash.tick(T + 1)
        self.assertEqual(self.limit_frames, ["claude:five_hour"])

    def test_question_alert_interrupts_limit_alert(self):
        self.over()
        dash = self.make()
        dash.tick(T)
        self.sources.state = "alerting"
        dash.tick(T + 2)
        self.assertEqual(self.sends()[-1], ("clock", 988, 1))
        self.sources.state = "chilling"
        dash.tick(T + 3)  # back to the dashboard at once, not the rest of the limit alert
        self.assertEqual(len(self.sends()), 3)
        self.assertEqual(self.limit_frames, ["claude:five_hour"])

    def test_limit_alert_waits_while_paused(self):
        (self.home / "paused").touch()
        self.over()
        dash = self.make()
        dash.tick(T)
        self.assertEqual(self.limit_frames, [])
        (self.home / "paused").unlink()
        dash.tick(T + 1)
        self.assertEqual(self.limit_frames, ["claude:five_hour"])

    def test_limit_alert_retried_after_device_error(self):
        self.over()
        dash = self.make()
        self.device.fail = True
        dash.tick(T)
        self.device.fail = False
        dash.tick(T + 31)
        self.assertEqual(self.limit_frames, ["claude:five_hour", "claude:five_hour"])
        dash.tick(T + 100)
        self.assertEqual(len(self.limit_frames), 2)

    def test_silent_device_detected_after_two_unanswered_sends(self):
        dash = self.make()
        self.device.replies = False
        dash.tick(T)
        dash.tick(T + 5)
        self.assertEqual(len(self.sends()), 1)
        dash.tick(T + 11)  # first send unanswered: probe again at once
        self.assertEqual(len(self.sends()), 2)
        self.assertEqual(self.notices, [])
        with self.assertLogs("minitoo_dashboard", "WARNING"):
            dash.tick(T + 22)
        self.assertEqual(len(self.notices), 1)
        self.assertIn(("stop",), self.device.calls)  # reconnect fresh on the next send
        self.assertEqual(store.read_json(self.home / "state.json")["device_silent_since"], T)
        dash.tick(T + 40)  # backing off
        self.assertEqual(len(self.sends()), 2)

    def test_wake_drops_a_reply_check_from_before_sleep(self):
        dash = self.make()
        self.device.replies = False
        dash.tick(T)
        dash.tick(T + 11)  # one unanswered
        dash.tick(T + 3600)  # asleep for an hour: start counting afresh
        self.assertEqual(self.notices, [])
        self.device.replies = True
        dash.tick(T + 3601)
        self.assertIsNone(dash.silent_since)

    def test_frequent_sends_do_not_hide_silence(self):
        dash = self.make()
        self.device.replies = False
        with self.assertLogs("minitoo_dashboard", "WARNING"):
            for i in range(25):
                self.sources.version = i  # a new frame every tick
                dash.tick(T + i)
        self.assertEqual(len(self.notices), 1)

    def test_silent_device_recovers_and_resends(self):
        dash = self.make()
        self.device.replies = False
        dash.tick(T)
        dash.tick(T + 11)
        with self.assertLogs("minitoo_dashboard", "WARNING"):
            dash.tick(T + 22)
        self.device.replies = True
        dash.tick(T + 52)  # backoff over: send again
        self.assertEqual(len(self.sends()), 3)
        with self.assertLogs("minitoo_dashboard", "INFO") as logs:
            dash.tick(T + 53)
        self.assertIn("responding again", logs.output[0])
        self.assertIsNone(store.read_json(self.home / "state.json")["device_silent_since"])

    def test_silence_notified_once_per_episode(self):
        dash = self.make()
        self.device.replies = False
        dash.tick(T)
        dash.tick(T + 11)
        with self.assertLogs("minitoo_dashboard", "WARNING"):
            dash.tick(T + 22)
            dash.tick(T + 52)
            dash.tick(T + 63)  # still silent after the backoff send: no second notice
        self.assertEqual(len(self.notices), 1)
        self.device.replies = True
        dash.tick(T + 200)
        dash.tick(T + 201)
        self.device.replies = False
        self.sources.version = 1
        dash.tick(T + 300)
        dash.tick(T + 311)
        with self.assertLogs("minitoo_dashboard", "WARNING"):
            dash.tick(T + 322)
        self.assertEqual(len(self.notices), 2)

    def test_state_file(self):
        dash = self.make()
        self.sources.state = "working"
        dash.tick(T)
        state = store.read_json(self.home / "state.json")
        self.assertEqual((state["status"], state["shown"], state["paused"]), ("working", "dashboard", False))


if __name__ == "__main__":
    unittest.main()
