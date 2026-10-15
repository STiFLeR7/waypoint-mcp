"""The screen's terminal layer (spec Part C, terminal handling): stdlib only, runs without the tui extra."""
import io
import unittest

from waypoint.tui import term

EXPECTED = [
    "██╗    ██╗ █████╗ ██╗   ██╗██████╗  ██████╗ ██╗███╗   ██╗████████╗",
    "██║    ██║██╔══██╗╚██╗ ██╔╝██╔══██╗██╔═══██╗██║████╗  ██║╚══██╔══╝",
    "██║ █╗ ██║███████║ ╚████╔╝ ██████╔╝██║   ██║██║██╔██╗ ██║   ██║",
    "██║███╗██║██╔══██║  ╚██╔╝  ██╔═══╝ ██║   ██║██║██║╚██╗██║   ██║",
    "╚███╔███╔╝██║  ██║   ██║   ██║     ╚██████╔╝██║██║ ╚████║   ██║",
    " ╚══╝╚══╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝      ╚═════╝ ╚═╝╚═╝  ╚═══╝   ╚═╝",
]


def stream(encoding):
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding)


class Term(unittest.TestCase):
    def test_banner_snapshot_and_gradient(self):
        rows = term.title_rows(term.Term(unicode=True, color=True, width=100))
        self.assertEqual([t for t, _ in rows], EXPECTED)
        self.assertEqual([c for _, c in rows], ["cyan", "cyan", "teal", "teal", "blue", "blue"])
        self.assertLessEqual(max(len(t) for t in EXPECTED), term.MIN_BANNER_WIDTH)

    def test_narrow_terminal_gets_one_line_title(self):
        self.assertEqual(term.title_rows(term.Term(unicode=True, color=True, width=69)), [("WAYPOINT", "brass")])
        self.assertEqual(len(term.title_rows(term.Term(unicode=True, color=True, width=70))), 6)

    def test_ascii_fallback_when_blocks_cant_be_encoded(self):
        t = term.detect(stream("cp437"), env={}, width=120)  # cp437 has ═ but not ✓
        self.assertFalse(t.unicode)
        self.assertEqual(term.title_rows(t), [("WAYPOINT", "brass")])
        self.assertEqual((t.glyphs["on"], t.glyphs["cursor"]), ("x", "->"))
        for g in t.glyphs.values():
            g.encode("ascii")

    def test_utf8_stream_keeps_unicode_glyphs(self):
        t = term.detect(stream("utf-8"), env={}, width=120)
        self.assertTrue(t.unicode)
        self.assertEqual((t.glyphs["on"], t.glyphs["cursor"], t.glyphs["radio"]), ("✓", "→", "●"))

    def test_no_color_is_honoured(self):
        self.assertFalse(term.detect(stream("utf-8"), env={"NO_COLOR": "1"}, width=80).color)
        self.assertTrue(term.detect(stream("utf-8"), env={"NO_COLOR": ""}, width=80).color)  # empty: not set
        self.assertFalse(term.detect(stream("utf-8"), env={"TERM": "dumb"}, width=80).color)

    def test_force_utf8_switches_a_legacy_console_stream(self):
        s = stream("cp1252")
        term.force_utf8(s)
        self.assertEqual(s.encoding.lower().replace("-", ""), "utf8")
        term.force_utf8(io.StringIO())  # no reconfigure: left alone, no error

    def test_palette_contrast_on_dark_and_light(self):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        for name, h in term.PALETTE.items():
            with self.subTest(name):
                self.assertGreaterEqual((lum(h) + 0.05) / 0.05, 3.0)          # on black
                self.assertGreaterEqual(1.05 / (lum(h) + 0.05), 3.0)          # on white


if __name__ == "__main__":
    unittest.main()
