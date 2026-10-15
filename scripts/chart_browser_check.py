"""Browser checks for chart.html (local only: needs Playwright and Microsoft Edge; CI does not run this).

    python scripts/chart_browser_check.py SHOTS [CHART.html ...]

Renders synthetic charts (hostile text, an empty logbook, the bare template) and checks each given chart.html at
1440, 390 and 320 px. Screenshots go to SHOTS.
"""
import json
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waypoint import chart  # noqa: E402

OK = {"state": "ok", "detail": ""}
HOSTILE = "</script><img src=x onerror=\"window.pwned=1\"><script>window.pwned=1</script> &amp;"


def synthetic(projects, **extra):
    return {"schema": 1, "generated": 1791969173, "config": {"stale_days": {"active": 21, "occasional": 60}},
            "sources": {k: OK for k in ("claude-code", "codex", "git", "logbook")}, "rules": {}, "kpi": {},
            "projects": projects, "archived": [], "untracked": [], "pulse": {}, "feed": [], **extra}


def main(argv):
    if not argv:
        sys.exit("usage: python scripts/chart_browser_check.py SCREENSHOT_DIR [chart.html ...]")
    shots, charts = Path(argv[0]), [Path(p) for p in argv[1:]]
    shots.mkdir(parents=True, exist_ok=True)
    passed = 0

    def check(cond, name):
        nonlocal passed
        assert cond, name
        passed += 1
        print("PASS", name, flush=True)

    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        tmp = Path(tmp)
        hostile = synthetic([{"name": HOSTILE, "status": "active", "summary": HOSTILE, "done": HOSTILE, "next": HOSTILE,
                              "attention": [HOSTILE], "docs": [HOSTILE], "last": 1791960000, "last_tool": "codex",
                              "idle": 0, "stale": False, "unlogged": 1,
                              "logs": [{"ts": 1791900000, "date": "2026-10-14", "tool": "codex", "text": HOSTILE}]}],
                            feed=[{"ts": 1791960000, "tool": "codex", "kind": "session", "project": HOSTILE, "text": HOSTILE}])
        (tmp / "hostile.html").write_text(chart.render_html(hostile), encoding="utf-8")
        (tmp / "empty.html").write_text(chart.render_html(synthetic([])), encoding="utf-8")
        (tmp / "bare.html").write_text(chart.resources.files("waypoint").joinpath("templates", "chart.html")
                                       .read_text(encoding="utf-8"), encoding="utf-8")
        browser = pw.chromium.launch(channel="msedge")
        errors, requests = [], []

        def page_for(path, width, height=900, **kw):
            page = browser.new_page(viewport={"width": width, "height": height}, **kw)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("request", lambda r: requests.append(r.url))
            page.goto(path.resolve().as_uri())
            return page

        page = page_for(tmp / "hostile.html", 1440)
        check(page.locator("#catalog .project-card").count() == 1, "hostile: one project")
        check(page.locator("img").count() == 0 and page.evaluate("window.pwned") is None, "hostile: text stays inert")
        check("<img src=x" in page.locator("#catalog").inner_text(), "hostile: shown as text")
        page.locator("#catalog .project-card").first.press("Enter")
        for tab in ("files", "sessions", "logs"):
            page.locator("#tab-" + tab).click()
            check(page.locator("img").count() == 0 and page.evaluate("window.pwned") is None, f"hostile: {tab} tab inert")
        page.focus("#tab-logs")
        page.keyboard.press("ArrowLeft")
        check(page.locator("#tab-sessions").get_attribute("aria-selected") == "true", "keyboard: arrow keys move tabs")
        page.keyboard.press("Escape")
        check(page.evaluate("document.activeElement.classList.contains('project-card')"), "keyboard: Esc restores focus")
        bad = {"name": "bad.json", "mimeType": "application/json", "buffer": b"{nope"}
        page.locator("#data-file").set_input_files(bad)
        page.wait_for_function("document.querySelector('#source-state').dataset.state === 'error'")
        check(page.locator("#catalog .project-card").count() == 1, "file: invalid JSON keeps the embedded data")
        picked = {"name": "picked.json", "mimeType": "application/json",
                  "buffer": json.dumps(synthetic([{"name": "picked", "status": "paused"}])).encode()}
        page.locator("#data-file").set_input_files(picked)
        page.wait_for_function("document.querySelector('#source-state').textContent.includes('picked.json')")
        check("picked" in page.locator("#catalog").inner_text(), "file: picker loads another dashboard")
        page.close()

        # review findings (Phase 6 Codex review)
        gen = 1791969173

        def proj(name, **kw):
            return {"name": name, "status": "active", "summary": "s", "done": "d", "next": "n", "attention": [],
                    "docs": [], "last": 0, "last_tool": "", "idle": None, "stale": False, "unlogged": 0, "logs": [], **kw}

        cases = synthetic([proj("alpha-fresh"),  # no history at all: last 0, idle null
                           proj("b-older", last=gen - 3600, idle=0, last_tool="codex"),
                           proj("c-newer", last=gen - 60, idle=0, last_tool="codex"),
                           proj("d-stale", last=gen - 30 * 86400, idle=30, stale=True, last_tool="git"),
                           proj("e-unknown", status="occasional", last=gen - 86400, idle=1, stale=None)],
                          config={"pulse_days": 35})
        (tmp / "cases.html").write_text(chart.render_html(cases), encoding="utf-8")
        page = page_for(tmp / "cases.html", 1440)
        names = page.locator(".project-title").all_inner_texts()
        check(names == ["c-newer", "b-older", "e-unknown", "d-stale", "alpha-fresh"],
              f"#6 recent sort: known last descending, unknown last, name only breaks ties ({names})")
        fresh = page.locator(".project-card", has_text="alpha-fresh")
        check("Activity unknown" in fresh.inner_text() and "days ago" not in fresh.inner_text(), "#1 last 0 is unknown, not ~20,000 days")

        def description(card):
            return card.evaluate("b => b.getAttribute('aria-describedby').split(' ').map(i => document.getElementById(i).textContent).join(' ')")
        check("Stale" in description(page.locator(".project-card", has_text="d-stale")), "#3 description includes Stale")
        check("Freshness unknown" in description(page.locator(".project-card", has_text="e-unknown")),
              "#3 description includes Freshness unknown")
        check("Tool unknown" in description(fresh), "#3 description includes tool")
        fresh.click()
        page.locator("#tab-files").click()
        check(page.locator("#project-panel").inner_text().strip() == "The project record lists no documentation."
              and page.locator("#project-panel h2").count() == 0, "#8 empty Docs panel says one thing once")
        page.locator("#tab-sessions").click()
        panel = page.locator("#project-panel").inner_text()
        check("No session titles in the recent feed" in panel and "untitled" in panel and "35 days" in panel,
              "#5 empty sessions state names the feed's limits")
        page.keyboard.press("Escape")
        # 2: a dataset that fails while rendering is rejected and the previous data, collection and filters stay
        page.locator("#collections button", has_text="Occasional").click()
        page.evaluate("() => { logsPanel = () => { throw Error('render failure') } }")
        page.locator("#data-file").set_input_files({"name": "breaks.json", "mimeType": "application/json",
                                                    "buffer": json.dumps(synthetic([proj("intruder")])).encode()})
        page.wait_for_function("document.querySelector('#source-state').dataset.state === 'error'")
        check(page.locator(".project-title").all_inner_texts() == ["e-unknown"]
              and page.locator("#collections [aria-current=page]").inner_text().startswith("Occasional")
              and page.locator("#collections button", has_text="All projects").inner_text().endswith("5"),
              "#2 rejected import keeps the previous data and controls")
        page.close()
        page = page_for(tmp / "cases.html", 1440)
        huge = synthetic([proj("huge", idle=1e308, last=1e308)])
        page.locator("#data-file").set_input_files({"name": "huge.json", "mimeType": "application/json",
                                                    "buffer": json.dumps(huge).encode()})
        page.wait_for_function("document.querySelector('#source-state').textContent.includes('huge.json')")
        check("Activity unknown" in page.locator("#catalog").inner_text(), "#2 out-of-range numbers become unknown")
        page.close()
        only_untracked = synthetic([], untracked=[{"path": "/code/scratch", "last": gen - 60, "title": "t"}])
        (tmp / "untracked.html").write_text(chart.render_html(only_untracked), encoding="utf-8")
        page = page_for(tmp / "untracked.html", 390)
        check(page.locator("#empty h2").inner_text() == "No tracked projects" and "see Untracked" in page.locator("#empty p").inner_text()
              and page.locator("#empty-reset").is_hidden(), "#4 untracked-only logbook is not 'filtered out'")
        page.close()

        page = page_for(tmp / "empty.html", 390)
        check(page.locator("#empty h2").inner_text() == "No projects yet", "empty logbook: honest empty state")
        page.screenshot(path=str(shots / "empty-390.png"))
        page.close()
        page = page_for(tmp / "bare.html", 1440)
        check(page.locator("#empty h2").inner_text() == "No project data", "bare template: no data, no samples")
        page.close()
        page = page_for(tmp / "hostile.html", 1440, reduced_motion="reduce")
        check(page.evaluate("getComputedStyle(document.querySelector('.project-card')).transitionDuration") == "0s",
              "reduced motion")
        page.close()

        for path in charts:
            raw = json.loads(path.read_text(encoding="utf-8").split('id="waypoint-data">', 1)[1].split("</script>", 1)[0])
            total, folders = len(raw["projects"]) + len(raw["archived"]), len(raw["untracked"])
            name = path.parent.name
            for width, height in ((1440, 960), (390, 844), (320, 800)):
                page = page_for(path, width, height)
                check(page.locator("#catalog .project-card").count() == total, f"{name} {width}: all {total} records")
                check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{name} {width}: fits")
                if width == 1440:
                    page.locator("#collections button", has_text="Untracked").click()
                    check(page.locator("#catalog .project-card").count() == folders, f"{name}: {folders} untracked folders")
                    page.locator("#collections button").first.click()
                page.screenshot(path=str(shots / f"{name}-{width}.png"))
                if width != 320:
                    page.screenshot(path=str(shots / f"{name}-{width}-full.png"), full_page=True)
                    page.locator("#catalog .project-card").first.click()
                    check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{name} {width}: detail fits")
                    page.screenshot(path=str(shots / f"{name}-{width}-detail.png"))
                page.close()
        browser.close()
    check(not errors, f"no browser errors: {errors}")
    check(all(u.startswith(("file:", "data:")) for u in requests), f"no network requests: {set(requests)}")
    print(f"{passed} checks passed")


if __name__ == "__main__":
    main(sys.argv[1:])
