"""Fact sources (LOGBOOK-SPEC §4). Each reader returns (sessions, health) and never raises on bad data.

A session is {"tool", "folder", "ts", "title"}; folder is already mapped to a top-level project folder.
Health is {"state": "ok|absent|degraded|unreadable", "detail": str}.
"""
import math
import os
from pathlib import Path


def health(state, detail=""):
    return {"state": state, "detail": detail}


def summarize(skipped, read_any):
    if not read_any and skipped:
        return health("unreadable", f"{skipped} unreadable records")
    return health("degraded", f"{skipped} records skipped") if skipped else health("ok")


def tool_dir(home: Path, env: str, default: str) -> Path:
    """A tool's config folder: its env var (CLAUDE_CONFIG_DIR, CODEX_HOME, ...) when Waypoint runs against the
    real home, else <home>/<default>. An explicit --home (tests, other machines' copies) ignores the env var."""
    value = os.environ.get(env)
    if value and Path(home).expanduser().resolve() == Path.home().resolve():
        return Path(value).expanduser()
    return Path(home) / default


MAX_MS = 253402300799000  # 9999-12-31: anything later can't be formatted as a date


def plausible_ms(x):
    """An epoch-milliseconds value we can safely turn into a date: a real number (not bool), 0 < x <= year 9999.
    Huge JSON integers can't overflow this check."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(float(x)) and 0 < x <= MAX_MS
    except OverflowError:
        return False
