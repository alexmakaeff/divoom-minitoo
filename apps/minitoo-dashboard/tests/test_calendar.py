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
            status, events, rstatus, reminders = calendar.fetch_events(Path("/x.app"), Path(tmp), run=self.fake_run(payload))
            self.assertEqual((status, len(events), rstatus, reminders), ("ok", 1, "unknown", []))

    def test_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = calendar.fetch_events(Path("/x.app"), Path(tmp), run=self.fake_run({"status": "denied"}))
            self.assertEqual(result[:2], ("denied", []))

    def test_no_output_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(calendar.fetch_events(Path("/x.app"), Path(tmp), run=self.fake_run(None)),
                             ("error", [], "error", []))


if __name__ == "__main__":
    unittest.main()


def rem(title, hour=None, minute=0, days=0, cal="Reminders"):
    """Timed reminder at hour:minute, or a date-only one (hour=None) due that day."""
    if hour is None:
        return calendar.Reminder(title, ts(0, days=days), True, cal)
    return calendar.Reminder(title, ts(hour, minute, days=days), False, cal)


class SelectItemTest(unittest.TestCase):
    def test_timed_reminder_competes_with_events_by_time(self):
        item = calendar.select_item([LATER], [rem("Bread", 21)], ts(20))
        self.assertEqual((item.kind, item.title, item.start), ("reminder", "Bread", ts(21)))

    def test_event_first_when_earlier_and_counts_reminders(self):
        item = calendar.select_item([ONGOING], [rem("Bread", 21), rem("Mom"), rem("Bill", 9, days=-1)], ts(20))
        self.assertEqual((item.kind, item.title, item.more), ("event", "Deep work", 3))

    def test_overdue_when_nothing_timed_ahead(self):
        reminders = [rem("Mom"), rem("Bill", 9, days=-2), rem("Tax", 10, days=-1)]
        item = calendar.select_item([PAST], reminders, ts(20))
        self.assertEqual((item.kind, item.title, item.more), ("overdue", "Bill", 2))

    def test_timed_reminder_becomes_overdue_after_its_time(self):
        item = calendar.select_item([], [rem("Bread", 19)], ts(19, 5))
        self.assertEqual((item.kind, item.title, item.more), ("overdue", "Bread", 0))

    def test_date_only_from_earlier_day_is_overdue(self):
        item = calendar.select_item([], [rem("Old", days=-1)], ts(20))
        self.assertEqual(item.kind, "overdue")

    def test_today_without_time_last(self):
        item = calendar.select_item([], [rem("Mom"), rem("Cat")], ts(20))
        self.assertEqual((item.kind, item.title, item.more), ("today", "Cat", 1))

    def test_future_reminders_ignored(self):
        self.assertIsNone(calendar.select_item([], [rem("Next", 9, days=1), rem("Later", days=2)], ts(20)))

    def test_list_filter_applies_to_reminders(self):
        item = calendar.select_item([], [rem("Work", cal="Work"), rem("Home", cal="Home")], ts(20), ["Home"])
        self.assertEqual((item.title, item.more), ("Home", 0))

    def test_events_only_behave_as_before(self):
        item = calendar.select_item([LATER, PAST, ONGOING], [], ts(20))
        self.assertEqual((item.kind, item.title, item.start, item.end, item.more),
                         ("event", "Deep work", ONGOING.start, ONGOING.end, 0))

    def test_parse_reminders_skips_malformed(self):
        raw = [{"title": "A", "due": 5, "all_day": True, "calendar": "R"}, {"title": "B"}, 7]
        self.assertEqual(calendar.parse_reminders(raw), [calendar.Reminder("A", 5.0, True, "R")])

    def test_fetch_returns_reminders(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"status": "ok", "events": [], "reminders_status": "ok",
                       "reminders": [{"title": "A", "due": 1, "all_day": False, "calendar": "R"}]}
            result = calendar.fetch_events(Path("/x.app"), Path(tmp), run=FetchEventsTest().fake_run(payload))
            self.assertEqual(result, ("ok", [], "ok", payload["reminders"]))
