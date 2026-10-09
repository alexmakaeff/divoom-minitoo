# Claude token auto-renewal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When Claude Code's login token has expired, the dashboard daemon renews it itself by running `claude mcp list` (at most once an hour), then checks limits again, so direct limits no longer need the owner to open `claude` in Terminal.

**Architecture:** `sources/claude.py` gains `find_claude()` (locates the CLI even under launchd's bare PATH) and `renew_login()` (runs `claude mcp list` in a clean environment, 60 s timeout, output discarded). `collect.Sources` exposes it as `renew_claude()`. The daemon's `_refresh` calls it on `TokenExpiredError`, holds further attempts for an hour, and on success re-checks limits on the next tick. The "run `claude` in Terminal" hint stays as the fallback.

**Tech Stack:** Python 3 stdlib (`subprocess`, `shutil`, `os`, `unittest`), launchd.

**Spec:** `docs/superpowers/specs/2026-09-29-minitoo-dashboard-design.md`, "Amendment (2026-10-08): Claude token without Keychain grant" — the "Expired token" bullet and the "Check result (2026-10-08 19:08 …)" bullet.

## Global Constraints

- Renewal only when the token is expired (`TokenExpiredError`, which also covers HTTP 401); never while it is valid.
- At most one renewal attempt per hour (`CLAUDE_RENEW_EVERY = 3600`), whatever its result; `minitoo-dashboard refresh-limits` lifts that hold.
- Command: `<claude> mcp list`; no model request. Never `claude -p`.
- Environment for the child: only `HOME`, `USER`, `LOGNAME`, `TERM=dumb`, and `PATH=<dir of claude>:/usr/bin:/bin:/usr/sbin:/sbin`; `cwd=$HOME`; stdin/stdout/stderr → `DEVNULL` (its output lists MCP servers; nothing is kept or logged).
- Timeout 60 s (`RENEW_TIMEOUT`); `subprocess.run` kills the child on timeout.
- `claude` lookup: current `PATH`, then `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin` (launchd's PATH is `/usr/bin:/bin:/usr/sbin:/sbin`).
- The dashboard still never reads, logs or writes the refresh token; renewal is done by Claude Code itself.
- Tests: `cd apps/minitoo-dashboard && python3 -m unittest discover -s tests`.

## Review Focus

- `claude` not installed → renewal fails quietly with one warning per hour, the Terminal hint stays; no crash, no tight loop. Test in Task 1 (`test_missing_cli`) and Task 2 (`test_failed_renewal_waits_an_hour`).
- `mcp list` exits 0 but the token is still expired (CLI changed) → exactly one re-check, then the normal 5-min retries with the hint, no second renewal within the hour. Test in Task 2 (`test_renewal_that_did_not_help_is_not_repeated`).
- `mcp list` hangs (an MCP server never answers) → killed after 60 s, counted as failed. Test in Task 1 (`test_timeout`).
- Desktop-app environment variables (`CLAUDE_CODE_*`, `ANTHROPIC_*`) must not reach the child, or it would use the app's host auth instead of the Keychain login. Test in Task 1 (`test_runs_mcp_list_in_clean_env`).
- `refresh-limits` while the renewal hold is active → renewal is tried again at once. Test in Task 2 (`test_refresh_limits_lifts_renewal_hold`).

---

### Task 1: `find_claude()` and `renew_login()`

**Files:**
- Modify: `apps/minitoo-dashboard/minitoo_dashboard/sources/claude.py` (imports lines 1-9; append after `fetch_direct`)
- Test: `apps/minitoo-dashboard/tests/test_claude.py` (new class `RenewLoginTest` before `class StatuslineScriptTest`)

**Interfaces:**
- Produces:
  - `claude.find_claude(which=shutil.which, environ=os.environ, home: Optional[str] = None) -> Optional[str]`
  - `claude.renew_login(run=subprocess.run, find=find_claude, environ=os.environ, timeout: float = RENEW_TIMEOUT) -> None` — raises `DirectError` on failure.
  - Constants `RENEW_TIMEOUT = 60`, `SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_claude.py` before `class StatuslineScriptTest(`:

```python
class RenewLoginTest(unittest.TestCase):
    ENV = {"HOME": "/Users/u", "USER": "u", "LOGNAME": "u", "PATH": "/usr/bin:/bin",
           "CLAUDE_CODE_OAUTH_TOKEN": "x", "ANTHROPIC_BASE_URL": "y", "CLAUDECODE": "1"}

    def test_find_claude_looks_past_launchd_path(self):
        seen = []

        def which(name, path=None):
            seen.append(path)
            return "/Users/u/.local/bin/claude"
        self.assertEqual(claude.find_claude(which=which, environ=self.ENV), "/Users/u/.local/bin/claude")
        self.assertEqual(seen, ["/usr/bin:/bin:/Users/u/.local/bin:/opt/homebrew/bin:/usr/local/bin"])

    def test_find_claude_none_when_missing(self):
        self.assertIsNone(claude.find_claude(which=lambda name, path=None: None, environ=self.ENV))

    def test_runs_mcp_list_in_clean_env(self):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0)
        claude.renew_login(run=run, find=lambda: "/Users/u/.local/bin/claude", environ=self.ENV)
        (cmd, kw), = calls
        self.assertEqual(cmd, ["/Users/u/.local/bin/claude", "mcp", "list"])
        self.assertEqual(kw["env"], {"HOME": "/Users/u", "USER": "u", "LOGNAME": "u", "TERM": "dumb",
                                     "PATH": "/Users/u/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"})
        self.assertEqual(kw["cwd"], "/Users/u")
        self.assertEqual(kw["timeout"], 60)
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(kw[stream], subprocess.DEVNULL)

    def test_missing_cli(self):
        with self.assertRaises(claude.DirectError) as ctx:
            claude.renew_login(run=None, find=lambda: None, environ=self.ENV)
        self.assertIn("not found", str(ctx.exception))

    def test_nonzero_exit(self):
        with self.assertRaises(claude.DirectError) as ctx:
            claude.renew_login(run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1),
                               find=lambda: "/x/claude", environ=self.ENV)
        self.assertIn("exited 1", str(ctx.exception))

    def test_timeout(self):
        def run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        with self.assertRaises(claude.DirectError) as ctx:
            claude.renew_login(run=run, find=lambda: "/x/claude", environ=self.ENV)
        self.assertIn("timed out", str(ctx.exception))

    def test_cannot_start(self):
        def run(cmd, **kw):
            raise PermissionError("denied")
        with self.assertRaises(claude.DirectError):
            claude.renew_login(run=run, find=lambda: "/x/claude", environ=self.ENV)
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd apps/minitoo-dashboard && python3 -m unittest tests.test_claude 2>&1 | tail -3`
Expected: `FAILED (errors=7)` — `AttributeError: ... has no attribute 'find_claude'` / `'renew_login'`.

- [ ] **Step 3: Implement**

In `minitoo_dashboard/sources/claude.py` add to the imports:

```python
import os
import shutil
```

Append after `fetch_direct`:

```python
RENEW_TIMEOUT = 60  # `mcp list` health-checks every MCP server; a hung one must not stall the daemon long
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"  # launchd's PATH
CLAUDE_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")  # where installers put `claude`


def find_claude(which: Callable[..., Optional[str]] = shutil.which, environ: Any = os.environ,
                home: Optional[str] = None) -> Optional[str]:
    """The Claude Code CLI, also when launchd's bare PATH does not reach it."""
    home = home or environ.get("HOME") or os.path.expanduser("~")
    dirs = [environ.get("PATH", "")] + [d.replace("~", home, 1) for d in CLAUDE_DIRS]
    return which("claude", path=os.pathsep.join(d for d in dirs if d))


def renew_login(run: Callable[..., Any] = subprocess.run, find: Callable[[], Optional[str]] = find_claude,
                environ: Any = os.environ, timeout: float = RENEW_TIMEOUT) -> None:
    """Let Claude Code renew its own expired token: `claude mcp list` fetches the account's claude.ai
    connectors, so the CLI refreshes the login first. No model request, no session, no hooks
    (checked 2026-10-08). Clean env: the desktop app's CLAUDE_CODE_*/ANTHROPIC_* would switch auth."""
    path = find()
    if not path:
        raise DirectError("claude CLI not found; cannot renew the login")
    home = environ.get("HOME") or os.path.expanduser("~")
    env = {"HOME": home, "USER": environ.get("USER", ""), "LOGNAME": environ.get("LOGNAME", ""),
           "TERM": "dumb", "PATH": f"{os.path.dirname(path)}:{SYSTEM_PATH}"}
    try:
        result = run([path, "mcp", "list"], env=env, cwd=home, timeout=timeout,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise DirectError(f"'claude mcp list' timed out after {timeout:.0f} s")
    except OSError as exc:
        raise DirectError(f"'claude mcp list' did not run: {exc}")
    if result.returncode != 0:
        raise DirectError(f"'claude mcp list' exited {result.returncode}")
```

- [ ] **Step 4: Run the tests**

Run: `cd apps/minitoo-dashboard && python3 -m unittest tests.test_claude 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/minitoo-dashboard/minitoo_dashboard/sources/claude.py apps/minitoo-dashboard/tests/test_claude.py
git commit -m "Renew an expired Claude login by running claude mcp list"
```

---

### Task 2: Daemon renews on an expired token

**Files:**
- Modify: `apps/minitoo-dashboard/minitoo_dashboard/collect.py` (constructor ~line 13-26; method after `refresh_claude` ~line 47)
- Modify: `apps/minitoo-dashboard/minitoo_dashboard/daemon.py` (import line 14; constants ~line 22-24; `__init__` ~line 111; `_refresh` ~lines 194-222)
- Test: `apps/minitoo-dashboard/tests/test_daemon.py` (`FakeSources` ~line 33-50; new tests in `DashboardTest` after `test_direct_failure_is_reported_and_retried`), `apps/minitoo-dashboard/tests/test_collect.py`

**Interfaces:**
- Consumes: `claude.renew_login() -> None` (raises `DirectError`), `claude.TokenExpiredError` (Task 1 / existing).
- Produces: `Sources(renew_claude=...)`, `Sources.renew_claude() -> None`; daemon constant `CLAUDE_RENEW_EVERY = 3600`, attribute `next_renew`.

- [ ] **Step 1: Write the failing tests**

`tests/test_collect.py`, in `CollectTest`:

```python
    def test_renew_claude_defaults_to_renew_login(self):
        from minitoo_dashboard.sources import claude
        self.assertIs(self.sources().renew_claude_login, claude.renew_login)
        calls = []
        self.sources(renew_claude=lambda: calls.append(1)).renew_claude()
        self.assertEqual(calls, [1])
```

`tests/test_daemon.py`, in `FakeSources` after `refresh_claude`:

```python
    renew_calls = 0
    renew_error = None
    on_renew = None

    def renew_claude(self):
        self.renew_calls += 1
        if self.on_renew:
            self.on_renew()
        if self.renew_error:
            raise self.renew_error
```

In `DashboardTest`, after `test_direct_failure_is_reported_and_retried`:

```python
    DIRECT = Config(device_mac="AA:BB:CC:DD:EE:FF", claude_limits="direct")

    def expired(self):
        from minitoo_dashboard.sources.claude import TokenExpiredError
        return TokenExpiredError("Claude usage: token expired; run 'claude' in Terminal once")

    def test_expired_token_is_renewed_and_rechecked(self):
        self.sources.claude_error = self.expired()
        self.sources.on_renew = lambda: setattr(self.sources, "claude_error", None)
        dash = self.make(cfg=self.DIRECT)
        with self.assertLogs("minitoo_dashboard", "INFO") as logs:
            dash.tick(T)
            dash.tick(T + 1)
        self.assertEqual((self.sources.renew_calls, self.sources.claude_calls), (1, 2))
        self.assertIsNone(store.read_json(self.home / "state.json")["limits_error"])
        self.assertTrue(any("renewed" in line for line in logs.output))

    def test_other_errors_never_renew(self):
        from minitoo_dashboard.sources.claude import DirectError, KeychainAccessError, RefusedError
        dash = self.make(cfg=self.DIRECT)
        for i, error in enumerate([DirectError("network"), KeychainAccessError("k"), RefusedError("r", None)]):
            self.sources.claude_error = error
            dash.next_claude = dash.claude_hold = 0.0
            dash.tick(T + 10 * i)
        self.assertEqual(self.sources.renew_calls, 0)

    def test_failed_renewal_waits_an_hour(self):
        from minitoo_dashboard.sources.claude import DirectError
        self.sources.claude_error = self.expired()
        self.sources.renew_error = DirectError("claude CLI not found; cannot renew the login")
        dash = self.make(cfg=self.DIRECT)
        with self.assertLogs("minitoo_dashboard", "INFO") as logs:
            for t in range(0, 3600, 10):
                dash.tick(T + t)
        self.assertEqual(self.sources.renew_calls, 1)
        self.assertEqual(self.sources.claude_calls, 12)  # ordinary 5-min checks continue
        self.assertTrue(any("not found" in line for line in logs.output))
        self.assertIn("run 'claude' in Terminal", store.read_json(self.home / "state.json")["limits_error"])
        dash.tick(T + 3600)
        self.assertEqual(self.sources.renew_calls, 2)

    def test_renewal_that_did_not_help_is_not_repeated(self):
        self.sources.claude_error = self.expired()  # renew "succeeds" but the token stays expired
        dash = self.make(cfg=self.DIRECT)
        for t in range(0, 1800, 1):
            dash.tick(T + t)
        self.assertEqual(self.sources.renew_calls, 1)
        self.assertEqual(self.sources.claude_calls, 2 + 5)  # T, re-check at T+1, then every 5 min

    def test_refresh_limits_lifts_renewal_hold(self):
        from minitoo_dashboard.sources.claude import DirectError
        self.sources.claude_error = self.expired()
        self.sources.renew_error = DirectError("exited 1")
        dash = self.make(cfg=self.DIRECT)
        dash.tick(T)
        (self.home / "refresh-limits").touch()
        dash.tick(T + 10)
        self.assertEqual(self.sources.renew_calls, 2)
```

(`self.make(cfg=...)`, `T` and `store` exist in `tests/test_daemon.py`. Every `tick` runs `_refresh` once; ticks 1-10 s apart stay under `WAKE_JUMP` (30 s), and a renewal that blocks a tick does not look like a wake because `tick` measures from the end of the previous tick.)

- [ ] **Step 2: Run them to see them fail**

Run: `cd apps/minitoo-dashboard && python3 -m unittest tests.test_daemon tests.test_collect 2>&1 | grep -E "^(FAIL|ERROR):|^FAILED|^OK"`
Expected: failures/errors in the five new daemon tests that expect renewals (`renew_calls` stays 0; `test_other_errors_never_renew` may already pass) and in `test_renew_claude_defaults_to_renew_login` (`TypeError: unexpected keyword argument 'renew_claude'`).

- [ ] **Step 3: Implement**

`collect.py` — constructor gets a parameter after `fetch_claude`:

```python
                 renew_claude: Optional[Callable[[], None]] = None,
```

and in its body, after `self.fetch_claude = ...`:

```python
        self.renew_claude_login = renew_claude or claude.renew_login
```

and after `refresh_claude`:

```python
    def renew_claude(self) -> None:
        self.renew_claude_login()
```

`daemon.py`:

```python
from .sources.claude import KeychainAccessError, RefusedError, TokenExpiredError
```

Constants, after `CLAUDE_REFUSED_BACKOFF`:

```python
CLAUDE_RENEW_EVERY = 3600  # expired token: let `claude mcp list` renew it at most this often
```

`__init__`, after `self.claude_refusals = 0`:

```python
        self.next_renew = 0.0  # no `claude mcp list` before this
```

`_refresh`: the refresh-limits request also lifts the renewal hold:

```python
            self.next_claude = self.claude_hold = self.next_renew = 0.0
```

and in the `except` chain, between the `KeychainAccessError` branch and the final `else`:

```python
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
```

(An expired token during the hold falls through to the existing `else: log.warning("direct Claude limits failed: %s", error)`.)

- [ ] **Step 4: Run the full suite**

Run: `cd apps/minitoo-dashboard && python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add apps/minitoo-dashboard/minitoo_dashboard/collect.py apps/minitoo-dashboard/minitoo_dashboard/daemon.py apps/minitoo-dashboard/tests/test_daemon.py apps/minitoo-dashboard/tests/test_collect.py
git commit -m "Daemon renews an expired Claude token at most once an hour"
```

---

### Task 3: Spec, docs, deploy and live check

**Files:**
- Modify: `docs/superpowers/specs/2026-09-29-minitoo-dashboard-design.md` (the "Expired token" bullet of the 2026-10-08 amendment)
- Modify: `apps/minitoo-dashboard/README.md` (the `direct` bullet's "expired token" sub-bullet; troubleshooting "token expired" bullet)
- Modify: `CHEATSHEET.md` (section "Если лимиты Claude не обновляются (истёк вход или ошибка Keychain)")
- Modify: `../HANDOFFS/minitoo.md`

- [ ] **Step 1: Spec**

Replace the "Expired token" bullet's text from "Which command renews" to "run `claude` in Terminal once." with:

```markdown
  It runs `claude mcp list` (see the check result below) with a clean
  environment (`HOME`, `USER`, `LOGNAME`, `TERM=dumb`, a minimal `PATH`),
  `cwd=$HOME`, output discarded, 60 s timeout; `claude` is looked up on the
  PATH and in `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`. Success →
  limits are checked again on the next tick; failure → one warning. The hour
  hold applies either way; `refresh-limits` lifts it. When renewal fails or
  does not help, the error still says to run `claude` in Terminal once.
```

- [ ] **Step 2: README and CHEATSHEET**

README, `direct` bullet — replace the sub-bullet that starts "an expired token is skipped, not refreshed." with:

```markdown
  - the dashboard never refreshes the token itself. When it has expired, the
    daemon runs `claude mcp list` (at most once an hour), which makes Claude
    Code renew it; that call also checks your MCP servers. If that fails, run
    `claude` in Terminal once (the Claude desktop app signs in on its own).
```

README troubleshooting — replace the "token expired; run 'claude' in Terminal" bullet with:

```markdown
- **"token expired; run 'claude' in Terminal" in `status`.** The daemon's own
  renewal (`claude mcp list`, hourly) did not work; the log says why
  (`minitoo-dashboard logs`). Start `claude` in Terminal once (or run
  `minitoo-dashboard refresh-limits` to retry the renewal now).
```

CHEATSHEET — replace the bullet starting «token expired; run 'claude' in Terminal» with:

```markdown
- «token expired; run 'claude' in Terminal» — дашборд сам продлевает вход
  (раз в час запускает `claude mcp list`), значит, это не сработало. Причина в
  `minitoo-dashboard logs`. Повторить сразу: `minitoo-dashboard refresh-limits`;
  или откройте `claude` в Терминале один раз.
```

- [ ] **Step 3: Full suite, deploy, live check on the really expired token**

```bash
cd apps/minitoo-dashboard && python3 -m unittest discover -s tests 2>&1 | tail -2
launchctl kickstart -k gui/$(id -u)/local.minitoo.dashboard
sleep 20
grep -E "renew|direct Claude limits" ~/.minitoo-dashboard/dashboard.log | tail -3
minitoo-dashboard status | grep -E "Claude limits|Limits error"
```

Expected: `OK`; a log line "Claude login token expired; renewed it via 'claude mcp list', checking again"; `status` shows "captured … via direct" and no "Limits error". If the token is not expired at this moment, the log shows no renewal and limits work — then record that the live renewal is still to be seen and check the log after the next expiry (`grep renew ~/.minitoo-dashboard/dashboard.log`).

Also confirm no dashboard hook fired: `find ~/.minitoo-dashboard/sessions -newermt "-2 minutes"` → nothing.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-29-minitoo-dashboard-design.md apps/minitoo-dashboard/README.md CHEATSHEET.md
git commit -m "Docs: the daemon renews an expired Claude token via claude mcp list"
```

- [ ] **Step 5: Handoff**

Rewrite the Claude-token paragraph and the NEXT line in `../HANDOFFS/minitoo.md`: renewal implemented and deployed (live result), remaining minors; release the claim (`State: idle`, `Claimed: none`).
