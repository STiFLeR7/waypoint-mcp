"""waypoint: take a fix across every project and every AI tool.

  waypoint setup                   guided: logbook, roots, and the rule for the AI CLIs it finds
  waypoint add <folder>            chart a voyage: create the project's file
  waypoint fix                     take a fix: harvest the facts, redraw the chart (DASHBOARD.md, dashboard.json, chart.html)
  waypoint connect <cli>           wire one AI CLI: its rule and the local MCP server (or print its config)
  waypoint rules [--install|--uninstall]   show, check, install or remove the global rule
  waypoint init <dir>              scaffold a logbook (setup does this for you)
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import webbrowser
from importlib import resources
from pathlib import Path

from . import __version__, chart, clis, config, logbook, rules
from .paths import real

ROOT_HINTS = ("code", "src", "projects", "dev", "repos", "workspace", "Documents/GitHub", "source/repos")
GIT_TIMEOUT = 60


# ---- small helpers ----

def _n(count, one, many=None):
    return f"{count} {one if count == 1 else (many or one + 's')}"


def ask(prompt, default=""):
    try:
        answer = input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
    except EOFError:
        sys.exit("waypoint: no answer (not a terminal?). Re-run with --yes and the flags it needs.")
    return answer or default


def confirm(prompt, yes=False):
    if yes:
        return True
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _home(a):
    return Path(a.home).expanduser().resolve() if getattr(a, "home", None) else Path.home()


def _logbook(arg):
    lb, source = config.find_logbook(arg)
    lb = lb.resolve()
    if not (lb / "projects").is_dir():
        sys.exit(f"waypoint: {lb} (from {source}) is not a logbook: it has no projects/ folder.\n"
                 "Run `waypoint setup` to create one, or pass --logbook <dir>.")
    return lb


def _broad(root: str):
    """A root that would scan far too much: the home folder or a drive/filesystem root."""
    r = real(root)
    return r == real(str(Path.home())) or bool(re.fullmatch(r"[a-z]:\\?|/", r))


# ---- init ----

def init_logbook(lb: Path, roots=None, refresh_rules=False, dry_run=False):
    """Create what's missing in a logbook; returns (created, kept, notes). Existing files are never overwritten,
    except GLOBAL-RULES.md with refresh_rules. dry_run: report what would be created, write nothing."""
    if not dry_run:
        (lb / "projects" / "_archive").mkdir(parents=True, exist_ok=True)
    tpl = resources.files("waypoint").joinpath("templates/logbook")
    targets = {
        "AGENTS.md": lb / "AGENTS.md", "CLAUDE.md": lb / "CLAUDE.md", "README.md": lb / "README.md",
        "waypoint.toml": lb / "waypoint.toml",
        "_template.md": lb / "projects" / "_template.md", "_ignore.txt": lb / "projects" / "_ignore.txt",
    }
    roots = roots or [str(lb.parent)]
    created, kept, notes = [], [], []
    for name, dest in targets.items():
        if dest.exists():
            kept.append(dest)
            continue
        text = tpl.joinpath(name).read_text(encoding="utf-8")
        if name == "waypoint.toml":
            text = text.replace("{roots}", ", ".join(json.dumps(r) for r in roots))  # JSON string == TOML basic string
            notes += [f"root {r} is very broad: every folder under it with AI activity will show as untracked. "
                      "Edit roots in waypoint.toml to narrow it." for r in roots if _broad(r)]
        if not dry_run:
            dest.write_text(text, encoding="utf-8", newline="\n")
        created.append(dest)
    rules_file = lb / "GLOBAL-RULES.md"
    rendered = rules.render(lb)
    if not rules_file.exists() or refresh_rules:
        if not dry_run:
            rules_file.write_text(rendered, encoding="utf-8", newline="\n")
        created.append(rules_file)
    elif rules_file.read_text(encoding="utf-8") != rendered:
        notes.append(f"{rules_file} differs from this Waypoint version's rule; kept. "
                     "Re-run `waypoint init` with --refresh-rules to replace it.")
    return created, kept, notes


def _print_init(created, kept, notes):
    for f in created:
        print(f"  + {f}")
    for f in kept:
        print(f"  = {f} (kept)")
    for n in notes:
        print(f"  ! {n}")


def cmd_init(a):
    lb = Path(a.dir).expanduser().resolve()
    _print_init(*init_logbook(lb, refresh_rules=a.refresh_rules))
    print(f"Logbook ready at {lb}. Next: `waypoint add <folder>` per project, then `waypoint fix --logbook {lb}`.")


# ---- add ----

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_DEVICES = {"con", "prn", "aux", "nul", "conin$", "conout$",
                *(f"{d}{n}" for d in ("com", "lpt") for n in "123456789\u00b9\u00b2\u00b3")}


def name_problem(name):
    """Why `name` can't be a project file name, or None (spec §1: files starting with _ aren't projects)."""
    if not name or len(name) > 100:
        return "must be 1–100 characters"
    if name[0] in "_.":
        return "can't start with _ or . (the logbook reserves those; such files aren't projects)"
    if _BAD_CHARS.search(name) or name[-1] in " .":
        return 'can\'t contain < > : " / \\ | ? * or control characters, or end with a space or dot'
    if name.split(".")[0].rstrip(" ").lower() in _WIN_DEVICES:  # Windows ignores the extension and trailing spaces
        return "is a reserved device name on Windows"
    return None


def add_project(lb: Path, folder: Path, name=None):
    """Create projects/<name>.md for `folder`; returns (file, warnings). Raises ValueError with the reason when it
    refuses. Never overwrites, never writes a log entry."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    name = name or folder.name
    problem = name_problem(name)
    if problem:
        raise ValueError(f"the name {name!r} {problem}. Choose another with --name")
    key = real(str(folder))
    roots = config.load(lb).roots  # before any write: a broken waypoint.toml must not leave a half-added project
    if key in logbook.load_ignore(lb):
        raise ValueError(f"{folder} is listed in projects/_ignore.txt. Remove it there first to track it")
    for p in logbook.load_projects(lb):
        if p["path"] and real(p["path"]) == key:
            raise ValueError(f"{folder} is already tracked by projects/{p['file'].split('/', 1)[-1]}")
    taken = {f.stem.lower() for f in (lb / "projects").rglob("*") if f.is_file() and f.suffix.lower() == ".md"}
    if name.lower() in taken:
        raise ValueError(f"projects/{name}.md (or an archived file with that name) already exists. Choose another with --name")
    tpl = lb / "projects" / "_template.md"
    text = tpl.read_text(encoding="utf-8") if tpl.exists() else \
        resources.files("waypoint").joinpath("templates/logbook/_template.md").read_text(encoding="utf-8")
    text = _new_project_text(text, name, folder)
    warnings = []
    if not any(key == real(r) or key.startswith(real(r).rstrip("\\/") + ("\\" if "\\" in real(r) else "/")) for r in roots):
        warnings.append(f"{folder} is outside the logbook's roots ({', '.join(roots)}). Its sessions still count, "
                        "but folders around it won't show as untracked.")
    target = lb / "projects" / f"{name}.md"
    with open(target, "x", encoding="utf-8", newline="\n") as fh:  # "x": never overwrite, even in a race
        fh.write(text)
    return target, warnings


def _new_project_text(template, name, folder):
    """The template filled for a new project, whatever the template says: title, resolved Path, Status active,
    blank placeholders, and an empty Log (spec §1: add never writes a log entry)."""
    log = re.search(r"^## Log[ \t]*$", template, re.M | re.I)
    head = template[: log.start()] if log else template  # everything from ## Log on is dropped, then re-added empty
    text = re.sub(r"^# .*$", lambda _: f"# {name}", head, count=1, flags=re.M)
    if not re.search(r"^# ", text, re.M):
        text = f"# {name}\n" + text
    for fld, value in (("Status", "active"), ("Path", str(folder))):  # missing ones go under the title: Path first
        if re.search(rf"^- {fld}:.*$", text, re.M):
            text = re.sub(rf"^- {fld}:.*$", lambda _: f"- {fld}: {value}", text, count=1, flags=re.M)
        else:
            text = re.sub(r"^(# .*)$", lambda m: f"{m[1]}\n- {fld}: {value}", text, count=1, flags=re.M)
    text = re.sub(r"^- Summary:.*$", "- Summary:", text, count=1, flags=re.M)
    text = re.sub(r"^(- [A-Za-z]+:)[ \t]*<[^>\n]*>[ \t]*$", r"\1", text, flags=re.M)  # blank the placeholders
    return text.rstrip("\n") + "\n\n## Log\n"


def cmd_add(a):
    lb = _logbook(a.logbook)
    try:
        target, warnings = add_project(lb, Path(a.folder), a.name)
    except ValueError as e:
        sys.exit(f"waypoint: not added: {e}.")
    print(f"Charted a new voyage (project): {target}")
    for w in warnings:
        print(f"  ! {w}")
    print("Fill in its Summary and Next in that file (or let your AI tool do it), then take a fix: waypoint fix")


# ---- fix ----

def cmd_fix(a):
    lb = _logbook(a.logbook)
    config.load(lb)  # validate before --push can pull or change the logbook
    if a.push:
        if _git(lb, "rev-parse", "--git-dir").returncode != 0:
            sys.exit("waypoint: --push needs the logbook to be a git repo.")
        _ok(_git(lb, "pull", "--rebase", "--autostash"), "git pull")
    d = chart.build(lb, _home(a))
    chart.write(lb, d)
    k = d["kpi"]
    if not k["tracked"]:
        verb = "is" if k["untracked"] == 1 else "are"
        print(f"No voyages (projects) charted yet; {_n(k['untracked'], 'folder')} with recent AI activity {verb} uncharted"
              " (untracked). Chart one: waypoint add <folder>")
    else:
        voyages = _n(k["tracked"], "voyage") + (" (project)" if k["tracked"] == 1 else " (projects)")
        hazards = _n(k["attention"], "hazard") + (" (attention item)" if k["attention"] == 1 else " (attention items)")
        print(f"Fix taken (sync): {voyages}, {k['velocity']} underway (active) this week, {hazards}, "
              f"{k['unlogged']} with unlogged watches (sessions), {k['stale']} adrift (stale), "
              f"{k['untracked']} uncharted (untracked folders).")
    page = (lb / "chart.html").resolve()
    print(f"Chart (dashboard) for your browser: {page}")
    if a.open and not webbrowser.open(page.as_uri()):
        print("Couldn't open a browser; open the file above yourself.")
    if a.push:
        # only the logbook's own data: project files and the chart, never unrelated files in the folder
        paths = ("projects", "DASHBOARD.md", "dashboard.json", "chart.html")
        ignored = _git(lb, "check-ignore", "--", *paths)  # 0 = some ignored, 1 = none, else error
        if ignored.returncode not in (0, 1):
            _ok(ignored, "git check-ignore")
        paths = tuple(p for p in paths if p not in ignored.stdout.split())  # e.g. a user who keeps chart.html local
        _ok(_git(lb, "add", "--", *paths), "git add")
        diff = _git(lb, "diff", "--cached", "--quiet", "--", *paths)  # 0 = no changes, 1 = changes, else error
        if diff.returncode == 0:
            print("Nothing to push.")
            return
        if diff.returncode != 1:
            _ok(diff, "git diff")
        # pathspec commit: anything else you had staged stays staged and is not included
        _ok(_git(lb, "commit", "-m", "chore: take a fix", "--", *paths), "git commit")
        _ok(_git(lb, "push"), "git push")
        print("Pushed.")


def _git(lb, *args):
    command = ["git", "-C", str(lb), *args]
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"git command timed out after {GIT_TIMEOUT} seconds")


def _ok(r, what):
    if r.returncode != 0:
        sys.exit(f"waypoint: {what} failed:\n{(r.stderr or r.stdout).strip()}")


# ---- rules ----

STATE_HELP = {
    "in-sync": "follows the current rule",
    "drifted": "has an older or edited copy. Run: waypoint rules --install",
    "missing": "has no Waypoint rule. Run: waypoint rules --install",
    "shadowed": "has the rule in AGENTS.md, but an AGENTS.override.md hides it. Run: waypoint rules --install --codex-override",
}


def show_plan(changes):
    """Print every target and its exact change; returns the changes that would write."""
    for c in changes:
        print(f"  {c.action:<9} {c.target}" + (f"  ({c.note})" if c.note else ""))
    writes = [c for c in changes if c.action in rules.WRITES]
    for c in writes:
        print()
        print(c.diff().rstrip() or f"  (removes {c.target})")
    return writes


def run_plan(changes, yes, verb="Apply these changes?"):
    """Show the plan, confirm, apply. Any refusal stops everything before a single write; returns success."""
    writes = show_plan(changes)
    refused = [c for c in changes if c.action == "refuse"]
    if refused:
        print(f"\n{_n(len(refused), 'change')} can't be made safely (see above); nothing was written.")
        return False
    if not writes:
        print("Nothing to change.")
        return True
    print("\nExisting files are backed up next to themselves (.bak-<time>) first.")
    if not confirm(verb, yes):
        print("Cancelled; nothing written.")
        return False
    return _report(rules.apply(writes))


def _report(results):
    failed = False
    for c, err in results:
        print(f"  {'✓' if not err else '✗'} {c.action} {c.target}" + (f": {err}" if err else ""))
        failed = failed or bool(err)
    if failed:
        print("Some changes didn't happen; the others did. Fix the cause and run the command again.")
    return not failed


def cmd_rules(a):
    lb = _logbook(a.logbook)
    home = _home(a)
    if a.uninstall:
        sys.exit(0 if run_plan(rules.plan_uninstall(home), a.yes, "Remove Waypoint's rule from these files?") else 1)
    tools = [*rules.TOOLS, *(t for t in clis.detected() if t not in rules.TOOLS)]
    if a.install:
        ok = run_plan(rules.plan_install(home, lb, codex_override=a.codex_override, tools=tools), a.yes)
        sys.exit(0 if ok else 1)
    print(rules.render(lb))
    for target, state in rules.check(home, lb, tools).items():
        print(f"{target}: {state}" + ("" if target == "logbook" else f" ({STATE_HELP.get(state, '')})"))


# ---- connect ----

def cmd_connect(a):
    c = clis.CLIS[a.cli]
    if c.tier == "snippet":
        lb, _ = config.find_logbook(a.logbook)
        print(clis.snippet_text(c, lb.resolve()))
        return
    lb = _logbook(a.logbook)
    served, source = config.find_logbook(None)  # what `waypoint mcp` (no flags) will open
    if not a.no_mcp and rules._real_home(_home(a)) and served.resolve() != lb:
        sys.exit(f"waypoint: the MCP server would open {served.resolve()} (from {source}), not {lb}. "
                 f"Make {lb} the default first (waypoint setup --logbook {lb}), or pass --no-mcp.")
    print(f"Wiring {c.name} ({c.tier}): the rule" + ("" if a.no_mcp else " and the local MCP server (waypoint mcp)") + ".\n")
    plan = rules.plan_install(_home(a), lb, tools=(c.id,), mcp=not a.no_mcp)
    ok = run_plan(plan, a.yes)
    if ok and not a.no_mcp:
        print(f"\nIn a new {c.name} session, its tools take_fix, read_chart and log_watch (tool id {c.id}) are aboard.")
    sys.exit(0 if ok else 1)


# ---- setup ----

def cmd_setup(a):
    home = _home(a)
    if a.yes and not (a.logbook and a.root):
        sys.exit("waypoint: setup --yes needs --logbook <dir> and at least one --root <dir>.")
    print("Fitting out Waypoint: one logbook (data folder) that every AI tool logs to.\n")
    saved, source = config.find_logbook(None)
    if a.logbook:
        lb = Path(a.logbook).expanduser().resolve()
    else:
        default = saved if source != "the current folder" else Path.home() / "waypoint-logbook"
        lb = Path(ask("Where should the logbook (data folder) live?", str(default))).expanduser().resolve()

    roots = [str(Path(r).expanduser().resolve()) for r in a.root]
    if (lb / "waypoint.toml").exists():
        current = config.load(lb).roots
        if roots and [real(r) for r in roots] != [real(r) for r in current]:
            print(f"  ! waypoint.toml already sets roots = {current}; kept. Edit it to change them.")
        roots = current
    elif not roots:
        found = [str(Path.home() / h) for h in ROOT_HINTS if (Path.home() / h).is_dir()]
        print("Roots are the folders your projects live under; Waypoint looks for AI activity there.")
        answer = ask("Roots (comma-separated)", ", ".join(found) or str(lb.parent))
        roots = [str(Path(r.strip()).expanduser().resolve()) for r in answer.split(",") if r.strip()]
    for r in [r for r in roots if _broad(r)]:  # every effective root, however it was given
        if a.yes and not a.allow_broad_root:
            sys.exit(f"waypoint: root {r} is very broad (every folder under it with AI activity shows up). "
                     "Pass --allow-broad-root to use it anyway.")
        if not a.yes and not confirm(f"{r} is very broad (every folder under it with AI activity shows up). Use it anyway?"):
            sys.exit("waypoint: setup stopped; nothing was written. Run it again with narrower roots.")

    tools = [c.id for c in clis.WIRED if c.id in a.tool] if a.tool else clis.detected()
    make_git = not (lb / ".git").exists() and (a.git or (not a.yes and confirm("Make the logbook a git repo (recommended)?")))

    # the whole plan, before any write
    created, kept, notes = init_logbook(lb, roots=roots, dry_run=True)
    real_home = rules._real_home(home)
    remember = config.remember_plan(lb) if real_home else None  # --home: leave the real user config alone
    plan = rules.plan_install(home, lb, tools=tools, mcp=not a.no_mcp) if tools else []
    print(f"\nLogbook: {lb}")
    _print_init(created, kept, notes)
    if make_git:
        print("  + git repo")
    if real_home:
        print(f"  {'=' if remember is None else '+'} logbook remembered in {config.user_dir() / 'logbook'}"
              + (" (already)" if remember is None else ""))
    else:
        print("  = logbook not remembered (--home isn't your home folder)")
    found = ', '.join(tools) or 'none found on PATH (' + ', '.join(c.exe for c in clis.WIRED) + ')'
    print(f"\nRule{'' if a.no_mcp else ' and MCP server'} for AI tools: {found}.")
    writes = show_plan(plan)
    if any(c.action == "refuse" for c in plan):
        sys.exit("\nwaypoint: some changes can't be made safely (see above); nothing was written.")
    if not (created or make_git or remember is not None or writes):
        print("\nAlready fitted out. Nothing to change.")
    elif not confirm("\nWrite these changes?", a.yes):
        sys.exit("Cancelled; nothing written.")
    else:
        init_logbook(lb, roots=roots)
        if make_git:
            r = _git(lb, "init", "-q")
            if r.returncode != 0:
                print(f"  ! git init failed: {(r.stderr or r.stdout).strip()}")
        if remember is not None:
            config.remember_logbook(lb)
        if writes and not _report(rules.apply(writes)):
            sys.exit(1)
    if "codex" in tools:
        print(f"\nCodex runs in a sandbox. To let it write the logbook, add this to {rules.codex_dir(home) / 'config.toml'}"
              f" yourself (Waypoint never widens a tool's permissions):\n  [sandbox_workspace_write]\n"
              f"  writable_roots = [{json.dumps(str(lb))}]")
    print("\nReady to sail. Next:\n  waypoint add <project folder>    chart a voyage (project)\n"
          "  waypoint fix                     take a fix: the chart (dashboard)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="waypoint", description="Every voyage (project), every navigator (AI tool), one chart.")
    p.add_argument("--version", action="version", version=f"waypoint {__version__} (logbook spec v1.1)")
    sub = p.add_subparsers(dest="cmd", required=True)
    lb_help = "logbook folder (default: WAYPOINT_LOGBOOK, then the one `setup` remembered, then the current folder)"

    s = sub.add_parser("setup", help="guided setup: logbook, roots, rules for your AI tools")
    s.add_argument("--logbook", help="where the logbook lives (created if missing)")
    s.add_argument("--root", action="append", default=[], help="a folder your projects live under (repeatable)")
    s.add_argument("--tool", action="append", choices=[c.id for c in clis.WIRED],
                   help="wire this AI CLI (repeatable; default: those on PATH)")
    s.add_argument("--no-mcp", action="store_true", help="install the rule only; don't register the local MCP server")
    s.add_argument("--git", action="store_true", help="make the logbook a git repo (asked when interactive)")
    s.add_argument("--allow-broad-root", action="store_true", help="accept a home folder or drive root as a root")
    s.add_argument("--yes", action="store_true", help="don't ask; needs --logbook and --root")
    s.add_argument("--home", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_setup)

    ad = sub.add_parser("add", help="chart a voyage: create a project file for a folder")
    ad.add_argument("folder", help="the project's folder")
    ad.add_argument("--name", help="project name (default: the folder's name)")
    ad.add_argument("--logbook", help=lb_help)
    ad.set_defaults(func=cmd_add)

    f = sub.add_parser("fix", help="take a fix: harvest the facts, redraw the chart")
    f.add_argument("--logbook", help=lb_help)
    f.add_argument("--push", action="store_true", help="git pull first, then commit and push the logbook")
    f.add_argument("--open", action="store_true", help="open chart.html in your browser")
    f.add_argument("--home", help=argparse.SUPPRESS)
    f.set_defaults(func=cmd_fix)

    c = sub.add_parser("connect", help="wire one AI CLI (rule + local MCP server), or print config for the others")
    c.add_argument("cli", choices=list(clis.CLIS), help="the AI CLI, by its tool id")
    c.add_argument("--logbook", help=lb_help)
    c.add_argument("--no-mcp", action="store_true", help="install the rule only")
    c.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    c.add_argument("--home", help=argparse.SUPPRESS)
    c.set_defaults(func=cmd_connect)

    r = sub.add_parser("rules", help="show, check, install or remove the global rule")
    r.add_argument("--logbook", help=lb_help)
    g = r.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="install or update the rule (shows each change, then asks)")
    g.add_argument("--uninstall", action="store_true", help="remove only what Waypoint added (backups kept)")
    r.add_argument("--codex-override", action="store_true", help="also put the rule in Codex's AGENTS.override.md")
    r.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    r.add_argument("--home", help=argparse.SUPPRESS)
    r.set_defaults(func=cmd_rules)

    m = sub.add_parser("mcp", help="run the local MCP server (stdio) for AI tools", add_help=False)
    m.set_defaults(func=None)

    i = sub.add_parser("init", help="scaffold a logbook (setup does this for you)")
    i.add_argument("dir", help="folder for the new logbook")
    i.add_argument("--refresh-rules", action="store_true", help="replace an existing GLOBAL-RULES.md")
    i.set_defaults(func=cmd_init)

    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["mcp"]:  # its own parser: the server owns stdout, so no extra output here
        from . import mcp
        return mcp.main(argv[1:])
    a = p.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles: keep ✓ and — printable
    try:
        a.func(a)
    except config.ConfigError as e:
        sys.exit(f"waypoint: {e}")


if __name__ == "__main__":
    main()
