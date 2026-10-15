"""Read project files (LOGBOOK-SPEC §1–§2, §4.3). Never writes."""
import re
from datetime import datetime
from pathlib import Path

from .paths import real

LOG_HEADING = re.compile(r"^## Log[ \t]*$", re.M | re.I)
ENTRY = re.compile(r"^### (\d{4}-\d{2}-\d{2}) [—-] (\S+)[ \t]*$", re.M)


def sections(text):
    """Split a project file into header / named sections / log, per the scoping rule (§2.1)."""
    m = LOG_HEADING.search(text)
    head, log = (text[: m.start()], text[m.end():]) if m else (text, "")
    parts = re.split(r"^## ", head, flags=re.M)
    out = {"header": parts[0], "log": log}
    for p in parts[1:]:
        name, _, body = p.partition("\n")
        out.setdefault(name.strip().lower(), body)  # first occurrence wins
    return out


def field(text, name):
    m = re.search(rf"^- {re.escape(name)}:[ \t]*(.+)$", text, re.M)
    return m[1].strip() if m else ""


def parse_log(body, diagnostics=None, source="project file"):
    """Log entries, newest date first; same-date entries keep file order (§2.2)."""
    heads = list(ENTRY.finditer(body))
    entries = []
    for i, h in enumerate(heads):
        try:
            ts = datetime.strptime(h[1], "%Y-%m-%d").timestamp()  # local midnight
        except (ValueError, OSError, OverflowError):
            if diagnostics is not None:
                diagnostics.append(f"{source}: unsupported log date {h[1]}")
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        lines = (l.strip() for l in body[h.end():end].splitlines())
        lines = (re.sub(r"^-(\s+|$)", "", l) for l in lines)  # bullet marker, including a bare "-"
        lines = (re.sub(r"^>\s?", "", l) for l in lines)  # one level of quoting from pasted text (§2.2)
        text = " ".join(l for l in lines if l)
        entries.append({"ts": ts, "date": h[1], "tool": h[2].lower(), "text": text})
    return sorted(entries, key=lambda e: -e["ts"])  # stable


KNOWN_STATUS = ("active", "occasional", "paused", "done")
# Tool ids writers may use (§2.3 v1.1). Readers accept and keep any id already in a file.
KNOWN_TOOLS = ("claude-code", "codex", "chatgpt", "gemini-cli", "copilot-cli", "cursor", "windsurf", "kiro",
               "goose", "cline", "claude-desktop", "opencode", "amp")


def _status(raw):
    # known values are case-insensitive; unknown ones are kept as written (§2.1)
    return raw.lower() if raw.lower() in KNOWN_STATUS else (raw or "active")


def load_project(f: Path, root: Path, diagnostics=None):
    s = sections(f.read_text(encoding="utf-8"))
    head, now = s["header"], s.get("now", "")
    archived = "_archive" in f.relative_to(root).parts
    logs = parse_log(s["log"], diagnostics, f.relative_to(root).as_posix())
    return {
        "name": f.stem,
        "file": f.relative_to(root).as_posix(),
        "path": field(head, "Path"),
        "status": "archived" if archived else _status(field(head, "Status")),
        "summary": field(head, "Summary"),
        "docs": [d.strip() for d in field(head, "Docs").split(",") if d.strip()],
        "done": field(now, "Done"),
        "next": field(now, "Next"),
        "attention": [a.strip() for a in re.findall(r"^- Attention:[ \t]*(.+)$", head, re.M)],
        "logs": logs,
    }


def load_projects(logbook: Path, diagnostics=None):
    root = logbook / "projects"
    if not root.exists():
        return []
    diagnostics = diagnostics if diagnostics is not None else []
    files = []
    for f in root.rglob("*.md"):
        if f.name.startswith("_"):
            continue
        if f.stem.startswith("."):
            diagnostics.append(f"{f.relative_to(root.parent).as_posix()}: unsupported project identity")
            continue
        if f.parent not in (root, root / "_archive"):
            diagnostics.append(f"{f.relative_to(root.parent).as_posix()}: unsupported project file location")
            continue
        files.append(f)
    files.sort(key=lambda f: (f.parent == root / "_archive", f.relative_to(root).as_posix().casefold(),
                              f.relative_to(root).as_posix()))
    projects, names = [], set()
    for f in files:
        key = f.stem.casefold()
        if key in names:
            diagnostics.append(f"{f.relative_to(root.parent).as_posix()}: ambiguous duplicate project name")
            continue
        try:
            project = load_project(f, root.parent, diagnostics)
        except (OSError, UnicodeError) as e:
            diagnostics.append(f"{f.relative_to(root.parent).as_posix()}: unreadable project file ({e})")
            continue
        names.add(key)
        projects.append(project)
    return projects


def load_ignore(logbook: Path, diagnostics=None):
    f = logbook / "projects" / "_ignore.txt"
    if not f.exists():
        return set()
    try:
        text = f.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as e:
        if diagnostics is not None:
            diagnostics.append(f"projects/_ignore.txt: unreadable ignore file ({e})")
        return set()
    lines = (l.strip() for l in text.splitlines())
    return {real(l) for l in lines if l and not l.startswith("#")}
