from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from . import store
from .sources.claude import WINDOWS

log = logging.getLogger("minitoo_dashboard")

AGENTS = ("claude", "codex")


@dataclass(frozen=True)
class Crossing:
    agent: str  # "claude" | "codex"
    window: str  # "five_hour" | "seven_day"
    pct: float
    resets_at: float

    @property
    def key(self) -> str:
        return f"{self.agent}:{self.window}"


def due(caches: Dict[str, Any], fired: Dict[str, float], threshold: int, now: float) -> List[Crossing]:
    """Windows at or over the threshold that have not been announced yet, Claude first.

    A window counts as announced until the reset time stored when it was announced has passed, so
    a resets_at that drifts by a few seconds between fetches never announces the same window twice.
    """
    found = []
    for agent in AGENTS:
        record = caches.get(agent)
        if not isinstance(record, dict):
            continue
        for window in WINDOWS:
            raw = record.get(window)
            if not isinstance(raw, dict):
                continue
            pct, resets = raw.get("used_percentage"), raw.get("resets_at")
            if not isinstance(pct, (int, float)) or not isinstance(resets, (int, float)):
                continue
            crossing = Crossing(agent, window, float(pct), float(resets))
            if pct >= threshold and resets > now and fired.get(crossing.key, 0.0) <= now:
                found.append(crossing)
    return found


class LimitAlerts:
    """Remembers announced windows in a file, so a restart at 92% does not announce again."""

    def __init__(self, path: Path):
        self.path = Path(path)
        loaded = store.read_json(self.path)
        self.fired: Dict[str, float] = {k: float(v) for k, v in loaded.items()
                                        if isinstance(v, (int, float))} if isinstance(loaded, dict) else {}

    def due(self, caches: Dict[str, Any], threshold: int, now: float) -> List[Crossing]:
        return due(caches, self.fired, threshold, now)

    def mark(self, crossing: Crossing, now: float) -> None:
        self.fired = {k: v for k, v in self.fired.items() if v > now}
        self.fired[crossing.key] = crossing.resets_at
        try:
            store.write_json_atomic(self.path, self.fired)
        except OSError as exc:  # remembered in memory still; only a restart could repeat it
            log.warning("cannot write %s: %s", self.path.name, exc)
