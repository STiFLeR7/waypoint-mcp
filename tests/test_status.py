"""`waypoint status` (spec Part A): read-only, never crashes on a half-set-up machine."""
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from waypoint import cli, config, project, rules, status
from waypoint.mcp import Server


class Status(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home, self.lb = base / "home", base / "lb"
        (self.lb / "projects" / "_archive").mkdir(parents=True)
        (self.home / ".codex").mkdir(parents=True)
        (self.lb / "waypoint.toml").write_text(f'roots = ["{base.as_posix()}"]\n', encoding="utf-8")
        (self.lb / "GLOBAL-RULES.md").write_text(rules.render(self.lb), encoding="utf-8")
        p = patch("waypoint.clis.detected", return_value=["codex"])  # hermetic: as if only Codex were on PATH
        p.start()
        self.addCleanup(p.stop)

    def collect(self, lb="default"):
        return status.collect(self.lb if lb == "default" else lb, "--logbook", self.home)

    def test_eod_state_never_shows_the_address(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.dict(os.environ, {"APPDATA": tmp.name, "XDG_CONFIG_HOME": tmp.name}):
            f = config.user_dir() / config.EOD_FILE
            self.assertEqual(self.collect()["eod"], "none")
            f.parent.mkdir(parents=True)
            f.write_text("secret@x.com\n",encoding="utf-8")
            s = self.collect()
            self.assertEqual(s["eod"], "remembered")
            self.assertNotIn("secret@x.com", json.dumps(s) + status.render_text(s))
            self.assertIn("End-of-day email  address remembered", status.render_text(s))
            f.write_text("not an address", encoding="utf-8")
            s = self.collect()
            self.assertEqual(s["eod"], "invalid")
            self.assertIn("eod-email-invalid", [p["id"] for p in s["problems"]])

    def test_no_logbook_is_a_problem_not_a_crash(self):
        s = self.collect(lb=None)
        self.assertIsNone(s["counts"])
        self.assertIn("waypoint setup", s["problems"][0]["fix"])
        self.assertIn("No logbook found", status.render_text(s))

    def test_no_logbook_text_names_the_folder_checked(self):
        s = status.collect(None, "WAYPOINT_LOGBOOK", self.home, checked=Path("/nowhere/lb"))
        self.assertIn("nowhere", s["problems"][0]["what"])
        self.assertIn("(from WAYPOINT_LOGBOOK)", s["problems"][0]["what"])
        out = io.StringIO()
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        with redirect_stdout(out):
            cli.main(["status", "--logbook", str(empty), "--home", str(self.home)])
        self.assertIn(f"No logbook found at {empty.resolve()} (from --logbook)", out.getvalue())

    def test_missing_logbook_rule_file_is_a_problem(self):
        (self.lb / "GLOBAL-RULES.md").unlink()
        p = next(p for p in self.collect()["problems"] if p["id"] == "logbook-rule-missing")
        self.assertEqual(p["what"], "The logbook has no GLOBAL-RULES.md")
        self.assertEqual(p["fix"], f"run: waypoint init {self.lb}")

    def test_counts_and_rule_states(self):
        (self.lb / "projects" / "_archive" / "old.md").write_text("# old\n- Path: /x\n\n## Log\n", encoding="utf-8")
        s = self.collect()
        self.assertEqual(s["counts"]["archived"], 1)
        self.assertEqual(s["tools"]["codex"]["rule"], "missing")
        self.assertTrue(any("Codex rule is missing" in p["what"] for p in s["problems"]))

    def test_counts_projects_whose_now_is_behind(self):
        (self.lb / "projects" / "a.md").write_text("# a\n- Path: /x/a\n\n## Now\n- Updated: 2026-01-01\n\n## Log\n"
                                                  "### 2026-02-01 — codex\n- later work\n", encoding="utf-8")
        s = self.collect()
        self.assertEqual(s["counts"]["behind"], 1)
        self.assertIn("1 with Now behind the log", status.render_text(s))

    def test_foreign_waypoint_server_and_unreadable_config(self):
        cfg = self.home / ".codex" / "config.toml"
        cfg.write_text('[mcp_servers.waypoint]\nurl = "https://example.invalid/mcp"\n', encoding="utf-8")
        s = self.collect()
        self.assertEqual(s["tools"]["codex"]["mcp"]["state"], "not ours")
        self.assertTrue(any("uses the name `waypoint`" in p["what"] for p in s["problems"]))
        self.assertNotIn("example.invalid", json.dumps(s))  # never echo a remote URL
        cfg.write_text("not = [toml", encoding="utf-8")
        s = self.collect()
        self.assertEqual(s["tools"]["codex"]["mcp"]["state"], "unreadable")
        self.assertIsNone(s["tools"]["codex"]["can_write_logbook"])
        self.assertTrue(any("config.toml" in p["what"] for p in s["problems"]))

    def test_malformed_logbook_config_still_lists_tools(self):
        (self.lb / "waypoint.toml").write_text("roots = [", encoding="utf-8")
        s = self.collect()
        self.assertIsNone(s["counts"])
        self.assertTrue(any("waypoint.toml" in p["what"] for p in s["problems"]))
        self.assertIn("codex", s["tools"])
        self.assertIn("Problems", status.render_text(s))

    def test_malformed_config_still_reports_real_rule_states(self):
        (self.home / ".codex" / "AGENTS.md").write_text(rules.render(self.lb), encoding="utf-8")
        (self.lb / "waypoint.toml").write_text("roots = [", encoding="utf-8")
        self.assertEqual(self.collect()["tools"]["codex"]["rule"], "in-sync")
        with patch("waypoint.status.rules.check", side_effect=OSError("x")):
            self.assertEqual(self.collect()["tools"]["codex"]["rule"], "unknown")

    def test_codex_sandbox_permission(self):
        cfg = self.home / ".codex" / "config.toml"
        cfg.write_text("", encoding="utf-8")
        self.assertFalse(self.collect()["tools"]["codex"]["can_write_logbook"])
        cfg.write_text(f'[sandbox_workspace_write]\nwritable_roots = [{json.dumps(str(self.lb.parent))}]\n',
                       encoding="utf-8")
        self.assertTrue(self.collect()["tools"]["codex"]["can_write_logbook"])

    def test_registered_command_off_path_is_flagged(self):
        entry = {"command": "waypoint", "args": ["mcp"]}
        with patch("waypoint.status.clis.mcp_entries", return_value=({"waypoint": entry, "waypoint-local": None}, None)), \
                patch("waypoint.status.shutil.which", return_value=None):
            s = self.collect()
        self.assertFalse(s["tools"]["codex"]["mcp"]["resolves"])
        self.assertTrue(any("isn't on PATH" in p["what"] for p in s["problems"]))

    def test_cli_text_and_json(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["status", "--logbook", str(self.lb), "--json", "--home", str(self.home)])
        self.assertEqual(json.loads(out.getvalue())["schema"], 1)
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["status", "--logbook", str(self.lb), "--home", str(self.home)])
        self.assertIn("Logbook", out.getvalue())

    def test_status_shows_when_hooks_last_ran(self):
        from waypoint import clis, hook
        cfg = self.home.parent / "cfg"
        with mock.patch.dict(os.environ, {"APPDATA": str(cfg), "XDG_CONFIG_HOME": str(cfg)}), \
                mock.patch.object(clis, "detected", return_value=["codex", "claude-code"]):
            hook.mark_seen("codex")
            s = self.collect()
            self.assertRegex(s["tools"]["codex"]["hooks"], r"^\d{4}-\d{2}-\d{2}T")
            self.assertIsNone(s["tools"]["claude-code"]["hooks"])
            text = status.render_text(s)
        self.assertRegex(text, r"Codex .*· hooks \d{4}-\d{2}-\d{2}")
        self.assertRegex(text, r"Claude Code .*· hooks never ran")
        self.assertFalse([p for p in s["problems"] if "hook" in p["id"]])

    def test_waypoint_local_counts_as_registered(self):
        (self.home / ".codex" / "config.toml").write_text(
            '[mcp_servers.waypoint]\nurl = "https://example.invalid/mcp"\n'
            '[mcp_servers.waypoint-local]\ncommand = "waypoint"\nargs = ["mcp"]\n', encoding="utf-8")
        with patch("waypoint.status.shutil.which", return_value="/bin/waypoint"):
            s = self.collect()
        m = s["tools"]["codex"]["mcp"]
        self.assertEqual((m["state"], m["name"]), ("registered", "waypoint-local"))
        self.assertNotIn("mcp-not-ours", [p["id"] for p in s["problems"]])
        self.assertNotIn("example.invalid", json.dumps(s))

    def make_repo(self):
        repo = Path(self.tmp.name) / "zebra"
        repo.mkdir()
        (self.lb / "projects" / "zebra.md").write_text(f"# zebra\n- Path: {repo}\n- Status: active\n\n## Log\n",
                                                       encoding="utf-8")
        return repo

    def test_per_project_installs_are_listed_with_their_method(self):
        repo = self.make_repo()
        with patch.object(rules, "can_symlink", return_value=False):
            rules.apply([project.plan_rule("codex", repo, self.lb)])
        s = self.collect()
        t = s["tools"]["codex"]
        self.assertEqual(t["projects"], [{"project": "zebra", "folder": str(repo), "rule": "in-sync", "method": "copy",
                                          "mcp": "missing"}])
        self.assertEqual((t["method"], t["scope"]), ("none", ["project"]))
        self.assertNotIn("rule-missing", [p["id"] for p in s["problems"]])
        self.assertIn("in zebra: rule in-sync (copy)", status.render_text(s))

    def test_global_and_project_rule_together_is_flagged(self):
        repo = self.make_repo()
        rules.apply(rules.plan_install(self.home, self.lb, tools=("codex",)))
        rules.apply([project.plan_rule("codex", repo, self.lb, copy=True)])
        s = self.collect()
        self.assertEqual(s["tools"]["codex"]["method"], "copy")
        self.assertEqual(s["tools"]["codex"]["scope"], ["global", "project"])
        self.assertIn("rule-twice", [p["id"] for p in s["problems"]])

    def test_broken_link_is_a_problem_and_connect_repoints_it(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        repo = self.make_repo()
        old = Path(self.tmp.name) / "old logbook"
        old.mkdir()
        (old / "GLOBAL-RULES.md").write_text("x", encoding="utf-8")
        rules.apply([rules.Change(repo / "AGENTS.md", "link", None, str(old / "GLOBAL-RULES.md"))])
        shutil.rmtree(old)
        s = self.collect()
        self.assertEqual(s["tools"]["codex"]["projects"][0]["rule"], "broken")
        p = next(p for p in s["problems"] if p["id"] == "link-broken")
        self.assertIn(f'--project "{repo}"', p["fix"])
        rules.apply([project.plan_rule("codex", repo, self.lb)])
        self.assertEqual(project.rule_state("codex", repo, self.lb), ("in-sync", "symlink"))

    def test_drifted_project_copy_is_a_problem(self):
        repo = self.make_repo()
        (repo / "GEMINI.md").write_text(f"{rules.BEGIN}\nan older waypoint rule\n{rules.END}\n", encoding="utf-8")
        p = next(p for p in self.collect()["problems"] if p["id"] == "project-rule-drifted")
        self.assertEqual((p["subject"], p["fix"]), ("gemini-cli", f'run: waypoint connect gemini-cli --project "{repo}"'))

    def test_codex_repo_config_in_an_untrusted_folder_is_flagged(self):
        repo = self.make_repo()
        with patch("waypoint.clis.server_command", return_value="waypoint"):
            rules.apply([project.plan_codex_config(repo, self.lb)])
        self.assertIn("codex-untrusted", [p["id"] for p in self.collect()["problems"]])

    def test_mcp_status_hides_project_name_and_path(self):
        repo = Path(self.tmp.name) / "zzSECRETzz-folder"
        repo.mkdir()
        (self.lb / "projects" / "zzSECRETzz-project.md").write_text(
            f"# zzSECRETzz-project\n- Path: {repo.resolve()}\n- Status: active\n\n## Log\n", encoding="utf-8")
        with patch("waypoint.cli.subprocess.run"):
            with redirect_stdout(io.StringIO()):
                try:
                    cli.main(["connect", "codex", "--project", str(repo), "--yes", "--no-mcp",
                              "--logbook", str(self.lb), "--home", str(self.home)])
                except SystemExit as e:
                    self.assertEqual(e.code, 0)
        result = Server(self.lb, home=self.home).status({})
        rendered = json.dumps(result)
        self.assertNotIn("zzSECRETzz", rendered)
        self.assertNotIn(str(repo.resolve()), rendered)
        self.assertNotIn(json.dumps(str(repo.resolve()))[1:-1], rendered)

    def test_project_status_errors_are_per_cli_and_method_none_does_not_count(self):
        one, two = self.make_repo(), Path(self.tmp.name) / "two"
        two.mkdir()
        (self.lb / "projects" / "two.md").write_text(f"# two\n- Path: {two}\n- Status: active\n\n## Log\n", encoding="utf-8")
        (self.lb / "GLOBAL-RULES.md").unlink()
        original = project.rule_state

        def state(cli_id, repo, lb):
            if repo.resolve() == one.resolve():
                raise PermissionError("denied")
            return original(cli_id, repo, lb)

        with patch.object(project, "rule_state", side_effect=state):
            result = status.collect(self.lb, "--logbook", self.home)
        rows = result["tools"]["codex"]["projects"]
        self.assertEqual(next(row for row in rows if row["project"] == "zebra")["rule"], "unknown")
        self.assertEqual(result["tools"]["codex"]["scope"], [])
        self.assertIn("logbook-rule-missing", [p["id"] for p in result["problems"]])

    def test_detected_tool_with_no_project_installs_has_no_project_rows(self):
        for name in ("one", "two", "three"):
            repo = Path(self.tmp.name) / name
            repo.mkdir()
            (self.lb / "projects" / f"{name}.md").write_text(
                f"# {name}\n- Path: {repo}\n- Status: active\n\n## Log\n", encoding="utf-8")
        result = self.collect()
        self.assertEqual(result["tools"]["codex"]["projects"], [])
        self.assertEqual(Server(self.lb, home=self.home).status({})["tools"]["codex"]["projects"]["count"], 0)

    def test_project_fix_quotes_folder_with_spaces(self):
        repo = Path(self.tmp.name) / "folder with spaces"
        repo.mkdir()
        (self.lb / "projects" / "spaced.md").write_text(f"# spaced\n- Path: {repo}\n- Status: active\n\n## Log\n", encoding="utf-8")
        (repo / "GEMINI.md").write_text(f"{rules.BEGIN}\nold\n{rules.END}\n", encoding="utf-8")
        s = self.collect()
        p = next(p for p in s["problems"] if p["id"] == "project-rule-drifted")
        self.assertIn(f'--project "{repo}"', p["fix"])

    def test_one_bad_project_install_is_unknown_without_hiding_the_next(self):
        bad = self.make_repo()
        good = Path(self.tmp.name) / "yak"
        good.mkdir()
        (self.lb / "projects" / "yak.md").write_text(f"# yak\n- Path: {good}\n- Status: active\n\n## Log\n", encoding="utf-8")
        rules.apply([project.plan_rule("codex", good, self.lb, copy=True)])
        original = project.rule_state

        def state(cli_id, repo, lb):
            if repo == bad:
                raise OSError("unreadable project")
            return original(cli_id, repo, lb)

        with patch.object(project, "rule_state", side_effect=state):
            installs = status._project_installs(self.lb, self.home)["codex"]
        self.assertEqual(next(p for p in installs if p["project"] == "zebra")["rule"], "unknown")
        self.assertTrue(any(p["project"] == "yak" for p in installs))

    def test_unreadable_project_only_adds_unknown_for_detected_tools(self):
        bad = self.make_repo()
        with patch("waypoint.clis.detected", return_value=["codex"]), \
                patch.object(project, "rule_state", side_effect=OSError("unreadable project")):
            installs = status._project_installs(self.lb, self.home)
        self.assertEqual(set(installs), {"codex"})
        self.assertEqual(installs["codex"][0]["rule"], "unknown")


if __name__ == "__main__":
    unittest.main()
