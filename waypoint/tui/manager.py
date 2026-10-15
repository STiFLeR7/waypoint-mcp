"""`waypoint` with no arguments (spec Part C, manager): banner and panel, then Projects, AI tools, Status and
Refresh. Every change goes through the same Review → Apply screens as setup, then the dashboard is refreshed."""
from pathlib import Path

from .. import config, project, status
from . import actions, render, wizard
from .model import Choice, ListModel

MENU = (("projects", "Projects", "track, archive, restore, remove, ignore"),
        ("tools", "Connect AI tools", "connect, disconnect, fix drifted rules"),
        ("status", "Status", "logbook, projects, AI tools, problems"),
        ("refresh", "Refresh dashboard", "DASHBOARD.md, dashboard.json and chart.html"),
        ("quit", "Quit", ""))
KEYS = {"tracked": ("x", "d"), "archived": ("r",), "untracked": ("t", "i")}
HINTS = {"tracked": "x archive · d remove", "archived": "r restore", "untracked": "t track · i ignore"}
LABELS = {"x": "Archive", "d": "Remove", "r": "Restore", "t": "Track", "i": "Ignore", "c": "Connect",
          "u": "Disconnect", "f": "Fix drifted rules"}
PLAN = {"x": actions.plan_archive, "d": actions.plan_remove, "r": actions.plan_restore, "t": actions.plan_track,
        "i": actions.plan_ignore}


def _hint(ui, text):
    sep = ui.term.glyphs["sep"]
    text = text.replace(" · ", f" {sep} ")
    back = "←/Backspace back" if ui.term.unicode else "left/Backspace back"
    return f"{text} {sep} / search {sep} {back} {sep} Esc quit"


def row_menu(ui, c, keys):
    """Enter on a row: its actions as a list (the letters are shortcuts). The action key, "back" or "quit"."""
    rows = [Choice(k, LABELS[k], f"key {k}") for k in keys] + [Choice("back", "Back")]
    rows[0].on = True
    m = ListModel(c.label, rows, multi=False)
    out = ui.choose(m)
    return out if out != "next" else m.selected()[0]


def apply_flow(ui, lb: Path, home: Path, steps) -> str:
    """Review, then Apply and refresh: returns "next" (done or nothing to do), "back" or "quit"."""
    steps = [st for st in steps if st.writes() or st.problem]
    if not steps:
        ui.show(render.line(ui.term, "ok", "Nothing to change."))
        return "next"
    out = ui.review(steps)
    if out != "apply":
        return out
    for st, err in actions.apply_steps(steps):
        ui.show(render.result_line(ui.term, st, err))
    refresh_line(ui, lb, home)
    return "next"


def refresh_line(ui, lb: Path, home: Path):
    try:
        k = actions.refresh(lb, home)
    except (config.ConfigError, OSError) as e:
        ui.show(render.line(ui.term, "bad", f"couldn't refresh the dashboard: {e}"))
        return
    ui.show(render.line(ui.term, "ok", f"Dashboard refreshed: {k['tracked']} tracked, {k['untracked']} untracked, "
                                       f"{k['attention']} attention items. {(lb / 'chart.html').resolve()}"))


def projects_screen(ui, lb: Path, source: str, home: Path) -> str:
    tab = 0
    while True:
        name = actions.TABS[tab]
        title = "Projects  " + "  ".join(f"[{t.title()}]" if t == name else t.title() for t in actions.TABS)
        rows = actions.manager_rows(lb, home, name)
        if rows:
            rows[0].on = True
        m = ListModel(title, rows, multi=False, actions=("tab", *KEYS[name]),
                      hint=_hint(ui, f"Enter actions · Tab next list · {HINTS[name]}"))
        out = ui.choose(m)
        if out == "tab":
            tab = (tab + 1) % len(actions.TABS)
            continue
        if out in ("back", "quit"):
            return out
        c = m.current()
        if c is None:
            continue
        if out == "next":
            out = row_menu(ui, c, KEYS[name])
            if out == "quit":
                return "quit"
            if out == "back":
                continue
        try:
            step = PLAN[out](lb, c.key)
        except ValueError as e:
            ui.show(render.line(ui.term, "bad", str(e)))
            continue
        if apply_flow(ui, lb, home, [step]) == "quit":
            return "quit"


def tools_screen(ui, lb: Path, source: str, home: Path) -> str:
    while True:
        s = status.collect(lb, source, home)
        rows = actions.tool_rows(s)
        if rows:
            rows[0].on = True
        m = ListModel("AI tools", rows, multi=False, actions=("c", "u", "f"),
                      hint=_hint(ui, "Enter actions · c connect · u disconnect · f fix drifted rules"))
        out = ui.choose(m)
        if out in ("back", "quit"):
            return out
        c = m.current()
        if c is None:
            continue
        if out == "next":
            out = row_menu(ui, c, ("c", "u", "f"))
            if out == "quit":
                return "quit"
            if out == "back":
                continue
        if out == "c":
            pool = [(str(folder), name) for name, folder in project.repos(lb)]
            where, repos = wizard.where_screen(ui, c.key, lb, home, pool)
            if where == "quit":
                return "quit"
            if where == "back":
                continue
            steps = actions.plan_connect(c.key, lb, home, repos)
        elif out == "u":
            t = s["tools"].get(c.key)
            repos = [Path(p["folder"]) for p in t["projects"]] if t else []
            steps = actions.plan_disconnect(c.key, home) + (actions.plan_disconnect(c.key, home, repos) if repos else [])
        else:
            steps = actions.plan_fix_drift(lb, home, [k for k, t in s["tools"].items() if t["rule"] == "drifted"])
        if apply_flow(ui, lb, home, steps) == "quit":
            return "quit"


def status_screen(ui, lb: Path, source: str, home: Path) -> str:
    ui.show(render.status_view(ui.term, status.collect(lb, source, home)))
    out = ui.choose(ListModel("", [Choice("back", "Back", on=True)], multi=False))
    return "quit" if out == "quit" else "back"


def run(ui, lb: Path, source: str, home: Path) -> int:
    ui.show(render.header(ui.term, status.collect(lb, source, home)))
    screens = {"projects": projects_screen, "tools": tools_screen, "status": status_screen}
    while True:
        rows = [Choice(k, label, detail) for k, label, detail in MENU]
        rows[0].on = True
        m = ListModel("Choose", rows, multi=False)
        out = ui.choose(m)
        if out in ("quit", "back") or m.selected() == ["quit"]:
            return 0
        pick = m.selected()[0]
        if pick == "refresh":
            refresh_line(ui, lb, home)
        elif screens[pick](ui, lb, source, home) == "quit":
            return 0
