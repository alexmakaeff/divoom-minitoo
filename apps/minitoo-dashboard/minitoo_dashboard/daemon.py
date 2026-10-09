from __future__ import annotations

import logging
import logging.handlers
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from . import config as config_mod, encode, i18n, paths, render, store
from .collect import Sources
from .device import Device, DeviceError
from .limit_alerts import Crossing, LimitAlerts, Reset
from .sources.claude import KeychainAccessError, RefusedError, TokenExpiredError

log = logging.getLogger("minitoo_dashboard")
log.addHandler(logging.NullHandler())  # silent until setup_logging() attaches the file handler

WEATHER_EVERY = 900
WEATHER_RETRY = 300
CALENDAR_EVERY = 60
CLAUDE_EVERY = 300  # direct usage requests: undocumented endpoint, keep it gentle
CLAUDE_ACCESS_RETRY = 1800  # Keychain refused `security`: only the owner can fix it (see `status`)
CLAUDE_REFUSED_BACKOFF = (600, 1200, 2400, 3600)  # 403/429: polling harder only prolongs a 429
CLAUDE_RENEW_EVERY = 3600  # expired token: let `claude mcp list` renew it at most this often
CODEX_EVERY = 5  # local log reads only
CODEX_LOG_EVERY = 600  # repeat an unchanged Codex error in the log at most this often
WAKE_JUMP = 30
HEARTBEAT = 60
RESEND_EVERY = 300  # dv acks at the FIFO, not the device: resend so a rebooted/reclaimed MiniToo recovers
BACKOFF = (30, 60, 120, 300)
SILENCE_WAIT = 10  # the MiniToo replies within a second or two; none after this: unanswered
SILENT_AFTER = 2  # unanswered sends in a row before the device counts as silent
RESTART_WINDOW = 60  # a closure that cuts an unconfirmed upload this soon after our send: the MiniToo restarted
LIMIT_ALERT_SHOW = 10  # seconds a limit alert stays on screen before the dashboard returns


def macos_notify(title: str, body: str) -> None:
    """One silent macOS notification; never a dialog that waits for an answer."""
    script = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"]
    args = [part for line in script for part in ("-e", line)]
    subprocess.run(["osascript", *args, title, body], capture_output=True, timeout=10, check=True)


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
    return encode.build_blob([render.render_screen(model)], 1000)


def default_alert_blob(cfg: config_mod.Config) -> bytes:
    return encode.build_blob([render.render_alert(cfg.lang)], 1000)


def default_limit_alert_blob(crossing: Crossing, cfg: config_mod.Config, now: float) -> bytes:
    return encode.build_blob([render.render_limit_alert(crossing, cfg.lang, now)], 1000)


def default_limit_reset_blob(reset: Reset, cfg: config_mod.Config) -> bytes:
    return encode.build_blob([render.render_limit_reset(reset, cfg.lang)], 1000)


class Dashboard:
    def __init__(self, *, sources: Any, device_for: Callable[[str], Any],
                 load_config: Callable[[], config_mod.Config] = config_mod.load_config,
                 clauddy_alert: Callable[..., Optional[Tuple[int, int]]] = config_mod.clauddy_alert,
                 dashboard_blob: Callable[..., bytes] = default_dashboard_blob,
                 alert_blob: Callable[..., bytes] = default_alert_blob,
                 limit_alert_blob: Callable[..., bytes] = default_limit_alert_blob,
                 limit_reset_blob: Callable[..., bytes] = default_limit_reset_blob,
                 home: Optional[Path] = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 notify: Callable[[str, str], None] = macos_notify):
        self.monotonic, self.notify = monotonic, notify
        self.sources, self.device_for, self.load_config = sources, device_for, load_config
        self.clauddy_alert, self.dashboard_blob, self.alert_blob = clauddy_alert, dashboard_blob, alert_blob
        self.limit_alert_blob, self.limit_reset_blob = limit_alert_blob, limit_reset_blob
        self.home = Path(home or paths.home())
        self.limit_alerts = LimitAlerts(self.home / "cache" / "limit-alerts.json")
        self.limit_alert_until = 0.0  # a limit alert is on screen until then
        self.reply_check: Optional[Tuple[int, float]] = None  # (log mark, sent at) awaiting a reply
        self.unanswered, self.unanswered_since = 0, 0.0
        self.silent_since: Optional[float] = None  # the MiniToo stopped replying (phone holds it?)
        self.backoff = Backoff()
        self.device: Any = None
        self.device_mac: Optional[str] = None
        self.last_blob: Optional[bytes] = None
        self.last_blob_sent = 0.0
        self.alert_shown = False
        self.alert_agent: Optional[str] = None  # whose face alert_shown refers to
        self.paused = False
        self.status = "chilling"
        self.last_tick: Optional[float] = None
        self.next_weather = self.next_calendar = self.next_claude = self.next_codex = 0.0
        self.limits_error: Optional[str] = None
        self.limits_error_at: Optional[float] = None
        self.keychain_lost = False  # notified about this loss of access already
        self.limits_checked_at: Optional[float] = None
        self.claude_hold = 0.0  # refused by the endpoint: no request before this, even after wake
        self.claude_refusals = 0
        self.next_renew = 0.0  # no `claude mcp list` before this
        self.codex_error: Optional[str] = None
        self.codex_error_logged = 0.0
        self.weather_key: Any = None
        self.last_sent_at: Optional[float] = None
        self.sent_tick: Optional[float] = None  # tick time of our last upload
        self.last_error: Optional[str] = None
        self._last_state: Any = None
        self._last_state_write = 0.0

    def _device(self, cfg: config_mod.Config) -> Any:
        if cfg.device_mac != self.device_mac:
            self.device_mac = cfg.device_mac
            self.device = self.device_for(cfg.device_mac) if cfg.device_mac else None
        return self.device

    def tick(self, now: float) -> None:
        # last_tick is when the previous tick *ended*: slow work inside a tick (a hung helper,
        # a device timeout) must not look like sleep/wake, or it re-runs itself every tick.
        started = self.monotonic()
        try:
            self._tick(now)
        finally:
            self.last_tick = now + (self.monotonic() - started)

    def _tick(self, now: float) -> None:
        cfg = self.load_config()
        if self.last_tick is not None and now - self.last_tick > WAKE_JUMP:
            log.info("clock jumped %.0fs (sleep/wake); refreshing everything", now - self.last_tick)
            self.next_weather = self.next_calendar = self.next_codex = 0.0
            self.next_claude = self.claude_hold
            self.last_blob = None
            self.backoff.ok()
            self.reply_check, self.unanswered = None, 0  # a send from before the sleep proves nothing
        device = self._device(cfg)

        if (self.home / "paused").exists():
            if not self.paused and device is not None:
                device.stop()
            self.paused, self.last_blob, self.alert_shown = True, None, False
            self._write_state(now)
            return
        self.paused = False
        if device is not None:
            self._watch_closures(device, now)

        self._refresh(cfg, now)
        self.status = self.sources.status(now)
        if device is None:
            self.last_error = "no device configured (DEVICE_MAC in config)"
        elif self._replies_ok(cfg, device, now) and self.backoff.ready(now):
            try:
                if self.status == "alerting":
                    self._show_alert(cfg, device, now)
                elif not self._show_limit_alert(cfg, device, now):
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
        request = self.home / "refresh-limits"
        if request.exists():  # `minitoo-dashboard refresh-limits`: check now, whatever the backoff
            request.unlink(missing_ok=True)
            self.next_claude = self.claude_hold = self.next_renew = 0.0
        if cfg.claude_limits == "direct" and now >= self.next_claude:
            self.next_claude = now + CLAUDE_EVERY
            try:
                self.sources.refresh_claude(now)
                self.limits_error = self.limits_error_at = None
                self.keychain_lost = False
                self.claude_hold, self.claude_refusals = 0.0, 0
            except Exception as exc:  # expired token, endpoint change, no network: keep last data
                error = str(exc)
                if isinstance(exc, RefusedError):
                    step = CLAUDE_REFUSED_BACKOFF[min(self.claude_refusals, len(CLAUDE_REFUSED_BACKOFF) - 1)]
                    delay = max(step, exc.retry_after or 0)
                    self.claude_refusals += 1
                    self.next_claude = self.claude_hold = now + delay
                    log.warning("direct Claude limits refused: %s (retry in %.0f min)", error, delay / 60)
                elif isinstance(exc, KeychainAccessError):
                    self.next_claude = now + CLAUDE_ACCESS_RETRY
                    if error != self.limits_error:
                        log.warning("direct Claude limits need Keychain access: %s", error)
                    if not self.keychain_lost:
                        self.keychain_lost = True
                        self._notify(cfg, "keychain_body")
                elif isinstance(exc, TokenExpiredError) and now >= self.next_renew:
                    self.next_renew = now + CLAUDE_RENEW_EVERY
                    try:
                        self.sources.renew_claude()
                    except Exception as renew_exc:
                        log.warning("direct Claude limits failed: %s; renewing via 'claude mcp list' failed: %s",
                                    error, renew_exc)
                    else:
                        log.info("Claude login token expired; renewed it via 'claude mcp list', checking again")
                        self.next_claude = now  # re-check on the next tick
                else:
                    log.warning("direct Claude limits failed: %s", error)
                self.limits_error, self.limits_error_at = error, now
            self.limits_checked_at = now
        if cfg.codex == "on" and now >= self.next_codex:
            try:
                self.sources.refresh_codex(now)
            except Exception as exc:  # unwritable cache and the like: keep last data, don't flood the log
                error = str(exc)
                if error != self.codex_error or now - self.codex_error_logged >= CODEX_LOG_EVERY:
                    log.warning("Codex refresh failed: %s", error)
                    self.codex_error_logged = now
                self.codex_error = error
            else:
                if self.codex_error is not None:
                    log.info("Codex refresh recovered")
                    self.codex_error = None
            self.next_codex = now + CODEX_EVERY

    def _notify(self, cfg: config_mod.Config, body_key: str) -> None:
        try:
            self.notify(i18n.t(cfg.lang, "notify_title"), i18n.t(cfg.lang, body_key))
        except Exception as exc:  # no osascript, notifications off: the log and `status` still say it
            log.warning("could not show the %s notification: %s", body_key, exc)

    def _replies_ok(self, cfg: config_mod.Config, device: Any, now: float) -> bool:
        """Check that the last send got a reply; a silent device backs off like a failing one."""
        if self.reply_check is None:
            return True
        mark, sent_at = self.reply_check
        if device.replied_since(mark):
            self.reply_check, self.unanswered = None, 0
            if self.silent_since is not None:
                log.info("device responding again")
                self.silent_since = None
            return True
        if now - sent_at < SILENCE_WAIT:
            return True
        self.reply_check, self.last_blob, self.alert_shown = None, None, False  # resend: it is a probe
        if self.unanswered == 0:
            self.unanswered_since = sent_at
        self.unanswered += 1
        if self.unanswered < SILENT_AFTER:
            return True
        if self.silent_since is None:
            self.silent_since = self.unanswered_since
            log.warning("device not responding (the phone app may hold the connection)")
            self._notify(cfg, "silent_body")
        delay = self.backoff.fail(now)
        device.stop()  # the next send reconnects from scratch
        self.last_error = f"device not responding (retry in {delay}s)"
        return False

    def _watch_closures(self, device: Any, now: float) -> None:
        """Log every Bluetooth closure dv saw; a closure mid-upload means the MiniToo restarted."""
        for during_upload in device.closures():
            if during_upload and self.sent_tick is not None and now - self.sent_tick <= RESTART_WINDOW:
                log.warning("device restarted while handling a frame (Bluetooth dropped before it was shown)")
            else:
                log.info("device Bluetooth link closed (switched off, out of range or the Mac slept)")

    def _show_alert(self, cfg: config_mod.Config, device: Any, now: float) -> None:
        self.limit_alert_until = 0.0  # a question replaces a limit alert; the dashboard follows it
        agent = self.sources.alert_agent(now) or "claude"
        if self.alert_shown and agent == self.alert_agent:
            return
        target = self.clauddy_alert(agent=agent)
        if target:
            device.select_clock(*target)
        else:
            self._send(cfg, device, self.alert_blob(cfg), now)
        self.alert_shown, self.alert_agent, self.last_blob = True, agent, None

    def _show_limit_alert(self, cfg: config_mod.Config, device: Any, now: float) -> bool:
        """True while a limit alert or reset is (or has just been put) on screen.

        Resets come first, then crossings; the rest follow, one per LIMIT_ALERT_SHOW.
        """
        if now < self.limit_alert_until:
            return True
        if cfg.limit_alert is None:
            return False
        caches = self.sources.limit_caches(cfg)
        resets = self.limit_alerts.due_resets(caches, cfg.limit_alert, now)
        if resets:
            self._show_limit_frame(cfg, device, now, self.limit_reset_blob(resets[0], cfg))
            self.limit_alerts.done(resets[0].key)
            log.info("limit reset: %s", resets[0].key)
            return True
        crossings = self.limit_alerts.due(caches, cfg.limit_alert, now)
        if not crossings:
            return False
        self._show_limit_frame(cfg, device, now, self.limit_alert_blob(crossings[0], cfg, now))
        self.limit_alerts.mark(crossings[0], now)
        log.info("limit alert: %s at %.0f%%", crossings[0].key, crossings[0].pct)
        return True

    def _show_limit_frame(self, cfg: config_mod.Config, device: Any, now: float, blob: bytes) -> None:
        started = self.monotonic()
        self._send(cfg, device, blob, now)
        shown_at = now + (self.monotonic() - started)  # the transfer itself takes a second or two
        self.limit_alert_until, self.alert_shown, self.last_blob = shown_at + LIMIT_ALERT_SHOW, False, None

    def _show_dashboard(self, cfg: config_mod.Config, device: Any, now: float) -> None:
        self.alert_shown = False
        blob = self.dashboard_blob(self.sources.model(cfg, self.status, now), cfg)
        if blob == self.last_blob and now - self.last_blob_sent < RESEND_EVERY:
            return
        self._send(cfg, device, blob, now)
        self.last_blob, self.last_blob_sent = blob, now

    def _send(self, cfg: config_mod.Config, device: Any, blob: bytes, now: float) -> None:
        path = self.home / "cache" / "frame.raw"
        encode.write_rawfile(blob, path)
        mark = device.send_rawfile(path, cfg.send_delay_ms)
        if self.reply_check is None:  # keep an older pending check: frequent sends must not hide silence
            self.reply_check = (mark, now)
        self.last_sent_at, self.last_error, self.sent_tick = time.time(), None, now

    def _write_state(self, now: float) -> None:
        shown = ("paused" if self.paused else "alert" if self.alert_shown else
                 "limit_alert" if now < self.limit_alert_until else "dashboard" if self.last_blob else "none")
        state = {"status": self.status, "shown": shown, "paused": self.paused,
                 "last_sent_at": self.last_sent_at, "last_error": self.last_error,
                 "retry_at": self.backoff.until or None, "limits_error": self.limits_error,
                 "limits_error_at": self.limits_error_at,
                 "limits_checked_at": self.limits_checked_at,
                 "codex_error": self.codex_error, "device_silent_since": self.silent_since}
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
