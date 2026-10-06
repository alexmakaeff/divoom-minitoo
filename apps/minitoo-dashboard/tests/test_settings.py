import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import settings

HOOK = "/Users/me/Minitoo/apps/minitoo-dashboard/bin/dashboard-hook.sh"
SL = "/Users/me/Minitoo/apps/minitoo-dashboard/bin/statusline.py"
CLAUDDY = {"type": "command", "command": '"/x/apps/clauddy/clauddy-hook.sh" working'}
OTHER = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}


def commands(s, event):
    return [h["command"] for g in s.get("hooks", {}).get(event, []) for h in g["hooks"]]


class HooksTest(unittest.TestCase):
    def test_install_on_empty_is_idempotent(self):
        s, removed = settings.install_hooks({}, HOOK)
        s, _ = settings.install_hooks(s, HOOK)
        for event, arg, _ in settings.HOOK_EVENTS:
            self.assertEqual(commands(s, event), [f'"{HOOK}" {arg}'])
        self.assertEqual(removed, [])

    def test_alert_only_on_prompts_not_idle(self):
        s, _ = settings.install_hooks({}, HOOK)
        group = s["hooks"]["Notification"][0]
        self.assertIn("permission_prompt", group["matcher"])
        self.assertNotIn("idle_prompt", group["matcher"])
        self.assertNotIn("matcher", s["hooks"]["Stop"][0])

    def test_preserves_unrelated_hooks(self):
        s, _ = settings.install_hooks({"hooks": {"PreToolUse": [OTHER]}, "theme": "dark"}, HOOK)
        self.assertEqual(commands(s, "PreToolUse"), ["my-linter", f'"{HOOK}" working'])
        self.assertEqual(s["theme"], "dark")

    def test_replace_clauddy(self):
        base = {"hooks": {"Stop": [{"hooks": [CLAUDDY]}]}}
        kept, _ = settings.install_hooks(base, HOOK)
        self.assertIn(CLAUDDY["command"], commands(kept, "Stop"))
        replaced, removed = settings.install_hooks(base, HOOK, replace_clauddy=True)
        self.assertEqual(commands(replaced, "Stop"), [f'"{HOOK}" chilling'])
        self.assertEqual(removed, [["Stop", CLAUDDY]])
        self.assertTrue(settings.has_clauddy(base))
        self.assertFalse(settings.has_clauddy(replaced))

    def test_uninstall_and_restore(self):
        base = {"hooks": {"Stop": [{"hooks": [CLAUDDY]}], "PreToolUse": [OTHER]}}
        installed, removed = settings.install_hooks(base, HOOK, replace_clauddy=True)
        cleaned = settings.uninstall_hooks(installed, HOOK)
        self.assertEqual(cleaned["hooks"], {"PreToolUse": [OTHER]})
        restored = settings.restore_hooks(cleaned, removed)
        self.assertEqual(commands(restored, "Stop"), [CLAUDDY["command"]])
        self.assertEqual(settings.restore_hooks(restored, removed), restored)

    def test_uninstall_drops_empty_hooks_key(self):
        installed, _ = settings.install_hooks({}, HOOK)
        self.assertNotIn("hooks", settings.uninstall_hooks(installed, HOOK))


class CodexHooksTest(unittest.TestCase):
    def test_alert_on_permission_request_and_never_working(self):
        s, _ = settings.install_hooks({}, HOOK, events=settings.CODEX_HOOK_EVENTS)
        self.assertEqual(commands(s, "PermissionRequest"), [f'"{HOOK}" alerting'])
        for event in ("UserPromptSubmit", "PostToolUse", "Stop", "Interrupt"):
            self.assertEqual(commands(s, event), [f'"{HOOK}" chilling'])
        self.assertEqual(commands(s, "SessionEnd"), [f'"{HOOK}" end'])
        args = [arg for _, arg, _ in settings.CODEX_HOOK_EVENTS]
        self.assertNotIn("working", args)  # Codex's badge comes from its logs, not the Claude badge

    def test_preserves_unrelated_and_uninstalls(self):
        base = {"hooks": {"PreToolUse": [OTHER]}}
        s, _ = settings.install_hooks(base, HOOK, events=settings.CODEX_HOOK_EVENTS)
        s, _ = settings.install_hooks(s, HOOK, events=settings.CODEX_HOOK_EVENTS)
        self.assertEqual(commands(s, "Stop"), [f'"{HOOK}" chilling'])
        self.assertEqual(settings.uninstall_hooks(s, HOOK), base)


class StatuslineTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(settings.statusline_state({}, SL), "absent")
        ours = settings.install_statusline({}, SL)
        self.assertEqual(settings.statusline_state(ours, SL), "ours")
        other = {"statusLine": {"type": "command", "command": "~/my-line.sh"}}
        self.assertEqual(settings.statusline_state(other, SL), "other")
        self.assertEqual(settings.install_statusline(other, SL), other)
        self.assertEqual(settings.remove_statusline(ours, SL), {})
        self.assertEqual(settings.remove_statusline(other, SL), other)


class CliTest(unittest.TestCase):
    def test_install_and_uninstall_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            sfile, saved = Path(tmp) / "settings.json", Path(tmp) / "saved.json"
            sfile.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [CLAUDDY]}]}}))
            self.assertEqual(settings.main(["has-clauddy", "--settings", str(sfile)]), 0)
            settings.main(["install-hooks", "--settings", str(sfile), "--hook", HOOK,
                           "--saved", str(saved), "--replace-clauddy"])
            with contextlib.redirect_stdout(io.StringIO()) as out:
                settings.main(["install-statusline", "--settings", str(sfile), "--statusline", SL])
            self.assertEqual(out.getvalue().strip(), "absent")
            data = json.loads(sfile.read_text())
            self.assertEqual(commands(data, "Stop"), [f'"{HOOK}" chilling'])
            self.assertIn("statusLine", data)
            settings.main(["uninstall", "--settings", str(sfile), "--hook", HOOK,
                           "--statusline", SL, "--saved", str(saved)])
            data = json.loads(sfile.read_text())
            self.assertEqual(commands(data, "Stop"), [CLAUDDY["command"]])
            self.assertNotIn("statusLine", data)
            self.assertFalse(saved.exists())

    def test_invalid_settings_file_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            sfile = Path(tmp) / "settings.json"
            original = '{"model": "opus", // comment\n}'
            sfile.write_text(original)
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = settings.main(["install-hooks", "--settings", str(sfile), "--hook", HOOK,
                                      "--saved", str(Path(tmp) / "saved.json")])
            self.assertEqual(code, 2)
            self.assertIn("not valid JSON", err.getvalue())
            self.assertEqual(sfile.read_text(), original)

    def test_missing_settings_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            sfile = Path(tmp) / "settings.json"
            settings.main(["install-hooks", "--settings", str(sfile), "--hook", HOOK,
                           "--saved", str(Path(tmp) / "saved.json")])
            self.assertIn("hooks", json.loads(sfile.read_text()))

    def test_codex_hooks_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            hfile = Path(tmp) / "hooks.json"
            settings.main(["install-codex-hooks", "--hooks-file", str(hfile), "--hook", HOOK])
            self.assertEqual(commands(json.loads(hfile.read_text()), "PermissionRequest"),
                             [f'"{HOOK}" alerting'])
            settings.main(["uninstall-codex-hooks", "--hooks-file", str(hfile), "--hook", HOOK])
            self.assertEqual(json.loads(hfile.read_text()), {})

    def test_render_template_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "t.in", Path(tmp) / "t.out"
            src.write_text("<string>__APP__</string>")
            settings.main(["render-template", "--src", str(src), "--dst", str(dst),
                           "--set", "__APP__=/a & b", "--xml"])
            self.assertEqual(dst.read_text(), "<string>/a &amp; b</string>")


if __name__ == "__main__":
    unittest.main()
