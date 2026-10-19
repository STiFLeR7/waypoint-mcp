# The beacon

> A light on the shore for navigators who can't see the chart.

ChatGPT has no local view of your logbook. The **beacon** (MCP server) is a small Cloudflare Worker that lets it, and any other remote navigator (tool), take a fix (read the dashboard) and log a watch (add one log entry), after you sign in with GitHub. It reads and writes your private logbook repo through the GitHub API. It never touches your machine.

The contract is [LOGBOOK-SPEC §8](../docs/LOGBOOK-SPEC.md#8-remote-access-contract-beacon-phase-5).

## Tools

| Tool | Kind | What it returns or does |
|---|---|---|
| `take_fix` | read-only | KPIs, plus for each tracked project: name, status, idle, last_tool, unlogged, now_behind, summary, next. |
| `show_chart` | read-only | The `take_fix` fields as a card inside ChatGPT and Claude (MCP Apps); Summary and Next are cut to 300 characters. |
| `read_chart(project)` | read-only | One project's name, status, idle, last_tool, unlogged, updated, now_behind, summary, done, next, and its 10 newest log entries. The result labels the log's source. |
| `log_watch(project, summary, tool?, date?, done?, next?)` | write | Adds one `### YYYY-MM-DD — <tool>` entry directly below `## Log` (known tool id; defaults to `chatgpt`), with every line of `summary` quoted with `> `. With `done`/`next`, also sets those lines and `- Updated:` in `## Now` in the same commit. |

- **Names only.** A project is addressed by its exact name, which must be in `dashboard.json`'s tracked `projects`. Anything else, including paths, `..`, archived names and names in a different case, gets an error that lists the valid names. The file is always `projects/<name>.md`; if the chart says otherwise, the beacon refuses.
- **Freshness.** KPIs and project fields come from `dashboard.json`, as of the last `waypoint refresh --push` (`chart_generated` in each result). `read_chart` reads logs live from the current project file (`log_source`); the chart fields and log can therefore reflect different file revisions, with no shared snapshot guarantee.
- **Collection health.** Read results include `data_complete`: `true` when all four expected collectors are `ok`, `false` when a collector is absent, degraded or unreadable, and `null` when health data is missing or unrecognized. This describes the chart snapshot's collection health, not complete historical coverage. No source names or diagnostic details are returned.
- **`log_watch` details:**
  - **Date.** It defaults to the Worker's current **UTC** date. Your logbook uses local dates (§3), and the Worker can't know your time zone, so the tool also accepts an optional `date`. That date must be a real date within one day of today in UTC. This covers every time zone and still prevents back-dating or future-dating.
  - **Size cap.** `summary` can be at most 2000 characters. Control characters and Unicode line separators become spaces. Blank lines inside the summary are kept as bare `>` lines; blank lines at the start or end are dropped. The whole file must stay at or under 900,000 bytes (UTF-8) after the insert, below GitHub's 1 MB contents limit; otherwise nothing is written.
  - **Duplicate check.** If the newest entry (the first one below `## Log`) already has the same date, tool id and text, nothing is written. If another entry has since become newest, repeating an older entry adds it again; retries are protected against duplicate writes when that entry is still newest.
  - **Write path.** It GETs the file (content and sha), then PUTs it back with that sha. On a 409 or 412 conflict it re-reads, re-checks for a duplicate and tries again, up to 4 attempts in all. After that it fails and writes nothing.
  - **What it leaves alone.** It changes no other byte: not Status or Attention, not Now unless you pass `done`/`next` (then only those lines and `- Updated:`, one line each, at most 300 characters, never starting with `#` or `>`), and not the BOM or the other entries. A repeated newest entry applies nothing, Now included. The new lines use the newline style of the `## Log` line (LF, CRLF or CR). It never creates, moves or deletes files. A file without a `## Log` line gets an error.

## Privacy contract

- **Reads never include:** session titles, the feed, the pulse, untracked folders, file paths (`Path`, `Docs`), Attention text, source details or rule state. Every result is built from an explicit list of allowed fields, and each **value** is checked: a string (capped at 2000 characters), a finite number, a boolean (`now_behind` only), or `null`. Anything else, such as an object hidden inside `summary` or a KPI, becomes `null`. Log entries are reduced to `{date, tool, text}` strings. Tests put all of these into the fixtures, including nested inside allowed fields, and check that none of them come back.
- **Who can connect:**
  - Every `/mcp` request needs an OAuth access token from this Worker.
  - Tokens are issued only after a consent page and a GitHub sign-in by a login in `ALLOWED_GITHUB_USERS`.
  - The allowlist is checked again on every call, so removing a login cuts off its live tokens.
- **Tokens and secrets:**
  - The GitHub sign-in token is used once to read your login, then dropped. The grant stores only `{ login }`.
  - The PAT (`GITHUB_TOKEN`) is never returned or logged. GitHub errors are reported by status code only.
- **Origin check:** any request with an `Origin` header other than the beacon's own origin gets 403 before the OAuth library, the sign-in pages or any tool run. Requests without `Origin` (server-side MCP clients, normal page loads) pass. Browser-based MCP clients on other sites can't connect.
- **Protocol limits:** one JSON-RPC message per POST (batches get `-32600`); a present `MCP-Protocol-Version` header must be one of the supported versions; a request without a string or number `id` is refused before dispatch, so nothing can write without an id; the 16,384-byte body cap is enforced while the body streams in, whatever `Content-Length` says. The header may be absent for older clients.
- **CORS:**
  - `/mcp` responses allow only the beacon's own origin, so browser pages on other sites can't read them.
  - MCP clients like ChatGPT and Claude connect from their servers, so they don't need CORS.
  - The OAuth library answers preflights and its own endpoints (token, register, metadata) itself.

## Layout

```
src/index.ts    OAuthProvider wiring (the only file that imports the library at runtime)
src/auth.ts     consent page -> GitHub sign-in -> allowlist -> grant
src/mcp.ts      stateless Streamable-HTTP JSON-RPC (adapted from port) the four tools and the chart card resource
src/logbook.ts  GitHub contents API, the log grammar, insertion and retries
test/           node --test suites with a fake GitHub
```

After a deploy that bumps the view URI, press **Refresh** on the ChatGPT connector.

## Develop

```sh
npm ci
npm test            # node --test (Node 24 runs the TypeScript directly)
npm run typecheck   # tsc --noEmit
npx wrangler@4.151.0 dev -c wrangler.local.jsonc --var BEACON_URL:http://localhost:8787   # local only; secrets go in .dev.vars (gitignored)
```

Wrangler always runs through `npx` at a pinned version. It isn't a devDependency, because that broke `npm ci` in another project.

## Deploy runbook

These are the one-time setup and deployment steps. Repeat the smoke tests (step 9) after every deploy.

The examples use **`https://beacon.example.com`** for the beacon's URL, **`<github-user>`** for your GitHub login and **`<logbook-repo>`** for your logbook's repo. Substitute your own everywhere. The URL must be on a zone in the same Cloudflare account; it is used for `BEACON_URL`, the custom-domain route, the GitHub OAuth callback, every token's audience, and every command below. Changing it after deploy means editing the OAuth app and reconnecting every client.

**Your values live in `wrangler.local.jsonc`** (git-ignored), never in the committed `wrangler.jsonc` template: `cp wrangler.jsonc wrangler.local.jsonc`, then fill it in. Every Wrangler command below passes `-c wrangler.local.jsonc`.

1. **Log in to Cloudflare:**
   ```sh
   npx wrangler@4.151.0 login
   ```
2. **Create the KV namespace:**
   ```sh
   npx wrangler@4.151.0 kv namespace create OAUTH_KV -c wrangler.local.jsonc
   ```
   Put the printed `id` into `wrangler.local.jsonc` as the `OAUTH_KV` binding's `id`. Set `BEACON_URL` (no trailing slash) and the route's `pattern` to your URL, `LOGBOOK_REPO` and `LOGBOOK_BRANCH` to your logbook, and `ALLOWED_GITHUB_USERS` to your login. **An unset, empty or malformed allowlist lets nobody in.**
3. **Create a GitHub OAuth app.** Go to GitHub → Settings → Developer settings → OAuth Apps → New OAuth App.
   - Application name: `Waypoint beacon`
   - Homepage URL: `https://beacon.example.com`
   - Authorization callback URL: `https://beacon.example.com/callback`
   - Keep the **Client ID**, then generate a **client secret**. The beacon asks for no scopes; it only reads your public login.
4. **Create a fine-grained personal access token.** Go to GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token.
   - Resource owner: `<github-user>`
   - Repository access: **Only select repositories** → `<logbook-repo>`
   - Repository permissions: **Contents: Read and write**. Metadata: read-only is added automatically. Add nothing else.
   - Expiration: **1 year**. Note the date: when the token expires, the beacon stops until you set a new one with `secret put GITHUB_TOKEN`.
5. **Set the secrets.** Run each command and paste the value when asked:
   ```sh
   npx wrangler@4.151.0 secret put GITHUB_CLIENT_ID -c wrangler.local.jsonc
   npx wrangler@4.151.0 secret put GITHUB_CLIENT_SECRET -c wrangler.local.jsonc
   npx wrangler@4.151.0 secret put GITHUB_TOKEN -c wrangler.local.jsonc
   ```
6. **Deploy:**
   ```sh
   npm run deploy      # npx wrangler@4.151.0 deploy -c wrangler.local.jsonc
   ```
7. **Check the custom domain.** The route in `wrangler.local.jsonc` (`"custom_domain": true`) makes the deploy in step 6 attach your URL (DNS record and certificate included). Confirm it under Workers & Pages → `waypoint-beacon` → Settings → Domains & Routes. The first certificate can take a few minutes.
8. **Connect the navigators:**
   - **ChatGPT:**
     1. Settings → Apps & Connectors → Advanced settings → turn on **Developer mode**.
     2. Create a connector with MCP server URL `https://beacon.example.com/mcp` and authentication **OAuth**.
     3. When the consent page asks, approve it, then sign in with GitHub.
     - If your plan blocks write tools, `take_fix` and `read_chart` still work.
   - **Claude (web/desktop):** Settings → Connectors → Add custom connector → `https://beacon.example.com/mcp`.
   - **Claude Code:**
     ```sh
     claude mcp add --transport http waypoint https://beacon.example.com/mcp
     ```
     Then run `/mcp` in a session to sign in.
   - **Codex:**
     ```sh
     codex mcp add waypoint --url https://beacon.example.com/mcp
     codex mcp login waypoint
     ```
     Check `codex mcp --help` if your version differs.
9. **Smoke tests:**
   1. A request without a token is refused:
      ```sh
      curl -i -X POST https://beacon.example.com/mcp
      ```
      Expect `401` with `WWW-Authenticate: Bearer ... resource_metadata="https://beacon.example.com/.well-known/oauth-protected-resource/mcp"`.
   2. In ChatGPT, ask it to *take a fix*. You should see `take_fix` with your projects, and no paths or Attention text.
   3. Ask it to *log a watch* on a project, for example "log on waypoint: tested the beacon". Check that a commit `log: waypoint (<tool id>, via beacon)` appears in `<github-user>/<logbook-repo>`.
   4. Ask for the same entry again. The tool should answer "Already logged", and no second commit should appear.
   5. Locally, run:
      ```sh
      waypoint refresh --push
      ```
      It pulls the commit, and the caller's tool id entry shows on the chart.
   6. Optional: sign in with a GitHub account that isn't on the allowlist. The client should get `access_denied`.
