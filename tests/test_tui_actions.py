"""Screen actions (spec Part C): choices and plans come from engine functions only. stdlib only, runs without the
tui extra. Hermetic: temp home, temp logbook, temp repos; `clis.detected` patched; no real config is read or written."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from waypoint import cli, rules
from waypoint.tui import actions, term  # term: glyph tables

DAY = 86400


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()  # resolved: macOS /private, Windows 8.3 names
        self.home, self.code, self.lb = base / "home", base / "code", base / "lb"
        for d in (self.home / ".claude" / "projects" / "slug", self.home / ".codex", self.code / "alpha",
                  self.code / "beta", self.code / "gamma"):
            d.mkdir(parents=True)
        self.now = time.time()
        detected = patch("waypoint.clis.detected", return_value=["codex"])
        command = patch("waypoint.clis.server_command", return_value="waypoint")  # CI has no `waypoint` on PATH
        command.start()
        self.addCleanup(command.stop)
        detected.start()
        self.addCleanup(detected.stop)
        env = patch.dict(os.environ, {"APPDATA": str(base / "cfg"), "XDG_CONFIG_HOME": str(base / "cfg")})
        env.start()
        self.addCleanup(env.stop)
        for k in ("WAYPOINT_LOGBOOK", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CLI_HOME", "COPILOT_HOME"):
            os.environ.pop(k, None)

    def session(self, sid, folder, days_ago):
        f = self.home / ".claude" / "projects" / "slug" / f"{sid}.jsonl"
        f.write_text(json.dumps({"type": "user", "cwd": str(folder)}) + "\n", encoding="utf-8")
        when = self.now - days_ago * DAY
        os.utime(f, (when, when))

    def make_logbook(self):
        cli.init_logbook(self.lb, roots=[str(self.code)])


class Choices(Base):
    def test_projects_before_the_logbook_exists_use_the_screens_roots(self):
        self.session("a", self.code / "alpha", 2)
        self.session("b", self.code / "beta", 25)  # untracked (≤ 30 days) but not active (> 21 days)
        rows = actions.project_choices(self.lb, self.home, roots=[str(self.code)], now=self.now)
        self.assertFalse(self.lb.exists())  # preview only: nothing written
        self.assertEqual([c.label for c in rows], ["alpha", "beta"])  # newest first
        self.assertEqual([c.on for c in rows], [True, False])
        self.assertEqual(Path(rows[0].key), (self.code / "alpha").resolve())
        self.assertIn("2 days ago", rows[0].detail)

    def test_tracked_projects_are_ticked_and_locked(self):
        self.make_logbook()
        cli.add_project(self.lb, self.code / "gamma")
        rows = actions.project_choices(self.lb, self.home, now=self.now)
        gamma = next(c for c in rows if c.label == "gamma")
        self.assertTrue(gamma.on and gamma.locked)
        self.assertIn("tracked", gamma.detail)

    def test_tools_detected_ticked_others_dimmed(self):
        rows = {c.key: c for c in actions.tool_choices()}
        self.assertTrue(rows["codex"].on and not rows["codex"].locked)
        self.assertEqual(rows["codex"].detail, "Supported")
        self.assertTrue(rows["claude-code"].locked)
        self.assertIn("not on PATH", rows["claude-code"].detail)
        self.assertEqual(rows["gemini-cli"].detail, "Preview · not on PATH")
        self.assertIn("print config: waypoint connect cursor", rows["cursor"].detail)

    def test_where_shows_method_and_name_clash(self):
        self.assertEqual(actions.where_detail("codex", self.lb, self.home), "rule: copy · MCP: `waypoint`")
        self.assertEqual(actions.where_detail("claude-code", self.lb, self.home).split(" · ")[0], "rule: import")
        (self.home / ".codex" / "config.toml").write_text('[mcp_servers.waypoint]\nurl = "https://beacon.example"\n',
                                                         encoding="utf-8")
        self.assertIn("`waypoint-local` (`waypoint` is another server)", actions.where_detail("codex", self.lb, self.home))
        repo = self.code / "alpha"
        want = "symlink" if rules.can_symlink() else "copy"
        self.assertEqual(actions.rule_method("codex", self.lb, repo), want)

    def test_refused_rule_method_reports_blocked_note(self):
        repo = self.code / "alpha"
        with patch("waypoint.tui.actions.project.plan_rule", return_value=rules.Change(repo, "refuse", note="owned")):
            self.assertEqual(actions.rule_method("codex", self.lb, repo), "blocked: owned")


    def test_manager_rows_and_tool_rows(self):
        self.make_logbook()
        cli.add_project(self.lb, self.code / "alpha")
        self.session("b", self.code / "beta", 1)
        self.assertEqual([c.key for c in actions.manager_rows(self.lb, self.home, "tracked")], ["alpha"])
        self.assertEqual(actions.manager_rows(self.lb, self.home, "archived"), [])
        self.assertEqual([(c.label, c.on) for c in actions.manager_rows(self.lb, self.home, "untracked")], [("beta", False)])
        s = {"tools": {"codex": {"rule": "in-sync", "mcp": {"state": "missing"}, "scope": ["global"], "projects": []}}}
        rows = {c.key: c.detail for c in actions.tool_rows(s)}
        self.assertEqual(rows["codex"], "Supported · rule in-sync · MCP missing · global")
        self.assertIn("not on PATH", rows["claude-code"])


class Plans(Base):
    def test_wizard_plan_writes_nothing_until_applied(self):
        s = actions.Setup(self.lb, [str(self.code)], track=[str(self.code / "alpha")], tools={"codex": None})
        steps = actions.wizard_plan(s, self.home)
        before = {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        cfg = Path(os.environ["XDG_CONFIG_HOME"]).resolve()
        cfg_before = {p.relative_to(cfg): p.read_bytes() for p in cfg.rglob("*") if p.is_file()} if cfg.exists() else {}
        self.assertEqual([st.title for st in steps][:2], [f"Create the logbook in {self.lb}", "Track alpha"])
        self.assertFalse(self.lb.exists())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())
        self.assertEqual(before, {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob("*") if p.is_file()})
        self.assertEqual(cfg_before, {p.relative_to(cfg): p.read_bytes() for p in cfg.rglob("*") if p.is_file()} if cfg.exists() else {})
        results = list(actions.apply_steps(steps))
        self.assertEqual([err for _, err in results], [None] * len(results))
        self.assertTrue((self.lb / "projects" / "alpha.md").exists())
        self.assertFalse((self.lb / "projects" / "_scratch.md").exists())
        self.assertIn(rules.BEGIN, (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"))

    def test_bad_project_name_is_skipped_and_the_rest_applies(self):
        bad = self.code / "_scratch"
        bad.mkdir()
        s = actions.Setup(self.lb, [str(self.code)], track=[str(bad), str(self.code / "alpha")])
        steps = actions.wizard_plan(s, self.home)
        track_bad = next(st for st in steps if st.title == "Track _scratch")
        self.assertIn("can't start with _", track_bad.problem)
        results = dict((st.title, err) for st, err in actions.apply_steps(steps))
        self.assertTrue(results["Track _scratch"].startswith("skipped: "))
        self.assertIsNone(results["Track alpha"])
        self.assertTrue((self.lb / "projects" / "alpha.md").exists())

    def test_refused_change_skips_that_step_only(self):
        self.make_logbook()
        own = self.home / ".claude" / "rules" / "waypoint.md"
        own.parent.mkdir(parents=True)
        own.write_text("my own rule file\n", encoding="utf-8")  # not Waypoint's: refused
        steps = actions.plan_connect("claude-code", self.lb, self.home) + actions.plan_connect("codex", self.lb, self.home)
        self.assertIn("isn't Waypoint's", steps[0].problem)
        results = [err for _, err in actions.apply_steps(steps)]
        self.assertTrue(results[0].startswith("skipped: "))
        self.assertIsNone(results[1])
        self.assertEqual(own.read_text(encoding="utf-8"), "my own rule file\n")
        self.assertTrue((self.home / ".codex" / "AGENTS.md").exists())

    def test_wizard_connects_tools_in_one_repo_with_one_exclude_snapshot(self):
        repo = self.code / "alpha"
        import subprocess
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        steps = actions.wizard_plan(actions.Setup(self.lb, [str(self.code)], tools={
            "claude-code": [repo], "codex": [repo]}), self.home)
        connects = [st for st in steps if st.tool]
        self.assertEqual(len(connects), 2)
        self.assertEqual(sum(any(ch.target.name == "exclude" for ch in st.changes) for st in connects), 1)
        self.assertTrue(any("Claude Code" in st.title for st in connects))
        self.assertTrue(any("Codex" in st.title for st in connects))
        results = list(actions.apply_steps(steps))
        self.assertTrue(all(err is None for _, err in results), results)
        self.assertTrue((repo / ".claude" / "rules" / "waypoint.md").exists())
        self.assertTrue((repo / ".codex" / "config.toml").exists())
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True,
                                text=True, check=True).stdout
        self.assertEqual(status.strip(), "")

    def test_first_tool_refused_in_a_repo_still_excludes_the_others_files(self):
        repo = self.code / "alpha"
        import subprocess
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        own = repo / ".claude" / "rules" / "waypoint.md"
        own.parent.mkdir(parents=True)
        own.write_text("my own rule file\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "own"],
                       check=True)
        steps = actions.wizard_plan(actions.Setup(self.lb, [str(self.code)], tools={
            "claude-code": [repo], "codex": [repo]}), self.home)
        results = {st.title: err for st, err in actions.apply_steps(steps)}
        claude = next(t for t in results if "Claude Code" in t)
        codex = next(t for t in results if "Codex" in t)
        self.assertTrue(results[claude].startswith("skipped: "))
        self.assertIsNone(results[codex])
        self.assertEqual(own.read_text(encoding="utf-8"), "my own rule file\n")
        self.assertTrue((repo / "AGENTS.md").exists())
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
                                capture_output=True, text=True, check=True).stdout
        self.assertEqual(status.strip(), "")

    def test_a_failing_run_stops_that_step_only(self):
        def boom():
            raise OSError("disk full")
        ran = []
        steps = [actions.Step("first", run=boom), actions.Step("second", run=lambda: ran.append(1))]
        self.assertEqual([err for _, err in actions.apply_steps(steps)], ["disk full", None])
        self.assertEqual(ran, [1])

    def test_unexpected_failure_is_reported_and_later_steps_run(self):
        ran = []
        def boom():
            raise RuntimeError("unexpected")
        self.assertEqual([err for _, err in actions.apply_steps([actions.Step("first", run=boom),
                                                                  actions.Step("second", run=lambda: ran.append(1))])],
                         ["unexpected", None])
        self.assertEqual(ran, [1])

    def test_failed_logbook_creation_skips_dependent_steps(self):
        s = actions.Setup(self.lb, [str(self.code)], track=[str(self.code / "alpha")],
                          ignore=[str(self.code / "beta")], tools={"codex": None}, git=True)
        steps = actions.wizard_plan(s, self.home)
        with patch("waypoint.tui.actions.cli.init_logbook", side_effect=OSError("no disk")):
            results = list(actions.apply_steps(steps))
        errors = {st.title: err for st, err in results}
        self.assertEqual(errors[f"Create the logbook in {self.lb}"], "no disk")
        self.assertTrue(all(err == "skipped: the logbook wasn't created" for title, err in errors.items()
                            if title.startswith(("Track ", "Ignore ", "Connect ", "Remember the logbook", "Make the logbook"))))

    def test_inline_ignore_titles_match_plan_ignore(self):
        s = actions.Setup(self.lb, [str(self.code)], ignore=[str(self.code / "beta")])
        self.assertEqual(actions.wizard_plan(s, self.home)[-1].title, "Ignore beta")

    def test_git_repo_is_created_only_when_steps_apply_and_failure_is_reported(self):
        s = actions.Setup(self.lb, [str(self.code)], git=True)
        steps = actions.wizard_plan(s, self.home)
        self.assertFalse((self.lb / ".git").exists())
        results = list(actions.apply_steps(steps))
        self.assertIsNone(results[0][1])
        self.assertTrue((self.lb / ".git").is_dir())
        other = self.lb.parent / "failed-lb"
        failed_steps = actions.wizard_plan(actions.Setup(other, [str(self.code)], git=True), self.home)
        with patch("waypoint.tui.actions.cli._git", return_value=type("R", (), {"returncode": 1, "stderr": "git broke", "stdout": ""})()):
            results = list(actions.apply_steps(failed_steps))
        self.assertIn("git broke", results[-1][1])

    def test_manager_project_steps(self):
        self.make_logbook()
        cli.add_project(self.lb, self.code / "alpha")
        f = self.lb / "projects" / "alpha.md"
        f.write_text(f.read_text(encoding="utf-8") + "\n### 2026-10-01 — codex\n- did a thing\n", encoding="utf-8")
        remove = actions.plan_remove(self.lb, "alpha")
        self.assertIn("1 log entry", remove.lines[0])
        self.assertIn("archive it instead", remove.lines[1])
        self.assertIn("is tracked by", actions.plan_ignore(self.lb, self.code / "alpha").problem)
        list(actions.apply_steps([actions.plan_archive(self.lb, "alpha")]))
        self.assertTrue((self.lb / "projects" / "_archive" / "alpha.md").exists())
        list(actions.apply_steps([actions.plan_restore(self.lb, "alpha")]))
        self.assertTrue(f.exists())
        list(actions.apply_steps([actions.plan_ignore(self.lb, self.code / "beta"),
                                  actions.plan_track(self.lb, self.code / "gamma")]))
        self.assertIn(str((self.code / "beta").resolve()),
                      (self.lb / "projects" / "_ignore.txt").read_text(encoding="utf-8"))
        self.assertTrue((self.lb / "projects" / "gamma.md").exists())

    def test_disconnect_and_fix_drift_reuse_the_engine_plans(self):
        self.make_logbook()
        list(actions.apply_steps(actions.plan_connect("codex", self.lb, self.home)))
        agents = self.home / ".codex" / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8").replace("Waypoint", "Waypoint (old)", 1), encoding="utf-8")
        self.assertEqual(rules.check(self.home, self.lb, ("codex",))["codex"], "drifted")
        list(actions.apply_steps(actions.plan_fix_drift(self.lb, self.home, ["codex"])))
        self.assertEqual(rules.check(self.home, self.lb, ("codex",))["codex"], "in-sync")
        list(actions.apply_steps(actions.plan_disconnect("codex", self.home)))
        self.assertNotIn(rules.BEGIN, agents.read_text(encoding="utf-8") if agents.exists() else "")

    def test_review_lines_use_the_plan_glyphs(self):
        self.make_logbook()
        st = actions.plan_connect("codex", self.lb, self.home)[0]
        lines = actions.step_lines(st, term.UNICODE)
        self.assertTrue(any(l.startswith("+ create ") and "AGENTS.md" in l for l in lines))
        self.assertTrue(any(l.startswith("+++ ") for l in actions.diff_lines(st)))

    def test_refresh_writes_the_dashboard(self):
        self.make_logbook()
        kpi = actions.refresh(self.lb, self.home)
        self.assertEqual(kpi["tracked"], 0)
        self.assertTrue((self.lb / "chart.html").exists())


if __name__ == "__main__":
    unittest.main()
