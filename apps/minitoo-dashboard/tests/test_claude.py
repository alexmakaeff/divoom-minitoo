import io
import json
import os
import subprocess
import urllib.error
from email.message import Message
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard.sources import claude

APP_DIR = Path(__file__).resolve().parent.parent
NOW = 2_000_000.0
FULL = {"rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": NOW + 7800},
                        "seven_day": {"used_percentage": 41.2, "resets_at": NOW + 259200}}}


class ExtractTest(unittest.TestCase):
    def test_full(self):
        rec = claude.extract(FULL, NOW)
        self.assertEqual(rec["captured_at"], NOW)
        self.assertEqual(rec["five_hour"], {"used_percentage": 23.5, "resets_at": NOW + 7800})
        self.assertIn("seven_day", rec)

    def test_missing_rate_limits(self):
        self.assertIsNone(claude.extract({"model": {}}, NOW))

    def test_partial(self):
        rec = claude.extract({"rate_limits": {"five_hour": FULL["rate_limits"]["five_hour"]}}, NOW)
        self.assertNotIn("seven_day", rec)

    def test_bad_types(self):
        self.assertIsNone(claude.extract({"rate_limits": {"five_hour": {"used_percentage": "x"}}}, NOW))


class ViewTest(unittest.TestCase):
    def test_no_cache(self):
        view = claude.limits_view(None, NOW)
        self.assertFalse(view.has_data)

    def test_fresh(self):
        view = claude.limits_view(claude.extract(FULL, NOW - 60), NOW)
        self.assertTrue(view.has_data)
        self.assertEqual(view.five, claude.Window(23.5, 7800, False))
        self.assertIsNone(view.as_of)

    def test_stale_sets_as_of(self):
        view = claude.limits_view(claude.extract(FULL, NOW - 1200), NOW)
        self.assertEqual(view.as_of, NOW - 1200)

    def test_reset_passed(self):
        data = {"rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": NOW - 10},
                                "seven_day": FULL["rate_limits"]["seven_day"]}}
        view = claude.limits_view(claude.extract(data, NOW - 9000), NOW)
        self.assertEqual(view.five, claude.Window(None, None, True))
        self.assertFalse(view.week.is_reset)

    def test_missing_window(self):
        rec = claude.extract({"rate_limits": {"five_hour": FULL["rate_limits"]["five_hour"]}}, NOW)
        self.assertEqual(claude.limits_view(rec, NOW).week, claude.Window(None, None, False))


class DirectUsageTest(unittest.TestCase):
    HELPER_OK = {"status": "ok",
                 "five_hour": {"utilization": 62.0, "resets_at": "2026-09-30T11:40:00.256658+00:00"},
                 "seven_day": {"utilization": 30.0, "resets_at": "2026-10-02T09:00:00+00:00"}}

    def test_parse_helper_output(self):
        rec = claude.parse_direct(self.HELPER_OK, 1000.0)
        self.assertEqual(rec["captured_at"], 1000.0)
        self.assertEqual(rec["source"], "direct")
        self.assertEqual(rec["five_hour"]["used_percentage"], 62.0)
        self.assertAlmostEqual(rec["five_hour"]["resets_at"], 1790768400.256658, places=3)
        self.assertEqual(rec["seven_day"]["used_percentage"], 30.0)

    def test_error_status_raises(self):
        with self.assertRaises(claude.DirectError) as ctx:
            claude.parse_direct({"status": "expired"}, 0)
        self.assertIn("expired", str(ctx.exception))

    def test_rate_limit_carries_retry_after(self):
        with self.assertRaises(claude.RefusedError) as ctx:
            claude.parse_direct({"status": "http_429", "detail": "rate_limit_error: slow down", "retry_after": 120}, 0)
        self.assertEqual(ctx.exception.retry_after, 120)
        self.assertIn("slow down", str(ctx.exception))

    def test_forbidden_is_refused_with_plan_hint(self):
        with self.assertRaises(claude.RefusedError) as ctx:
            claude.parse_direct({"status": "http_403", "detail": "permission_error: no access"}, 0)
        self.assertIsNone(ctx.exception.retry_after)
        self.assertIn("subscription", str(ctx.exception))

    def test_missing_windows_raise(self):
        with self.assertRaises(claude.DirectError):
            claude.parse_direct({"status": "ok"}, 0)

    def test_null_resets_at_skips_window(self):
        data = {"status": "ok", "five_hour": {"utilization": 5.0, "resets_at": None},
                "seven_day": self.HELPER_OK["seven_day"]}
        self.assertNotIn("five_hour", claude.parse_direct(data, 0))


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


class StatuslineScriptTest(unittest.TestCase):
    def run_script(self, home, payload):
        env = dict(os.environ, MINITOO_DASHBOARD_HOME=home)
        return subprocess.run(["python3", str(APP_DIR / "bin" / "statusline.py")], input=payload,
                              text=True, env=env, capture_output=True, timeout=10)

    def test_writes_cache_and_prints_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            r = self.run_script(home, json.dumps(FULL))
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            data = json.loads((Path(home) / "cache" / "claude.json").read_text())
            self.assertIn("five_hour", data)

    def test_invalid_json_is_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            r = self.run_script(home, "not json")
            self.assertEqual(r.returncode, 0)
            self.assertFalse((Path(home) / "cache" / "claude.json").exists())


if __name__ == "__main__":
    unittest.main()
