from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from .. import paths, store

HELPER_APP = paths.APP_DIR / "calendar-helper" / "calendar-helper.app"


@dataclass
class Event:
    title: str
    start: float
    end: float
    calendar: str


def parse_events(raw: Any) -> List[Event]:
    events = []
    for item in raw if isinstance(raw, list) else []:
        try:
            events.append(Event(str(item.get("title", "")), float(item["start"]), float(item["end"]),
                                str(item.get("calendar", ""))))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return events


def select_event(events: List[Event], now: float, calendars: Optional[List[str]] = None) -> Optional[Event]:
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = (midnight + timedelta(days=1)).timestamp()
    candidates = [e for e in events
                  if e.end > now and e.start < day_end and (calendars is None or e.calendar in calendars)]
    return min(candidates, key=lambda e: e.start) if candidates else None


def fetch_events(app_path: Path, out_dir: Path, run: Callable[..., Any] = subprocess.run,
                 timeout: float = 20) -> Tuple[str, list]:
    out = Path(out_dir) / "calendar-helper.json"
    try:
        out.unlink()
    except FileNotFoundError:
        pass
    run(["open", "-n", "-W", "-g", str(app_path), "--args", "--out", str(out)],
        capture_output=True, text=True, timeout=timeout)
    data = store.read_json(out)
    if not isinstance(data, dict):
        return "error", []
    return str(data.get("status", "error")), list(data.get("events") or [])
