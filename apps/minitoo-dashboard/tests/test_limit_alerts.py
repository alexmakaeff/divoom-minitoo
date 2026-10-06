import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import store
from minitoo_dashboard.limit_alerts import Crossing, LimitAlerts, Reset, due, due_resets

T = 1_790_600_000.0


def cache(five=None, week=None):
    record = {"captured_at": T}
    if five is not None:
        record["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
    if week is not None:
        record["seven_day"] = {"used_percentage": week[0], "resets_at": week[1]}
    return record


class DueTest(unittest.TestCase):
    def test_window_at_threshold_is_due(self):
        caches = {"claude": cache(five=(90.0, T + 3600))}
        self.assertEqual(due(caches, {}, 90, T), [Crossing("claude", "five_hour", 90.0, T + 3600)])

    def test_below_threshold_is_not_due(self):
        self.assertEqual(due({"claude": cache(five=(89.9, T + 3600))}, {}, 90, T), [])

    def test_window_already_reset_is_not_due(self):
        self.assertEqual(due({"claude": cache(five=(99.0, T))}, {}, 90, T), [])

    def test_fired_window_is_not_due_until_its_reset_passes(self):
        caches = {"claude": cache(five=(95.0, T + 3600))}
        fired = {"claude:five_hour": T + 3600}
        self.assertEqual(due(caches, fired, 90, T), [])
        # resets_at drifting by a few seconds is still the same window
        caches = {"claude": cache(five=(95.0, T + 3605))}
        self.assertEqual(due(caches, fired, 90, T + 100), [])

    def test_next_window_fires_again(self):
        fired = {"claude:five_hour": T}
        caches = {"claude": cache(five=(91.0, T + 5 * 3600))}
        self.assertEqual(len(due(caches, fired, 90, T + 60)), 1)

    def test_all_agents_and_windows_claude_first(self):
        caches = {"codex": cache(five=(92.0, T + 60)), "claude": cache(five=(50.0, T + 60), week=(97.0, T + 86400))}
        found = due(caches, {}, 90, T)
        self.assertEqual([(c.agent, c.window) for c in found], [("claude", "seven_day"), ("codex", "five_hour")])

    def test_garbage_caches_are_ignored(self):
        caches = {"claude": None, "codex": {"five_hour": {"used_percentage": "x", "resets_at": T + 60}},
                  "other": {"seven_day": "nope"}}
        self.assertEqual(due(caches, {}, 90, T), [])


class DueResetsTest(unittest.TestCase):
    def test_announced_window_past_its_reset_is_due(self):
        fired = {"claude:five_hour": T}
        self.assertEqual(due_resets({"claude": cache()}, fired, 90, T + 1), ([Reset("claude", "five_hour", T)], []))

    def test_window_not_reset_yet_is_not_due(self):
        self.assertEqual(due_resets({"claude": cache()}, {"claude:five_hour": T + 1}, 90, T), ([], []))

    def test_other_window_still_over_threshold_drops_it(self):
        fired = {"claude:five_hour": T}
        caches = {"claude": cache(five=(3.0, T + 18000), week=(92.0, T + 86400))}
        self.assertEqual(due_resets(caches, fired, 90, T + 1), ([], ["claude:five_hour"]))
        # and the other way round: the week reset while the current 5 hours are used up
        fired = {"claude:seven_day": T}
        caches = {"claude": cache(five=(97.0, T + 600), week=(1.0, T + 7 * 86400))}
        self.assertEqual(due_resets(caches, fired, 90, T + 1), ([], ["claude:seven_day"]))

    def test_other_window_already_reset_does_not_block(self):
        fired = {"claude:five_hour": T}
        caches = {"claude": cache(week=(99.0, T))}  # stale data: the week reset at T as well
        self.assertEqual(due_resets(caches, fired, 90, T + 1)[0], [Reset("claude", "five_hour", T)])

    def test_window_over_threshold_again_is_left_to_the_crossing(self):
        fired = {"claude:five_hour": T}
        caches = {"claude": cache(five=(93.0, T + 18000))}
        self.assertEqual(due_resets(caches, fired, 90, T + 3600), ([], []))

    def test_service_switched_off_or_unknown_key_is_dropped(self):
        fired = {"codex:five_hour": T, "other:five_hour": T, "claude:year": T}
        resets, dropped = due_resets({"claude": cache()}, fired, 90, T + 1)
        self.assertEqual((resets, sorted(dropped)), ([], sorted(fired)))

    def test_claude_first_then_codex_five_hour_first(self):
        fired = {"codex:five_hour": T, "claude:seven_day": T, "claude:five_hour": T}
        caches = {"claude": cache(), "codex": cache()}
        resets, _ = due_resets(caches, fired, 90, T + 1)
        self.assertEqual([r.key for r in resets], ["claude:five_hour", "claude:seven_day", "codex:five_hour"])


class LimitAlertsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cache" / "limit-alerts.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_marked_crossing_survives_restart(self):
        crossing = Crossing("codex", "seven_day", 93.0, T + 86400)
        LimitAlerts(self.path).mark(crossing, T)
        caches = {"codex": cache(week=(94.0, T + 86400))}
        self.assertEqual(LimitAlerts(self.path).due(caches, 90, T + 60), [])

    def test_reset_waits_until_announced_even_across_restarts(self):
        alerts = LimitAlerts(self.path)
        alerts.mark(Crossing("claude", "five_hour", 90.0, T + 60), T)
        alerts.mark(Crossing("codex", "five_hour", 90.0, T + 7200), T + 120)
        caches = {"claude": cache(), "codex": cache()}
        self.assertEqual(LimitAlerts(self.path).due_resets(caches, 90, T + 120),
                         [Reset("claude", "five_hour", T + 60)])
        alerts.done("claude:five_hour")
        self.assertEqual(store.read_json(self.path), {"codex:five_hour": T + 7200})

    def test_reset_that_cannot_be_used_is_dropped_for_good(self):
        alerts = LimitAlerts(self.path)
        alerts.mark(Crossing("claude", "five_hour", 95.0, T + 60), T)
        caches = {"claude": cache(week=(96.0, T + 86400))}
        self.assertEqual(alerts.due_resets(caches, 90, T + 61), [])
        self.assertEqual(store.read_json(self.path), {})

    def test_unreadable_file_starts_empty(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json")
        caches = {"claude": cache(five=(90.0, T + 60))}
        self.assertEqual(len(LimitAlerts(self.path).due(caches, 90, T)), 1)

    def test_unwritable_file_still_remembers_in_memory(self):
        self.path.parent.mkdir(parents=True)
        self.path.mkdir()  # a directory where the file should be: writes fail
        alerts = LimitAlerts(self.path)
        crossing = Crossing("claude", "five_hour", 90.0, T + 60)
        with self.assertLogs("minitoo_dashboard", "WARNING"):
            alerts.mark(crossing, T)
        self.assertEqual(alerts.due({"claude": cache(five=(91.0, T + 60))}, 90, T + 1), [])


if __name__ == "__main__":
    unittest.main()
