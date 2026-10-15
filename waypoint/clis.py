"""The AI CLIs Waypoint wires up (LOGBOOK-SPEC §7): one table, used by `setup`, `connect` and `rules`.

Supported and Preview CLIs get the rule and the local MCP server (registered through the CLI's own `mcp add`);
Snippet CLIs get printed config only: Waypoint writes nothing for them.
"""
import json
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .sources import tool_dir

SERVER = {"command": "waypoint", "args": ["mcp"]}


@dataclass(frozen=True)
class Cli:
    id: str  # also its log_watch tool id
    name: str
    tier: str  # supported | preview | snippet
    exe: str | None = None
    rule: str | None = None  # managed-block file in the config dir (Claude Code has its own file; see rules.py)
    add: tuple = ()  # argv after the executable that registers SERVER
    remove: tuple = ()
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
    Cli("claude-code", "Claude Code", "supported", "claude", None,
        ("mcp", "add", "--scope", "user", "waypoint", "--", "waypoint", "mcp"), ("mcp", "remove", "--scope", "user", "waypoint")),
    Cli("codex", "Codex", "supported", "codex", "AGENTS.md",
        ("mcp", "add", "waypoint", "--", "waypoint", "mcp"), ("mcp", "remove", "waypoint")),
    Cli("gemini-cli", "Gemini CLI", "preview", "gemini", "GEMINI.md",
        ("mcp", "add", "--scope", "user", "waypoint", "waypoint", "mcp"), ("mcp", "remove", "--scope", "user", "waypoint")),
    Cli("copilot-cli", "GitHub Copilot CLI", "preview", "copilot", "copilot-instructions.md",
        ("mcp", "add", "waypoint", "--", "waypoint", "mcp"), ("mcp", "remove", "waypoint")),
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


def mcp_entry(f: Path):
    """The registered `waypoint` server in f: (entry dict or None, error or None)."""
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
    servers = data.get("mcp_servers" if f.suffix == ".toml" else "mcpServers") if isinstance(data, dict) else None
    entry = servers.get("waypoint") if isinstance(servers, dict) else None
    return (entry if isinstance(entry, dict) or entry is None else {"value": entry}), None


def is_ours(entry):
    """A local stdio registration of exactly `waypoint mcp` (or `waypoint-mcp`), as Waypoint registers it: the bare
    command, or an absolute path to the `waypoint` on PATH. Remote entries and look-alikes are not ours."""
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
    found = shutil.which(Path(cmd).stem)
    return bool(found) and os.path.normcase(os.path.realpath(found)) == os.path.normcase(os.path.realpath(cmd))


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
