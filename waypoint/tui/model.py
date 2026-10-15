"""Screen state and key handling, stdlib only (spec Part C). No drawing and no engine calls here: a widget feeds
key names to handle() and draws what the model says, so every key is unit-testable without a terminal.

Key names: "up", "down", "left", "tab", "space", "enter", "escape", "backspace", "c-c", or one printable character.
handle() returns None (stay on this screen), "next", "back", "quit", or a key listed in `actions`."""
from dataclasses import dataclass, field


@dataclass
class Choice:
    key: str  # stable id: a folder, a tool id, a menu item
    label: str
    detail: str = ""
    on: bool = False
    locked: bool = False  # shown, but Space doesn't change it (an undetected tool, an already-tracked project)


@dataclass
class ListModel:
    title: str
    choices: list
    multi: bool = True  # checklist; False: radio list (Enter picks exactly one)
    hint: str = ""
    actions: tuple = ()  # extra keys handed back to the screen, e.g. ("i",) to ignore a folder, ("tab",) for tabs
    cursor: int = 0
    query: str | None = None  # None: not typing a search; otherwise the `/` text being typed
    filter: str = ""

    def visible(self) -> list:
        q = self.filter.casefold()
        return [c for c in self.choices if not q or q in c.label.casefold() or q in c.detail.casefold()]

    def current(self) -> Choice | None:
        v = self.visible()
        self.cursor = max(0, min(self.cursor, len(v) - 1))
        return v[self.cursor] if v else None

    def selected(self) -> list:
        return [c.key for c in self.choices if c.on]

    def _clamp(self):
        self.cursor = max(0, min(self.cursor, len(self.visible()) - 1))

    def _pick(self, c):
        if c is None or c.locked:
            return
        if self.multi:
            c.on = not c.on
        else:
            for other in self.choices:
                other.on = other is c

    def handle(self, key: str):
        if key == "c-c":
            return "quit"
        if self.query is not None:  # typing a search: letters are text, Enter keeps the filter, Esc drops it
            if key == "enter":
                self.query = None
            elif key == "escape":
                self.query, self.filter = None, ""
            elif key == "backspace":
                self.query = self.filter = self.query[:-1]
            elif key == "space":
                self.query += " "
                self.filter = self.query
            elif len(key) == 1 and key.isprintable():
                self.query += key
                self.filter = self.query
            self._clamp()
            return None
        if key in ("up", "down"):
            n = len(self.visible())
            if n:
                self.cursor = (self.cursor + (1 if key == "down" else -1)) % n
                if not self.multi:  # a single choice follows the cursor, so the dot is never somewhere else
                    self._pick(self.current())
        elif key == "space":
            self._pick(self.current())
        elif key == "enter":
            if not self.multi:
                c = self.current()
                if c is None or c.locked:
                    return None
                self._pick(c)
            return "next"
        elif key in ("left", "backspace"):
            return "back"
        elif key == "escape":
            return "quit"
        elif key == "/":
            self.query = self.filter
        elif key == "a" and self.multi and "a" not in self.actions:
            pool = [c for c in self.visible() if not c.locked]
            want = not all(c.on for c in pool)
            for c in pool:
                c.on = want
        elif key in self.actions:
            return key
        return None


@dataclass
class ReviewModel:
    """The Review screen: one row per step, then the Apply row. Enter on a step folds or unfolds its changes."""
    titles: list
    cursor: int = 0
    open: set = field(default_factory=set)

    def handle(self, key: str):
        rows = len(self.titles) + 1  # the last row is "Apply"
        if key in ("up", "down"):
            self.cursor = (self.cursor + (1 if key == "down" else -1)) % rows
        elif key == "enter":
            if self.cursor == len(self.titles):
                return "apply"
            self.open ^= {self.cursor}
        elif key in ("left", "backspace"):
            return "back"
        elif key in ("escape", "c-c"):
            return "quit"
        return None
