"""Rich drawing (spec Part C, look and terminal handling): banner, panel, status and result lines, in Unicode and
in ASCII. Needs the tui extra (skipped without it)."""
import io
import unittest
from pathlib import Path

from waypoint import tui
from waypoint.tui import term
from waypoint.tui.actions import Step

NEED = unittest.skipUnless(tui.available(), "needs the tui extra: pip install -e .[tui]")
WIDE = term.Term(unicode=True, color=True, width=100)
NARROW_ASCII = term.Term(unicode=False, color=False, width=60)
STATUS = {"logbook": "/lb", "counts": {"tracked": 2, "archived": 1, "untracked": 3},
          "tools": {"codex": {"name": "Codex", "tier": "supported", "rule": "in-sync", "method": "copy",
                              "mcp": {"state": "registered"}, "scope": ["global"], "projects": []}},
          "sources": {"git": {"state": "ok"}},
          "problems": [{"what": "Codex can't write the logbook (its sandbox)", "fix": "add /lb to writable_roots"}]}


def printed(t, *renderables):
    from waypoint.tui import render
    buf = io.StringIO()
    render.console(t, buf).print(*renderables)
    return buf.getvalue()


@NEED
class Look(unittest.TestCase):
    def test_header_wide_has_the_banner_and_panel(self):
        from waypoint.tui import render
        out = printed(WIDE, render.header(WIDE, STATUS))
        self.assertIn(term.BANNER.splitlines()[0], out)
        self.assertIn("2 tracked · 1 archived · 3 untracked", out)
        self.assertIn("AI tools Codex", out)
        self.assertIn("One logbook for every voyage", out)  # the tagline (it may wrap)

    def test_banner_gradient_colours(self):
        from waypoint.tui import render
        styles = [str(s.style) for s in render.banner(WIDE).spans]
        self.assertEqual([s.split()[-1] for s in styles], [term.PALETTE[c] for c in term.GRADIENT])

    def test_no_color_means_no_colour_styles(self):
        from waypoint.tui import render
        t = term.Term(unicode=True, color=False, width=100)
        self.assertTrue(all("#" not in str(s.style) for s in render.banner(t).spans))

    def test_header_and_status_emit_no_colour_escapes_when_disabled(self):
        from waypoint.tui import render
        from rich.console import Console
        t = term.Term(unicode=True, color=False, width=100)
        buf = io.StringIO()
        c = Console(file=buf, width=100, force_terminal=True, color_system=None, highlight=False)
        c.print(render.header(t, STATUS), render.status_view(t, STATUS))
        out = buf.getvalue()
        self.assertNotIn("\x1b[3", out)
        self.assertNotIn("\x1b[9", out)
        self.assertNotIn("38;", out)

    def test_done_wraps_descriptions_in_second_column_and_handles_zero(self):
        from waypoint.tui import render
        done = render.done(NARROW_ASCII, 0, 0, "/some/very/long/logbook", 0)
        out = printed(NARROW_ASCII, done)
        self.assertIn("No AI tools found on PATH.", out)
        self.assertNotIn("0/0 AI tools connected", out)
        self.assertEqual(out.count("Next"), 1)
        self.assertIn("waypoint add|archive|remove|ignore", out)
        self.assertFalse(any(line.startswith(("refresh the dashboard", "this screen", "archive / remove"))
                            for line in out.splitlines()))
        self.assertIn("2 steps didn't happen", printed(NARROW_ASCII, render.done(NARROW_ASCII, 0, 2, "/lb", 2)))

    def test_narrow_ascii_header_is_plain_and_encodable(self):
        from waypoint.tui import render
        out = printed(NARROW_ASCII, render.header(NARROW_ASCII, None))
        self.assertEqual(out.splitlines()[0], "WAYPOINT")
        self.assertIn("No logbook yet", out)
        out.encode("ascii")  # no block, box or arrow characters left

    def test_ascii_header_with_long_logbook_path_encodes(self):
        from waypoint.tui import render
        status = {**STATUS, "logbook": "/" + "long" * 100}
        printed(NARROW_ASCII, render.header(NARROW_ASCII, status)).encode("ascii")

    def test_status_view_lines(self):
        from waypoint.tui import render
        out = printed(WIDE, render.status_view(WIDE, STATUS))
        self.assertIn("⚠ Codex can't write the logbook", out)
        self.assertIn("→ add /lb to writable_roots", out)
        printed(NARROW_ASCII, render.status_view(NARROW_ASCII, STATUS)).encode("ascii")

    def test_result_lines(self):
        from waypoint.tui import render
        self.assertEqual(str(render.result_line(WIDE, Step("Track alpha"), None)), "✓ Track alpha")
        self.assertEqual(str(render.result_line(WIDE, Step("Track _x"), "skipped: bad name")), "⚠ Track _x: skipped: bad name")
        self.assertEqual(str(render.result_line(NARROW_ASCII, Step("Connect Codex"), "disk full")), "X Connect Codex: disk full")
        self.assertIn("1 step didn't happen", str(render.done(WIDE, 0, 1, "/lb", 1).renderables[0]))


if __name__ == "__main__":
    unittest.main()
