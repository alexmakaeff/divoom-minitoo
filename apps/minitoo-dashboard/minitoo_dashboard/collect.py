from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from . import paths, render, status as status_mod, store
from .config import Config
from .sources import calendar, claude, codex, weather

USAGE_HELPER = paths.APP_DIR / "usage-helper" / "usage-helper"


class Sources:
    def __init__(self, cache_dir: Path, sessions_dir: Path, *,
                 fetch_weather: Callable[..., dict] = weather.fetch_weather,
                 fetch_events: Optional[Callable[..., Any]] = None,
                 helper_app: Optional[Path] = None,
                 fetch_claude: Optional[Callable[[float], dict]] = None,
                 codex_root: Optional[Path] = None):
        self.cache_dir = Path(cache_dir)
        self.sessions_dir = Path(sessions_dir)
        self.fetch_weather = fetch_weather
        self.fetch_events = fetch_events or calendar.fetch_events
        self.helper_app = helper_app or calendar.HELPER_APP
        self.fetch_claude = fetch_claude or (lambda now: claude.fetch_direct(USAGE_HELPER, now))
        self.codex_root = Path(codex_root or paths.codex_sessions_dir())
        self.codex_scanner = codex.Scanner(self.codex_root)

    def status(self, now: float) -> str:
        return status_mod.aggregate(status_mod.read_sessions(self.sessions_dir), now)

    def refresh_weather(self, cfg: Config, now: float) -> None:
        record = self.fetch_weather(cfg.city_lat, cfg.city_lon, cfg.temp_unit, now)
        store.write_json_atomic(self.cache_dir / "weather.json", record)

    def refresh_calendar(self, cfg: Config, now: float) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        state, events, reminders_state, reminders = self.fetch_events(self.helper_app, self.cache_dir)
        store.write_json_atomic(self.cache_dir / "calendar.json",
                                {"fetched_at": now, "status": state, "events": events,
                                 "reminders_status": reminders_state, "reminders": reminders})

    def refresh_claude(self, now: float) -> None:
        store.write_json_atomic(self.cache_dir / "claude.json", self.fetch_claude(now))

    def refresh_codex(self, now: float) -> None:
        record, working = self.codex_scanner.scan(now)
        path = self.cache_dir / "codex.json"
        old = store.read_json(path)
        new = codex.merge(old, record, working, now)
        if codex.needs_write(old, new, now):
            store.write_json_atomic(path, new)

    def limit_caches(self, cfg: Config) -> dict:
        caches = {"claude": store.read_json(self.cache_dir / "claude.json")}
        if cfg.codex == "on":
            caches["codex"] = store.read_json(self.cache_dir / "codex.json")
        return caches

    def model(self, cfg: Config, status: str, now: float) -> render.DashboardModel:
        wcache = store.read_json(self.cache_dir / "weather.json")
        if isinstance(wcache, dict) and (wcache.get("lat"), wcache.get("lon")) != (cfg.city_lat, cfg.city_lon):
            wcache = None
        cal = store.read_json(self.cache_dir / "calendar.json")
        cal = cal if isinstance(cal, dict) else {}
        events = calendar.parse_events(cal.get("events")) if cal.get("status") == "ok" else []
        reminders = calendar.parse_reminders(cal.get("reminders")) if cal.get("reminders_status") == "ok" else []
        xcache = store.read_json(self.cache_dir / "codex.json") if cfg.codex == "on" else None
        return render.DashboardModel(
            now=now, status=status, lang=cfg.lang, temp_unit=cfg.temp_unit, city_name=cfg.city_name,
            weather=weather.weather_view(wcache, now) if cfg.has_city else None,
            event=calendar.select_item(events, reminders, now, cfg.calendar_list()),
            claude=claude.limits_view(store.read_json(self.cache_dir / "claude.json"), now),
            codex=claude.limits_view(xcache, now) if cfg.codex == "on" else None,
            codex_working=codex.is_working(xcache, now),
        )
