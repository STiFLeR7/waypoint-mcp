"""The optional screen (spec Part C): `pip install waypoint-mcp[tui]` adds Rich and prompt_toolkit.

The engine never imports this package's screens; cli.py asks available() first and falls back to numbered prompts.
term, model and actions are stdlib-only; render, widgets, wizard and manager import Rich / prompt_toolkit."""
import importlib.util
import sys

INSTALL_HINT = "For the arrow-key screen: pip install waypoint-mcp[tui]"


def available() -> bool:
    """Rich and prompt_toolkit are importable (the tui extra is installed)."""
    return all(importlib.util.find_spec(m) is not None for m in ("rich", "prompt_toolkit"))


def interactive(stdin=None, stdout=None) -> bool:
    """Both ends are a terminal: the screen can take keys and draw."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    return all(hasattr(s, "isatty") and s.isatty() for s in (stdin, stdout))


def _ui():
    from . import render, term, widgets
    term.force_utf8(sys.stdout)
    t = term.detect(sys.stdout)
    return widgets.UI(t, render.console(t))


def run_wizard(home) -> int:
    """`waypoint setup` on a terminal with the extra installed."""
    from . import wizard
    return wizard.run(_ui(), home)


def run_manager(lb, source, home) -> int:
    """`waypoint` alone, once a logbook is known."""
    from . import manager
    return manager.run(_ui(), lb, source, home)
