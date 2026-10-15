"""Terminal facts for the screen, stdlib only: palette, glyphs, banner, UTF-8 and colour detection."""
import os
import shutil
import sys
from dataclasses import dataclass

# "Waypoint sea". Each colour is >= 3:1 against both black and white backgrounds (WCAG large text / UI graphics),
# so the banner and headings read on dark and light terminals alike.
PALETTE = {
    "cyan": "#0FA3B1",   # 6.9:1 on black, 3.1:1 on white
    "teal": "#14958A",   # 5.7:1 / 3.7:1
    "blue": "#3474D4",   # 4.6:1 / 4.6:1
    "brass": "#9C7A2A",  # 5.2:1 / 4.0:1: titles, panel headings, warnings
    "dim": "#7A808A",    # 5.3:1 / 4.0:1: key hints, details
    "ok": "#2E9E5B",     # 6.2:1 / 3.4:1
    "bad": "#D0533C",    # 5.0:1 / 4.2:1
}

BANNER = """\
██╗    ██╗ █████╗ ██╗   ██╗██████╗  ██████╗ ██╗███╗   ██╗████████╗
██║    ██║██╔══██╗╚██╗ ██╔╝██╔══██╗██╔═══██╗██║████╗  ██║╚══██╔══╝
██║ █╗ ██║███████║ ╚████╔╝ ██████╔╝██║   ██║██║██╔██╗ ██║   ██║
██║███╗██║██╔══██║  ╚██╔╝  ██╔═══╝ ██║   ██║██║██║╚██╗██║   ██║
╚███╔███╔╝██║  ██║   ██║   ██║     ╚██████╔╝██║██║ ╚████║   ██║
 ╚══╝╚══╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝      ╚═════╝ ╚═╝╚═╝  ╚═══╝   ╚═╝"""
GRADIENT = ("cyan", "cyan", "teal", "teal", "blue", "blue")  # one colour per banner row, top to bottom
TITLE = "WAYPOINT"
TAGLINE = "One logbook for every voyage, whichever navigator is at the helm."
MIN_BANNER_WIDTH = 70

UNICODE = {"cursor": "→", "on": "✓", "radio": "●", "ok": "✓", "warn": "⚠", "bad": "✗", "link": "→", "sep": "·"}
ASCII = {"cursor": "->", "on": "x", "radio": "*", "ok": "ok", "warn": "!", "bad": "X", "link": "->", "sep": "|"}


@dataclass(frozen=True)
class Term:
    unicode: bool
    color: bool
    width: int

    @property
    def glyphs(self) -> dict:
        return UNICODE if self.unicode else ASCII


def force_utf8(stream) -> None:
    """Windows consoles (cp1252 and friends): switch the stream to UTF-8 when it allows it."""
    enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    if enc != "utf8" and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


def can_encode(stream) -> bool:
    """The stream can print the banner's block characters and the widget glyphs."""
    try:
        (BANNER + "".join(UNICODE.values())).encode(getattr(stream, "encoding", None) or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def detect(stream=None, env=None, width=None) -> Term:
    stream = stream or sys.stdout
    env = os.environ if env is None else env
    color = not env.get("NO_COLOR") and env.get("TERM") != "dumb"  # https://no-color.org: set and nonempty
    return Term(unicode=can_encode(stream), color=color, width=width or shutil.get_terminal_size((80, 24)).columns)


def title_rows(term: Term) -> list:
    """[(text, palette key or None)]: the banner with its gradient, or one plain line when it can't fit or print."""
    if not term.unicode or term.width < MIN_BANNER_WIDTH:
        return [(TITLE, "brass")]
    return list(zip(BANNER.splitlines(), GRADIENT))
