import json
import os
import tempfile
import time
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
        record, working = codex.Scanner(self.root).scan(NOW)
        self.assertEqual(record, {"captured_at": NOW - 30,
                                  "five_hour": {"used_percentage": 58.0, "resets_at": NOW + 600},
                                  "seven_day": {"used_percentage": 21.0, "resets_at": NOW + 86400}})
        self.assertTrue(working)

    def test_complete_and_aborted_mean_idle(self):
        self.write("a.jsonl", [turn("task_started", NOW - 60), turn("task_complete", NOW - 50)])
        self.write("b.jsonl", [turn("task_started", NOW - 60), turn("turn_aborted", NOW - 50)])
        self.assertEqual(codex.Scanner(self.root).scan(NOW), (None, False))

    def test_any_working_file_wins(self):
        self.write("a.jsonl", [turn("task_complete", NOW - 50)])
        self.write("b.jsonl", [turn("task_started", NOW - 50)])
        self.assertTrue(codex.Scanner(self.root).scan(NOW)[1])

    def test_newest_record_by_event_time_not_file_name(self):
        self.write("2026/10/01/rollout-new-name.jsonl", [limits(NOW - 300, h5=10.0)])
        self.write("2026/09/23/rollout-old-name.jsonl", [limits(NOW - 20, h5=77.0)])
        self.assertEqual(codex.Scanner(self.root).scan(NOW)[0]["five_hour"]["used_percentage"], 77.0)

    def test_old_files_ignored(self):
        self.write("a.jsonl", [turn("task_started", NOW - 4000), limits(NOW - 4000)], mtime=NOW - 1801)
        self.assertEqual(codex.Scanner(self.root).scan(NOW), (None, False))

    def test_week_only_record(self):
        row = limits(NOW - 30)
        row["payload"]["rate_limits"]["primary"] = {"used_percent": 5.0, "window_minutes": 10080, "resets_at": NOW + 9}
        row["payload"]["rate_limits"]["secondary"] = None
        self.write("a.jsonl", [row])
        self.assertEqual(codex.Scanner(self.root).scan(NOW)[0],
                         {"captured_at": NOW - 30, "seven_day": {"used_percentage": 5.0, "resets_at": NOW + 9}})

    def test_premium_and_null_windows_skipped(self):
        premium = limits(NOW - 10, limit_id="premium")
        premium["payload"]["rate_limits"].update(primary=None, secondary=None)
        self.write("a.jsonl", [limits(NOW - 30, h5=40.0), premium])
        self.assertEqual(codex.Scanner(self.root).scan(NOW)[0]["five_hour"]["used_percentage"], 40.0)

    def test_half_written_and_garbage_lines_skipped(self):
        self.write("a.jsonl", [limits(NOW - 30)], tail='not json\n{"timestamp": "2026-10-01T12:0')
        self.assertEqual(codex.Scanner(self.root).scan(NOW)[0]["captured_at"], NOW - 30)

    def test_reads_long_file_from_the_end(self):
        filler = [{"timestamp": iso(NOW - 100), "type": "response_item", "payload": {"text": "x" * 1000}}] * 200
        self.write("a.jsonl", [limits(NOW - 500, h5=1.0)] + filler + [limits(NOW - 20, h5=99.0)] + filler)
        self.assertEqual(codex.Scanner(self.root).scan(NOW)[0]["five_hour"]["used_percentage"], 99.0)

    def test_read_limit(self):
        filler = [{"timestamp": iso(NOW - 100), "type": "response_item", "payload": {"text": "x" * 1000}}] * 5000
        self.write("a.jsonl", [limits(NOW - 500)] + filler)
        self.assertIsNone(codex.Scanner(self.root).scan(NOW)[0])

    def test_missing_root(self):
        self.assertEqual(codex.Scanner(self.root / "nope").scan(NOW), (None, False))

    def test_memo_skips_unchanged_files(self):
        path = self.write("a.jsonl", [limits(NOW - 30)])
        scanner = codex.Scanner(self.root)
        scanner.scan(NOW)
        scanner.memo[str(path)] = (scanner.memo[str(path)][0], ({"captured_at": 1.0, "five_hour": {}}, None))
        self.assertEqual(scanner.scan(NOW + 1)[0]["captured_at"], 1.0)

    def test_old_conversation_found_on_full_walk(self):
        scanner = codex.Scanner(self.root)
        scanner.scan(NOW)
        self.write("2026/08/01/rollout-old.jsonl", [turn("task_started", NOW + 10)], mtime=NOW + 10)
        self.assertFalse(scanner.scan(NOW + 10)[1])   # between full walks only hot files and today's dirs
        self.assertTrue(scanner.scan(NOW + codex.FULL_WALK_EVERY)[1])

    def test_new_file_in_todays_dir_found_at_once(self):
        scanner = codex.Scanner(self.root)
        scanner.scan(NOW)
        today = time.strftime("%Y/%m/%d", time.localtime(NOW + 10))
        self.write(f"{today}/rollout-new.jsonl", [turn("task_started", NOW + 10)], mtime=NOW + 10)
        self.assertTrue(scanner.scan(NOW + 10)[1])

    def test_hot_file_followed_between_walks(self):
        path = self.write("2026/08/01/rollout-old.jsonl", [turn("task_started", NOW - 10)])
        scanner = codex.Scanner(self.root)
        self.assertTrue(scanner.scan(NOW)[1])
        self.write("2026/08/01/rollout-old.jsonl", [turn("task_complete", NOW + 5)], mtime=NOW + 5)
        self.assertFalse(scanner.scan(NOW + 5)[1])

    def test_needs_write(self):
        old = {"captured_at": 1.0, "working": False, "checked_at": NOW}
        self.assertTrue(codex.needs_write(None, dict(old), NOW))
        self.assertFalse(codex.needs_write(old, dict(old, checked_at=NOW + 29), NOW + 29))
        self.assertTrue(codex.needs_write(old, dict(old, checked_at=NOW + 30), NOW + 30))
        self.assertTrue(codex.needs_write(old, dict(old, working=True, checked_at=NOW + 5), NOW + 5))
        self.assertTrue(codex.needs_write(old, dict(old, captured_at=2.0, checked_at=NOW + 5), NOW + 5))

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


if __name__ == "__main__":
    unittest.main()
