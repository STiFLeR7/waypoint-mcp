"""Packaging of the screen (spec Part C, packaging): an optional `tui` extra; the base package stays dependency-free
and the engine never imports Rich or prompt_toolkit. stdlib only, runs with or without the extra."""
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from waypoint import tui

ROOT = Path(__file__).resolve().parent.parent


class Packaging(unittest.TestCase):
    def test_extra_declared_and_base_dependency_free(self):
        p = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(p["dependencies"], [])
        self.assertEqual(p["optional-dependencies"]["tui"], ["rich>=13", "prompt_toolkit>=3"])

    def test_tui_package_ships_in_the_wheel(self):
        p = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("waypoint.tui", p["tool"]["setuptools"]["packages"])

    def test_engine_and_stdlib_screen_parts_never_import_the_extra(self):
        code = ("import sys, waypoint.cli, waypoint.mcp, waypoint.status, waypoint.tui, waypoint.tui.term, waypoint.tui.model, waypoint.tui.actions; "
                "print(sorted(m for m in sys.modules if m.split('.')[0] in ('rich', 'prompt_toolkit')))")
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "[]")

    def test_available_needs_both_libraries(self):
        with patch("importlib.util.find_spec", side_effect=lambda m: None if m == "prompt_toolkit" else object()):
            self.assertFalse(tui.available())
        with patch("importlib.util.find_spec", return_value=object()):
            self.assertTrue(tui.available())

    def test_interactive_needs_both_ends_on_a_terminal(self):
        class Tty:
            def __init__(self, tty):
                self.tty = tty

            def isatty(self):
                return self.tty
        self.assertTrue(tui.interactive(Tty(True), Tty(True)))
        self.assertFalse(tui.interactive(Tty(True), Tty(False)))
        self.assertFalse(tui.interactive(Tty(False), Tty(True)))


if __name__ == "__main__":
    unittest.main()
