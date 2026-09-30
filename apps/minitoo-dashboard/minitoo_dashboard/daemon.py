from __future__ import annotations

import logging
import logging.handlers
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from . import config as config_mod, encode, paths, render, store
from .collect import Sources
from .device import Device, DeviceError

log = logging.getLogger("minitoo_dashboard")
log.addHandler(logging.NullHandler())  # silent until setup_logging() attaches the file handler

WEATHER_EVERY = 900
WEATHER_RETRY = 300
CALENDAR_EVERY = 60
WAKE_JUMP = 30
HEARTBEAT = 60
BACKOFF = (30, 60, 120, 300)


class Backoff:
    def __init__(self, schedule: Tuple[int, ...] = BACKOFF):
        self.schedule, self.failures, self.until = schedule, 0, 0.0

    def ready(self, now: float) -> bool:
        return now >= self.until

    def fail(self, now: float) -> int:
        delay = self.schedule[min(self.failures, len(self.schedule) - 1)]
        self.failures += 1
        self.until = now + delay
        return delay

    def ok(self) -> None:
        self.failures, self.until = 0, 0.0


def default_dashboard_blob(model: render.DashboardModel, cfg: config_mod.Config) -> bytes:
    return encode.build_blob(render.render_pages(model), cfg.page_seconds * 1000)


def default_alert_blob(cfg: config_mod.Config) -> bytes:
    return encode.build_blob([render.render_alert(cfg.lang)], 1000)


class Dashboard:
    def __init__(self, *, sources: Any, device_for: Callable[[str], Any],
                 load_config: Callable[[], config_mod.Config] = config_mod.load_config,
                 clauddy_alert: Callable[[], Optional[Tuple[int, int]]] = config_mod.clauddy_alert,
                 dashboard_blob: Callable[..., bytes] = default_dashboard_blob,
                 alert_blob: Callable[..., bytes] = default_alert_blob,
                 home: Optional[Path] = None):
        self.sources, self.device_for, self.load_config = sources, device_for, load_config
        self.clauddy_alert, self.dashboard_blob, self.alert_blob = clauddy_alert, dashboard_blob, alert_blob
        self.home = Path(home or paths.home())
        self.backoff = Backoff()
        self.device: Any = None
        self.device_mac: Optional[str] = None
        self.last_blob: Optional[bytes] = None
        self.alert_shown = False
        self.paused = False
        self.status = "chilling"
        self.last_tick: Optional[float] = None
        self.next_weather = self.next_calendar = 0.0
        self.weather_key: Any = None
        self.last_sent_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self._last_state: Any = None
        self._last_state_write = 0.0

    def _device(self, cfg: config_mod.Config) -> Any:
        if cfg.device_mac != self.device_mac:
            self.device_mac = cfg.device_mac
            self.device = self.device_for(cfg.device_mac) if cfg.device_mac else None
        return self.device

    def tick(self, now: float) -> None:
        cfg = self.load_config()
        if self.last_tick is not None and now - self.last_tick > WAKE_JUMP:
            log.info("clock jumped %.0fs (sleep/wake); refreshing everything", now - self.last_tick)
            self.next_weather = self.next_calendar = 0.0
            self.last_blob = None
            self.backoff.ok()
        self.last_tick = now
        device = self._device(cfg)

        if (self.home / "paused").exists():
            if not self.paused and device is not None:
                device.stop()
            self.paused, self.last_blob, self.alert_shown = True, None, False
            self._write_state(now)
            return
        self.paused = False

        self._refresh(cfg, now)
        self.status = self.sources.status(now)
        if device is None:
            self.last_error = "no device configured (DEVICE_MAC in config)"
        elif self.backoff.ready(now):
            try:
                if self.status == "alerting":
                    self._show_alert(cfg, device)
                else:
                    self._show_dashboard(cfg, device, now)
                self.backoff.ok()
            except DeviceError as exc:
                delay = self.backoff.fail(now)
                self.last_error, self.last_blob, self.alert_shown = str(exc), None, False
                log.warning("device error: %s (retry in %ss)", exc, delay)
        self._write_state(now)

    def _refresh(self, cfg: config_mod.Config, now: float) -> None:
        key = (cfg.city_lat, cfg.city_lon, cfg.temp_unit)
        if key != self.weather_key:
            self.weather_key, self.next_weather = key, 0.0
        if cfg.has_city and now >= self.next_weather:
            try:
                self.sources.refresh_weather(cfg, now)
                self.next_weather = now + WEATHER_EVERY
            except Exception as exc:  # network, JSON, API changes: keep last data
                log.warning("weather refresh failed: %s", exc)
                self.next_weather = now + WEATHER_RETRY
        if now >= self.next_calendar:
            try:
                self.sources.refresh_calendar(cfg, now)
            except Exception as exc:
                log.warning("calendar refresh failed: %s", exc)
            self.next_calendar = now + CALENDAR_EVERY

    def _show_alert(self, cfg: config_mod.Config, device: Any) -> None:
        if self.alert_shown:
            return
        target = self.clauddy_alert()
        if target:
            device.select_clock(*target)
        else:
            self._send(cfg, device, self.alert_blob(cfg))
        self.alert_shown, self.last_blob = True, None

    def _show_dashboard(self, cfg: config_mod.Config, device: Any, now: float) -> None:
        self.alert_shown = False
        blob = self.dashboard_blob(self.sources.model(cfg, self.status, now), cfg)
        if blob == self.last_blob:
            return
        self._send(cfg, device, blob)
        self.last_blob = blob

    def _send(self, cfg: config_mod.Config, device: Any, blob: bytes) -> None:
        path = self.home / "cache" / "frame.raw"
        encode.write_rawfile(blob, path)
        device.send_rawfile(path, cfg.send_delay_ms)
        self.last_sent_at, self.last_error = time.time(), None

    def _write_state(self, now: float) -> None:
        shown = "paused" if self.paused else ("alert" if self.alert_shown else
                                              ("dashboard" if self.last_blob else "none"))
        state = {"status": self.status, "shown": shown, "paused": self.paused,
                 "last_sent_at": self.last_sent_at, "last_error": self.last_error,
                 "retry_at": self.backoff.until or None}
        if state == self._last_state and now - self._last_state_write < HEARTBEAT:
            return
        self._last_state, self._last_state_write = state, now
        try:
            store.write_json_atomic(self.home / "state.json", dict(state, updated_at=now))
        except OSError as exc:
            log.warning("cannot write state: %s", exc)


def setup_logging() -> None:
    paths.home().mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(paths.log_path(), maxBytes=1_000_000, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def run_forever() -> None:
    setup_logging()
    sources = Sources(paths.cache_dir(), paths.sessions_dir())
    dashboard = Dashboard(sources=sources, device_for=lambda mac: Device(mac))
    log.info("minitoo-dashboard started")
    while True:
        try:
            dashboard.tick(time.time())
        except Exception:
            log.exception("tick failed")
        time.sleep(1)
