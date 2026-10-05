from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, List, Optional

from . import paths

DEFAULT_FIFO = Path(os.environ.get("DIVOOM_FIFO", "/tmp/divoom.fifo"))
DEFAULT_LOG = Path("/tmp/divoom-send.log")  # core/dv: LOG, truncated by every `dv start`


class DeviceError(Exception):
    pass


def selector_json(clock_id: int, device_id: int) -> str:
    # Same selector Clauddy sends (apps/clauddy/lib/clauddy-lib.sh). No spaces:
    # dv passes the FIFO line through a space tokenizer.
    return ('{"Command":"Channel/SetClockSelectId","ClockId":%d,"DeviceId":%d,"ParentClockId":0,'
            '"ParentItemId":"","PageIndex":0,"LcdIndependence":0,"LcdIndex":0,"Language":"en"}'
            % (clock_id, device_id))


class Device:
    def __init__(self, mac: str, dv: Optional[str] = None, fifo: Path = DEFAULT_FIFO,
                 run: Callable[..., Any] = subprocess.run, log: Path = DEFAULT_LOG):
        self.mac = mac
        self.dv = str(dv or paths.CORE_DIR / "dv")
        self.fifo = Path(fifo)
        self.run = run
        self.log = Path(log)
        self._closures_pos: Optional[int] = None  # how far closures() has read the dv log
        self._uploading = False  # an upload started and the MiniToo has not confirmed it yet

    def _call(self, args: List[str], timeout: float) -> None:
        try:
            result = self.run([self.dv, *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise DeviceError(f"dv {args[0]} timed out")
        except OSError as exc:
            raise DeviceError(f"dv {args[0]}: {exc}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-300:]
            raise DeviceError(f"dv {args[0]} failed: {detail}")

    def daemon_running(self) -> bool:
        try:
            if not stat.S_ISFIFO(self.fifo.stat().st_mode):
                return False
        except OSError:
            return False
        result = self.run(["pgrep", "-f", "divoom-send"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0

    def ensure_daemon(self) -> None:
        if self.daemon_running():
            return
        try:
            self.fifo.unlink()  # a stale FIFO makes `dv start` refuse to run
        except FileNotFoundError:
            pass
        self._call(["start", self.mac], timeout=20)

    def send_rawfile(self, path: Path, delay_ms: int) -> int:
        """Send; returns the log mark to pass to replied_since() (dv acks at the FIFO, not the device)."""
        if " " in str(path):
            raise ValueError("rawfile path must not contain spaces (dv splits FIFO lines on spaces)")
        self.ensure_daemon()
        mark = self.log_mark()
        self._call(["rawfile", str(path), str(delay_ms)], timeout=15)
        return mark

    def log_mark(self) -> int:
        try:
            return self.log.stat().st_size
        except OSError:
            return 0

    def replied_since(self, mark: int) -> bool:
        """Has the MiniToo sent anything since mark? It acks each upload and broadcasts every second."""
        try:
            with open(self.log, "rb") as fh:
                if fh.seek(0, 2) >= mark:  # smaller: `dv start` reconnected and truncated the log
                    fh.seek(mark)
                else:
                    fh.seek(0)
                return b"rx[" in fh.read()
        except OSError:
            return False

    def closures(self) -> List[bool]:
        """Bluetooth closures logged since the last call, each True if it cut an unconfirmed upload.

        A MiniToo that restarts while handling a frame acks the transfer (`8b 55`), never confirms
        it (`bd 55 13`) and drops the channel. The first call only finds the end of the log.
        """
        try:
            with open(self.log, "rb") as fh:
                size = fh.seek(0, 2)
                if self._closures_pos is None:
                    self._closures_pos = size
                    return []
                if size < self._closures_pos:  # `dv start` truncated the log: a new connection
                    self._closures_pos, self._uploading = 0, False
                fh.seek(self._closures_pos)
                data = fh.read(size - self._closures_pos)
        except OSError:
            return []
        data = data[:data.rfind(b"\n") + 1]  # a half-written line waits for the next call
        self._closures_pos += len(data)
        found = []
        for line in data.splitlines():
            if line.startswith(b"sendFrames: count"):
                self._uploading = True
            elif b"bd 55 13" in line:
                self._uploading = False
            elif b"channel closed" in line:
                found.append(self._uploading)
                self._uploading = False
        return found

    def select_clock(self, clock_id: int, device_id: int) -> None:
        self.ensure_daemon()
        self._call(["json", selector_json(clock_id, device_id)], timeout=15)

    def stop(self) -> None:
        try:
            self._call(["stop"], timeout=5)
        except DeviceError:
            pass
