"""prompt_toolkit widgets (spec Part C, widgets and keys): what the lists and Review draw, and one pipe-input run
of each widget. Needs the tui extra (skipped without it)."""
import unittest

from waypoint import tui
from waypoint.tui import term
from waypoint.tui.actions import Step
from waypoint.tui.model import Choice, ListModel, ReviewModel

NEED = unittest.skipUnless(tui.available(), "needs the tui extra: pip install -e .[tui]")
WIDE = term.Term(unicode=True, color=True, width=100)
NARROW_ASCII = term.Term(unicode=False, color=False, width=60)
DOWN, UP, ENTER, SPACE, ESC = "\x1b[B", "\x1b[A", "\r", " ", "\x1b"


@NEED
class Draw(unittest.TestCase):
    def text(self, frags):
        return "".join(t for _, t in frags)

    def test_checklist_rows_unicode_and_ascii(self):
        from waypoint.tui import widgets
        m = ListModel("Projects", [Choice("a", "alpha", "codex · 2 days ago", on=True), Choice("b", "beta")])
        frags, line = widgets.list_fragments(WIDE, m)
        self.assertIn(" → [✓] alpha  codex · 2 days ago", self.text(frags))
        self.assertIn("↑↓ move · Space toggle", self.text(frags))
        self.assertEqual(line, 1)
        frags, _ = widgets.list_fragments(NARROW_ASCII, m)
        self.assertIn(" -> [x] alpha  codex | 2 days ago", self.text(frags))  # engine text made ASCII too
        self.text(frags).encode("ascii")

    def test_ascii_label_replaces_dash_and_accent(self):
        from waypoint.tui import widgets
        m = ListModel("Projects", [Choice("x", "— café")])
        self.assertIn("- caf?", self.text(widgets.list_fragments(NARROW_ASCII, m)[0]))

    def test_radio_rows(self):
        from waypoint.tui import widgets
        m = ListModel("Logbook", [Choice("a", "New", on=True), Choice("b", "Choose")], multi=False, cursor=1)
        frags, line = widgets.list_fragments(WIDE, m)
        self.assertIn("   (●) New", self.text(frags))
        self.assertIn(" → ( ) Choose", self.text(frags))
        self.assertEqual(line, 2)

    def test_review_folds_and_marks_refusals(self):
        from waypoint.tui import widgets
        steps = [Step("Track alpha", run=lambda: None, lines=["+ create projects/alpha.md"]),
                 Step("Track _x", run=lambda: None, why="bad name")]
        model = ReviewModel([s.title for s in steps])
        closed = self.text(widgets.review_fragments(WIDE, model, steps)[0])
        self.assertNotIn("+ create projects/alpha.md", closed)
        self.assertIn("✗ Track _x", closed)
        self.assertIn("won't run: bad name", closed)
        model.open.add(0)
        self.assertIn("+ create projects/alpha.md", self.text(widgets.review_fragments(WIDE, model, steps)[0]))
        self.text(widgets.review_fragments(NARROW_ASCII, model, steps)[0]).encode("ascii")


@NEED
class Keys(unittest.TestCase):
    def setUp(self):
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        self.pipe, self.output = self.enterContext(create_pipe_input()), DummyOutput()

    def test_choose_maps_keys_to_the_model(self):
        from waypoint.tui import widgets
        m = ListModel("Projects", [Choice("a", "alpha"), Choice("b", "beta")], actions=("i",))
        self.pipe.send_text(DOWN + SPACE + ENTER)
        self.assertEqual(widgets.choose(WIDE, m, self.pipe, self.output), "next")
        self.assertEqual(m.selected(), ["b"])
        self.pipe.send_text("i")
        self.assertEqual(widgets.choose(WIDE, m, self.pipe, self.output), "i")

    def test_escape_quits(self):
        from waypoint.tui import widgets
        self.pipe.send_text(ESC)
        self.assertEqual(widgets.choose(WIDE, ListModel("x", [Choice("a", "A")]), self.pipe, self.output), "quit")

    def test_ctrl_c_quits_and_left_goes_back(self):
        from waypoint.tui import widgets
        self.pipe.send_text("\x03")
        self.assertEqual(widgets.choose(WIDE, ListModel("x", [Choice("a", "A")]), self.pipe, self.output), "quit")
        self.pipe.send_text("\x1b[D")
        self.assertEqual(widgets.choose(WIDE, ListModel("x", [Choice("a", "A")]), self.pipe, self.output), "back")

    def test_review_apply_row(self):
        from waypoint.tui import widgets
        self.pipe.send_text(ENTER + UP + ENTER)  # unfold step 1, then the Apply row
        self.assertEqual(widgets.review(WIDE, [Step("Track alpha", run=lambda: None)], self.pipe, self.output), "apply")

    def test_ask_text(self):
        from waypoint.tui import widgets
        self.pipe.send_text("/srv/logbook" + ENTER)
        self.assertEqual(widgets.ask_text("Folder: ", "", self.pipe, self.output), "/srv/logbook")


if __name__ == "__main__":
    unittest.main()
