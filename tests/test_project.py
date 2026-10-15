"""Per-project install (spec Part B): rule methods, MCP entries in the repo, git hygiene, disconnect. stdlib only."""
import builtins
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from waypoint import cli, clis, project, rules

REAL_SERVER_COMMAND = clis.server_command  # Base patches it; Clis tests need the real one


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()  # macOS: /var -> /private/var, so paths compare as written
        self.home, self.lb, self.repo = base / "home", base / "lb", base / "code" / "zebra"
        for d in (self.home / ".codex", self.home / ".claude", self.repo):
            d.mkdir(parents=True)
        saved = {k: os.environ.get(k) for k in ("APPDATA", "XDG_CONFIG_HOME", "WAYPOINT_LOGBOOK")}
        self.addCleanup(self._restore_env, saved)
        os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"] = str(base / "cfg")
        os.environ.pop("WAYPOINT_LOGBOOK", None)
        for target, value in (("waypoint.clis.detected", []), ("waypoint.clis.server_command", "waypoint")):
            p = mock.patch(target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        cli.init_logbook(self.lb, roots=[str(base / "code")])
        (self.lb / "projects" / "zebra.md").write_text(
            f"# zebra\n- Path: {self.repo}\n- Status: active\n- Summary: s\n\n## Log\n", encoding="utf-8")

    @staticmethod
    def _restore_env(saved):
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def ours(self):
        return str((self.lb / "GLOBAL-RULES.md").resolve())

    def run_cli(self, *args, answers=None):
        out = io.StringIO()
        feed = iter(answers or [])
        with redirect_stdout(out), mock.patch.object(builtins, "input", lambda *_: next(feed)):
            try:
                cli.main(list(args))
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                out.write(str(e.code or ""))
        return code, out.getvalue()

    def fake_clis(self, rc=0):
        """Treat the temp home as the real one, put every CLI on PATH, record (argv, cwd) of what would run."""
        ran = []

        def run(argv, **kw):
            ran.append((argv, kw.get("cwd")))
            return mock.Mock(returncode=rc, stdout="", stderr="")
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(rules, "_real_home", lambda h: True))
        stack.enter_context(mock.patch("shutil.which", lambda exe: f"/bin/{exe}"))
        stack.enter_context(mock.patch("subprocess.run", run))
        self.addCleanup(stack.close)
        return ran


class Clis(Base):
    def test_argv_for_each_scope(self):
        self.assertEqual(clis.mcp_argv("claude-code", "add", project=True),
                         ("mcp", "add", "--scope", "local", "waypoint", "--", "waypoint", "mcp"))
        self.assertEqual(clis.mcp_argv("claude-code", "remove"), ("mcp", "remove", "--scope", "user", "waypoint"))
        self.assertEqual(clis.mcp_argv("gemini-cli", "remove", "waypoint-local", project=True),
                         ("mcp", "remove", "--scope", "project", "waypoint-local"))
        self.assertEqual(clis.mcp_argv("codex", "add", "waypoint", "/opt/wp/waypoint"),
                         ("mcp", "add", "waypoint", "--", "/opt/wp/waypoint", "mcp"))
        with self.assertRaises(ValueError):
            clis.mcp_argv("codex", "add", project=True)

    def test_claude_local_entry_is_found_under_the_repo_key(self):
        f = self.home / ".claude.json"
        mine = {"type": "stdio", "command": "waypoint", "args": ["mcp"]}
        f.write_text(json.dumps({"mcpServers": {}, "projects": {self.repo.as_posix(): {"mcpServers": {"waypoint": mine}}}}),
                     encoding="utf-8")
        self.assertIsNone(clis.mcp_entry(f)[0])  # not in user scope
        entry, err = clis.mcp_entry(f, project=self.repo)
        self.assertIsNone(err)
        self.assertTrue(clis.is_ours(entry))
        self.assertEqual(clis.mcp_entries(f, self.repo)[0], {"waypoint": mine, "waypoint-local": None})

    def test_choose_prefers_ours_then_the_first_free_name(self):
        beacon, mine = {"type": "http", "url": "https://x.example/mcp"}, {"command": "waypoint", "args": ["mcp"]}
        self.assertEqual(clis.choose({"waypoint": None, "waypoint-local": None}), (None, "waypoint"))
        self.assertEqual(clis.choose({"waypoint": beacon, "waypoint-local": None}), (None, "waypoint-local"))
        self.assertEqual(clis.choose({"waypoint": beacon, "waypoint-local": mine}), ("waypoint-local", None))
        self.assertEqual(clis.choose({"waypoint": beacon, "waypoint-local": beacon}), (None, None))
        self.assertEqual(clis.entries_of({"waypoint": "text"}), {"waypoint": {"value": "text"}, "waypoint-local": None})

    def test_off_path_uses_this_installations_script(self):  # Review Focus 5
        scripts = Path(self.tmp.name).resolve() / "scripts"
        scripts.mkdir()
        exe = scripts / ("waypoint.exe" if os.name == "nt" else "waypoint")
        exe.write_text("", encoding="utf-8")
        with mock.patch("waypoint.clis.sysconfig.get_path", lambda *a: str(scripts)), \
                mock.patch("waypoint.clis.shutil.which", lambda name: None):
            self.assertEqual(REAL_SERVER_COMMAND(), str(exe))
            self.assertTrue(clis.is_ours({"command": str(exe), "args": ["mcp"]}))
            self.assertFalse(clis.is_ours({"command": str(scripts.parent / "elsewhere" / exe.name), "args": ["mcp"]}))
        with mock.patch("waypoint.clis.shutil.which", lambda name: "/usr/bin/waypoint"):
            self.assertEqual(REAL_SERVER_COMMAND(), "waypoint")


class Plumbing(Base):
    def test_markers_are_parameters(self):
        marks = ("# waypoint:x:begin", "# waypoint:x:end")
        text = rules.edit_block("a = 1\n", "b = 2", *marks)
        self.assertEqual(text, "a = 1\n\n# waypoint:x:begin\nb = 2\n# waypoint:x:end\n")
        self.assertEqual(rules.edit_block(text, None, *marks), "a = 1\n")

    def test_drop_empty_deletes_a_file_that_held_only_the_block(self):
        f = self.repo / "GEMINI.md"
        f.write_text(rules.edit_block("", "rule"), encoding="utf-8")
        c = rules._block_change(f, None, "x", drop_empty=True)
        self.assertEqual((c.action, c.new), ("remove", None))
        rules.apply([c])
        self.assertFalse(f.exists())

    def test_link_change_creates_and_repoints(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        f = self.repo / "AGENTS.md"
        [(c, err)] = rules.apply([rules.Change(f, "link", None, self.ours())])
        self.assertIsNone(err)
        self.assertTrue(f.is_symlink())
        self.assertEqual(rules.link_state(f), self.ours())
        other = str(self.home / "GLOBAL-RULES.md")
        self.assertIn("changed since", rules.apply([rules.Change(f, "link", "somewhere-else", other)])[0][1])
        self.assertIsNone(rules.apply([rules.Change(f, "link", self.ours(), other)])[0][1])
        self.assertEqual(rules.link_state(f), other)
        self.assertIn("→", rules.Change(f, "link", None, other).diff())

    def test_link_repoint_failure_keeps_old_link(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        f = self.repo / "AGENTS.md"
        old = self.repo / "old.md"
        old.write_text("old\n", encoding="utf-8")
        new = self.repo / "new.md"
        new.write_text("new\n", encoding="utf-8")
        f.symlink_to(old)
        with mock.patch("waypoint.rules.os.replace", side_effect=OSError("replace failed")):
            [(change, error)] = rules.apply([rules.Change(f, "link", str(old), str(new))])
        self.assertIn("replace failed", error)
        self.assertTrue(f.is_symlink())
        self.assertEqual(f.resolve(), old.resolve())
        self.assertEqual(list(self.repo.glob(".AGENTS.md.*.tmp")), [])

    def test_block_update_through_symlink_preserves_link_and_backs_up_content(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        real = self.repo / "real.md"
        original = f"notes\n\n{rules.BEGIN}\nold rule\n{rules.END}\n"
        real.write_text(original, encoding="utf-8")
        link = self.repo / "AGENTS.md"
        link.symlink_to(real)
        c = rules._block_change(link, "new rule", "update")
        [(change, error)] = rules.apply([c])
        self.assertIsNone(error)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), real.resolve())
        self.assertIn("new rule", real.read_text(encoding="utf-8"))
        backups = list(self.repo.glob("AGENTS.md.bak-*")) + list(self.repo.glob("real.md.bak-*"))
        self.assertTrue(backups)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

    def test_block_removal_through_symlink_keeps_link(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        real = self.repo / "real.md"
        real.write_text(f"notes\n\n{rules.BEGIN}\nold rule\n{rules.END}\n", encoding="utf-8")
        link = self.repo / "AGENTS.md"
        link.symlink_to(real)
        [(change, error)] = rules.apply([rules._block_change(link, None, "remove")])
        self.assertIsNone(error)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), real.resolve())
        self.assertEqual(real.read_text(encoding="utf-8"), "notes\n")

    def test_empty_block_removal_through_symlink_keeps_link_and_clears_file(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        real = self.repo / "real.md"
        real.write_text(rules.edit_block("", "only rule"), encoding="utf-8")
        link = self.repo / "AGENTS.md"
        link.symlink_to(real)
        c = rules._block_change(link, None, "remove", drop_empty=True)
        [(change, error)] = rules.apply([c])
        self.assertIsNone(error)
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(encoding="utf-8"), "")
        self.assertTrue(real.exists())

    def test_content_change_link_to_global_rules_is_refused(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        link = self.repo / "AGENTS.md"
        link.symlink_to(self.lb / "GLOBAL-RULES.md")
        before = (self.lb / "GLOBAL-RULES.md").read_text(encoding="utf-8")
        [(change, error)] = rules.apply([rules._block_change(link, "replacement", "update")])
        self.assertIsNotNone(error)
        self.assertTrue(link.is_symlink())
        self.assertEqual((self.lb / "GLOBAL-RULES.md").read_text(encoding="utf-8"), before)

    def test_logbook_state(self):
        self.assertEqual(rules.logbook_state(self.lb), "in-sync")
        (self.lb / "GLOBAL-RULES.md").write_text("old\n", encoding="utf-8")
        self.assertEqual(rules.logbook_state(self.lb), "drifted")

    def test_uninstall_can_be_limited_to_one_tool(self):
        rules.apply(rules.plan_install(self.home, self.lb, tools=("claude-code", "codex")))
        plan = rules.plan_uninstall(self.home, mcp=False, tools=("codex",))
        self.assertEqual([c.target for c in plan], [self.home / ".codex" / "AGENTS.md"])
        self.assertEqual(len(rules.plan_uninstall(self.home, mcp=False)), 2)


if __name__ == "__main__":
    unittest.main()

class ProjectRule(Base):
    def test_claude_code_gets_an_import_in_its_own_file(self):
        c = project.plan_rule("claude-code", self.repo, self.lb)
        self.assertEqual((c.action, c.target), ("create", self.repo / ".claude" / "rules" / "waypoint.md"))
        self.assertIn("asks once", c.note)
        rules.apply([c])
        self.assertEqual(c.target.read_text(encoding="utf-8"), rules.claude_file_text(self.lb))
        self.assertEqual(project.rule_state("claude-code", self.repo, self.lb), ("in-sync", "import"))

    def test_a_foreign_claude_rule_file_is_refused(self):
        f = project.rule_file("claude-code", self.repo)
        f.parent.mkdir(parents=True)
        f.write_text("mine\n", encoding="utf-8")
        self.assertEqual(project.plan_rule("claude-code", self.repo, self.lb).action, "refuse")

    def test_a_new_codex_file_is_a_link(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        c = project.plan_rule("codex", self.repo, self.lb)
        self.assertEqual((c.action, c.old, c.new), ("link", None, self.ours()))
        rules.apply([c])
        self.assertEqual(project.rule_state("codex", self.repo, self.lb), ("in-sync", "symlink"))

    def test_no_symlinks_falls_back_to_a_copied_block(self):  # Review Focus 1
        with mock.patch.object(rules, "can_symlink", return_value=False):
            c = project.plan_rule("copilot-cli", self.repo, self.lb)
        self.assertEqual((c.action, c.target), ("create", self.repo / ".github" / "copilot-instructions.md"))
        rules.apply([c])
        self.assertEqual(project.rule_state("copilot-cli", self.repo, self.lb), ("in-sync", "copy"))

    def test_gemini_is_always_a_copy(self):  # Gemini CLI won't import from outside the repo
        c = project.plan_rule("gemini-cli", self.repo, self.lb)
        self.assertEqual(c.action, "create")
        self.assertIn(rules.BEGIN, c.new)

    def test_an_existing_file_keeps_its_text_and_gets_a_block(self):
        agents = self.repo / "AGENTS.md"
        agents.write_text("# Team notes\n", encoding="utf-8")
        rules.apply([project.plan_rule("codex", self.repo, self.lb)])
        self.assertTrue(agents.read_text(encoding="utf-8").startswith("# Team notes\n"))
        self.assertEqual(project.rule_state("codex", self.repo, self.lb), ("in-sync", "copy"))

    def test_copy_turns_waypoints_link_into_a_file_to_edit(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        rules.apply([project.plan_rule("codex", self.repo, self.lb)])
        c = project.plan_rule("codex", self.repo, self.lb, copy=True)
        self.assertEqual(c.action, "update")
        rules.apply([c])
        f = self.repo / "AGENTS.md"
        self.assertFalse(f.is_symlink())
        self.assertEqual(project.rule_state("codex", self.repo, self.lb), ("in-sync", "copy"))
        self.assertEqual(rules.logbook_state(self.lb), "in-sync")  # the logbook's own file is untouched
        self.assertEqual((self.lb / "GLOBAL-RULES.md").read_bytes(), rules.template().replace("{logbook}", self.lb.resolve().as_posix()).encode())
        self.assertTrue(list(self.repo.glob("AGENTS.md.bak-*")))

    def test_replace_link_refuses_a_symlink_to_a_users_own_file(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        own = self.repo / "notes.md"
        original = b"my notes\n"
        own.write_bytes(original)
        link = self.repo / "AGENTS.md"
        link.symlink_to(own)
        change = rules.Change(link, "update", original.decode(), "replacement", replace_link=True)
        [(change, error)] = rules.apply([change])
        self.assertIsNotNone(error)
        self.assertTrue(link.is_symlink())
        self.assertEqual(own.read_bytes(), original)

    def test_someone_elses_link_is_refused(self):
        if not rules.can_symlink():
            self.skipTest("this OS refuses symlinks here")
        readme = self.repo / "README.md"
        readme.write_text("x\n", encoding="utf-8")
        os.symlink(readme, self.repo / "AGENTS.md")
        self.assertEqual(project.plan_rule("codex", self.repo, self.lb).action, "refuse")
        self.assertEqual(project.rule_state("codex", self.repo, self.lb), ("missing", "none"))

    def test_exclude_lists_created_files_and_forgets_them(self):
        (self.repo / ".git").mkdir()
        f = self.repo / ".git" / "info" / "exclude"
        [c] = project.plan_exclude(self.repo, add=[self.repo / "AGENTS.md", self.repo / ".codex" / "config.toml"])
        rules.apply([c])
        self.assertIn("/AGENTS.md\n/.codex/config.toml", f.read_text(encoding="utf-8"))
        self.assertEqual(project.plan_exclude(self.repo, add=[self.repo / "AGENTS.md"]), [])  # already listed
        rules.apply(project.plan_exclude(self.repo, drop=[self.repo / "AGENTS.md", self.repo / ".codex" / "config.toml"]))
        self.assertEqual(f.read_text(encoding="utf-8"), "")

    def test_exclude_outside_a_plain_checkout_only_says_so(self):
        [c] = project.plan_exclude(self.repo, add=[self.repo / "AGENTS.md"])
        self.assertEqual(c.action, "unchanged")
        self.assertIn("/AGENTS.md", c.note)
        self.assertEqual(project.plan_exclude(self.repo, drop=[self.repo / "AGENTS.md"]), [])

class ProjectMcp(Base):
    def test_global_waypoint_registration_name_is_reused_for_project(self):
        f = self.home / ".gemini" / "settings.json"
        f.parent.mkdir()
        f.write_text(json.dumps({"mcpServers": {"waypoint": {"command": "waypoint", "args": ["mcp"]}}}), encoding="utf-8")
        with mock.patch.object(rules, "_real_home", return_value=True),                 mock.patch("waypoint.rules.shutil.which", return_value="/bin/gemini"):
            c = project.plan_mcp("gemini-cli", self.repo, self.home)
        self.assertIn("waypoint", c.run)
        self.assertNotIn("waypoint-local", c.run)

    def test_codex_reuses_our_global_waypoint_registration_name(self):
        f = self.home / ".codex" / "config.toml"
        f.write_text('[mcp_servers.waypoint]\ncommand = "waypoint"\nargs = ["mcp"]\n', encoding="utf-8")
        c = project.plan_codex_config(self.repo, self.lb, home=self.home)
        self.assertNotIn("waypoint-local", c.new)

    def test_copilot_reuses_waypoint_name_from_own_user_registration(self):
        user = self.home / ".copilot" / "mcp-config.json"
        user.parent.mkdir()
        user.write_text(json.dumps({"mcpServers": {"waypoint": {"command": "waypoint", "args": ["mcp"]}}}),
                        encoding="utf-8")
        c = project.plan_mcp("copilot-cli", self.repo, self.home)
        self.assertIn('"waypoint"', c.new)
        self.assertNotIn("waypoint-local", c.new)

    @unittest.skipUnless(rules.can_symlink(), "needs symlink support")
    def test_codex_uninstall_writes_planned_remainder_through_symlink(self):
        target = self.repo / "actual.toml"
        target.write_text(rules.toml_edit("", project.MCP_MARKS,
                                          '[mcp_servers."waypoint"]\ncommand = "waypoint"'), encoding="utf-8")
        link = self.codex_cfg()
        link.parent.mkdir()
        link.symlink_to(target)
        change = project.plan_codex_config(self.repo, None, remove=True)
        self.assertEqual(change.action, "remove")
        self.assertEqual(change.remove_text, "")
        self.assertIsNone(rules.apply([change])[0][1])
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "")

    def test_claude_local_registration_counts_user_scope_name(self):
        f = self.home / ".claude.json"
        f.write_text(json.dumps({"mcpServers": {"waypoint": {"url": "https://beacon.invalid"}}}), encoding="utf-8")
        with mock.patch.object(rules, "_real_home", return_value=True), mock.patch("waypoint.rules.shutil.which", return_value="/bin/claude"):
            c = project.plan_mcp("claude-code", self.repo, self.home)
        self.assertIn("waypoint-local", c.run)

    def test_disconnect_removes_both_waypoint_names(self):
        f = self.home / ".codex" / "config.toml"
        f.write_text('[mcp_servers.waypoint]\ncommand = "waypoint"\nargs = ["mcp"]\n'
                     '[mcp_servers.waypoint-local]\ncommand = "waypoint"\nargs = ["mcp"]\n', encoding="utf-8")
        with mock.patch("waypoint.rules._real_home", return_value=True), \
                mock.patch("waypoint.rules.shutil.which", return_value="/bin/codex"):
            c = rules._plan_mcp("codex", self.home, register=False)
        self.assertEqual(c.run_more, (("mcp", "remove", "waypoint-local"),))
        with mock.patch("waypoint.rules.shutil.which", return_value="/bin/codex"), \
                mock.patch("waypoint.rules.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self.assertIsNone(rules.apply([c])[0][1])
        self.assertEqual(run.call_count, 2)

    def test_failed_gemini_remove_keeps_exclude(self):
        (self.repo / ".git" / "info").mkdir(parents=True)
        exclude = self.repo / ".git" / "info" / "exclude"
        ran = self.fake_clis()
        with mock.patch.object(project, "git_tracked", return_value=False):
            rules.apply(project.plan_connect("gemini-cli", self.repo, self.lb, self.home))
        settings = self.repo / ".gemini" / "settings.json"
        settings.parent.mkdir(exist_ok=True)
        settings.write_text(json.dumps({"mcpServers": {"waypoint": {
            "type": "stdio", "command": "waypoint", "args": ["mcp"]}}}), encoding="utf-8")
        plan = project.plan_disconnect(self.repo, self.home, tools=("gemini-cli",))
        with mock.patch("waypoint.rules.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "no")):
            rules.apply(plan)
        self.assertIn("/.gemini/settings.json", exclude.read_text(encoding="utf-8"))

    def test_refused_rule_removal_keeps_its_exclude_line(self):
        (self.repo / ".git" / "info").mkdir(parents=True)
        exclude = self.repo / ".git" / "info" / "exclude"
        with mock.patch.object(project, "git_tracked", return_value=False), \
                mock.patch("waypoint.clis.server_command", return_value="waypoint"), \
                mock.patch.object(rules, "can_symlink", return_value=False):
            rules.apply(project.plan_connect("codex", self.repo, self.lb, self.home, mcp=False))
        agents = self.repo / "AGENTS.md"
        self.assertIn("/AGENTS.md", exclude.read_text(encoding="utf-8"))
        plan = project.plan_disconnect(self.repo, self.home, tools=("codex",), mcp=False)
        agents.write_text(agents.read_text(encoding="utf-8") + "my edit after the plan was shown\n", encoding="utf-8")
        results = rules.apply(plan)
        self.assertTrue(any(err and "changed since" in err for _, err in results), results)
        self.assertIn("/AGENTS.md", exclude.read_text(encoding="utf-8").splitlines())  # not just the .bak-* pattern

    def test_local_registration_counts_user_scope_name(self):
        from waypoint import clis
        cfg = self.home / ".codex" / "config.toml"
        cfg.write_text('[mcp_servers.waypoint]\nurl = "https://beacon.invalid"\n', encoding="utf-8")
        with mock.patch("waypoint.clis.server_command", return_value="waypoint"):
            c = project.plan_codex_config(self.repo, self.lb, home=self.home)
        self.assertIn('mcp_servers."waypoint-local"', c.new)

    def test_codex_remove_ignores_unrelated_broken_toml(self):
        f = self.codex_cfg()
        f.parent.mkdir()
        f.write_text("broken = [", encoding="utf-8")
        self.assertEqual(project.plan_codex_config(self.repo, None, remove=True).action, "unchanged")

    def test_git_tracked_finds_parent_repository(self):
        import subprocess
        parent = self.repo.parent
        subprocess.run(["git", "init", str(parent)], check=True, capture_output=True)
        f = self.repo / "AGENTS.md"
        f.write_text("team\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(parent), "add", "zebra/AGENTS.md"], check=True, capture_output=True)
        self.assertTrue(project.git_tracked(self.repo, f))

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_connect_refuses_tracked_file_in_parent_repository(self):
        parent = self.repo.parent
        subprocess.run(["git", "init", str(parent)], check=True, capture_output=True)
        f = self.repo / "AGENTS.md"
        f.write_text("team\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(parent), "add", "zebra/AGENTS.md"], check=True, capture_output=True)
        plan = project.plan_connect("codex", self.repo, self.lb, self.home, mcp=False)
        refusal = next(c for c in plan if c.target == f)
        self.assertEqual(refusal.action, "refuse")
        self.assertIn("is tracked by git", refusal.note)

    def test_git_unavailable_refusal_says_uncertain(self):
        f = self.repo / "AGENTS.md"
        f.write_text("team\n", encoding="utf-8")
        with mock.patch.object(project, "git_tracked", return_value=True), mock.patch.object(project.shutil, "which", return_value=None):
            plan = project.plan_connect("codex", self.repo, self.lb, self.home, mcp=False)
        refusal = next(c for c in plan if c.target == f)
        self.assertIn("git isn't available to check whether AGENTS.md is tracked; pass --allow-tracked", refusal.note)

    def test_connect_update_excludes_backup_from_git(self):
        parent = self.repo.parent
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)
        with mock.patch.object(project, "git_tracked", return_value=False):
            rules.apply(project.plan_connect("codex", self.repo, self.lb, self.home, mcp=False))
            agents = self.repo / "AGENTS.md"
            agents.write_text("edited\n", encoding="utf-8")
            rules.apply([project.plan_rule("codex", self.repo, self.lb, copy=True)])
        output = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"],
                                check=True, capture_output=True, text=True).stdout
        self.assertNotIn("bak-", output)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_disconnect_keeps_backup_excluded_in_repo_and_parent_repo(self):
        for parent_repo in (False, True):
            repo = self.repo / ("nested" if parent_repo else "standalone")
            repo.mkdir()
            git_root = self.repo if parent_repo else repo
            subprocess.run(["git", "init", str(git_root)], check=True, capture_output=True)
            (repo / "AGENTS.md").write_text("user notes\n", encoding="utf-8")
            if parent_repo:
                subprocess.run(["git", "-C", str(git_root), "add", "nested/AGENTS.md"], check=True, capture_output=True)
            with mock.patch.object(project, "git_tracked", return_value=False):
                rules.apply(project.plan_connect("codex", repo, self.lb, self.home, mcp=False))
            rules.apply(project.plan_disconnect(repo, self.home, tools=("codex",), mcp=False))
            output = subprocess.run(["git", "-C", str(git_root), "status", "--porcelain"],
                                    check=True, capture_output=True, text=True).stdout
            self.assertNotIn("bak-", output)

    def codex_cfg(self):
        return self.repo / ".codex" / "config.toml"

    def test_claude_code_registers_in_the_repo_with_local_scope(self):
        ran = self.fake_clis()
        c = project.plan_mcp("claude-code", self.repo, self.home)
        self.assertEqual((c.action, c.target, c.cwd, c.keyed), ("register", self.home / ".claude.json", self.repo, True))
        rules.apply([c])
        self.assertEqual(ran, [(["/bin/claude", "mcp", "add", "--scope", "local", "waypoint", "--", "waypoint", "mcp"],
                                self.repo)])
        (self.home / ".claude.json").write_text(json.dumps({"projects": {self.repo.as_posix(): {"mcpServers": {
            "waypoint": {"type": "stdio", "command": "waypoint", "args": ["mcp"]}}}}}), encoding="utf-8")
        c = project.plan_mcp("claude-code", self.repo, self.home, register=False)
        self.assertEqual(c.run[1:], ("mcp", "remove", "--scope", "local", "waypoint"))

    def test_gemini_registers_with_project_scope_in_the_repo(self):
        ran = self.fake_clis()
        c = project.plan_mcp("gemini-cli", self.repo, self.home)
        self.assertEqual(c.target, self.repo / ".gemini" / "settings.json")
        rules.apply([c])
        self.assertEqual(ran, [(["/bin/gemini", "mcp", "add", "--scope", "project", "waypoint", "waypoint", "mcp"],
                                self.repo)])

    def test_fake_home_runs_nothing(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("ran a CLI")):
            c = project.plan_mcp("claude-code", self.repo, self.home)
        self.assertEqual(c.action, "unchanged")
        self.assertIn("real home", c.note)

    def test_codex_config_gets_the_server_and_the_logbook(self):
        c = project.plan_codex_config(self.repo, self.lb)
        self.assertEqual(c.action, "create")
        rules.apply([c])
        data = tomllib.loads(self.codex_cfg().read_text(encoding="utf-8"))
        self.assertTrue(clis.is_ours(data["mcp_servers"]["waypoint"]))
        self.assertEqual(data["sandbox_workspace_write"]["writable_roots"], [str(self.lb)])
        self.assertEqual(project.plan_codex_config(self.repo, self.lb).action, "unchanged")
        gone = project.plan_codex_config(self.repo, None, remove=True)
        self.assertEqual((gone.action, gone.new), ("remove", None))
        rules.apply([gone])
        self.assertFalse(self.codex_cfg().exists())

    def test_codex_config_with_its_own_sandbox_table_stays_valid(self):
        self.codex_cfg().parent.mkdir()
        self.codex_cfg().write_text("[sandbox_workspace_write]\nnetwork_access = true\n", encoding="utf-8")
        c = project.plan_codex_config(self.repo, self.lb)
        self.assertEqual(c.action, "update")
        self.assertIn("yourself", c.note)
        rules.apply([c])
        data = tomllib.loads(self.codex_cfg().read_text(encoding="utf-8"))
        self.assertTrue(clis.is_ours(data["mcp_servers"]["waypoint"]))
        self.assertEqual(data["sandbox_workspace_write"], {"network_access": True})

    def test_codex_config_name_clash_uses_waypoint_local(self):
        self.codex_cfg().parent.mkdir()
        self.codex_cfg().write_text('[mcp_servers.waypoint]\nurl = "https://x.example/mcp"\n', encoding="utf-8")
        rules.apply([project.plan_codex_config(self.repo, self.lb, writable_root=False)])
        servers = tomllib.loads(self.codex_cfg().read_text(encoding="utf-8"))["mcp_servers"]
        self.assertTrue(clis.is_ours(servers["waypoint-local"]))
        self.assertEqual(servers["waypoint"], {"url": "https://x.example/mcp"})

    def test_copilot_repo_file_is_written_and_removed(self):
        f = self.repo / ".github" / "mcp.json"
        (self.repo / ".git" / "info").mkdir(parents=True)
        rules.apply([project.plan_copilot_mcp(self.repo)])
        rules.apply(project.plan_exclude(self.repo, add=[f]))
        self.assertTrue(clis.is_ours(json.loads(f.read_text(encoding="utf-8"))["mcpServers"]["waypoint"]))
        rules.apply([project.plan_copilot_mcp(self.repo, register=False)])
        self.assertFalse(f.exists())

    def test_copilot_sibling_subproject_line_does_not_make_this_file_ours(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        sub1, sub2 = self.repo / "sub1", self.repo / "sub2"
        sub1.mkdir()
        mine = sub2 / ".github" / "mcp.json"  # the user's own file, not in Waypoint's exclude block
        mine.parent.mkdir(parents=True)
        mine.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        rules.apply(project.plan_exclude(sub1, add=[sub1 / ".github" / "mcp.json"]))
        self.assertIn("/sub1/.github/mcp.json", (self.repo / ".git" / "info" / "exclude").read_text(encoding="utf-8"))
        rules.apply([project.plan_copilot_mcp(sub2)])
        rules.apply([project.plan_copilot_mcp(sub2, register=False)])
        self.assertTrue(mine.exists())  # emptied, not deleted
        self.assertEqual(json.loads(mine.read_text(encoding="utf-8")), {"mcpServers": {}})

    def test_copilot_keeps_other_servers(self):
        f = self.repo / ".github" / "mcp.json"
        f.parent.mkdir()
        f.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
        rules.apply([project.plan_copilot_mcp(self.repo)])
        rules.apply([project.plan_copilot_mcp(self.repo, register=False)])
        self.assertEqual(json.loads(f.read_text(encoding="utf-8")), {"mcpServers": {"other": {"command": "x"}}})

    def test_codex_trust_is_noted_never_written(self):
        cfg = self.home / ".codex" / "config.toml"
        self.assertFalse(project.codex_trusted(self.repo, self.home))
        plan = project.plan_connect("codex", self.repo, self.lb, self.home)
        self.assertTrue(any(c.target == cfg and c.action == "unchanged" and "trust" in c.note for c in plan))
        self.assertFalse(any(c.target == cfg and c.action in rules.WRITES for c in plan))
        cfg.write_text(f'[projects.{json.dumps(str(self.repo))}]\ntrust_level = "trusted"\n', encoding="utf-8")
        self.assertTrue(project.codex_trusted(self.repo, self.home))
        self.assertFalse(any(c.target == cfg for c in project.plan_connect("codex", self.repo, self.lb, self.home)))

    def test_connect_then_disconnect_leaves_the_repo_as_it_was(self):
        (self.repo / ".git" / "info").mkdir(parents=True)
        excl, agents = self.repo / ".git" / "info" / "exclude", self.repo / "AGENTS.md"
        excl.write_text("# git's own\n*.log\n", encoding="utf-8")
        agents.write_text("# Team notes\n", encoding="utf-8")
        before = {p: p.read_bytes() for p in (excl, agents)}
        with mock.patch.object(project, "git_tracked", return_value=False):
            rules.apply(project.plan_connect("codex", self.repo, self.lb, self.home))
        self.assertIn(rules.BEGIN, agents.read_text(encoding="utf-8"))
        self.assertIn("/.codex/config.toml", excl.read_text(encoding="utf-8"))
        rules.apply(project.plan_disconnect(self.repo, self.home, tools=("codex",)))
        self.assertEqual(agents.read_bytes(), before[agents])
        remaining = excl.read_text(encoding="utf-8")
        self.assertNotIn("/.codex/config.toml\n", remaining)
        self.assertNotIn("/AGENTS.md\n", remaining)
        self.assertIn("/AGENTS.md.bak-*", remaining)
        self.assertIn("/.codex/config.toml.bak-*", remaining)
        self.assertFalse(self.codex_cfg().exists())

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_disconnect_allows_future_codex_config_to_be_seen_by_git(self):
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)
        with mock.patch.object(project, "git_tracked", return_value=False):
            rules.apply(project.plan_connect("codex", self.repo, self.lb, self.home, mcp=False))
        rules.apply(project.plan_disconnect(self.repo, self.home, tools=("codex",), mcp=False))
        config = self.repo / ".codex" / "config.toml"
        config.parent.mkdir(exist_ok=True)
        config.write_text("user setting = true\n", encoding="utf-8")
        output = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"],
                                check=True, capture_output=True, text=True).stdout
        self.assertIn(".codex", output)

    def test_global_rule_too_is_pointed_out(self):
        rules.apply(rules.plan_install(self.home, self.lb, tools=("codex",)))
        plan = project.plan_connect("codex", self.repo, self.lb, self.home, mcp=False, writable_root=False)
        self.assertTrue(any("twice" in c.note for c in plan))

    def test_repos_includes_archived_and_skips_missing_folders(self):
        (self.lb / "projects" / "zebra.md").rename(self.lb / "projects" / "_archive" / "zebra.md")
        (self.lb / "projects" / "gone.md").write_text(f"# gone\n- Path: {self.repo.parent / 'gone'}\n\n## Log\n",
                                                     encoding="utf-8")
        self.assertEqual(project.repos(self.lb), [("zebra", self.repo)])

    def test_plan_connect_refuses_a_tracked_rule_file(self):
        agents = self.repo / "AGENTS.md"
        agents.write_text("# Team notes\n", encoding="utf-8")
        with mock.patch.object(project, "git_tracked", return_value=True):
            plan = project.plan_connect("codex", self.repo, self.lb, self.home)
            self.assertTrue(any(c.target == agents and c.action == "refuse" for c in plan))
            allowed = project.plan_connect("codex", self.repo, self.lb, self.home, allow_tracked=True)
            self.assertFalse(any(c.target == agents and c.action == "refuse" for c in allowed))

    def test_plan_connect_refuses_tracked_codex_config_unless_allowed(self):
        cfg = self.codex_cfg()
        cfg.parent.mkdir()
        cfg.write_text("[other]\nvalue = true\n", encoding="utf-8")
        with mock.patch.object(project, "git_tracked", return_value=True):
            plan = project.plan_connect("codex", self.repo, self.lb, self.home)
            refused = next(c for c in plan if c.target == cfg)
            self.assertEqual(refused.action, "refuse")
            self.assertIn("--allow-tracked", refused.note)
            allowed = project.plan_connect("codex", self.repo, self.lb, self.home, allow_tracked=True)
            self.assertNotEqual(next(c for c in allowed if c.target == cfg).action, "refuse")

    def test_plan_connect_refuses_tracked_copilot_file_unless_allowed(self):
        f = self.repo / ".github" / "mcp.json"
        f.parent.mkdir()
        f.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        with mock.patch.object(project, "git_tracked", return_value=True):
            plan = project.plan_connect("copilot-cli", self.repo, self.lb, self.home)
            refused = next(c for c in plan if c.target == f)
            self.assertEqual(refused.action, "refuse")
            self.assertIn("--allow-tracked", refused.note)
            allowed = project.plan_connect("copilot-cli", self.repo, self.lb, self.home, allow_tracked=True)
            self.assertNotEqual(next(c for c in allowed if c.target == f).action, "refuse")

    def test_plan_connect_refuses_tracked_gemini_registration_before_cli(self):
        f = self.repo / ".gemini" / "settings.json"
        f.parent.mkdir()
        f.write_text('{}\n', encoding="utf-8")
        ran = self.fake_clis()
        with mock.patch.object(project, "git_tracked", return_value=True):
            plan = project.plan_connect("gemini-cli", self.repo, self.lb, self.home)
            refused = next(c for c in plan if c.target == f)
            self.assertEqual(refused.action, "refuse")
            self.assertIn("--allow-tracked", refused.note)
            self.assertFalse(any(args[0][0] == "/bin/gemini" for args in ran))
            allowed = project.plan_connect("gemini-cli", self.repo, self.lb, self.home, allow_tracked=True)
            self.assertNotEqual(next(c for c in allowed if c.target == f).action, "refuse")

    def test_copilot_disconnect_preserves_preexisting_empty_server_file(self):
        f = self.repo / ".github" / "mcp.json"
        f.parent.mkdir()
        f.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        rules.apply([project.plan_copilot_mcp(self.repo)])
        rules.apply([project.plan_copilot_mcp(self.repo, register=False)])
        self.assertTrue(f.exists())
        self.assertEqual(json.loads(f.read_text(encoding="utf-8")), {"mcpServers": {}})

    def test_gemini_connect_disconnect_restores_exclude_bytes(self):
        info = self.repo / ".git" / "info"
        info.mkdir(parents=True)
        exclude = info / "exclude"
        exclude.write_bytes(b"# git's own\n*.log\n")
        before = exclude.read_bytes()
        ran = self.fake_clis()
        with mock.patch.object(project, "git_tracked", return_value=False):
            rules.apply(project.plan_connect("gemini-cli", self.repo, self.lb, self.home))
            settings = self.repo / ".gemini" / "settings.json"
            settings.parent.mkdir(exist_ok=True)
            settings.write_text(json.dumps({"mcpServers": {"waypoint": {
                "type": "stdio", "command": "waypoint", "args": ["mcp"]}}}),
                                encoding="utf-8")
            rules.apply(project.plan_disconnect(self.repo, self.home, tools=("gemini-cli",)))
        self.assertIn(b"/GEMINI.md.bak-*", exclude.read_bytes())
        self.assertIn(b"/.gemini/settings.json.bak-*", exclude.read_bytes())


class Commands(Base):
    def connect(self, *extra, cli_id="codex"):
        return self.run_cli("connect", cli_id, "--project", str(self.repo), "--yes", "--no-mcp", "--no-writable-root",
                            "--logbook", str(self.lb), "--home", str(self.home), *extra)

    def test_connect_project_writes_only_the_repo(self):
        code, out = self.connect()
        self.assertEqual(code, 0, out)
        self.assertEqual(project.rule_state("codex", self.repo, self.lb)[0], "in-sync")
        self.assertEqual(list((self.home / ".codex").iterdir()), [])

    def test_project_is_repeatable(self):
        other = self.repo.parent / "yak"
        other.mkdir()
        code, out = self.run_cli("connect", "copilot-cli", "--project", str(self.repo), "--project", str(other), "--yes",
                                 "--no-mcp", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        for r in (self.repo, other):
            self.assertEqual(project.rule_state("copilot-cli", r, self.lb)[0], "in-sync")

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_a_tracked_file_needs_allow_tracked(self):  # Review Focus 3
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        agents = self.repo / "AGENTS.md"
        agents.write_text("# Team notes\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "AGENTS.md"], check=True, capture_output=True)
        code, out = self.connect()
        self.assertEqual(code, 1)
        self.assertIn("--allow-tracked", out)
        self.assertEqual(agents.read_text(encoding="utf-8"), "# Team notes\n")
        code, out = self.connect("--allow-tracked")
        self.assertEqual(code, 0, out)
        self.assertIn(rules.BEGIN, agents.read_text(encoding="utf-8"))

    def test_disconnect_project_then_global(self):
        self.connect()
        self.run_cli("connect", "codex", "--yes", "--no-mcp", "--no-writable-root", "--logbook", str(self.lb),
                     "--home", str(self.home))
        code, out = self.run_cli("disconnect", "codex", "--project", str(self.repo), "--yes", "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertEqual(project.rule_state("codex", self.repo, self.lb), ("missing", "none"))
        self.assertIn(rules.BEGIN, (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"))  # global kept
        code, out = self.run_cli("disconnect", "codex", "--yes", "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertNotIn(rules.BEGIN, (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_disconnect_only_drops_selected_cli_excludes(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                repo = self.repo / ("nested" if nested else "standalone")
                repo.mkdir()
                git_root = self.repo if nested else repo
                subprocess.run(["git", "init", "-q", str(git_root)], check=True)
                connect_args = ("--yes", "--logbook", str(self.lb), "--home", str(self.home))
                disconnect_args = ("--yes", "--home", str(self.home))
                for tool in ("codex", "copilot-cli"):
                    code, out = self.run_cli("connect", tool, "--project", str(repo), *connect_args)
                    self.assertEqual(code, 0, out)
                code, out = self.run_cli("disconnect", "codex", "--project", str(repo), *disconnect_args)
                self.assertEqual(code, 0, out)
                exclude = git_root / ".git" / "info" / "exclude"
                lines = exclude.read_text(encoding="utf-8")
                self.assertIn("/.github/copilot-instructions.md", lines)
                self.assertIn("/.github/mcp.json", lines)
                self.assertIn("/.github/mcp.json.bak-*", lines)
                status = subprocess.run(["git", "-C", str(git_root), "status", "--porcelain"],
                                        check=True, capture_output=True, text=True).stdout
                self.assertNotIn(".github/", status)
                code, out = self.run_cli("disconnect", "copilot-cli", "--project", str(repo), *disconnect_args)
                self.assertEqual(code, 0, out)
                self.assertFalse((repo / ".github" / "mcp.json").exists())
                status = subprocess.run(["git", "-C", str(git_root), "status", "--porcelain"],
                                        check=True, capture_output=True, text=True).stdout
                self.assertNotIn(".github/", status)
                self.assertNotIn("AGENTS.md", status)
                self.assertNotIn("GEMINI.md", status)

    def test_rules_uninstall_finds_project_installs_without_a_registry(self):
        self.connect()
        (self.lb / "projects" / "zebra.md").rename(self.lb / "projects" / "_archive" / "zebra.md")
        code, out = self.run_cli("rules", "--uninstall", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertEqual(project.rule_state("codex", self.repo, self.lb), ("missing", "none"))

    def test_rules_install_refreshes_project_copies(self):
        with mock.patch.object(rules, "can_symlink", return_value=False):
            self.connect("--copy")
        f = self.repo / "AGENTS.md"
        first = rules._clean(rules.render(self.lb)).splitlines()[0]
        f.write_text(f.read_text(encoding="utf-8").replace(first, "# an older rule"), encoding="utf-8")
        self.assertEqual(project.rule_state("codex", self.repo, self.lb)[0], "drifted")
        code, out = self.run_cli("rules", "--install", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertEqual(project.rule_state("codex", self.repo, self.lb)[0], "in-sync")

    def test_snippet_cli_has_no_project_install(self):
        code, out = self.run_cli("connect", "cursor", "--project", str(self.repo), "--logbook", str(self.lb))
        self.assertEqual(code, 1)
        self.assertIn("prints its config", out)

    def test_copy_needs_project(self):
        code, out = self.run_cli("connect", "codex", "--copy", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 1)
        self.assertIn("--project", out)

    def test_connect_duplicate_project_plans_once(self):
        code, out = self.run_cli("connect", "codex", "--project", str(self.repo), "--project", str(self.repo),
                                 "--yes", "--no-mcp", "--no-writable-root", "--logbook", str(self.lb),
                                 "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("  link      "), 1, out)

    @unittest.skipUnless(rules.can_symlink(), "needs symlink support")
    def test_disconnect_removes_waypoint_link_without_changing_logbook_rule(self):
        self.connect()
        f = self.repo / "AGENTS.md"
        rule = self.lb / "GLOBAL-RULES.md"
        before = rule.read_bytes()
        self.assertTrue(f.is_symlink())
        plan = project.plan_disconnect(self.repo, self.home, tools=("codex",), mcp=False)
        link = next(c for c in plan if c.target == f)
        self.assertEqual(link.action, "link")
        self.assertIsNone(link.new)
        self.assertIsNone(next(err for c, err in rules.apply(plan) if c is link))
        self.assertFalse(f.is_symlink())
        self.assertEqual(rule.read_bytes(), before)

    @unittest.skipUnless(rules.can_symlink(), "needs symlink support")
    def test_disconnect_plan_describes_unlink_without_none_target(self):
        self.connect()
        code, out = self.run_cli("disconnect", "codex", "--project", str(self.repo), "--yes", "--no-mcp",
                                 "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertIn("removes the link", out)
        self.assertIn("unlink", out)
        self.assertNotIn("→ None", out)

    @unittest.skipUnless(rules.can_symlink(), "needs symlink support")
    def test_link_removal_refuses_if_link_changed_after_planning(self):
        self.connect()
        f = self.repo / "AGENTS.md"
        plan = project.plan_disconnect(self.repo, self.home, tools=("codex",), mcp=False)
        link = next(c for c in plan if c.target == f)
        f.unlink()
        replacement = self.repo / "replacement.md"
        replacement.write_text("leave me", encoding="utf-8")
        f.symlink_to(replacement)
        result = next(err for c, err in rules.apply([link]) if c is link)
        self.assertIn("changed since it was shown", result)
        self.assertTrue(f.is_symlink())
        self.assertEqual(f.read_text(encoding="utf-8"), "leave me")
