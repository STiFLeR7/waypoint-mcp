"""prompt_toolkit widgets (needs the tui extra): an inline checklist / radio list and the Review list. They only map
keys to model.handle() and draw the model; outcomes come back to the screen that asked."""
from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.shortcuts import PromptSession
from prompt_toolkit.styles import Style

from . import actions
from .model import ReviewModel
from .term import PALETTE, Term

ASCII_TEXT = str.maketrans({"·": "|", "…": "...", "→": "->", "—": "-", "–": "-", "⚠": "!", "✓": "ok", "✗": "X", "↑": "^", "↓": "v"})
NAMED = {"up": "up", "down": "down", "left": "left", "tab": "tab", "enter": "enter", "escape": "escape",
         "backspace": "backspace", "c-c": "c-c", " ": "space"}


def style(term: Term) -> Style:
    if not term.color:
        return Style([])
    return Style.from_dict({"title": f"{PALETTE['brass']} bold", "cursor": PALETTE["cyan"] + " bold",
                            "dim": PALETTE["dim"], "bad": PALETTE["bad"], "apply": f"{PALETTE['brass']} bold"})


def _t(term: Term, text: str) -> str:
    """Labels and details come from the engine and may hold · or →: plain ASCII on a terminal that can't show them."""
    return text if term.unicode else text.translate(ASCII_TEXT).encode("ascii", "replace").decode()


def hint(term: Term, model) -> str:
    if model.hint:
        return _t(term, model.hint)
    move = "↑↓ move" if term.unicode else "up/down move"
    back = "←/Backspace back" if term.unicode else "left/Backspace back"
    parts = [move, "Space toggle" if model.multi else "", "a all" if model.multi else "", "Enter next", back,
             "/ search", "Esc quit"]
    return f" {term.glyphs['sep']} ".join(p for p in parts if p)


def list_fragments(term: Term, model):
    """(fragments, cursor line) for a ListModel."""
    g, out, cursor_line = term.glyphs, [("class:title", f"{_t(term, model.title)}\n")], 1
    pad = " " * len(g["cursor"])
    for i, c in enumerate(model.visible()):
        here = i == model.cursor
        mark = (g["on"] if c.on else " ") if model.multi else (g["radio"] if c.on else " ")
        box = f"[{mark}]" if model.multi else f"({mark})"
        if here:
            cursor_line = 1 + i  # line 0 is the title
        style_ = "class:dim" if c.locked and not c.on else ("class:cursor" if here else "")
        out.append((style_, f" {g['cursor'] if here else pad} {box} {_t(term, c.label)}"))
        out.append(("class:dim", f"  {_t(term, c.detail)}\n" if c.detail else "\n"))
    if not model.visible():
        out.append(("class:dim", "   (nothing matches)\n"))
    if model.query is not None:
        out.append(("", f" / {model.query}\n"))
    elif model.filter:
        out.append(("class:dim", f" filter: {model.filter}  (/ to change)\n"))
    out.append(("class:dim", " " + hint(term, model)))
    return out, cursor_line


def review_fragments(term: Term, model: ReviewModel, steps):
    g, out = term.glyphs, [("class:title", "Review: nothing is written until you choose Apply\n")]
    line, cursor_line = 1, 1
    pad = " " * len(g["cursor"])
    for i, st in enumerate(steps):
        here = i == model.cursor
        if here:
            cursor_line = line
        fold = ("▾" if i in model.open else "▸") if term.unicode else ("v" if i in model.open else ">")
        mark = f"{g['bad']} " if st.problem else ""
        out.append(("class:cursor" if here else "", f" {g['cursor'] if here else pad} {fold} {mark}{_t(term, st.title)}\n"))
        line += 1
        if st.problem:
            out.append(("class:bad", f"      won't run: {_t(term, st.problem)}\n"))
            line += 1
        if i in model.open:
            for text in actions.step_lines(st, g) + actions.diff_lines(st):
                out.append(("class:dim", f"      {_t(term, text)}\n"))
                line += 1
    here = model.cursor == len(steps)
    if here:
        cursor_line = line
    label = "Apply these changes" if any(not st.problem for st in steps) else "Nothing to apply: finish"
    out.append(("class:apply", f" {g['cursor'] if here else pad} {label}\n"))
    out.append(("class:dim", " Existing files are backed up next to themselves (.bak-<time>) first.\n"))
    move = "↑↓ move" if term.unicode else "up/down move"
    out.append(("class:dim", f" {move} {g['sep']} Enter show/hide changes {g['sep']} Enter on Apply applies "
                             f"{g['sep']} {'←' if term.unicode else 'left'} back {g['sep']} Esc quit"))
    return out, cursor_line


def _run(term: Term, model, fragments, input=None, output=None):
    kb = KeyBindings()

    def send(name):
        def handler(event):
            result = model.handle(name)
            if result is not None:
                event.app.exit(result=result)
        return handler

    for key, name in NAMED.items():
        kb.add(key, eager=key == "escape")(send(name))
    kb.add(Keys.SIGINT)(send("c-c"))

    @kb.add(Keys.Any)
    def _(event):
        if len(event.data) == 1 and event.data.isprintable():
            send(event.data)(event)

    state = {}

    def text():
        frags, state["line"] = fragments()
        return frags

    control = FormattedTextControl(text, focusable=True, show_cursor=False,
                                   get_cursor_position=lambda: Point(0, state.get("line", 0)))
    app = Application(layout=Layout(Window(control, wrap_lines=False)), key_bindings=kb, style=style(term),
                      full_screen=False, input=input, output=output, erase_when_done=False,
                      color_depth=None if term.color else ColorDepth.DEPTH_1_BIT)
    return app.run()


def choose(term: Term, model, input=None, output=None):
    """Run a ListModel until it returns an outcome: "next", "back", "quit" or one of its action keys."""
    return _run(term, model, lambda: list_fragments(term, model), input, output)


def review(term: Term, steps, input=None, output=None):
    """The Review screen for a plan of Steps: "apply", "back" or "quit"."""
    model = ReviewModel([st.title for st in steps])
    return _run(term, model, lambda: review_fragments(term, model, steps), input, output)


class UI:
    """What a screen needs: the terminal facts, a Rich console to print to, and (in tests) a pipe input."""

    def __init__(self, term: Term, console, input=None, output=None):
        self.term, self.console, self.input, self.output = term, console, input, output

    def show(self, *renderables):
        self.console.print(*renderables)

    def choose(self, model):
        return choose(self.term, model, self.input, self.output)

    def review(self, steps):
        return review(self.term, steps, self.input, self.output)

    def text(self, message, default=""):
        return ask_text(message, default, self.input, self.output)


def ask_text(message: str, default: str = "", input=None, output=None):
    """One line of text (a path); None when the user presses Ctrl-C or the input ends."""
    try:
        return PromptSession(input=input, output=output).prompt(message, default=default).strip()
    except (KeyboardInterrupt, EOFError):
        return None
