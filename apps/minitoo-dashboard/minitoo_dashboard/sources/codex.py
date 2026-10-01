from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .claude import iso_epoch

ACTIVE_WITHIN = 1800  # same safety net as Claude session files
READ_LIMIT = 4 * 1024 * 1024
CHUNK = 64 * 1024
WORKING_TTL = 60  # the daemon rewrites the flag every 5 s; older means nobody is checking
WINDOWS = {300: "five_hour", 10080: "seven_day"}
TURN_EVENTS = {"task_started": True, "task_complete": False, "turn_aborted": False}
LIMIT_KEYS = ("captured_at", "five_hour", "seven_day")

Result = Tuple[Optional[dict], Optional[bool]]


def recent_files(root: Path, now: float, within: float = ACTIVE_WITHIN) -> List[Tuple[Path, os.stat_result]]:
    found = []
    for dirpath, _, names in os.walk(root):  # a missing root yields nothing
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            if now - st.st_mtime <= within:
                found.append((path, st))
    return found


def lines_backwards(path: Path, limit: int = READ_LIMIT, chunk: int = CHUNK) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        pos = fh.seek(0, os.SEEK_END)
        start = max(0, pos - limit)
        head = b""
        while pos > start:
            step = min(chunk, pos - start)
            pos -= step
            fh.seek(pos)
            parts = (fh.read(step) + head).split(b"\n")
            head = parts[0]  # may continue in the previous chunk
            for line in reversed(parts[1:]):
                if line.strip():
                    yield line
        if start == 0 and head.strip():
            yield head


def limits_record(rate_limits: Any, captured_at: float) -> Optional[dict]:
    if not isinstance(rate_limits, dict) or rate_limits.get("limit_id", "codex") != "codex":
        return None
    record: dict = {"captured_at": captured_at}
    for slot in ("primary", "secondary"):
        window = rate_limits.get(slot)
        if not isinstance(window, dict):
            continue
        key = WINDOWS.get(window.get("window_minutes"))
        pct, resets = window.get("used_percent"), window.get("resets_at")
        if key and isinstance(pct, (int, float)) and isinstance(resets, (int, float)):
            record[key] = {"used_percentage": float(pct), "resets_at": float(resets)}
    return record if len(record) > 1 else None


def scan_file(path: Path) -> Result:
    record: Optional[dict] = None
    working: Optional[bool] = None
    try:
        for raw in lines_backwards(path):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            payload = obj.get("payload")
            if obj.get("type") != "event_msg" or not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if working is None and kind in TURN_EVENTS:
                working = TURN_EVENTS[kind]
            elif record is None and kind == "token_count":
                at = iso_epoch(obj.get("timestamp"))
                record = limits_record(payload.get("rate_limits"), at) if at is not None else None
            if record is not None and working is not None:
                break
    except OSError:
        pass
    return record, working


def scan(root: Path, now: float, memo: Dict[str, Tuple[Tuple[float, int], Result]]) -> Tuple[Optional[dict], bool]:
    best: Optional[dict] = None
    working = False
    seen = set()
    for path, st in recent_files(root, now):
        key, stamp = str(path), (st.st_mtime, st.st_size)
        seen.add(key)
        if key not in memo or memo[key][0] != stamp:
            memo[key] = (stamp, scan_file(path))
        record, busy = memo[key][1]
        working = working or bool(busy)
        if record is not None and (best is None or record["captured_at"] > best["captured_at"]):
            best = record
    for key in set(memo) - seen:
        del memo[key]
    return best, working


def merge(old: Any, record: Optional[dict], working: bool, now: float) -> dict:
    old = old if isinstance(old, dict) else {}
    limits = {k: old[k] for k in LIMIT_KEYS if k in old}
    if record is not None and record["captured_at"] > float(limits.get("captured_at", 0)):
        limits = record
    return dict(limits, working=working, checked_at=now)


def is_working(cache: Any, now: float) -> bool:
    return (isinstance(cache, dict) and cache.get("working") is True
            and now - float(cache.get("checked_at", 0)) <= WORKING_TTL)
