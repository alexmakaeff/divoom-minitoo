from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from .claude import iso_epoch

ACTIVE_WITHIN = 1800  # same safety net as Claude session files
READ_LIMIT = 4 * 1024 * 1024
CHUNK = 64 * 1024
WORKING_TTL = 60  # the daemon rewrites the flag every 5 s; older means nobody is checking
WINDOWS = {300: "five_hour", 10080: "seven_day"}
TURN_EVENTS = {"task_started": True, "task_complete": False, "turn_aborted": False}
LIMIT_KEYS = ("captured_at", "five_hour", "seven_day")
FULL_WALK_EVERY = 60  # a resumed old conversation is noticed within this; in between only hot files are checked
REWRITE_EVERY = 30  # keep checked_at well inside WORKING_TTL without rewriting the cache every 5 s

Result = Tuple[Optional[dict], Optional[bool]]


def all_files(root: Path) -> Iterator[Path]:
    for dirpath, _, names in os.walk(root):  # a missing root yields nothing
        for name in names:
            if name.endswith(".jsonl"):
                yield Path(dirpath) / name


def day_files(root: Path, now: float) -> Iterator[Path]:
    """New conversations land in the directory of the local date they start on."""
    for ts in (now, now - 86400):
        try:
            yield from (root / time.strftime("%Y/%m/%d", time.localtime(ts))).glob("*.jsonl")
        except OSError:
            continue


def recent(paths: Iterable[Path], now: float, within: float = ACTIVE_WITHIN) -> List[Tuple[Path, os.stat_result]]:
    found = []
    for path in paths:
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


class Scanner:
    """Finds recent session files without stat-ing the whole history every time."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.walked_at: Optional[float] = None
        self.hot: Set[Path] = set()
        self.memo: Dict[str, Tuple[Tuple[float, int], Result]] = {}

    def _files(self, now: float) -> List[Tuple[Path, os.stat_result]]:
        if self.walked_at is None or not 0 <= now - self.walked_at < FULL_WALK_EVERY:
            self.walked_at = now
            candidates: Set[Path] = set(all_files(self.root))
        else:
            candidates = self.hot | set(day_files(self.root, now))
        found = recent(candidates, now)
        self.hot = {path for path, _ in found}
        return found

    def scan(self, now: float) -> Tuple[Optional[dict], bool]:
        best: Optional[dict] = None
        working = False
        seen = set()
        for path, st in self._files(now):
            key, stamp = str(path), (st.st_mtime, st.st_size)
            seen.add(key)
            if key not in self.memo or self.memo[key][0] != stamp:
                self.memo[key] = (stamp, scan_file(path))
            record, busy = self.memo[key][1]
            working = working or bool(busy)
            if record is not None and (best is None or record["captured_at"] > best["captured_at"]):
                best = record
        for key in set(self.memo) - seen:
            del self.memo[key]
        return best, working


def merge(old: Any, record: Optional[dict], working: bool, now: float) -> dict:
    old = old if isinstance(old, dict) else {}
    limits = {k: old[k] for k in LIMIT_KEYS if k in old}
    if record is not None and record["captured_at"] > float(limits.get("captured_at", 0)):
        limits = record
    return dict(limits, working=working, checked_at=now)


def needs_write(old: Any, new: dict, now: float) -> bool:
    if not isinstance(old, dict):
        return True
    same = {k: v for k, v in old.items() if k != "checked_at"} == {k: v for k, v in new.items() if k != "checked_at"}
    return not same or now - float(old.get("checked_at", 0)) >= REWRITE_EVERY


def is_working(cache: Any, now: float) -> bool:
    return (isinstance(cache, dict) and cache.get("working") is True
            and now - float(cache.get("checked_at", 0)) <= WORKING_TTL)
