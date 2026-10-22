"""What the screens show and do, stdlib only: choices built from engine data, and plans of Steps built only from
engine functions (cli, manage, project, rules, chart, config). No terminal here, so all of it is unit-testable.

A Step is one line on the Review and Apply screens: either planned file changes (rules.Change, applied with
rules.apply, exactly as `waypoint connect` does) or one engine call (`run`) with the lines that describe it."""
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import chart, cli, clis, config, logbook, manage, project, rules
from .model import Choice

ACTIVE_DAYS = 21  # pre-tick projects with AI activity in the last 21 days (the stale limit for active projects)


def _age(ts, now) -> str:
    if not ts:
        return "no activity"
    days = max(0, int((now - ts) // 86400))
    return "today" if days == 0 else ("1 day ago" if days == 1 else f"{days} days ago")


# ---- choices ----

def project_choices(lb: Path, home: Path, roots=None, now=None) -> list:
    """Tracked projects (ticked, locked) and untracked folders with AI activity under the roots, newest first.
    Untracked folders active in the last ACTIVE_DAYS are pre-ticked."""
    now = time.time() if now is None else now
    d = chart.build(lb, home, now=now, roots=roots)
    rows = [(p["last"], Choice(p["path"], p["name"], f"{p['path']} · {p['last_tool'] or 'no tool yet'} · "
                                                     f"{_age(p['last'], now)} · tracked", on=True, locked=True))
            for p in d["projects"] if p["path"]]
    for u in d["untracked"]:
        folder = Path(u["path"]).resolve()  # chart keys are case-folded on Windows; show the real name
        rows.append((u["last"], Choice(str(folder), folder.name, f"{folder} · {_age(u['last'], now)}",
                                       on=now - u["last"] <= ACTIVE_DAYS * 86400)))
    rows.sort(key=lambda r: -r[0])
    return [c for _, c in rows]


def tool_choices() -> list:
    """Wired CLIs (detected ones ticked; others dimmed), then the config-snippet tools (dimmed, print config)."""
    found = set(clis.detected())
    out = [Choice(c.id, c.name, c.tier.title() + ("" if c.id in found else " · not on PATH"),
                  on=c.id in found, locked=c.id not in found) for c in clis.WIRED]
    out += [Choice(c.id, c.name, f"print config: waypoint connect {c.id}", locked=True)
            for c in clis.CLIS.values() if c.tier == "snippet"]
    return out


def rule_method(cli_id: str, lb: Path, repo: Path | None = None) -> str:
    """How the rule would be installed: import | symlink | copy (spec Part B)."""
    if cli_id == "claude-code":
        return "import"
    if repo is None:
        return "copy"
    ch = project.plan_rule(cli_id, repo, lb)
    if ch.action == "refuse":
        return f"blocked: {ch.note or ch.target}"
    return "symlink" if ch.action == "link" or (ch.action == "unchanged" and ch.target.is_symlink()) else "copy"


def mcp_name(cli_id: str, home: Path, repo: Path | None = None) -> str:
    """The MCP name Waypoint would use: `waypoint`, or `waypoint-local` when another server holds `waypoint`."""
    f, keyed = project.mcp_location(cli_id, repo, home) if repo else (clis.mcp_file(cli_id, home), False)
    entries, err = clis.mcp_entries(f, repo if keyed else None)
    if err:
        return "unknown (its config can't be read)"
    ours, free = clis.choose(entries)
    if ours:
        return f"`{ours}` (already registered)"
    if free == "waypoint-local":
        return "`waypoint-local` (`waypoint` is another server)"
    return "`waypoint`" if free else "none free (both names are other servers)"


def where_detail(cli_id: str, lb: Path, home: Path, repo: Path | None = None) -> str:
    return f"rule: {rule_method(cli_id, lb, repo)} · MCP: {mcp_name(cli_id, home, repo)}"


TABS = ("tracked", "archived", "untracked")


def manager_rows(lb: Path, home: Path, tab: str) -> list:
    """The manager's Projects tabs: tracked or archived project files (key = name), or untracked folders (key = path)."""
    if tab == "untracked":
        rows = [c for c in project_choices(lb, home) if not c.locked]
        for c in rows:
            c.on = False
        return rows
    return [Choice(p["name"], p["name"], p["path"] or "no Path line") for p in logbook.load_projects(lb)
            if p["archived"] == (tab == "archived")]


def tool_rows(s: dict) -> list:
    """The manager's AI tools list, from status.collect()."""
    out = []
    for c in clis.WIRED:
        t = s["tools"].get(c.id)
        if t is None:
            detail = f"{c.tier.title()} · not on PATH, not connected"
        else:
            n = len(t["projects"])
            scope = ", ".join(t["scope"]) or "not connected"
            detail = (f"{c.tier.title()} · rule {t['rule']} · MCP {t['mcp']['state']} · {scope}"
                      + (f" ({n} repo{'s' if n != 1 else ''})" if n else ""))
        out.append(Choice(c.id, c.name, detail))
    return out


# ---- plans ----

@dataclass
class Step:
    title: str
    changes: list = field(default_factory=list)  # rules.Change
    run: Callable | None = None  # an engine call; raises ValueError/OSError when it can't be done
    lines: list = field(default_factory=list)  # what `run` will do, for Review
    why: str | None = None  # known before Apply: this step can't be done safely, so it is skipped
    tool: str | None = None  # the AI tool a connect step wires (the Done screen counts them)
    needs_logbook: bool = False

    @property
    def problem(self) -> str | None:
        refused = [c.note or str(c.target) for c in self.changes if c.action == "refuse"]
        return self.why or ("; ".join(refused) if refused else None)

    def writes(self) -> bool:
        return self.run is not None or any(c.action in rules.WRITES for c in self.changes)


def change_line(c, g: dict) -> str:
    """One planned change as a Review line: `+ create`, `~ update`, `→ link`, `runs: …`."""
    if c.run:
        return f"runs: {' '.join(c.run)}" + (f"  ({c.note})" if c.note else "")
    if c.action == "link":
        return (f"{g['link']} unlink {c.target}" if c.new is None else f"{g['link']} link {c.target} {g['link']} {c.new}")
    if c.action == "refuse":
        return f"{g['bad']} can't: {c.target}" + (f"  ({c.note})" if c.note else "")
    sign = {"create": "+", "update": "~", "remove": "-"}.get(c.action, "=")
    return f"{sign} {c.action} {c.target}" + (f"  ({c.note})" if c.note else "")


def step_lines(step: Step, g: dict) -> list:
    return list(step.lines) + [change_line(c, g) for c in step.changes]


def diff_lines(step: Step) -> list:
    return [line for c in step.changes if c.action in rules.WRITES and not c.run for line in c.diff().splitlines()]


@dataclass
class Setup:
    """Everything the wizard chose. Nothing is written until apply_steps runs the plan built from it."""
    lb: Path
    roots: list
    git: bool = False
    track: list = field(default_factory=list)  # folders to add
    ignore: list = field(default_factory=list)  # folders to list in _ignore.txt
    tools: dict = field(default_factory=dict)  # cli id -> None (global) or [repo folders] (per project)
    codex_writable: bool = True
    eod_email: str | None = None


def _git_init(lb: Path):
    r = cli._git(lb, "init", "-q")
    if r.returncode != 0:
        raise OSError(f"git init failed: {(r.stderr or r.stdout).strip()}")


def wizard_plan(s: Setup, home: Path) -> list:
    """The Review screen's steps, in apply order: logbook, git, remember, projects, ignores, AI tools."""
    lb, steps = s.lb, []
    created, _, notes = cli.init_logbook(lb, roots=s.roots, dry_run=True)
    if created:
        steps.append(Step(f"Create the logbook in {lb}", run=lambda: cli.init_logbook(lb, roots=s.roots),
                          lines=[f"+ create {f}" for f in created] + [f"! {n}" for n in notes]))
    if s.git and not (lb / ".git").exists():
        steps.append(Step("Make the logbook a git repo", run=lambda: _git_init(lb), lines=[f"runs: git -C {lb} init"], needs_logbook=True))
    if rules._real_home(home) and config.remember_plan(lb) is not None:
        steps.append(Step("Remember the logbook", run=lambda: config.remember_logbook(lb), needs_logbook=True,
                          lines=[f"+ {config.user_dir() / 'logbook'}"]))
    if s.eod_email and rules._real_home(home) and config.eod_email_plan(s.eod_email) is not None:
        steps.append(Step("Remember the end-of-day email address", run=lambda: config.remember_eod_email(s.eod_email),
                          lines=[f"+ {config.user_dir() / config.EOD_FILE} (this machine only)"], needs_logbook=False))
    for folder in s.track:
        st = plan_track(lb, Path(folder)); st.needs_logbook = True; steps.append(st)
    for folder in s.ignore:
        steps.append(Step(f"Ignore {Path(folder).name}", run=lambda f=folder: manage.ignore(lb, f), needs_logbook=True,
                          lines=[f"+ {folder} in projects/_ignore.txt (not a project)"]))
    grouped = {}
    for cli_id, repos in s.tools.items():
        planned = plan_connect(cli_id, lb, home, repos, writable_root=s.codex_writable)
        for i, st in enumerate(planned):
            st.needs_logbook = True
            if repos:
                grouped.setdefault(Path(repos[i]).resolve(), []).append(st)
            else:
                steps.append(st)
    for repo, group in grouped.items():
        if len(group) > 1:
            # One exclude change for the repo (each step's own would go stale after the first applies). It covers
            # only steps that will run and rides on the first of them; a refused exclude blocks every step, as before.
            is_exclude = lambda ch: ch.target.name == "exclude" and ch.target.parent.name == "info"
            refused = [ch for st in group for ch in st.changes if is_exclude(ch) and ch.action == "refuse"][:1]
            for st in group:
                st.changes = [ch for ch in st.changes if not is_exclude(ch)]
            live, added = [st for st in group if not st.problem], []
            for st in live:
                for ch in st.changes:
                    if ch.action in ("create", "link", "register") and rules.inside(ch.target, repo):
                        added.append(ch.target)
                    if ch.action in rules.WRITES and rules.inside(ch.target, repo):
                        added.append(ch.target.with_name(ch.target.name + ".bak-*"))
            exclude = refused or (project.plan_exclude(repo, add=added) if live else [])
            if any(ch.action == "refuse" for ch in exclude):
                for st in group:
                    st.changes += exclude
            elif live:
                live[0].changes += exclude
            steps.extend(group)
        else:
            steps.extend(group)
    return [st for st in steps if st.writes() or st.problem]


def plan_track(lb: Path, folder) -> Step:
    folder = Path(folder)
    name = folder.name
    problem = cli.name_problem(name)
    return Step(f"Track {name}", run=lambda: cli.add_project(lb, folder), lines=[f"+ create projects/{name}.md ({folder})"],
                why=f"the name {name!r} {problem}; add it with: waypoint add \"{folder}\" --name <name>" if problem else None)


def plan_connect(cli_id: str, lb: Path, home: Path, repos=None, writable_root=True) -> list:
    """`waypoint connect <cli>` (repos None) or `--project` per repo: one Step each."""
    c = clis.CLIS[cli_id]
    if not repos:
        return [Step(f"Connect {c.name} (global)", tool=cli_id,
                     changes=rules.plan_install(home, lb, tools=(cli_id,), mcp=True, writable_root=writable_root))]
    return [Step(f"Connect {c.name} in {Path(r).name}", tool=cli_id,
                 changes=project.plan_connect(cli_id, Path(r), lb, home, mcp=True, writable_root=writable_root))
            for r in repos]


def plan_disconnect(cli_id: str, home: Path, repos=None) -> list:
    c = clis.CLIS[cli_id]
    if not repos:
        return [Step(f"Disconnect {c.name} (global)", changes=rules.plan_uninstall(home, mcp=True, tools=(cli_id,)))]
    return [Step(f"Disconnect {c.name} in {Path(r).name}", changes=project.plan_disconnect(Path(r), home, tools=(cli_id,)))
            for r in repos]


def plan_fix_drift(lb: Path, home: Path, drifted) -> list:
    """`waypoint rules --install` for the drifted global rules, plus every drifted per-project copy."""
    changes = (rules.plan_install(home, lb, tools=tuple(drifted)) if drifted else []) + project.plan_refresh(lb)
    return [Step("Refresh drifted rules", changes=changes)]


def plan_archive(lb: Path, name: str) -> Step:
    p = manage.find(lb, name)
    return Step(f"Archive {p['name']}", run=lambda: manage.archive(lb, p["name"]),
                lines=[f"~ move {p['file']} to projects/_archive/ (its log is kept)"])


def plan_restore(lb: Path, name: str) -> Step:
    p = manage.find(lb, name, True)
    return Step(f"Restore {p['name']}", run=lambda: manage.restore(lb, p["name"]),
                lines=[f"~ move {p['file']} back to projects/"])


def plan_remove(lb: Path, name: str) -> Step:
    p = manage.find(lb, name)
    n = len(p["logs"])
    lines = [f"- delete {p['file']} ({n} log {'entry' if n == 1 else 'entries'}; a git logbook keeps its history)"]
    if n:
        lines.append("! It has a log. To keep it but hide it, go back and archive it instead (x)")
    return Step(f"Remove {p['name']}", run=lambda: manage.remove(lb, p["name"]), lines=lines)


def plan_ignore(lb: Path, folder) -> Step:
    folder = Path(folder).resolve()
    return Step(f"Ignore {folder.name}", run=lambda: manage.ignore(lb, folder),
                lines=[f"+ {folder} in projects/_ignore.txt (not a project)"], why=manage.ignore_problem(lb, folder))


def apply_steps(steps):
    """Yield (step, error or None) as each step finishes. A failure stops that step only; backups are kept."""
    logbook_created = False
    create_step = next((st for st in steps if st.run is not None and st.title.startswith("Create the logbook in ")), None)
    for st in steps:
        dependent = st.needs_logbook
        if dependent and create_step is not None and not logbook_created:
            yield st, "skipped: the logbook wasn't created"
            continue
        if st.problem:
            yield st, f"skipped: {st.problem}"
            continue
        try:
            if st.run is not None:
                st.run()
                if st is create_step:
                    logbook_created = True
                err = None
            else:
                errs = [f"{c.target}: {e}" for c, e in rules.apply([c for c in st.changes if c.action in rules.WRITES]) if e]
                err = "; ".join(errs) or None
        except Exception as e:
            err = str(e)
        if st is create_step and err is not None:
            logbook_created = False
        yield st, err


def refresh(lb: Path, home: Path) -> dict:
    """`waypoint refresh` without its printing: rebuild DASHBOARD.md, dashboard.json and chart.html; returns kpi."""
    d = chart.build(lb, home)
    chart.write(lb, d)
    return d["kpi"]
