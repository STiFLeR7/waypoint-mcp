"""R0.1 Codex review fixes: installer and setup safety (docs/reviews/r0.1-codex-review.md). stdlib only."""
import builtins
import io
import json
import os
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from waypoint import cli, logbook, rules


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home, self.lb, self.root = base / "home", base / "lb", base / "code"
        for d in (self.home / ".codex", self.home / ".claude", self.root / "alpha"):
            d.mkdir(parents=True)
        self._env = {k: os.environ.get(k) for k in ("APPDATA", "XDG_CONFIG_HOME", "WAYPOINT_LOGBOOK")}
        os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"] = str(base / "cfg")
        os.environ.pop("WAYPOINT_LOGBOOK", None)

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

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

    def init(self):
        self.run_cli("init", str(self.lb))


class Apply(Base):
    def test_config_diff_shows_only_both_waypoint_regions(self):
        f = self.home / ".codex" / "config.toml"
        old = '[mcp_servers.waypoint]\nurl = "https://example.invalid/mcp"\n\n'
        f.write_text(old, encoding="utf-8")
        c = rules.plan_writable_root(f, self.lb)
        c.new = rules.toml_edit(old, rules.SANDBOX, rules.sandbox_block(self.lb))
        c.new = rules.toml_edit(c.new, rules.MCP_MARKS,
                                '[mcp_servers."waypoint-local"]\ncommand = "waypoint"')
        diff = c.diff()
        self.assertNotIn("example.invalid", diff)
        self.assertIn("mcp_servers", diff)
        self.assertIn("waypoint:sandbox:begin", diff)
        c.old = c.new
        c.new = ""
        self.assertNotIn("example.invalid", c.diff())

    @unittest.skipUnless(rules.can_symlink(), "needs symlink support")
    def test_uninstall_refuses_symlinked_claude_owned_file(self):
        target = self.home / "target.md"
        target.write_text(rules.claude_file_text(self.lb), encoding="utf-8")
        link = rules.claude_rule_file(self.home)
        link.parent.mkdir(parents=True)
        link.symlink_to(target)
        result = rules.apply(rules.plan_uninstall(self.home, mcp=False, tools=("claude-code",)))
        self.assertTrue(any("is a link to" in (err or "") for _, err in result))
        self.assertEqual(target.read_text(encoding="utf-8"), rules.claude_file_text(self.lb))

    @unittest.skipUnless(rules.can_symlink(), "needs symlink support")
    def test_symlinked_mcp_file_refusal_names_server_entry_action(self):
        target = self.home / "mcp.json"
        target.write_text('{"mcpServers": {"waypoint": {}}}\n', encoding="utf-8")
        link = self.home / ".github" / "mcp.json"
        link.parent.mkdir()
        link.symlink_to(target)
        from waypoint.rules import Change
        [(change, error)] = rules.apply([Change(link, "remove", target.read_text(encoding="utf-8"), None)])
        self.assertIn("remove Waypoint's server entry from that file by hand", error)

    def test_target_changed_after_plan_is_refused_not_overwritten(self):  # H1
        self.init()
        agents = self.home / ".codex" / "AGENTS.md"
        agents.write_text("v1\n", encoding="utf-8")
        plan = rules.plan_install(self.home, self.lb, tools=("codex",))
        agents.write_text("v2 edited while the prompt was open\n", encoding="utf-8")
        [(c, err)] = rules.apply(plan)
        self.assertIn("changed since it was shown", err)
        self.assertEqual(agents.read_text(encoding="utf-8"), "v2 edited while the prompt was open\n")

    def test_planned_create_does_not_overwrite_a_file_that_appeared(self):  # H1
        self.init()
        plan = rules.plan_install(self.home, self.lb, tools=("codex",))
        (self.home / ".codex" / "AGENTS.md").write_text("appeared\n", encoding="utf-8")
        self.assertIn("changed since", rules.apply(plan)[0][1])

    def test_refusal_fails_the_command_and_writes_nothing(self):  # M3
        self.init()
        (self.home / ".codex" / "AGENTS.md").write_text(f"{rules.BEGIN}\n{rules.BEGIN}\n", encoding="utf-8")
        code, out = self.run_cli("rules", "--install", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 1)
        self.assertIn("nothing was written", out)
        self.assertFalse(rules.claude_rule_file(self.home).exists())  # the other target wasn't written either

    def test_legacy_block_kept_when_the_rules_file_is_refused(self):  # M4
        self.init()
        cm = self.home / ".claude" / "CLAUDE.md"
        cm.write_text(f"{rules.BEGIN}\n{rules.claude_import(self.lb)}\n{rules.END}\n", encoding="utf-8")
        rules.claude_rule_file(self.home).parent.mkdir(parents=True)
        rules.claude_rule_file(self.home).write_text("not ours\n", encoding="utf-8")
        plan = rules.plan_install(self.home, self.lb, tools=("claude-code",))
        self.assertEqual([c.action for c in plan], ["refuse"])
        rules.apply(plan)
        self.assertIn(rules.BEGIN, cm.read_text(encoding="utf-8"))

    def test_legacy_removal_skipped_if_the_rules_file_write_fails(self):  # M4
        self.init()
        cm = self.home / ".claude" / "CLAUDE.md"
        cm.write_text(f"{rules.BEGIN}\nx\n{rules.END}\n", encoding="utf-8")
        plan = rules.plan_install(self.home, self.lb, tools=("claude-code",))
        with mock.patch.object(rules, "_atomic_write", side_effect=[OSError("disk full"), None]):
            results = rules.apply(plan)
        self.assertEqual([bool(e) for _, e in results], [True, True])
        self.assertIn("depends on a change", results[1][1])
        self.assertIn(rules.BEGIN, cm.read_text(encoding="utf-8"))

    def test_uninstall_keeps_whitespace_around_a_block_in_the_middle(self):  # M6
        agents = self.home / ".codex" / "AGENTS.md"
        agents.write_text(f"notes\n\n{rules.BEGIN}\nrule\n{rules.END}\nTAIL\n", encoding="utf-8")
        rules.apply(rules.plan_uninstall(self.home))
        self.assertEqual(agents.read_text(encoding="utf-8"), "notes\n\nTAIL\n")


class Setup(Base):
    def test_yes_needs_root_even_with_an_existing_toml(self):  # M5
        self.init()
        code, out = self.run_cli("setup", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertNotEqual(code, 0)
        self.assertIn("--root", out)

    def test_broad_root_needs_explicit_consent(self):  # M5
        new = Path(self.tmp.name) / "new"
        code, out = self.run_cli("setup", "--yes", "--logbook", str(new), "--root", str(Path.home()), "--tool", "codex",
                                 "--home", str(self.home))
        self.assertNotEqual(code, 0)
        self.assertIn("--allow-broad-root", out)
        self.assertFalse(new.exists())  # refused before any write

    def test_declining_writes_nothing(self):  # M2
        new = Path(self.tmp.name) / "new"
        code, out = self.run_cli("setup", "--logbook", str(new), "--root", str(self.root), "--tool", "codex",
                                 "--home", str(self.home), answers=["n", "n", "n"])  # no git, no email, then don't write
        self.assertNotEqual(code, 0)
        self.assertFalse(new.exists())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())
        self.assertFalse((Path(self.tmp.name) / "cfg" / "waypoint" / "logbook").exists())

    @mock.patch("waypoint.rules._real_home", lambda home: True)  # so it remembers; --no-mcp so nothing is run
    def test_rerun_writes_nothing(self):  # M2
        args = ("setup", "--yes", "--logbook", str(self.lb), "--root", str(self.root), "--tool", "codex", "--no-mcp",
                "--home", str(self.home))
        self.assertEqual(self.run_cli(*args)[0], 0)
        f = Path(self.tmp.name) / "cfg" / "waypoint" / "logbook"
        before = f.stat().st_mtime_ns
        code, out = self.run_cli(*args)
        self.assertIn("Nothing to change.", out)
        self.assertEqual(f.stat().st_mtime_ns, before)

    def test_codex_writable_root_is_a_reviewed_change(self):
        code, out = self.run_cli("setup", "--yes", "--logbook", str(self.lb), "--root", str(self.root), "--tool", "codex",
                                 "--no-mcp", "--home", str(self.home))
        self.assertEqual(code, 0, out)
        cfg = self.home / ".codex" / "config.toml"
        self.assertIn("writable_roots", out)
        self.assertEqual(tomllib.loads(cfg.read_text(encoding="utf-8"))["sandbox_workspace_write"]["writable_roots"],
                         [str(self.lb.resolve())])
        rules.apply(rules.plan_uninstall(self.home, mcp=False))
        self.assertNotIn("sandbox_workspace_write", cfg.read_text(encoding="utf-8"))

    def test_an_existing_sandbox_table_is_left_to_the_user(self):
        cfg = self.home / ".codex" / "config.toml"
        cfg.write_text("[sandbox_workspace_write]\nnetwork_access = true\n", encoding="utf-8")
        code, out = self.run_cli("setup", "--yes", "--logbook", str(self.lb), "--root", str(self.root), "--tool", "codex",
                                 "--no-mcp", "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertIn("yourself", out)
        self.assertEqual(cfg.read_text(encoding="utf-8"), "[sandbox_workspace_write]\nnetwork_access = true\n")

    def test_no_writable_root_skips_it(self):
        code, out = self.run_cli("setup", "--yes", "--logbook", str(self.lb), "--root", str(self.root), "--tool", "codex",
                                 "--no-mcp", "--no-writable-root", "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertFalse((self.home / ".codex" / "config.toml").exists())


class Add(Base):
    def test_more_windows_device_names(self):  # M9
        for name in ("COM¹", "lpt²", "CONIN$", "conout$", "con .notes", "NUL.txt"):
            self.assertIn("reserved device", cli.name_problem(name) or "", name)
        self.assertIsNone(cli.name_problem("console"))

    def test_uppercase_extension_counts_as_taken(self):  # M9
        self.init()
        (self.lb / "projects" / "Alpha.MD").write_text("# x\n", encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            cli.add_project(self.lb, self.root / "alpha")
        self.assertIn("already exists", str(e.exception))

    def test_custom_template_cannot_break_the_guarantees(self):  # M10
        self.init()
        (self.lb / "projects" / "_template.md").write_text(
            "# T\n- Path: x\n- Status: done\n- Summary: example\n\n## Log\n### 2026-01-01 — codex\n- example\n",
            encoding="utf-8")
        f, _ = cli.add_project(self.lb, self.root / "alpha")
        p = logbook.load_project(f, self.lb)
        self.assertEqual((p["status"], p["summary"], p["logs"]), ("active", "", []))

    def test_bad_config_fails_before_creating_the_file(self):  # M11
        self.init()
        (self.lb / "waypoint.toml").write_text("roots = [\n", encoding="utf-8")
        with self.assertRaises(Exception):
            cli.add_project(self.lb, self.root / "alpha")
        self.assertFalse((self.lb / "projects" / "alpha.md").exists())

    def test_one_untracked_folder_is_singular(self):  # L13
        self.init()
        with mock.patch("waypoint.chart.build", return_value={"kpi": {"tracked": 0, "untracked": 1}}), \
                mock.patch("waypoint.chart.write"):
            code, out = self.run_cli("fix", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertIn("1 folder with recent AI activity is untracked", out)


class Connect(Base):
    """R0.3: the CLI table, rules for Preview CLIs, MCP registration through the CLI's own command."""

    def mcp(self, *, which=True, rc=0):
        """Treat the temp home as the real one, fake `which`, record the commands that would run."""
        ran = []

        def run(argv, **kw):
            ran.append(argv)
            return mock.Mock(returncode=rc, stdout="", stderr="boom")
        return ran, mock.patch.multiple(rules, _real_home=lambda h: True), \
            mock.patch("shutil.which", lambda exe: f"/bin/{exe}" if which else None), \
            mock.patch("subprocess.run", run)

    def init(self):
        super().init()
        from waypoint import config
        config.remember_logbook(self.lb)  # APPDATA/XDG point into the temp folder (Base)

    def test_fake_home_never_runs_a_real_cli(self):  # a --home run once rewrote the real ~/.codex/config.toml
        with mock.patch("subprocess.run", side_effect=AssertionError("ran a CLI")):
            self.init()
            plan = rules.plan_install(self.home, self.lb, tools=("codex", "claude-code"), mcp=True)
        self.assertFalse([c for c in plan if c.run])

    def test_registers_through_the_clis_own_command(self):
        self.init()
        ran, *patches = self.mcp()
        with patches[0], patches[1], patches[2]:
            code, out = self.run_cli("connect", "codex", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertEqual(ran, [["/bin/codex", "mcp", "add", "waypoint", "--", "waypoint", "mcp"]])
        self.assertIn("log_watch (tool id codex)", out)

    def test_a_taken_name_registers_waypoint_local(self):  # Review Focus 4
        self.init()
        (self.home / ".codex" / "config.toml").write_text('[mcp_servers.waypoint]\nurl = "https://x.example/mcp"\n',
                                                         encoding="utf-8")
        ran, *patches = self.mcp()
        with patches[0], patches[1], patches[2]:
            code, out = self.run_cli("connect", "codex", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertIn("waypoint-local", out)
        self.assertNotIn("x.example", out)  # never print its URL
        self.assertEqual(ran, [["/bin/codex", "mcp", "add", "waypoint-local", "--", "waypoint", "mcp"]])

    def test_both_names_taken_is_refused_and_nothing_is_written(self):
        self.init()
        (self.home / ".codex" / "config.toml").write_text(
            '[mcp_servers.waypoint]\nurl = "https://x.example/mcp"\n[mcp_servers.waypoint-local]\nurl = "https://y.example"\n',
            encoding="utf-8")
        ran, *patches = self.mcp()
        with patches[0], patches[1], patches[2]:
            code, out = self.run_cli("connect", "codex", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 1)
        self.assertIn("both", out)
        self.assertNotIn("example", out)
        self.assertEqual(ran, [])
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())  # the rule waits too

    def test_uninstall_removes_waypoint_local_and_leaves_the_beacon(self):  # Review Focus 4
        (self.home / ".codex" / "config.toml").write_text(
            '[mcp_servers.waypoint]\nurl = "https://x.example/mcp"\n'
            '[mcp_servers.waypoint-local]\ncommand = "waypoint"\nargs = ["mcp"]\n', encoding="utf-8")
        ran, *patches = self.mcp()
        with patches[0], patches[1], patches[2]:
            rules.apply(rules.plan_uninstall(self.home))
        self.assertEqual(ran, [["/bin/codex", "mcp", "remove", "waypoint-local"]])

    def test_off_path_registers_the_full_path(self):  # Review Focus 5
        self.init()
        ran, *patches = self.mcp()
        with patches[0], patches[1], patches[2], \
                mock.patch("waypoint.clis.server_command", return_value="/opt/wp/bin/waypoint"):
            code, out = self.run_cli("connect", "codex", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertEqual(ran, [["/bin/codex", "mcp", "add", "waypoint", "--", "/opt/wp/bin/waypoint", "mcp"]])
        self.assertIn("full path", out)

    def test_no_mcp_installs_the_rule_only(self):
        self.init()
        (self.home / ".codex" / "config.toml").write_text('[mcp_servers.waypoint]\nurl = "https://x.example/mcp"\n',
                                                         encoding="utf-8")
        code, out = self.run_cli("connect", "codex", "--no-mcp", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertIn(rules.BEGIN, (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"))

    def test_already_registered_is_unchanged(self):
        self.init()
        settings = self.home / ".gemini" / "settings.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps({"mcpServers": {"waypoint": {"command": "waypoint", "args": ["mcp"]}}}), encoding="utf-8")
        ran, *patches = self.mcp()
        with patches[0], patches[1], patches[2]:
            plan = rules.plan_install(self.home, self.lb, tools=("gemini-cli",), mcp=True)
        self.assertEqual([c.action for c in plan], ["create", "unchanged"])
        self.assertEqual(plan[0].target, self.home / ".gemini" / "GEMINI.md")

    def test_preview_rules_are_checked(self):
        self.init()
        rules.apply(rules.plan_install(self.home, self.lb, tools=("copilot-cli",)))
        states = rules.check(self.home, self.lb, ("copilot-cli", "gemini-cli"))
        self.assertEqual((states["copilot-cli"], states["gemini-cli"]), ("in-sync", "missing"))

    def test_uninstall_removes_only_our_registration(self):
        (self.home / ".codex" / "config.toml").write_text('[mcp_servers.waypoint]\ncommand = "waypoint"\nargs = ["mcp"]\n',
                                                         encoding="utf-8")
        (self.home / ".copilot").mkdir()
        (self.home / ".copilot" / "mcp-config.json").write_text(
            json.dumps({"mcpServers": {"waypoint": {"type": "http", "url": "https://x.example"}}}), encoding="utf-8")
        ran, *patches = self.mcp()
        with patches[0], patches[1], patches[2]:
            rules.apply(rules.plan_uninstall(self.home))
        self.assertEqual(ran, [["/bin/codex", "mcp", "remove", "waypoint"]])

    def test_snippet_cli_prints_config_and_writes_nothing(self):
        self.init()
        code, out = self.run_cli("connect", "cursor", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0)
        self.assertIn('"args": [\n', out)
        self.assertIn("writes nothing", out)
        self.assertIn("tool id `cursor`", out)

    # Codex R0.3 review (docs/reviews/r0.3-codex-review.md)
    def test_mcp_refused_when_the_server_would_open_another_logbook(self):  # 1
        self.init()
        other = Path(self.tmp.name) / "other"
        self.run_cli("init", str(other))
        ran, *patches = self.mcp()
        with patches[0], patches[1], patches[2]:
            code, out = self.run_cli("connect", "codex", "--yes", "--logbook", str(other), "--home", str(self.home))
        self.assertEqual(code, 1)
        self.assertIn("would open", out)
        self.assertEqual(ran, [])

    def test_look_alike_registrations_are_not_ours(self):  # 2
        from waypoint import clis
        for entry in ({"command": "waypoint.py", "args": ["mcp"]}, {"command": "waypoint", "args": ["mcp"], "url": "https://x"},
                      {"type": "http", "command": "waypoint", "args": ["mcp"]}, {"command": "/elsewhere/waypoint", "args": ["mcp"]},
                      {"command": "waypoint", "args": ["mcp", "--other"]}, {"command": "waypoint", "args": []},
                      {"command": "waypoint.exe", "args": ["mcp"]},
                      {"command": "waypoint", "args": ["mcp"], "env": {"WAYPOINT_LOGBOOK": "/elsewhere"}}):
            self.assertFalse(clis.is_ours(entry), entry)
        self.assertTrue(clis.is_ours({"command": "waypoint", "args": ["mcp"]}))
        self.assertTrue(clis.is_ours({"type": "local", "command": "waypoint-mcp", "args": []}))

    def test_config_is_backed_up_before_the_cli_runs(self):  # 3
        self.init()
        f = self.home / ".codex" / "config.toml"
        f.write_text("model = 'x'\n", encoding="utf-8")
        ran, *patches = self.mcp()
        with patches[0], patches[1], patches[2]:
            rules.apply(rules.plan_install(self.home, self.lb, tools=("codex",), mcp=True))
        self.assertEqual(len(ran), 1)
        self.assertEqual([b.read_text(encoding="utf-8") for b in f.parent.glob("config.toml.bak-*")], ["model = 'x'\n"])

    def test_fake_home_setup_does_not_remember(self):  # 4
        new = Path(self.tmp.name) / "new"
        code, out = self.run_cli("setup", "--yes", "--logbook", str(new), "--root", str(self.root), "--tool", "codex",
                                 "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertFalse((Path(self.tmp.name) / "cfg" / "waypoint" / "logbook").exists())

    def test_gemini_settings_with_comments_are_read(self):  # 5
        from waypoint import clis
        f = self.home / "settings.json"
        f.write_text('{\n  // mine\n  "mcpServers": {"waypoint": {"command": "waypoint", "args": ["mcp"]}}, /* x */\n'
                     '  "url": "http://a//b"\n}\n', encoding="utf-8")
        entry, err = clis.mcp_entry(f)
        self.assertIsNone(err)
        self.assertTrue(clis.is_ours(entry))
        f.write_text('{"mcpServers": {}, "timeout": 1/*c*/2}', encoding="utf-8")  # Gemini rejects this too
        self.assertIsNotNone(clis.mcp_entry(f)[1])

    def test_uninstall_reports_registrations_it_cannot_remove(self):  # 6
        (self.home / ".codex" / "config.toml").write_text('[mcp_servers.waypoint]\ncommand = "waypoint"\nargs = ["mcp"]\n',
                                                         encoding="utf-8")
        self.init()
        ran, *patches = self.mcp(which=False)
        with patches[0], patches[1], patches[2]:
            code, out = self.run_cli("rules", "--uninstall", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertIn("by hand", out)
        self.assertEqual(ran, [])

    def test_chart_reports_preview_rule_drift(self):  # 7
        self.init()
        f = self.home / ".gemini" / "GEMINI.md"
        f.parent.mkdir()
        f.write_text(f"{rules.BEGIN}\nan old waypoint rule\n{rules.END}\n", encoding="utf-8")
        self.assertEqual(rules.check(self.home, self.lb)["gemini-cli"], "drifted")
        f.unlink()  # a deleted rule still shows while Gemini CLI is on PATH
        with mock.patch("shutil.which", lambda exe: "/bin/gemini" if exe == "gemini" else None):
            self.assertEqual(rules.check(self.home, self.lb)["gemini-cli"], "missing")

    def test_every_wired_cli_tool_id_can_log(self):
        from waypoint import clis
        self.assertTrue(set(clis.CLIS) <= set(logbook.KNOWN_TOOLS))

class PluginConnect(Base):
    def test_plugin_text_names_every_install_step(self):
        from waypoint import clis
        self.assertEqual(set(clis.PLUGIN_STEPS), {"claude-code", "codex", "gemini-cli", "copilot-cli"})
        self.assertIn("claude plugin install waypoint@waypoint-mcp", clis.plugin_text("claude-code"))
        self.assertIn("/hooks", clis.plugin_text("codex"))  # Codex runs plugin hooks only once trusted
        self.assertIn("gemini extensions install https://github.com/STiFLeR7/waypoint-mcp", clis.plugin_text("gemini-cli"))
        self.assertIn("copilot plugin install STiFLeR7/waypoint-mcp", clis.plugin_text("copilot-cli"))

    def test_plugin_with_project_is_refused(self):
        self.init()
        code, out = self.run_cli("connect", "claude-code", "--plugin", "--project", str(self.root / "alpha"), "--yes",
                                 "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 1)
        self.assertIn("--plugin is user-wide", out)
        self.assertFalse((self.root / "alpha" / ".claude").exists())

    def test_plugin_installs_the_rule_and_prints_the_steps(self):
        self.init()
        code, out = self.run_cli("connect", "codex", "--plugin", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertIn("codex plugin add waypoint@waypoint-mcp", out)
        self.assertIn(rules.render(self.lb).splitlines()[0],
                      (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"))

    def test_plugin_wrong_default_logbook_names_the_plugin_not_no_mcp(self):
        self.init()
        with mock.patch.object(rules, "_real_home", lambda home: True):
            code, out = self.run_cli("connect", "codex", "--plugin", "--yes", "--logbook", str(self.lb),
                                     "--home", str(self.home))
        self.assertEqual(code, 1)
        self.assertIn("the plugin starts `waypoint mcp`", out)
        self.assertNotIn("--no-mcp", out)

    def test_plugin_warns_when_waypoint_is_another_server(self):
        self.init()
        (self.home / ".codex" / "config.toml").write_text('[mcp_servers.waypoint]\nurl = "https://example.invalid/mcp"\n',
                                                           encoding="utf-8")
        code, out = self.run_cli("connect", "codex", "--plugin", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertIn("is another server", out)
        self.assertIn("example.invalid", (self.home / ".codex" / "config.toml").read_text(encoding="utf-8"))
        code, out = self.run_cli("connect", "claude-code", "--plugin", "--yes", "--logbook", str(self.lb),
                                 "--home", str(self.home))
        self.assertNotIn("is another server", out)

    def test_plugin_plan_asks_only_to_unregister(self):
        self.init()
        with mock.patch.object(rules, "_plan_mcp", wraps=rules._plan_mcp) as spy:
            code, out = self.run_cli("connect", "claude-code", "--plugin", "--yes", "--logbook", str(self.lb),
                                     "--home", str(self.home))
        self.assertEqual(code, 0, out)
        spy.assert_called_once_with("claude-code", self.home.resolve(), register=False)


if __name__ == "__main__":
    unittest.main()
