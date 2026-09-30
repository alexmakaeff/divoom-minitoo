from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

STATES = ("chilling", "working", "alerting")
SESSION_TTL = 1800


def aggregate(entries: List[Tuple[str, float]], now: float, ttl: float = SESSION_TTL) -> str:
    live = {state for state, ts in entries if state in STATES and now - ts <= ttl}
    if "alerting" in live:
        return "alerting"
    if "working" in live:
        return "working"
    return "chilling"


def read_sessions(directory: Path) -> List[Tuple[str, float]]:
    try:
        files = [f for f in Path(directory).iterdir() if f.is_file() and not f.name.startswith(".")]
    except OSError:
        return []
    entries: List[Tuple[str, float]] = []
    for f in files:
        try:
            state, ts = f.read_text(encoding="utf-8").split()
            entries.append((state, float(ts)))
        except (OSError, ValueError):
            continue
    return entries
