"""Claude Code: transcripts in ~/.claude/projects, plus history.jsonl for sessions whose transcript is gone."""
import json
from pathlib import Path

from . import plausible_ms, health, summarize, tool_dir


def read(home: Path, place):
    """place: cwd -> folder key or None (paths.placer)."""
    base = tool_dir(home, "CLAUDE_CONFIG_DIR", ".claude")
    tdir, hist = base / "projects", base / "history.jsonl"
    if not tdir.exists() and not hist.exists():
        return [], health("absent")
    sessions, seen, skipped, read_any = [], set(), 0, False

    try:
        transcripts = list(tdir.glob("*/*.jsonl")) if tdir.exists() else []
    except OSError:
        transcripts, skipped = [], skipped + 1
    for f in transcripts:
        folder, title, found_cwd = None, "", False
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    # ponytail: substring prefilter keeps hundreds of MB of transcripts fast; only candidate lines are parsed
                    if '"cwd"' not in line and '"ai-title"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (ValueError, RecursionError):
                        skipped += 1
                        continue
                    if not isinstance(rec, dict):
                        skipped += 1
                        continue
                    if rec.get("type") == "ai-title" and isinstance(rec.get("aiTitle"), str):
                        title = rec["aiTitle"]  # last one wins, including an empty title
                    if not found_cwd and isinstance(rec.get("cwd"), str):
                        found_cwd = True
                        folder = place(rec["cwd"])
                        if not folder:
                            break  # no project and outside every root: skip the rest of this transcript
            mtime = f.stat().st_mtime
        except OSError:
            skipped += 1
            continue
        read_any = read_any or found_cwd
        if found_cwd:
            seen.add(f.stem)
        if folder:
            sessions.append({"tool": "claude-code", "folder": folder, "ts": mtime, "title": title, "id": f.stem})

    latest = {}
    if hist.exists():
        try:
            with hist.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                        sid, raw_ts, project = e["sessionId"], e["timestamp"], e["project"]
                    except (ValueError, RecursionError, KeyError, TypeError):
                        skipped += 1
                        continue
                    if not (isinstance(sid, str) and isinstance(project, str) and plausible_ms(raw_ts)):
                        skipped += 1
                        continue
                    read_any = True
                    if sid in seen:
                        continue
                    folder, ts = place(project), raw_ts / 1000
                    if folder and ts > latest.get(sid, (None, 0))[1]:
                        latest[sid] = (folder, ts)
        except OSError:
            skipped += 1
    sessions += [{"tool": "claude-code", "folder": fo, "ts": ts, "title": "", "id": sid} for sid, (fo, ts) in latest.items()]
    return sessions, summarize(skipped, read_any)
