"""waypoint.toml loading (LOGBOOK-SPEC §6)."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MAX_DAYS = 3650
MAX_PULSE_DAYS = 365
MAX_FEED = 1000


class ConfigError(ValueError):
    """A user-correctable waypoint.toml error."""


@dataclass
class Config:
    roots: list
    stale_days: dict = field(default_factory=lambda: {"active": 21, "occasional": 60})
    untracked_days: int = 30
    pulse_days: int = 35
    feed_max: int = 60
    mcp_attention: bool = False  # [mcp] include_attention: Attention lines in local MCP results (§8.2)


def load(logbook: Path) -> Config:
    data = {}
    f = logbook / "waypoint.toml"
    if f.exists():
        try:
            data = tomllib.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as e:
            raise ConfigError(f"invalid {f}: {e}") from e
    roots = data.get("roots", [str(logbook.resolve().parent)])
    if not isinstance(roots, list) or not roots or any(not isinstance(r, str) or not r.strip() for r in roots):
        raise ConfigError(f"{f}: roots must be a nonempty list of nonempty path strings")
    cfg = Config(roots=roots)
    stale = data.get("stale_days", {})
    if not isinstance(stale, dict) or set(stale) - {"active", "occasional"}:
        raise ConfigError(f"{f}: stale_days may contain only active and occasional")
    for k, value in stale.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"{f}: stale_days.{k} must be a nonnegative integer")
        cfg.stale_days[k] = value
    for k, maximum in (("untracked_days", MAX_DAYS), ("pulse_days", MAX_PULSE_DAYS), ("feed_max", MAX_FEED)):
        value = data.get(k, getattr(cfg, k))
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ConfigError(f"{f}: {k} must be an integer from 1 to {maximum}")
        setattr(cfg, k, value)
    mcp = data.get("mcp", {})
    if not isinstance(mcp, dict) or ("include_attention" in mcp and not isinstance(mcp["include_attention"], bool)):
        raise ConfigError(f"{f}: mcp.include_attention must be true or false")
    cfg.mcp_attention = mcp.get("include_attention") is True
    return cfg


def user_dir() -> Path:
    """Waypoint's own per-user folder: %APPDATA%/waypoint on Windows, else $XDG_CONFIG_HOME/waypoint or ~/.config/waypoint."""
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "waypoint"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "waypoint"


def find_logbook(arg=None) -> tuple[Path, str]:
    """The logbook to use and where that came from: --logbook, then WAYPOINT_LOGBOOK, then the path `setup`
    remembered, then the current folder (§6 v1.1)."""
    if arg:
        return Path(arg).expanduser(), "--logbook"
    if os.environ.get("WAYPOINT_LOGBOOK"):
        return Path(os.environ["WAYPOINT_LOGBOOK"]).expanduser(), "WAYPOINT_LOGBOOK"
    f = user_dir() / "logbook"
    try:
        saved = f.read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved:
        return Path(saved).expanduser(), str(f)
    return Path("."), "the current folder"


def remember_plan(logbook: Path):
    """The text `remember_logbook` would write, or None when it is already remembered."""
    want = str(logbook.resolve()) + "\n"
    try:
        return None if (user_dir() / "logbook").read_text(encoding="utf-8") == want else want
    except OSError:
        return want


def remember_logbook(logbook: Path) -> Path:
    f = user_dir() / "logbook"
    want = remember_plan(logbook)
    if want is not None:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f".logbook.{os.getpid()}.tmp")
        tmp.write_text(want, encoding="utf-8")
        os.replace(tmp, f)
    return f
