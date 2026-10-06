from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

STATES = ("chilling", "working", "alerting")
AGENTS = ("claude", "codex")
SESSION_TTL = 1800

Entry = Tuple[str, float, str]  # state, timestamp, agent


def aggregate(entries: List[Entry], now: float, ttl: float = SESSION_TTL) -> str:
    live = {state for state, ts, *_ in entries if state in STATES and now - ts <= ttl}
    if "alerting" in live:
        return "alerting"
    if "working" in live:
        return "working"
    return "chilling"


def alert_agent(entries: List[Entry], now: float, ttl: float = SESSION_TTL) -> Optional[str]:
    """Whose question is newest: that agent's alert face is shown."""
    alerts = [(ts, agent) for state, ts, agent in entries if state == "alerting" and now - ts <= ttl]
    return max(alerts)[1] if alerts else None


def read_sessions(directory: Path) -> List[Entry]:
    try:
        files = [f for f in Path(directory).iterdir() if f.is_file() and not f.name.startswith(".")]
    except OSError:
        return []
    entries: List[Entry] = []
    for f in files:
        try:
            state, ts, *agent = f.read_text(encoding="utf-8").split()
            entries.append((state, float(ts), agent[0] if agent and agent[0] in AGENTS else "claude"))
        except (OSError, ValueError):
            continue
    return entries
