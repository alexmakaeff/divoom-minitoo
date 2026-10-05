from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

from PIL import Image

from . import config, paths, render, store
from .collect import Sources
from .collect import USAGE_HELPER
from .limit_alerts import Crossing
from .sources import claude, codex, weather


def _ago(ts: Optional[float], now: float) -> str:
    if not ts:
        return "never"
    seconds = int(now - ts)
    return f"{seconds}s ago" if seconds < 120 else f"{seconds // 60} min ago"


def cmd_init(args) -> int:
    cfg = config.load_config()
    if args.mac:
        cfg.device_mac = args.mac.replace("-", ":").upper()
    if args.lang:
        cfg.lang = args.lang
    if args.temp_unit:
        cfg.temp_unit = args.temp_unit
    if args.claude_limits:
        cfg.claude_limits = args.claude_limits
    if args.codex:
        cfg.codex = args.codex
    config.save_config(cfg)
    print(f"Config written: {paths.config_path()}")
    return 0


def cmd_city(args, geocode: Callable) -> int:
    cfg = config.load_config()
    name = " ".join(args.name)
    try:
        places = geocode(name, cfg.lang)
    except OSError as exc:
        print(f"Could not reach the geocoding service: {exc}", file=sys.stderr)
        return 1
    if not places:
        print(f"No city found for '{name}'.", file=sys.stderr)
        return 1
    if len(places) == 1:
        chosen = places[0]
    elif args.pick:
        if not 1 <= args.pick <= len(places):
            print(f"--pick must be 1..{len(places)}", file=sys.stderr)
            return 2
        chosen = places[args.pick - 1]
    else:
        for i, place in enumerate(places, 1):
            print(f"{i}. {place.label()}")
        if not sys.stdin.isatty():
            print(f"Several places match. Re-run with --pick N, e.g.: minitoo-dashboard city \"{name}\" --pick 1")
            return 2
        answer = input(f"Choose 1-{len(places)}: ").strip()
        if not answer.isdigit() or not 1 <= int(answer) <= len(places):
            print("No city selected.", file=sys.stderr)
            return 2
        chosen = places[int(answer) - 1]
    cfg.city_name, cfg.city_lat, cfg.city_lon = chosen.label(), chosen.lat, chosen.lon
    config.save_config(cfg)
    print(f"Weather city set to {chosen.label()} ✓")
    return 0


def cmd_status(args) -> int:
    now = time.time()
    cfg = config.load_config()
    state = store.read_json(paths.state_path()) or {}
    alive = bool(state) and now - state.get("updated_at", 0) < 90
    cache = paths.cache_dir()
    print(f"Daemon:        {'running (' + _ago(state.get('updated_at'), now) + ')' if alive else 'not running'}")
    print(f"Paused:        {'yes' if paths.paused_path().exists() else 'no'}")
    print(f"Device:        {cfg.device_mac or 'not configured'}")
    print(f"Claude status: {state.get('status', '?')}   screen: {state.get('shown', '?')}")
    print(f"Last sent:     {_ago(state.get('last_sent_at'), now)}")
    if state.get("last_error"):
        print(f"Last error:    {state['last_error']}")
    w = store.read_json(cache / "weather.json")
    print(f"Weather:       {cfg.city_name or 'disabled'}"
          + (f" (updated {_ago(w.get('fetched_at'), now)})" if cfg.has_city and isinstance(w, dict) else ""))
    cal = store.read_json(cache / "calendar.json") or {}
    cal_status = cal.get("status", "unknown")
    hint = "  → allow calendar-helper in System Settings > Privacy & Security > Calendars" \
        if cal_status in ("denied", "not_determined") else ""
    print(f"Calendar:      {cal_status}{hint}")
    rem_status = cal.get("reminders_status", "unknown")
    rem_hint = "  → allow calendar-helper in System Settings > Privacy & Security > Reminders" \
        if rem_status in ("denied", "not_determined") else ""
    print(f"Reminders:     {rem_status}{rem_hint}")
    c = store.read_json(cache / "claude.json")
    captured = f"captured {_ago(c.get('captured_at'), now)} via {c.get('source', 'statusline')}" \
        if isinstance(c, dict) else "no data yet"
    print(f"Claude limits: {captured}   (source setting: {cfg.claude_limits})")
    error_at = state.get("limits_error_at") or 0
    fixed_since = isinstance(c, dict) and (c.get("captured_at") or 0) > error_at
    if state.get("limits_error") and not fixed_since:
        print(f"Limits error:  {state['limits_error']}")
    if cfg.codex == "on":
        x = store.read_json(cache / "codex.json")
        captured = f"captured {_ago(x.get('captured_at'), now)}" \
            if isinstance(x, dict) and x.get("captured_at") else "no data yet"
        print(f"Codex limits:  {captured}   status: {'working' if codex.is_working(x, now) else 'idle'}")
        if state.get("codex_error"):
            print(f"Codex error:   {state['codex_error']}")
    else:
        print("Codex limits:  off (set CODEX=on in the config to show them)")
    return 0


def cmd_grant_keychain(args) -> int:
    print("macOS will ask to let 'usage-helper' use the 'Claude Code-credentials' Keychain item.")
    print("Choose 'Always Allow'. The background dashboard never shows this prompt itself.")
    now = time.time()
    try:
        record = claude.fetch_direct(USAGE_HELPER, now, timeout=300, interactive=True)
    except claude.DirectError as exc:
        print(f"Did not work: {exc}", file=sys.stderr)
        return 1
    store.write_json_atomic(paths.cache_dir() / "claude.json", record)
    print("Keychain access granted; direct Claude limits work ✓")
    return 0


def cmd_refresh_limits(args) -> int:
    """Ask the daemon to check Claude limits now (skipping any backoff) and report the result."""
    state = store.read_json(paths.state_path()) or {}
    if time.time() - state.get("updated_at", 0) >= 90:
        print("The dashboard daemon is not running.", file=sys.stderr)
        return 1
    asked = time.time()
    request = paths.home() / "refresh-limits"
    request.touch()
    for _ in range(60):
        time.sleep(0.5)
        state = store.read_json(paths.state_path()) or {}
        if (state.get("limits_checked_at") or 0) >= asked:
            if state.get("limits_error"):
                print(f"Did not work: {state['limits_error']}", file=sys.stderr)
                return 1
            print("Claude limits updated ✓")
            return 0
    request.unlink(missing_ok=True)
    print("The daemon did not answer within 30 s; see 'minitoo-dashboard logs'.", file=sys.stderr)
    return 1


def cmd_preview(args) -> int:
    now = time.time()
    cfg = config.load_config()
    if args.demo:
        model = render.demo_model(now, cfg.lang, codex=cfg.codex == "on")
    else:
        sources = Sources(paths.cache_dir(), paths.sessions_dir())
        model = sources.model(cfg, sources.status(now), now)
    out = Path(args.out) if args.out else paths.home() / "preview"
    out.mkdir(parents=True, exist_ok=True)
    size = (render.W * 3, render.H * 3)
    render.render_screen(model).resize(size, Image.NEAREST).save(out / "screen.png")
    render.render_alert(cfg.lang).resize(size, Image.NEAREST).save(out / "alert.png")
    sample = Crossing("claude", "five_hour", 92.0, now + 4800)
    render.render_limit_alert(sample, cfg.lang, now).resize(size, Image.NEAREST).save(out / "limit-alert.png")
    print(f"Wrote screen.png, alert.png and limit-alert.png to {out}")
    return 0


def cmd_pause(args) -> int:
    paths.home().mkdir(parents=True, exist_ok=True)
    paths.paused_path().touch()
    print("Paused: the dashboard released the device (e.g. for the phone app). "
          "Run 'minitoo-dashboard resume' to continue.")
    return 0


def cmd_resume(args) -> int:
    try:
        paths.paused_path().unlink()
    except FileNotFoundError:
        pass
    print("Resumed.")
    return 0


def cmd_logs(args) -> int:
    try:
        lines = paths.log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        print("No log yet.")
        return 0
    print("\n".join(lines[-args.n:]))
    return 0


def cmd_run(args) -> int:
    from .daemon import run_forever
    run_forever()
    return 0


def main(argv: Optional[List[str]] = None, geocode: Optional[Callable] = None) -> int:
    parser = argparse.ArgumentParser(prog="minitoo-dashboard", description="MiniToo dashboard")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the daemon (used by launchd)")
    p = sub.add_parser("init", help="write device/language/unit settings")
    p.add_argument("--mac")
    p.add_argument("--lang", choices=("en", "ru"))
    p.add_argument("--temp-unit", choices=("celsius", "fahrenheit"))
    p.add_argument("--claude-limits", choices=("statusline", "direct"))
    p.add_argument("--codex", choices=("on", "off"))
    p = sub.add_parser("city", help="set the weather city")
    p.add_argument("name", nargs="+")
    p.add_argument("--pick", type=int)
    sub.add_parser("status", help="show what the dashboard is doing")
    sub.add_parser("refresh-limits", help="check Claude limits now (CLAUDE_LIMITS=direct)")
    sub.add_parser("grant-keychain", help="let usage-helper read the Claude Code login (shows the macOS prompt)")
    p = sub.add_parser("preview", help="render pages to PNG without a device")
    p.add_argument("--demo", action="store_true", help="use sample data")
    p.add_argument("--out")
    sub.add_parser("pause", help="stop driving the device")
    sub.add_parser("resume", help="resume driving the device")
    p = sub.add_parser("logs", help="show recent log lines")
    p.add_argument("-n", type=int, default=40)
    args = parser.parse_args(argv)

    if args.command == "city":
        return cmd_city(args, geocode or weather.geocode)
    return {"run": cmd_run, "init": cmd_init, "status": cmd_status, "preview": cmd_preview,
            "pause": cmd_pause, "resume": cmd_resume, "logs": cmd_logs,
            "grant-keychain": cmd_grant_keychain, "refresh-limits": cmd_refresh_limits}[args.command](args)
