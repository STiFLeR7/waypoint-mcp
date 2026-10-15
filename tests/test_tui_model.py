"""Screen models (spec Part C): every key, without a terminal. stdlib only, runs without the tui extra."""
import unittest

from waypoint.tui.model import Choice, ListModel, ReviewModel


def checklist(**kw):
    return ListModel("Projects", [Choice("/c/alpha", "alpha", "claude-code · 2 days"),
                                  Choice("/c/beta", "beta", "codex · 40 days"),
                                  Choice("/c/gamma", "gamma", "git · 3 days", on=True, locked=True)], **kw)


def keys(m, *names):
    out = None
    for k in names:
        out = m.handle(k)
    return out


class Lists(unittest.TestCase):
    def test_move_wraps_and_space_toggles(self):
        m = checklist()
        keys(m, "down", "space")
        self.assertEqual(m.selected(), ["/c/beta", "/c/gamma"])
        keys(m, "up", "up")
        self.assertEqual(m.current().label, "gamma")  # wrapped from the top
        keys(m, "space")
        self.assertTrue(m.choices[2].on)  # locked: Space doesn't change it

    def test_a_toggles_every_unlocked_visible_choice(self):
        m = checklist()
        m.handle("a")
        self.assertEqual(m.selected(), ["/c/alpha", "/c/beta", "/c/gamma"])
        m.handle("a")
        self.assertEqual(m.selected(), ["/c/gamma"])

    def test_search_filters_and_letters_are_text_while_typing(self):
        m = checklist(actions=("i",))
        self.assertIsNone(keys(m, "/", "c", "o", "d", "e", "x"))  # no "i" action, no quit: it's all query text
        self.assertEqual([c.label for c in m.visible()], ["beta"])
        keys(m, "enter")  # stops typing, keeps the filter
        self.assertEqual(m.filter, "codex")
        self.assertEqual(m.handle("i"), "i")  # letters are actions again
        keys(m, "/", "escape")  # Esc while typing drops the filter; it doesn't quit
        self.assertEqual(len(m.visible()), 3)

    def test_search_with_no_match_is_safe(self):
        m = checklist()
        keys(m, "/", "z", "z", "enter")
        self.assertIsNone(m.current())
        self.assertIsNone(keys(m, "down", "space"))
        self.assertEqual(m.handle("enter"), "next")

    def test_backspace_goes_back_except_while_searching(self):
        m = ListModel("t", [Choice("a", "A"), Choice("b", "B")], multi=False)
        self.assertEqual(m.handle("backspace"), "back")
        m.handle("/"); m.handle("x")
        self.assertIsNone(m.handle("backspace"))  # deletes the typed letter
        self.assertEqual(m.query, "")
        self.assertEqual(ReviewModel(["s"]).handle("backspace"), "back")

    def test_single_choice_follows_the_cursor(self):
        m = ListModel("t", [Choice("a", "A", on=True), Choice("b", "B")], multi=False)
        m.handle("down")
        self.assertEqual(m.selected(), ["b"])

    def test_current_clamps_when_choices_shrink(self):
        m = checklist()
        m.handle("up")  # wraps to the last row (gamma)
        m.choices.pop()
        self.assertEqual(m.current().label, "beta")

    def test_search_starts_with_current_filter_and_accepts_spaces(self):
        m = checklist()
        keys(m, "/", "c", "o", "d", "e", "x", "enter", "/", "space")
        self.assertEqual(m.query, "codex ")
        self.assertEqual(m.filter, "codex ")

    def test_all_under_filter_only_toggles_visible_and_search_keeps_selection(self):
        m = ListModel("Projects", [Choice("alpha", "alpha", on=True), Choice("beta", "beta"),
                                    Choice("gamma", "gamma")])
        keys(m, "/", "b", "e", "t", "a", "enter")
        self.assertEqual(m.selected(), ["alpha"])
        m.handle("a")
        self.assertEqual(m.selected(), ["alpha", "beta"])

    def test_searching_does_not_clear_selected_choice(self):
        m = checklist()
        m.handle("space")
        keys(m, "/", "b", "e", "t", "a", "enter")
        self.assertEqual(m.selected(), ["/c/alpha", "/c/gamma"])

    def test_backspace_edits_the_query(self):
        m = checklist()
        keys(m, "/", "b", "x", "backspace")
        self.assertEqual([c.label for c in m.visible()], ["beta"])

    def test_navigation_outcomes(self):
        m = checklist(actions=("tab",))
        self.assertEqual(m.handle("enter"), "next")
        self.assertEqual(m.handle("left"), "back")
        self.assertEqual(m.handle("escape"), "quit")
        self.assertEqual(m.handle("c-c"), "quit")
        self.assertEqual(m.handle("tab"), "tab")
        self.assertIsNone(m.handle("q"))  # not an action here

    def test_radio_enter_picks_exactly_one(self):
        m = ListModel("Logbook", [Choice("a", "A", on=True), Choice("b", "B")], multi=False)
        self.assertEqual(keys(m, "down", "enter"), "next")
        self.assertEqual(m.selected(), ["b"])
        locked = ListModel("x", [Choice("a", "A", locked=True)], multi=False)
        self.assertIsNone(locked.handle("enter"))  # a locked row can't be the answer


class Review(unittest.TestCase):
    def test_enter_folds_and_unfolds_then_apply_row(self):
        r = ReviewModel(["Create the logbook", "Connect Codex"])
        r.handle("enter")
        self.assertEqual(r.open, {0})
        r.handle("enter")
        self.assertEqual(r.open, set())
        self.assertEqual(keys(r, "down", "down", "enter"), "apply")
        self.assertEqual(r.handle("left"), "back")
        self.assertEqual(r.handle("escape"), "quit")


if __name__ == "__main__":
    unittest.main()
