from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, List, Optional

from . import paths

DEFAULT_FIFO = Path(os.environ.get("DIVOOM_FIFO", "/tmp/divoom.fifo"))


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
                 run: Callable[..., Any] = subprocess.run):
        self.mac = mac
        self.dv = str(dv or paths.CORE_DIR / "dv")
        self.fifo = Path(fifo)
        self.run = run

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

    def send_rawfile(self, path: Path, delay_ms: int) -> None:
        if " " in str(path):
            raise ValueError("rawfile path must not contain spaces (dv splits FIFO lines on spaces)")
        self.ensure_daemon()
        self._call(["rawfile", str(path), str(delay_ms)], timeout=15)

    def select_clock(self, clock_id: int, device_id: int) -> None:
        self.ensure_daemon()
        self._call(["json", selector_json(clock_id, device_id)], timeout=15)

    def stop(self) -> None:
        try:
            self._call(["stop"], timeout=5)
        except DeviceError:
            pass
