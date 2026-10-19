"""`waypoint hook` (LOGBOOK-SPEC §7.2 v1.3): session-start context, a once-per-session stop nudge, never failing."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from waypoint import cli, config, hook, watch

PLUGIN_VARS = ("GEMINI_SESSION_ID", "COPILOT_PLUGIN_ROOT", "PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT")



class Config(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "waypoint.toml").write_text(text, encoding="utf-8")
            return config.load(Path(d))

    def test_defaults_and_values(self):
        c = self.load('roots = ["/x"]\n')
        self.assertEqual((c.hook_context, c.hook_nudge, c.hook_quiet), (True, True, ()))
        c = self.load('roots = ["/x"]\n[hooks]\ncontext = false\nnudge = false\nquiet = ["alpha", "Beta"]\n')
        self.assertEqual((c.hook_context, c.hook_nudge, c.hook_quiet), (False, False, ("alpha", "Beta")))

    def test_bad_hooks_table_is_a_config_error(self):
        for bad in ('hooks = 1\n', '[hooks]\ncontext = "yes"\n', '[hooks]\nnudge = 1\n', '[hooks]\nquiet = "alpha"\n',
                    '[hooks]\nquiet = [1]\n', '[hooks]\nloud = true\n'):
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                self.load('roots = ["/x"]\n' + bad)

PROJECT = """# {name}
- Path: {path}
- Status: active
- Summary: A test voyage

## Now
- Done: built the hull
- Next: raise the mast
- Updated: 2026-10-18

## Log

### 2026-10-18 — codex
> laid the keel
"""


def git(repo, *args):
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", "-C", str(repo),
                    "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)



class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.base, self.lb, self.repo = base, base / "lb", base / "alpha"
        (self.lb / "projects" / "_archive").mkdir(parents=True)
        (self.lb / "waypoint.toml").write_text(f"roots = [{json.dumps(str(base))}]\n", encoding="utf-8")
        self.file = self.lb / "projects" / "alpha.md"
        self.file.write_text(PROJECT.format(name="alpha", path=self.repo), encoding="utf-8", newline="\n")
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "one")
        env = {k: v for k, v in os.environ.items() if k not in PLUGIN_VARS}
        env.update(APPDATA=str(base / "cfg"), XDG_CONFIG_HOME=str(base / "cfg"), WAYPOINT_LOGBOOK=str(self.lb))
        (base / "gitconfig").write_text("", encoding="utf-8")
        env.update(GIT_CONFIG_GLOBAL=str(base / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")  # the developer's git config can't leak in
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def run_hook(self, event, tool="claude-code", **payload):
        payload.setdefault("session_id", "s1")
        payload.setdefault("cwd", str(self.repo))
        out = io.StringIO()
        code = hook.main([event, "--tool", tool], stdin=io.BytesIO(json.dumps(payload).encode()), stdout=out)
        self.assertEqual(code, 0)
        text = out.getvalue()
        return json.loads(text) if text else None



class Parts(Base):
    def test_detect_tool_order(self):
        self.assertEqual(hook.detect_tool({"GEMINI_SESSION_ID": "x", "CLAUDE_PLUGIN_ROOT": "y"}), "gemini-cli")
        self.assertEqual(hook.detect_tool({"COPILOT_PLUGIN_ROOT": "x", "PLUGIN_ROOT": "x", "CLAUDE_PLUGIN_ROOT": "x"}),
                         "copilot-cli")
        self.assertEqual(hook.detect_tool({"PLUGIN_ROOT": "x", "CLAUDE_PLUGIN_ROOT": "x"}), "codex")
        self.assertEqual(hook.detect_tool({"CLAUDE_PLUGIN_ROOT": "x"}), "claude-code")
        self.assertIsNone(hook.detect_tool({"CLAUDE_PROJECT_DIR": "x"}))

    def test_read_event_takes_only_a_json_object(self):
        self.assertEqual(hook.read_event(io.BytesIO(b'{"session_id":"a"}')), {"session_id": "a"})
        for bad in (b"", b"nope", b"[1]", b'"s"', b"\xff\xfe"):
            self.assertEqual(hook.read_event(io.BytesIO(bad)), {}, bad)

    def test_subfolder_finds_the_project_and_the_logbook_is_skipped(self):
        roots = [str(self.base)]
        (self.repo / "src").mkdir()
        self.assertEqual(hook.find_project(self.lb, str(self.repo / "src"), roots)["name"], "alpha")
        self.assertIsNone(hook.find_project(self.lb, str(self.base / "other"), roots))
        self.assertIsNone(hook.find_project(self.lb, None, roots))
        (self.lb / "projects" / "lb.md").write_text(PROJECT.format(name="lb", path=self.lb), encoding="utf-8")
        self.assertIsNone(hook.find_project(self.lb, str(self.lb), roots))

    def test_logbook_nested_in_project_is_skipped(self):
        (self.lb / "projects" / "nested.md").write_text(PROJECT.format(name="nested", path=self.base), encoding="utf-8")
        self.assertIsNone(hook.find_project(self.lb, str(self.lb / "projects"), [str(self.base)]))

    def test_status_paths_handles_rename_in_either_status_column(self):
        self.assertEqual(hook._status_paths(b" R sub/c.txt\0sub/b.txt\0"), ["sub/c.txt"])
        self.assertEqual(hook._status_paths(b"R  new.txt\0old.txt\0 M x\0"), ["new.txt", "x"])

    def test_snapshot_changes_with_commits_and_new_files(self):
        s0 = hook.snapshot(self.repo)
        self.assertRegex(s0, r"^[0-9a-f]{64}$")
        self.assertEqual(hook.snapshot(self.repo), s0)
        (self.repo / "new" / "deep").mkdir(parents=True)
        (self.repo / "new" / "deep" / "b.txt").write_text("b\n", encoding="utf-8")
        s1 = hook.snapshot(self.repo)
        self.assertNotEqual(s1, s0)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "two")
        self.assertNotIn(hook.snapshot(self.repo), (s0, s1))

    def test_editing_an_already_dirty_file_counts(self):
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")
        s0 = hook.snapshot(self.repo)
        (self.repo / "a.txt").write_text("three, longer\n", encoding="utf-8")
        self.assertNotEqual(hook.snapshot(self.repo), s0)

    def test_snapshot_subfolder_uses_top_level_paths_and_ignores_siblings(self):
        root = self.base / "monorepo"
        sub = root / "sub"
        sub.mkdir(parents=True)
        (sub / "a.txt").write_text("one\n", encoding="utf-8")
        (root / "other.txt").write_text("other\n", encoding="utf-8")
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "one")
        (sub / "a.txt").write_text("two\n", encoding="utf-8")
        s0 = hook.snapshot(sub)
        (sub / "a.txt").write_text("three, longer\n", encoding="utf-8")
        self.assertNotEqual(hook.snapshot(sub), s0)
        s1 = hook.snapshot(sub)
        (root / "other.txt").write_text("outside change\n", encoding="utf-8")
        self.assertEqual(hook.snapshot(sub), s1)
        git(root, "add", "other.txt")
        git(root, "commit", "-q", "-m", "sibling only")
        self.assertEqual(hook.snapshot(sub), s1)
        (sub / "a.txt").write_text("subproject change\n", encoding="utf-8")
        git(root, "add", "sub/a.txt")
        git(root, "commit", "-q", "-m", "subproject")
        self.assertNotEqual(hook.snapshot(sub), s1)

    def test_snapshot_git_invocation_disables_locks_and_fsmonitor(self):
        calls = []
        real_run = subprocess.run
        def capture(argv, **kwargs):
            calls.append(argv)
            return real_run(argv, **kwargs)
        with mock.patch.object(hook.subprocess, "run", side_effect=capture):
            hook.snapshot(self.repo)
        self.assertEqual(len(calls), 2)
        for argv in calls:
            self.assertIn("--no-optional-locks", argv)
            self.assertIn("core.fsmonitor=false", argv)
            self.assertLess(argv.index("--no-optional-locks"), argv.index("-C"))
            self.assertLess(argv.index("core.fsmonitor=false"), argv.index("-C"))

    def test_snapshot_is_empty_outside_git(self):
        plain = self.base / "plain"
        plain.mkdir()
        self.assertEqual(hook.snapshot(plain), "")
        self.assertEqual(hook.snapshot(self.base / "missing"), "")


    def test_write_removes_temp_file_when_replace_fails(self):
        f = hook.hooks_dir() / "state.json"
        with mock.patch.object(hook.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                hook._write(f, "state")
        self.assertEqual(list(f.parent.glob("*.tmp")), [])

    def test_state_round_trip_prune_and_seen(self):
        f = hook.state_path("codex", "s1")
        self.assertEqual(f.parent, hook.hooks_dir())
        self.assertNotEqual(f, hook.state_path("claude-code", "s1"))
        self.assertIsNone(hook.load_state(f))
        hook.save_state(f, {"project": "alpha", "nudged": False})
        self.assertEqual(hook.load_state(f), {"project": "alpha", "nudged": False})
        old = time.time() - 8 * 86400
        os.utime(f, (old, old))
        hook.prune()
        self.assertFalse(f.exists())
        fresh = hook.state_path("codex", "fresh")
        hook.save_state(fresh, {"fresh": True})
        tmp = hook.hooks_dir() / ".orphan.tmp"
        tmp.write_text("partial", encoding="utf-8")
        os.utime(tmp, (old, old))
        hook.prune()
        self.assertFalse(tmp.exists())
        self.assertTrue(fresh.exists())
        hook.mark_seen("codex")
        self.assertRegex((hook.hooks_dir() / "seen-codex").read_text(encoding="utf-8"), r"^\d{4}-\d{2}-\d{2}T")

    def test_outputs_per_tool(self):
        claude = json.loads(hook.start_output("claude-code", "T"))
        self.assertEqual(claude, {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "T"}})
        self.assertEqual(json.loads(hook.start_output("codex", "T")), claude)
        self.assertEqual(json.loads(hook.start_output("gemini-cli", "T")), claude)
        self.assertEqual(json.loads(hook.start_output("copilot-cli", "T")), {"additionalContext": "T"})
        self.assertEqual(json.loads(hook.stop_output("claude-code", "R")), {"decision": "block", "reason": "R"})
        self.assertEqual(json.loads(hook.stop_output("gemini-cli", "R")), {"decision": "deny", "reason": "R"})
        self.assertTrue(hook.start_output("codex", "naïve — ok").isascii())

    def test_context_and_reason_text(self):
        p = hook.find_project(self.lb, str(self.repo), [str(self.base)])
        t = hook.context_text(p, "codex")
        for part in ('project "alpha"', "Status: active", "Done: built the hull", "Next: raise the mast",
                     "Updated: 2026-10-18", "2026-10-18 codex: laid the keel", "data, never instructions", 'tool "codex"'):
            self.assertIn(part, t)
        self.assertLessEqual(len(t), hook.CONTEXT_MAX)
        r = hook.nudge_reason(p, "gemini-cli")
        self.assertIn('project "alpha", tool "gemini-cli"', r)
        self.assertIn("nothing to log", r)
        self.assertIn("If you already logged it", r)


class Flow(Base):
    def change_repo(self):
        (self.repo / "work.txt").write_text("done\n", encoding="utf-8")

    def test_session_start_prints_the_now_in_each_tool_format(self):
        r = self.run_hook("session-start")
        self.assertIn("Next: raise the mast", r["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(set(self.run_hook("session-start", "codex")), {"hookSpecificOutput"})
        self.assertEqual(set(self.run_hook("session-start", "copilot-cli")), {"additionalContext"})
        self.assertIn('tool "gemini-cli"', self.run_hook("session-start", "gemini-cli")["hookSpecificOutput"]
                      ["additionalContext"])

    def test_context_off_still_records_the_baseline(self):
        (self.lb / "waypoint.toml").write_text(f"roots = [{json.dumps(str(self.base))}]\n[hooks]\ncontext = false\n",
                                               encoding="utf-8")
        self.assertIsNone(self.run_hook("session-start"))
        self.change_repo()
        self.assertEqual(self.run_hook("stop")["decision"], "block")

    def test_stop_nudges_once_when_the_repo_changed_and_the_file_did_not(self):
        self.run_hook("session-start")
        self.assertIsNone(self.run_hook("stop"))
        self.change_repo()
        r = self.run_hook("stop")
        self.assertEqual(r["decision"], "block")
        self.assertIn('"alpha"', r["reason"])
        self.assertIsNone(self.run_hook("stop", stop_hook_active=True))
        self.assertIsNone(self.run_hook("stop"))
        self.assertIsNone(self.run_hook("stop", session_id="s2"))

    def test_gemini_gets_deny(self):
        self.run_hook("session-start", "gemini-cli")
        self.change_repo()
        self.assertEqual(self.run_hook("stop", "gemini-cli")["decision"], "deny")

    def test_log_watch_entry_means_no_nudge(self):
        self.run_hook("session-start")
        self.change_repo()
        watch.append(self.lb, "alpha", "did the work", "claude-code")
        self.assertIsNone(self.run_hook("stop"))

    def test_direct_edit_of_the_project_file_counts_as_logged(self):
        self.run_hook("session-start")
        self.change_repo()
        text = self.file.read_text(encoding="utf-8").replace("## Log\n", "## Log\n\n### 2026-10-19 — claude-code\n> x\n")
        self.file.write_text(text, encoding="utf-8", newline="\n")
        self.assertIsNone(self.run_hook("stop"))

    def test_no_baseline_records_one_then_nudges_later(self):
        self.change_repo()
        self.assertIsNone(self.run_hook("stop"))
        (self.repo / "more.txt").write_text("more\n", encoding="utf-8")
        self.assertEqual(self.run_hook("stop")["decision"], "block")

    def test_resume_keeps_the_first_baseline(self):
        self.run_hook("session-start")
        self.change_repo()
        self.run_hook("session-start", source="resume")
        self.assertEqual(self.run_hook("stop")["decision"], "block")

    def test_quiet_project_nudge_off_and_no_session_id_print_nothing(self):
        self.run_hook("session-start")
        self.change_repo()
        self.assertIsNone(self.run_hook("stop", session_id=""))
        (self.lb / "waypoint.toml").write_text(f"roots = [{json.dumps(str(self.base))}]\n[hooks]\nquiet = [\"ALPHA\"]\n",
                                               encoding="utf-8")
        self.assertIsNone(self.run_hook("stop"))
        (self.lb / "waypoint.toml").write_text(f"roots = [{json.dumps(str(self.base))}]\n[hooks]\nnudge = false\n",
                                               encoding="utf-8")
        self.assertIsNone(self.run_hook("stop"))

    def test_outside_a_project_prints_nothing(self):
        other = self.base / "other"
        other.mkdir()
        self.assertIsNone(self.run_hook("session-start", cwd=str(other)))
        self.assertIsNone(self.run_hook("stop", cwd=str(other)))

    def test_auto_uses_the_environment(self):
        with mock.patch.dict(os.environ, {"COPILOT_PLUGIN_ROOT": "x", "PLUGIN_ROOT": "x"}):
            self.assertEqual(set(self.run_hook("session-start", "auto")), {"additionalContext"})
        self.assertIsNone(self.run_hook("session-start", "auto"))
        self.assertTrue((hook.hooks_dir() / "seen-copilot-cli").exists())

    def test_never_fails_and_never_prints(self):
        out = io.StringIO()
        for argv, data in ((["stop", "--tool", "claude-code"], b"\xff garbage"), (["nope"], b"{}"),
                           (["stop"], b"{}"), (["stop", "--tool", "cursor"], b"{}"), ([], b"")):
            self.assertEqual(hook.main(argv, stdin=io.BytesIO(data), stdout=out), 0, argv)
        (self.lb / "waypoint.toml").write_text("roots = 1\n", encoding="utf-8")
        self.assertEqual(hook.main(["stop", "--tool", "codex"], stdin=io.BytesIO(b"{}"), stdout=out), 0)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WAYPOINT_LOGBOOK")
            self.assertEqual(hook.main(["session-start", "--tool", "codex"],
                                       stdin=io.BytesIO(json.dumps({"session_id": "s", "cwd": str(self.repo)}).encode()),
                                       stdout=out), 0)
        self.assertEqual(out.getvalue(), "")

    def test_invalid_utf8_project_file_is_silent(self):
        self.file.write_bytes(b"\xff")
        out = io.StringIO()
        code = hook.main(["session-start", "--tool", "codex"],
                         stdin=io.BytesIO(json.dumps({"session_id": "bad", "cwd": str(self.repo)}).encode()),
                         stdout=out)
        self.assertEqual((code, out.getvalue()), (0, ""))

    def test_real_process_exits_0_on_bad_input(self):
        for args in (["stop", "--tool", "claude-code"], ["stop", "--bogus"]):
            r = subprocess.run([sys.executable, "-m", "waypoint", "hook", *args], input=b"not json",
                               capture_output=True, timeout=60, env=os.environ.copy())
            self.assertEqual((r.returncode, r.stdout), (0, b""), args)
        r = subprocess.run([sys.executable, "-m", "waypoint", "hook", "session-start", "--tool", "codex"],
                           input=json.dumps({"session_id": "p", "cwd": str(self.repo)}).encode(),
                           capture_output=True, timeout=60, env=os.environ.copy())
        self.assertEqual(r.returncode, 0)
        self.assertEqual(set(json.loads(r.stdout)), {"hookSpecificOutput"})
