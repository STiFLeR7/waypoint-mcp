"""Codex: threads table in the newest compatible ~/.codex/state_N.sqlite (read-only)."""
import re
import sqlite3
from pathlib import Path

from . import plausible_ms, health, tool_dir

NEEDED = {"cwd", "title", "updated_at_ms", "archived"}


def candidates(home: Path):
    dbs = []
    for f in tool_dir(home, "CODEX_HOME", ".codex").glob("state_*.sqlite"):
        m = re.fullmatch(r"state_(\d+)\.sqlite", f.name)
        if m:
            dbs.append((int(m[1]), f))
    return [f for _, f in sorted(dbs, reverse=True)]


def read(home: Path, place):
    """place: cwd -> folder key or None (paths.placer)."""
    dbs = candidates(home)
    if not dbs:
        return [], health("absent")
    errors, read_errors = [], []
    for db in dbs:  # highest N first; fall back if the schema doesn't fit
        sessions, skipped = [], 0
        try:
            con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
            try:
                cols = {r[1] for r in con.execute("pragma table_info(threads)")}
                if not NEEDED <= cols:
                    errors.append(f"{db.name}: missing {sorted(NEEDED - cols)}")
                    continue
                id_col = "id" if "id" in cols else "null"
                for tid, cwd, title, upd in con.execute(f"select {id_col}, cwd, title, updated_at_ms from threads"):
                    if not isinstance(cwd, str) or not plausible_ms(upd):
                        skipped += 1
                        continue
                    folder = place(cwd)
                    if folder:
                        text = title.strip() if isinstance(title, str) else ""
                        first = text.splitlines()[0][:80] if text else ""
                        sessions.append({"tool": "codex", "folder": folder, "ts": upd / 1000, "title": first, "id": tid})
            finally:
                con.close()
        except sqlite3.Error as e:
            errors.append(f"{db.name}: {e}")
            read_errors.append(f"{db.name}: {e}")
            continue
        detail = "; ".join(read_errors + [f"{skipped} rows skipped"] if skipped else read_errors)
        state = health("degraded", detail) if detail else health("ok", db.name)
        return sessions, state
    return [], health("unreadable", "; ".join(errors))
