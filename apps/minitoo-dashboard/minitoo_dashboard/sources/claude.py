from __future__ import annotations

import http.client
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, List, Optional

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


def number(value: Any) -> Optional[float]:
    """A finite JSON number; True/False are ints to Python but never a percentage or a time."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def extract(data: Any, now: float) -> Optional[dict]:
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        return None
    record: dict = {"captured_at": now}
    for key in WINDOWS:
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        pct, resets = number(window.get("used_percentage")), number(window.get("resets_at"))
        if pct is not None and resets is not None:
            record[key] = {"used_percentage": pct, "resets_at": resets}
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


SERVICE = "Claude Code-credentials"
SECURITY = "/usr/bin/security"
NOT_FOUND = 44  # security's exit code for a missing item
KEYCHAIN_TIMEOUT = 10  # still running after this: a Keychain prompt appeared; never wait on it
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"  # undocumented; Claude Code's /usage uses it
RETRY_AFTER_MAX = 6 * 3600  # a server's Retry-After beyond this (or `inf`) must not hold checks until restart


class DirectError(Exception):
    pass


class KeychainAccessError(DirectError):
    """`security` may not read the Claude Code login; only the owner can fix it."""


class TokenExpiredError(DirectError):
    """The login token expired or was rejected; only the Claude Code CLI renews it."""


class OfflineError(DirectError):
    """No network (e.g. a DarkWake without Wi-Fi): a renewal attempt now would achieve nothing."""


class RefusedError(DirectError):
    """The usage endpoint refused (403 no subscription access, 429 rate limited): ask again later."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


REFUSED_STATUSES = ("http_403", "http_429")
PLAN_HINT = "no subscription access (plan lapsed?); after renewing it can take a while"
# The Claude app signs in on its own and never renews the Keychain token Claude Code's CLI keeps.
EXPIRED_HINT = "Claude Code login token expired; run 'claude' in Terminal once (the Claude app does not renew it)"
SIGNED_OUT_HINT = "Claude Code login not found in the Keychain; run 'claude' in Terminal and sign in"
ACCESS_HINT = ("in Terminal run: security find-generic-password -s 'Claude Code-credentials' -w >/dev/null"
               " and choose Always Allow")


@dataclass
class Login:
    token: str = field(repr=False)
    expires_at: Optional[float]


def read_login(run: Callable[..., Any] = subprocess.run, timeout: float = KEYCHAIN_TIMEOUT) -> Login:
    """Claude Code's login, read by `security`: Claude Code writes the item through it, so it needs no grant."""
    try:
        result = run([SECURITY, "find-generic-password", "-s", SERVICE, "-w"],
                     capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:  # run() has killed it, and with it the prompt's requester
        raise KeychainAccessError(f"reading the Keychain timed out (a prompt?); {ACCESS_HINT}")
    except OSError as exc:
        raise DirectError(f"security did not run: {exc}")
    if result.returncode == NOT_FOUND:
        raise DirectError(SIGNED_OUT_HINT)
    if result.returncode != 0:
        raise KeychainAccessError(f"security exited {result.returncode}; {ACCESS_HINT}")
    try:
        oauth = json.loads(result.stdout)["claudeAiOauth"]
        token, expires = oauth["accessToken"], oauth.get("expiresAt")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise DirectError("unexpected Keychain item format")
    if not isinstance(token, str) or not token:
        raise DirectError("unexpected Keychain item format")
    expires = number(expires)
    return Login(token, expires / 1000 if expires is not None else None)


def _error_detail(body: bytes) -> Optional[str]:
    """The API's {"error": {"type", "message"}}; holds no credentials, shortened for log and `status`."""
    try:
        error = json.loads(body)["error"]
        text = ": ".join(str(error[k]) for k in ("type", "message") if error.get(k))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return text[:200] or None


def _retry_after(value: Any) -> Optional[float]:
    """Retry-After in seconds; an HTTP date or nonsense falls back to the daemon's own back-off."""
    try:
        seconds = float(value or 0)
    except ValueError:
        return None
    return min(seconds, RETRY_AFTER_MAX) if math.isfinite(seconds) and seconds > 0 else None


def request_usage(token: str, timeout: float = 15, urlopen: Callable[..., Any] = urllib.request.urlopen) -> dict:
    """One usage request. The token goes only into the header; the result never contains it."""
    request = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "User-Agent": "minitoo-dashboard"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except (OSError, http.client.HTTPException):
            raw = b""
        out: dict = {"status": f"http_{exc.code}",
                     "detail": _error_detail(raw) or ("token rejected" if exc.code == 401 else "unexpected HTTP status")}
        retry = _retry_after((exc.headers or {}).get("Retry-After"))
        if retry is not None:
            out["retry_after"] = retry
        return out
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:  # no network, DNS, timeouts, cut-off
        return {"status": "network", "detail": str(getattr(exc, "reason", exc))[:200]}
    except ValueError:  # http.client refused the header itself; its message would quote the token
        return {"status": "request", "detail": "token not usable in a request (damaged Keychain item?)"}
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        return {"status": "format", "detail": "response is not JSON"}
    if not isinstance(body, dict):
        return {"status": "format", "detail": "response is not a JSON object"}
    return {"status": "ok", **{key: body[key] for key in WINDOWS if key in body}}


def iso_epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_direct(data: Any, now: float) -> dict:
    """Turn a usage result into the same cache record the status line writes."""
    if not isinstance(data, dict):
        raise DirectError("Claude usage: no JSON object")
    if data.get("status") in REFUSED_STATUSES:
        retry = number(data.get("retry_after"))
        retry = retry if retry is not None and retry > 0 else None
        message = f"Claude usage: {data['status']} {data.get('detail', '')}".strip()
        if data["status"] == "http_403":
            message += f"; {PLAN_HINT}"
        raise RefusedError(message, retry)
    if data.get("status") != "ok":
        raise DirectError(f"Claude usage: {data.get('status', 'error')} {data.get('detail', '')}".strip())
    record: dict = {"captured_at": now, "source": "direct"}
    for key in WINDOWS:
        window = data.get(key)
        if not isinstance(window, dict):
            continue
        pct, resets = number(window.get("utilization")), iso_epoch(window.get("resets_at"))
        if pct is not None and resets is not None:
            record[key] = {"used_percentage": pct, "resets_at": resets}
    if not any(key in record for key in WINDOWS) and not any(isinstance(data.get(k), dict) for k in WINDOWS):
        raise DirectError("usage response has no five_hour/seven_day windows (format changed?)")
    return record


def fetch_direct(now: float, read: Callable[[], Login] = read_login,
                 request: Callable[[str], dict] = request_usage) -> dict:
    """Direct Claude limits: Keychain login → usage endpoint → cache record. Never prompts, never refreshes."""
    login = read()
    if login.expires_at is not None and login.expires_at <= now:
        raise TokenExpiredError(f"Claude usage: {EXPIRED_HINT}")
    data = request(login.token)
    if isinstance(data, dict) and data.get("status") == "http_401":
        raise TokenExpiredError(f"Claude usage: http_401 {data.get('detail', '')}; {EXPIRED_HINT}")
    return parse_direct(data, now)


RENEW_TIMEOUT = 60  # `mcp list` health-checks every MCP server; a hung one must not stall the daemon long
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"  # launchd's PATH
CLAUDE_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")  # where installers put `claude`


def find_claude(which: Callable[..., Optional[str]] = shutil.which, environ: Any = os.environ,
                home: Optional[str] = None) -> Optional[str]:
    """The Claude Code CLI, also when launchd's bare PATH does not reach it."""
    home = home or environ.get("HOME") or os.path.expanduser("~")
    dirs = [environ.get("PATH", "")] + [d.replace("~", home, 1) for d in CLAUDE_DIRS]
    return which("claude", path=os.pathsep.join(d for d in dirs if d))


def run_in_group(cmd: List[str], timeout: float, **kw: Any) -> subprocess.CompletedProcess:
    """subprocess.run in its own process group: `claude mcp list` starts stdio MCP servers, and a
    hung one must not outlive the timeout (run() kills only the direct child)."""
    with subprocess.Popen(cmd, start_new_session=True, **kw) as proc:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid)
            proc.wait()
            raise
        _kill_group(proc.pid)  # leftovers of a normal exit
    return subprocess.CompletedProcess(cmd, proc.returncode)


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def network_up(resolve: Callable[..., Any] = socket.getaddrinfo, host: str = "api.anthropic.com") -> bool:
    """Whether the API host resolves: false in a DarkWake without Wi-Fi."""
    try:
        return bool(resolve(host, 443))
    except OSError:  # socket.gaierror included
        return False


def renew_login(run: Callable[..., Any] = run_in_group, find: Callable[[], Optional[str]] = find_claude,
                environ: Any = os.environ, timeout: float = RENEW_TIMEOUT,
                online: Callable[[], bool] = network_up, read: Callable[[], Login] = read_login,
                clock: Callable[[], float] = time.time) -> None:
    """Let Claude Code renew its own expired token: `claude mcp list` fetches the account's claude.ai
    connectors, so the CLI refreshes the login first. No model request, no session, no hooks
    (checked 2026-10-08). Clean env: the desktop app's CLAUDE_CODE_*/ANTHROPIC_* would switch auth."""
    path = find()
    if not path:
        raise DirectError("claude CLI not found; cannot renew the login")
    if not online():  # offline `mcp list` still exits 0 and renews nothing (2026-10-12)
        raise OfflineError("no network; renewal postponed")
    home = environ.get("HOME") or os.path.expanduser("~")
    env = {"HOME": home, "TERM": "dumb", "PATH": f"{os.path.dirname(path)}:{SYSTEM_PATH}"}
    env.update({key: environ[key] for key in ("USER", "LOGNAME") if environ.get(key)})
    try:
        result = run([path, "mcp", "list"], env=env, cwd=home, timeout=timeout,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise DirectError(f"'claude mcp list' timed out after {timeout:.0f} s")
    except OSError as exc:
        raise DirectError(f"'claude mcp list' did not run: {exc}")
    if result.returncode != 0:
        raise DirectError(f"'claude mcp list' exited {result.returncode}")
    login = read()  # exit 0 proves nothing: check that the token really moved on
    if login.expires_at is not None and login.expires_at <= clock():
        raise DirectError("'claude mcp list' ran but did not renew the token")
