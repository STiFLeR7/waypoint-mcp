"""End-to-end: one realistic story through the public CLI and the real stdio MCP server (LOGBOOK-SPEC v1.1).

A temp "machine" (home with Claude Code transcripts and a Codex state DB, a code root, a logbook) goes through setup,
add, fix, an MCP session, Attention opt-in, archiving and uninstall. Throughout, nothing outside the temp folder is
written (a write guard plus fingerprints of the real home's rule files) and no AI CLI is ever run.
stdlib only; no network, no browser.
"""
import builtins
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from waypoint import chart, cli, config, rules
from waypoint.paths import real

DAY = 86400
AI_EXES = ("claude", "codex", "gemini", "copilot")
TOP_KEYS = {"schema", "generated", "config", "sources", "rules", "kpi", "projects", "archived", "untracked", "pulse", "feed"}
KPI_KEYS = {"tracked", "attention", "velocity", "stale", "untracked", "unlogged", "behind"}
PROJECT_KEYS = {"name", "file", "path", "status", "summary", "docs", "done", "next", "updated", "attention", "last", "last_tool",
                "idle", "claude", "codex", "git", "stale", "unlogged", "now_behind", "logs"}
TITLES = ("refactor app parser", "app codex thread", "very old app session", "old app work", "scratch idea",
          "tools session")
REAL_HOME_FILES = (".claude/rules/waypoint.md", ".claude/CLAUDE.md", ".codex/AGENTS.md", ".codex/AGENTS.override.md",
                   ".gemini/GEMINI.md", ".copilot/copilot-instructions.md")


def _fingerprint(paths):
    out = {}
    for p in paths:
        try:
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
        except OSError:
            out[str(p)] = None
    return out


class Story(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.home, self.code, self.lb = self.base / "home", self.base / "code", self.base / "lb"
        self.cfg, self.shims, self.elsewhere = self.base / "cfg", self.base / "bin", self.base / "elsewhere"
        self.now = float(int(time.time()))  # frozen for every in-process chart; the MCP server runs at real time
        self.real = [Path.home() / f for f in REAL_HOME_FILES] + [config.user_dir() / "logbook"]  # before redirecting
        self.real_before = _fingerprint(self.real)
        self.violations, self.spawned = [], []
        for d in (self.home / ".claude" / "projects", self.home / ".codex", self.cfg, self.shims, self.elsewhere,
                  *(self.code / n for n in ("app/src", "app-old", "tools", "scratch"))):
            d.mkdir(parents=True)
        self._shims()
        self._guard()

    def tearDown(self):
        self._stack.close()
        self._tmp.cleanup()

    # ---- the machine ----

    def _shims(self):
        """Fake AI CLIs on PATH: running any of them leaves a mark (they must never run)."""
        self.mark = self.base / "ran-an-ai-cli"
        for exe in ("claude", "codex"):  # the two tools setup wires; gemini/copilot absent, so `rules` is exact
            if os.name == "nt":
                (self.shims / f"{exe}.cmd").write_text(f'@echo {exe}>>"{self.mark}"\r\n', encoding="utf-8")
            else:
                f = self.shims / exe
                f.write_text(f"#!/bin/sh\necho {exe} >> '{self.mark}'\n", encoding="utf-8")
                f.chmod(0o755)

    def _guard(self):
        """Redirect config dirs to temp and refuse (and record) any in-process write outside the temp folder."""
        self._stack = ExitStack()
        git = shutil.which("git")
        path = os.pathsep.join([str(self.shims), *([str(Path(git).parent)] if git else [])])
        self._stack.enter_context(mock.patch.dict(os.environ, {"APPDATA": str(self.cfg), "XDG_CONFIG_HOME": str(self.cfg),
                                                               "PATH": path}))
        for k in ("WAYPOINT_LOGBOOK", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CLI_HOME", "COPILOT_HOME"):
            os.environ.pop(k, None)
        base = os.path.normcase(str(self.base))

        def inside(p):
            if isinstance(p, int):
                return True  # an already-open descriptor
            s = os.path.normcase(os.path.realpath(os.fspath(p)))
            return s == base or s.startswith(base + os.sep) or "__pycache__" in s

        def check(p, what):
            if not inside(p):
                self.violations.append(f"{what} {p}")
                raise PermissionError(f"e2e guard: {what} outside the temp machine: {p}")

        real_open, real_os_open, real_replace = builtins.open, os.open, os.replace
        real_unlink, real_remove, real_popen = os.unlink, os.remove, subprocess.Popen
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

        def g_open(file, mode="r", *a, **k):
            if any(c in mode for c in "wax+"):
                check(file, "open")
            return real_open(file, mode, *a, **k)

        def g_os_open(p, flags, *a, **k):
            if flags & write_flags:
                check(p, "os.open")
            return real_os_open(p, flags, *a, **k)

        def g_replace(src, dst, *a, **k):
            check(src, "replace")
            check(dst, "replace")
            return real_replace(src, dst, *a, **k)

        def g_unlink(p, *a, **k):
            check(p, "unlink")
            return real_unlink(p, *a, **k)

        def g_remove(p, *a, **k):
            check(p, "remove")
            return real_remove(p, *a, **k)

        def spy(args, *a, **k):
            argv = [args] if isinstance(args, (str, os.PathLike)) else list(args)
            self.spawned.append(Path(os.fspath(argv[0])).stem.lower())
            return real_popen(args, *a, **k)

        for target, name, fn in ((builtins, "open", g_open), (io, "open", g_open), (os, "open", g_os_open),
                                 (os, "replace", g_replace), (os, "unlink", g_unlink), (os, "remove", g_remove),
                                 (subprocess, "Popen", spy)):
            self._stack.enter_context(mock.patch.object(target, name, fn))
        frozen = types.SimpleNamespace(time=lambda: self.now, sleep=time.sleep, monotonic=time.monotonic)
        self._stack.enter_context(mock.patch.object(chart, "time", frozen))

    def claude(self, sid, cwd, age_days, title):
        d = self.home / ".claude" / "projects" / ("slug-" + Path(cwd).name)
        d.mkdir(exist_ok=True)
        f = d / f"{sid}.jsonl"
        lines = [{"type": "summary"}, {"type": "user", "cwd": str(cwd)}, {"type": "ai-title", "aiTitle": "draft"},
                 {"type": "ai-title", "aiTitle": title}]  # the last title wins
        f.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        when = self.now - age_days * DAY
        os.utime(f, (when, when))

    def codex(self, rows):
        con = sqlite3.connect(self.home / ".codex" / "state_5.sqlite")
        con.execute("create table threads (id, cwd, title, updated_at_ms, archived)")
        con.executemany("insert into threads values (?, ?, ?, ?, ?)",
                        [(i, str(cwd), title, int((self.now - age * DAY) * 1000), arch)
                         for i, (cwd, title, age, arch) in enumerate(rows)])
        con.commit()
        con.close()

    def day(self, age_days):
        return date.fromtimestamp(self.now - age_days * DAY).isoformat()

    # ---- driving it ----

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(builtins, "input", side_effect=AssertionError("prompted")):
            try:
                cli.main(list(args))
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                out.write(str(e.code or ""))
        return code, out.getvalue()

    def fix(self):
        code, out = self.run_cli("fix", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertIn("Dashboard refreshed", out)
        return self.check_chart()

    def mcp(self, *calls):
        """One real `python -m waypoint mcp` process: initialize, tools/list, then each call. Replies by id."""
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
        msgs += [c if "jsonrpc" in c else {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": c}
                 for i, c in enumerate(calls, start=2)]
        env = {k: v for k, v in os.environ.items()}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        p = subprocess.run([sys.executable, "-m", "waypoint", "mcp", "--logbook", str(self.lb)], cwd=self.elsewhere,
                           input="".join(json.dumps(m) + "\n" for m in msgs).encode(), capture_output=True, env=env,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr.decode(errors="replace"))
        replies = [json.loads(l) for l in p.stdout.decode("utf-8").splitlines()]  # stdout: protocol only
        self.assertEqual([r["id"] for r in replies], list(range(len(calls) + 2)))
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["take_fix", "read_chart", "log_watch", "status", "eod_report"])
        self.assert_private(p.stdout.decode("utf-8"))
        return replies[2:]

    def assert_private(self, raw):
        """MCP output never holds paths or session titles (§8.2)."""
        for s in (str(self.base), self.base.as_posix(), json.dumps(str(self.base))[1:-1], *TITLES):
            self.assertNotIn(s, raw)

    def check_chart(self):
        """dashboard.json is schema 1 with consistent counts; DASHBOARD.md is from the same generation (§5)."""
        d = json.loads((self.lb / "dashboard.json").read_text(encoding="utf-8"))
        md = (self.lb / "DASHBOARD.md").read_text(encoding="utf-8")
        self.assertEqual(d["schema"], 1)
        self.assertEqual(set(d), TOP_KEYS)
        self.assertEqual(set(d["kpi"]), KPI_KEYS)
        self.assertEqual(d["generated"], self.now)
        page = (self.lb / "chart.html").read_text(encoding="utf-8")  # chart.html embeds the same generation (§5.2)
        self.assertEqual(json.loads(page.split('id="waypoint-data">', 1)[1].split("</script>", 1)[0]), d)
        self.assertIn(f"# Chart (taken {datetime.fromtimestamp(d['generated']):%Y-%m-%d %H:%M})", md)
        for p in d["projects"]:
            self.assertEqual(set(p), PROJECT_KEYS)
        k, live = d["kpi"], d["projects"]
        self.assertEqual(k["tracked"], len(live))
        self.assertEqual(k["untracked"], len(d["untracked"]))
        self.assertEqual(k["unlogged"], sum(1 for p in live if p["unlogged"]))
        self.assertEqual(k["behind"], sum(1 for p in live if p["now_behind"]))
        self.assertEqual(k["attention"], sum(len(p["attention"]) for p in live))
        self.assertEqual(k["stale"], sum(p["stale"] for p in live))
        self.assertEqual(len(d["pulse"]), d["config"]["pulse_days"])
        self.assertEqual(max(d["pulse"]), date.fromtimestamp(self.now).isoformat())
        for e in d["feed"]:
            self.assertEqual(set(e), {"ts", "tool", "kind", "project", "text"})
            self.assertIn(e["kind"], ("session", "log"))
        self.assertEqual(d["feed"], sorted(d["feed"], key=lambda e: (-e["ts"], e["project"], e["tool"])))
        self.assertTrue(set(d["sources"]) >= {"claude-code", "codex", "git", "logbook"})
        return d

    def project_files(self):
        return {f.relative_to(self.lb).as_posix(): f.read_bytes() for f in (self.lb / "projects").rglob("*.md")}

    # ---- the story ----

    def test_story(self):
        app, old, tools, scratch = (self.code / n for n in ("app", "app-old", "tools", "scratch"))
        # 1. history on the machine
        self.claude("c-app-src", app / "src", 3, TITLES[0])   # nested folder: belongs to app
        self.claude("c-app-old", app, 12, TITLES[2])          # before app's log entry: not unlogged
        self.claude("c-old", old, 1, TITLES[3])               # sibling prefix: never app's
        self.claude("c-tools", tools, 5, TITLES[5])
        self.codex([(app, TITLES[1] + "\nsecond line", 2, 0), (scratch, TITLES[4], 1, 1)])  # archived threads count
        codex_dir, claude_dir = self.home / ".codex", self.home / ".claude"
        user_agents = b"# My Codex notes\r\nKeep this line.\r\n"
        user_claude = b"# My Claude notes\n\nKeep me too.\n"
        (codex_dir / "AGENTS.md").write_bytes(user_agents)
        (claude_dir / "CLAUDE.md").write_bytes(user_claude)

        # 2. setup, rules in sync, rerun is a no-op
        setup = ("setup", "--yes", "--logbook", str(self.lb), "--root", str(self.code), "--tool", "claude-code",
                 "--tool", "codex", "--no-mcp", "--home", str(self.home))
        code, out = self.run_cli(*setup)
        self.assertEqual(code, 0, out)
        self.assertIn("logbook not remembered", out)
        self.assertFalse((self.cfg / "waypoint").exists())  # --home isn't the real home: nothing remembered
        rule_file = claude_dir / "rules" / "waypoint.md"
        self.assertEqual(rule_file.read_text(encoding="utf-8"), rules.claude_file_text(self.lb))
        self.assertTrue((codex_dir / "AGENTS.md").read_bytes().startswith(user_agents))
        self.assertIn(rules.BEGIN.encode(), (codex_dir / "AGENTS.md").read_bytes())
        self.assertEqual((claude_dir / "CLAUDE.md").read_bytes(), user_claude)
        code, out = self.run_cli("rules", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        states = dict(l.split(": ", 1) for l in out.splitlines()[-3:])
        self.assertEqual({k: v.split(" ")[0] for k, v in states.items()},
                         {"logbook": "in-sync", "claude-code": "in-sync", "codex": "in-sync"})
        snapshot = {f: f.read_bytes() for f in (rule_file, codex_dir / "AGENTS.md")}
        code, out = self.run_cli(*setup)
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing to change", out)
        self.assertEqual({f: f.read_bytes() for f in snapshot}, snapshot)

        # 3. track two projects; the owner's agent fills in app's file and both get a log entry
        for folder in (app, tools):
            code, out = self.run_cli("add", str(folder), "--logbook", str(self.lb))
            self.assertEqual(code, 0, out)
        self.assertNotIn("### ", (self.lb / "projects" / "app.md").read_text(encoding="utf-8"))  # add never logs
        app_md, tools_md = self.lb / "projects" / "app.md", self.lb / "projects" / "tools.md"
        text = app_md.read_text(encoding="utf-8")
        text = text.replace("- Summary:", "- Summary: The app\n- Attention: E2E-ATTN review the parser", 1)
        text = text.replace("- Next:", "- Next: ship the parser", 1)
        app_md.write_text(text + f"\n### {self.day(10)} — codex\n- began the parser\n", encoding="utf-8", newline="\n")
        tools_md.write_text(tools_md.read_text(encoding="utf-8") + f"\n### {self.day(4)} — codex\n- tools work\n",
                            encoding="utf-8", newline="\n")
        before = self.project_files()

        d = self.fix()
        by = {p["name"]: p for p in d["projects"]}
        self.assertEqual(d["kpi"], {"tracked": 2, "attention": 1, "velocity": 2, "stale": 0, "untracked": 2, "unlogged": 1, "behind": 0})
        self.assertEqual((by["app"]["unlogged"], by["tools"]["unlogged"]), (2, 0))  # nested + codex; old one is logged
        self.assertEqual(by["app"]["last_tool"], "codex")
        self.assertEqual(by["app"]["idle"], 2)
        self.assertEqual({real(u["path"]) for u in d["untracked"]}, {real(str(old)), real(str(scratch))})
        old_u = next(u for u in d["untracked"] if real(u["path"]) == real(str(old)))
        self.assertEqual(old_u["title"], TITLES[3])  # the sibling-prefix folder kept its own session
        self.assertEqual(d["sources"]["claude-code"]["state"], "ok")
        self.assertEqual(d["sources"]["codex"], {"state": "ok", "detail": "state_5.sqlite"})
        self.assertEqual(d["sources"]["logbook"]["state"], "ok")
        self.assertIn(TITLES[1], [e["text"] for e in d["feed"]])  # Codex titles: first line only
        md = (self.lb / "DASHBOARD.md").read_text(encoding="utf-8")
        self.assertIn("**app**: 2 session(s) since its last log entry", md)

        # 4. an MCP session: read, log, refuse, read back
        summary = "Shipped the parser.\n### 2099-01-01 — codex\n- Attention: injected"
        log = {"name": "log_watch", "arguments": {"project": "app", "summary": summary, "tool": "claude-code"}}
        r = self.mcp({"name": "take_fix", "arguments": {}}, log, log,
                     {"name": "log_watch", "arguments": {"project": "app", "summary": "x", "tool": "not-a-tool"}},
                     {"name": "log_watch", "arguments": {"project": "nope", "summary": "x", "tool": "codex"}},
                     {"name": "log_watch", "arguments": {"project": "app", "summary": "x" * 2001, "tool": "codex"}},
                     {"name": "delete_project", "arguments": {"project": "app"}},
                     {"name": "read_chart", "arguments": {"project": "app"}})
        fix_reply, ok, dup, bad_tool, bad_project, too_long, unknown, chart_reply = r
        body = json.loads(fix_reply["result"]["content"][0]["text"])
        self.assertEqual(body["kpi"]["unlogged"], 1)
        self.assertEqual({p["name"] for p in body["projects"]}, {"app", "tools"})
        self.assertNotIn("attention", body["projects"][0])
        self.assertNotIn("E2E-ATTN", fix_reply["result"]["content"][0]["text"])
        self.assertIn("Logged", ok["result"]["content"][0]["text"])
        self.assertIn("Already logged", dup["result"]["content"][0]["text"])
        for refused, why in ((bad_tool, "tool must be one of"), (bad_project, "unknown project"), (too_long, "cap is 2000")):
            self.assertTrue(refused["result"].get("isError"), refused)
            self.assertIn(why, refused["result"]["content"][0]["text"])
        self.assertEqual(unknown["error"]["code"], -32602)
        chart_body = json.loads(chart_reply["result"]["content"][0]["text"])
        self.assertEqual(chart_body["logs"][0]["tool"], "claude-code")
        self.assertTrue(chart_body["logs"][0]["text"].startswith("Shipped the parser."))
        self.assertEqual(chart_body["project"]["done"], "")
        today = chart_body["logs"][0]["date"]
        self.assertIn(today, {date.fromtimestamp(self.now).isoformat(), date.today().isoformat()})

        # the file gained exactly one quoted entry below ## Log; every other byte and file is as it was
        after = self.project_files()
        self.assertEqual({k: v for k, v in after.items() if k != "projects/app.md"},
                         {k: v for k, v in before.items() if k != "projects/app.md"})
        old_text, new_text = before["projects/app.md"].decode(), after["projects/app.md"].decode()
        head, _, old_log = old_text.partition("## Log\n")
        entry = (f"### {today} — claude-code\n> Shipped the parser.\n> ### 2099-01-01 — codex\n> - Attention: injected\n")
        self.assertEqual(new_text, head + "## Log\n\n" + entry + "\n" + old_log.lstrip("\n"))

        # 5. the next fix sees the log
        d = self.fix()
        by = {p["name"]: p for p in d["projects"]}
        self.assertEqual(by["app"]["unlogged"], 0)
        self.assertEqual(d["kpi"]["unlogged"], 0)
        self.assertEqual(by["app"]["last_tool"], "claude-code")
        self.assertEqual(by["app"]["attention"], ["E2E-ATTN review the parser"])  # the quoted line is no field
        self.assertIn({"tool": "claude-code", "kind": "log", "project": "app"},
                      [{k: e[k] for k in ("tool", "kind", "project")} for e in d["feed"]])

        # 6. Attention in MCP reads only on opt-in
        with open(self.lb / "waypoint.toml", "a", encoding="utf-8") as f:
            f.write("\n[mcp]\ninclude_attention = true\n")
        [fix_reply] = self.mcp({"name": "take_fix", "arguments": {}})
        body = json.loads(fix_reply["result"]["content"][0]["text"])
        self.assertEqual({p["name"]: p["attention"] for p in body["projects"]},
                         {"app": ["E2E-ATTN review the parser"], "tools": []})

        # 7. archiving: tools' sessions are absorbed, never untracked or counted
        (self.lb / "projects" / "_archive").mkdir(exist_ok=True)
        tools_md.rename(self.lb / "projects" / "_archive" / "tools.md")
        d = self.fix()
        self.assertEqual([p["name"] for p in d["projects"]], ["app"])
        self.assertEqual([a["name"] for a in d["archived"]], ["tools"])
        self.assertEqual(d["kpi"]["tracked"], 1)
        self.assertEqual(d["kpi"]["untracked"], 2)
        self.assertNotIn(real(str(tools)), {real(u["path"]) for u in d["untracked"]})
        self.assertFalse([e for e in d["feed"] if e["project"] == "tools" or e["text"] in (TITLES[5], "tools work")])
        events = sum(sum(day.values()) for day in d["pulse"].values())
        self.assertEqual(events, 7)  # app: 3 sessions + 2 log entries; untracked: 1 Claude + 1 Codex session
        [listed] = self.mcp({"name": "take_fix", "arguments": {}})
        self.assertEqual([p["name"] for p in json.loads(listed["result"]["content"][0]["text"])["projects"]], ["app"])

        # 8. uninstall removes only Waypoint's file and block
        code, out = self.run_cli("rules", "--uninstall", "--yes", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertEqual(code, 0, out)
        self.assertFalse(rule_file.exists())
        self.assertEqual((codex_dir / "AGENTS.md").read_bytes(), user_agents)
        self.assertEqual((claude_dir / "CLAUDE.md").read_bytes(), user_claude)
        code, out = self.run_cli("rules", "--logbook", str(self.lb), "--home", str(self.home))
        self.assertIn("claude-code: missing", out)
        self.assertIn("codex: missing", out)

        # 9. safety, end to end
        self.assertEqual(self.violations, [])
        self.assertFalse(self.mark.exists(), "an AI CLI was run")
        self.assertFalse(set(self.spawned) & set(AI_EXES), self.spawned)
        self.assertEqual(_fingerprint(self.real), self.real_before)
        self.assertFalse(list(self.lb.glob(".waypoint.lock")))


if __name__ == "__main__":
    unittest.main()
