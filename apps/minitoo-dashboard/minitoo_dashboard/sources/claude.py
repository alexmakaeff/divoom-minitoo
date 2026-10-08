from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
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


SERVICE = "Claude Code-credentials"
SECURITY = "/usr/bin/security"
NOT_FOUND = 44  # security's exit code for a missing item
KEYCHAIN_TIMEOUT = 10  # still running after this: a Keychain prompt appeared; never wait on it
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"  # undocumented; Claude Code's /usage uses it


class DirectError(Exception):
    pass


class KeychainAccessError(DirectError):
    """`security` may not read the Claude Code login; only the owner can fix it."""


class TokenExpiredError(DirectError):
    """The login token expired or was rejected; only the Claude Code CLI renews it."""


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
    return Login(token, expires / 1000 if isinstance(expires, (int, float)) else None)


def _error_detail(body: bytes) -> Optional[str]:
    """The API's {"error": {"type", "message"}}; holds no credentials, shortened for log and `status`."""
    try:
        error = json.loads(body)["error"]
        text = ": ".join(str(error[k]) for k in ("type", "message") if error.get(k))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return text[:200] or None


def request_usage(token: str, timeout: float = 15, urlopen: Callable[..., Any] = urllib.request.urlopen) -> dict:
    """One usage request. The token goes only into the header; the result never contains it."""
    request = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "User-Agent": "minitoo-dashboard"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except OSError:
            raw = b""
        out: dict = {"status": f"http_{exc.code}",
                     "detail": _error_detail(raw) or ("token rejected" if exc.code == 401 else "unexpected HTTP status")}
        try:
            retry = float((exc.headers or {}).get("Retry-After") or 0)
        except ValueError:  # an HTTP date: fall back to the daemon's own back-off
            retry = 0
        if retry > 0:
            out["retry_after"] = retry
        return out
    except (urllib.error.URLError, OSError) as exc:  # no network, DNS, timeouts
        return {"status": "network", "detail": str(getattr(exc, "reason", exc))[:200]}
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
        retry = data.get("retry_after")
        retry = float(retry) if isinstance(retry, (int, float)) and retry > 0 else None
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
        pct, resets = window.get("utilization"), iso_epoch(window.get("resets_at"))
        if isinstance(pct, (int, float)) and resets is not None:
            record[key] = {"used_percentage": float(pct), "resets_at": resets}
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
