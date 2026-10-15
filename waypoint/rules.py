"""Global rule rendering, drift check, install and uninstall (LOGBOOK-SPEC §7).

Install never edits what it doesn't own: Claude Code gets its own file (`<claude>/rules/waypoint.md`, an import line);
Codex has no rules folder, so `<codex>/AGENTS.md` gets a managed block between markers. Every write is planned first
(shown to the user), skipped when nothing changes, refused on malformed markers, backed up and replaced atomically.
"""
import difflib
import functools
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path

from . import clis
from .paths import norm, real

BEGIN, END = "<!-- waypoint:rules:begin -->", "<!-- waypoint:rules:end -->"
OWNED = "<!-- waypoint: managed file. `waypoint rules --uninstall` removes it. -->"
MCP_MARKS = ("# waypoint:mcp:begin", "# waypoint:mcp:end")


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


def global_rule_file(cli_id, home):
    """The file holding a CLI's global rule."""
    return claude_rule_file(home) if cli_id == "claude-code" else clis.config_dir(cli_id, home) / clis.CLIS[cli_id].rule


def global_method(cli_id: str, home: Path) -> str:
    """How the global rule is installed: import (Claude Code's file, or an older CLAUDE.md block) | copy | none."""
    if cli_id == "claude-code":
        own, legacy = (_read(claude_rule_file(home))[0] or ""), (_read(claude_dir(home) / "CLAUDE.md")[0] or "")
        return "import" if OWNED in own or BEGIN in legacy else "none"
    return "copy" if BEGIN in (_read(global_rule_file(cli_id, home))[0] or "") else "none"


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
    out = {"logbook": logbook_state(logbook)}
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


def logbook_state(logbook: Path):
    """in-sync | drifted | missing for <logbook>/GLOBAL-RULES.md against this version's rendered rule (§7)."""
    on_disk, _ = _read(logbook / "GLOBAL-RULES.md")
    return "missing" if on_disk is None else ("in-sync" if _clean(on_disk) == _clean(render(logbook)) else "drifted")


# ---- planning and applying changes ----

@dataclass
class Change:
    target: Path
    action: str  # create | update | remove | register | unregister | unchanged | refuse
    old: str | None = None
    new: str | None = None
    note: str = ""
    after: "Change | tuple[Change, ...] | None" = None  # apply only after each dependency succeeded
    run: tuple = ()  # register/unregister: the CLI's own command (target is the file it keeps registrations in)
    cwd: Path | None = None
    keyed: bool = False
    replace_link: bool = False
    link_target: str | None = None
    remove_text: str | None = None
    run_more: tuple = ()

    def diff(self):
        if self.action == "link":
            if self.new is None:
                return f"  removes the link {self.target} (it pointed to {self.old}); the file it points to is kept"
            return f"  links {self.target} → {self.new}" + (f" (was → {self.old})" if self.old else "")
        if self.run:
            return "  runs: " + " ".join(self.run)
        if self.target.name == "config.toml" and any(m in (self.new or "") or m in (self.old or "")
                                                        for marks in (MCP_MARKS, SANDBOX) for m in marks):
            def marked(text):
                out = []
                for begin, end in (MCP_MARKS, SANDBOX):
                    if text and begin in text and end in text:
                        out.extend(line + "\n" for line in text[text.index(begin):text.index(end) + len(end)].splitlines())
                return out
            return "".join(difflib.unified_diff(marked(self.old), marked(self.new),
                                                str(self.target), str(self.target), n=1))
        a = (self.old or "").splitlines(keepends=True)
        b = (self.new or "").splitlines(keepends=True)
        return "".join(difflib.unified_diff(a, b, str(self.target), str(self.target), n=1))


def edit_block(old, block, begin=BEGIN, end=END):
    """New text with the managed block set to `block` (None removes it); ValueError for malformed markers.
    Bytes outside the block are kept; the block uses the file's own line endings."""
    old = old or ""
    b, e = old.count(begin), old.count(end)
    if (b, e) not in ((0, 0), (1, 1)) or (b == 1 and old.index(begin) > old.index(end)):
        raise ValueError(f"its Waypoint markers are malformed ({b} begin, {e} end); fix them by hand first")
    eol = "\r\n" if "\r\n" in old else "\n"
    if block is None:
        if b == 0:
            return old
        i, j = old.index(begin), old.index(end) + len(end)
        if old.startswith(eol, j):
            j += len(eol)  # the END line's own line ending
        if j == len(old) and old[:i].endswith(eol * 2):
            i -= len(eol)  # block at the end of the file: the blank line install put before it
        return old[:i] + old[j:]
    wrapped = begin + eol + eol.join(block.strip().splitlines()) + eol + end
    if b == 1:
        i, j = old.index(begin), old.index(end) + len(end)
        return old[:i] + wrapped + old[j:]
    if not old:
        return wrapped + eol
    return old + (eol if old.endswith(("\n", "\r")) else eol * 2) + wrapped + eol


def _block_change(f: Path, block, note, begin=BEGIN, end=END, drop_empty=False):
    old, err = _read(f)
    if err:
        return Change(f, "refuse", note=err)
    if old is None and block is None:
        return Change(f, "unchanged", note=note)
    try:
        new = edit_block(old, block, begin, end)
    except ValueError as e:
        return Change(f, "refuse", old, note=str(e))
    if new == (old or ""):
        return Change(f, "unchanged", old, new, note)
    if drop_empty and block is None and not new.strip():
        return Change(f, "remove", old, None, note, remove_text=new)
    return Change(f, "create" if old is None else ("remove" if block is None else "update"), old, new, note,
                  remove_text=new if block is None else None)


TOOLS = ("claude-code", "codex")
WRITES = ("create", "update", "remove", "link", "register", "unregister")


def plan_install(home: Path, logbook: Path, codex_override=False, tools=TOOLS, mcp=False, writable_root=False):
    changes = []
    if "claude-code" in tools:
        changes += _plan_claude(home, logbook)
    if "codex" in tools:
        changes += _plan_codex(home, logbook, codex_override)
        if writable_root:
            changes.append(plan_writable_root(codex_dir(home) / "config.toml", logbook))
    for t in [t for t in tools if t in BLOCK_CLIS]:
        c = clis.CLIS[t]
        changes.append(_block_change(clis.config_dir(t, home) / c.rule, render(logbook), f"{c.name} rule (managed block)"))
    if mcp:
        changes += [_plan_mcp(t, home, register=True) for t in tools]
    return changes


def _plan_mcp(cli_id, home, register):
    c, f = clis.CLIS[cli_id], clis.mcp_file(cli_id, home)
    if not _real_home(home):  # the CLI's own command always writes the real home's config, whatever --home says
        return Change(f, "unchanged", note=f"{c.name} MCP server: not registered (only for your real home folder)")
    return mcp_change(cli_id, f, register)


def mcp_snapshot(f: Path, project: Path | None = None):
    """Both names' entries in f as comparable text: what the plan showed must still be there when the CLI runs."""
    entries, err = clis.mcp_entries(f, project)
    return (None, err) if err else (json.dumps(entries, sort_keys=True), None)


def mcp_change(cli_id, f: Path, register, project: Path | None = None, reserved=()):
    """Register or remove Waypoint through the CLI, choosing a free name without replacing another server."""
    c = clis.CLIS[cli_id]
    keyed = cli_id == "claude-code" and project is not None
    note = f"{c.name} MCP server" + (f" for {project.name}" if project else "")
    entries, err = clis.mcp_entries(f, project if keyed else None)
    if err:
        return Change(f, "refuse", note=f"{note}: {f.name} {err}")
    snap, (ours, free) = json.dumps(entries, sort_keys=True), clis.choose(entries)
    if register and free in reserved:
        free = next((n for n in clis.NAMES if entries.get(n) is None and n not in reserved), None)
    where = {"cwd": project, "keyed": keyed}
    if not register:
        if ours is None:
            return Change(f, "unchanged", snap, note=f"{note}: not registered"
                          + ("; the `waypoint` entry is another server, left alone" if entries["waypoint"] else ""))
        if not shutil.which(c.exe):
            return Change(f, "unchanged", snap, note=f"{note}: `{ours}` is still registered, but `{c.exe}` isn't on "
                                                      f"PATH to remove it. Remove the `{ours}` entry from {f} by hand")
        names = tuple(n for n in clis.NAMES if entries.get(n) is not None and clis.is_ours(entries[n]))
        return Change(f, "unregister", snap, note=f"{note} `{', '.join(names)}`",
                      run=(c.exe, *clis.mcp_argv(cli_id, "remove", ours, project=project is not None)),
                      run_more=tuple(clis.mcp_argv(cli_id, "remove", n, project=project is not None)
                                     for n in names if n != ours), **where)
    if ours is not None:
        return Change(f, "unchanged", snap, note=f"{note} `{ours}` (registered)")
    if free is None:
        return Change(f, "refuse", snap, note=f"{note}: other servers already use both `waypoint` and `waypoint-local` "
                                              f"({clis.describe(entries['waypoint'])}). Keep them and pass --no-mcp, "
                                              "or remove one of them")
    command = clis.server_command()
    if command is None:
        return Change(f, "refuse", snap, note=f"{note}: {clis.NO_COMMAND}")
    if not shutil.which(c.exe):
        return Change(f, "refuse", snap, note=f"{note}: `{c.exe}` isn't on PATH")
    why = ([f"`waypoint` is another server ({clis.describe(entries['waypoint'])}), so this one is `waypoint-local`"]
           if free == "waypoint-local" and entries["waypoint"] is not None else []) + (["`waypoint` isn't on PATH, so it registers the full path"]
                                             if command != "waypoint" else [])
    return Change(f, "register", snap, note=f"{note} `{free}`" + "".join(f"; {w}" for w in why),
                  run=(c.exe, *clis.mcp_argv(cli_id, "add", free, command, project is not None)), **where)


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


SANDBOX = ("# waypoint:sandbox:begin", "# waypoint:sandbox:end")


def inside(path, root):
    p, r = real(str(path)), real(str(root)).rstrip("\\/")
    return p == r or p.startswith(r + ("\\" if "\\" in r else "/"))


def _can_write(data: dict, lb: Path):
    """Whether a parsed Codex config lets Codex write lb: full access, or lb under a writable root."""
    if not isinstance(data, dict):
        return False
    if data.get("sandbox_mode") == "danger-full-access":
        return True
    table = data.get("sandbox_workspace_write")
    roots = table.get("writable_roots") if isinstance(table, dict) else None
    return isinstance(roots, list) and any(isinstance(r, str) and inside(lb, r) for r in roots)


def codex_can_write(config: Path, lb: Path):
    """True/False from a Codex config.toml; None when it can't be read."""
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None
    return _can_write(data, lb)


def sandbox_block(lb: Path):
    return f"[sandbox_workspace_write]\nwritable_roots = [{json.dumps(str(lb))}]"  # a JSON string is a TOML basic string


def toml_edit(old: str | None, marks: tuple, block: str | None):
    """Set a marked TOML block; None if the result is invalid TOML; ValueError for malformed markers."""
    new = edit_block(old, block, *marks)
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        return None
    return new


def plan_writable_root(config: Path, lb: Path | None, remove=False):
    """Add or remove the marked Codex writable root, without editing a table Waypoint doesn't own."""
    note = "Codex sandbox: let it write the logbook"
    if remove:
        return _block_change(config, None, note, *SANDBOX)
    state = codex_can_write(config, lb)
    if state is None:
        return Change(config, "refuse", note=f"{note}: can't read {config.name}")
    if state:
        return Change(config, "unchanged", note=f"{note} (already allowed)")
    old, _ = _read(config)
    try:
        new = toml_edit(old, SANDBOX, sandbox_block(lb))
    except ValueError as e:
        return Change(config, "refuse", old, note=f"{note}: {e}")
    if new is None:
        return Change(config, "unchanged", old, note=f"{note}: {config.name} already has a [sandbox_workspace_write] "
                                                    f"table; add {lb} to its writable_roots yourself")
    return Change(config, "create" if old is None else "update", old, new, note + " (adds it to writable_roots)")


def plan_uninstall(home: Path, mcp=True, tools=None):
    """Remove only what Waypoint added globally, for `tools` (default: every wired CLI)."""
    tools = set(tools) if tools is not None else {c.id for c in clis.WIRED}
    changes = []
    if "claude-code" in tools:
        rf = claude_rule_file(home)
        old, err = _read(rf)
        if err:
            changes.append(Change(rf, "refuse", note=err))
        elif old is not None:
            changes.append(Change(rf, "remove", old, None, "Claude Code rule file") if OWNED in old
                           else Change(rf, "refuse", old, note="isn't Waypoint's; left alone"))
    files = [(claude_dir(home) / "CLAUDE.md", "older Waypoint block")] if "claude-code" in tools else []
    if "codex" in tools:
        files += [(codex_dir(home) / "AGENTS.md", "Codex rule block"),
                  (codex_dir(home) / "AGENTS.override.md", "Codex override block")]
    files += [(clis.config_dir(t, home) / clis.CLIS[t].rule, f"{clis.CLIS[t].name} rule block")
              for t in BLOCK_CLIS if t in tools]
    for f, note in files:
        c = _block_change(f, None, note)
        if c.action != "unchanged":
            changes.append(c)
    if "codex" in tools:
        sandbox = plan_writable_root(codex_dir(home) / "config.toml", None, remove=True)
        if sandbox.action != "unchanged":
            changes.append(sandbox)
    if mcp:
        changes += [c for c in (_plan_mcp(t.id, home, register=False) for t in clis.WIRED if t.id in tools)
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


def link_state(f: Path):
    """Where the symlink f points (as stored), or None when f isn't a symlink."""
    try:
        target = os.readlink(f) if f.is_symlink() else None
        if target and target[:8].upper() == "\\\\?\\UNC\\":
            return "\\\\" + target[8:]
        return target[4:] if target and target.startswith("\\\\?\\") else target
    except OSError:
        return None


@functools.lru_cache(maxsize=1)
def can_symlink():
    """Whether this OS lets Waypoint make file symlinks (Windows needs Developer Mode or admin rights)."""
    with tempfile.TemporaryDirectory() as d:
        try:
            os.symlink(os.path.join(d, "target"), os.path.join(d, "link"))
        except (OSError, NotImplementedError):
            return False
    return True


def _link(c):
    """Create, re-point, or remove a symlink if it still matches the plan."""
    if link_state(c.target) != c.old or (c.old is None and c.target.exists()):
        return "changed since it was shown; nothing written. Run the command again to see the new plan"
    if c.new is None:
        try:
            c.target.unlink()
        except OSError as e:
            return f"{type(e).__name__}: {e}"
        return None
    tmp = None
    try:
        c.target.parent.mkdir(parents=True, exist_ok=True)
        if c.old is None:
            os.symlink(c.new, c.target)
        else:
            fd, tmp = tempfile.mkstemp(dir=c.target.parent, prefix=f".{c.target.name}.", suffix=".tmp")
            os.close(fd)
            os.unlink(tmp)
            os.symlink(c.new, tmp)
            os.replace(tmp, c.target)
    except OSError as e:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        return f"{type(e).__name__}: {e}"
    return None


def apply(changes):
    """Carry out create/update/remove changes; returns [(change, error or None)]. One failure doesn't stop the rest.
    A target that changed since it was planned (and shown) is refused, never overwritten."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results, failed = [], set()
    for c in changes:
        if c.action not in WRITES:
            continue
        dependencies = c.after if isinstance(c.after, tuple) else (c.after,) if c.after is not None else ()
        if any(id(dep) in failed or dep.action == "refuse" for dep in dependencies):
            results.append((c, "skipped: it depends on a change that didn't happen"))
            failed.add(id(c))
            continue
        if c.run or c.action == "link":
            results.append((c, _run(c) if c.run else _link(c)))
            if results[-1][1]:
                failed.add(id(c))
            continue
        if c.action == "remove" and c.target.is_symlink() and c.remove_text is None:
            action = "remove Waypoint's server entry from that file by hand" if c.target.name == "mcp.json" else "remove Waypoint's import from that file by hand"
            results.append((c, f"{c.target} is a link to {c.target.resolve()}; {action}"))
            failed.add(id(c))
            continue
        now, err = _read(c.target)
        if err or now != c.old:
            results.append((c, "changed since it was shown; nothing written. Run the command again to see the new plan"))
            failed.add(id(c))
            continue
        try:
            target = c.target
            if c.replace_link and not c.target.is_symlink():
                raise ValueError("changed since it was shown; nothing written. Run the command again to see the new plan")
            if c.target.is_symlink():
                target = c.target.resolve()
                if c.replace_link:
                    if target.name.casefold() != "global-rules.md":
                        raise ValueError("replacing a link to a file other than Waypoint's GLOBAL-RULES.md is refused")
                    if link_state(c.target) != c.link_target:
                        raise ValueError("changed since it was shown; nothing written. Run the command again to see the new plan")
                    shutil.copy2(target, _free_name(c.target, stamp))
                    c.target.unlink()
                    target = c.target
                elif target.name.casefold() == "global-rules.md":
                    raise ValueError("content changes to GLOBAL-RULES.md through a symlink are refused; use a link change")
            if c.target.exists():
                shutil.copy2(c.target, _free_name(c.target, stamp))
            if c.new is None:
                if c.target.is_symlink():
                    _atomic_write(target, c.remove_text if c.remove_text is not None else c.old)
                else:
                    target.unlink()
            else:
                _atomic_write(target, c.new)
            results.append((c, None))
        except ValueError as e:
            results.append((c, str(e)))
            failed.add(id(c))
        except OSError as e:
            results.append((c, f"{type(e).__name__}: {e}"))
            failed.add(id(c))
    return results


def _real_home(home):
    return Path(home).expanduser().resolve() == Path.home().resolve()


def _run(c):
    snap, err = mcp_snapshot(c.target, c.cwd if c.keyed else None)
    if err or snap != c.old:
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
        for args in ((c.run[1:],) + c.run_more):
            r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=60, cwd=c.cwd)
            if r.returncode:
                return f"{c.run[0]} failed: {(r.stderr or r.stdout).strip()[:300]}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"{type(e).__name__} running {c.run[0]}"
    return None


def install(home: Path, logbook: Path, codex_override=False, tools=TOOLS):
    """Plan and apply in one go (no confirmation); returns the paths written. The CLI confirms first."""
    return [c.target for c, err in apply(plan_install(home, logbook, codex_override, tools)) if not err]
