"""Per-project install (spec Part B, LOGBOOK-SPEC §7.1): one repo's rule and MCP entry, nothing in the home config.
(Claude Code keeps its local-scope MCP entry in ~/.claude.json under the repo's key: stored at home, scoped to the repo.)
Every function here returns plans (rules.Change) or plain data; rules.apply is the only writer. No registry: status,
uninstall and the screen look at each tracked or archived project's folder."""
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from . import clis, logbook, rules
from .paths import norm, real
from .rules import Change

RULE_FILES = {"claude-code": ".claude/rules/waypoint.md", "codex": "AGENTS.md", "gemini-cli": "GEMINI.md",
              "copilot-cli": ".github/copilot-instructions.md"}
LINKABLE = ("codex", "copilot-cli")  # files Waypoint may own outright; Gemini CLI won't import from outside the repo
EXCLUDE = ("# waypoint:exclude:begin", "# waypoint:exclude:end")
GIT_TIMEOUT = 60


def rule_file(cli_id, repo: Path) -> Path:
    return repo / RULE_FILES[cli_id]


def _rules_target(lb: Path) -> str:
    return str((lb / "GLOBAL-RULES.md").resolve())


def _our_link(f: Path) -> bool:
    """A symlink Waypoint made: it points at some logbook's GLOBAL-RULES.md (this one, or one that moved)."""
    target = rules.link_state(f)
    return target is not None and Path(target).name == "GLOBAL-RULES.md"


def rule_state(cli_id, repo: Path, lb: Path):
    """(state, method) of the repo's rule. state: in-sync | drifted | missing | broken (a link whose file is gone);
    method: import | symlink | copy | none."""
    f = rule_file(cli_id, repo)
    fresh = rules.logbook_state(lb) == "in-sync"
    if f.is_symlink():
        if not _our_link(f):
            return "missing", "none"
        if not f.exists():
            return "broken", "symlink"
        same = norm(str(f.resolve())) == norm(_rules_target(lb))
        return ("in-sync" if same and fresh else "drifted"), "symlink"
    text, _ = rules._read(f)
    if text is None:
        return "missing", "none"
    if cli_id == "claude-code":
        if rules.OWNED not in text:
            return "missing", "none"
        imported = norm(_rules_target(lb)) in rules._imports(text)
        return ("in-sync" if imported and fresh else "drifted"), "import"
    if rules.BEGIN not in text:
        return "missing", "none"
    return ("in-sync" if rules._clean(rules.render(lb)) in rules._clean(text) else "drifted"), "copy"


def plan_rule(cli_id, repo: Path, lb: Path, copy=False) -> Change:
    """The repo's rule, best method first: import (Claude Code's own file) → symlink (a file Waypoint owns outright,
    where the OS allows links) → copy (a managed block). copy=True never links, and turns Waypoint's link into a real
    file the user can edit. A broken or moved link is re-pointed."""
    f, name = rule_file(cli_id, repo), clis.CLIS[cli_id].name
    note, target = f"{name} rule in {repo.name}", _rules_target(lb)
    if f.is_symlink():
        if not _our_link(f):
            return Change(f, "refuse", note=f"{note}: a link Waypoint didn't make is in the way")
        if copy:
            old, _ = rules._read(f)
            return Change(f, "update", old, rules.edit_block("", rules.render(lb)),
                          f"{note}: the link becomes a file with a copied block, yours to edit",
                          replace_link=True, link_target=rules.link_state(f))
        now = rules.link_state(f)
        if norm(now) == norm(target):
            return Change(f, "unchanged", note=f"{note} (a link to the logbook's rule)")
        return Change(f, "link", now, target, f"{note}: point the link at this logbook's rule")
    if cli_id == "claude-code":
        old, err = rules._read(f)
        want = rules.claude_file_text(lb)
        if err:
            return Change(f, "refuse", note=f"{note}: {err}")
        if old is not None and rules.OWNED not in old:
            return Change(f, "refuse", old, note=f"{note}: a file with this name exists and isn't Waypoint's")
        return Change(f, "unchanged" if old == want else ("create" if old is None else "update"), old, want,
                      f"{note} (an import line; Claude Code asks once per repo before it loads a file from outside it)")
    if not f.exists() and not copy and cli_id in LINKABLE and rules.can_symlink():
        return Change(f, "link", None, target, f"{note} (a link to the logbook's rule)")
    return rules._block_change(f, rules.render(lb), f"{note} (managed block)")


def plan_exclude(repo: Path, add=(), drop=()) -> list:
    """Keep Waypoint files out of commits in the enclosing repository's local info/exclude file."""
    if (repo / ".git").is_dir():
        top, f = repo, repo / ".git" / "info" / "exclude"
    else:
        top = f = None
    git = shutil.which("git")
    if top is None and not git:
        if not add:
            return []
        return [Change(repo / ".git" / "info" / "exclude", "unchanged",
                       note=f"{repo.name} isn't a plain git checkout; if it's a repo, keep "
                            + ", ".join("/" + Path(p).relative_to(repo).as_posix() for p in add)
                            + " out of commits yourself")]
    if top is None:
        try:
            top_result = subprocess.run([git, "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True,
                                        text=True, timeout=GIT_TIMEOUT, check=True)
            path_result = subprocess.run([git, "-C", str(repo), "rev-parse", "--git-path", "info/exclude"],
                                         capture_output=True, text=True, timeout=GIT_TIMEOUT, check=True)
            if not isinstance(top_result.stdout, str) or not isinstance(path_result.stdout, str):
                raise ValueError("git returned no paths")
            top, f = Path(top_result.stdout.strip()), Path(path_result.stdout.strip())
            if not str(top) or not str(f):
                raise ValueError("git returned an empty path")
            if not f.is_absolute():
                f = repo / f
        except (OSError, ValueError, subprocess.SubprocessError):
            if not add:
                return []
            return [Change(repo / ".git" / "info" / "exclude", "unchanged",
                           note=f"{repo.name} isn't a plain git checkout; if it's a repo, keep "
                                + ", ".join("/" + Path(p).relative_to(repo).as_posix() for p in add)
                                + " out of commits yourself")]

    def rel(p):
        return "/" + Path(p).absolute().relative_to(top.resolve()).as_posix()
    old, err = rules._read(f)
    if err:
        return [Change(f, "refuse", note=f"{f} {err}")]
    m = re.search(re.escape(EXCLUDE[0]) + r"(.*?)" + re.escape(EXCLUDE[1]), old or "", re.S)
    gone = {rel(p) for p in drop}
    kept = [l.strip() for l in (m[1] if m else "").splitlines() if l.strip() and l.strip() not in gone]
    kept += [l for l in dict.fromkeys(rel(p) for p in add) if l not in kept]
    c = rules._block_change(f, "\n".join(kept) or None, f"keep Waypoint's files in {repo.name} out of git (local only)",
                            *EXCLUDE)
    return [] if c.action == "unchanged" else [c]


def git_tracked(repo: Path, f: Path) -> bool:
    """Whether git tracks f; run in the project folder so git can find a parent repo. Unsure counts as tracked."""
    if not f.exists():
        return False
    git = shutil.which("git")
    if not git:
        return True
    try:
        r = subprocess.run([git, "-C", str(repo), "ls-files", "--error-unmatch", "--", f.relative_to(repo).as_posix()],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return True
    return r.returncode == 0


MCP_MARKS = ("# waypoint:mcp:begin", "# waypoint:mcp:end")


def repos(lb: Path):
    """(project name, folder) for every tracked and archived project whose folder exists, each folder once."""
    out, seen = [], set()
    for p in logbook.load_projects(lb):
        folder = Path(p["path"]) if p["path"] else None
        if folder is None or not folder.is_absolute() or not folder.is_dir() or real(str(folder)) in seen:
            continue
        seen.add(real(str(folder)))
        out.append((p["name"], folder))
    return out


def mcp_location(cli_id, repo: Path, home: Path):
    """(file, keyed): where the repo's MCP entry lives; keyed = under projects[<repo>] in that file (Claude Code)."""
    if cli_id == "claude-code":
        return clis.mcp_file("claude-code", home), True
    return repo / {"codex": ".codex/config.toml", "gemini-cli": ".gemini/settings.json",
                   "copilot-cli": ".github/mcp.json"}[cli_id], False


def codex_trusted(repo: Path, home: Path):
    """Whether ~/.codex/config.toml trusts this exact repo; None means the config can't be read."""
    f = clis.config_dir("codex", home) / "config.toml"
    try:
        data = tomllib.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None
    projects = data.get("projects")
    key = real(str(repo))
    return isinstance(projects, dict) and any(
        real(k) == key and isinstance(v, dict) and v.get("trust_level") == "trusted" for k, v in projects.items())


def plan_codex_config(repo: Path, lb: Path | None, mcp=True, writable_root=True, remove=False, home=None) -> Change:
    """Plan the repo's Codex MCP and writable_roots blocks; validate the complete TOML result before writing."""
    f = repo / ".codex" / "config.toml"
    note = f"Codex in {repo.name}"
    old, err = rules._read(f)
    if err:
        return Change(f, "refuse", note=f"{note}: {f.name} {err}")
    if remove and not any(mark in (old or "") for mark in (*MCP_MARKS, *rules.SANDBOX)):
        return Change(f, "unchanged", old, old, note)
    try:
        data = tomllib.loads(old or "")
    except tomllib.TOMLDecodeError as e:
        return Change(f, "refuse", old, note=f"{note}: can't parse {f.name} ({type(e).__name__}); fix it first")
    new, parts = old or "", []
    try:
        if remove:
            new = rules.edit_block(rules.edit_block(new, None, *MCP_MARKS), None, *rules.SANDBOX)
            parts.append("remove Waypoint's MCP server and writable_roots blocks")
        else:
            if mcp:
                ours, free = clis.choose(clis.entries_of(data.get("mcp_servers")))
                if ours is None and home is not None:
                    user, user_err = clis.mcp_entries(clis.mcp_file("codex", home))
                    if user_err:
                        return Change(f, "refuse", old, note=f"{note}: can't check user MCP entries ({user_err})")
                    taken = {n for n, value in user.items() if value is not None and not clis.is_ours(value)}
                    if free in taken:
                        local = clis.entries_of(data.get("mcp_servers"))
                        free = next((n for n in clis.NAMES if local[n] is None and n not in taken), None)
                if ours is None:
                    if free is None:
                        return Change(f, "refuse", old, note=f"{note}: other servers already use both `waypoint` and `waypoint-local`")
                    command = clis.server_command()
                    if command is None:
                        return Change(f, "refuse", old, note=f"{note}: {clis.NO_COMMAND}")
                    block = f"[mcp_servers.{json.dumps(free)}]\ncommand = {json.dumps(command)}\nargs = [\"mcp\"]"
                    attempt = rules.toml_edit(new, MCP_MARKS, block)
                    if attempt is None:
                        return Change(f, "refuse", old, note=f"{note}: {f.name} sets mcp_servers in a way Waypoint can't extend; add the `{free}` server by hand")
                    new = attempt
                    parts.append(f"MCP server `{free}`" + ("" if free == "waypoint" else " (`waypoint` is another server)"))
            if writable_root and lb is not None and not rules._can_write(data, lb):
                attempt = rules.toml_edit(new, rules.SANDBOX, rules.sandbox_block(lb))
                if attempt is None:
                    parts.append(f"{f.name} already has a [sandbox_workspace_write] table; add {lb} to its writable_roots yourself")
                else:
                    new = attempt
                    parts.append("the logbook in writable_roots")
    except ValueError as e:
        return Change(f, "refuse", old, note=f"{note}: {e}")
    text = note + (": " + "; ".join(parts) if parts else "")
    if new == (old or ""):
        return Change(f, "unchanged", old, note=text)
    if not new.strip():
        return Change(f, "remove", old, None, text, remove_text=new)
    return Change(f, "create" if old is None else "update", old, new, text)


def plan_copilot_mcp(repo: Path, register=True, home=None) -> Change:
    """Plan Copilot CLI's repo-local MCP server while keeping other servers."""
    f = repo / ".github" / "mcp.json"
    note = f"Copilot CLI MCP server in {repo.name}"
    old, err = rules._read(f)
    if err:
        return Change(f, "refuse", note=f"{note}: {f.name} {err}")
    try:
        data = json.loads(old) if (old or "").strip() else {}
    except ValueError:
        return Change(f, "refuse", old, note=f"{note}: {f.name} isn't plain JSON; add Waypoint to it by hand")
    if not isinstance(data, dict) or (data and not isinstance(data.get("mcpServers"), dict)):
        return Change(f, "refuse", old, note=f"{note}: {f.name} uses a layout Waypoint doesn't edit; add it by hand")
    servers = data.setdefault("mcpServers", {})
    ours, free = clis.choose(clis.entries_of(servers))
    if register and home is not None:
        user, user_err = clis.mcp_entries(clis.mcp_file("copilot-cli", home))
        if user_err:
            return Change(f, "refuse", old, note=f"{note}: can't check user MCP entries ({user_err})")
        reserved = {n for n, value in user.items() if value is not None and not clis.is_ours(value)}
        if ours is None and free in reserved:
            free = next((n for n in clis.NAMES if clis.entries_of(servers)[n] is None and n not in reserved), None)
    if not register:
        if ours is None:
            return Change(f, "unchanged", old, note=f"{note}: not registered")
        del servers[ours]
        top = repo
        while not (top / ".git").is_dir() and top.parent != top:
            top = top.parent
        exclude = top / ".git" / "info" / "exclude"
        block = re.search(re.escape(EXCLUDE[0]) + r"(.*?)" + re.escape(EXCLUDE[1]),
                          rules._read(exclude)[0] or "", re.S)
        try:  # this repo's own mcp.json line, not a sibling subproject's
            line = "/" + f.resolve().relative_to(top.resolve()).as_posix()
        except ValueError:
            line = None
        owned_file = bool(block) and line in block[1].splitlines()
        new = None if owned_file and data == {"mcpServers": {}} else json.dumps(data, indent=2) + "\n"
        return Change(f, "remove" if new is None else "update", old, new, f"{note} `{ours}`")
    if ours is not None:
        return Change(f, "unchanged", old, note=f"{note} `{ours}` (registered)")
    if free is None:
        return Change(f, "refuse", old, note=f"{note}: other servers already use both `waypoint` and `waypoint-local`")
    command = clis.server_command()
    if command is None:
        return Change(f, "refuse", old, note=f"{note}: {clis.NO_COMMAND}")
    servers[free] = {"type": "local", "command": command, "args": ["mcp"], "tools": ["*"]}
    return Change(f, "create" if old is None else "update", old, json.dumps(data, indent=2) + "\n",
                  f"{note} `{free}`" + ("" if free == "waypoint" else " (`waypoint` is another server)"))


def plan_mcp(cli_id, repo: Path, home: Path, register=True) -> Change:
    """Plan repo scoped MCP registration; only external CLI commands may touch the real home."""
    if cli_id == "copilot-cli":
        return plan_copilot_mcp(repo, register, home)
    f, _ = mcp_location(cli_id, repo, home)
    if not rules._real_home(home):
        return Change(f, "unchanged", note=f"{clis.CLIS[cli_id].name} MCP server for {repo.name}: not registered (only for your real home folder)")
    reserved = ()
    if register:
        user, err = clis.mcp_entries(clis.mcp_file(cli_id, home))
        if err:
            return Change(f, "refuse", note=f"{clis.CLIS[cli_id].name} MCP server for {repo.name}: can't check user entries ({err})")
        reserved = tuple(n for n, value in user.items() if value is not None and not clis.is_ours(value))
    return rules.mcp_change(cli_id, f, register, project=repo, reserved=reserved)


def plan_connect(cli_id, repo: Path, lb: Path, home: Path, mcp=True, copy=False, allow_tracked=False,
                 writable_root=True) -> list:
    """Plan the repo rule and MCP entry, Codex trust guidance and git exclude entries."""
    name = clis.CLIS[cli_id].name
    changes = [plan_rule(cli_id, repo, lb, copy)]
    if cli_id == "codex":
        if mcp or writable_root:
            cfg = plan_codex_config(repo, lb, mcp=mcp, writable_root=writable_root, home=home)
            changes.append(cfg)
            trusted = codex_trusted(repo, home)
            if cfg.action in rules.WRITES and not trusted:
                changes.append(Change(clis.config_dir("codex", home) / "config.toml", "unchanged",
                                      note=f"Codex reads {repo.name}/.codex/config.toml only in folders you trust"
                                           + ("" if trusted is False else " (couldn't tell)")
                                           + f": start Codex in {repo} once and trust it"))
    elif mcp:
        changes.append(plan_mcp(cli_id, repo, home))
    if rules.check(home, lb, (cli_id,)).get(cli_id, "missing") != "missing":
        changes.append(Change(rules.global_rule_file(cli_id, home), "unchanged",
                              note=f"{name} also has the rule globally, so in {repo.name} it reads it twice. Keep one: waypoint disconnect {cli_id}"))
    for i, ch in enumerate(changes):
        if ch.action not in ("unchanged", "refuse") and rules.inside(ch.target, repo) and ch.target.exists() \
                and not allow_tracked and git_tracked(repo, ch.target):
            path = ch.target.relative_to(repo).as_posix()
            reason = (f"git isn't available to check whether {path} is tracked" if not shutil.which("git")
                      else f"{path} is tracked by git")
            changes[i] = Change(ch.target, "refuse", ch.old,
                                note=f"{ch.note}: {reason}; pass --allow-tracked to change it anyway")
    created = [ch.target for ch in changes
               if ch.action == "create" or (ch.action == "link" and ch.old is None)
               or (ch.action == "register" and ch.cwd is not None and rules.inside(ch.target, repo)
                   and not ch.target.exists())]
    backups = [ch.target.with_name(ch.target.name + ".bak-*") for ch in changes
               if ch.action in rules.WRITES and rules.inside(ch.target, repo)]
    return changes + plan_exclude(repo, add=created + backups)


def plan_disconnect(repo: Path, home: Path, tools=None, mcp=True) -> list:
    """Plan removal of the per-project rules and MCP entries installed by connect."""
    changes = []
    for cli_id in tools or RULE_FILES:
        f, note = rule_file(cli_id, repo), f"{clis.CLIS[cli_id].name} rule in {repo.name}"
        if f.is_symlink():
            if _our_link(f):
                changes.append(Change(f, "link", rules.link_state(f), None, note + " (a link)"))
        elif cli_id == "claude-code":
            old, err = rules._read(f)
            if err:
                changes.append(Change(f, "refuse", note=f"{note}: {err}"))
            elif old is not None and rules.OWNED in old:
                changes.append(Change(f, "remove", old, None, note))
        else:
            ch = rules._block_change(f, None, note, drop_empty=True)
            if ch.action != "unchanged":
                changes.append(ch)
        if mcp:
            m = plan_codex_config(repo, None, remove=True, home=home) if cli_id == "codex" else plan_mcp(cli_id, repo, home, False)
            if m.action != "unchanged" or "by hand" in m.note:
                changes.append(m)
    managed = [repo / RULE_FILES[c] for c in (tools or RULE_FILES)]
    managed += [repo / {"codex": ".codex/config.toml", "gemini-cli": ".gemini/settings.json",
                        "copilot-cli": ".github/mcp.json"}[c]
                for c in (tools or RULE_FILES) if c != "claude-code"]
    exclude_changes = plan_exclude(repo, drop=managed)
    # Un-exclude only once every removal in the repo happened: a refused one keeps Waypoint's paths out of git.
    deps = tuple(c for c in changes if (c.run and c.cwd == repo)
                 or (c.action in rules.WRITES and not c.run and rules.inside(c.target, repo)))
    for x in exclude_changes:
        if deps:
            x.after = deps[0] if len(deps) == 1 else deps
    return changes + exclude_changes


def plan_refresh(lb: Path) -> list:
    """Plan updates for installed per-project rules that drifted from the logbook."""
    out = []
    for _, repo in repos(lb):
        for cli_id in RULE_FILES:
            state, method = rule_state(cli_id, repo, lb)
            if method != "none" and state != "in-sync":
                ch = plan_rule(cli_id, repo, lb, copy=method == "copy")
                if ch.action != "unchanged":
                    out.append(ch)
    return out


def plan_uninstall_all(lb: Path, home: Path) -> list:
    """Plan removal of every per-project install in tracked and archived projects."""
    return [ch for _, repo in repos(lb) for ch in plan_disconnect(repo, home)]
