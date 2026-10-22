"""End-of-day email (spec §6 v1.4). Hermetic: APPDATA and XDG_CONFIG_HOME point at a temp dir."""
import base64
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from waypoint import cli, clis, config, eod


class Address(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"APPDATA": tmp.name, "XDG_CONFIG_HOME": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.file = config.user_dir() / config.EOD_FILE

    def put(self, data: bytes):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_bytes(data)

    def test_valid_addresses(self):
        for a in ("you@gmail.com", "Me+eod@Gmail.COM", "a.b_c%d-e@mail.b.co.uk"):
            self.assertTrue(config.valid_email(a), a)

    def test_invalid_addresses(self):
        for a in ("", "you", "a@b", "a@@b.com", "a@b@c.com", "a b@c.com", "a@b.com\nBcc: x@y.com", "ü@b.com",
                  "a@b.c", "a" * 250 + "@b.com"):
            self.assertFalse(config.valid_email(a), repr(a))

    def test_remember_then_read_then_forget(self):
        self.assertEqual(config.eod_email_state(), "none")
        self.assertEqual(config.remember_eod_email("you@gmail.com"), self.file)
        self.assertEqual(config.eod_email(), "you@gmail.com")
        self.assertEqual(config.eod_email_state(), "remembered")
        self.assertIsNone(config.eod_email_plan("you@gmail.com"))
        self.assertEqual(config.eod_email_plan("me@gmail.com"), "me@gmail.com\n")
        self.assertTrue(config.forget_eod_email())
        self.assertFalse(config.forget_eod_email())
        self.assertIsNone(config.eod_email())
        self.assertEqual(config.eod_email_state(), "none")

    def test_plan_refuses_a_bad_address(self):
        with self.assertRaises(ValueError) as cm:
            config.eod_email_plan("x\n@y.com")
        self.assertIn("not an email address", str(cm.exception))
        self.assertNotIn("\n", str(cm.exception))
        self.assertFalse(self.file.exists())

    def test_read_strips_one_line_only(self):
        self.put(b" you@x.com \n")
        self.assertEqual(config.eod_email(), "you@x.com")
        self.put(b"a@x.com\nb@x.com\n")
        self.assertIsNone(config.eod_email())
        self.assertEqual(config.eod_email_state(), "invalid")

    def test_unreadable_file_is_no_address(self):
        self.file.mkdir(parents=True)
        self.assertIsNone(config.eod_email())
        self.file.rmdir()
        self.put(b"\xff\xfe")
        self.assertIsNone(config.eod_email())
        self.assertEqual(config.eod_email_state(), "invalid")

    def test_forget_raises_when_the_file_cannot_be_deleted(self):
        self.file.mkdir(parents=True)
        with self.assertRaises(OSError):
            config.forget_eod_email()


class Steps(unittest.TestCase):
    def test_steps_for_two_tools(self):
        text = clis.email_steps(["claude-code", "codex", "codex"])
        for line in ("claude mcp add --transport http --scope user agentmail https://mcp.agentmail.to/mcp",
                     "codex mcp add agentmail --url https://mcp.agentmail.to/mcp", "codex mcp login agentmail",
                     "console.agentmail.to", "Sent via AgentMail", "/mcp__waypoint__send_eod",
                     "/mcp__waypoint-local__send_eod", "/send_eod (Gemini CLI)"):
            self.assertIn(line, text)
        self.assertEqual(text.count("ChatGPT (if you use the beacon)"), 1)
        self.assertEqual(text.count("Codex:"), 1)
        self.assertNotIn("gemini mcp add", text)

    def test_snippet_tool_gets_the_generic_line(self):
        text = clis.email_steps(["cursor"])
        self.assertIn("Cursor:", text)
        self.assertIn("remote (HTTP)", text)

    def test_claude_and_gemini_add_agentmail_for_every_project(self):
        self.assertIn("--scope user", clis.AGENTMAIL_STEPS["claude-code"][0])
        self.assertIn("--scope user", clis.AGENTMAIL_STEPS["gemini-cli"][0])

    def test_no_chatgpt_block_when_asked(self):
        self.assertNotIn("ChatGPT (if", clis.email_steps(["codex"], chatgpt=False))

    def test_chatgpt_once(self):
        self.assertEqual(clis.email_steps(["chatgpt"]).count("ChatGPT (if"), 1)

    def test_unknown_tool(self):
        with self.assertRaises(KeyError):
            clis.email_steps(["nope"])


class EmailCommand(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (patch.dict(os.environ, {"APPDATA": tmp.name, "XDG_CONFIG_HOME": tmp.name}),
                  patch("waypoint.clis.detected", return_value=["codex"])):
            p.start()
            self.addCleanup(p.stop)
        self.file = config.user_dir() / config.EOD_FILE

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["email", *args])
        return out.getvalue()

    def test_plain_prints_steps_and_state(self):
        out = self.run_cli()
        self.assertIn("codex mcp add", out)
        self.assertIn("End-of-day email address: none remembered (waypoint email --to <address>)", out)

    def test_to_forget_cycle(self):
        self.assertIn("Remembered you@gmail.com", self.run_cli("--to", "you@gmail.com"))
        self.assertIn("Already remembered.", self.run_cli("--to", "you@gmail.com"))
        self.assertIn("address: you@gmail.com", self.run_cli())
        self.assertIn("Forgot the end-of-day email address.", self.run_cli("--forget"))
        self.assertIn("No end-of-day email address was remembered.", self.run_cli("--forget"))

    def test_to_refuses_bad(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("--to", "a b@c.com")
        self.assertIn("not an email address", str(cm.exception))
        self.assertFalse(self.file.exists())

    def test_to_refuses_empty(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("--to", "")
        self.assertIn("not an email address", str(cm.exception))
        self.assertFalse(self.file.exists())

    def test_tool_shows_only_that_tool(self):
        out = self.run_cli("--tool", "claude-code")
        self.assertIn("claude mcp add", out)
        self.assertNotIn("ChatGPT (if", out)

    def test_tool_chatgpt(self):
        out = self.run_cli("--tool", "chatgpt")
        self.assertEqual(out.count("ChatGPT (if"), 1)
        self.assertNotIn("claude mcp add", out)


GEN = datetime(2026, 10, 22, 18, 0).timestamp()


def log(text, hours=0.0, date="2026-10-22", tool="claude-code"):
    return {"ts": GEN + hours * 3600, "date": date, "tool": tool, "text": text}


def proj(name, logs=(), idle=5, unlogged=0, nxt="", done="", summary="", attention=(), status="active"):
    return {"name": name, "status": status, "summary": summary, "done": done, "next": nxt, "idle": idle,
            "unlogged": unlogged, "attention": list(attention), "logs": list(logs)}


BOUND = 19250  # 55% of the 35012 chars this day took before the <style> classes; 19184 after


class Report(unittest.TestCase):
    def chart(self, *projects):
        return {"generated": GEN, "projects": list(projects)}

    def test_today_only_and_future_excluded(self):
        r = eod.report(self.chart(proj("a", [log("FUTURE", 1), log("TODAY", -1), log("OLD", -30, "2026-10-21")])))
        for f in ("text", "html"):
            self.assertIn("TODAY", r[f])
            self.assertNotIn("FUTURE", r[f])
            self.assertNotIn("OLD", r[f])
        self.assertEqual(r["counts"]["entries"], 1)

    def test_all_entries_no_cap(self):
        r = eod.report(self.chart(proj("a", [log(f"entry-{i:02d}", -i / 10) for i in range(25)])))
        for i in range(25):
            self.assertIn(f"entry-{i:02d}", r["text"])
            self.assertIn(f"entry-{i:02d}", r["html"])
        self.assertEqual(r["counts"]["entries"], 25)

    def test_html_escapes_and_text_verbatim(self):
        bad = '<b>"x"</b> & \'y\''
        r = eod.report(self.chart(proj(bad, [log(bad, tool=bad)], nxt=bad, done=bad, summary=bad),
                                  proj("other" + bad, nxt=bad)))
        self.assertNotIn("<b>", r["html"])
        self.assertIn("&lt;b&gt;&quot;x&quot;&lt;/b&gt; &amp; &#x27;y&#x27;", r["html"])
        self.assertIn(f"- [{bad}] {bad}", r["text"])

    def test_attention_never_included(self):
        r = eod.report(self.chart(proj("a", [log("x")], attention=["ATTN-SECRET"]),
                                  proj("b", idle=0, attention=["ATTN-SECRET"])))
        self.assertNotIn("ATTN-SECRET", repr(r))

    def test_empty_day(self):
        r = eod.report(self.chart(proj("idle-one", nxt="later")))
        self.assertIn("No project moved today", r["html"])
        self.assertIn("idle-one", r["text"])  # the others line
        self.assertEqual(r["counts"], {"projects": 0, "entries": 0, "unlogged": 0})
        r = eod.report(self.chart())
        self.assertIn("PROGRESS", r["text"])

    def test_html_is_email_safe(self):
        r = eod.report(self.chart(proj("a", [log("x")]), proj("b", idle=0, unlogged=2, nxt="n"), proj("c", nxt="n")))
        h = r["html"].lower()
        self.assertNotIn("<script", h)
        self.assertNotIn("<svg", h)
        srcs = h.split("src=")[1:]
        self.assertTrue(srcs)
        for s in srcs:
            self.assertTrue(s.startswith('"cid:waypoint-mark"'), s[:40])
        self.assertNotIn("<a ", h)
        for attr in ("src=", "href="):
            for s in h.split(attr)[1:]:
                self.assertFalse(s.lstrip("\"'").startswith(("http://", "https://")), s[:40])

    def test_realistic_day_is_compact(self):
        # 30 entries of ~300 chars over 6 projects, a quiet project and an other.
        text = ("Moved the chart builder onto the new parser, fixed the idle count for archived projects, and wrote "
                "the tests that pin the behaviour down; the review found two small issues in the escaping of links, "
                "now resolved and covered, see docs/reviews/phase-4.md for the details and the follow-up list here.")
        ps = [proj(f"project-{k}", [log(f"[{k}.{i}] {text}", -(k * 5 + i) / 6) for i in range(5)], idle=0,
                   unlogged=k % 2, nxt="Ship the next thing after review", done="Shipped the thing",
                   summary="A tool that does a useful thing for its owner, in one sentence.") for k in range(6)]
        r = eod.report(self.chart(*ps, proj("quiet", idle=0, unlogged=2, nxt="Wait"), proj("other", nxt="Later")))
        h = r["html"]
        self.assertLess(len(h), BOUND)
        self.assertIn("cid:waypoint-mark", h)
        self.assertIn("7 projects moved today", h)
        for k in range(6):
            for i in range(5):
                self.assertIn(f"[{k}.{i}] {text}", h)

    def test_preheader_plural(self):
        one = eod.report(self.chart(proj("a", [log("x")])))["html"]
        self.assertIn("1 project moved today, 1 log entry<", one)
        two = eod.report(self.chart(proj("a", [log("x"), log("y", -1)])))["html"]
        self.assertIn("1 project moved today, 2 log entries<", two)

    def test_others_are_active_or_occasional(self):
        r = eod.report(self.chart(proj("a", [log("x")]), proj("live", nxt="n"), proj("sometimes", nxt="n", status="occasional"),
                                  proj("finished", nxt="n", status="done"), proj("resting", nxt="n", status="paused")))
        for f in ("text", "html"):
            self.assertIn("live, sometimes.", r[f])
            self.assertNotIn("finished", r[f])
            self.assertNotIn("resting", r[f])

    def test_subject_and_counts(self):
        r = eod.report(self.chart(proj("a", [log("x"), log("y", -1)], unlogged=1), proj("b", idle=0, unlogged=2),
                                  proj("c", unlogged=7)))
        self.assertEqual(r["subject"], "Waypoint EoD — 2026-10-22")
        self.assertEqual(r["date"], "2026-10-22")
        self.assertEqual(r["counts"], {"projects": 2, "entries": 2, "unlogged": 3})
        self.assertIn("2 projects moved today", r["html"])
        self.assertIn("END OF DAY · 22 OCT 2026", r["html"])
        self.assertIn("1 project moved today", eod.report(self.chart(proj("a", [log("x")])))["html"])
        t = r["text"]
        idx = [t.index(h) for h in ("PROGRESS", "TODAY'S WORK", "COMPLETED", "AHEAD")]
        self.assertEqual(idx, sorted(idx))
        self.assertTrue(t.rstrip().endswith("Sent at your request from your Waypoint logbook. "
                                            "Waypoint itself never sends mail."))

    def test_mark_attachment(self):
        a = eod.mark_attachment()
        self.assertEqual({k: v for k, v in a.items() if k != "content"},
                         {"filename": "waypoint.png", "contentType": "image/png", "contentDisposition": "inline",
                          "contentId": eod.MARK_CID})
        self.assertTrue(base64.b64decode(a["content"]).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
