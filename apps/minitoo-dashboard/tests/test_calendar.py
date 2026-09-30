import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from minitoo_dashboard.sources import calendar

DAY = datetime(2026, 9, 29)


def ts(hour, minute=0, days=0):
    return (DAY + timedelta(days=days, hours=hour, minutes=minute)).timestamp()


PAST = calendar.Event("Standup", ts(9), ts(9, 15), "Work")
ONGOING = calendar.Event("Deep work", ts(19, 30), ts(21), "Work")
LATER = calendar.Event("Call", ts(22), ts(22, 30), "Home")
TOMORROW = calendar.Event("Breakfast", ts(9, days=1), ts(10, days=1), "Home")


class SelectEventTest(unittest.TestCase):
    def test_in_progress_first(self):
        self.assertEqual(calendar.select_event([LATER, PAST, ONGOING, TOMORROW], ts(20)), ONGOING)

    def test_next_upcoming(self):
        self.assertEqual(calendar.select_event([PAST, LATER, TOMORROW], ts(20)), LATER)

    def test_tomorrow_hidden_until_midnight(self):
        self.assertIsNone(calendar.select_event([PAST, TOMORROW], ts(23)))
        self.assertEqual(calendar.select_event([PAST, TOMORROW], ts(0, 5, days=1)), TOMORROW)

    def test_overnight_event_in_progress_after_midnight(self):
        overnight = calendar.Event("Deploy", ts(23), ts(1, days=1), "Work")
        self.assertEqual(calendar.select_event([overnight], ts(0, 30, days=1)), overnight)

    def test_calendar_filter(self):
        self.assertEqual(calendar.select_event([ONGOING, LATER], ts(20), ["Home"]), LATER)

    def test_parse_events_skips_malformed(self):
        raw = [{"title": "A", "start": 1, "end": 2, "calendar": "W"}, {"title": "B"}, "junk"]
        self.assertEqual(calendar.parse_events(raw), [calendar.Event("A", 1.0, 2.0, "W")])


class FetchEventsTest(unittest.TestCase):
    def fake_run(self, payload):
        def run(cmd, **kwargs):
            out = Path(cmd[cmd.index("--out") + 1])
            if payload is not None:
                out.write_text(json.dumps(payload))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return run

    def test_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"status": "ok", "events": [{"title": "A", "start": 1, "end": 2, "calendar": "W"}]}
            status, events = calendar.fetch_events(Path("/x.app"), Path(tmp), run=self.fake_run(payload))
            self.assertEqual((status, len(events)), ("ok", 1))

    def test_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, events = calendar.fetch_events(Path("/x.app"), Path(tmp), run=self.fake_run({"status": "denied"}))
            self.assertEqual((status, events), ("denied", []))

    def test_no_output_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(calendar.fetch_events(Path("/x.app"), Path(tmp), run=self.fake_run(None)), ("error", []))


if __name__ == "__main__":
    unittest.main()
