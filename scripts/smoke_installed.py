"""Installed-package smoke test (R0 exit criterion 2). Run with the installed `waypoint` and `waypoint-mcp` on PATH,
from any folder: everything happens in a temp folder with a temp --home, so real config is never touched.

    python scripts/smoke_installed.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, f"{args[:2]} exited {r.returncode}:\n{r.stdout}\n{r.stderr}"
    return r.stdout


def mcp(exe, lb, cwd, *calls):
    """One server session: initialize, then each tools/call; returns the replies to the calls by id."""
    msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
    msgs += [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": n, "arguments": a}}
             for i, (n, a) in enumerate(calls, start=2)]
    r = subprocess.run([exe, "--logbook", str(lb)], input="".join(json.dumps(m) + "\n" for m in msgs), cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    replies = {m["id"]: m for m in map(json.loads, r.stdout.splitlines())}  # stdout must be protocol only
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert {t["name"] for t in replies[1]["result"]["tools"]} == {"take_fix", "read_chart", "log_watch", "status"}
    return [replies[i]["result"] for i in range(2, 2 + len(calls))]


def main():
    exe = {n: shutil.which(n) for n in ("waypoint", "waypoint-mcp")}
    assert all(exe.values()), f"not on PATH: {[n for n, p in exe.items() if not p]}"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        home, code, lb, elsewhere = base / "home", base / "code", base / "logbook", base / "elsewhere"
        for d in (home, code / "alpha", elsewhere):
            d.mkdir(parents=True)
        out = run(exe["waypoint"], "--version", cwd=elsewhere)
        assert "(logbook spec v1.2)" in out, out
        run(exe["waypoint-mcp"], "--help", cwd=elsewhere)
        run(exe["waypoint"], "setup", "--yes", "--logbook", str(lb), "--root", str(code), "--no-mcp", "--home", str(home),
            cwd=elsewhere)
        assert (lb / "projects" / "_template.md").exists()
        run(exe["waypoint"], "add", str(code / "alpha"), "--logbook", str(lb), cwd=elsewhere)
        assert "Dashboard refreshed" in run(exe["waypoint"], "fix", "--logbook", str(lb), "--home", str(home), cwd=elsewhere)
        page = (lb / "chart.html").read_text(encoding="utf-8")  # the template shipped as package data
        assert '<script type="application/json" id="waypoint-data">{"schema":1,' in page, page[:200]

        ok, bad_tool, bad_project, too_long, fix = mcp(exe["waypoint-mcp"], lb, elsewhere,
            ("log_watch", {"project": "alpha", "summary": "smoke test entry", "tool": "codex"}),
            ("log_watch", {"project": "alpha", "summary": "x", "tool": "not-a-tool"}),
            ("log_watch", {"project": "nope", "summary": "x", "tool": "codex"}),
            ("log_watch", {"project": "alpha", "summary": "x" * 2001, "tool": "codex"}),
            ("take_fix", {}))
        assert not ok.get("isError") and "Logged" in ok["content"][0]["text"], ok
        for r in (bad_tool, bad_project, too_long):
            assert r.get("isError"), r
        assert "smoke test entry" in (lb / "projects" / "alpha.md").read_text(encoding="utf-8")
        assert not fix.get("isError"), fix

        (lb / ".waypoint.lock").write_text("12345", encoding="utf-8")  # another writer holds the lock
        [busy] = mcp(exe["waypoint-mcp"], lb, elsewhere, ("log_watch", {"project": "alpha", "summary": "y", "tool": "codex"}))
        assert busy.get("isError") and "busy" in busy["content"][0]["text"], busy
    print("installed-package smoke test passed.")


if __name__ == "__main__":
    sys.exit(main())
