// GitHub sign-in wiring with fake provider helpers. The helpers' own guarantees (browser-bound,
// single-use handles and state) belong to the library and are checked after deploy.
import { test } from "node:test";
import assert from "node:assert/strict";
import { allowed, handleAuth, type AuthEnv } from "../src/auth.ts";

const original = { responseType: "code", clientId: "c1", redirectUri: "https://chatgpt.com/cb", scope: [], state: "st8", issuer: "https://waypoint.example" };

function setup(login = "octocat", allow: string | undefined = "octocat", fetchOverride?: (url: string, init?: RequestInit) => Promise<Response>) {
  const log: string[] = [];
  let completed: Record<string, unknown> | undefined;
  const h = () => new Headers({ "set-cookie": "__Host-oauth-x=1" });
  const env = {
    GITHUB_CLIENT_ID: "gh-client",
    GITHUB_CLIENT_SECRET: "gh-secret",
    ALLOWED_GITHUB_USERS: allow,
    OAUTH_PROVIDER: {
      parseAuthRequest: async () => (log.push("parse"), original),
      describeConsent: async () => ({ clientId: "c1", clientName: '<script>alert(1)</script>Evil', redirectUri: original.redirectUri, redirectHost: "chatgpt.com", redirectIsLoopback: false, scope: [] }),
      beginConsent: async () => (log.push("begin"), { handle: "H1", headers: new Headers({ "x-frame-options": "DENY" }) }),
      approveConsent: async (_r: Request, handle: string) => {
        if (handle !== "H1") throw Object.assign(new Error("bad"), { name: "AuthorizationError", description: "This page expired." });
        log.push("approve");
        return { request: original, headers: h() };
      },
      denyConsent: async () => (log.push("deny"), { request: original, redirectTo: "https://chatgpt.com/cb?error=access_denied", headers: new Headers({ location: "https://chatgpt.com/cb?error=access_denied" }) }),
      beginUpstream: async (_r: unknown, o: { data: { verifier: string }; headers: Headers }) => {
        log.push(`upstream:${o.data.verifier.length}`);
        return { state: "S1", headers: o.headers };
      },
      finishUpstream: async () => ({ request: original, data: { verifier: "v".repeat(43) }, headers: h() }),
      completeAuthorization: async (o: Record<string, unknown>) => ((completed = o), { redirectTo: "https://chatgpt.com/cb?code=abc&state=st8" }),
    },
  } as unknown as AuthEnv;
  const github = async (url: string, init: RequestInit = {}) => {
    log.push(`fetch:${url}`);
    if (url === "https://github.com/login/oauth/access_token") {
      const b = JSON.parse(String(init.body));
      assert.equal(b.code_verifier, "v".repeat(43));
      assert.equal(b.redirect_uri, "https://waypoint.example/callback");
      return Response.json(b.code === "good" ? { access_token: "gho_upstream" } : { error: "bad_verification_code" });
    }
    if (url === "https://api.github.com/user") {
      assert.equal(new Headers(init.headers).get("authorization"), "Bearer gho_upstream");
      return Response.json({ login, id: 123 });
    }
    return new Response("?", { status: 404 });
  };
  const go = (path: string, init?: RequestInit) => handleAuth(new Request(`https://waypoint.example${path}`, init), env, fetchOverride ?? github);
  return { go, log, completed: () => completed };
}

const form = (fields: Record<string, string>) => ({ method: "POST", body: new URLSearchParams(fields) });

test("allowlist: case-insensitive comma list; unset, empty or malformed lets nobody in", () => {
  assert.equal(allowed({ ALLOWED_GITHUB_USERS: "Octocat" }, "octocat"), true);
  assert.equal(allowed({ ALLOWED_GITHUB_USERS: " a , B " }, "b"), true);
  assert.equal(allowed({ ALLOWED_GITHUB_USERS: "octocat" }, "someone"), false);
  for (const list of [undefined, "", " , ", "octocat,", "octo cat", "-bad", "octocat,<x>"]) {
    assert.equal(allowed({ ALLOWED_GITHUB_USERS: list }, "octocat"), false, String(list));
  }
  assert.equal(allowed({ ALLOWED_GITHUB_USERS: "octocat" }, undefined), false);
  assert.equal(allowed({ ALLOWED_GITHUB_USERS: "octocat" }, ""), false);
});

test("GET /authorize shows an escaped consent page with the library's headers", async () => {
  const { go, log } = setup();
  const res = await go("/authorize?client_id=c1");
  const html = await res.text();
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("x-frame-options"), "DENY");
  assert.ok(!html.includes("<script>alert"));
  assert.ok(html.includes("&#60;script&#62;"));
  assert.ok(html.includes("chatgpt.com"));
  assert.ok(html.includes('name="handle" value="H1"'));
  assert.deepEqual(log, ["parse", "begin"]);
});

test("Deny goes back to the client with access_denied and never reaches GitHub", async () => {
  const { go, log } = setup();
  const res = await go("/authorize", form({ handle: "H1", decision: "deny" }));
  assert.equal(res.status, 302);
  assert.match(res.headers.get("location")!, /error=access_denied/);
  assert.ok(!log.some((l) => l.startsWith("fetch")));
});

test("Allow redirects to GitHub with state and an S256 PKCE challenge, no secret, no scope", async () => {
  const { go, log } = setup();
  const res = await go("/authorize", form({ handle: "H1", decision: "approve" }));
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("set-cookie"), "__Host-oauth-x=1");
  const to = new URL(res.headers.get("location")!);
  assert.equal(to.origin + to.pathname, "https://github.com/login/oauth/authorize");
  assert.equal(to.searchParams.get("client_id"), "gh-client");
  assert.equal(to.searchParams.get("state"), "S1");
  assert.equal(to.searchParams.get("redirect_uri"), "https://waypoint.example/callback");
  assert.equal(to.searchParams.get("code_challenge_method"), "S256");
  assert.match(to.searchParams.get("code_challenge")!, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(to.searchParams.get("scope"), null);
  assert.ok(!to.href.includes("gh-secret"));
  assert.deepEqual(log, ["approve", "upstream:43"]);
});

test("an expired or forged handle renders a local error page, no redirect", async () => {
  const { go } = setup();
  const res = await go("/authorize", form({ handle: "forged", decision: "approve" }));
  assert.equal(res.status, 400);
  assert.match(await res.text(), /This page expired/);
});

test("callback for the owner completes with props {login} only, never a token", async () => {
  const { go, completed } = setup();
  const res = await go("/callback?code=good&state=S1");
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), "https://chatgpt.com/cb?code=abc&state=st8");
  const c = completed()!;
  assert.deepEqual(c.props, { login: "octocat" });
  assert.equal(c.userId, "123");
  assert.ok(!JSON.stringify(c).includes("gho_upstream"));
});

test("callback for anyone else is access_denied and grants nothing", async () => {
  const { go, completed } = setup("intruder");
  const res = await go("/callback?code=good&state=S1");
  const to = new URL(res.headers.get("location")!);
  assert.equal(to.origin + to.pathname, "https://chatgpt.com/cb");
  assert.equal(to.searchParams.get("error"), "access_denied");
  assert.equal(to.searchParams.get("state"), "st8");
  assert.equal(to.searchParams.get("iss"), "https://waypoint.example");
  assert.equal(completed(), undefined);
});

test("callback with a GitHub error or a bad code is access_denied", async () => {
  for (const q of ["?error=access_denied&state=S1", "?state=S1", "?code=bad&state=S1"]) {
    const { go, completed } = setup();
    const res = await go(`/callback${q}`);
    assert.equal(new URL(res.headers.get("location")!).searchParams.get("error"), "access_denied", q);
    assert.equal(completed(), undefined);
  }
});

test("callback network failures return a safe restart response with OAuth state", async () => {
  for (const failAt of ["https://github.com/login/oauth/access_token", "https://api.github.com/user"]) {
    const { go, completed } = setup("octocat", "octocat", async (url) => {
      if (url === failAt) throw new TypeError("socket failed with gh-secret and token detail");
      return url.endsWith("/access_token") ? Response.json({ access_token: "gho_upstream" }) : Response.json({ login: "octocat", id: 123 });
    });
    const res = await go("/callback?code=good&state=S1");
    const to = new URL(res.headers.get("location")!);
    assert.equal(to.searchParams.get("error"), "access_denied");
    assert.equal(to.searchParams.get("state"), "st8");
    assert.match(to.searchParams.get("error_description")!, /Start connecting again/);
    assert.ok(!res.headers.get("location")!.includes("gh-secret"));
    assert.equal(completed(), undefined);
  }
});

test("both GitHub callback requests carry a bounded timeout signal", async () => {
  const signals: AbortSignal[] = [];
  const { go } = setup("octocat", "octocat", async (url, init) => {
    assert.ok(init?.signal instanceof AbortSignal);
    signals.push(init.signal);
    return url.endsWith("/access_token") ? Response.json({ access_token: "gho_upstream" }) : Response.json({ login: "octocat", id: 123 });
  });
  assert.equal((await go("/callback?code=good&state=S1")).status, 302);
  assert.equal(signals.length, 2);
  assert.ok(signals.every((s) => !s.aborted));
});

test("other paths: / is a plain hint, the rest 404", async () => {
  const { go } = setup();
  assert.match(await (await go("/")).text(), /Waypoint beacon/);
  assert.equal((await go("/nope")).status, 404);
  assert.equal((await go("/callback", { method: "POST" })).status, 404);
});
