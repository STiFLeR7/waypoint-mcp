// The provider's defaultHandler: consent page -> GitHub sign-in -> owner allowlist -> grant.
// Uses the library's consent and upstream helpers (env.OAUTH_PROVIDER), which bind each step to
// the browser and forbid framing. Only type imports from the library, so this loads under Node.
import type { AuthRequest, ConsentDescription, OAuthHelpers } from "@cloudflare/workers-oauth-provider";

export type AuthEnv = {
  OAUTH_PROVIDER: OAuthHelpers;
  GITHUB_CLIENT_ID: string;
  GITHUB_CLIENT_SECRET: string;
  ALLOWED_GITHUB_USERS?: string;
};
export type Props = { login: string };
type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

const UA = "waypoint-beacon";

const LOGIN = /^[a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38}$/i;

/** Comma-separated GitHub logins, case-insensitive. Fails closed: unset, empty or any malformed entry lets nobody in. */
export function allowed(env: Pick<AuthEnv, "ALLOWED_GITHUB_USERS">, login: unknown): boolean {
  if (typeof login !== "string" || !login || typeof env.ALLOWED_GITHUB_USERS !== "string") return false;
  const list = env.ALLOWED_GITHUB_USERS.split(",").map((s) => s.trim().toLowerCase());
  if (!list.every((s) => LOGIN.test(s))) return false;
  return list.includes(login.toLowerCase());
}

/**
 * Streamable HTTP requires Origin validation. A browser request from any other origin is refused
 * before the provider or a tool runs. No Origin (server-side MCP clients, top-level navigations) passes.
 */
export function badOrigin(request: Request, beaconUrl: string): Response | null {
  const origin = request.headers.get("origin");
  if (origin === null || origin === new URL(beaconUrl).origin) return null;
  return new Response("Forbidden: cross-origin requests are not accepted.", { status: 403 });
}

const escape = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

function page(status: number, title: string, body: string, headers = new Headers()) {
  headers.set("content-type", "text/html; charset=utf-8");
  const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>${escape(title)}</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:4rem auto;padding:0 1rem;color:#1b2430}
button{font:inherit;padding:.5rem 1.2rem;margin-right:.5rem}.warn{color:#9a3412}</style>
<h1>${escape(title)}</h1>${body}`;
  return new Response(html, { status, headers });
}

function consentPage(d: ConsentDescription, handle: string, headers: Headers) {
  const origin = d.clientDomain
    ? `Published by <strong>${escape(d.clientDomain)}</strong>.`
    : "This app registered itself; its name is not verified.";
  return page(
    200,
    "Raise the beacon?",
    `<p>Allow <strong>${escape(d.clientName)}</strong> to read your Waypoint chart (dashboard) and add log entries to your projects (with the app's own tool id)?</p>
<p>${origin} Access will be sent to <strong>${escape(d.redirectHost)}</strong>.</p>
${d.redirectIsLoopback ? '<p class="warn"><strong>This sends access to an app on your computer.</strong> Continue only if you just started connecting from it.</p>' : ""}
<p>Next you sign in with GitHub. Only the logbook owner is let through.</p>
<form method="post"><input type="hidden" name="handle" value="${escape(handle)}">
<button name="decision" value="approve">Allow</button><button name="decision" value="deny">Deny</button></form>`,
    headers,
  );
}

/** Same as the library's authorizationErrorRedirect; inlined so this module needs no runtime import. */
function errorRedirect(r: AuthRequest, code: string, description: string) {
  const u = new URL(r.redirectUri);
  for (const k of ["error", "error_description", "error_uri", "state", "iss"]) u.searchParams.delete(k);
  u.searchParams.set("error", code);
  u.searchParams.set("error_description", description);
  if (r.state) u.searchParams.set("state", r.state);
  if (r.issuer) u.searchParams.set("iss", r.issuer);
  return u.href;
}

const b64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const s256 = async (v: string) => b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(v))));

const redirect = (headers: Headers, to: string) => {
  headers.set("location", to);
  return new Response(null, { status: 302, headers });
};

export async function handleAuth(request: Request, env: AuthEnv, fetchFn: Fetch = (u, i) => fetch(u, i)): Promise<Response> {
  const url = new URL(request.url);
  const oauth = env.OAUTH_PROVIDER;
  try {
    if (url.pathname === "/authorize" && request.method === "GET") {
      const req = await oauth.parseAuthRequest(request);
      const details = await oauth.describeConsent(req); // first: a failed lookup leaves nothing in KV
      const consent = await oauth.beginConsent(req);
      return consentPage(details, consent.handle, consent.headers);
    }

    if (url.pathname === "/authorize" && request.method === "POST") {
      const form = await request.formData();
      const handle = String(form.get("handle") ?? "");
      if (form.get("decision") !== "approve") {
        const denied = await oauth.denyConsent(request, handle);
        return new Response(null, { status: 302, headers: denied.headers });
      }
      const approved = await oauth.approveConsent(request, handle);
      const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
      const up = await oauth.beginUpstream(approved.request, { data: { verifier }, headers: approved.headers });
      const gh = new URL("https://github.com/login/oauth/authorize");
      gh.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
      gh.searchParams.set("redirect_uri", `${url.origin}/callback`);
      gh.searchParams.set("state", up.state);
      gh.searchParams.set("code_challenge", await s256(verifier));
      gh.searchParams.set("code_challenge_method", "S256");
      gh.searchParams.set("allow_signup", "false"); // no scope: the public profile (login) is all we read
      return redirect(up.headers, gh.href);
    }

    if (url.pathname === "/callback" && request.method === "GET") {
      const { request: original, data, headers } = await oauth.finishUpstream<{ verifier: string }>(request);
      const deny = (why: string) => redirect(headers, errorRedirect(original, "access_denied", why));
      const code = url.searchParams.get("code");
      if (!code || url.searchParams.has("error")) return deny("GitHub sign-in was cancelled.");

      let tokenRes: Response;
      try {
        tokenRes = await fetchFn("https://github.com/login/oauth/access_token", {
          method: "POST",
          headers: { accept: "application/json", "content-type": "application/json", "user-agent": UA },
          body: JSON.stringify({
            client_id: env.GITHUB_CLIENT_ID,
            client_secret: env.GITHUB_CLIENT_SECRET,
            code,
            redirect_uri: `${url.origin}/callback`,
            code_verifier: data.verifier,
          }),
          signal: AbortSignal.timeout(10_000),
        });
      } catch {
        return deny("GitHub sign-in could not reach GitHub. Start connecting again.");
      }
      const token = ((await tokenRes.json().catch(() => ({}))) as { access_token?: unknown }).access_token;
      if (!tokenRes.ok || typeof token !== "string") return deny("GitHub sign-in failed.");

      let userRes: Response;
      try {
        userRes = await fetchFn("https://api.github.com/user", {
          headers: { authorization: `Bearer ${token}`, accept: "application/vnd.github+json", "user-agent": UA },
          signal: AbortSignal.timeout(10_000),
        });
      } catch {
        return deny("GitHub sign-in could not reach GitHub. Start connecting again.");
      }
      const user = (await userRes.json().catch(() => ({}))) as { login?: unknown; id?: unknown };
      if (!userRes.ok || typeof user.login !== "string" || typeof user.id !== "number") return deny("GitHub sign-in failed.");
      if (!allowed(env, user.login)) return deny("This beacon answers only to its owner.");

      // The GitHub token is dropped here: props carry only the login, never a credential.
      const props: Props = { login: user.login };
      const { redirectTo } = await oauth.completeAuthorization({
        request: original,
        userId: String(user.id),
        metadata: { login: user.login },
        scope: original.scope,
        props,
      });
      return redirect(headers, redirectTo);
    }
  } catch (e) {
    // Expected flow failures (expired, replayed or cross-browser handles; bad client requests).
    const name = e instanceof Error ? e.name : "";
    if (name === "AuthorizationError") {
      const { redirectTo, description } = e as { redirectTo?: string; description?: string };
      if (redirectTo) return Response.redirect(redirectTo, 302);
      return page(400, "Sign-in stopped", `<p>${escape(description ?? "This sign-in request is not valid.")}</p><p>Start connecting again from your app.</p>`);
    }
    if (name === "CimdFetchError") return page(400, "Sign-in stopped", "<p>This app could not be verified.</p>");
    throw e;
  }

  if (url.pathname === "/" && request.method === "GET") {
    return new Response("Waypoint beacon: remote MCP access to one logbook. Connect an MCP client to /mcp.\n", {
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  }
  return new Response("Not found", { status: 404 });
}
