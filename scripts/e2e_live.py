"""Read-only live check (not run in CI): a real logbook, copied, charted and read through the MCP server.

    python scripts/e2e_live.py [LOGBOOK]      (default: WAYPOINT_LOGBOOK, else the logbook `setup` remembered)

The logbook is copied to a temp folder (without .git); `waypoint fix` and an MCP take_fix/read_chart session run
against the copy with your real tool history (read-only). The original is fingerprinted before and after and must
be unchanged. Checks the chart against LOGBOOK-SPEC §5 and the MCP replies against §8.2, then prints a summary.
Exit code 1 if any check fails.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from waypoint import config  # noqa: E402

TOP = {"schema", "generated", "config", "sources", "rules", "kpi", "projects", "archived", "untracked", "pulse", "feed"}
KPI = {"tracked", "attention", "velocity", "stale", "untracked", "unlogged"}
PROJECT = {"name", "file", "path", "status", "summary", "docs", "done", "next", "attention", "last", "last_tool",
           "idle", "claude", "codex", "git", "stale", "unlogged", "logs"}
MCP_PROJECT = {"name", "status", "idle", "last_tool", "unlogged", "summary", "next", "done", "attention"}
failures, warnings = [], []


def check(ok, what):
    if not ok:
        failures.append(what)
    return ok


def fingerprint(root: Path):
    return {str(f.relative_to(root)): (f.stat().st_size, f.stat().st_mtime_ns)
            for f in root.rglob("*") if f.is_file() and ".git" not in f.relative_to(root).parts}


def run(*args, stdin=None, cwd):
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    env.pop("WAYPOINT_LOGBOOK", None)
    return subprocess.run([sys.executable, "-m", "waypoint", *args], input=stdin, capture_output=True, cwd=cwd,
                          env=env, timeout=120)


def check_chart(d):
    check(d.get("schema") == 1, "schema is 1")
    check(set(d) >= TOP, f"top-level keys: missing {sorted(TOP - set(d))}")
    check(set(d["kpi"]) == KPI, f"kpi keys: {sorted(d['kpi'])}")
    live, k = d["projects"], d["kpi"]
    for p in live:
        check(set(p) >= PROJECT, f"{p.get('name')}: missing {sorted(PROJECT - set(p))}")
    check(k["tracked"] == len(live), "kpi.tracked == len(projects)")
    check(k["untracked"] == len(d["untracked"]), "kpi.untracked == len(untracked)")
    check(k["unlogged"] == sum(1 for p in live if p["unlogged"]), "kpi.unlogged == projects with unlogged > 0")
    check(k["attention"] == sum(len(p["attention"]) for p in live), "kpi.attention == Attention lines")
    check(k["stale"] == sum(p["stale"] for p in live), "kpi.stale == stale projects")
    week = d["generated"] - 7 * 86400
    check(k["velocity"] == sum(1 for p in live if p["status"] == "active" and p["last"] >= week), "kpi.velocity")
    check(len(d["pulse"]) == d["config"]["pulse_days"], "pulse has pulse_days keys")
    check(max(d["pulse"]) == date.fromtimestamp(d["generated"]).isoformat(), "pulse ends today")
    check(len(d["feed"]) <= 1000, "feed within feed_max cap")
    check(d["feed"] == sorted(d["feed"], key=lambda e: (-e["ts"], e["project"], e["tool"])), "feed order (§5.1)")
    check(all(e["kind"] in ("session", "log") for e in d["feed"]), "feed kinds")
    order = {s: i for i, s in enumerate(("active", "occasional", "paused", "done"))}
    keys = [(order.get(p["status"], 4), -p["last"], p["name"]) for p in live]
    check(keys == sorted(keys), "projects order (§5.1)")
    check(all(p["unlogged"] >= 0 and (p["idle"] is None or p["idle"] >= 0) for p in live), "nonnegative counts")
    names = {p["name"] for p in live} | {a["name"] for a in d["archived"]}
    check(len(names) == len(live) + len(d["archived"]), "project names unique")


def mcp_session(lb, names, cwd):
    msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "take_fix", "arguments": {}}}]
    msgs += [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": "read_chart", "arguments": {"project": n}}}
             for i, n in enumerate(names, start=2)]
    p = run("mcp", "--logbook", str(lb), stdin="".join(json.dumps(m) + "\n" for m in msgs).encode(), cwd=cwd)
    raw = p.stdout.decode("utf-8")
    replies = {r["id"]: r for r in map(json.loads, raw.splitlines())}  # stdout must be protocol only
    check(p.returncode == 0, f"mcp exit code {p.returncode}")
    check(sorted(replies) == list(range(len(names) + 2)), "one MCP reply per request")
    texts = "\n".join(r["result"]["content"][0]["text"] for r in replies.values() if "content" in r.get("result", {}))
    return texts.lower(), replies


def main():
    src, source = config.find_logbook(sys.argv[1] if len(sys.argv) > 1 else None)
    src = src.resolve()
    if not (src / "projects").is_dir():
        sys.exit(f"{src} (from {source}) is not a logbook")
    before = fingerprint(src)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "logbook"
        shutil.copytree(src, copy, ignore=shutil.ignore_patterns(".git", ".waypoint.lock"))
        project_bytes = {f: f.read_bytes() for f in (copy / "projects").rglob("*.md")}

        p = run("fix", "--logbook", str(copy), cwd=tmp)
        fix_out = p.stdout.decode("utf-8", "replace").strip()
        if not check(p.returncode == 0, f"fix exit code {p.returncode}: {p.stderr.decode(errors='replace')[-300:]}"):
            return report(src, None, fix_out)
        d = json.loads((copy / "dashboard.json").read_text(encoding="utf-8"))
        check_chart(d)
        check({f: f.read_bytes() for f in (copy / "projects").rglob("*.md")} == project_bytes,
              "fix left every project file unchanged")

        names = [x["name"] for x in d["projects"]]
        raw, replies = mcp_session(copy, names, tmp)
        fix = json.loads(replies[1]["result"]["content"][0]["text"])
        check(fix["kpi"]["tracked"] == d["kpi"]["tracked"], "MCP take_fix kpi.tracked matches the chart")
        check([x["name"] for x in fix["projects"]] == names, "MCP project list matches the chart")
        attention_opt_in = config.load(copy).mcp_attention
        for x in fix["projects"]:
            extra = set(x) - MCP_PROJECT
            check(not extra, f"take_fix {x['name']}: unexpected fields {sorted(extra)}")
            check(("attention" in x) == attention_opt_in, f"take_fix {x['name']}: Attention only on opt-in")
        check(set(fix) == {"chart_generated", "data_complete", "kpi", "projects", "note"}, f"take_fix keys: {sorted(fix)}")
        for i, n in enumerate(names, start=2):
            r = replies[i]["result"]
            if check(not r.get("isError"), f"read_chart {n} failed: {r['content'][0]['text'][:120]}"):
                body = json.loads(r["content"][0]["text"])
                check(set(body) == {"chart_generated", "data_complete", "project", "logs", "more_logs", "note"},
                      f"read_chart {n} keys: {sorted(body)}")
                check(set(body["project"]) <= MCP_PROJECT, f"read_chart {n}: unexpected project fields")
        # paths and session titles never appear (§8.2); owner text that quotes a path is only a warning
        paths = {str(copy), str(src), str(Path.home()), *(x["path"] for x in d["projects"] if x["path"]),
                 *(u["path"] for u in d["untracked"])}
        owner = " ".join(json.dumps([x.get(f) for f in ("summary", "next", "done", "attention")])
                         for x in d["projects"]) + json.dumps([e["text"] for p in d["projects"] for e in p["logs"]])
        owner = owner.lower()
        for s in sorted(paths):
            for form in {s, s.replace("\\", "/")}:
                needle = json.dumps(form.lower(), ensure_ascii=False)[1:-1]
                if needle in raw:
                    (warnings if needle in owner else failures).append(f"path in MCP output: {form}")
        titles = {e["text"] for e in d["feed"] if e["kind"] == "session" and len(e["text"]) >= 20}
        titles -= {e["text"] for e in d["feed"] if e["kind"] == "log"}
        esc = lambda t: json.dumps(t.lower(), ensure_ascii=False)[1:-1]
        leaked = [t for t in titles if esc(t) in raw and esc(t) not in owner]
        check(not leaked, f"session titles in MCP output: {len(leaked)}")
        check(fingerprint(src) == before, "the original logbook is unchanged")
        return report(src, d, fix_out, fix)


def report(src, d, fix_out, fix=None):
    print(f"Live check of a copy of {src}")
    print(f"  fix: {fix_out}")
    if d:
        print(f"  kpi: {json.dumps(d['kpi'])}")
        print("  sources: " + ", ".join(f"{k}={v['state']}" for k, v in d["sources"].items()))
        print("  rules (checked against the copy's own path, so drifted is expected): "
              + ", ".join(f"{k}={v}" for k, v in d["rules"].items()))
        print(f"  projects {len(d['projects'])}, archived {len(d['archived'])}, untracked {len(d['untracked'])}, "
              f"feed {len(d['feed'])}, pulse days {len(d['pulse'])}")
    if fix:
        print(f"  MCP: take_fix + {len(d['projects'])} read_chart, data_complete={fix['data_complete']}")
    for w in warnings:
        print(f"  WARN {w}")
    for f in failures:
        print(f"  FAIL {f}")
    print("PASSED" if not failures else f"FAILED ({len(failures)})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
