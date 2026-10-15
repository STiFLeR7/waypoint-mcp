// A fake GitHub contents API and a fixture logbook. Sentinel strings mark everything the
// beacon must never return (LOGBOOK-SPEC §8); tests assert none of them leak.
import { handleMcp } from "../src/mcp.ts";
import { toBase64, fromBase64, type Gh } from "../src/logbook.ts";

export const TOKEN = "github_pat_FAKE_TOKEN_never_echoed";
export const SENTINELS = [
  "SECRET-PATH",
  "ATTN-SECRET",
  "TITLE-SECRET",
  "FEED-SECRET",
  "UNTRACKED-SECRET",
  "SOURCE-SECRET",
  "RULES-SECRET",
  "ARCHIVED-SECRET",
  "DOCS-SECRET",
  TOKEN,
];

export const ALPHA = [
  "# alpha",
  "- Path: D:\\SECRET-PATH\\alpha",
  "- Status: active",
  "- Summary: Alpha — the first voyage",
  "- Docs: DOCS-SECRET.md",
  "- Attention: ATTN-SECRET",
  "",
  "## Now",
  "- Done: Built the hull",
  "- Next: Sea trials",
  "",
  "## Log",
  "### 2026-10-12 — codex",
  "- Older entry — with an em dash",
  "",
  "### 2026-10-13 — claude-code",
  "- Newer entry",
  "",
].join("\n");

const beta = ["# beta", "- Status: paused", "", "## Log"];
for (let i = 1; i <= 12; i++) beta.push(`### 2026-09-${String(i).padStart(2, "0")} — codex`, `- entry ${i}`, "");
export const BETA = beta.join("\n");

const project = (name: string, extra: Record<string, unknown> = {}) => ({
  name,
  file: `projects/${name}.md`,
  path: `D:\\SECRET-PATH\\${name}`,
  status: "active",
  summary: `${name} summary`,
  docs: ["DOCS-SECRET.md"],
  done: `${name} done`,
  next: `${name} next`,
  attention: ["ATTN-SECRET: reef ahead"],
  last: 1760400000,
  last_tool: "claude-code",
  idle: 1,
  claude: 1760400000,
  codex: 0,
  git: 0,
  stale: false,
  unlogged: 2,
  logs: [{ ts: 1, date: "2026-10-13", tool: "claude-code", text: "x" }],
  sessions: [{ title: "TITLE-SECRET" }],
  ...extra,
});

export const dashboard = (projects = [project("alpha"), project("beta", { status: "paused", idle: 40 })]) => ({
  schema: 1,
  generated: 1760430000,
  config: { stale_days: { active: 21 } },
  sources: { "claude-code": { state: "degraded", detail: "SOURCE-SECRET" } },
  rules: { logbook: "RULES-SECRET" },
  kpi: { tracked: 2, attention: 1, velocity: 1, stale: 0, untracked: 3, unlogged: 1, extra_secret: "ATTN-SECRET" },
  projects,
  archived: [{ name: "ARCHIVED-SECRET", file: "projects/_archive/ARCHIVED-SECRET.md", summary: "", done: "", logs: [] }],
  untracked: [{ path: "D:\\UNTRACKED-SECRET", last: 1, title: "TITLE-SECRET" }],
  pulse: { "2026-10-14": { "claude-code": 1, codex: 0, chatgpt: 0 } },
  feed: [{ ts: 1, tool: "codex", project: "alpha", text: "FEED-SECRET", kind: "session" }],
});
export { project };

type Call = { method: string; path: string; auth: string | null; body?: { content: string; sha: string; message: string; branch: string } };

/** In-memory repo. `onPut` runs before each PUT is applied and may return a status to fail it with. */
export function fakeGithub(files: Record<string, string>, onPut?: (path: string, n: number) => number | void) {
  const repo = new Map<string, { text: string; sha: string }>();
  let version = 0;
  const set = (path: string, text: string) => repo.set(path, { text, sha: `sha${++version}` });
  for (const [p, t] of Object.entries(files)) set(p, t);
  const calls: Call[] = [];
  let puts = 0;

  const fetch = async (url: string, init: RequestInit = {}) => {
    const u = new URL(url);
    const prefix = "/repos/octocat/logbook/contents/";
    if (!u.pathname.startsWith(prefix)) return new Response("not found", { status: 404 });
    const path = decodeURIComponent(u.pathname.slice(prefix.length));
    const method = init.method ?? "GET";
    const auth = new Headers(init.headers).get("authorization");
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, auth, body });
    if (auth !== `Bearer ${TOKEN}`) return new Response("bad credentials", { status: 401 });
    const f = repo.get(path);
    if (method === "GET") {
      if (u.searchParams.get("ref") !== "master") return new Response("wrong ref", { status: 404 });
      if (!f) return new Response("not found", { status: 404 });
      // GitHub wraps base64 at 60 columns
      const content = toBase64(f.text).replace(/.{60}/g, "$&\n");
      return Response.json({ type: "file", encoding: "base64", content, sha: f.sha, path });
    }
    if (method === "PUT") {
      const forced = onPut?.(path, ++puts);
      if (forced) return new Response("forced", { status: forced });
      const cur = repo.get(path); // onPut may have changed it
      if (!cur || body.sha !== cur.sha) return new Response("sha mismatch", { status: 409 });
      set(path, fromBase64(body.content));
      return Response.json({ content: { sha: repo.get(path)!.sha } }, { status: 200 });
    }
    return new Response("method", { status: 405 });
  };
  const gh: Gh = { repo: "octocat/logbook", branch: "master", token: TOKEN, fetch };
  return { gh, calls, repo, set };
}

export function standardRepo(onPut?: (path: string, n: number) => number | void, dash = dashboard()) {
  return fakeGithub(
    { "dashboard.json": JSON.stringify(dash), "projects/alpha.md": ALPHA, "projects/beta.md": BETA, "secrets.md": "SECRET-PATH" },
    onPut,
  );
}

export const NOW = new Date("2026-10-14T10:00:00Z");

export function client(gh: Gh, now = NOW) {
  const rpc = (body: unknown, init: RequestInit = {}) =>
    handleMcp(
      new Request("https://waypoint.example/mcp", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json, text/event-stream" },
        body: typeof body === "string" ? body : JSON.stringify(body),
        ...init,
      }),
      { gh, now: () => now },
    );
  const call = async (name: string, args: unknown = {}) => {
    const res = await rpc({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } });
    const json = await res.json();
    const text: string = json.result?.content?.[0]?.text ?? "";
    return { res, json, text, isError: json.result?.isError === true };
  };
  return { rpc, call };
}
