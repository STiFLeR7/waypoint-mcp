"""Rich drawing (needs the tui extra): banner, the panel under it, step titles, apply lines, the done summary and the
status view. Pure functions from data to Rich renderables; the screens print them."""
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import __version__
from .term import PALETTE, TAGLINE, Term, title_rows


def console(term: Term, file=None) -> Console:
    return Console(file=file, width=term.width, no_color=not term.color, highlight=False, emoji=False,
                   force_terminal=None if file is None else False)


def _c(term: Term, key: str) -> str:
    return PALETTE[key] if term.color else ""


def banner(term: Term) -> Text:
    out = Text()
    rows = title_rows(term)
    for i, (row, colour) in enumerate(rows):
        out.append(row + ("\n" if i < len(rows) - 1 else ""), style=f"bold {_c(term, colour)}".strip())
    return out


def panel(term: Term, s: dict | None) -> Panel:
    """Version and tagline left; logbook, project counts and connected tools right. s: status.collect() or None."""
    sep = term.glyphs["sep"]
    left = Text(TAGLINE, style=_c(term, "dim"))
    right = Text()
    if s and s.get("logbook"):
        right.append(f"Logbook  {s['logbook']}\n")
        c = s.get("counts")
        if c:
            right.append(f"Projects {c['tracked']} tracked {sep} {c['archived']} archived {sep} {c['untracked']} untracked\n")
        on = [t["name"] for t in s["tools"].values() if t["scope"]]
        right.append(f"AI tools {', '.join(on) if on else 'none connected yet'}")
    else:
        right.append("No logbook yet: setup creates one")
    grid = Table.grid(padding=(0, 3), expand=True)
    grid.add_column(ratio=1, overflow="crop" if not term.unicode else "ellipsis")
    grid.add_column(ratio=1, overflow="crop" if not term.unicode else "ellipsis")
    grid.add_row(left, right)
    return Panel(grid, box=box.ROUNDED if term.unicode else box.ASCII, border_style=_c(term, "teal") or "none",
                 title=Text(f"Waypoint {__version__}", style=f"bold {_c(term, 'brass')}".strip()), title_align="left")


def header(term: Term, s: dict | None) -> Group:
    return Group(banner(term), panel(term, s))


def step_title(term: Term, n: int, total: int, title: str, explain: str = "") -> Text:
    t = Text(f"\nStep {n} of {total} {term.glyphs['sep']} {title}\n", style=f"bold {_c(term, 'brass')}".strip())
    if explain:
        t.append(explain, style=_c(term, "dim"))
    return t


def line(term: Term, kind: str, text: str) -> Text:
    """One status line: kind ok | warn | bad."""
    colour = {"ok": "ok", "warn": "brass", "bad": "bad"}[kind]
    return Text(f"{term.glyphs[kind]} {text}", style=_c(term, colour))


def result_line(term: Term, step, err) -> Text:
    if err is None:
        return line(term, "ok", step.title)
    return line(term, "warn" if err.startswith("skipped: ") else "bad", f"{step.title}: {err}")


def done(term: Term, connected: int, total: int, lb, failed: int) -> Group:
    head = line(term, "ok", "Setup complete") if not failed else line(term, "warn", f"Setup finished; {failed} step{'s' if failed != 1 else ''} didn't happen (see above)")
    help_table = Table.grid(padding=(0, 2))
    help_table.add_column(no_wrap=True)
    help_table.add_column()
    help_table.add_row(Text("Next", style=f"bold {_c(term, 'brass')}".strip()), "")
    help_table.add_row("AI tool", "Start a session in any connected tool; it logs to the logbook")
    help_table.add_row("waypoint refresh", "refresh the dashboard")
    help_table.add_row("waypoint", "this screen, to manage projects and tools")
    help_table.add_row(Text("Change later", style=f"bold {_c(term, 'brass')}".strip()), "")
    help_table.add_row("waypoint add|archive|remove|ignore", "<folder>")
    help_table.add_row("waypoint connect", "<tool> [--project <repo>] / disconnect <tool>")
    help_table.add_row("Logbook", str(lb))
    return Group(head, Text("No AI tools found on PATH." if total == 0 else f"{connected}/{total} AI tools connected"),
                 help_table)


def status_view(term: Term, s: dict) -> Group:
    """`waypoint status` as tables and ✓ ⚠ ✗ lines (the same data as status.render_text)."""
    parts = []
    tools = Table(box=box.SIMPLE if term.unicode else box.ASCII, header_style=f"bold {_c(term, 'brass')}".strip())
    for col in ("AI tool", "Tier", "Rule", "MCP", "Scope"):
        tools.add_column(col)
    for t in s["tools"].values():
        rule = t["rule"] + (f" ({t['method']})" if t.get("method") not in (None, "none") else "")
        scope = ", ".join(t["scope"]) or "not connected"
        if t["projects"]:
            scope += f" ({len(t['projects'])} repo{'s' if len(t['projects']) != 1 else ''})"
        tools.add_row(t["name"], t["tier"], rule, t["mcp"]["state"], scope)
    parts.append(tools if s["tools"] else Text("No AI tools found on PATH."))
    if s["sources"]:
        parts.append(Text("Sources  " + f" {term.glyphs['sep']} ".join(f"{k} {v['state']}" for k, v in s["sources"].items())))
    if s["problems"]:
        for p in s["problems"]:
            parts.append(line(term, "warn", p["what"]))
            parts.append(Text(f"    {term.glyphs['link']} {p['fix']}", style=_c(term, "dim")))
    else:
        parts.append(line(term, "ok", "No problems found."))
    return Group(*parts)
