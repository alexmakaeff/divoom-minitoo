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


@dataclass
class Reminder:
    title: str
    due: float       # timed: due moment; date-only: local midnight of the due day
    all_day: bool    # True when the reminder has a date but no time
    calendar: str    # Reminders list name


@dataclass
class Item:
    """What the event row shows: an event, a timed reminder, an overdue reminder
    or a date-only reminder for today, plus how many other open reminders exist."""
    kind: str        # event | reminder | overdue | today
    title: str
    start: float
    end: float
    more: int = 0


def parse_reminders(raw: Any) -> List[Reminder]:
    reminders = []
    for item in raw if isinstance(raw, list) else []:
        try:
            reminders.append(Reminder(str(item.get("title", "")), float(item["due"]),
                                      bool(item.get("all_day", False)), str(item.get("calendar", ""))))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return reminders


def _day_bounds(now: float) -> Tuple[float, float]:
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp(), (midnight + timedelta(days=1)).timestamp()


def select_event(events: List[Event], now: float, calendars: Optional[List[str]] = None) -> Optional[Event]:
    _, day_end = _day_bounds(now)
    candidates = [e for e in events
                  if e.end > now and e.start < day_end and (calendars is None or e.calendar in calendars)]
    return min(candidates, key=lambda e: e.start) if candidates else None


def select_item(events: List[Event], reminders: List[Reminder], now: float,
                calendars: Optional[List[str]] = None) -> Optional[Item]:
    """Priority: next timed item today (event or reminder), then the oldest overdue
    reminder, then a date-only reminder for today. `more` counts the other open
    reminders (timed today, overdue, date-only today)."""
    day_start, day_end = _day_bounds(now)
    open_reminders = [r for r in reminders if calendars is None or r.calendar in calendars]
    upcoming = [r for r in open_reminders if not r.all_day and now <= r.due < day_end]
    overdue = [r for r in open_reminders if (r.due < day_start if r.all_day else r.due < now)]
    today = [r for r in open_reminders if r.all_day and day_start <= r.due < day_end]
    total = len(upcoming) + len(overdue) + len(today)

    event = select_event(events, now, calendars)
    reminder = min(upcoming, key=lambda r: r.due) if upcoming else None
    if event is not None and (reminder is None or event.start <= reminder.due):
        return Item("event", event.title, event.start, event.end, total)
    if reminder is not None:
        return Item("reminder", reminder.title, reminder.due, reminder.due, total - 1)
    if overdue:
        oldest = min(overdue, key=lambda r: r.due)
        return Item("overdue", oldest.title, oldest.due, oldest.due, total - 1)
    if today:
        first = min(today, key=lambda r: r.title.lower())
        return Item("today", first.title, first.due, first.due, total - 1)
    return None


def fetch_events(app_path: Path, out_dir: Path, run: Callable[..., Any] = subprocess.run,
                 timeout: float = 20) -> Tuple[str, list, str, list]:
    out = Path(out_dir) / "calendar-helper.json"
    try:
        out.unlink()
    except FileNotFoundError:
        pass
    run(["open", "-n", "-W", "-g", str(app_path), "--args", "--out", str(out)],
        capture_output=True, text=True, timeout=timeout)
    data = store.read_json(out)
    if not isinstance(data, dict):
        return "error", [], "error", []
    return (str(data.get("status", "error")), list(data.get("events") or []),
            str(data.get("reminders_status", "unknown")), list(data.get("reminders") or []))
