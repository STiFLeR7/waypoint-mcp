// Waypoint beacon: OAuthProvider around /mcp (LOGBOOK-SPEC §8). Wiring only; logic lives in
// mcp.ts (protocol + tools), logbook.ts (GitHub data) and auth.ts (GitHub sign-in).
import { OAuthProvider } from "@cloudflare/workers-oauth-provider";
import { allowed, badOrigin, handleAuth, type AuthEnv, type Props } from "./auth.ts";
import { handleMcp } from "./mcp.ts";

// Minimal local type so tsc does not need @cloudflare/workers-types.
type Env = AuthEnv & {
  OAUTH_KV: unknown; // used by the provider
  BEACON_URL: string; // public origin clients connect to, e.g. https://beacon.example.com
  LOGBOOK_REPO: string;
  LOGBOOK_BRANCH: string;
  GITHUB_TOKEN: string; // fine-grained PAT: the logbook repo only, Contents read/write
};

const api = {
  async fetch(request: Request, env: Env, ctx: { props?: Props }): Promise<Response> {
    // Re-check on every call, so removing a login from ALLOWED_GITHUB_USERS cuts off live tokens too.
    if (!allowed(env, ctx.props?.login)) return new Response("Forbidden", { status: 403 });
    const res = await handleMcp(request, {
      gh: {
        repo: env.LOGBOOK_REPO,
        branch: env.LOGBOOK_BRANCH,
        token: env.GITHUB_TOKEN,
        fetch: (u, i) => fetch(u, i), // unbound: Workers rejects fetch called as a method of another object
      },
    });
    // The provider reflects any Origin unless the handler sets one: allow only our own origin.
    res.headers.set("access-control-allow-origin", new URL(request.url).origin);
    return res;
  },
};

// Built on first request: the canonical resource URL comes from the BEACON_URL var.
let provider: OAuthProvider<Env> | undefined;

export default {
  fetch(request: Request, env: Env, ctx: unknown): Promise<Response> {
    const origin = env.BEACON_URL.replace(/\/+$/, "");
    const refused = badOrigin(request, origin); // before the provider, the sign-in pages or any tool
    if (refused) return Promise.resolve(refused);
    provider ??= new OAuthProvider<Env>({
      apiRoute: "/mcp",
      apiHandler: api,
      defaultHandler: { fetch: (req: Request, e: Env) => handleAuth(req, e) },
      authorizeEndpoint: "/authorize",
      tokenEndpoint: "/token",
      clientRegistrationEndpoint: "/register",
      resourceMetadata: {
        resource: `${origin}/mcp`,
        authorization_servers: [origin],
        resource_name: "Waypoint beacon",
      },
    });
    return provider.fetch(request, env, ctx as never);
  },
};
