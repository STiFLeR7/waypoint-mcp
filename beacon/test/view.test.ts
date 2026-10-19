// Static safety checks on the chart view (LOGBOOK-SPEC §8.1; design Part D). Behaviour: beacon/scripts/view_check.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { VIEW_HTML } from "../src/view.ts";

test("the view is one inline HTML document with its own strict CSP", () => {
  assert.match(VIEW_HTML, /^<!DOCTYPE html>/);
  assert.ok(VIEW_HTML.includes(`content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:"`));
  assert.ok(!/https?:\/\//i.test(VIEW_HTML), "no external URL");
  assert.ok(!/<script[^>]+src=|<link[^>]+href=|@import/i.test(VIEW_HTML), "no external script, style or import");
});

test("no HTML sinks: text only", () => {
  for (const sink of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"]) {
    assert.ok(!VIEW_HTML.includes(sink), sink);
  }
  assert.ok(VIEW_HTML.includes("textContent"));
});

test("speaks the MCP Apps bridge", () => {
  for (const m of ["ui/initialize", "ui/notifications/initialized", "ui/notifications/tool-result",
                   "ui/notifications/host-context-changed", "ui/resource-teardown", "ui/notifications/size-changed"]) {
    assert.ok(VIEW_HTML.includes(m), m);
  }
  assert.ok(VIEW_HTML.includes("e.source !== host"), "ignores messages from other windows");
});
