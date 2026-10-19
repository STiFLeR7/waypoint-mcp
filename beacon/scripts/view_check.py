"""Check the chart view (beacon/src/view.ts) in Chromium against a stub MCP Apps host.

Local only (not CI): needs `pip install playwright` and `python -m playwright install chromium`.
Run from anywhere: python beacon/scripts/view_check.py
"""
import json
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BEACON = Path(__file__).resolve().parent.parent
VIEW = subprocess.run(["node", "--input-type=module", "-e",
                       "import { VIEW_HTML } from './src/view.ts'; process.stdout.write(VIEW_HTML)"],
                      cwd=BEACON, check=True, capture_output=True, text=True, encoding="utf-8").stdout

RESULT = {"content": [{"type": "text", "text": "Chart taken"}], "structuredContent": {
    "chart_generated": "2026-10-19T08:12:00.000Z", "data_complete": True,
    "kpi": {"tracked": 2, "attention": 1, "velocity": 1, "stale": 0, "untracked": 0, "unlogged": 3, "behind": 1},
    "projects": [
        {"name": "alpha", "status": "active", "idle": 0, "last_tool": "codex", "unlogged": 3, "now_behind": True,
         "summary": "s", "next": "<img src=x onerror=\"document.title='pwned'\">raise the mast"},
        {"name": "beta", "status": "paused", "idle": 40, "last_tool": None, "unlogged": None, "now_behind": None,
         "summary": None, "next": None}]}}

HOST = """<!DOCTYPE html><html><body>
<iframe id="v" sandbox="allow-scripts" style="width:600px;height:200px;border:0"></iframe>
<script>
const f = document.getElementById('v'); window.log = [];
const RESULT = __RESULT__;
window.addEventListener('message', e => {
  if (e.source !== f.contentWindow) return;
  const m = e.data; window.log.push(m);
  const to = msg => f.contentWindow.postMessage(Object.assign({jsonrpc: '2.0'}, msg), '*');
  if (m.method === 'ui/initialize') to({id: m.id, result: {protocolVersion: '2026-01-26',
      hostInfo: {name: 'stub', version: '1'}, hostCapabilities: {}, hostContext: {theme: 'dark'}}});
  if (m.method === 'ui/notifications/initialized') {
    to({method: 'ui/notifications/tool-input', params: {arguments: {}}});
    to({method: 'ui/notifications/tool-result', params: RESULT});
    setTimeout(() => to({id: 99, method: 'ui/resource-teardown', params: {reason: 'test'}}), 300);
  }
});
f.srcdoc = __VIEW__;
</script></body></html>"""


def main():
    host = HOST.replace("__RESULT__", json.dumps(RESULT)).replace("__VIEW__", json.dumps(VIEW).replace("</", "<\\/"))
    fails = 0
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")  # the installed Chrome; no Playwright download needed
        except Exception:
            browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.set_content(host)
        page.wait_for_function("window.log.some(m => m.id === 99 && 'result' in m)", timeout=10000)
        page.wait_for_function("window.log.some(m => m.method === 'ui/notifications/size-changed' && m.params.height > 0)", timeout=10000)
        log = page.evaluate("window.log")
        frame = page.frame_locator("#v")
        text = frame.locator("body").inner_text()
        methods = [m.get("method") for m in log]
        checks = {
            "handshake first": methods[:2] == ["ui/initialize", "ui/notifications/initialized"],
            "renders both projects": "alpha" in text and "beta" in text,
            "kpi labels": "Now behind log" in text and "unlogged" in text,
            "unlogged signal": "3 sessions not logged" in text,
            "text only": frame.locator("img").count() == 0 and "<img" in text and frame.locator("title").inner_text() != "pwned",
            "nulls handled": "No next action recorded." in text and "Logging unknown" in text,
            "dark theme": frame.locator("html").get_attribute("data-theme") == "dark",
            "size reported": any(m.get("method") == "ui/notifications/size-changed" and m["params"]["height"] > 0
                                 for m in log),
            "teardown answered": any(m.get("id") == 99 and m.get("result") == {} for m in log),
            "no errors": not errors,
        }
        browser.close()
    for name, good in checks.items():
        print(f"{'ok  ' if good else 'FAIL'} {name}")
        fails += not good
    if errors:
        print("errors:", *errors, sep="\n  ")
    print(f"{'PASSED' if not fails else 'FAILED'}: {fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
