"""The AI CLIs Waypoint wires up (LOGBOOK-SPEC §7): one table, used by `setup`, `connect` and `rules`.

Supported and Preview CLIs get the rule and the local MCP server (registered through the CLI's own `mcp add`);
Snippet CLIs get printed config only: Waypoint writes nothing for them.
"""
import json
import os
import re
import shutil
import sys
import sysconfig
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .sources import tool_dir
from .paths import real

SERVER = {"command": "waypoint", "args": ["mcp"]}
NAMES = ("waypoint", "waypoint-local")  # waypoint-local: when `waypoint` is already someone else's server
NO_COMMAND = ("`waypoint` isn't on PATH and this installation's script wasn't found. Reinstall Waypoint "
              "(pip install waypoint-mcp) and try again")


@dataclass(frozen=True)
class Cli:
    id: str  # also its log_watch tool id
    name: str
    tier: str  # supported | preview | snippet
    exe: str | None = None
    rule: str | None = None  # managed-block file in the config dir (Claude Code has its own file; see rules.py)
    snippet: tuple = ()  # (config path under home, text) for snippet CLIs


def _json(obj):
    return json.dumps(obj, indent=2)


def _desktop_config():
    if sys.platform == "win32":
        return "%APPDATA%/Claude/claude_desktop_config.json"
    if sys.platform == "darwin":
        return "~/Library/Application Support/Claude/claude_desktop_config.json"
    return "~/.config/Claude/claude_desktop_config.json"


_STD = {"mcpServers": {"waypoint": SERVER}}

CLIS = {c.id: c for c in (
    Cli("claude-code", "Claude Code", "supported", "claude"),
    Cli("codex", "Codex", "supported", "codex", "AGENTS.md"),
    Cli("gemini-cli", "Gemini CLI", "preview", "gemini", "GEMINI.md"),
    Cli("copilot-cli", "GitHub Copilot CLI", "preview", "copilot", "copilot-instructions.md"),
    Cli("cursor", "Cursor", "snippet", snippet=("~/.cursor/mcp.json", _json({"mcpServers": {"waypoint": {"type": "stdio", **SERVER}}}))),
    Cli("windsurf", "Windsurf", "snippet", snippet=("~/.codeium/windsurf/mcp_config.json", _json(_STD))),
    Cli("kiro", "Kiro", "snippet", snippet=("~/.kiro/settings/mcp.json", _json(_STD))),
    Cli("cline", "Cline", "snippet", snippet=("~/.cline/data/settings/cline_mcp_settings.json", _json(_STD))),
    Cli("claude-desktop", "Claude Desktop", "snippet", snippet=(_desktop_config(), _json(_STD))),
    Cli("goose", "Goose", "snippet", snippet=("~/.config/goose/config.yaml",
        "extensions:\n  waypoint:\n    name: waypoint\n    type: stdio\n    cmd: waypoint\n    args: [mcp]\n"
        "    enabled: true\n    timeout: 300\n")),
    Cli("opencode", "opencode", "snippet", snippet=("~/.config/opencode/opencode.json",
        _json({"mcp": {"waypoint": {"type": "local", "command": ["waypoint", "mcp"], "enabled": True}}}))),
    Cli("amp", "Amp", "snippet", snippet=("~/.config/amp/settings.json", _json({"amp.mcpServers": {"waypoint": SERVER}}))),
)}
WIRED = [c for c in CLIS.values() if c.tier != "snippet"]

PLUGIN_REPO = "STiFLeR7/waypoint-mcp"
PLUGIN_STEPS = {  # spec B.2; the user runs these (network, trust prompts), Waypoint never does
    "claude-code": (f"claude plugin marketplace add {PLUGIN_REPO}", "claude plugin install waypoint@waypoint-mcp"),
    "codex": (f"codex plugin marketplace add {PLUGIN_REPO}", "codex plugin add waypoint@waypoint-mcp",
              "then in Codex: /hooks, and trust Waypoint's two hooks (again after each plugin update)"),
    "copilot-cli": (f"copilot plugin install {PLUGIN_REPO}",),
    "gemini-cli": (f"gemini extensions install https://github.com/{PLUGIN_REPO}",),
}


def plugin_text(cli_id):
    c = CLIS[cli_id]
    steps = "\n".join(f"  {s}" for s in PLUGIN_STEPS[cli_id])
    return (f"Now install the Waypoint plugin for {c.name} (it brings the hooks and the MCP server):\n{steps}\n"
            "The hooks run `waypoint`, so keep it on PATH.")


def config_dir(cli_id, home: Path) -> Path:
    if cli_id == "claude-code":
        return tool_dir(home, "CLAUDE_CONFIG_DIR", ".claude")
    if cli_id == "codex":
        return tool_dir(home, "CODEX_HOME", ".codex")
    if cli_id == "gemini-cli":
        return tool_dir(home, "GEMINI_CLI_HOME", ".") / ".gemini"  # the env var is the parent of .gemini
    if cli_id == "copilot-cli":
        return tool_dir(home, "COPILOT_HOME", ".copilot")
    raise KeyError(cli_id)


def mcp_file(cli_id, home: Path) -> Path:
    """The file where the CLI keeps its user-wide MCP registrations."""
    if cli_id == "claude-code":
        env = tool_dir(home, "CLAUDE_CONFIG_DIR", "")
        return env / ".claude.json"  # ~/.claude.json, or inside CLAUDE_CONFIG_DIR
    name = {"codex": "config.toml", "gemini-cli": "settings.json", "copilot-cli": "mcp-config.json"}[cli_id]
    return config_dir(cli_id, home) / name


def mcp_argv(cli_id, op, name="waypoint", command="waypoint", project=False):
    """argv after the executable that adds or removes the server `name` running `<command> mcp`. project: the CLI's
    per-repo scope (run it with the repo as working directory); only Claude Code (local) and Gemini CLI (project)."""
    scope = {"claude-code": ("--scope", "local" if project else "user"),
             "gemini-cli": ("--scope", "project" if project else "user")}.get(cli_id, ())
    if project and not scope:
        raise ValueError(f"{CLIS[cli_id].name} has no per-project `mcp add`")
    if op == "remove":
        return ("mcp", "remove", *scope, name)
    return ("mcp", "add", *scope, name, *(() if cli_id == "gemini-cli" else ("--",)), command, "mcp")


def mcp_entry(f: Path, name="waypoint", project: Path | None = None):
    """The registered server `name` in f: (entry dict or None, error or None). project: Claude Code's local scope,
    which ~/.claude.json keeps under projects[<repo path>] (code.claude.com/docs/en/mcp, "Local scope")."""
    try:
        text = f.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError) as e:
        return None, f"can't read it ({type(e).__name__})"
    try:
        data = tomllib.loads(text) if f.suffix == ".toml" else (_loads_json(text) if text.strip() else {})
    except (ValueError, tomllib.TOMLDecodeError) as e:
        return None, f"can't parse it ({type(e).__name__})"
    if project is not None:
        projects = data.get("projects") if isinstance(data, dict) else None
        key = real(str(project))
        data = next((v for k, v in projects.items() if real(k) == key), {}) if isinstance(projects, dict) else {}
    servers = data.get("mcp_servers" if f.suffix == ".toml" else "mcpServers") if isinstance(data, dict) else None
    entry = servers.get(name) if isinstance(servers, dict) else None
    return (entry if isinstance(entry, dict) or entry is None else {"value": entry}), None


def mcp_entries(f: Path, project: Path | None = None):
    """{name: entry or None} for both NAMES (one mcp_entry call each), or (None, error)."""
    out = {}
    for n in NAMES:
        entry, err = mcp_entry(f, n, project)
        if err:
            return None, err
        out[n] = entry
    return out, None


def entries_of(servers):
    """{name: entry or None} for NAMES from a parsed servers table; a non-table value counts as someone else's."""
    servers = servers if isinstance(servers, dict) else {}
    return {n: (e if isinstance(e, dict) or e is None else {"value": e}) for n in NAMES for e in [servers.get(n)]}


def choose(entries):
    """(ours, free): the name already holding Waypoint's own server, and the first name nobody uses."""
    ours = next((n for n in NAMES if entries.get(n) is not None and is_ours(entries[n])), None)
    free = next((n for n in NAMES if entries.get(n) is None), None)
    return ours, free


def installed_commands():
    """Absolute paths of this installation's `waypoint` script: the running Python's scripts folder and the per-user
    one. Used when `waypoint` isn't on PATH, so the registration still starts this Waypoint."""
    exe = "waypoint.exe" if os.name == "nt" else "waypoint"
    found = []
    for scheme in (None, f"{os.name}_user"):
        try:
            d = sysconfig.get_path("scripts", scheme) if scheme else sysconfig.get_path("scripts")
        except KeyError:
            continue
        if d and (Path(d) / exe).is_file() and str(Path(d) / exe) not in found:
            found.append(str(Path(d) / exe))
    return found


def server_command():
    """What to register: `waypoint` when it's on PATH (the tool resolves it at launch), else this installation's
    absolute script, else None. ponytail: our PATH stands in for the tool's; status flags an entry that doesn't resolve."""
    if shutil.which("waypoint"):
        return "waypoint"
    found = installed_commands()
    return found[0] if found else None


def is_ours(entry):
    """A local stdio registration of exactly `waypoint mcp` (or `waypoint-mcp`), as Waypoint registers it: the bare
    command, or an absolute path to the `waypoint` on PATH or to this installation's script. Remote entries and look-alikes are not ours."""
    cmd, args = entry.get("command"), entry.get("args") or []
    if not isinstance(cmd, str) or "url" in entry or entry.get("type") not in (None, "stdio", "local"):
        return False
    env = entry.get("env") or entry.get("environment") or {}
    if not isinstance(env, dict) or "WAYPOINT_LOGBOOK" in env:  # pinned elsewhere: not the registration we make
        return False
    if args not in ([], ["mcp"]) or (args == []) != (Path(cmd).stem.lower() == "waypoint-mcp"):
        return False
    name = Path(cmd).name.lower()
    if name not in ("waypoint", "waypoint.exe", "waypoint-mcp", "waypoint-mcp.exe"):
        return False
    if Path(cmd).name == cmd:  # bare: resolved on PATH at launch, exactly as Waypoint registers it
        return cmd in ("waypoint", "waypoint-mcp")
    target = os.path.normcase(os.path.realpath(cmd))
    found = shutil.which(Path(cmd).stem)
    candidates = ([found] if found else []) + (installed_commands() if Path(cmd).stem.lower() == "waypoint" else [])
    return any(os.path.normcase(os.path.realpath(c)) == target for c in candidates)


_JSON_COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)


def _loads_json(text):
    """JSON with // and /* */ comments, as Gemini CLI accepts in settings.json."""
    return json.loads(_JSON_COMMENTS.sub(lambda m: m[0] if m[0].startswith('"') else " ", text))  # keep tokens apart


def detected():
    """Wired CLIs whose executable is on PATH."""
    return [c.id for c in WIRED if shutil.which(c.exe)]


def describe(entry):
    """A registration, for the plan, without its URL or secrets."""
    kind = entry.get("type") or ("http" if "url" in entry else "stdio")
    return f"{kind} server" + (f" `{entry['command']}`" if isinstance(entry.get("command"), str) else "")


def snippet_text(c: Cli, logbook: Path):
    path, text = c.snippet
    return (f"{c.name} isn't wired automatically ({c.tier}); Waypoint writes nothing for it.\n"
            f"Add this to {os.path.expanduser(path)}, merging with what's already there:\n\n{text}\n\n"
            f"For logging, point its instructions at {(logbook / 'GLOBAL-RULES.md').as_posix()} "
            f"and log with tool id `{c.id}`.")
