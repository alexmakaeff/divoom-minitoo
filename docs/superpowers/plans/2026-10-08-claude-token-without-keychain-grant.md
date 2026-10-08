# Claude token without Keychain grant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Direct Claude limits read Claude Code's login through `/usr/bin/security` and call the usage endpoint from Python, so no Keychain grant (`grant-keychain`, Swift `usage-helper`) is needed any more.

**Architecture:** `sources/claude.py` gains `read_login()` (runs `security`, parses the item), `request_usage()` (one `urllib` request, returns the same dict shape the Swift helper printed) and a new `fetch_direct(now)` that chains them and feeds the existing `parse_direct()`. The daemon keeps its error handling; only the helper, its CLI command, installer step and texts go. Automatic renewal of an expired token is **not** in this plan: it waits for the check on a really expired token (spec amendment 2026-10-08, last bullet) and gets its own plan.

**Tech Stack:** Python 3 stdlib (`subprocess`, `urllib`, `unittest`), zsh/bash installer, launchd.

**Spec:** `docs/superpowers/specs/2026-09-29-minitoo-dashboard-design.md`, "Amendment (2026-10-08): Claude token without Keychain grant".

## Global Constraints

- Keychain service name: `Claude Code-credentials`; read with `/usr/bin/security find-generic-password -s "Claude Code-credentials" -w`.
- `security` timeout: 10 s; still running → killed and treated as no Keychain access (retry 30 min, logged once, one notification per episode — existing daemon behaviour).
- Usage request: `GET https://api.anthropic.com/api/oauth/usage`, headers `Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`, `User-Agent: minitoo-dashboard`, 15 s timeout.
- The token is never logged, written, put in an exception message or refreshed by the dashboard. Only percentages and reset times are kept.
- API error type/message shortened to 200 chars.
- 403/429 back-off with Retry-After stays as is (`RefusedError`).
- Tests: `cd apps/minitoo-dashboard && python3 -m unittest discover -s tests` (stdlib unittest, no pytest).

## Review Focus

- Keychain item without `claudeAiOauth` (only `mcpOAuth`, seen on the owner's Mac in `Claude Code-credentials-9d56b93d`) → "unexpected Keychain item format", no crash. Test in Task 1.
- Any failure path must not leak the token into the error string (it lands in `state.json`, the log and `status`). Test in Task 1 (`test_errors_never_contain_the_token`).
- HTTP 401 before `expiresAt` (token revoked) → same "run `claude` in Terminal" hint as an expired token. Test in Task 1.
- `Retry-After` given as an HTTP date, not seconds → ignored, default back-off applies. Test in Task 1.
- Network timeout / no network → `DirectError`, last data kept, ordinary 5-min retry (existing daemon path). Test in Task 1 (`network` status).

---

### Task 1: Read the login via `security` and request usage from Python

**Files:**
- Modify: `apps/minitoo-dashboard/minitoo_dashboard/sources/claude.py` (lines 1-9 imports; 61-136 direct-usage section)
- Test: `apps/minitoo-dashboard/tests/test_claude.py` (class `DirectUsageTest`, lines ~60-137)

**Interfaces:**
- Produces:
  - `claude.Login(token: str, expires_at: Optional[float])` (dataclass; `token` excluded from repr)
  - `claude.read_login(run=subprocess.run, timeout: float = 10) -> Login`
  - `claude.request_usage(token: str, timeout: float = 15, urlopen=urllib.request.urlopen) -> dict` — `{"status": "ok", "five_hour": {...}, "seven_day": {...}}` or `{"status": "http_<code>"|"network"|"format", "detail": str, ["retry_after": float]}`
  - `claude.fetch_direct(now: float, read=read_login, request=request_usage) -> dict` (cache record)
  - `claude.TokenExpiredError(DirectError)`; `KeychainAccessError`, `RefusedError`, `DirectError`, `parse_direct` keep their names.
  - Removed: `ACCESS_STATUSES`, `GRANT_HINT`, the old `fetch_direct(helper, now, run, timeout, interactive)`.

- [ ] **Step 1: Replace the helper-based tests with failing tests for the new functions**

In `tests/test_claude.py` add `import urllib.error` and `from email.message import Message` to the imports. In `DirectUsageTest`:
- delete `test_keychain_access_statuses_raise_access_error`, `test_expired_names_the_terminal_command`, `test_fetch_runs_helper_and_parses`, `test_fetch_interactive_passes_flag`, `test_fetch_bad_output_raises`;
- keep the `parse_direct` tests (`test_parse_helper_output`, `test_error_status_raises`, rate limit, 403, missing windows, null resets_at);
- add a new class after `DirectUsageTest`:

```python
SECRET = "sk-ant-oat01-SECRET"


def keychain(stdout="", code=0, calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw.get("timeout")))
        return subprocess.CompletedProcess(cmd, code, stdout, "")
    return run


def item(expires_ms=None, token=SECRET):
    oauth = {"accessToken": token, "refreshToken": "r"}
    if expires_ms is not None:
        oauth["expiresAt"] = expires_ms
    return json.dumps({"claudeAiOauth": oauth, "mcpOAuth": {}})


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def http_error(code, body=b"", retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(claude.USAGE_URL, code, "x", headers, io.BytesIO(body))


class ReadLoginTest(unittest.TestCase):
    def test_runs_security_with_timeout_and_parses(self):
        calls = []
        login = claude.read_login(run=keychain(item(1_700_000_000_000), calls=calls))
        self.assertEqual(calls, [(["/usr/bin/security", "find-generic-password", "-s",
                                   "Claude Code-credentials", "-w"], 10)])
        self.assertEqual(login.token, SECRET)
        self.assertEqual(login.expires_at, 1_700_000_000.0)
        self.assertNotIn(SECRET, repr(login))

    def test_missing_expiry_is_none(self):
        self.assertIsNone(claude.read_login(run=keychain(item())).expires_at)

    def test_item_not_found_says_sign_in(self):
        with self.assertRaises(claude.DirectError) as ctx:
            claude.read_login(run=keychain(code=44))
        self.assertNotIsInstance(ctx.exception, claude.KeychainAccessError)
        self.assertIn("run 'claude' in Terminal", str(ctx.exception))

    def test_timeout_is_keychain_access(self):
        def run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        with self.assertRaises(claude.KeychainAccessError):
            claude.read_login(run=run)

    def test_other_exit_is_keychain_access(self):
        with self.assertRaises(claude.KeychainAccessError) as ctx:
            claude.read_login(run=keychain(code=51))
        self.assertIn("Always Allow", str(ctx.exception))

    def test_item_without_claude_login_is_format_error(self):
        for stdout in ('{"mcpOAuth": {}}', "not json", "[]", '{"claudeAiOauth": {"accessToken": ""}}'):
            with self.assertRaises(claude.DirectError) as ctx:
                claude.read_login(run=keychain(stdout))
            self.assertIn("format", str(ctx.exception))


class RequestUsageTest(unittest.TestCase):
    def test_ok_sends_headers_and_returns_windows(self):
        seen = []

        def urlopen(request, timeout):
            seen.append((request.full_url, request.get_header("Authorization"),
                         request.get_header("Anthropic-beta"), timeout))
            body = {"five_hour": {"utilization": 5.0, "resets_at": "2026-10-08T12:00:00+00:00"},
                    "seven_day": {"utilization": 9.0, "resets_at": None}, "extra": 1}
            return FakeResponse(json.dumps(body).encode())
        data = claude.request_usage(SECRET, urlopen=urlopen)
        self.assertEqual(seen, [(claude.USAGE_URL, f"Bearer {SECRET}", "oauth-2025-04-20", 15)])
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["five_hour"]["utilization"], 5.0)
        self.assertNotIn("extra", data)

    def test_http_error_carries_detail_and_retry_after(self):
        def urlopen(request, timeout):
            raise http_error(429, b'{"type":"error","error":{"type":"rate_limit_error","message":"slow"}}', "120")
        data = claude.request_usage(SECRET, urlopen=urlopen)
        self.assertEqual(data, {"status": "http_429", "detail": "rate_limit_error: slow", "retry_after": 120.0})

    def test_retry_after_date_is_ignored(self):
        def urlopen(request, timeout):
            raise http_error(429, b"", "Wed, 21 Oct 2026 07:28:00 GMT")
        self.assertNotIn("retry_after", claude.request_usage(SECRET, urlopen=urlopen))

    def test_long_error_message_is_shortened(self):
        body = json.dumps({"error": {"type": "x", "message": "m" * 500}}).encode()

        def urlopen(request, timeout):
            raise http_error(500, body)
        self.assertEqual(len(claude.request_usage(SECRET, urlopen=urlopen)["detail"]), 200)

    def test_network_and_bad_json(self):
        def offline(request, timeout):
            raise urllib.error.URLError("no route")
        self.assertEqual(claude.request_usage(SECRET, urlopen=offline)["status"], "network")

        def slow(request, timeout):
            raise TimeoutError("timed out")
        self.assertEqual(claude.request_usage(SECRET, urlopen=slow)["status"], "network")
        garbage = claude.request_usage(SECRET, urlopen=lambda r, timeout: FakeResponse(b"<html>"))
        self.assertEqual(garbage["status"], "format")


class FetchDirectTest(unittest.TestCase):
    OK = DirectUsageTest.HELPER_OK

    def login(self, expires_at=None):
        return lambda: claude.Login(SECRET, expires_at)

    def test_valid_token_is_requested_and_parsed(self):
        tokens = []
        rec = claude.fetch_direct(100.0, read=self.login(200.0),
                                  request=lambda token: tokens.append(token) or self.OK)
        self.assertEqual(tokens, [SECRET])
        self.assertEqual(rec["source"], "direct")
        self.assertEqual(rec["five_hour"]["used_percentage"], 62.0)

    def test_expired_token_is_not_sent(self):
        tokens = []
        with self.assertRaises(claude.TokenExpiredError) as ctx:
            claude.fetch_direct(300.0, read=self.login(200.0), request=lambda token: tokens.append(token))
        self.assertEqual(tokens, [])
        self.assertIn("run 'claude' in Terminal", str(ctx.exception))

    def test_rejected_token_reads_as_expired(self):
        with self.assertRaises(claude.TokenExpiredError):
            claude.fetch_direct(100.0, read=self.login(None),
                                request=lambda token: {"status": "http_401", "detail": "token rejected"})

    def test_errors_never_contain_the_token(self):
        cases = [lambda t: {"status": "http_401", "detail": "x"}, lambda t: {"status": "network", "detail": "x"},
                 lambda t: {"status": "http_429", "detail": "x"}]
        for request in cases:
            with self.assertRaises(claude.DirectError) as ctx:
                claude.fetch_direct(100.0, read=self.login(None), request=request)
            self.assertNotIn(SECRET, str(ctx.exception))
```

Also add `import io` to the imports (`subprocess` and `json` are already imported).

- [ ] **Step 2: Run the tests to see them fail**

Run: `cd apps/minitoo-dashboard && python3 -m unittest tests.test_claude 2>&1 | tail -5`
Expected: errors such as `AttributeError: module 'minitoo_dashboard.sources.claude' has no attribute 'read_login'`.

- [ ] **Step 3: Implement**

In `minitoo_dashboard/sources/claude.py`:

Imports become:

```python
import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional
```

(`Path` is no longer used in this module; drop it if nothing else needs it — check with `grep -n "Path" minitoo_dashboard/sources/claude.py`.)

Replace everything from `class DirectError` to the end of the file with:

```python
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
```

Note: `iso_epoch` and `parse_direct` keep their bodies apart from the message prefix `usage-helper:` → `Claude usage:` and the removed access/expired branches.

- [ ] **Step 4: Run the module's tests**

Run: `cd apps/minitoo-dashboard && python3 -m unittest tests.test_claude 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/minitoo-dashboard/minitoo_dashboard/sources/claude.py apps/minitoo-dashboard/tests/test_claude.py
git commit -m "Read the Claude login via security and request usage from Python"
```

Also run the full suite: `python3 -m unittest discover -s tests 2>&1 | tail -3` → `OK` (`collect.py`/`cli.py` still pass the helper path at runtime, but no test reaches that call; Task 2 rewires them).

---

### Task 2: Wire it in and remove the helper, `grant-keychain` and its installer step

**Files:**
- Modify: `apps/minitoo-dashboard/minitoo_dashboard/collect.py:10,25`
- Modify: `apps/minitoo-dashboard/minitoo_dashboard/cli.py:13,126-137,232,246`
- Modify: `apps/minitoo-dashboard/minitoo_dashboard/daemon.py:23` (comment only)
- Modify: `apps/minitoo-dashboard/minitoo_dashboard/i18n.py:13,26`
- Modify: `apps/minitoo-dashboard/install.sh:70-92`
- Modify: `apps/minitoo-dashboard/.gitignore:3`
- Delete: `apps/minitoo-dashboard/usage-helper/` (`UsageHelper.swift`, `build.sh`, built binary)
- Test: `apps/minitoo-dashboard/tests/test_cli.py:103-123`, `apps/minitoo-dashboard/tests/test_daemon.py:259-275`

**Interfaces:**
- Consumes: `claude.fetch_direct(now: float) -> dict` from Task 1.
- Produces: `Sources(fetch_claude=...)` default is `claude.fetch_direct`; CLI without `grant-keychain`; i18n key `keychain_body` keeps its name, new text.

- [ ] **Step 1: Update the tests first**

In `tests/test_cli.py` delete `test_grant_keychain_runs_helper_interactively_and_caches` and `test_grant_keychain_reports_failure`, and add:

```python
    def test_grant_keychain_is_gone(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["grant-keychain"])
```

(`run_cli` does not catch `SystemExit`; argparse exits on an unknown command.)

In `tests/test_daemon.py`, `test_lost_keychain_access_notifies_once_per_episode`: replace

```python
        self.assertIn("grant-keychain", self.notices[0][1])
```

with

```python
        self.assertIn("minitoo-dashboard status", self.notices[0][1])
```

In `tests/test_collect.py` add to `CollectTest` (it builds `Sources` via `self.sources(**kw)`):

```python
    def test_claude_defaults_to_direct_fetch(self):
        from minitoo_dashboard.sources import claude
        self.assertIs(self.sources().fetch_claude, claude.fetch_direct)
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd apps/minitoo-dashboard && python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: failures in the three tests above (grant-keychain still parses; notice text still names grant-keychain; `fetch_claude` is a lambda), plus possibly import errors from `cli.py` importing `USAGE_HELPER`.

- [ ] **Step 3: Implement**

`collect.py`: delete line 10 (`USAGE_HELPER = ...`) and make line 25

```python
        self.fetch_claude = fetch_claude or claude.fetch_direct
```

`cli.py`: delete `from .collect import USAGE_HELPER`, the whole `cmd_grant_keychain` function, the `sub.add_parser("grant-keychain", ...)` line and the `"grant-keychain": cmd_grant_keychain, ` entry in the dispatch dict (keep `"refresh-limits": cmd_refresh_limits`). Run `grep -n "time\b\|claude\." minitoo_dashboard/cli.py` afterwards; keep the imports other commands still use.

`daemon.py:23` comment:

```python
CLAUDE_ACCESS_RETRY = 1800  # Keychain refused `security`: only the owner can fix it (see `status`)
```

`i18n.py`:

```python
        "keychain_body": "Keychain blocks reading Claude Code's login for limits. Details: minitoo-dashboard status",
```

```python
        "keychain_body": "Keychain не даёт прочитать вход Claude Code для лимитов. Подробности: minitoo-dashboard status",
```

`install.sh`: in the option text replace the two lines

```
       - a small helper reads your Claude Code login token from the Keychain
         (only percentages leave it; the token is never printed or stored);
```

with

```
       - it reads your Claude Code login token from the Keychain with macOS's
         security tool (only percentages are kept; the token is never printed,
         stored or refreshed);
```

and replace the block from `if [ "$(ask 'Choose 1 or 2 [1]: ' 1)" = 2 ]; then` to its `fi` with

```bash
if [ "$(ask 'Choose 1 or 2 [1]: ' 1)" = 2 ]; then
  limits=direct
  note "Direct Claude limits on. In a minute check them with: minitoo-dashboard status"
fi
```

`.gitignore`: delete the line `usage-helper/usage-helper`.

Delete the helper:

```bash
git rm -r apps/minitoo-dashboard/usage-helper
rm -rf apps/minitoo-dashboard/usage-helper
```

Check nothing else refers to it:

```bash
grep -rnI "usage-helper\|usage_helper\|grant-keychain\|USAGE_HELPER" apps/minitoo-dashboard --exclude-dir=__pycache__
```

Expected: no hits except README (Task 3) and the comment in `tests/test_daemon.py:197` (history of the 2026-10-02 incident — reword to "re-ran the Claude usage check every ~50 s").

- [ ] **Step 4: Run the full suite and the installer syntax check**

Run: `cd apps/minitoo-dashboard && python3 -m unittest discover -s tests 2>&1 | tail -3 && bash -n install.sh && echo syntax-ok`
Expected: `OK` and `syntax-ok`.

- [ ] **Step 5: Commit**

```bash
git add -A apps/minitoo-dashboard
git commit -m "Drop usage-helper and grant-keychain: limits read the login via security"
```

---

### Task 3: Docs, deploy and live check

**Files:**
- Modify: `apps/minitoo-dashboard/README.md:102,136-159,262-265`
- Modify: `CHEATSHEET.md:40-50,115-123`
- Modify: `../HANDOFFS/minitoo.md` (outside the repo; coordination file)

**Interfaces:**
- Consumes: behaviour from Tasks 1-2.

- [ ] **Step 1: README**

Line 102: delete the `minitoo-dashboard grant-keychain ...` line.

Replace the `direct (opt-in)` bullet text from "Every 5 minutes a small helper" through "`status` tells you to run `grant-keychain`." with:

```markdown
- **`direct` (opt-in).** Every 5 minutes the dashboard reads Claude Code's
  login from the macOS Keychain with `/usr/bin/security` and asks the same
  endpoint that Claude Code's `/usage` uses. Claude Code writes its login item
  through `security`, so no Keychain grant or prompt is needed. Only
  percentages and reset times are kept; the token is never printed, stored or
  refreshed. If the Keychain ever asks about `security`, the dashboard stops
  waiting after 10 s, shows one macOS notification, falls back to the status
  line, retries every 30 minutes and `status` says what to run. Be aware that:
```

Replace the bullet "an expired token is skipped, not refreshed. It renews the next time Claude Code runs." with:

```markdown
  - an expired token is skipped, not refreshed. Only the terminal `claude`
    CLI renews it; the Claude desktop app signs in on its own. Run `claude`
    in Terminal once and limits come back.
```

Delete "After rebuilding `usage-helper`, run `minitoo-dashboard grant-keychain` again."

Replace the troubleshooting bullet "**"Keychain access needed" in `status`.** ..." with:

```markdown
- **"token expired; run 'claude' in Terminal" in `status`.** Start `claude` in
  Terminal once (any prompt, or just open and quit it); it renews the login.
- **Keychain error in `status`.** The Keychain refused `/usr/bin/security`.
  Run the command `status` shows and choose *Always Allow*.
```

- [ ] **Step 2: CHEATSHEET (Russian, owner's commands)**

Replace the section "## Если пришло уведомление «Нет доступа к Keychain»" (heading, code block and paragraph) with:

````markdown
## Если лимиты Claude не обновляются

```bash
minitoo-dashboard status
```

- «token expired; run 'claude' in Terminal» — откройте `claude` в Терминале
  один раз: только он продлевает вход (приложение Claude — нет).
- Ошибка Keychain — выполните команду, которую показывает `status`, и нажмите
  «Разрешать всегда».
````

Delete the paragraph "Если менялся `usage-helper` ..." and its code block (`build.sh` + `grant-keychain`).

- [ ] **Step 3: Deploy and verify live**

```bash
launchctl kickstart -k gui/$(id -u)/local.minitoo.dashboard
minitoo-dashboard refresh-limits
minitoo-dashboard status
```

Expected: `refresh-limits` reports fresh data; `status` shows "Claude limits: captured … via direct" and no "Limits error". No Keychain window appears. Then:

```bash
grep -n "Claude limits" ~/.minitoo-dashboard/dashboard.log | tail -3
```

Expected: no new warnings after the restart time.

If the token has already expired at this point, expected instead: `Limits error:  Claude usage: Claude Code login token expired; run 'claude' in Terminal once …` — that is correct behaviour; renewal is the follow-up plan.

- [ ] **Step 4: Commit docs**

```bash
git add apps/minitoo-dashboard/README.md CHEATSHEET.md
git commit -m "Docs: limits need no Keychain grant; run claude in Terminal when the token expires"
```

- [ ] **Step 5: Handoff**

Rewrite `../HANDOFFS/minitoo.md` per `../HANDOFFS/README.md`: helper and `grant-keychain` gone; deploy done; remaining = renewal check on an expired token (candidate `claude auth status`, fallback `claude -p` with hooks off) → own plan. Release the claim (`State: idle`, `Claimed: none`) unless the renewal work continues right away.
