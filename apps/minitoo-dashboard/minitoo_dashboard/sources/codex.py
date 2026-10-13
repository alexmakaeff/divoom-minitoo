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
# Choice cards (request_user_input_async in the app, request_user_input in plan mode) fire no hook.
# A card is open until the user's reply names its call_id, or the user starts or stops a turn.
QUESTION_TOOL = "request_user_input"
QUESTION_REPLY = "send_user_message_question_reply"
QUESTION_GONE = ("task_started", "turn_aborted")
LIMIT_KEYS = ("captured_at", "five_hour", "seven_day")
FULL_WALK_EVERY = 60  # a resumed old conversation is noticed within this; in between only hot files are checked
REWRITE_EVERY = 30  # keep checked_at well inside WORKING_TTL without rewriting the cache every 5 s

Question = Tuple[str, float]  # call_id, when it was asked
Result = Tuple[Optional[dict], Optional[bool], Optional[Question]]
Memo = Dict[str, Tuple[Tuple[float, int], Result, int]]  # path -> ((mtime, size), result, end of complete lines)


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


def lines_backwards(path: Path, floor: int = 0, limit: int = READ_LIMIT,
                    chunk: int = CHUNK) -> Iterator[Tuple[int, bytes]]:
    """Yields (offset, line), newest first, down to `floor` (a line start) or `limit` bytes."""
    with open(path, "rb") as fh:
        pos = fh.seek(0, os.SEEK_END)
        start = max(floor, pos - limit)
        tail: List[bytes] = []  # pieces of a line whose start is not read yet, in file order
        while pos > start:
            step = min(chunk, pos - start)
            pos -= step
            fh.seek(pos)
            data = fh.read(step)
            cut = data.rfind(b"\n")
            if cut < 0:
                tail.insert(0, data)
                continue
            line = data[cut + 1:] + b"".join(tail)
            if line.strip():
                yield pos + cut + 1, line
            lines = data[:cut].split(b"\n")
            offset = pos + cut
            for line in reversed(lines[1:]):
                offset -= len(line)
                if line.strip():
                    yield offset, line
                offset -= 1
            tail = [lines[0]]  # may continue in the previous chunk
        line = b"".join(tail)
        if start == floor and line.strip():
            yield start, line


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


def _last_byte(path: Path, size: int) -> bytes:
    if size == 0:
        return b""
    with open(path, "rb") as fh:
        fh.seek(size - 1)
        return fh.read(1)


def _replies(payload: dict) -> str:
    if payload.get("type") != "message" or payload.get("role") != "user":
        return ""
    content = payload.get("content")
    texts = [c.get("text") for c in content if isinstance(c, dict)] if isinstance(content, list) else []
    return "".join(t for t in texts if isinstance(t, str) and QUESTION_REPLY in t)


Scan = Tuple[Optional[dict], Optional[bool], Any, str, int]


def scan_file(path: Path, floor: int = 0) -> Scan:
    """After byte `floor`: newest limits record, turn state, open question, reply text and
    where the complete lines end. The question is None while undecided, False if none is open."""
    record: Optional[dict] = None
    working: Optional[bool] = None
    question: Any = None
    replies = ""  # replies newer than the line being read
    end = floor
    try:
        size = path.stat().st_size
        complete = _last_byte(path, size) == b"\n"
        end = max(floor, size) if complete else floor
        for offset, raw in lines_backwards(path, floor):
            if not complete:  # the last line is still being written: read it again next time
                end, complete = offset, True
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if obj.get("type") == "response_item":
                if question is None:
                    replies += _replies(payload)
                    call_id, name = payload.get("call_id"), payload.get("name")
                    if (kind == "function_call" and isinstance(name, str) and name.startswith(QUESTION_TOOL)
                            and isinstance(call_id, str) and call_id and call_id not in replies):
                        at = iso_epoch(obj.get("timestamp"))
                        question = (call_id, at) if at is not None else None
                continue
            if obj.get("type") != "event_msg":
                continue
            if question is None and kind in QUESTION_GONE:
                question = False
            if working is None and kind in TURN_EVENTS:
                working = TURN_EVENTS[kind]
            elif record is None and kind == "token_count":
                at = iso_epoch(obj.get("timestamp"))
                record = limits_record(payload.get("rate_limits"), at) if at is not None else None
            if record is not None and working is not None and question is not None:
                break
    except OSError:
        pass
    return record, working, question, replies, end


class Scanner:
    """Finds recent session files without stat-ing the whole history every time, and reads
    only what was appended since the last look, so a huge tool output cannot hide a turn."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.walked_at: Optional[float] = None
        self.hot: Set[Path] = set()
        self.memo: Memo = {}

    def _files(self, now: float) -> List[Tuple[Path, os.stat_result]]:
        if self.walked_at is None or not 0 <= now - self.walked_at < FULL_WALK_EVERY:
            self.walked_at = now
            candidates: Set[Path] = set(all_files(self.root))
        else:
            candidates = self.hot | set(day_files(self.root, now))
        found = recent(candidates, now)
        self.hot = {path for path, _ in found}
        return found

    def _result(self, path: Path, st: os.stat_result) -> Result:
        key, stamp = str(path), (st.st_mtime, st.st_size)
        prev = self.memo.get(key)
        if prev is not None and prev[0] == stamp:
            return prev[1]
        if prev is not None and st.st_size >= prev[2]:  # appended since the last look
            record, working, question, replies, end = scan_file(path, prev[2])
            old_record, old_working, old_question = prev[1]
            if question is None:
                question = old_question if old_question and old_question[0] not in replies else None
            result = (record or old_record, old_working if working is None else working, question or None)
        else:  # first look, or the file was truncated
            record, working, question, _, end = scan_file(path)
            result = (record, working, question or None)
        self.memo[key] = (stamp, result, end)
        return result

    def scan(self, now: float) -> Tuple[Optional[dict], bool, Optional[float]]:
        """Newest limits record, whether a turn runs, and when the newest open question was asked."""
        best: Optional[dict] = None
        working = False
        asking: Optional[float] = None
        seen = set()
        for path, st in self._files(now):
            seen.add(str(path))
            record, busy, question = self._result(path, st)
            working = working or bool(busy)
            if question and (asking is None or question[1] > asking):
                asking = question[1]
            if record is not None and (best is None or record["captured_at"] > best["captured_at"]):
                best = record
        for key in set(self.memo) - seen:
            del self.memo[key]
        return best, working, asking


def merge(old: Any, record: Optional[dict], working: bool, now: float,
          asking: Optional[float] = None) -> dict:
    old = old if isinstance(old, dict) else {}
    limits = {k: old[k] for k in LIMIT_KEYS if k in old}
    captured = limits.get("captured_at")
    if not isinstance(captured, (int, float)):  # hand-edited or corrupt cache: start over
        limits, captured = {}, 0
    if record is not None and record["captured_at"] > captured:
        limits = record
    flags = {"working": working, "asking": asking} if asking is not None else {"working": working}
    return dict(limits, **flags, checked_at=now)


def needs_write(old: Any, new: dict, now: float) -> bool:
    if not isinstance(old, dict):
        return True
    same = {k: v for k, v in old.items() if k != "checked_at"} == {k: v for k, v in new.items() if k != "checked_at"}
    return not same or now - float(old.get("checked_at", 0)) >= REWRITE_EVERY


def is_working(cache: Any, now: float) -> bool:
    return (isinstance(cache, dict) and cache.get("working") is True
            and now - float(cache.get("checked_at", 0)) <= WORKING_TTL)


def question_entries(cache: Any, now: float) -> List[Tuple[str, float, str]]:
    """An open Codex question as a session entry, so it alerts like a permission request."""
    if not isinstance(cache, dict) or now - float(cache.get("checked_at", 0)) > WORKING_TTL:
        return []
    asking = cache.get("asking")
    if not isinstance(asking, (int, float)) or isinstance(asking, bool):
        return []
    return [("alerting", float(asking), "codex")]
