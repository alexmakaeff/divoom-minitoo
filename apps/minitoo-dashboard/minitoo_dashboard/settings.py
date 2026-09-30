"""Merge MiniToo dashboard hooks/status line into Claude Code settings.json."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional
from xml.sax.saxutils import escape

from . import store

HOOK_EVENTS = (("UserPromptSubmit", "working"), ("PreToolUse", "working"), ("Notification", "alerting"),
               ("Stop", "chilling"), ("SessionEnd", "end"))
CLAUDDY_MARK = "clauddy-hook.sh"


def _commands(group: dict) -> List[str]:
    return [h.get("command", "") for h in group.get("hooks", []) if isinstance(h, dict)]


def has_clauddy(s: dict) -> bool:
    return any(CLAUDDY_MARK in c for groups in s.get("hooks", {}).values() for g in groups for c in _commands(g))


def _remove(s: dict, predicate: Callable[[str], bool]) -> list:
    removed: list = []
    hooks = s.get("hooks", {})
    for event in list(hooks):
        kept_groups = []
        for group in hooks[event]:
            kept = []
            for entry in group.get("hooks", []):
                if isinstance(entry, dict) and predicate(entry.get("command", "")):
                    removed.append([event, entry])
                else:
                    kept.append(entry)
            if kept:
                kept_groups.append(dict(group, hooks=kept))
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if "hooks" in s and not s["hooks"]:
        del s["hooks"]
    return removed


def install_hooks(settings: dict, hook_path: str, replace_clauddy: bool = False):
    s = copy.deepcopy(settings)
    removed = _remove(s, lambda c: CLAUDDY_MARK in c) if replace_clauddy else []
    hooks = s.setdefault("hooks", {})
    for event, arg in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if any(hook_path in c for g in groups for c in _commands(g)):
            continue
        groups.append({"hooks": [{"type": "command", "command": f'"{hook_path}" {arg}'}]})
    return s, removed


def uninstall_hooks(settings: dict, hook_path: str) -> dict:
    s = copy.deepcopy(settings)
    _remove(s, lambda c: hook_path in c)
    return s


def restore_hooks(settings: dict, saved: list) -> dict:
    s = copy.deepcopy(settings)
    hooks = s.setdefault("hooks", {})
    for event, entry in saved:
        groups = hooks.setdefault(event, [])
        if entry.get("command") not in [c for g in groups for c in _commands(g)]:
            groups.append({"hooks": [entry]})
    if not hooks:
        del s["hooks"]
    return s


def statusline_state(settings: dict, path: str) -> str:
    line = settings.get("statusLine")
    if not line:
        return "absent"
    return "ours" if path in str(line.get("command", "")) else "other"


def install_statusline(settings: dict, path: str) -> dict:
    s = copy.deepcopy(settings)
    if statusline_state(s, path) == "absent":
        s["statusLine"] = {"type": "command", "command": f'"{path}"'}
    return s


def remove_statusline(settings: dict, path: str) -> dict:
    s = copy.deepcopy(settings)
    if statusline_state(s, path) == "ours":
        del s["statusLine"]
    return s


def render_template(src: Path, dst: Path, mapping: Dict[str, str], xml: bool = False) -> None:
    text = Path(src).read_text(encoding="utf-8")
    for key, value in mapping.items():
        text = text.replace(key, escape(value) if xml else value)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(text, encoding="utf-8")


def _load(path: Path) -> dict:
    data = store.read_json(path)
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="minitoo_dashboard.settings")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("has-clauddy")
    p.add_argument("--settings", type=Path, required=True)
    p = sub.add_parser("install-hooks")
    for arg in ("--settings", "--saved"):
        p.add_argument(arg, type=Path, required=True)
    p.add_argument("--hook", required=True)
    p.add_argument("--replace-clauddy", action="store_true")
    p = sub.add_parser("install-statusline")
    p.add_argument("--settings", type=Path, required=True)
    p.add_argument("--statusline", required=True)
    p = sub.add_parser("uninstall")
    for arg in ("--settings", "--saved"):
        p.add_argument(arg, type=Path, required=True)
    p.add_argument("--hook", required=True)
    p.add_argument("--statusline", required=True)
    p = sub.add_parser("render-template")
    p.add_argument("--src", type=Path, required=True)
    p.add_argument("--dst", type=Path, required=True)
    p.add_argument("--set", action="append", default=[])
    p.add_argument("--xml", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "has-clauddy":
        return 0 if has_clauddy(_load(args.settings)) else 1
    if args.command == "install-hooks":
        data, removed = install_hooks(_load(args.settings), args.hook, args.replace_clauddy)
        if removed:
            previous = store.read_json(args.saved) or []
            store.write_json_atomic(args.saved, previous + removed)
        _save(args.settings, data)
        return 0
    if args.command == "install-statusline":
        data = _load(args.settings)
        print(statusline_state(data, args.statusline))
        _save(args.settings, install_statusline(data, args.statusline))
        return 0
    if args.command == "uninstall":
        data = remove_statusline(uninstall_hooks(_load(args.settings), args.hook), args.statusline)
        saved = store.read_json(args.saved)
        if isinstance(saved, list):
            data = restore_hooks(data, saved)
            args.saved.unlink()
        _save(args.settings, data)
        return 0
    mapping = dict(item.split("=", 1) for item in args.set)
    render_template(args.src, args.dst, mapping, args.xml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
