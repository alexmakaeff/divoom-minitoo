from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

STALE_AFTER = 600
WINDOWS = ("five_hour", "seven_day")


@dataclass
class Window:
    pct: Optional[float]
    reset_in: Optional[float]
    is_reset: bool


@dataclass
class LimitsView:
    five: Window
    week: Window
    as_of: Optional[float]
    has_data: bool


def extract(data: Any, now: float) -> Optional[dict]:
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        return None
    record: dict = {"captured_at": now}
    for key in WINDOWS:
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        pct, resets = window.get("used_percentage"), window.get("resets_at")
        if isinstance(pct, (int, float)) and isinstance(resets, (int, float)):
            record[key] = {"used_percentage": float(pct), "resets_at": float(resets)}
    return record if len(record) > 1 else None


def _window(raw: Any, now: float) -> Window:
    if not isinstance(raw, dict):
        return Window(None, None, False)
    if raw["resets_at"] <= now:
        return Window(None, None, True)
    return Window(raw["used_percentage"], raw["resets_at"] - now, False)


def limits_view(cache: Any, now: float) -> LimitsView:
    empty = Window(None, None, False)
    if not isinstance(cache, dict) or "captured_at" not in cache:
        return LimitsView(empty, empty, None, False)
    captured = float(cache["captured_at"])
    as_of = captured if now - captured > STALE_AFTER else None
    return LimitsView(_window(cache.get("five_hour"), now), _window(cache.get("seven_day"), now), as_of, True)


class DirectError(Exception):
    pass


class KeychainAccessError(DirectError):
    """usage-helper may not read the Claude Code login; only the owner can grant it."""


ACCESS_STATUSES = ("needs_access", "keychain_denied")
GRANT_HINT = "run 'minitoo-dashboard grant-keychain' and choose Always Allow"


def iso_epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_direct(data: Any, now: float) -> dict:
    """Turn usage-helper output into the same cache record the status line writes."""
    if not isinstance(data, dict):
        raise DirectError("usage-helper returned no JSON object")
    if data.get("status") in ACCESS_STATUSES:
        raise KeychainAccessError(f"usage-helper: {data.get('detail') or 'no Keychain access'}; {GRANT_HINT}")
    if data.get("status") != "ok":
        raise DirectError(f"usage-helper: {data.get('status', 'error')} {data.get('detail', '')}".strip())
    record: dict = {"captured_at": now, "source": "direct"}
    for key in WINDOWS:
        window = data.get(key)
        if not isinstance(window, dict):
            continue
        pct, resets = window.get("utilization"), iso_epoch(window.get("resets_at"))
        if isinstance(pct, (int, float)) and resets is not None:
            record[key] = {"used_percentage": float(pct), "resets_at": resets}
    if not any(key in record for key in WINDOWS) and not any(isinstance(data.get(k), dict) for k in WINDOWS):
        raise DirectError("usage response has no five_hour/seven_day windows (format changed?)")
    return record


def fetch_direct(helper: Path, now: float, run: Callable[..., Any] = subprocess.run, timeout: float = 30,
                 interactive: bool = False) -> dict:
    """Run usage-helper. Only `interactive` lets macOS show the Keychain prompt; the daemon never does."""
    cmd = [str(helper), "--interactive"] if interactive else [str(helper)]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DirectError(f"usage-helper did not run: {exc}")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise DirectError(f"usage-helper exited {result.returncode} without JSON")
    return parse_direct(data, now)
