"""Entry points (spec Part C): which of screen, numbered prompts or plain output `waypoint` and `waypoint setup`
choose. stdlib only: tui.available / tui.interactive and the screen runners are patched, so this runs with or
without the extra and never opens a real screen."""
import builtins
import io
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from waypoint import cli, tui


class Entry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home, self.lb, self.code = base / "home", base / "lb", base / "code"
        self.home.mkdir()
        for d in (self.home / ".codex", self.code):
            d.mkdir(parents=True)
        env = patch.dict(os.environ, {"APPDATA": str(base / "cfg"), "XDG_CONFIG_HOME": str(base / "cfg")})
        env.start()
        self.addCleanup(env.stop)
        for var in ("WAYPOINT_LOGBOOK", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CLI_HOME", "COPILOT_HOME"):
            os.environ.pop(var, None)
        for p in (patch("waypoint.clis.detected", return_value=[]),
                  patch("waypoint.tui.run_wizard", return_value=0), patch("waypoint.tui.run_manager", return_value=0)):
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *args, tty=True, extra=True, answers=()):
        out, feed = io.StringIO(), iter(answers)
        home_patch = patch("waypoint.cli.Path.home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        with redirect_stdout(out), patch.object(tui, "interactive", return_value=tty), \
                patch.object(tui, "available", return_value=extra), patch.object(builtins, "input", lambda *_: next(feed)):
            try:
                cli.main(list(args))
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue()

    def known_logbook(self):
        cli.init_logbook(self.lb, roots=[str(self.code)])
        os.environ["WAYPOINT_LOGBOOK"] = str(self.lb)

    def test_no_terminal_prints_the_commands_and_never_opens_a_screen(self):
        code, out = self.run_cli(tty=False)
        self.assertEqual(code, 0)
        self.assertIn("waypoint setup", out)
        tui.run_wizard.assert_not_called()
        tui.run_manager.assert_not_called()

    def test_bare_waypoint_opens_the_manager_once_a_logbook_is_known(self):
        self.known_logbook()
        self.assertEqual(self.run_cli()[0], 0)
        lb, source, home = tui.run_manager.call_args[0]
        self.assertEqual((lb, source, home), (self.lb, "WAYPOINT_LOGBOOK", self.home))

    def test_bare_waypoint_opens_the_wizard_without_a_logbook(self):
        with patch("waypoint.config.find_logbook", return_value=(self.lb, "the current folder")):
            self.assertEqual(self.run_cli()[0], 0)
        tui.run_wizard.assert_called_once()
        tui.run_manager.assert_not_called()

    def test_numbered_menu_rejects_other_nonempty_answers(self):
        self.known_logbook()
        code, out = self.run_cli(extra=False, answers=["4"])
        self.assertEqual(code, 2)
        self.assertIn("waypoint: choose 1, 2 or 3 (Enter quits).", out)

    def test_setup_flags_keep_the_numbered_prompts(self):
        code, out = self.run_cli("setup", "--yes", "--logbook", str(self.lb), "--root", str(self.code),
                                 "--home", str(self.home))
        self.assertEqual(code, 0)
        tui.run_wizard.assert_not_called()
        self.assertIn("Ready to sail", out)

    def test_plain_setup_on_a_terminal_opens_the_wizard(self):
        self.assertEqual(self.run_cli("setup", "--home", str(self.home))[0], 0)
        self.assertEqual(tui.run_wizard.call_args[0][0], self.home)

    def test_piped_plain_setup_never_opens_the_screen(self):
        self.run_cli("setup", "--home", str(self.home), tty=False, answers=[str(self.lb), str(self.code), "n", "n"])
        tui.run_wizard.assert_not_called()

    def test_without_the_extra_a_numbered_menu_runs_plain_commands(self):
        self.known_logbook()
        code, out = self.run_cli(extra=False, answers=["2"])
        self.assertIn(tui.INSTALL_HINT, out)
        self.assertIn("  2. Status", out)
        self.assertIn(f"Logbook   {self.lb}", out)  # `waypoint status` ran
        tui.run_manager.assert_not_called()

    def test_without_the_extra_and_no_logbook_setup_prompts_run(self):
        # logbook, roots, no git, then "n" to writing: bare `waypoint` has no --home, so nothing may be written
        code, out = self.run_cli(extra=False, answers=[str(self.lb), str(self.code), "n", "n"])
        self.assertIn("No logbook yet, so: waypoint setup", out)
        self.assertIn("Fitting out Waypoint", out)
        self.assertEqual(code, 1)  # "Cancelled; nothing written."
        self.assertFalse(self.lb.exists())
        tui.run_wizard.assert_not_called()

    def test_screen_entry_forces_utf8_before_detection(self):
        calls = []
        fake_t = object()
        from waypoint.tui import term
        render, widgets = types.ModuleType("render"), types.ModuleType("widgets")
        render.console = lambda _term: object()
        widgets.UI = unittest.mock.Mock()
        # patch the package attributes too: `from . import render` reads them first if the real modules were imported
        with patch.dict(sys.modules, {"waypoint.tui.render": render, "waypoint.tui.widgets": widgets}),                 patch.object(tui, "render", render, create=True), patch.object(tui, "widgets", widgets, create=True), \
                patch.object(term, "force_utf8", side_effect=lambda stream: calls.append("force")), \
                patch.object(term, "detect", side_effect=lambda stream: calls.append("detect") or fake_t):
            tui._ui()
        self.assertEqual(calls, ["force", "detect"])
        self.assertIs(widgets.UI.call_args.args[0], fake_t)


if __name__ == "__main__":
    unittest.main()
