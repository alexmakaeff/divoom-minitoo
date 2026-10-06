from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


@dataclass(frozen=True)
class Reset:
    agent: str
    window: str
    reset_at: float

    @property
    def key(self) -> str:
        return f"{self.agent}:{self.window}"


def _window(record: Any, window: str) -> Optional[Tuple[float, float]]:
    raw = record.get(window) if isinstance(record, dict) else None
    if not isinstance(raw, dict):
        return None
    pct, resets = raw.get("used_percentage"), raw.get("resets_at")
    if not isinstance(pct, (int, float)) or not isinstance(resets, (int, float)):
        return None
    return float(pct), float(resets)


def _over(record: Any, window: str, threshold: int, now: float) -> bool:
    current = _window(record, window)
    return current is not None and current[0] >= threshold and current[1] > now


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
            current = _window(record, window)
            if current is None:
                continue
            crossing = Crossing(agent, window, *current)
            if _over(record, window, threshold, now) and fired.get(crossing.key, 0.0) <= now:
                found.append(crossing)
    return found


def due_resets(caches: Dict[str, Any], fired: Dict[str, float], threshold: int,
               now: float) -> Tuple[List[Reset], List[str]]:
    """Announced windows that have reset: (to announce "go ahead", Claude first; keys to drop silently).

    A reset is not worth announcing while the service's other window is still over the threshold
    (5 hours back, but the week used up): that one is dropped. A window already over the threshold
    again is left alone; its new crossing replaces the record.
    """
    resets, dropped = [], []
    order = {(a, w): i for i, (a, w) in enumerate((a, w) for a in AGENTS for w in WINDOWS)}
    for key, reset_at in fired.items():
        if reset_at > now:
            continue
        agent, _, window = key.partition(":")
        if (agent, window) not in order or agent not in caches:  # unknown, or the service is off
            dropped.append(key)
            continue
        record = caches[agent]
        if _over(record, window, threshold, now):
            continue
        other = "seven_day" if window == "five_hour" else "five_hour"
        if _over(record, other, threshold, now):
            dropped.append(key)
        else:
            resets.append(Reset(agent, window, reset_at))
    resets.sort(key=lambda r: order[(r.agent, r.window)])
    return resets, dropped


class LimitAlerts:
    """Remembers announced windows in a file, so a restart at 92% does not announce again.

    A record outlives its reset time until the reset is announced (or dropped), so a reset that
    happens during a restart or while the Mac sleeps is still announced afterwards.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        loaded = store.read_json(self.path)
        self.fired: Dict[str, float] = {k: float(v) for k, v in loaded.items()
                                        if isinstance(v, (int, float))} if isinstance(loaded, dict) else {}

    def due(self, caches: Dict[str, Any], threshold: int, now: float) -> List[Crossing]:
        return due(caches, self.fired, threshold, now)

    def due_resets(self, caches: Dict[str, Any], threshold: int, now: float) -> List[Reset]:
        resets, dropped = due_resets(caches, self.fired, threshold, now)
        for key in dropped:
            log.info("limit reset not announced: %s (still blocked by the other window, or off)", key)
            self.fired.pop(key, None)
        if dropped:
            self._save()
        return resets

    def mark(self, crossing: Crossing, now: float) -> None:
        self.fired[crossing.key] = crossing.resets_at
        self._save()

    def done(self, key: str) -> None:
        self.fired.pop(key, None)
        self._save()

    def _save(self) -> None:
        try:
            store.write_json_atomic(self.path, self.fired)
        except OSError as exc:  # remembered in memory still; only a restart could repeat it
            log.warning("cannot write %s: %s", self.path.name, exc)
