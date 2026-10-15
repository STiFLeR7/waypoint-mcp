"""The manager screen (spec Part C): one prompt_toolkit pipe-input test per screen. Needs the tui extra (skipped
without it). Hermetic: temp home, temp logbook, `clis.detected` patched."""
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from waypoint import cli, rules, tui

NEED = unittest.skipUnless(tui.available(), "needs the tui extra: pip install -e .[tui]")
UP, DOWN, LEFT, ENTER, TAB, CTRL_C = "\x1b[A", "\x1b[B", "\x1b[D", "\r", "\t", "\x03"  # Ctrl-C: quit


@NEED
class Manager(unittest.TestCase):
    def setUp(self):
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from waypoint.tui import render, term, widgets
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home, self.code, self.lb = base / "home", base / "code", base / "lb"
        for d in (self.home / ".claude" / "projects" / "slug", self.home / ".codex", self.code / "alpha",
                  self.code / "beta"):
            d.mkdir(parents=True)
        for p in (patch("waypoint.clis.detected", return_value=["codex"]),
                  patch.dict(os.environ, {"APPDATA": str(base / "cfg"), "XDG_CONFIG_HOME": str(base / "cfg")})):
            p.start()
            self.addCleanup(p.stop)
        for k in ("WAYPOINT_LOGBOOK", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CLI_HOME", "COPILOT_HOME"):
            os.environ.pop(k, None)
        cli.init_logbook(self.lb, roots=[str(self.code)])
        cli.add_project(self.lb, self.code / "alpha")
        f = self.home / ".claude" / "projects" / "slug" / "b.jsonl"
        f.write_text(json.dumps({"type": "user", "cwd": str(self.code / "beta")}) + "\n", encoding="utf-8")
        when = time.time() - 3600
        os.utime(f, (when, when))
        self.pipe = self.enterContext(create_pipe_input())
        self.out = io.StringIO()
        t = term.Term(unicode=True, color=False, width=100)
        self.ui = widgets.UI(t, render.console(t, self.out), self.pipe, DummyOutput())

    def run_manager(self, *keys):
        from waypoint.tui import manager
        self.pipe.send_text("".join(keys))
        return manager.run(self.ui, self.lb, "--logbook", self.home)

    def test_menu_refresh_then_quit(self):
        self.assertEqual(self.run_manager(DOWN * 3, ENTER, DOWN * 4, ENTER), 0)
        self.assertTrue((self.lb / "chart.html").exists())
        self.assertIn("✓ Dashboard refreshed: 1 tracked, 1 untracked", self.out.getvalue())
        self.assertIn("1 tracked · 0 archived · 1 untracked", self.out.getvalue())

    def test_menu_prompt_is_choose(self):
        from waypoint.tui import manager
        titles, original = [], manager.ListModel
        def capture(title, *args, **kwargs):
            titles.append(title)
            return original(title, *args, **kwargs)
        with patch.object(manager, "ListModel", side_effect=capture):
            self.assertEqual(self.run_manager(CTRL_C), 0)
        self.assertEqual(titles[0], "Choose")

    def test_projects_archive_restore_and_track(self):
        self.assertEqual(self.run_manager(ENTER, "x", UP, ENTER, TAB, "r", UP, ENTER,
                                          TAB, "t", UP, ENTER, CTRL_C), 0)
        text = self.out.getvalue()
        for line in ("✓ Archive alpha", "✓ Restore alpha", "✓ Track beta"):
            self.assertIn(line, text)
        self.assertTrue((self.lb / "projects" / "alpha.md").exists())
        self.assertTrue((self.lb / "projects" / "beta.md").exists())

    def test_tools_connect_then_fix_drift(self):
        codex = 1
        self.assertEqual(self.run_manager(DOWN, ENTER, DOWN * codex, "c", ENTER, UP, ENTER,
                                          "f", CTRL_C), 0)
        self.assertIn(rules.BEGIN, (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"))
        text = self.out.getvalue()
        self.assertIn("✓ Connect Codex (global)", text)
        self.assertIn("✓ Nothing to change.", text)

    def test_enter_on_a_row_opens_its_actions(self):
        # Projects → Enter on alpha → "Archive" (first) → Review: Apply is last
        self.assertEqual(self.run_manager(ENTER, ENTER, ENTER, UP, ENTER, CTRL_C), 0)
        self.assertIn("✓ Archive alpha", self.out.getvalue())
        self.assertTrue((self.lb / "projects" / "_archive" / "alpha.md").exists())

    def test_row_menu_back_changes_nothing(self):
        self.assertEqual(self.run_manager(DOWN, ENTER, ENTER, UP, ENTER, CTRL_C), 0)  # AI tools → Claude Code → Back
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())

    def test_status_then_back_then_quit(self):
        self.assertEqual(self.run_manager(DOWN * 2, ENTER, ENTER, CTRL_C), 0)
        text = self.out.getvalue()
        self.assertIn("AI tool", text)
        self.assertIn("Codex rule is missing", text)

    def test_remove_review_back_with_left_keeps_project(self):
        self.assertEqual(self.run_manager(ENTER, "d", LEFT, LEFT, CTRL_C), 0)
        self.assertTrue((self.lb / "projects" / "alpha.md").exists())

    def test_remove_review_back_with_ctrl_c_keeps_project(self):
        self.assertEqual(self.run_manager(ENTER, "d", CTRL_C), 0)
        self.assertTrue((self.lb / "projects" / "alpha.md").exists())

    def test_ignore_apply_path(self):
        self.assertEqual(self.run_manager(ENTER, TAB, TAB, "i", UP, ENTER, CTRL_C), 0)
        self.assertIn(str(self.code / "beta"), (self.lb / "projects" / "_ignore.txt").read_text(encoding="utf-8"))

    def test_disconnect_review_back_with_left_keeps_install(self):
        from waypoint.tui import actions
        list(actions.apply_steps(actions.plan_connect("codex", self.lb, self.home)))
        self.assertEqual(self.run_manager(DOWN, ENTER, DOWN, "u", LEFT, CTRL_C), 0)
        self.assertIn(rules.BEGIN, (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
