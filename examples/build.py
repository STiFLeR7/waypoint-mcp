"""Rebuild examples/logbook's chart (DASHBOARD.md, dashboard.json, chart.html) from fixture sessions and a frozen clock.

    python examples/build.py           write the chart
    python examples/build.py --check   exit 1 if the committed chart differs (CI)

Run on Linux or macOS with git installed: the committed chart uses POSIX paths and UTC dates, and records git's
source health as ok.
"""
import difflib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waypoint import chart, clis, rules  # noqa: E402

HERE = Path(__file__).resolve().parent / "logbook"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc).timestamp()
PLACEHOLDER = "~/code"
DAY = 86400

# (tool, folder, days before NOW, title): the facts the example's AI tools left behind
SESSIONS = [
    ("claude-code", "aurora", 1, "Offline cache for the last forecast"),  # after the last log entry: an unlogged watch
    ("codex", "aurora", 4, "Unit switching for the hourly chart"),
    ("claude-code", "lighthouse", 24, "Release v1.0"),
    ("codex", "scratch", 2, "Try a CSV export"),  # no project file: uncharted
]


def build(base: Path):
    root, home, lb = base / "code", base / "home", base / "logbook"
    shutil.copytree(HERE, lb, ignore=shutil.ignore_patterns("DASHBOARD.md", "dashboard.json", "chart.html"))
    (lb / "projects" / "_archive").mkdir(exist_ok=True)
    for f in [lb / "waypoint.toml", *(lb / "projects").glob("*.md")]:
        f.write_text(f.read_text(encoding="utf-8").replace(PLACEHOLDER, str(root)), encoding="utf-8", newline="\n")
    claude_dir, codex_dir = home / ".claude" / "projects" / "example", home / ".codex"
    claude_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    con = sqlite3.connect(codex_dir / "state_5.sqlite")
    con.execute("create table threads (id, cwd, title, updated_at_ms, archived)")
    for i, (tool, folder, ago, title) in enumerate(SESSIONS):
        (root / folder).mkdir(parents=True, exist_ok=True)
        when = NOW - ago * DAY
        if tool == "claude-code":
            f = claude_dir / f"session-{i}.jsonl"
            f.write_text(json.dumps({"type": "user", "cwd": str(root / folder)}) + "\n"
                         + json.dumps({"type": "ai-title", "aiTitle": title}) + "\n", encoding="utf-8")
            os.utime(f, (when, when))
        else:
            con.execute("insert into threads values (?, ?, ?, ?, 0)", (f"thread-{i}", str(root / folder), title, int(when * 1000)))
    con.commit()
    con.close()
    (root / "tidewater").mkdir(parents=True, exist_ok=True)
    (lb / "GLOBAL-RULES.md").write_text(rules.render(lb), encoding="utf-8", newline="\n")
    rules.install(home, lb)  # the example's tools follow the current rule (rule files only; no CLI is run)
    clis.detected = lambda: []  # the chart must not depend on which AI CLIs this machine has installed
    d = chart.build(lb, home, now=NOW)
    chart.write(lb, d)
    # the Markdown escapes characters such as "_" that a random temp folder name may contain
    return {name: (lb / name).read_text(encoding="utf-8").replace(chart._md(str(root)), PLACEHOLDER)
            .replace(str(root), PLACEHOLDER) for name in ("DASHBOARD.md", "dashboard.json", "chart.html")}


def main(argv):
    os.environ["TZ"] = "UTC"
    time.tzset()  # POSIX only, like the committed chart
    with tempfile.TemporaryDirectory() as tmp:
        out = build(Path(os.path.realpath(tmp)))
    if "--check" in argv:
        stale = [n for n, text in out.items()  # bytes: a line-ending change is a difference too
                 if not (HERE / n).exists() or (HERE / n).read_bytes() != text.encode("utf-8")]
        for n in stale:
            old = (HERE / n).read_text(encoding="utf-8") if (HERE / n).exists() else ""
            sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), out[n].splitlines(True), n, f"{n} (rebuilt)"))
        if stale:
            sys.exit(f"examples/logbook is out of date ({', '.join(stale)}). Run: python examples/build.py")
        print("examples/logbook is up to date.")
        return
    for n, text in out.items():
        (HERE / n).write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {', '.join(out)} in {HERE}")


if __name__ == "__main__":
    main(sys.argv[1:])
