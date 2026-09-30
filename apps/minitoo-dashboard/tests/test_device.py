import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import device


class FakeRun:
    def __init__(self, pgrep_rc=0, fail=None, timeout=None):
        self.calls, self.pgrep_rc, self.fail, self.timeout = [], pgrep_rc, fail, timeout

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(cmd, self.pgrep_rc, "", "")
        if self.timeout == cmd[1]:
            raise subprocess.TimeoutExpired(cmd, 1)
        rc = 1 if self.fail == cmd[1] else 0
        return subprocess.CompletedProcess(cmd, rc, "", "boom" if rc else "")


class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fifo = Path(self.tmp.name) / "divoom.fifo"

    def tearDown(self):
        self.tmp.cleanup()

    def dev(self, run):
        return device.Device("AA:BB:CC:DD:EE:FF", dv="/dv", fifo=self.fifo, run=run)

    def test_send_when_daemon_running(self):
        os.mkfifo(self.fifo)
        run = FakeRun()
        self.dev(run).send_rawfile(Path("/tmp/frame.raw"), 20)
        self.assertEqual([c for c in run.calls if c[0] == "/dv"], [["/dv", "rawfile", "/tmp/frame.raw", "20"]])

    def test_starts_daemon_when_missing(self):
        run = FakeRun()
        self.dev(run).send_rawfile(Path("/tmp/frame.raw"), 5)
        dv_calls = [c for c in run.calls if c[0] == "/dv"]
        self.assertEqual(dv_calls[0], ["/dv", "start", "AA:BB:CC:DD:EE:FF"])
        self.assertEqual(dv_calls[1][1], "rawfile")

    def test_stale_fifo_removed_before_start(self):
        os.mkfifo(self.fifo)
        run = FakeRun(pgrep_rc=1)
        self.dev(run).send_rawfile(Path("/tmp/frame.raw"), 5)
        self.assertFalse(self.fifo.exists())
        self.assertIn(["/dv", "start", "AA:BB:CC:DD:EE:FF"], run.calls)

    def test_start_failure_raises(self):
        with self.assertRaises(device.DeviceError):
            self.dev(FakeRun(fail="start")).send_rawfile(Path("/tmp/frame.raw"), 5)

    def test_timeout_raises(self):
        os.mkfifo(self.fifo)
        with self.assertRaises(device.DeviceError):
            self.dev(FakeRun(timeout="rawfile")).send_rawfile(Path("/tmp/frame.raw"), 5)

    def test_path_with_space_rejected(self):
        with self.assertRaises(ValueError):
            self.dev(FakeRun()).send_rawfile(Path("/tmp/my frame.raw"), 5)

    def test_select_clock(self):
        os.mkfifo(self.fifo)
        run = FakeRun()
        self.dev(run).select_clock(988, 123456789)
        cmd = [c for c in run.calls if c[0] == "/dv"][0]
        self.assertEqual(cmd[1], "json")
        body = json.loads(cmd[2])
        self.assertEqual((body["Command"], body["ClockId"], body["DeviceId"]),
                         ("Channel/SetClockSelectId", 988, 123456789))
        self.assertNotIn(" ", cmd[2])

    def test_stop_swallows_errors(self):
        self.dev(FakeRun(fail="stop")).stop()


if __name__ == "__main__":
    unittest.main()
