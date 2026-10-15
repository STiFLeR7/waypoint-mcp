// Protocol cases ported from port/tests/mcp.spec.ts, plus the §8 privacy allowlist and name checks.
import { test, describe } from "node:test";
import assert from "node:assert/strict";
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
    assert.equal(j.result.protocolVersion, "2025-06-18");
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
    assert.equal((await res.json()).result.protocolVersion, "2025-06-18");
  });

  test("accepts a supported MCP protocol header", async () => {
    const res = await client(standardRepo().gh).rpc(
      { jsonrpc: "2.0", id: 1, method: "ping" },
      { headers: { "content-type": "application/json", "mcp-protocol-version": "2025-06-18" } },
    );
    assert.equal(res.status, 200);
    assert.deepEqual((await res.json()).result, {});
  });

  test("notifications get 202 and no body", async () => {
    const res = await rpc({ jsonrpc: "2.0", method: "notifications/initialized" });
    assert.equal(res.status, 202);
    assert.equal(await res.text(), "");
  });

  test("tools/list: three tools with the right annotations", async () => {
    const j = await (await rpc({ jsonrpc: "2.0", id: 2, method: "tools/list" })).json();
    const by = Object.fromEntries(j.result.tools.map((t: { name: string }) => [t.name, t]));
    assert.deepEqual(Object.keys(by).sort(), ["log_watch", "read_chart", "take_fix"]);
    for (const n of ["take_fix", "read_chart"]) {
      assert.equal(by[n].annotations.readOnlyHint, true);
      assert.equal(by[n].annotations.openWorldHint, false);
    }
    assert.deepEqual(
      { ...by.log_watch.annotations, title: undefined },
      { title: undefined, readOnlyHint: false, destructiveHint: false, openWorldHint: false },
    );
    assert.deepEqual(by.log_watch.inputSchema.required, ["project", "summary"]);
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
    assert.deepEqual(await (await handleMcp(req, { gh: standardRepo().gh })).json(), { jsonrpc: "2.0", id: 7, result: {} });
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
  test("take_fix returns KPIs and allowed project fields only", async () => {
    const { call } = client(standardRepo().gh);
    const { text, isError } = await call("take_fix");
    assert.equal(isError, false);
    noLeak(text);
    const j = JSON.parse(text);
    assert.deepEqual(Object.keys(j).sort(), ["chart_generated", "data_complete", "kpi", "projects"]);
    assert.equal(j.data_complete, false); // fixture contains a degraded collector
    assert.deepEqual(Object.keys(j.kpi).sort(), ["attention", "stale", "tracked", "unlogged", "untracked", "velocity"]);
    assert.equal(j.chart_generated, "2025-10-14T08:20:00.000Z");
    assert.deepEqual(j.projects.map((p: { name: string }) => p.name), ["alpha", "beta"]);
    for (const p of j.projects) {
      assert.deepEqual(Object.keys(p).sort(), ["idle", "last_tool", "name", "next", "status", "summary", "unlogged"]);
    }
    assert.deepEqual(j.projects[1], { name: "beta", status: "paused", idle: 40, last_tool: "claude-code", unlogged: 2, summary: "beta summary", next: "beta next" });
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
    assert.deepEqual(Object.keys(j.project).sort(), ["done", "idle", "last_tool", "name", "next", "status", "summary", "unlogged"]);
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
    project("alpha", { summary: nested, next: [nested], done: { toString: "ATTN-SECRET" }, status: nested, idle: nested, unlogged: "FEED-SECRET", last_tool: 7 }),
    project("beta", { idle: Infinity, summary: "x".repeat(5000) }),
    { name: nested, file: "projects/x.md" }, // unaddressable: dropped
    "UNTRACKED-SECRET",
  ] as never);
  (evil as Record<string, unknown>).kpi = { tracked: nested, attention: "ATTN-SECRET", velocity: 3, stale: NaN, untracked: [1], unlogged: null };
  (evil as Record<string, unknown>).generated = nested;

  test("take_fix nulls every non-conforming value", async () => {
    const { text, isError } = await client(standardRepo(undefined, evil).gh).call("take_fix");
    assert.equal(isError, false, text);
    noLeak(text);
    const j = JSON.parse(text);
    assert.equal(j.chart_generated, null);
    assert.deepEqual(j.kpi, { tracked: null, attention: null, velocity: 3, stale: null, untracked: null, unlogged: null });
    assert.deepEqual(j.projects.map((p: { name: string }) => p.name), ["alpha", "beta"]);
    assert.deepEqual(j.projects[0], { name: "alpha", status: null, idle: null, last_tool: null, unlogged: null, summary: null, next: null });
    assert.equal(j.projects[1].idle, null);
    assert.equal(j.projects[1].summary.length, 2000);
    for (const p of j.projects) for (const v of Object.values(p)) assert.ok(v === null || ["string", "number"].includes(typeof v));
  });

  test("read_chart nulls the same values and returns log entries as plain strings", async () => {
    const { text, isError } = await client(standardRepo(undefined, evil).gh).call("read_chart", { project: "alpha" });
    assert.equal(isError, false, text);
    noLeak(text);
    const j = JSON.parse(text);
    assert.deepEqual(j.project, { name: "alpha", status: null, idle: null, last_tool: null, unlogged: null, summary: null, done: null, next: null });
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
