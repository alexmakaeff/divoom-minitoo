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

    def test_alert_agent_is_the_newest_alert(self):
        entries = [("alerting", NOW - 20, "claude"), ("alerting", NOW - 5, "codex"), ("working", NOW, "claude")]
        self.assertEqual(status.alert_agent(entries, NOW), "codex")
        entries.append(("alerting", NOW - 1, "claude"))
        self.assertEqual(status.alert_agent(entries, NOW), "claude")

    def test_alert_agent_none_without_live_alert(self):
        self.assertIsNone(status.alert_agent([("alerting", NOW - 1801, "codex"), ("working", NOW, "claude")], NOW))

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
            (d / "x").write_text("alerting 789 codex\n")
            self.assertEqual(sorted(status.read_sessions(d)),
                             [("alerting", 456.0, "claude"), ("alerting", 789.0, "codex"), ("working", 123.0, "claude")])

    def test_missing_dir(self):
        self.assertEqual(status.read_sessions(Path("/nonexistent/sessions")), [])


class HookScriptTest(unittest.TestCase):
    def run_hook(self, home, state, payload, *extra):
        env = dict(os.environ, MINITOO_DASHBOARD_HOME=home)
        return subprocess.run(["bash", str(HOOK), state, *extra], input=payload, text=True,
                              env=env, capture_output=True, timeout=5)

    def test_writes_session_state(self):
        with tempfile.TemporaryDirectory() as home:
            r = self.run_hook(home, "chilling", '{"session_id":"abc-123","hook_event_name":"Stop"}')
            self.assertEqual(r.returncode, 0)
            content = (Path(home) / "sessions" / "abc-123").read_text()
            self.assertTrue(content.startswith("chilling "))

    def test_agent_argument_is_recorded(self):
        with tempfile.TemporaryDirectory() as home:
            self.run_hook(home, "alerting", '{"session_id":"c1"}', "codex")
            state, _, agent = (Path(home) / "sessions" / "c1").read_text().split()
            self.assertEqual((state, agent), ("alerting", "codex"))
            self.run_hook(home, "chilling", '{"session_id":"c2"}', "bogus agent")
            self.assertEqual(len((Path(home) / "sessions" / "c2").read_text().split()), 2)

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
