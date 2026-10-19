// Protocol cases ported from port/tests/mcp.spec.ts, plus the §8 privacy allowlist and name checks.
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { handleMcp } from "../src/mcp.ts";
import { badOrigin } from "../src/auth.ts";
import { SENTINELS, client, dashboard, project, standardRepo } from "./fakes.ts";

const noLeak = (s: string) => {
  for (const x of SENTINELS) assert.ok(!s.includes(x), `leaked ${x}`);
};

describe("mcp protocol", () => {
  const { rpc } = client(standardRepo().gh);

  test("initialize negotiates a version and advertises tools", async () => {
    const res = await rpc({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26", capabilities: {} } });
    const j = await res.json();
    assert.equal(res.status, 200);
    assert.equal(j.result.protocolVersion, "2025-03-26");
    assert.ok(j.result.capabilities.tools);
    assert.equal(j.result.serverInfo.name, "waypoint-beacon");
    assert.match(j.result.instructions, /log_watch appends one log entry/);
  });

  test("an unsupported client version falls back to the latest supported", async () => {
    const j = await (await rpc({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "1999-01-01" } })).json();
    assert.equal(j.result.protocolVersion, "2025-11-25");
  });

  test("rejects invalid or unsupported MCP protocol headers before dispatch", async () => {
    const fake = standardRepo();
    for (const version of ["garbage", "2025-03-26-extra", "1999-01-01"]) {
      const res = await client(fake.gh).rpc(
        { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "log_watch", arguments: { project: "alpha", summary: "x" } } },
        { headers: { "content-type": "application/json", "mcp-protocol-version": version } },
      );
      assert.equal(res.status, 400, version);
      assert.equal((await res.json()).error.code, -32600);
    }
    assert.equal(fake.calls.length, 0);
  });

  test("a newer client's header on initialize still negotiates", async () => {
    const res = await client(standardRepo().gh).rpc(
      { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2099-01-01" } },
      { headers: { "content-type": "application/json", "mcp-protocol-version": "2099-01-01" } },
    );
    assert.equal(res.status, 200);
    assert.equal((await res.json()).result.protocolVersion, "2025-11-25");
  });

  test("accepts a supported MCP protocol header", async () => {
    const res = await client(standardRepo().gh).rpc(
      { jsonrpc: "2.0", id: 1, method: "ping" },
      { headers: { "content-type": "application/json", "mcp-protocol-version": "2025-06-18" } },
    );
    assert.equal(res.status, 200);
    assert.equal((await res.json()).result.resultType, "complete");
  });

  const PV = "io.modelcontextprotocol/protocolVersion";
  const CAPS = "io.modelcontextprotocol/clientCapabilities";
  const modernBody = (id: number, method: string, params: Record<string, unknown> = {}, version = "2026-07-28") => ({
    jsonrpc: "2.0", id, method, params: { ...params, _meta: { [PV]: version, [CAPS]: {} } },
  });
  const modernHeaders = (method: string, name?: string, version = "2026-07-28") => ({
    headers: {
      "content-type": "application/json", "mcp-protocol-version": version, "mcp-method": method,
      ...(name === undefined ? {} : { "mcp-name": name }),
    },
  });

  describe("mcp 2026-07-28", () => {
    test("server/discover answers with modern versions and cache hints", async () => {
      const res = await client(standardRepo().gh).rpc(modernBody(1, "server/discover"), modernHeaders("server/discover"));
      const j = await res.json();
      assert.equal(res.status, 200);
      assert.deepEqual(j.result.supportedVersions, ["2026-07-28"]);
      assert.equal(j.result.resultType, "complete");
      assert.equal(j.result.ttlMs, 3600000);
      assert.equal(j.result.cacheScope, "public");
      assert.equal(j.result._meta["io.modelcontextprotocol/serverInfo"].name, "waypoint-beacon");
      assert.match(j.result.instructions, /log_watch appends one log entry/);
    });

    test("modern resources/list and resources/read work; Mcp-Name must be the uri", async () => {
      const fake = standardRepo();
      const uri = "ui://waypoint/chart-v1";
      const list = await client(fake.gh).rpc(modernBody(1, "resources/list"), modernHeaders("resources/list"));
      assert.equal(list.status, 200);
      const read = await client(fake.gh).rpc(modernBody(2, "resources/read", { uri }), modernHeaders("resources/read", uri));
      assert.equal(read.status, 200);
      assert.match((await read.json()).result.contents[0].text, /^<!DOCTYPE html>/);
      const stray = await client(fake.gh).rpc(modernBody(3, "resources/read", { uri, name: "x" }), modernHeaders("resources/read", "x"));
      assert.equal(stray.status, 400);
      assert.equal(fake.calls.length, 0);
    });

    test("modern tools/list and tools/call work with matching headers", async () => {
      const { gh } = standardRepo();
      const list = await (await client(gh).rpc(modernBody(1, "tools/list"), modernHeaders("tools/list"))).json();
      assert.equal(list.result.cacheScope, "public");
      const call = await client(gh).rpc(modernBody(2, "tools/call", { name: "take_fix", arguments: {} }),
                                        modernHeaders("tools/call", "take_fix"));
      assert.equal(call.status, 200);
      assert.ok(JSON.parse((await call.json()).result.content[0].text).kpi);
    });

    test("a base64 Mcp-Name is decoded before comparing", async () => {
      const b64 = "=?base64?" + btoa("take_fix") + "?=";
      const res = await client(standardRepo().gh).rpc(modernBody(1, "tools/call", { name: "take_fix", arguments: {} }),
                                                      modernHeaders("tools/call", b64));
      assert.equal(res.status, 200);
    });

    test("a malformed or mismatched base64 Mcp-Name is 400 -32020, never a 500", async () => {
      const fake = standardRepo();
      const write = modernBody(1, "tools/call", { name: "log_watch", arguments: { project: "alpha", summary: "x" } });
      for (const name of ["=?base64?a?=", "=?base64?QQ=Q?=", "=?base64?" + btoa("take_fix") + "?=", "=?base64?/w==?="]) {
        const res = await client(fake.gh).rpc(write, modernHeaders("tools/call", name));
        assert.equal(res.status, 400, name);
        assert.equal((await res.json()).error.code, -32020, name);
      }
      assert.equal(fake.calls.length, 0);
    });

    test("a modern request without MCP-Protocol-Version is 400 -32020; a bad id is not echoed", async () => {
      const init = modernHeaders("tools/list");
      delete (init.headers as Record<string, string>)["mcp-protocol-version"];
      const res = await client(standardRepo().gh).rpc({ ...modernBody(1, "tools/list"), id: { x: 1 } }, init);
      assert.equal(res.status, 400);
      const j = await res.json();
      assert.equal(j.error.code, -32020);
      assert.equal(j.id, null);
    });

    test("a modern notification gets 202", async () => {
      const body = { jsonrpc: "2.0", method: "notifications/cancelled", params: { _meta: modernBody(0, "x").params._meta } };
      const res = await client(standardRepo().gh).rpc(body, modernHeaders("notifications/cancelled"));
      assert.equal(res.status, 202);
    });

    test("header mismatches are refused before dispatch, with no GitHub call", async () => {
      const fake = standardRepo();
      const write = modernBody(1, "tools/call", { name: "log_watch", arguments: { project: "alpha", summary: "x" } });
      for (const init of [modernHeaders("tools/call", "take_fix"), modernHeaders("tools/list", "log_watch"),
                          modernHeaders("tools/call", "log_watch", "2025-06-18"), modernHeaders("tools/call")]) {
        const res = await client(fake.gh).rpc(write, init);
        assert.equal(res.status, 400);
        assert.equal((await res.json()).error.code, -32020);
      }
      assert.equal(fake.calls.length, 0);
    });

    test("unsupported modern version, missing capabilities, unknown method, initialize", async () => {
      const { gh } = standardRepo();
      const v = await client(gh).rpc(modernBody(1, "tools/list", {}, "2099-01-01"), modernHeaders("tools/list", undefined, "2099-01-01"));
      assert.equal(v.status, 400);
      const vj = await v.json();
      assert.equal(vj.error.code, -32022);
      assert.deepEqual(vj.error.data.supported, ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]);
      const noCaps = { jsonrpc: "2.0", id: 2, method: "tools/list", params: { _meta: { [PV]: "2026-07-28" } } };
      const c = await client(gh).rpc(noCaps, modernHeaders("tools/list"));
      assert.equal(c.status, 400);
      assert.equal((await c.json()).error.code, -32602);
      const m = await client(gh).rpc(modernBody(3, "nope/method"), modernHeaders("nope/method"));
      assert.equal(m.status, 404);
      assert.equal((await m.json()).error.code, -32601);
      const i = await client(gh).rpc(modernBody(4, "initialize"), modernHeaders("initialize"));
      assert.equal(i.status, 404);
    });

    test("a modern header without _meta is the legacy fallback signal", async () => {
      const res = await client(standardRepo().gh).rpc({ jsonrpc: "2.0", id: 1, method: "tools/list" },
                                                      modernHeaders("tools/list"));
      assert.equal(res.status, 400);
      assert.equal((await res.json()).error.code, -32600);
    });
  });

  test("notifications get 202 and no body", async () => {
    const res = await rpc({ jsonrpc: "2.0", method: "notifications/initialized" });
    assert.equal(res.status, 202);
    assert.equal(await res.text(), "");
  });

  test("tools/list: four tools with the right annotations", async () => {
    const j = await (await rpc({ jsonrpc: "2.0", id: 2, method: "tools/list" })).json();
    const by = Object.fromEntries(j.result.tools.map((t: { name: string }) => [t.name, t]));
    assert.deepEqual(Object.keys(by).sort(), ["log_watch", "read_chart", "show_chart", "take_fix"]);
    for (const n of ["take_fix", "read_chart"]) {
      assert.equal(by[n].annotations.readOnlyHint, true);
      assert.equal(by[n].annotations.openWorldHint, false);
    }
    assert.deepEqual(
      { ...by.log_watch.annotations, title: undefined },
      { title: undefined, readOnlyHint: false, destructiveHint: false, openWorldHint: false },
    );
    assert.deepEqual(by.log_watch.inputSchema.required, ["project", "summary"]);
    assert.deepEqual(Object.keys(by.log_watch.inputSchema.properties).sort(), ["date", "done", "next", "project", "summary", "tool"]);
    assert.equal(by.log_watch.inputSchema.properties.tool.description, "Your own tool id: codex for Codex, claude-code for Claude Code, chatgpt for ChatGPT. Defaults to chatgpt.");
    assert.equal(by.log_watch.inputSchema.properties.done.maxLength, 300);
    assert.equal(by.log_watch.inputSchema.properties.next.maxLength, 300);
  });

  describe("chart view tool (MCP Apps)", () => {
    test("resources: advertised, listed and read with no GitHub call; unknown URI is -32602", async () => {
      const fake = standardRepo();
      const gh = fake.gh;
      const init = await (await client(gh).rpc({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18" } })).json();
      assert.deepEqual(init.result.capabilities.resources, {});
      const list = (await (await client(gh).rpc({ jsonrpc: "2.0", id: 2, method: "resources/list" })).json()).result;
      assert.deepEqual(list.resources.map((r: { uri: string; mimeType: string }) => [r.uri, r.mimeType]),
                       [["ui://waypoint/chart-v1", "text/html;profile=mcp-app"]]);
      assert.equal(list.cacheScope, "public");
      const read = (await (await client(gh).rpc({ jsonrpc: "2.0", id: 3, method: "resources/read", params: { uri: "ui://waypoint/chart-v1" } })).json()).result;
      const [c] = read.contents;
      assert.equal(c.mimeType, "text/html;profile=mcp-app");
      assert.match(c.text, /^<!DOCTYPE html>/);
      assert.deepEqual(c._meta, { ui: { prefersBorder: true } });
      assert.equal(read.ttlMs, 3600000);
      const bad = await (await client(gh).rpc({ jsonrpc: "2.0", id: 4, method: "resources/read", params: { uri: "ui://waypoint/other" } })).json();
      assert.equal(bad.error.code, -32602);
      assert.equal(fake.calls.length, 0); // the template is static: no logbook read
    });

    test("show_chart is listed with its view and schema; log_watch is hidden from views; reads have no _meta", async () => {
      const j = await (await client(standardRepo().gh).rpc({ jsonrpc: "2.0", id: 1, method: "tools/list" })).json();
      const by = Object.fromEntries(j.result.tools.map((t: { name: string }) => [t.name, t]));
      assert.deepEqual(Object.keys(by), ["take_fix", "read_chart", "log_watch", "show_chart"]);
      const s = by.show_chart;
      assert.equal(s._meta.ui.resourceUri, "ui://waypoint/chart-v1");
      assert.equal(s._meta["ui/resourceUri"], "ui://waypoint/chart-v1");
      assert.equal(s.annotations.readOnlyHint, true);
      assert.deepEqual(s.inputSchema, { type: "object", properties: {}, additionalProperties: false });
      assert.deepEqual(s.outputSchema.required, ["chart_generated", "data_complete", "kpi", "projects"]);
      assert.deepEqual(by.log_watch._meta, { ui: { visibility: ["model"] } });
      assert.equal(by.take_fix._meta, undefined);
      assert.equal(by.read_chart._meta, undefined);
    });

    test("show_chart returns the allowlisted payload as structuredContent", async () => {
      const res = await client(standardRepo().gh).rpc({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "show_chart", arguments: {} } });
      const r = (await res.json()).result;
      noLeak(JSON.stringify(r));
      assert.deepEqual(Object.keys(r.structuredContent).sort(), ["chart_generated", "data_complete", "kpi", "projects"]);
      for (const p of r.structuredContent.projects) {
        assert.deepEqual(Object.keys(p).sort(), ["idle", "last_tool", "name", "next", "now_behind", "status", "summary", "unlogged"]);
      }
      const viaTakeFix = JSON.parse((await client(standardRepo().gh).call("take_fix")).text);
      assert.deepEqual(r.structuredContent, viaTakeFix);
    });

    test("show_chart has a text reply for hosts without views", async () => {
      const res = await client(standardRepo().gh).rpc({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "show_chart", arguments: {} } });
      const r = (await res.json()).result;
      assert.equal(r.content[0].type, "text");
      assert.match(r.content[0].text, /^Chart taken .*: 2 tracked, \S+ unlogged watches, \S+ with Now behind the log\./);
      assert.ok(!r.content[0].text.includes("summary"));
    });

    test("show_chart cuts summary and next to 300; take_fix keeps its own cap", async () => {
      const long = "x".repeat(900);
      const { gh } = standardRepo(undefined, dashboard([project("alpha", { summary: long, next: long })]));
      const r = (await (await client(gh).rpc({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "show_chart", arguments: {} } })).json()).result;
      assert.equal(r.structuredContent.projects[0].summary.length, 300);
      assert.equal(r.structuredContent.projects[0].next.length, 300);
      assert.equal(JSON.parse((await client(gh).call("take_fix")).text).projects[0].summary.length, 900);
    });
  });

  test("log_watch tool ids match the local server's KNOWN_TOOLS", async () => {
    const j = await (await rpc({ jsonrpc: "2.0", id: 2, method: "tools/list" })).json();
    const tool = j.result.tools.find((t: { name: string }) => t.name === "log_watch");
    const local = readFileSync("../waypoint/logbook.py", "utf8").match(/KNOWN_TOOLS = \(([^)]*)\)/s)?.[1];
    assert.ok(local, "KNOWN_TOOLS declaration in waypoint/logbook.py");
    const known = [...local.matchAll(/"([a-z][a-z0-9-]+)"/g)].map((m) => m[1]);
    assert.deepEqual(tool.inputSchema.properties.tool.enum, known);
  });

  test("malformed traffic yields JSON-RPC errors, never a throw or 500", async () => {
    assert.equal((await (await rpc("{not json")).json()).error.code, -32700);
    assert.equal((await (await rpc({ jsonrpc: "2.0", id: 1, method: "nope" })).json()).error.code, -32601);
    assert.equal((await (await rpc({ jsonrpc: "2.0", id: 1, method: "tools/call", params: {} })).json()).error.code, -32602);
    assert.equal((await (await rpc({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "ghost" } })).json()).error.code, -32602);
    assert.equal((await (await rpc([])).json()).error.code, -32600);
    assert.equal((await (await rpc({ id: 1, method: "ping" })).json()).error.code, -32600);
  });

  test("batches are refused with -32600 and run nothing", async () => {
    const fake = standardRepo();
    const res = await client(fake.gh).rpc([
      { jsonrpc: "2.0", id: 1, method: "ping" },
      { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "log_watch", arguments: { project: "alpha", summary: "x" } } },
    ]);
    assert.equal(res.status, 400);
    assert.equal((await res.json()).error.code, -32600);
    assert.equal(fake.calls.length, 0);
  });

  test("a request without an id is refused before dispatch: no id-less writes", async () => {
    const fake = standardRepo();
    const { rpc } = client(fake.gh);
    for (const id of [undefined, null, { x: 1 }, [1], true]) {
      const res = await rpc({ jsonrpc: "2.0", id, method: "tools/call", params: { name: "log_watch", arguments: { project: "alpha", summary: "x" } } });
      assert.equal(res.status, 400, String(id));
      const j = await res.json();
      assert.equal(j.error.code, -32600);
      assert.equal(j.id, null);
    }
    const ping = await rpc({ jsonrpc: "2.0", method: "ping" });
    assert.equal((await ping.json()).error.code, -32600);
    assert.equal(fake.calls.length, 0);
  });

  test("oversized bodies are refused with 413", async () => {
    assert.equal((await rpc("x".repeat(20000))).status, 413);
  });

  test("body cap counts bytes, not UTF-16 units", async () => {
    assert.equal((await rpc("€".repeat(6000))).status, 413);
  });

  const streamed = (chunks: number, headers: Record<string, string> = {}) => {
    let pulled = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(ctl) {
        if (pulled++ >= chunks) return ctl.close();
        ctl.enqueue(new Uint8Array(4096).fill(0x20));
      },
    });
    const req = new Request("https://waypoint.example/mcp", { method: "POST", body, headers, duplex: "half" } as RequestInit);
    return { req, pulled: () => pulled };
  };

  test("body cap holds while streaming: no Content-Length, endless body", async () => {
    const fake = standardRepo();
    const s = streamed(1_000_000);
    assert.equal(s.req.headers.get("content-length"), null);
    const res = await handleMcp(s.req, { gh: fake.gh });
    assert.equal(res.status, 413);
    assert.ok(s.pulled() <= 8, `read ${s.pulled()} chunks`);
    assert.equal(fake.calls.length, 0);
  });

  test("body cap holds while streaming: a false small Content-Length", async () => {
    const s = streamed(10, { "content-length": "20" });
    assert.equal((await handleMcp(s.req, { gh: standardRepo().gh })).status, 413);
  });

  test("a streamed body within the cap still works", async () => {
    const body = new Blob([JSON.stringify({ jsonrpc: "2.0", id: 7, method: "ping" })]).stream();
    const req = new Request("https://waypoint.example/mcp", { method: "POST", body, duplex: "half" } as RequestInit);
    const reply = await (await handleMcp(req, { gh: standardRepo().gh })).json();
    assert.equal(reply.result.resultType, "complete");
  });

  test("unknown tool and method names are echoed capped", async () => {
    const j = await (await rpc({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "x".repeat(500) } })).json();
    assert.ok(!j.error.message.includes("x".repeat(100)));
  });

  test("GET is 405 with a hint; other methods 405; no wildcard CORS", async () => {
    const get = await handleMcp(new Request("https://waypoint.example/mcp"), { gh: standardRepo().gh });
    assert.equal(get.status, 405);
    assert.equal(get.headers.get("allow"), "POST");
    assert.match(await get.text(), /claude mcp add --transport http waypoint https:\/\/waypoint\.example\/mcp/);
    const del = await handleMcp(new Request("https://waypoint.example/mcp", { method: "DELETE" }), { gh: standardRepo().gh });
    assert.equal(del.status, 405);
    const res = await rpc({ jsonrpc: "2.0", id: 1, method: "ping" });
    assert.equal(res.headers.get("access-control-allow-origin"), null);
  });
});

describe("privacy allowlist (§8)", () => {
  test("now_behind and updated pass through from a v1.2 chart; an older chart gives null", async () => {
    const dash = dashboard([project("alpha", { updated: "2026-10-01", now_behind: true }), project("beta")]);
    (dash.kpi as Record<string, unknown>).behind = 1;
    const { call } = client(standardRepo(undefined, dash).gh);
    const fix = JSON.parse((await call("take_fix")).text);
    assert.equal(fix.kpi.behind, 1);
    assert.deepEqual(fix.projects.map((p: { now_behind: unknown }) => p.now_behind), [true, null]);
    const one = JSON.parse((await call("read_chart", { project: "alpha" })).text).project;
    assert.deepEqual([one.updated, one.now_behind], ["2026-10-01", true]);
    const old = JSON.parse((await client(standardRepo().gh).call("take_fix")).text);
    assert.equal(old.kpi.behind, null);
  });

  test("take_fix returns KPIs and allowed project fields only", async () => {
    const { call } = client(standardRepo().gh);
    const { text, isError } = await call("take_fix");
    assert.equal(isError, false);
    noLeak(text);
    const j = JSON.parse(text);
    assert.deepEqual(Object.keys(j).sort(), ["chart_generated", "data_complete", "kpi", "projects"]);
    assert.equal(j.data_complete, false); // fixture contains a degraded collector
    assert.deepEqual(Object.keys(j.kpi).sort(), ["attention", "behind", "stale", "tracked", "unlogged", "untracked", "velocity"]);
    assert.equal(j.chart_generated, "2025-10-14T08:20:00.000Z");
    assert.deepEqual(j.projects.map((p: { name: string }) => p.name), ["alpha", "beta"]);
    for (const p of j.projects) {
      assert.deepEqual(Object.keys(p).sort(), ["idle", "last_tool", "name", "next", "now_behind", "status", "summary", "unlogged"]);
    }
    assert.deepEqual(j.projects[1], { name: "beta", status: "paused", idle: 40, last_tool: "claude-code", unlogged: 2, now_behind: null, summary: "beta summary", next: "beta next" });
  });

  test("read_chart returns one project's allowed fields and its log, never the header", async () => {
    const { call } = client(standardRepo().gh);
    const { text, isError } = await call("read_chart", { project: "alpha" });
    assert.equal(isError, false);
    noLeak(text);
    const j = JSON.parse(text);
    assert.deepEqual(Object.keys(j).sort(), ["chart_generated", "data_complete", "log_source", "logs", "project"]);
    assert.equal(j.data_complete, false);
    assert.equal(j.log_source, "current project file");
    assert.deepEqual(Object.keys(j.project).sort(), ["done", "idle", "last_tool", "name", "next", "now_behind", "status", "summary", "unlogged", "updated"]);
    assert.deepEqual(j.logs, [
      { date: "2026-10-13", tool: "claude-code", text: "Newer entry" },
      { date: "2026-10-12", tool: "codex", text: "Older entry — with an em dash" },
    ]);
  });

  test("read_chart labels its log as live when chart fields come from an older snapshot", async () => {
    const fake = standardRepo();
    fake.set("projects/alpha.md", "# alpha\n## Log\n### 2026-10-14 — codex\n- live entry\n");
    const { text } = await client(fake.gh).call("read_chart", { project: "alpha" });
    const j = JSON.parse(text);
    assert.equal(j.chart_generated, "2025-10-14T08:20:00.000Z");
    assert.equal(j.log_source, "current project file");
    assert.equal(j.logs[0].text, "live entry");
  });

  test("reports complete, incomplete, and unknown coverage without returning source details", async () => {
    const sources = {
      "claude-code": { state: "ok", detail: "CLAUDE-PRIVATE" },
      codex: { state: "ok", detail: "CODEX-PRIVATE" },
      git: { state: "ok", detail: "GIT-PRIVATE" },
      logbook: { state: "ok", detail: "LOGBOOK-PRIVATE" },
    };
    const complete = standardRepo(undefined, dashboard());
    complete.set("dashboard.json", JSON.stringify({ ...dashboard(), sources }));
    const take = JSON.parse((await client(complete.gh).call("take_fix")).text);
    const read = JSON.parse((await client(complete.gh).call("read_chart", { project: "alpha" })).text);
    assert.equal(take.data_complete, true);
    assert.equal(read.data_complete, true);
    noLeak(JSON.stringify({ take, read }));
    for (const detail of ["CLAUDE-PRIVATE", "CODEX-PRIVATE", "GIT-PRIVATE", "LOGBOOK-PRIVATE"]) {
      assert.ok(!JSON.stringify({ take, read }).includes(detail));
    }

    const absent = standardRepo(undefined, dashboard());
    absent.set("dashboard.json", JSON.stringify({ ...dashboard(), sources: { ...sources, codex: { state: "absent" } } }));
    const absentResult = JSON.parse((await client(absent.gh).call("take_fix")).text);
    assert.equal(absentResult.data_complete, false);

    const unknown = standardRepo(undefined, dashboard());
    const noSources = dashboard() as Record<string, unknown>;
    delete noSources.sources;
    unknown.set("dashboard.json", JSON.stringify(noSources));
    const result = JSON.parse((await client(unknown.gh).call("take_fix")).text);
    assert.equal(result.data_complete, null);
  });

  test("read_chart caps the log at 10 entries, newest first", async () => {
    const { call } = client(standardRepo().gh);
    const j = JSON.parse((await call("read_chart", { project: "beta" })).text);
    assert.equal(j.logs.length, 10);
    assert.equal(j.logs[0].date, "2026-09-12");
    assert.equal(j.logs[9].date, "2026-09-03");
  });

  test("a GitHub failure is a tool error that leaks neither token nor body", async () => {
    const fake = standardRepo();
    fake.gh.token = "github_pat_FAKE_TOKEN_never_echoed_WRONG";
    const { text, isError } = await client(fake.gh).call("take_fix");
    assert.equal(isError, true);
    assert.match(text, /GitHub answered 401/);
    assert.ok(!text.includes("FAKE_TOKEN") && !text.includes("bad credentials"));
  });
});

describe("project names", () => {
  for (const bad of ["../secrets", "alpha/../../secrets", "projects/alpha.md", "alpha.md", "ALPHA", "_template", "ARCHIVED-SECRET", "", 42, null]) {
    test(`rejects ${JSON.stringify(bad)} and reads no project file`, async () => {
      const fake = standardRepo();
      for (const tool of ["read_chart", "log_watch"]) {
        const { text, isError } = await client(fake.gh).call(tool, { project: bad, summary: "hello" });
        assert.equal(isError, true, tool);
        assert.match(text, /Tracked projects: alpha, beta/);
      }
      assert.deepEqual([...new Set(fake.calls.map((c) => c.path))], ["dashboard.json"]);
      assert.ok(!fake.calls.some((c) => c.method === "PUT"));
    });
  }

  test("unknown-name echo is capped", async () => {
    const { text } = await client(standardRepo().gh).call("read_chart", { project: "y".repeat(500) });
    assert.ok(!text.includes("y".repeat(100)));
  });

  test("preserves project identity beyond 100 characters so it remains addressable", async () => {
    const name = "a".repeat(101);
    const fake = standardRepo(undefined, dashboard([project(name)]));
    fake.set(`projects/${name}.md`, `# ${name}\n## Log\n### 2026-10-13 — codex\n- entry\n`);
    const { text, isError } = await client(fake.gh).call("read_chart", { project: name });
    assert.equal(isError, false, text);
    assert.equal(JSON.parse(text).project.name, name);
    assert.ok(fake.calls.some((c) => c.path === `projects/${name}.md`));
  });

  test("a tampered chart cannot point a project at another file", async () => {
    const dash = dashboard([project("alpha", { file: "secrets.md" }), project("../x", { file: "projects/../x.md" })]);
    const fake = standardRepo(undefined, dash);
    for (const name of ["alpha", "../x"]) {
      const { text, isError } = await client(fake.gh).call("read_chart", { project: name });
      assert.equal(isError, true);
      assert.match(text, /unexpected file/);
    }
    assert.deepEqual([...new Set(fake.calls.map((c) => c.path))], ["dashboard.json"]);
  });
});

describe("value validation (§8): forbidden data nested in allowed fields", () => {
  const nested = { attention: ["ATTN-SECRET"], path: "D:\SECRET-PATH", sources: { codex: { detail: "SOURCE-SECRET" } }, feed: [{ text: "FEED-SECRET" }] };
  const evil = dashboard([
    project("alpha", { summary: nested, next: [nested], done: { toString: "ATTN-SECRET" }, status: nested, idle: nested, unlogged: "FEED-SECRET", last_tool: 7, now_behind: "ATTN-SECRET", updated: nested }),
    project("beta", { idle: Infinity, summary: "x".repeat(5000) }),
    { name: nested, file: "projects/x.md" }, // unaddressable: dropped
    "UNTRACKED-SECRET",
  ] as never);
  (evil as Record<string, unknown>).kpi = { tracked: nested, attention: "ATTN-SECRET", velocity: 3, stale: NaN, untracked: [1], unlogged: null, behind: "ATTN-SECRET" };
  (evil as Record<string, unknown>).generated = nested;

  test("take_fix nulls every non-conforming value", async () => {
    const { text, isError } = await client(standardRepo(undefined, evil).gh).call("take_fix");
    assert.equal(isError, false, text);
    noLeak(text);
    const j = JSON.parse(text);
    assert.equal(j.chart_generated, null);
    assert.deepEqual(j.kpi, { tracked: null, attention: null, velocity: 3, stale: null, untracked: null, unlogged: null, behind: null });
    assert.deepEqual(j.projects.map((p: { name: string }) => p.name), ["alpha", "beta"]);
    assert.deepEqual(j.projects[0], { name: "alpha", status: null, idle: null, last_tool: null, unlogged: null, summary: null, next: null, now_behind: null });
    assert.equal(j.projects[1].idle, null);
    assert.equal(j.projects[1].summary.length, 2000);
    for (const p of j.projects) for (const v of Object.values(p)) assert.ok(v === null || ["string", "number", "boolean"].includes(typeof v));
  });

  test("read_chart nulls the same values and returns log entries as plain strings", async () => {
    const { text, isError } = await client(standardRepo(undefined, evil).gh).call("read_chart", { project: "alpha" });
    assert.equal(isError, false, text);
    noLeak(text);
    const j = JSON.parse(text);
    assert.deepEqual(j.project, { name: "alpha", status: null, idle: null, last_tool: null, unlogged: null, summary: null, done: null, next: null, updated: null, now_behind: null });
    for (const e of j.logs) {
      assert.deepEqual(Object.keys(e).sort(), ["date", "text", "tool"]);
      for (const v of Object.values(e)) assert.equal(typeof v, "string");
    }
  });
});

describe("Origin check", () => {
  const BEACON = "https://waypoint.example";
  // The same order as index.ts: the Origin check first, then (here) the MCP handler.
  const serve = async (origin: string | null) => {
    const fake = standardRepo();
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (origin !== null) headers.origin = origin;
    const req = new Request(`${BEACON}/mcp`, {
      method: "POST",
      headers,
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "log_watch", arguments: { project: "alpha", summary: "x" } } }),
    });
    const res = badOrigin(req, BEACON) ?? (await handleMcp(req, { gh: fake.gh }));
    return { res, fake };
  };

  for (const origin of ["https://evil.example", "null", "https://waypoint.example.evil.com", "http://waypoint.example"]) {
    test(`Origin ${origin} is refused with 403 and no GitHub call`, async () => {
      const { res, fake } = await serve(origin);
      assert.equal(res.status, 403);
      assert.equal(fake.calls.length, 0);
    });
  }

  test("no Origin, or our own Origin, passes", async () => {
    for (const origin of [null, BEACON]) {
      const { res, fake } = await serve(origin);
      assert.equal(res.status, 200);
      assert.equal(fake.calls.filter((c) => c.method === "PUT").length, 1);
    }
  });
});
