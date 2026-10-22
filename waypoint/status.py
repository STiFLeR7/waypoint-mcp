"""`waypoint status`: one read-only view of the install (spec Part A). Never writes; every problem comes with a fix."""
import shutil
import json
from pathlib import Path

from . import __version__, chart, clis, config, logbook, project as project_mod, rules

SPEC = "1.5"
RULE_FIX = {"drifted": "run: waypoint rules --install", "missing": "run: waypoint rules --install",
            "shadowed": "run: waypoint rules --install --codex-override"}


def hooks_seen(cli_id):
    """ISO time a hook last ran for this tool (spec B.4), or None."""
    from . import hook
    try:
        return (hook.hooks_dir() / f"seen-{cli_id}").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def mcp_state(cli_id, home: Path, f: Path | None = None, project: Path | None = None) -> dict:
    """The registration in f (default: the CLI's user-wide file); waypoint-local also counts as Waypoint."""
    f = f or clis.mcp_file(cli_id, home)
    entries, err = clis.mcp_entries(f, project)
    if err:
        return {"state": "unreadable", "file": str(f), "detail": err}
    ours, _ = clis.choose(entries)
    if ours is None:
        other = entries["waypoint"] or entries["waypoint-local"]
        if other is None:
            return {"state": "missing", "file": str(f)}
        return {"state": "not ours", "file": str(f), "detail": clis.describe(other)}  # describe() hides URLs
    cmd = entries[ours]["command"]
    resolves = bool(shutil.which(cmd)) if Path(cmd).name == cmd else Path(cmd).is_file()
    return {"state": "registered", "name": ours, "file": str(f), "command": cmd, "resolves": resolves}


def _project_installs(lb: Path, home: Path) -> dict:
    """Installed project rules and MCP entries in tracked and archived project folders."""
    out, unreadable = {}, {}
    for name, repo in project_mod.repos(lb):
        for cli_id in project_mod.RULE_FILES:
            try:
                rule, method = project_mod.rule_state(cli_id, repo, lb)
            except Exception:
                rule, method = "unknown", "none"
            try:
                f, keyed = project_mod.mcp_location(cli_id, repo, home)
                mcp = mcp_state(cli_id, home, f, repo if keyed else None)["state"]
            except Exception:
                mcp = "unknown"
            if method == "none" and rule != "unknown" and mcp == "missing":
                continue
            row = {"project": name, "folder": str(repo), "rule": rule, "method": method, "mcp": mcp}
            if rule == "unknown" or mcp == "unknown":
                unreadable.setdefault(cli_id, []).append(row)
            else:
                out.setdefault(cli_id, []).append(row)
    relevant = set(clis.detected()) | set(out)
    for cli_id in relevant:
        out.setdefault(cli_id, []).extend(unreadable.get(cli_id, []))
    return out


def collect(lb, source: str, home: Path, build=chart.build, checked=None) -> dict:
    out = {"schema": 1, "version": __version__, "spec": SPEC, "logbook": str(lb) if lb else None,
           "logbook_source": source, "roots": [], "counts": None, "tools": {}, "sources": {}, "eod": config.eod_email_state(),
           "problems": []}
    problems = out["problems"]
    rule_states = {}
    if lb is None:
        where = f"at {checked} (from {source})" if checked else f"(looked at {source})"
        problems.append({"id": "no-logbook", "what": f"No logbook found {where}", "fix": "run: waypoint setup"})
    else:
        try:
            out["roots"] = config.load(lb).roots
            d = build(lb, home)
            counts = {"tracked": d["kpi"]["tracked"], "archived": len(d["archived"]),
                      "untracked": d["kpi"]["untracked"], "ignored": len(logbook.load_ignore(lb)),
                      "behind": d["kpi"]["behind"]}
        except config.ConfigError as e:
            problems.append({"id": "logbook-config", "what": str(e), "fix": "fix that file, then run: waypoint status"})
            try:
                rule_states = rules.check(home, lb)  # needs no config; "unknown" below only if even this fails
            except Exception:
                pass
        else:
            out["counts"], out["sources"], rule_states = counts, d["sources"], d["rules"]
            for name, h in d["sources"].items():
                if h["state"] != "ok":
                    problems.append({"id": f"source-{h['state']}", "subject": name,
                                     "what": f"{name} history is {h['state']}" + (f": {h['detail']}" if h["detail"] else ""),
                                     "fix": "the dashboard shows what could be read; see LOGBOOK-SPEC §4.1"})
        if rule_states.get("logbook") == "drifted":
            problems.append({"id": "logbook-rule-drifted",
                             "what": "The logbook's GLOBAL-RULES.md differs from this version's rule",
                             "fix": f"run: waypoint init {lb} --refresh-rules"})
        elif rule_states.get("logbook") == "missing":
            problems.append({"id": "logbook-rule-missing", "what": "The logbook has no GLOBAL-RULES.md",
                             "fix": f"run: waypoint init {lb}"})
    installs = {}
    if lb is not None:
        try:
            installs = _project_installs(lb, home)
        except OSError:
            pass  # a folder that can't be read: the global view still stands
    found = set(clis.detected())
    for c in clis.WIRED:
        mcp, rule, projects = mcp_state(c.id, home), rule_states.get(c.id), installs.get(c.id, [])
        if c.id not in found and rule in (None, "missing") and mcp["state"] == "missing" and not projects:
            continue
        tool = {"name": c.name, "tier": c.tier, "detected": c.id in found,
                "rule": rule or ("missing" if rule_states else "unknown"), "method": rules.global_method(c.id, home),
                "mcp": mcp, "projects": projects, "hooks": hooks_seen(c.id)}
        active_projects = [p for p in projects if p["method"] != "none" or p["mcp"] == "registered"]
        tool["scope"] = [s for s, on in (("global", tool["rule"] not in ("missing", "unknown")
                                          or mcp["state"] == "registered"), ("project", bool(active_projects))) if on]
        out["tools"][c.id] = tool
        if rule in RULE_FIX and not (rule == "missing" and any(p["method"] != "none" for p in projects)):
            problems.append({"id": f"rule-{rule}", "subject": c.id, "what": f"{c.name} rule is {rule}", "fix": RULE_FIX[rule]})
        for p in projects:
            if p["method"] != "none" and rule not in (None, "missing"):
                problems.append({"id": "rule-twice", "subject": c.id,
                                 "what": f"{c.name} reads the rule twice in {p['project']}: globally and from the repo",
                                 "fix": f"keep one: waypoint disconnect {c.id} (global), or "
                                        f"waypoint disconnect {c.id} --project \"{p['folder']}\""})
            if p["rule"] == "broken":
                problems.append({"id": "link-broken", "subject": c.id,
                                 "what": f"{c.name}'s rule link in {p['project']} points to a file that's gone",
                                 "fix": f"run: waypoint connect {c.id} --project \"{p['folder']}\""})
            elif p["rule"] == "drifted":
                problems.append({"id": "project-rule-drifted", "subject": c.id,
                                 "what": f"{c.name}'s rule in {p['project']} is drifted",
                                 "fix": f"run: waypoint connect {c.id} --project \"{p['folder']}\""})
            try:
                trusted = project_mod.codex_trusted(Path(p["folder"]), home) if c.id == "codex" else True
            except Exception:
                trusted = None
            if c.id == "codex" and p["mcp"] == "registered" and trusted is False:
                problems.append({"id": "codex-untrusted", "subject": "codex",
                                 "what": f"Codex ignores {p['project']}'s .codex/config.toml: the folder isn't trusted",
                                 "fix": f"start Codex in {p['folder']} once and trust it"})
        if mcp["state"] == "not ours":
            problems.append({"id": "mcp-not-ours", "subject": c.id,
                             "what": f"{c.name}: another MCP server ({mcp['detail']}) uses the name `waypoint`, so "
                                     "Waypoint's local server isn't registered",
                             "fix": f"run: waypoint connect {c.id} (it registers as `waypoint-local` and leaves that "
                                    "server alone)"})
        elif mcp["state"] == "unreadable":
            problems.append({"id": "mcp-unreadable", "subject": c.id,
                             "what": f"{c.name}: {mcp['file']} {mcp['detail']}", "fix": "fix or restore that file"})
        elif mcp["state"] == "registered" and not mcp["resolves"]:
            problems.append({"id": "mcp-off-path", "subject": c.id,
                             "what": f"{c.name} starts `{mcp['command']}`, which isn't on PATH",
                             "fix": "see the README: `waypoint` not found?"})
        if c.id == "codex" and lb is not None:
            tool["can_write_logbook"] = rules.codex_can_write(clis.config_dir("codex", home) / "config.toml", lb)
            if tool["can_write_logbook"] is False:
                problems.append({"id": "codex-sandbox", "subject": "codex",
                                 "what": "Codex can't write the logbook (its sandbox)",
                                 "fix": f"add {lb} to [sandbox_workspace_write] writable_roots in "
                                        f"{clis.config_dir('codex', home) / 'config.toml'}"})
    if out["eod"] == "invalid":  # the state only, never the address (spec §6)
        problems.append({"id": "eod-email-invalid",
                         "what": "The remembered end-of-day email address isn't a valid address",
                         "fix": "run: waypoint email --to <address>"})
    return out


def render_text(s: dict) -> str:
    lines = [f"Waypoint {s['version']} (logbook spec v{s['spec']})"]
    if s["logbook"]:
        lines.append(f"Logbook   {s['logbook']}  (from {s['logbook_source']})")
        c = s["counts"]
        if c:
            lines.append(f"Roots     {', '.join(s['roots'])}")
            lines.append(f"Projects  {c['tracked']} tracked · {c['archived']} archived · {c['untracked']} untracked · "
                         f"{c['ignored']} ignored" + (f" · {c['behind']} with Now behind the log" if c.get("behind") else ""))
    if s["tools"]:
        lines.append("AI tools")
        width = max(len(t["name"]) for t in s["tools"].values())
        for t in s["tools"].values():
            m = t["mcp"]
            mcp = m["state"] + (f" ({m['detail']})" if m.get("detail") else "")
            method = f" ({t['method']})" if t.get("method") not in (None, "none") else ""
            hooks = f" · hooks {t['hooks'][:10]}" if t.get("hooks") else " · hooks never ran"
            lines.append(f"  {t['name']:<{width}}  {t['tier']:<9}  rule {t['rule']}{method} · MCP {mcp}{hooks}"
                         + ("" if t["detected"] else " · not on PATH"))
            for p in t.get("projects", []):
                lines.append(f"  {'':<{width}}  in {p['project']}: rule {p['rule']} ({p['method']}) · MCP {p['mcp']}")
    lines.append("End-of-day email  " + ("address remembered" if s["eod"] == "remembered" else "not set up"))
    if s["sources"]:
        lines.append("Sources   " + " · ".join(f"{k} {v['state']}" for k, v in s["sources"].items()))
    if s["problems"]:
        lines.append("Problems")
        for p in s["problems"]:
            lines += [f"  ! {p['what']}", f"    → {p['fix']}"]
    else:
        lines.append("No problems found.")
    return "\n".join(lines)
