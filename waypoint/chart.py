"""Build the chart: dashboard.json (contract), DASHBOARD.md and chart.html (presentation). LOGBOOK-SPEC §4–§5."""
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta
from importlib import resources
from pathlib import Path
from urllib.parse import quote

from . import config as config_mod
from . import logbook as lb
from . import rules as rules_mod
from .paths import placer, real
from .sources import claude, codex, health
from .sources.git import Git
from .watch import _Lock

ORDER = ["active", "occasional", "paused", "done"]
NAVIGATORS = ("claude-code", "codex")
SOURCE_RANK = {"log": 3, "claude-code": 2, "codex": 1, "git": 0}  # equal timestamps: the authored log wins (§4.4)


def _day(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "—"


def _next_midnight(ts):
    d = date.fromtimestamp(ts) + timedelta(days=1)
    return datetime(d.year, d.month, d.day).timestamp()


def _newest_date(logs, now):
    """Date of the newest authored (not future-dated) log entry, or "". `logs` is newest first."""
    return next((e["date"] for e in logs if e["ts"] <= now), "")


def build(logbook: Path, home: Path, now=None, deadline=None, roots=None):
    """deadline: time.monotonic() value; git lookups after it are skipped and reported as degraded.
    roots: use these instead of waypoint.toml's (the setup screen previews roots it hasn't written yet)."""
    now = time.time() if now is None else now
    cfg = config_mod.load(logbook)
    if roots is not None:
        cfg.roots = list(roots)
    logbook_issues = []
    projects = lb.load_projects(logbook, logbook_issues)
    for p in projects:
        future_count = sum(1 for entry in p["logs"] if entry["ts"] > now)
        if future_count:
            logbook_issues.append(f"{p['file']}: {future_count} future-dated log entries")
    ignored = lb.load_ignore(logbook, logbook_issues)
    # live projects first, so a live Path wins over an archived duplicate; archived Paths still absorb their
    # sessions (they never count, and never show as untracked) (§1, §4.2)
    ordered = [p for p in projects if not p["archived"]] + [p for p in projects if p["archived"]]
    seen, dupes = {}, []
    for p in ordered:
        k = real(p["path"])
        if k and k in seen:
            dupes.append(f"{p['file']} has the same Path as {seen[k]}")
        elif k:
            seen[k] = p["file"]
    place = placer([p["path"] for p in ordered], cfg.roots)
    cl, cl_health = claude.read(home, place)
    cx, cx_health = codex.read(home, place)
    git = Git(deadline)
    sessions = cl + cx

    by_folder = {}
    for s in sessions:
        by_folder.setdefault(s["folder"], []).append(s)

    tracked = set(seen)
    live = [p for p in projects if not p["archived"]]
    for p in live:
        mine = by_folder.get(real(p["path"]), []) if seen.get(real(p["path"])) == p["file"] else []
        newest = {t: max((s["ts"] for s in mine if s["tool"] == t), default=0) for t in NAVIGATORS}
        p["claude"], p["codex"], p["git"] = newest["claude-code"], newest["codex"], git.last_commit(p["path"])
        authored_logs = [entry for entry in p["logs"] if entry["ts"] <= now]
        log_ts = authored_logs[0]["ts"] if authored_logs else 0
        # (ts, source rank, displayed tool): rank by *source*, so a "codex" log entry still beats a Claude session
        cands = [(p["claude"], SOURCE_RANK["claude-code"], "claude-code"), (p["codex"], SOURCE_RANK["codex"], "codex"),
                 (p["git"], SOURCE_RANK["git"], "git"),
                 (log_ts, SOURCE_RANK["log"], authored_logs[0]["tool"] if authored_logs else "log")]
        ts, _, tool = max(cands)
        p["last"], p["last_tool"] = (ts, tool) if ts else (0, "")
        # max(0, ...): a source can update a moment after `now` was taken (a live Codex thread); never negative idle
        p["idle"] = max(0, int((now - p["last"]) // 86400)) if p["last"] else None
        limit = cfg.stale_days.get(p["status"])
        p["stale"] = bool(limit is not None and p["idle"] is not None and p["idle"] > limit)
        cutoff = _next_midnight(log_ts) if authored_logs else 0
        p["unlogged"] = sum(1 for s in mine if s["ts"] >= cutoff)
        newest = _newest_date(p["logs"], now)
        p["now_behind"] = bool(p["updated"] and newest > p["updated"])  # ISO dates compare as text (§4.4 v1.2)

    untracked = []
    for f, ss in by_folder.items():
        if f in tracked or f in ignored:
            continue
        recent = [s for s in ss if 0 <= now - s["ts"] <= cfg.untracked_days * 86400]
        if recent:
            newest_s = max(recent, key=lambda s: s["ts"])
            untracked.append({"path": f, "last": newest_s["ts"], "title": newest_s["title"]})
    untracked.sort(key=lambda u: (-u["last"], u["path"]))
    untracked_folders = {u["path"] for u in untracked}

    # pulse + feed: tracked (non-archived) projects and *listed* untracked folders, local calendar window
    first_day = date.fromtimestamp(now) - timedelta(days=cfg.pulse_days - 1)
    start = datetime(first_day.year, first_day.month, first_day.day).timestamp()
    name_of = {real(p["path"]): p["name"] for p in live if p["path"] and seen.get(real(p["path"])) == p["file"]}
    events = [{"ts": s["ts"], "tool": s["tool"], "kind": "session",
               "project": name_of.get(s["folder"], s["folder"]), "text": s["title"]}
              for s in sessions if s["folder"] in name_of or s["folder"] in untracked_folders]
    events += [{"ts": e["ts"], "tool": e["tool"], "kind": "log", "project": p["name"], "text": e["text"]}
               for p in live for e in p["logs"]]
    events = [e for e in events if start <= e["ts"] <= now]
    pulse = {(first_day + timedelta(days=i)).isoformat(): {t: 0 for t in (*NAVIGATORS, "chatgpt")}
             for i in range(cfg.pulse_days)}
    for e in events:
        bucket = pulse.get(_day(e["ts"]))
        if bucket is not None:
            bucket[e["tool"]] = bucket.get(e["tool"], 0) + 1
    feed = sorted((e for e in events if e["text"]), key=lambda e: (-e["ts"], e["project"], e["tool"]))[: cfg.feed_max]

    status_rank = {s: i for i, s in enumerate(ORDER)}
    live.sort(key=lambda p: (status_rank.get(p["status"], len(ORDER)), -p["last"], p["name"]))
    archived = sorted((p for p in projects if p["archived"]), key=lambda p: p["name"])
    keep = ("name", "file", "path", "status", "summary", "docs", "done", "next", "updated", "attention", "last",
            "last_tool", "idle", "claude", "codex", "git", "stale", "unlogged", "now_behind", "logs")
    return {
        "schema": 1,
        "generated": now,
        "config": {"stale_days": cfg.stale_days, "pulse_days": cfg.pulse_days, "untracked_days": cfg.untracked_days},
        "sources": {"claude-code": cl_health, "codex": cx_health, "git": git.health(),
                    "logbook": health("degraded", "; ".join([*logbook_issues, *dupes]))
                    if logbook_issues or dupes else health("ok")},
        "rules": rules_mod.check(home, logbook),
        "kpi": {
            "tracked": len(live),
            "attention": sum(len(p["attention"]) for p in live),
            "velocity": sum(1 for p in live if p["status"] == "active" and p["last"] >= now - 7 * 86400),
            "stale": sum(p["stale"] for p in live),
            "untracked": len(untracked),
            "unlogged": sum(1 for p in live if p["unlogged"]),
            "behind": sum(1 for p in live if p["now_behind"]),
        },
        "projects": [{k: p[k] for k in keep} for p in live],
        "archived": [{k: p[k] for k in ("name", "file", "summary", "done", "logs")} for p in archived],
        "untracked": untracked,
        "pulse": pulse,
        "feed": feed,
    }


# --- Markdown rendering: all data is untrusted text -------------------------------------------------------------

_MD_SPECIAL = re.compile(r"([\\`*_\[\]|#!])")


def _md(s, n=None):
    """Escape free text for a Markdown table cell or list item: no HTML, links, code or formatting survive."""
    s = " ".join(str(s).split())
    if n and len(s) > n:
        s = s[: n - 1] + "…"
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MD_SPECIAL.sub(r"\\\1", s)


def _link(text, rel):
    return f"[{_md(text)}]({quote(rel)})"


def render_md(d):
    gen = datetime.fromtimestamp(d["generated"]).strftime("%Y-%m-%d %H:%M")
    out = [f"# Chart (taken {gen})", "",
           "Generated by `waypoint refresh`. Don't edit: change `projects/<name>.md` and refresh again.", ""]
    live = d["projects"]

    hazards = [(_link(p["name"], p["file"]), a) for p in live for a in p["attention"]]
    hazards += [(_link(p["name"], p["file"]), f"Now is behind the log: updated {p['updated']}, newest entry "
                 f"{_newest_date(p['logs'], d['generated'])}") for p in live if p["now_behind"]]
    for target, state in d["rules"].items():
        if state != "in-sync":
            hazards.append((_link("GLOBAL-RULES", "GLOBAL-RULES.md"), f"{target} rule is {state}"))
    for src, h in d["sources"].items():
        if h["state"] in ("degraded", "unreadable"):
            hazards.append((_md(src), f"source {h['state']}: {h['detail']}"))
    if hazards:
        out += ["## Needs attention (hazards)", ""] + [f"- **{who}**: {_md(a, 300)}" for who, a in hazards] + [""]

    unlogged = [p for p in live if p["unlogged"]]
    if unlogged:
        out += ["## Unlogged sessions (watches nobody logged)", ""]
        out += [f"- **{_md(p['name'])}**: {p['unlogged']} session(s) since its last log entry" for p in unlogged] + [""]

    groups = {}
    for p in live:
        groups.setdefault(p["status"], []).append(p)
    for status, group in groups.items():
        out += [f"## {_md(status.title())} ({len(group)})", "",
                "| Project | Last | Idle | Claude | Codex | Git | Logged | Next |", "|---|---|---|---|---|---|---|---|"]
        for p in group:
            idle = "—" if p["idle"] is None else f"{p['idle']}d"
            logged = f"{p['logs'][0]['date']} ({_md(p['logs'][0]['tool'])})" if p["logs"] else "—"
            out.append(f"| {_link(p['name'], p['file'])} | {_day(p['last'])} | {idle} | {_day(p['claude'])} | "
                       f"{_day(p['codex'])} | {_day(p['git'])} | {logged} | {_md(p['next'], 90)} |")
        out.append("")

    stale = [p for p in live if p["stale"]]
    if stale:
        out += ["## Going stale (adrift)", ""]
        out += [f"- **{_md(p['name'])}** is {_md(p['status'])} but idle {p['idle']}d. "
                "Set Status to paused, or archive it." for p in stale] + [""]

    if d["untracked"]:
        out += ["## Untracked folders (uncharted waters)", "",
                "Track one (copy `projects/_template.md`) or add its path to `projects/_ignore.txt`.", "",
                "| Folder | Last active | Latest session title |", "|---|---|---|"]
        out += [f"| {_md(u['path'])} | {_day(u['last'])} | {_md(u['title'], 70)} |" for u in d["untracked"]] + [""]

    if d["archived"]:
        out += [f"## Archived (harbour, {len(d['archived'])})", "",
                ", ".join(_link(p["name"], p["file"]) for p in d["archived"]), ""]
    return "\n".join(out)


# --- HTML rendering (§5.2): the template reads the dashboard from an inert JSON block ---------------------------

_SLOT = '<script type="application/json" id="waypoint-data">null</script>'
# no data can close the script element or start a comment; U+2028/U+2029 are escaped for old JS parsers
_JSON_SAFE = {ord(c): "\\u%04x" % ord(c) for c in "<>&\u2028\u2029"}


def render_html(d):
    page = resources.files("waypoint").joinpath("templates", "chart.html").read_text(encoding="utf-8")
    data = json.dumps(d, ensure_ascii=False, separators=(",", ":")).translate(_JSON_SAFE)
    return page.replace(_SLOT, _SLOT.replace(">null<", f">{data}<"), 1)


def _atomic_write(path: Path, text: str):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: file briefly locked by an editor
                if attempt == 4:
                    raise
                time.sleep(0.2)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write(logbook: Path, d):
    with _Lock(logbook / ".waypoint.lock"):
        _atomic_write(logbook / "dashboard.json", json.dumps(d, ensure_ascii=False, indent=1))
        _atomic_write(logbook / "DASHBOARD.md", render_md(d))
        _atomic_write(logbook / "chart.html", render_html(d))
