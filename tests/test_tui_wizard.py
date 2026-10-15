"""The setup screen (spec Part C): one prompt_toolkit pipe-input test per wizard screen, plus a full run and a quit.
Needs the tui extra (skipped without it). Hermetic: temp home, temp logbook, `clis.detected` patched."""
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from waypoint import rules, tui

NEED = unittest.skipUnless(tui.available(), "needs the tui extra: pip install -e .[tui]")
UP, DOWN, LEFT, ENTER, ESC, SPACE, CTRL_C = "\x1b[A", "\x1b[B", "\x1b[D", "\r", "\x1b", " ", "\x03"
# A lone ESC is recognised after a short wait, by the screen running then: a test sends it last, once that screen
# has started. Queued behind other screens' keys, a test quits with Ctrl-C (the same "quit" outcome).


@NEED
class Screens(unittest.TestCase):
    def setUp(self):
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from waypoint.tui import render, term, widgets
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home, self.code = base / "home", base / "home" / "code"
        for d in (self.home / ".claude" / "projects" / "slug", self.home / ".codex", self.code / "alpha",
                  self.code / "beta"):
            d.mkdir(parents=True)
        self.lb = self.home / "waypoint-logbook"
        f = self.home / ".claude" / "projects" / "slug" / "s.jsonl"
        f.write_text(json.dumps({"type": "user", "cwd": str(self.code / "alpha")}) + "\n", encoding="utf-8")
        when = time.time() - 2 * 86400
        os.utime(f, (when, when))
        for p in (patch("waypoint.clis.detected", return_value=["codex"]),
                  patch.dict(os.environ, {"APPDATA": str(base / "cfg"), "XDG_CONFIG_HOME": str(base / "cfg")})):
            p.start()
            self.addCleanup(p.stop)
        for k in ("WAYPOINT_LOGBOOK", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CLI_HOME", "COPILOT_HOME", "NO_COLOR"):
            os.environ.pop(k, None)
        self.pipe = self.enterContext(create_pipe_input())
        self.out = io.StringIO()
        t = term.Term(unicode=True, color=False, width=100)
        self.ui = widgets.UI(t, render.console(t, self.out), self.pipe, DummyOutput())

    def wizard(self, **kw):
        from waypoint.tui import wizard
        w = wizard.Wizard(home=self.home, **kw)
        return wizard, w

    def keys(self, *k):
        self.pipe.send_text("".join(k))

    def test_logbook_choose_a_folder_and_make_it_git(self):
        wizard, w = self.wizard()
        self.keys(DOWN, ENTER, "D:/elsewhere" if os.name == "nt" else "/elsewhere", ENTER, ENTER)
        self.assertEqual(wizard.screen_logbook(self.ui, w), "next")
        self.assertEqual(w.lb, Path("D:/elsewhere" if os.name == "nt" else "/elsewhere").resolve())
        self.assertTrue(w.git)  # "Yes" is pre-picked
        self.assertIn("one note per project", self.out.getvalue())

    def test_logbook_in_use_by_the_installed_rule_is_the_default(self):
        from waypoint import cli, rules
        used = self.home.parent / "my logbook"
        cli.init_logbook(used, roots=[str(self.code)])
        rule = rules.claude_rule_file(self.home)
        rule.parent.mkdir(parents=True)
        rule.write_text(rules.claude_file_text(used), encoding="utf-8")
        wizard, w = self.wizard()
        self.keys(ENTER, ENTER)  # accept the default logbook, then "Yes" to git
        self.assertEqual(wizard.screen_logbook(self.ui, w), "next")
        self.assertEqual(w.lb, used.resolve())  # not a new, empty home/waypoint-logbook

    def test_roots_n_adds_a_folder(self):
        wizard, w = self.wizard(lb=self.lb)
        extra = self.home / "work"
        self.keys("n", str(extra), ENTER, ENTER)
        self.assertEqual(wizard.screen_roots(self.ui, w), "next")
        self.assertEqual(w.roots, [str(self.code), str(extra.resolve())])  # home/code found and ticked; home not

    def test_projects_search_ignore_and_keys(self):
        f = self.home / ".claude" / "projects" / "slug" / "b.jsonl"
        f.write_text(json.dumps({"type": "user", "cwd": str(self.code / "beta")}) + "\n", encoding="utf-8")
        when = time.time() - 3600  # an hour ago: never "in the future" through file-time rounding
        os.utime(f, (when, when))
        wizard, w = self.wizard(lb=self.lb, roots=[str(self.code)])
        self.keys("/", "beta", ENTER, "i", ENTER)  # find beta, ignore it, go on
        self.assertEqual(wizard.screen_projects(self.ui, w), "next")
        self.assertEqual([c.label for c in w.projects], ["alpha"])
        self.assertEqual(w.ignore, [str((self.code / "beta").resolve())])
        self.assertEqual(w.setup().track, [str((self.code / "alpha").resolve())])

    def test_projects_rescan_when_logbook_changes_with_same_roots(self):
        from waypoint.tui import wizard
        w = wizard.Wizard(home=self.home, lb=self.lb, roots=[str(self.code)])
        with patch("waypoint.tui.actions.project_choices", return_value=[]) as scan:
            self.keys(ENTER)
            wizard.screen_projects(self.ui, w)
            w.lb = self.home / "another-logbook"
            self.keys(ENTER)
            wizard.screen_projects(self.ui, w)
        self.assertEqual(scan.call_count, 2)

    def test_logbook_from_environment_is_labeled_and_review_warns_on_mismatch(self):
        from waypoint.tui import wizard
        with patch.dict(os.environ, {"WAYPOINT_LOGBOOK": str(self.lb)}):
            wizard_, w = self.wizard()
            seen = []
            choose = self.ui.choose
            self.ui.choose = lambda model: seen.extend(c.label for c in model.choices) or "quit"
            self.assertEqual(wizard_.screen_logbook(self.ui, w), "quit")
            self.ui.choose = choose
            self.assertTrue(any(label.startswith("From WAYPOINT_LOGBOOK:") for label in seen))
        other = self.home / "other-logbook"
        with patch.dict(os.environ, {"WAYPOINT_LOGBOOK": str(other)}):
            w.lb = self.lb
            self.keys(ESC)
            self.assertEqual(wizard.screen_review(self.ui, w), "quit")
            out = " ".join(self.out.getvalue().split())  # Rich wraps at the console width
            self.assertIn(f"WAYPOINT_LOGBOOK points to {other.resolve()}; AI tools will open that logbook until you unset it", out)

    def test_ticked_project_hidden_by_search_is_still_tracked(self):
        wizard, w = self.wizard(lb=self.lb, roots=[str(self.code)])
        self.keys("/", "zzz", ENTER, ENTER)  # alpha (ticked) is filtered out of view
        self.assertEqual(wizard.screen_projects(self.ui, w), "next")
        self.assertEqual(w.setup().track, [str((self.code / "alpha").resolve())])

    def test_tools_space_unticks(self):
        wizard, w = self.wizard(lb=self.lb)
        codex = [c.key for c in wizard.actions.tool_choices()].index("codex")
        self.keys(DOWN * codex, SPACE, ENTER)
        self.assertEqual(wizard.screen_tools(self.ui, w), "next")
        self.assertEqual(w.tools, [])
        self.assertEqual(wizard.screen_where(self.ui, w), "skip")  # nothing to place

    def test_where_per_project_picks_repos_and_codex_permission(self):
        from waypoint.tui.model import Choice
        alpha = str((self.code / "alpha").resolve())
        wizard, w = self.wizard(lb=self.lb, tools=["codex"], projects=[Choice(alpha, "alpha", on=True)])
        self.keys(DOWN, ENTER, ENTER, DOWN, ENTER)  # Per project; alpha (ticked); "No" to the sandbox change
        self.assertEqual(wizard.screen_where(self.ui, w), "next")
        self.assertEqual(w.where, {"codex": [Path(alpha)]})
        self.assertFalse(w.codex_writable)

    def test_review_back_and_quit_write_nothing(self):
        codex_config = self.home / ".codex" / "config.toml"
        before = codex_config.read_bytes() if codex_config.exists() else None
        remembered = Path(__import__("waypoint").config.user_dir()) / "logbook"
        wizard, w = self.wizard(lb=self.lb, roots=[str(self.code)], tools=["codex"])
        self.keys(ENTER, LEFT)  # unfold the first step, then back
        self.assertEqual(wizard.screen_review(self.ui, w), "back")
        self.keys(ESC)
        self.assertEqual(wizard.screen_review(self.ui, w), "quit")
        self.assertFalse(self.lb.exists())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())
        self.assertEqual(codex_config.read_bytes() if codex_config.exists() else None, before)
        self.assertFalse(remembered.exists())

    def test_done_refreshes_and_offers_the_dashboard(self):
        from waypoint import cli
        cli.init_logbook(self.lb, roots=[str(self.code)])
        wizard, w = self.wizard(lb=self.lb)
        self.keys(ENTER)
        with patch("webbrowser.open", return_value=True) as opened:
            self.assertEqual(wizard.screen_done(self.ui, w), "next")
        self.assertTrue((self.lb / "chart.html").exists())
        self.assertEqual(opened.call_args[0][0], (self.lb / "chart.html").resolve().as_uri())
        self.assertIn("Setup complete", self.out.getvalue())

    def test_full_run_writes_after_apply(self):
        from waypoint.tui import wizard
        self.keys(ENTER, DOWN, ENTER,  # New logbook; not a git repo
                  ENTER,               # roots: home/code
                  ENTER,               # projects: alpha pre-ticked
                  ENTER,               # tools: codex
                  ENTER, ENTER,        # global; let Codex write the logbook
                  UP, ENTER,           # Review: jump to Apply
                  DOWN, ENTER)         # Done: don't open the browser
        self.assertEqual(wizard.run(self.ui, self.home), 0)
        self.assertTrue((self.lb / "projects" / "alpha.md").exists())
        self.assertIn(rules.BEGIN, (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertTrue(rules.codex_can_write(self.home / ".codex" / "config.toml", self.lb))
        self.assertTrue((self.lb / "chart.html").exists())
        text = self.out.getvalue()
        self.assertIn("✓ Track alpha", text)
        self.assertIn("1/1 AI tools connected", text)

    def test_quit_before_apply_writes_nothing(self):
        from waypoint.tui import wizard
        self.keys(ENTER, DOWN, ENTER, ENTER, CTRL_C)  # quit on the Projects screen
        self.assertEqual(wizard.run(self.ui, self.home), 1)
        self.assertFalse(self.lb.exists())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())
        self.assertIn("nothing was written", self.out.getvalue())


if __name__ == "__main__":
    unittest.main()
