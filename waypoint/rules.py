"""Global rule rendering, drift check, install and uninstall (LOGBOOK-SPEC §7).

Install never edits what it doesn't own: Claude Code gets its own file (`<claude>/rules/waypoint.md`, an import line);
Codex has no rules folder, so `<codex>/AGENTS.md` gets a managed block between markers. Every write is planned first
(shown to the user), skipped when nothing changes, refused on malformed markers, backed up and replaced atomically.
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path

from . import clis
from .paths import norm

BEGIN, END = "<!-- waypoint:rules:begin -->", "<!-- waypoint:rules:end -->"
OWNED = "<!-- waypoint: managed file. `waypoint rules --uninstall` removes it. -->"


def template():
    return resources.files("waypoint").joinpath("templates/GLOBAL-RULES.md").read_text(encoding="utf-8")


def render(logbook: Path):
    return template().replace("{logbook}", logbook.resolve().as_posix())


def _clean(s):
    return "\n".join(l.rstrip() for l in s.replace("\r\n", "\n").strip().splitlines())


def claude_import(logbook: Path):
    # Claude Code ends an import path at the first unescaped space (memory docs: "put a backslash before each space")
    return "@" + (logbook.resolve() / "GLOBAL-RULES.md").as_posix().replace(" ", "\\ ")


def claude_dir(home):
    return clis.config_dir("claude-code", home)


def codex_dir(home):
    return clis.config_dir("codex", home)


BLOCK_CLIS = ("gemini-cli", "copilot-cli")  # a managed block holding the full rule, like Codex


def claude_rule_file(home):
    return claude_dir(home) / "rules" / "waypoint.md"


def claude_file_text(logbook: Path):
    return f"{OWNED}\n{claude_import(logbook)}\n"


def _read(f: Path):
    """(text, error). Text keeps its line endings; a file that isn't UTF-8 is never rewritten."""
    try:
        return f.read_bytes().decode("utf-8"), None
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as e:
        return None, f"can't read it as UTF-8 ({type(e).__name__})"


def _imports(text):
    found = re.finditer(r"^@((?:\\ |\S)*GLOBAL-RULES\.md)\s*$", text or "", re.M | re.I)
    return [norm(m[1].replace("\\ ", " ")) for m in found]


def _mentions_rules(text):
    # ponytail: heuristic for "some older progress rule is here"; good enough to say drifted vs missing
    return bool(re.search(r"GLOBAL-RULES|waypoint|^# Progress tracking", text or "", re.I | re.M))


def check(home: Path, logbook: Path, tools=None):
    """States for the logbook's canonical file and each tool's rule, all against render(logbook) (§7):
    in-sync | drifted | missing, and for Codex also shadowed (an AGENTS.override.md that Codex reads instead)."""
    canonical = _clean(render(logbook))
    canonical_file = logbook / "GLOBAL-RULES.md"
    on_disk, _ = _read(canonical_file)
    out = {"logbook": "missing" if on_disk is None else ("in-sync" if _clean(on_disk) == canonical else "drifted")}
    target = norm(str(canonical_file.resolve()))

    rf_text, _ = _read(claude_rule_file(home))
    md_text, _ = _read(claude_dir(home) / "CLAUDE.md")
    texts = [t for t in (rf_text, md_text) if t]
    imported = any(target in _imports(t) for t in texts)
    if (imported and out["logbook"] == "in-sync") or any(canonical in _clean(t) for t in texts):
        out["claude-code"] = "in-sync"  # an import only counts if the file it imports is current
    else:
        out["claude-code"] = "drifted" if imported or any(_mentions_rules(t) for t in texts) else "missing"

    override, _ = _read(codex_dir(home) / "AGENTS.override.md")
    agents, _ = _read(codex_dir(home) / "AGENTS.md")
    if override and override.strip():
        out["codex"] = "in-sync" if canonical in _clean(override) else "shadowed"
    elif agents and canonical in _clean(agents):
        out["codex"] = "in-sync"
    else:
        out["codex"] = "drifted" if _mentions_rules(agents) else "missing"
    in_use = set(tools) if tools is not None else set(clis.detected())
    for t in BLOCK_CLIS:  # Preview CLIs: asked for (default: on PATH), or their rule file exists
        if t not in in_use and not (clis.config_dir(t, home) / clis.CLIS[t].rule).exists():
            continue
        text, _ = _read(clis.config_dir(t, home) / clis.CLIS[t].rule)
        out[t] = "in-sync" if text and canonical in _clean(text) else ("drifted" if _mentions_rules(text) else "missing")
    return out


# ---- planning and applying changes ----

@dataclass
class Change:
    target: Path
    action: str  # create | update | remove | register | unregister | unchanged | refuse
    old: str | None = None
    new: str | None = None
    note: str = ""
    after: "Change | None" = None  # apply this only if that change succeeded (or was already in place)
    run: tuple = ()  # register/unregister: the CLI's own command (target is the file it keeps registrations in)

    def diff(self):
        if self.run:
            return "  runs: " + " ".join(self.run)
        a = (self.old or "").splitlines(keepends=True)
        b = (self.new or "").splitlines(keepends=True)
        return "".join(difflib.unified_diff(a, b, str(self.target), str(self.target), n=1))


def edit_block(old, block):
    """New text with the managed block set to `block` (None removes it); ValueError for malformed markers.
    Bytes outside the block are kept; the block uses the file's own line endings."""
    old = old or ""
    b, e = old.count(BEGIN), old.count(END)
    if (b, e) not in ((0, 0), (1, 1)) or (b == 1 and old.index(BEGIN) > old.index(END)):
        raise ValueError(f"its Waypoint markers are malformed ({b} begin, {e} end); fix them by hand first")
    eol = "\r\n" if "\r\n" in old else "\n"
    if block is None:
        if b == 0:
            return old
        i, j = old.index(BEGIN), old.index(END) + len(END)
        if old.startswith(eol, j):
            j += len(eol)  # the END line's own line ending
        if j == len(old) and old[:i].endswith(eol * 2):
            i -= len(eol)  # block at the end of the file: the blank line install put before it
        return old[:i] + old[j:]
    wrapped = BEGIN + eol + eol.join(block.strip().splitlines()) + eol + END
    if b == 1:
        i, j = old.index(BEGIN), old.index(END) + len(END)
        return old[:i] + wrapped + old[j:]
    if not old:
        return wrapped + eol
    return old + (eol if old.endswith(("\n", "\r")) else eol * 2) + wrapped + eol


def _block_change(f: Path, block, note):
    old, err = _read(f)
    if err:
        return Change(f, "refuse", note=err)
    if old is None and block is None:
        return Change(f, "unchanged", note=note)
    try:
        new = edit_block(old, block)
    except ValueError as e:
        return Change(f, "refuse", old, note=str(e))
    if new == (old or ""):
        return Change(f, "unchanged", old, new, note)
    return Change(f, "create" if old is None else ("remove" if block is None else "update"), old, new, note)


TOOLS = ("claude-code", "codex")
WRITES = ("create", "update", "remove", "register", "unregister")


def plan_install(home: Path, logbook: Path, codex_override=False, tools=TOOLS, mcp=False):
    changes = []
    if "claude-code" in tools:
        changes += _plan_claude(home, logbook)
    if "codex" in tools:
        changes += _plan_codex(home, logbook, codex_override)
    for t in [t for t in tools if t in BLOCK_CLIS]:
        c = clis.CLIS[t]
        changes.append(_block_change(clis.config_dir(t, home) / c.rule, render(logbook), f"{c.name} rule (managed block)"))
    if mcp:
        changes += [_plan_mcp(t, home, register=True) for t in tools]
    return changes


def _plan_mcp(cli_id, home, register):
    """Register (or remove) the local MCP server through the CLI's own command; a different `waypoint` server is
    refused, never replaced."""
    c, f = clis.CLIS[cli_id], clis.mcp_file(cli_id, home)
    if not _real_home(home):  # the CLI's own command always writes the real home's config, whatever --home says
        return Change(f, "unchanged", note=f"{c.name} MCP server: not registered (only for your real home folder)")
    entry, err = clis.mcp_entry(f)
    old = json.dumps(entry, sort_keys=True) if entry is not None else None
    note = f"{c.name} MCP server `waypoint`"
    if err:
        return Change(f, "refuse", note=f"{note}: {err}")
    if entry is not None and not clis.is_ours(entry):
        if not register:
            return Change(f, "unchanged", old, note=f"{note}: a different server ({clis.describe(entry)}); left alone")
        return Change(f, "refuse", old, note=f"{note}: a different server ({clis.describe(entry)}) already has that name. "
                                             f"Keep it and pass --no-mcp, or remove it: {c.exe} {' '.join(c.remove)}")
    if register == (entry is not None):
        return Change(f, "unchanged", old, note=note + (" (registered)" if register else ""))
    if not shutil.which(c.exe):
        if not register:  # reported, but it doesn't block removing the rest
            return Change(f, "unchanged", old, note=f"{note}: still registered, but `{c.exe}` isn't on PATH to remove it."
                                                    f" Remove the `waypoint` entry from {f} by hand")
        return Change(f, "refuse", old, note=f"{note}: `{c.exe}` isn't on PATH")
    return Change(f, "register" if register else "unregister", old, note=note, run=(c.exe, *(c.add if register else c.remove)))


def _plan_claude(home, logbook):
    changes = []
    rf = claude_rule_file(home)
    old, err = _read(rf)
    want = claude_file_text(logbook)
    if err:
        changes.append(Change(rf, "refuse", note=err))
    elif old is not None and OWNED not in old:
        changes.append(Change(rf, "refuse", old, note="a file with this name exists and isn't Waypoint's"))
    else:
        changes.append(Change(rf, "unchanged" if old == want else ("create" if old is None else "update"), old, want,
                              "Claude Code rule (an import line, in Waypoint's own file)"))
    # an older install put the import into CLAUDE.md itself; take that block out so the rule isn't loaded twice,
    # but only once the rules file is in place (never leave Claude Code without the rule)
    if changes[0].action != "refuse":
        legacy = _block_change(claude_dir(home) / "CLAUDE.md", None, "Claude Code: remove the older Waypoint block")
        if legacy.action != "unchanged":
            legacy.after = changes[0]
            changes.append(legacy)
    return changes


def _plan_codex(home, logbook, codex_override):
    changes = [_block_change(codex_dir(home) / "AGENTS.md", render(logbook), "Codex rule (managed block)")]
    ov = codex_dir(home) / "AGENTS.override.md"
    if codex_override:
        changes.append(_block_change(ov, render(logbook), "Codex override file (managed block)"))
    elif (_read(ov)[0] or "").strip():
        changes.append(Change(ov, "unchanged", note="Codex reads this file instead of AGENTS.md, so it won't see the rule."
                                                    " Add it there too: waypoint rules --install --codex-override"))
    return changes


def plan_uninstall(home: Path, mcp=True):
    changes = []
    rf = claude_rule_file(home)
    old, err = _read(rf)
    if err:
        changes.append(Change(rf, "refuse", note=err))
    elif old is not None:
        changes.append(Change(rf, "remove", old, None, "Claude Code rule file") if OWNED in old
                       else Change(rf, "refuse", old, note="isn't Waypoint's; left alone"))
    for f, note in ((claude_dir(home) / "CLAUDE.md", "older Waypoint block"),
                    (codex_dir(home) / "AGENTS.md", "Codex rule block"),
                    (codex_dir(home) / "AGENTS.override.md", "Codex override block"),
                    *((clis.config_dir(t, home) / clis.CLIS[t].rule, f"{clis.CLIS[t].name} rule block") for t in BLOCK_CLIS)):
        c = _block_change(f, None, note)
        if c.action != "unchanged":
            changes.append(c)
    if mcp:
        changes += [c for c in (_plan_mcp(t.id, home, register=False) for t in clis.WIRED)
                    if c.action in ("unregister", "refuse") or "by hand" in c.note]
    return changes


def _free_name(f: Path, stamp):
    """A backup path that never overwrites an earlier backup."""
    n, cand = 1, f.with_name(f"{f.name}.bak-{stamp}")
    while cand.exists():
        n += 1
        cand = f.with_name(f"{f.name}.bak-{stamp}-{n}")
    return cand


def _atomic_write(f: Path, text: str):
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=f".{f.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        os.replace(tmp, f)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply(changes):
    """Carry out create/update/remove changes; returns [(change, error or None)]. One failure doesn't stop the rest.
    A target that changed since it was planned (and shown) is refused, never overwritten."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results, failed = [], set()
    for c in changes:
        if c.action not in WRITES:
            continue
        if c.after is not None and (id(c.after) in failed or c.after.action == "refuse"):
            results.append((c, "skipped: it depends on a change that didn't happen"))
            failed.add(id(c))
            continue
        if c.run:
            results.append((c, _run(c)))
            if results[-1][1]:
                failed.add(id(c))
            continue
        now, err = _read(c.target)
        if err or now != c.old:
            results.append((c, "changed since it was shown; nothing written. Run the command again to see the new plan"))
            failed.add(id(c))
            continue
        try:
            if c.target.exists():
                shutil.copy2(c.target, _free_name(c.target, stamp))
            if c.new is None:
                c.target.unlink()
            else:
                _atomic_write(c.target, c.new)
            results.append((c, None))
        except OSError as e:
            results.append((c, f"{type(e).__name__}: {e}"))
            failed.add(id(c))
    return results


def _real_home(home):
    return Path(home).expanduser().resolve() == Path.home().resolve()


def _run(c):
    entry, err = clis.mcp_entry(c.target)
    if err or (json.dumps(entry, sort_keys=True) if entry is not None else None) != c.old:
        return "changed since it was shown; nothing run. Run the command again to see the new plan"
    exe = shutil.which(c.run[0])
    if not exe:
        return f"`{c.run[0]}` isn't on PATH"
    try:
        if c.target.exists():  # the CLI writes its own file (not atomically, for some); keep a copy first
            shutil.copy2(c.target, _free_name(c.target, datetime.now().strftime("%Y%m%d-%H%M%S")))
    except OSError as e:
        return f"couldn't back up {c.target.name} first ({type(e).__name__}); nothing run"
    try:
        r = subprocess.run([exe, *c.run[1:]], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"{type(e).__name__} running {c.run[0]}"
    return None if r.returncode == 0 else f"{c.run[0]} failed: {(r.stderr or r.stdout).strip()[:300]}"


def install(home: Path, logbook: Path, codex_override=False, tools=TOOLS):
    """Plan and apply in one go (no confirmation); returns the paths written. The CLI confirms first."""
    return [c.target for c, err in apply(plan_install(home, logbook, codex_override, tools)) if not err]
