"""`waypoint setup` as a screen (spec Part C, wizard steps 1-8). Each screen function asks, stores the answer in
the Wizard, and returns "next", "back", "quit" or "skip". Nothing is written before Apply: steps 1-5 only read,
step 6 shows the plan (actions.wizard_plan), step 7 applies it."""
import os
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from .. import cli, clis, config, rules, status
from . import actions, render
from .model import Choice, ListModel

TOTAL = 8


@dataclass
class Wizard:
    home: Path
    lb: Path | None = None
    git: bool = False
    roots: list = field(default_factory=list)
    extra_roots: list = field(default_factory=list)
    projects: list = field(default_factory=list)
    scanned: list | None = None
    ignore: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    where: dict = field(default_factory=dict)
    codex_writable: bool = True
    plan: list = field(default_factory=list)
    results: list = field(default_factory=list)

    def setup(self) -> actions.Setup:
        track = [c.key for c in self.projects if c.on and not c.locked]
        return actions.Setup(self.lb, self.roots, git=self.git, track=track, ignore=list(self.ignore),
                             tools={t: self.where.get(t) for t in self.tools}, codex_writable=self.codex_writable)

    def repo_pool(self) -> list:
        """Folders a per-project install can target: tracked projects and the ones being added."""
        return [(c.key, c.label) for c in self.projects if c.on]


def _lb_detail(p: Path) -> str:
    if (p / "projects").is_dir():
        return "a logbook · " + ("git repo" if (p / ".git").exists() else "not a git repo")
    return "will be created"


def rule_logbook(home: Path):
    """The logbook Claude Code's installed rule imports, if it's a logbook: offered first when none is remembered,
    so a re-run doesn't default to a new, empty one."""
    text, _ = rules._read(rules.claude_rule_file(home))
    for line in (text or "").splitlines():
        if line.startswith("@") and line.endswith("/GLOBAL-RULES.md"):
            p = Path(line[1:-len("/GLOBAL-RULES.md")].replace("\\ ", " "))
            if (p / "projects").is_dir():
                return p
    return None


def screen_logbook(ui, w: Wizard):
    ui.show(render.step_title(ui.term, 1, TOTAL, "Logbook",
                              "The logbook is a separate folder holding one note per project, "
                              f"e.g. {w.home / 'waypoint-logbook'}"))
    saved, source = config.find_logbook(None)
    rows = []
    if source != "the current folder":
        label = f"From WAYPOINT_LOGBOOK: {saved}" if source == "WAYPOINT_LOGBOOK" else f"Remembered: {saved}"
        rows.append(Choice(str(saved.expanduser().resolve()), label, _lb_detail(saved)))
    else:
        used = rule_logbook(w.home)
        if used is not None:
            rows.append(Choice(str(used.resolve()), f"In use by your AI tools: {used}", _lb_detail(used)))
    new = w.home / "waypoint-logbook"
    if not rows or Path(rows[0].key) != new.resolve():
        rows.append(Choice(str(new.resolve()), f"{'Use' if (new / 'projects').is_dir() else 'New'}: {new}", _lb_detail(new)))
    rows.append(Choice("", "Choose a folder…" if ui.term.unicode else "Choose a folder..."))
    rows[0].on = True
    while True:
        m = ListModel("Where should the logbook live?", rows, multi=False)
        out = ui.choose(m)
        if out != "next":
            return out
        chosen = m.selected()[0]
        if not chosen:
            typed = ui.text("Logbook folder: ", str(w.lb or ""))
            if not typed:
                continue
            chosen = str(Path(typed).expanduser().resolve())
        w.lb = Path(chosen)
        if (w.lb / ".git").exists():
            w.git = False
            return "next"
        g = ListModel("Make the logbook a git repo? It keeps every change, and lets you push it",
                      [Choice("yes", "Yes, make it a git repo (recommended)", on=True), Choice("no", "Not now")], multi=False)
        out = ui.choose(g)
        if out == "next":
            w.git = g.selected() == ["yes"]
            return "next"
        if out == "quit":
            return out


def screen_roots(ui, w: Wizard):
    ui.show(render.step_title(ui.term, 2, TOTAL, "Roots",
                              f"Folders your projects live under, e.g. {w.home / 'code'}. "
                              "Waypoint looks for AI activity there."))
    toml = w.lb / "waypoint.toml"
    if toml.exists():
        try:
            w.roots = config.load(w.lb).roots
        except config.ConfigError as e:
            ui.show(render.line(ui.term, "bad", str(e)))
            return "quit"
        m = ListModel("This logbook's waypoint.toml sets the roots (edit that file to change them)",
                      [Choice("keep", ", ".join(w.roots), "kept", on=True)], multi=False)
        return ui.choose(m)
    found = [str(w.home / h) for h in cli.ROOT_HINTS if (w.home / h).is_dir()]
    candidates = list(dict.fromkeys([*found, str(w.lb.parent), *w.extra_roots]))
    chosen = set(w.roots) or set(found or [str(w.lb.parent)]) | set(w.extra_roots)
    warn = "⚠" if ui.term.unicode else "!"
    rows = [Choice(r, r, f"{warn} very broad: every folder under it with AI activity shows up" if cli._broad(r) else "",
                   on=r in chosen and not (cli._broad(r) and r not in w.roots)) for r in candidates]
    while True:
        m = ListModel("Roots (tick one or more; n adds a folder)", rows, actions=("n",),
                      hint=f"Space toggle {ui.term.glyphs['sep']} n add a folder {ui.term.glyphs['sep']} Enter next "
                           f"{ui.term.glyphs['sep']} {'←' if ui.term.unicode else 'left'} back {ui.term.glyphs['sep']} Esc quit")
        out = ui.choose(m)
        if out == "n":
            typed = ui.text("Add a folder: ")
            if typed:
                r = str(Path(typed).expanduser().resolve())
                w.extra_roots.append(r)
                rows.append(Choice(r, r, "", on=True))
            continue
        if out == "next" and not m.selected():
            ui.show(render.line(ui.term, "warn", "Tick at least one root."))
            continue
        if out == "next":
            w.roots = m.selected()
        return out


def screen_projects(ui, w: Wizard):
    ui.show(render.step_title(ui.term, 3, TOTAL, "Projects",
                              "Folders under your roots with recent AI activity. Ticked ones get a project file."))
    scan_key = (w.lb, tuple(w.roots))
    if w.scanned != scan_key:
        try:
            w.projects = [c for c in actions.project_choices(w.lb, w.home, roots=w.roots) if c.key not in w.ignore]
            w.scanned = scan_key
        except (config.ConfigError, OSError) as e:
            ui.show(render.line(ui.term, "bad", f"couldn't read your AI activity: {e}"))
            return "quit"
    if not w.projects:
        ui.show(render.line(ui.term, "warn", "No folders with recent AI activity under these roots. "
                                             "Add projects later: waypoint add <folder>"))
    sep = ui.term.glyphs["sep"]
    while True:
        m = ListModel("Projects to track", w.projects, actions=("i",),
                      hint=f"Space toggle {sep} a all {sep} i ignore folder {sep} / search {sep} Enter next {sep} "
                           f"{'←' if ui.term.unicode else 'left'} back {sep} Esc quit")
        out = ui.choose(m)
        if out == "i":
            c = m.current()
            if c is not None and not c.locked:
                w.ignore.append(c.key)
                w.projects.remove(c)
            continue
        return out


def screen_tools(ui, w: Wizard):
    ui.show(render.step_title(ui.term, 4, TOTAL, "Connect AI tools",
                              "Each one gets Waypoint's rule (how to log) and the local MCP server."))
    rows = actions.tool_choices()
    if w.tools:
        for c in rows:
            c.on = c.key in w.tools and not c.locked
    m = ListModel("AI tools", rows)
    out = ui.choose(m)
    if out == "next":
        w.tools = m.selected()
    return out


def where_screen(ui, cli_id: str, lb: Path, home: Path, pool: list):
    """Global or Per project for one tool, with the rule method and MCP name inline: (outcome, None or [repos])."""
    name = clis.CLIS[cli_id].name
    while True:
        m = ListModel(f"{name}: where?", [
            Choice("global", "Global (every project)", actions.where_detail(cli_id, lb, home), on=True),
            Choice("project", "Per project", "only the repos you pick; nothing in your home config",
                   locked=not pool)], multi=False)
        out = ui.choose(m)
        if out != "next":
            return out, None
        if m.selected() == ["global"]:
            return "next", None
        repos = ListModel(f"{name}: which repos?",
                          [Choice(k, label, actions.where_detail(cli_id, lb, home, Path(k)), on=True) for k, label in pool])
        out = ui.choose(repos)
        if out == "next" and repos.selected():
            return "next", [Path(k) for k in repos.selected()]
        if out == "quit":
            return out, None


def screen_where(ui, w: Wizard):
    if not w.tools:
        return "skip"
    ui.show(render.step_title(ui.term, 5, TOTAL, "Where", "Global wires every project at once; Per project only "
                                                          "the repos you pick."))
    i = 0
    while i < len(w.tools):
        out, repos = where_screen(ui, w.tools[i], w.lb, w.home, w.repo_pool())
        if out == "quit":
            return out
        if out == "back":
            if i == 0:
                return "back"
            i -= 1
            continue
        w.where[w.tools[i]] = repos
        i += 1
    if "codex" in w.tools:
        m = ListModel("Codex's sandbox can't write outside the repo. Let it write the logbook?", [
            Choice("yes", "Yes: add the logbook to Codex's writable_roots (shown in Review)", on=w.codex_writable),
            Choice("no", "No: leave Codex's sandbox alone", on=not w.codex_writable)], multi=False)
        out = ui.choose(m)
        if out != "next":
            return out
        w.codex_writable = m.selected() == ["yes"]
    return "next"


def screen_review(ui, w: Wizard):
    ui.show(render.step_title(ui.term, 6, TOTAL, "Review", "Every change, grouped by step. Enter shows a step's changes."))
    w.plan = actions.wizard_plan(w.setup(), w.home)
    env_lb = os.environ.get("WAYPOINT_LOGBOOK")
    if env_lb and Path(env_lb).expanduser().resolve() != w.lb.resolve():
        ui.show(render.line(ui.term, "warn", f"WAYPOINT_LOGBOOK points to {Path(env_lb).expanduser().resolve()}; AI tools will open that logbook until you unset it"))
    out = ui.review(w.plan)
    return "next" if out == "apply" else out


def screen_apply(ui, w: Wizard):
    ui.show(render.step_title(ui.term, 7, TOTAL, "Apply"))
    w.results = []
    for st, err in actions.apply_steps(w.plan):
        w.results.append((st, err))
        ui.show(render.result_line(ui.term, st, err))
    return "next"


def screen_done(ui, w: Wizard):
    ui.show(render.step_title(ui.term, 8, TOTAL, "Done"))
    failed = sum(1 for _, err in w.results if err)
    broken = {st.tool for st, err in w.results if st.tool and err}
    ui.show(render.done(ui.term, len([t for t in w.tools if t not in broken]), len(w.tools), w.lb, failed))
    if not (w.lb / "projects").is_dir():
        return "next"
    try:
        kpi = actions.refresh(w.lb, w.home)
    except (config.ConfigError, OSError) as e:
        ui.show(render.line(ui.term, "bad", f"couldn't refresh the dashboard: {e}"))
        return "next"
    ui.show(render.line(ui.term, "ok", f"Dashboard refreshed: {kpi['tracked']} tracked, {kpi['untracked']} untracked"))
    page = (w.lb / "chart.html").resolve()
    m = ListModel(f"Open the dashboard ({page.name}) in your browser?",
                  [Choice("open", "Open it", on=True), Choice("no", "Not now")], multi=False)
    if ui.choose(m) == "next" and m.selected() == ["open"] and not webbrowser.open(page.as_uri()):
        ui.show(render.line(ui.term, "warn", f"Couldn't open a browser; open {page} yourself."))
    return "next"


SCREENS = (screen_logbook, screen_roots, screen_projects, screen_tools, screen_where, screen_review, screen_apply,
           screen_done)


def run(ui, home: Path) -> int:
    """The whole wizard. Exit code 0 when everything applied, 1 when the user quit or a step failed."""
    ui.show(render.header(ui.term, current_status(home)))
    w, i, direction = Wizard(home=home), 0, 1
    while i < len(SCREENS):
        out = SCREENS[i](ui, w)
        if out == "quit":
            ui.show(render.line(ui.term, "warn", "Quit; nothing was written."))
            return 1
        if out == "skip":
            out = "next" if direction > 0 else "back"
        direction = 1 if out == "next" else -1
        i = max(0, i + direction)
    return 0 if all(err is None for _, err in w.results) else 1


def current_status(home: Path):
    """status.collect() for the remembered logbook, or None when there's none yet (the panel says so)."""
    lb, source = config.find_logbook(None)
    lb = lb.resolve()
    if not (lb / "projects").is_dir():
        return None
    try:
        return status.collect(lb, source, home)
    except (config.ConfigError, OSError):
        return None
