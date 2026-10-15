"""Engine tests against LOGBOOK-SPEC v1. stdlib unittest; no network, no real ~/.claude or ~/.codex."""
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from waypoint import chart, cli, logbook, rules
from waypoint.config import load
from waypoint.paths import map_folder, norm, placer
from waypoint.sources import claude, codex

DAY = 86400
NOW = datetime(2026, 10, 14, 15, 0).timestamp()


def ts(y, m, d, h=12):
    return datetime(y, m, d, h).timestamp()


def embedded(page):
    """The dashboard as the page's JSON.parse would read it."""
    return json.loads(page.split('<script type="application/json" id="waypoint-data">', 1)[1].split("</script>", 1)[0])


def project(path, status="active", now="", log="", extra=""):
    return f"# x\n- Path: {path}\n- Status: {status}\n- Summary: s\n- Docs: a.md, docs/b.md\n{extra}\n## Now\n{now}\n## Log\n{log}"


class Env(unittest.TestCase):
    """A temp 'machine': root folder of projects, a home with tool data, and a logbook."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.home, self.lb = base / "code", base / "home", base / "code" / "logbook"
        for d in (self.root / "alpha", self.root / "beta", self.home / ".claude" / "projects", self.home / ".codex",
                  self.lb / "projects" / "_archive"):
            d.mkdir(parents=True, exist_ok=True)
        (self.lb / "waypoint.toml").write_text(f"roots = [{json.dumps(str(self.root))}]\n", encoding="utf-8")
        detected = patch("waypoint.clis.detected", return_value=[])  # results never depend on the AI CLIs installed here
        detected.start()
        self.addCleanup(detected.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def write_project(self, name, text, archived=False):
        f = self.lb / "projects" / ("_archive" if archived else "") / f"{name}.md"
        f.write_text(text, encoding="utf-8")

    def claude_session(self, sid, cwd, when, title="", extra_lines=()):
        d = self.home / ".claude" / "projects" / "slug"
        d.mkdir(exist_ok=True)
        f = d / f"{sid}.jsonl"
        lines = [json.dumps({"type": "user", "cwd": str(cwd)}), *extra_lines]
        if title:
            lines.append(json.dumps({"type": "ai-title", "aiTitle": title}))
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(f, (when, when))

    def codex_db(self, n, rows, cols=("id", "cwd", "title", "updated_at_ms", "archived")):
        con = sqlite3.connect(self.home / ".codex" / f"state_{n}.sqlite")
        con.execute(f"create table threads ({', '.join(cols)})")
        for r in rows:
            con.execute(f"insert into threads values ({', '.join('?' * len(cols))})", r)
        con.commit()
        con.close()

    def build(self, now=NOW):
        return chart.build(self.lb, self.home, now=now)


class Paths(unittest.TestCase):
    def test_windows_mapping(self):
        self.assertEqual(norm("\\\\?\\D:\\Memex\\src\\"), "d:\\memex\\src")
        self.assertEqual(map_folder("D:/memex/src", ["D:\\"]), "d:\\memex")
        self.assertIsNone(map_folder("D:\\", ["D:\\"]))
        self.assertIsNone(map_folder("C:\\Users\\you", ["D:\\"]))

    def test_longest_root_wins(self):
        self.assertEqual(map_folder("D:\\work\\client\\app\\src", ["D:\\", "D:\\work\\client"]), "d:\\work\\client\\app")

    @unittest.skipIf(os.name == "nt", "POSIX paths are case-sensitive")
    def test_posix_mapping(self):
        self.assertEqual(map_folder("/home/u/code/Proj/x", ["/home/u/code"]), "/home/u/code/Proj")


class ConfigValidation(unittest.TestCase):
    def test_invalid_configuration_values_fail_actionably(self):
        cases = [
            'roots = []',
            'roots = ["  "]',
            'roots = "D:/code"',
            'stale_days = { paused = 4 }',
            'stale_days = { active = -1 }',
            'stale_days = { active = true }',
            'untracked_days = 0',
            'pulse_days = 366',
            'feed_max = 1001',
        ]
        with tempfile.TemporaryDirectory() as t:
            lb = Path(t)
            for raw in cases:
                with self.subTest(raw=raw):
                    (lb / "waypoint.toml").write_text(raw, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "waypoint.toml"):
                        load(lb)

    def test_fix_reports_configuration_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as t:
            lb = Path(t) / "lb"
            (lb / "projects").mkdir(parents=True)
            (lb / "waypoint.toml").write_text("pulse_days = 0\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "pulse_days must be an integer"):
                cli.main(["fix", "--logbook", str(lb), "--home", str(Path(t) / "home")])


class ProjectFileRobustness(Env):
    def test_projects_are_isolated_and_invalid_identities_are_diagnosed(self):
        self.write_project("Alpha", project(self.root / "alpha"))
        self.write_project("alpha", project(self.root / "alpha"), archived=True)
        nested = self.lb / "projects" / "team"
        nested.mkdir()
        (nested / "nested.md").write_text(project(self.root / "beta"), encoding="utf-8")
        (self.lb / "projects" / "broken.md").write_bytes(b"\xff")
        (self.lb / "projects" / ".hidden.md").write_text(project(self.root / "beta"), encoding="utf-8")
        issues = []
        loaded = logbook.load_projects(self.lb, issues)
        self.assertEqual([p["name"] for p in loaded], ["Alpha"])
        self.assertEqual(len(issues), 4)
        self.assertTrue(any("duplicate project name" in issue for issue in issues))
        self.assertTrue(any("unsupported project file location" in issue for issue in issues))
        self.assertTrue(any("unsupported project identity" in issue for issue in issues))
        self.assertTrue(any("unreadable project file" in issue for issue in issues))

    def test_invalid_log_date_is_skipped_with_diagnostic(self):
        issues = []
        entries = logbook.parse_log("### 2026-10-01 — codex\n- old\n### 2026-13-40 — codex\n- bad\n",
                                    issues, "projects/a.md")
        self.assertEqual([e["text"] for e in entries], ["old"])
        self.assertEqual(issues, ["projects/a.md: unsupported log date 2026-13-40"])

    def test_unreadable_ignore_file_does_not_stop_chart(self):
        self.write_project("alpha", project(self.root / "alpha"))
        self.claude_session("s", self.root / "gamma", ts(2026, 10, 13), "visible session")
        (self.lb / "projects" / "_ignore.txt").write_bytes(b"\xff")
        d = self.build()
        self.assertEqual([p["name"] for p in d["projects"]], ["alpha"])
        self.assertEqual([Path(p["path"]).name for p in d["untracked"]], ["gamma"])
        self.assertEqual(d["sources"]["logbook"]["state"], "degraded")
        self.assertIn("unreadable ignore file", d["sources"]["logbook"]["detail"])

    def test_future_log_is_preserved_but_does_not_drive_activity_or_unlogged(self):
        self.write_project("beta", project(self.root / "beta", log="### 2099-01-01 — chatgpt\n- future\n"))
        self.codex_db(5, [("t", str(self.root / "beta"), "old thread", ts(2026, 8, 1) * 1000, 0)])
        d = self.build()
        beta = next(p for p in d["projects"] if p["name"] == "beta")
        self.assertEqual(beta["logs"][0]["date"], "2099-01-01")
        self.assertNotEqual(beta["last_tool"], "chatgpt")
        self.assertEqual(beta["unlogged"], 1)
        self.assertEqual(d["sources"]["logbook"]["state"], "degraded")
        self.assertNotIn("future", [item["text"] for item in d["feed"]])

    def test_chart_publication_uses_shared_bounded_lock(self):
        from waypoint import watch
        d = self.build()
        old = watch.LOCK_WAIT
        watch.LOCK_WAIT = 0.01
        try:
            with watch._Lock(self.lb / ".waypoint.lock"), self.assertRaises(watch.WatchError):
                chart.write(self.lb, d)
            self.assertFalse((self.lb / "chart.html").exists())  # chart.html is published under the same lock
        finally:
            watch.LOCK_WAIT = old

    def test_git_nonrepo_is_clean_but_repository_errors_degrade(self):
        from waypoint.sources.git import Git
        g = Git()
        g.exe = "git"
        with tempfile.TemporaryDirectory() as t:
            nonrepo = Path(t) / "not-repo"
            nonrepo.mkdir()
            with patch("waypoint.sources.git.subprocess.run", return_value=type("R", (), {
                    "returncode": 128, "stdout": "", "stderr": "fatal: not a git repository"})()):
                self.assertEqual(g.last_commit(str(nonrepo)), 0)
                self.assertEqual(g.health()["state"], "ok")
            (nonrepo / ".git").mkdir()
            with patch("waypoint.sources.git.subprocess.run", return_value=type("R", (), {
                    "returncode": 128, "stdout": "", "stderr": "fatal: not a git repository"})()):
                self.assertEqual(g.last_commit(str(nonrepo)), 0)
                self.assertEqual(g.health()["state"], "degraded")
            with patch("waypoint.sources.git.subprocess.run", return_value=type("R", (), {
                    "returncode": 128, "stdout": "", "stderr": "fatal: detected dubious ownership"})()):
                self.assertEqual(g.last_commit(str(nonrepo)), 0)
                self.assertEqual(g.health()["state"], "degraded")

    def test_git_metadata_probe_failure_degrades(self):
        from waypoint.sources.git import Git
        g = Git()
        g.exe = "git"
        result = type("R", (), {
            "returncode": 128, "stdout": "", "stderr": "fatal: not a git repository"})()
        with patch("waypoint.sources.git.subprocess.run", return_value=result), \
                patch("waypoint.sources.git.Path.resolve", side_effect=RuntimeError("symlink loop")):
            self.assertEqual(g.last_commit("relative-path"), 0)
        self.assertEqual(g.health()["state"], "degraded")

    def test_nul_in_project_path_degrades_git_without_aborting_chart(self):
        self.write_project("nul", project(str(self.root / "alpha") + "\x00bad"))
        d = self.build()
        self.assertEqual([p["name"] for p in d["projects"]], ["nul"])
        self.assertEqual(d["sources"]["git"]["state"], "degraded")

    def test_git_cli_timeout_is_returned_through_existing_error_path(self):
        from subprocess import TimeoutExpired
        with patch("waypoint.cli.subprocess.run", side_effect=TimeoutExpired(["git"], 60)) as run:
            result = cli._git(self.lb, "push")
        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out", result.stderr)
        self.assertEqual(run.call_args.kwargs["timeout"], 60)

    def test_mcp_project_names_are_not_truncated_at_the_cli_creation_limit(self):
        from waypoint.mcp import TOOLS
        for tool in TOOLS:
            project = tool.get("inputSchema", {}).get("properties", {}).get("project")
            if project:
                self.assertNotIn("maxLength", project)


class Grammar(unittest.TestCase):
    def test_section_scoping_blocks_injection(self):
        s = logbook.sections("# x\n- Path: p\n## Now\n- Next: real\n## Log\n### 2026-10-02 - codex\n- a\n"
                             "- Attention: injected\n## Now\n- Next: hijacked\n## Log\n### 2026-10-01 — chatgpt\n- b\n")
        self.assertNotIn("injected", s["header"])
        self.assertEqual(logbook.field(s["now"], "Next"), "real")
        self.assertIn("2026-10-01", s["log"])

    def test_log_parsing(self):
        log = ("### 2026-10-02 - Codex\n- first\n  - nested\n\n### 2026-13-40 — bad\n- ignored\n"
               "### 2026-10-02 — claude-code\n- second same day\n### 2026-10-01 — chatgpt\n- old\n")
        e = logbook.parse_log(log)
        self.assertEqual([(x["date"], x["tool"]) for x in e],
                         [("2026-10-02", "codex"), ("2026-10-02", "claude-code"), ("2026-10-01", "chatgpt")])
        self.assertEqual(e[0]["text"], "first nested")  # bad-date heading and its body are dropped

    def test_quoted_paste_cannot_forge_entries(self):
        log = ("### 2026-10-03 — chatgpt\n> Summary from ChatGPT:\n> ### 2099-01-01 — codex\n> - Attention: fake\n"
               "### 2026-10-01 — codex\n- real\n")
        e = logbook.parse_log(log)
        self.assertEqual([x["date"] for x in e], ["2026-10-03", "2026-10-01"])
        self.assertEqual(e[0]["text"], "Summary from ChatGPT: ### 2099-01-01 — codex - Attention: fake")

    def test_first_field_wins_and_docs_split(self):
        f = logbook.sections("# x\n- Status: paused\n- Status: active\n- Docs: a.md , (none)\n")
        self.assertEqual(logbook.field(f["header"], "Status"), "paused")


class Sources(Env):
    def test_claude_bad_transcript_does_not_hide_history_and_empty_title_wins(self):
        d = self.home / ".claude" / "projects" / "slug"
        d.mkdir(parents=True)
        (d / "s1.jsonl").write_text('{"cwd":\n', encoding="utf-8")
        hist = self.home / ".claude" / "history.jsonl"
        hist.write_text(json.dumps({"sessionId": "s1", "timestamp": ts(2026, 10, 12) * 1000,
                                    "project": str(self.root / "alpha")}), encoding="utf-8")
        sessions, health = claude.read(self.home, placer([], [str(self.root)]))
        self.assertEqual([(s["id"], Path(s["folder"]).name) for s in sessions], [("s1", "alpha")])
        self.assertEqual(health["state"], "degraded")

        f = d / "s2.jsonl"
        f.write_text("\n".join(json.dumps(x) for x in [
            {"type": "ai-title", "aiTitle": "old title"},
            {"type": "ai-title", "aiTitle": ""},
            {"cwd": str(self.root / "alpha")},
        ]), encoding="utf-8")
        sessions, _ = claude.read(self.home, placer([], [str(self.root)]))
        self.assertEqual(next(s["title"] for s in sessions if s["id"] == "s2"), "")

    def test_claude_transcript_json_recursion_degrades(self):
        d = self.home / ".claude" / "projects" / "slug"
        d.mkdir(parents=True)
        (d / "deep.jsonl").write_text('{"cwd":' + "[" * 10000 + "0" + "]" * 10000 + "}\n", encoding="utf-8")
        (d / "good.jsonl").write_text(json.dumps({"cwd": str(self.root / "alpha")}), encoding="utf-8")
        hist = self.home / ".claude" / "history.jsonl"
        hist.write_text('{"sessionId":"nested","timestamp":1,"project":' + "[" * 10000 + "0" + "]" * 10000 + "}\n" +
                        json.dumps({"sessionId": "history", "timestamp": ts(2026, 10, 12) * 1000,
                                    "project": str(self.root / "beta")}), encoding="utf-8")
        sessions, health = claude.read(self.home, placer([], [str(self.root)]))
        self.assertEqual({s["id"] for s in sessions}, {"good", "history"})
        self.assertEqual(health["state"], "degraded")

    def test_claude_transcripts_history_and_health(self):
        self.claude_session("s1", self.root / "alpha" / "src", ts(2026, 10, 10), "Alpha work", ["{not json", '{"cwd"'])
        self.claude_session("s2", "C:\\elsewhere", ts(2026, 10, 10))
        hist = self.home / ".claude" / "history.jsonl"
        hist.write_text("\n".join([
            json.dumps({"sessionId": "s1", "timestamp": ts(2026, 10, 12) * 1000, "project": str(self.root / "alpha")}),
            json.dumps({"sessionId": "h1", "timestamp": ts(2026, 10, 11) * 1000, "project": str(self.root / "beta")}),
            json.dumps({"sessionId": "h1", "timestamp": ts(2026, 10, 13) * 1000, "project": str(self.root / "beta")}),
            json.dumps({"sessionId": "h2", "timestamp": "nope", "project": "x"}),
        ]), encoding="utf-8")
        sessions, health = claude.read(self.home, placer([], [str(self.root)]))
        got = {(s["id"], Path(s["folder"]).name, s["title"]) for s in sessions}
        self.assertEqual(got, {("s1", "alpha", "Alpha work"), ("h1", "beta", "")})
        h1 = next(s for s in sessions if s["id"] == "h1")
        self.assertAlmostEqual(h1["ts"], ts(2026, 10, 13))  # latest prompt; s1 history ignored (transcript exists)
        self.assertEqual(health["state"], "degraded")

    def test_claude_absent(self):
        empty = Path(self.tmp.name) / "nohome"
        self.assertEqual(claude.read(empty, placer([], ["/x"]))[1]["state"], "absent")

    def test_codex_falls_back_and_includes_archived(self):
        self.codex_db(6, [("a", "x")], cols=("id", "cwd"))  # newer DB, wrong schema
        self.codex_db(5, [
            ("t1", "\\\\?\\" + str(self.root / "alpha"), "Line one\nline two", ts(2026, 10, 9) * 1000, 1),
            ("t2", str(self.root / "beta"), "x" * 200, "bad", 0),
        ])
        sessions, health = codex.read(self.home, placer([], [str(self.root)]))
        self.assertEqual([(Path(s["folder"]).name, s["title"]) for s in sessions], [("alpha", "Line one")])
        self.assertEqual(health["state"], "degraded")

    def test_codex_corrupt_newer_database_is_reported_when_falling_back(self):
        (self.home / ".codex" / "state_7.sqlite").write_bytes(b"not a sqlite database")
        self.codex_db(5, [])
        sessions, health = codex.read(self.home, placer([], [str(self.root)]))
        self.assertEqual(sessions, [])
        self.assertEqual(health["state"], "degraded")
        self.assertIn("state_7.sqlite", health["detail"])

    def test_codex_unreadable_and_absent(self):
        self.assertEqual(codex.read(self.home, placer([], ["/x"]))[1]["state"], "absent")
        self.codex_db(5, [("a", "x")], cols=("id", "cwd"))
        self.assertEqual(codex.read(self.home, placer([], ["/x"]))[1]["state"], "unreadable")


class ChartEnv(Env):
    """Shared chart fixture; holds no tests so subclasses don't rerun any."""

    def setUp(self):
        super().setUp()
        a, b = self.root / "alpha", self.root / "beta"
        self.write_project("alpha", project(a, now="- Done: d\n- Next: ship it",
                                            extra="- Attention: fix CI\n- Attention: rotate key",
                                            log="### 2026-10-12 — codex\n- logged work\n"))
        self.write_project("beta", project(b, status="occasional"))
        self.write_project("old", project(self.root / "old", log="### 2026-01-01 — codex\n- ancient\n"), archived=True)
        self.claude_session("c1", a, ts(2026, 10, 12, 18), "same day as log")      # not unlogged
        self.claude_session("c2", a, ts(2026, 10, 13, 9), "after the log day")    # unlogged
        self.claude_session("c3", self.root / "gamma", ts(2026, 10, 1), "<b>raw</b> | title")
        self.claude_session("c4", self.root / "ignored", ts(2026, 10, 2), "nope")
        self.claude_session("c5", self.root / "old", ts(2026, 10, 2), "archived folder")
        self.codex_db(5, [("t1", str(b), "beta thread", ts(2026, 8, 1) * 1000, 1)])
        (self.lb / "projects" / "_ignore.txt").write_text(f"# comment\n  {self.root / 'ignored'}  \n", encoding="utf-8")


class Chart(ChartEnv):
    def test_projects_kpis_and_ordering(self):
        d = self.build()
        alpha, beta = d["projects"]
        self.assertEqual((alpha["name"], beta["name"]), ("alpha", "beta"))  # status order
        self.assertEqual(alpha["last_tool"], "claude-code")
        self.assertEqual(alpha["unlogged"], 1)
        self.assertEqual(beta["unlogged"], 1)  # no log entry: every session counts
        self.assertEqual(beta["idle"], 74)
        self.assertTrue(beta["stale"])  # occasional, 74 > 60
        self.assertEqual(alpha["docs"], ["a.md", "docs/b.md"])
        self.assertEqual(d["kpi"], {"tracked": 2, "attention": 2, "velocity": 1, "stale": 1, "untracked": 1, "unlogged": 2})
        self.assertEqual([p["name"] for p in d["archived"]], ["old"])
        self.assertEqual([Path(u["path"]).name for u in d["untracked"]], ["gamma"])  # archived=tracked, ignored skipped

    def test_stale_is_strict(self):
        self.write_project("beta", project(self.root / "beta", status="occasional",
                                           log="### 2026-08-15 — codex\n- x\n"))
        d = self.build(now=datetime(2026, 10, 14, 0, 0).timestamp())  # exactly 60 days after the log date
        beta = next(p for p in d["projects"] if p["name"] == "beta")
        self.assertEqual(beta["idle"], 60)
        self.assertFalse(beta["stale"])

    def test_last_tool_tie_prefers_log(self):
        self.write_project("beta", project(self.root / "beta", log="### 2026-10-05 — chatgpt\n- planned\n"))
        self.claude_session("c6", self.root / "beta", datetime(2026, 10, 5).timestamp())
        beta = next(p for p in self.build()["projects"] if p["name"] == "beta")
        self.assertEqual(beta["last_tool"], "chatgpt")

    def test_pulse_window_and_feed(self):
        d = self.build()
        self.assertEqual(len(d["pulse"]), 35)
        self.assertEqual(min(d["pulse"]), "2026-09-10")
        self.assertEqual(max(d["pulse"]), "2026-10-14")
        self.assertEqual(d["pulse"]["2026-10-12"], {"claude-code": 1, "codex": 1, "chatgpt": 0})
        self.assertEqual(d["pulse"]["2026-10-02"]["claude-code"], 0)  # ignored + archived folders excluded
        texts = [e["text"] for e in d["feed"]]
        self.assertEqual(texts[0], "after the log day")
        kinds = {e["text"]: e["kind"] for e in d["feed"]}
        self.assertEqual((kinds["after the log day"], kinds["logged work"]), ("session", "log"))
        self.assertNotIn("beta thread", texts)  # outside the window
        self.assertNotIn("ancient", texts)

    def test_markdown_escapes_and_atomic_write(self):
        d = self.build()
        chart.write(self.lb, d)
        md = (self.lb / "DASHBOARD.md").read_text(encoding="utf-8")
        self.assertIn(r"&lt;b&gt;raw&lt;/b&gt; \| title", md)
        self.assertIn("fix CI", md)
        self.assertEqual(json.loads((self.lb / "dashboard.json").read_text(encoding="utf-8"))["schema"], 1)
        self.assertEqual(embedded((self.lb / "chart.html").read_text(encoding="utf-8")), d)  # written with the pair
        self.assertFalse(list(self.lb.glob(".*.tmp")))

    def test_html_embeds_the_dashboard_inertly(self):
        d = self.build()
        hostile = "</script><script>alert(1)</script><img src=x onerror=alert(1)><!-- & \u2028\u2029 </SCRIPT >"
        d["projects"][0]["summary"] = d["projects"][0]["name"] = hostile
        d["feed"].append({"ts": 0, "tool": "codex", "kind": "session", "project": hostile, "text": hostile})
        page = chart.render_html(d)
        template = chart.resources.files("waypoint").joinpath("templates", "chart.html").read_text(encoding="utf-8")
        self.assertEqual(page.lower().count("</script"), template.lower().count("</script"))  # nothing closes early
        data = page.split('id="waypoint-data">', 1)[1].split("</script>", 1)[0]
        for c in "<>&\u2028\u2029":
            self.assertNotIn(c, data)
        self.assertEqual(json.loads(data), d)  # JSON.parse gives back the same document
        self.assertEqual(embedded(page), d)

    def test_spec_shows_the_html_escape_sequences(self):
        spec = (Path(__file__).resolve().parents[1] / "docs" / "LOGBOOK-SPEC.md").read_text(encoding="utf-8")
        self.assertIn(r"written as `\u003c`, `\u003e`, `\u0026`, `\u2028` and `\u2029`", spec)
        self.assertFalse({"\u2028", "\u2029"} & set(spec))

    def test_html_template_is_package_data_and_offline(self):
        from importlib import resources
        t = resources.files("waypoint").joinpath("templates", "chart.html").read_text(encoding="utf-8")
        self.assertIn(chart._SLOT, t)
        self.assertIn('"templates/*.html"', (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(set(re.findall(r"https?://[^'\"\s)]+", t)), {"http://www.w3.org/2000/svg"})  # a namespace, not a request
        self.assertIn("default-src 'none'", t)
        self.assertNotRegex(t, r"(?i)innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|fetch\(|XMLHttpRequest|sample")

    def test_bad_sources_never_stop_the_chart(self):
        (self.home / ".claude" / "history.jsonl").write_text("garbage\n[1,2]\n", encoding="utf-8")
        self.codex_db(7, [("x",)], cols=("id",))
        d = self.build()
        self.assertEqual(d["sources"]["claude-code"]["state"], "degraded")
        self.assertEqual(d["sources"]["codex"]["state"], "ok")  # fell back to state_5


class Rules(Env):
    def test_render_check_install(self):
        canonical = rules.render(self.lb)
        self.assertNotIn("{logbook}", canonical)
        self.assertNotRegex(canonical, r"(?i)navigator|voyage|hazard")
        (self.lb / "GLOBAL-RULES.md").write_text(canonical, encoding="utf-8")
        self.assertEqual(rules.check(self.home, self.lb), {"logbook": "in-sync", "claude-code": "missing", "codex": "missing"})

        (self.home / ".codex" / "AGENTS.md").write_text("# Progress tracking\nold rule\n", encoding="utf-8")
        self.assertEqual(rules.check(self.home, self.lb)["codex"], "drifted")

        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.write_text("# my notes\nkeep me\n", encoding="utf-8")
        rules.install(self.home, self.lb)
        rules.install(self.home, self.lb)  # idempotent
        self.assertEqual(claude_md.read_text(encoding="utf-8"), "# my notes\nkeep me\n")  # never edited
        self.assertEqual(rules.claude_rule_file(self.home).read_text(encoding="utf-8"), rules.claude_file_text(self.lb))
        self.assertEqual((self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8").count(rules.BEGIN), 1)
        self.assertEqual(rules.check(self.home, self.lb), {"logbook": "in-sync", "claude-code": "in-sync", "codex": "in-sync"})
        self.assertTrue(list((self.home / ".codex").glob("AGENTS.md.bak-*")))

        (self.home / ".codex" / "AGENTS.md").write_text(
            (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8").replace("last step", "first step"),
            encoding="utf-8")
        self.assertEqual(rules.check(self.home, self.lb)["codex"], "drifted")


class Cli(unittest.TestCase):
    def test_fix_summary_pluralization(self):
        expected = {
            0: "No voyages (projects) charted yet; 0 folders with recent AI activity are uncharted (untracked). "
               "Chart one: waypoint add <folder>\n",
            1: "Fix taken (sync): 1 voyage (project), 0 underway (active) this week, 1 hazard (attention item), "
               "0 with unlogged watches (sessions), 0 adrift (stale), 0 uncharted (untracked folders).\n",
            2: "Fix taken (sync): 2 voyages (projects), 0 underway (active) this week, 2 hazards (attention items), "
               "0 with unlogged watches (sessions), 0 adrift (stale), 0 uncharted (untracked folders).\n",
        }
        for count in (0, 1, 2):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as t:
                page = f"Chart (dashboard) for your browser: {(Path(t) / 'lb' / 'chart.html').resolve()}\n"
                lb = Path(t) / "lb"
                with redirect_stdout(io.StringIO()):
                    cli.main(["init", str(lb)])
                for i in range(count):
                    (lb / "projects" / f"p{i}.md").write_text(
                        project(Path(t) / f"p{i}", extra="- Attention: check"), encoding="utf-8")
                out = io.StringIO()
                with redirect_stdout(out):
                    cli.main(["fix", "--logbook", str(lb), "--home", str(Path(t) / "home")])
                self.assertEqual(out.getvalue(), expected[count] + page)  # story term paired with its plain term (STORY.md)

    def test_init_then_fix(self):
        with tempfile.TemporaryDirectory() as t:
            lb = Path(t) / "lb"
            with redirect_stdout(io.StringIO()):
                cli.main(["init", str(lb)])
                cli.main(["init", str(lb)])  # second run keeps files
                cli.main(["fix", "--logbook", str(lb), "--home", str(Path(t) / "home")])
            self.assertEqual(load(lb).roots, [str(lb.resolve().parent)])  # resolved: Windows runners use 8.3 temp names
            for f in ("AGENTS.md", "CLAUDE.md", "GLOBAL-RULES.md", "projects/_template.md", "projects/_ignore.txt",
                      "DASHBOARD.md", "dashboard.json", "chart.html"):
                self.assertTrue((lb / f).exists(), f)
            d = embedded((lb / "chart.html").read_text(encoding="utf-8"))  # an empty logbook embeds an empty chart
            self.assertEqual((d["projects"], d["archived"], d["untracked"]), ([], [], []))
            with patch("webbrowser.open", return_value=True) as opened, redirect_stdout(io.StringIO()):
                cli.main(["fix", "--logbook", str(lb), "--home", str(Path(t) / "home"), "--open"])
            opened.assert_called_once_with((lb / "chart.html").resolve().as_uri())
            self.assertEqual(logbook.load_projects(lb), [])  # _template.md is not a project and has no log entry


class ReviewFixes(ChartEnv):
    """Regression tests for the Phase 2 Codex review."""

    def test_log_wins_tie_by_source_not_tool(self):
        self.write_project("beta", project(self.root / "beta", log="### 2026-10-05 — codex\n- logged by codex\n"))
        self.claude_session("c7", self.root / "beta", datetime(2026, 10, 5).timestamp())
        beta = next(p for p in self.build()["projects"] if p["name"] == "beta")
        self.assertEqual(beta["last_tool"], "codex")  # the log entry (tool codex) beats the Claude session

    def test_old_untracked_folder_not_in_pulse(self):
        self.claude_session("c8", self.root / "delta", NOW - 32 * DAY, "too old to be untracked")
        d = self.build()
        self.assertNotIn("delta", [Path(u["path"]).name for u in d["untracked"]])
        self.assertNotIn("too old to be untracked", [e["text"] for e in d["feed"]])
        self.assertEqual(sum(v["claude-code"] for v in d["pulse"].values()), 3)  # c1, c2 (alpha), c3 (gamma); not c4/c5/c8

    def test_future_session_not_untracked(self):
        self.claude_session("c9", self.root / "future", NOW + DAY, "from the future")
        self.assertNotIn("future", [Path(u["path"]).name for u in self.build()["untracked"]])

    def test_markdown_escapes_names_and_links(self):
        self.write_project("we[ird]", project(self.root / "weird", now="- Next: see [x](http://evil) `code`"))
        md = chart.render_md(self.build())
        self.assertIn("[we\\[ird\\]](projects/we%5Bird%5D.md)", md)
        self.assertIn("see \\[x\\](http://evil) \\`code\\`", md)


class Robustness(Env):
    def test_malformed_field_types(self):
        d = self.home / ".claude" / "projects" / "slug"
        d.mkdir(parents=True)
        (d / "s1.jsonl").write_text('{"cwd": 5}\n{"type":"ai-title","aiTitle":7}\n[1]\n', encoding="utf-8")
        (self.home / ".claude" / "history.jsonl").write_text("\n".join([
            json.dumps({"sessionId": ["x"], "timestamp": 1, "project": "p"}),
            json.dumps({"sessionId": "a", "timestamp": float("inf") if False else "NaN", "project": "p"}),
            json.dumps({"sessionId": "b", "timestamp": True, "project": "p"}),
            '{"sessionId": "c", "timestamp": 1' + "0" * 400 + ', "project": "p"}',  # huge int: no OverflowError
            json.dumps({"sessionId": "d", "timestamp": 10 ** 17, "project": "p"}),  # beyond year 9999
        ]), encoding="utf-8")
        sessions, h = claude.read(self.home, placer([], [str(self.root)]))
        self.assertEqual(sessions, [])
        self.assertIn(h["state"], ("degraded", "unreadable"))
        self.codex_db(5, [("t", 5, "x", 1.0, 0), ("u", str(self.root / "a"), None, float("nan"), 0)])
        sessions, h = codex.read(self.home, placer([], [str(self.root)]))
        self.assertEqual((sessions, h["state"]), ([], "degraded"))

    def test_repeated_installs_are_no_ops_and_keep_the_first_backup(self):
        (self.lb / "GLOBAL-RULES.md").write_text(rules.render(self.lb), encoding="utf-8")
        ag = self.home / ".codex" / "AGENTS.md"
        ag.write_text("ORIGINAL\n", encoding="utf-8")
        rules.install(self.home, self.lb)
        mtime = ag.stat().st_mtime_ns
        self.assertEqual({c.action for c in rules.plan_install(self.home, self.lb)}, {"unchanged"})
        rules.install(self.home, self.lb)
        self.assertEqual(ag.stat().st_mtime_ns, mtime)  # unchanged target: no write
        backups = list((self.home / ".codex").glob("AGENTS.md.bak-*"))
        self.assertEqual([b.read_text(encoding="utf-8") for b in backups], ["ORIGINAL\n"])

    def test_edited_canonical_is_drift(self):
        (self.lb / "GLOBAL-RULES.md").write_text(rules.render(self.lb), encoding="utf-8")
        rules.install(self.home, self.lb)
        (self.lb / "GLOBAL-RULES.md").write_text("tampered\n", encoding="utf-8")
        self.assertEqual(rules.check(self.home, self.lb), {"logbook": "drifted", "claude-code": "drifted", "codex": "in-sync"})

    def test_unc_and_relative_home(self):
        self.assertEqual(norm("\\\\?\\UNC\\srv\\share\\x"), "\\\\srv\\share\\x")
        self.assertEqual(map_folder("\\\\?\\UNC\\srv\\share\\proj\\a", ["\\\\srv\\share"]), "\\\\srv\\share\\proj")
        self.codex_db(5, [("t", str(self.root / "alpha"), "x", NOW * 1000, 0)])
        cwd = os.getcwd()
        try:
            os.chdir(self.home.parent)
            sessions, h = codex.read(Path("home"), placer([], [str(self.root)]))
        finally:
            os.chdir(cwd)
        self.assertEqual((len(sessions), h["state"]), (1, "ok"))

    def test_unknown_status_kept_and_blank_log_lines(self):
        self.write_project("p", project(self.root / "p", status="Exploring",
                                        log="### 2026-10-01 — codex\n-   \n- one\n   \n- two\n"))
        p = logbook.load_projects(self.lb)[0]
        self.assertEqual(p["status"], "Exploring")
        self.assertEqual(p["logs"][0]["text"], "one two")


class InitAndPush(unittest.TestCase):
    def test_init_keeps_edited_rules(self):
        with tempfile.TemporaryDirectory() as t:
            lb = Path(t) / "lb"
            with redirect_stdout(io.StringIO()):
                cli.main(["init", str(lb)])
                (lb / "GLOBAL-RULES.md").write_text("mine\n", encoding="utf-8")
                cli.main(["init", str(lb)])
                self.assertEqual((lb / "GLOBAL-RULES.md").read_text(encoding="utf-8"), "mine\n")
                cli.main(["init", str(lb), "--refresh-rules"])
            self.assertEqual((lb / "GLOBAL-RULES.md").read_text(encoding="utf-8"), rules.render(lb))

    def test_push_commits_only_logbook_data(self):
        import shutil
        import subprocess
        if not shutil.which("git"):
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as t:
            remote, lb = Path(t) / "remote.git", Path(t) / "lb"
            g = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)
            g("init", "--bare", "-q", str(remote))
            with redirect_stdout(io.StringIO()):
                cli.main(["init", str(lb)])
            g("init", "-q", "-b", "main", cwd=lb)
            g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "root", cwd=lb)
            g("remote", "add", "origin", str(remote), cwd=lb)
            g("push", "-q", "-u", "origin", "main", cwd=lb)
            (lb / "secret-notes.txt").write_text("not logbook data", encoding="utf-8")
            (lb / "staged-by-user.txt").write_text("user's own staged work", encoding="utf-8")
            g("add", "staged-by-user.txt", cwd=lb)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            old = os.environ.copy()
            os.environ.update(env)
            try:
                with redirect_stdout(io.StringIO()):
                    cli.main(["fix", "--logbook", str(lb), "--push", "--home", str(Path(t) / "home")])
            finally:
                os.environ.clear()
                os.environ.update(old)
            committed = g("show", "--name-only", "--format=", "HEAD", cwd=lb).stdout.split()
            self.assertIn("dashboard.json", committed)
            self.assertIn("chart.html", committed)
            self.assertNotIn("secret-notes.txt", committed)
            self.assertNotIn("staged-by-user.txt", committed)
            self.assertIn("staged-by-user.txt", g("diff", "--cached", "--name-only", cwd=lb).stdout)  # still staged
            # a user who gitignores chart.html can still push the rest
            g("rm", "-q", "--cached", "chart.html", cwd=lb)
            (lb / ".gitignore").write_text("chart.html\n", encoding="utf-8")
            (lb / "projects" / "new.md").write_text("# new\n", encoding="utf-8")
            os.environ.update(env)
            try:
                with redirect_stdout(io.StringIO()):
                    cli.main(["fix", "--logbook", str(lb), "--push", "--home", str(Path(t) / "home")])
            finally:
                os.environ.clear()
                os.environ.update(old)
            committed = g("show", "--name-only", "--format=", "HEAD", cwd=lb).stdout.split()
            self.assertIn("projects/new.md", committed)
            self.assertNotIn("chart.html", committed)


if __name__ == "__main__":
    unittest.main()


class SafeInstall(Env):
    """R0.1 installer guarantees (R0-SCOPE R0.1; Codex review H6, M1)."""

    def setUp(self):
        super().setUp()
        (self.lb / "GLOBAL-RULES.md").write_text(rules.render(self.lb), encoding="utf-8")
        self.agents = self.home / ".codex" / "AGENTS.md"

    def test_crlf_and_outside_bytes_kept(self):
        self.agents.write_bytes(b"line one\r\nno newline at end")
        rules.install(self.home, self.lb)
        data = self.agents.read_bytes()
        self.assertTrue(data.startswith(b"line one\r\nno newline at end\r\n\r\n" + rules.BEGIN.encode()))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))  # every line ending is still CRLF
        rules.apply(rules.plan_uninstall(self.home))
        self.assertEqual(self.agents.read_bytes(), b"line one\r\nno newline at end\r\n")

    def test_uninstall_restores_text_and_removes_owned_file_only(self):
        self.agents.write_text("mine\n", encoding="utf-8")
        rules.install(self.home, self.lb)
        rules.apply(rules.plan_uninstall(self.home))
        self.assertEqual(self.agents.read_text(encoding="utf-8"), "mine\n")
        self.assertFalse(rules.claude_rule_file(self.home).exists())
        rules.claude_rule_file(self.home).write_text("someone else's\n", encoding="utf-8")
        self.assertEqual([c.action for c in rules.plan_uninstall(self.home)], ["refuse"])
        self.assertEqual(rules.plan_install(self.home, self.lb)[0].action, "refuse")

    def test_malformed_markers_refused_and_untouched(self):
        bad = f"x\n{rules.BEGIN}\n{rules.BEGIN}\n{rules.END}\n"
        self.agents.write_text(bad, encoding="utf-8")
        c = next(c for c in rules.plan_install(self.home, self.lb) if c.target == self.agents)
        self.assertEqual(c.action, "refuse")
        rules.install(self.home, self.lb)
        self.assertEqual(self.agents.read_text(encoding="utf-8"), bad)

    def test_legacy_claude_block_is_migrated(self):
        cm = self.home / ".claude" / "CLAUDE.md"
        cm.write_text(f"notes\n\n{rules.BEGIN}\n{rules.claude_import(self.lb)}\n{rules.END}\n", encoding="utf-8")
        self.assertEqual(rules.check(self.home, self.lb)["claude-code"], "in-sync")  # the owner's current setup
        rules.install(self.home, self.lb)
        self.assertEqual(cm.read_text(encoding="utf-8"), "notes\n")
        self.assertEqual(rules.check(self.home, self.lb)["claude-code"], "in-sync")

    def test_codex_override_is_shadowed_until_installed_there(self):
        rules.install(self.home, self.lb)
        ov = self.home / ".codex" / "AGENTS.override.md"
        ov.write_text("override rules\n", encoding="utf-8")
        self.assertEqual(rules.check(self.home, self.lb)["codex"], "shadowed")
        self.assertIn("--codex-override", rules.plan_install(self.home, self.lb)[-1].note)
        rules.install(self.home, self.lb, codex_override=True)
        self.assertTrue(ov.read_text(encoding="utf-8").startswith("override rules\n"))
        self.assertEqual(rules.check(self.home, self.lb)["codex"], "in-sync")

    def test_import_path_with_spaces_is_escaped_and_checked(self):
        lb = Path(self.tmp.name) / "my logbook"
        (lb / "projects").mkdir(parents=True)
        (lb / "GLOBAL-RULES.md").write_text(rules.render(lb), encoding="utf-8")
        self.assertIn("my\\ logbook", rules.claude_import(lb))
        rules.install(self.home, lb)
        self.assertEqual(rules.check(self.home, lb)["claude-code"], "in-sync")


class Matching(Env):
    """Spec v1.1 §4.2: sessions map to the tracked project whose Path is the cwd or a separator-bounded ancestor."""

    def test_nested_sibling_outside_root_duplicate_archived(self):
        nested = self.root / "org" / "api"
        outside = Path(self.tmp.name) / "elsewhere" / "tool"
        for d in (nested, self.root / "app", self.root / "app-old", outside):
            d.mkdir(parents=True)
        self.write_project("api", project(nested))
        self.write_project("app", project(self.root / "app"))
        self.write_project("tool", project(outside))
        self.write_project("zz-dup", project(self.root / "app"))  # same Path as app: app (first by name) wins
        self.write_project("gone", project(self.root / "gone"), archived=True)
        self.claude_session("n1", nested / "src", NOW - DAY, "nested work")
        self.claude_session("s1", self.root / "app-old", NOW - DAY, "sibling, not app")
        self.claude_session("o1", outside, NOW - DAY, "outside every root")
        self.claude_session("a1", self.root / "app" / "lib", NOW - DAY, "app work")
        self.claude_session("g1", self.root / "gone", NOW - DAY, "archived folder")
        d = self.build()
        un = {p["name"]: p["unlogged"] for p in d["projects"]}
        self.assertEqual((un["api"], un["app"], un["tool"], un["zz-dup"]), (1, 1, 1, 0))
        self.assertEqual([Path(u["path"]).name for u in d["untracked"]], ["app-old"])  # archived "gone" never untracked
        self.assertEqual(d["sources"]["logbook"]["state"], "degraded")
        self.assertIn("projects/zz-dup.md has the same Path as projects/app.md", d["sources"]["logbook"]["detail"])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_cwd_maps_to_target(self):
        real_dir = self.root / "alpha"
        link = Path(self.tmp.name) / "link-to-alpha"
        link.symlink_to(real_dir, target_is_directory=True)
        self.write_project("alpha", project(real_dir))
        self.claude_session("l1", link / "src", NOW - DAY, "via symlink")
        self.assertEqual(self.build()["projects"][0]["unlogged"], 1)

    def test_idle_never_negative(self):
        self.write_project("alpha", project(self.root / "alpha"))
        self.claude_session("f1", self.root / "alpha", NOW + 5, "a moment after now")
        self.assertEqual(self.build()["projects"][0]["idle"], 0)

    def test_config_dir_env_vars_only_for_the_real_home(self):
        from waypoint.sources import tool_dir
        os.environ["CODEX_HOME"] = str(self.home / "elsewhere")
        try:
            self.assertEqual(tool_dir(self.home, "CODEX_HOME", ".codex"), self.home / ".codex")  # explicit --home wins
            self.assertEqual(tool_dir(Path.home(), "CODEX_HOME", ".codex"), self.home / "elsewhere")
        finally:
            del os.environ["CODEX_HOME"]


class AddAndSetup(Env):
    def setUp(self):
        super().setUp()
        self.cfg = Path(self.tmp.name) / "cfg"
        self._env = {k: os.environ.get(k) for k in ("APPDATA", "XDG_CONFIG_HOME", "WAYPOINT_LOGBOOK")}
        os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"] = str(self.cfg)
        os.environ.pop("WAYPOINT_LOGBOOK", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(list(args))
        return out.getvalue()

    def test_add_creates_a_filled_file_without_a_log_entry(self):
        self.run_cli("init", str(self.lb))
        out = self.run_cli("add", str(self.root / "alpha"), "--logbook", str(self.lb))
        self.assertIn("Charted a new voyage (project)", out)
        text = (self.lb / "projects" / "alpha.md").read_text(encoding="utf-8")
        self.assertIn(f"- Path: {(self.root / 'alpha').resolve()}", text)
        self.assertNotIn("<", text)  # placeholders blanked
        p = logbook.load_projects(self.lb)[0]
        self.assertEqual((p["name"], p["status"], p["logs"]), ("alpha", "active", []))

    def test_add_refusals(self):
        self.run_cli("init", str(self.lb))
        self.run_cli("add", str(self.root / "alpha"), "--logbook", str(self.lb))
        (self.lb / "projects" / "_ignore.txt").write_text(str(self.root / "beta") + "\n", encoding="utf-8")
        (self.root / "other" / "alpha").mkdir(parents=True)
        cases = [
            ((str(self.root / "alpha"),), "already tracked"),
            ((str(self.root / "beta"),), "_ignore.txt"),
            ((str(self.root / "other" / "alpha"),), "already exists"),
            ((str(self.root / "other" / "alpha"), "--name", "_hidden"), "can't start with _"),
            ((str(self.root / "other" / "alpha"), "--name", "con"), "reserved device name"),
            ((str(self.root / "other" / "alpha"), "--name", "a/b"), "can't contain"),
            ((str(self.root / "missing"),), "is not a folder"),
        ]
        for args, why in cases:
            with self.subTest(args=args), self.assertRaises(SystemExit) as e:
                self.run_cli("add", *args, "--logbook", str(self.lb))
            self.assertIn(why, str(e.exception))
        self.assertEqual(len(logbook.load_projects(self.lb)), 1)

    def test_add_outside_roots_warns(self):
        self.run_cli("init", str(self.lb))
        far = Path(self.tmp.name) / "far" / "proj"
        far.mkdir(parents=True)
        out = self.run_cli("add", str(far), "--logbook", str(self.lb))
        self.assertIn("outside the logbook's roots", out)

    def test_setup_yes_needs_logbook_and_root(self):
        with self.assertRaises(SystemExit) as e:
            self.run_cli("setup", "--yes", "--home", str(self.home))
        self.assertIn("--logbook", str(e.exception))
        with self.assertRaises(SystemExit) as e:
            self.run_cli("setup", "--yes", "--logbook", str(Path(self.tmp.name) / "new"), "--home", str(self.home))
        self.assertIn("--root", str(e.exception))

    @patch("waypoint.rules._real_home", lambda home: True)  # remembering is only for the real home; --no-mcp so no CLI runs
    def test_setup_yes_then_logbook_is_remembered(self):
        lb = Path(self.tmp.name) / "fresh"
        out = self.run_cli("setup", "--yes", "--logbook", str(lb), "--root", str(self.root), "--tool", "codex",
                           "--no-mcp", "--home", str(self.home))
        self.assertIn("remembered", out)
        self.assertEqual(load(lb).roots, [str(self.root.resolve())])
        self.assertTrue((self.home / ".codex" / "AGENTS.md").exists())
        self.assertFalse(rules.claude_rule_file(self.home).exists())  # only the tool asked for
        self.assertIn("No voyages", self.run_cli("fix", "--home", str(self.home)))  # no --logbook
        again = self.run_cli("setup", "--yes", "--logbook", str(lb), "--root", str(self.root), "--tool", "codex",
                             "--no-mcp", "--home", str(self.home))
        self.assertIn("Nothing to change.", again)  # idempotent

    def test_logbook_discovery_order(self):
        from waypoint.config import find_logbook, remember_logbook
        self.assertEqual(find_logbook(None)[1], "the current folder")
        remember_logbook(self.lb)
        self.assertEqual(find_logbook(None)[0], self.lb.resolve())
        os.environ["WAYPOINT_LOGBOOK"] = str(self.root)
        self.assertEqual(find_logbook(None)[0], self.root)
        self.assertEqual(find_logbook("x")[0], Path("x"))
