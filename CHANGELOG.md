# Changelog

## 0.5.2

- MCP tool annotations: every tool on the local server and the beacon now declares all four hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) as explicit booleans. The read tools add `destructiveHint: false`. `log_watch` now says `destructiveHint: true` on both servers, because it can replace Done and Next. On the beacon it also says `idempotentHint: false`, because it only skips a repeat of the newest entry; the local server skips any identical entry that day and stays `true`. Directories such as OpenAI's reject tools missing any hint. No behaviour changes; a host may now ask before `log_watch` runs.

## 0.5.1

- Package metadata: PyPI Homepage and the MCP Registry `websiteUrl` now point to the website, https://waypoint.stifler.in; the README links it. No code changes.

## 0.5.0 — "Evening watch"

- **End-of-day email (optional).** Ask your AI tool for it and it emails you today's work across every project through AgentMail: progress, today's log entries, Done and Next. Waypoint builds the email itself: the read-only MCP tool `eod_report` returns the subject, a plain-text body and an email-safe dark HTML body with the Waypoint mark attached, listing every log entry dated today. Attention lines are never included, whatever `[mcp] include_attention` says. The `send_eod` prompt tells your tool to send exactly that (`/mcp__waypoint__send_eod` in Claude Code, `/send_eod` in Gemini CLI, or say "send my EoD"). Waypoint itself still sends nothing.
- `waypoint email` prints the AgentMail connect steps for each AI tool (`--scope user` in Claude Code and Gemini CLI, so it works in every project) and remembers (`--to`) or forgets (`--forget`) your address, in one local file that is never in the logbook. `setup` can ask for it (wizard step 6 of 9; never under `--yes`). `waypoint status` shows whether an address is remembered, never the address.
- Logbook spec v1.5 (additive; v1.4 and v1.5 add the address file, `send_eod` and `eod_report`). Existing logbooks need no changes; `dashboard.json` stays schema 1.

## 0.4.0 — "Ship's bell"

- The Waypoint plugin: one repo installs Waypoint's hooks and MCP server in Claude Code, Codex and Copilot CLI (plugin) and Gemini CLI (extension). `waypoint connect <cli> --plugin` prepares a machine for it; `waypoint status` shows when hooks last ran.
- `waypoint hook session-start|stop --tool <id>`: run by an AI CLI as a hook. At session start the agent sees the project's Now and newest log entry; at a turn end it is asked once per session for a log entry when the repo changed and the project file didn't. Never fails its tool. `[hooks]` in `waypoint.toml` turns either part off or quiets projects. Logbook spec v1.3.
- MCP 2026-07-28: the local server and the beacon answer `server/discover` and stateless requests, and still serve every older client. The local server adds two prompts, `where_am_i` and `log_this` (slash commands in Claude Code and Gemini CLI).
- Listed in the official MCP Registry as `io.github.STiFLeR7/waypoint-mcp` (published by the release workflow).
- The beacon's `show_chart` shows the chart as a card inside ChatGPT and Claude (MCP Apps). `log_watch` can't be called from a card.

## 0.3.0 — "Fitting out"

- Screen: Backspace goes back (as ←), except while typing a search.
- Manager screen: Enter on a project or AI tool opens its actions (the letter keys stay as shortcuts); a single-choice list's dot follows the cursor.
- Setup screen: with no remembered logbook, the logbook your installed Claude Code rule already uses is offered first ("In use by your AI tools"), so a re-run no longer defaults to a new, empty one.
- The global rule names both MCP server names (`waypoint`, or `waypoint-local` where that name was taken).
- `disconnect --project`: a removal that is refused keeps its paths in the repo's git exclude; Copilot's `.github/mcp.json` counts as Waypoint's only when this repo's own line is in the exclude (not a sibling subproject's); an unreadable exclude file is named in the refusal.
- **The screen** (optional extra `waypoint-mcp[tui]`: Rich + prompt_toolkit): `waypoint setup` is a guided wizard (logbook, roots, projects, AI tools, global or per project, Review, Apply) and `waypoint` alone opens a manager for projects, AI tools and status. Nothing is written before Apply. Without the extra, numbered prompts as before.
- `setup --yes` includes the Codex `writable_roots` change unless `--no-writable-root` is supplied.
- **Beacon:** `log_watch` takes the caller's tool id (default chatgpt), so Codex and Claude Code entries are no longer labelled ChatGPT.
- **`waypoint refresh`**: the plain name for `fix` (`fix` still works).
- **`archive`, `restore`, `remove`, `ignore`**: manage which projects are tracked without editing files by hand.
- **`waypoint status`** (and an MCP `status` tool): the whole install at a glance, with a fix for each problem.
- README leads with what Waypoint does, says which tools give automatic history, and explains what each signal means.
- **Record freshness** (logbook spec v1.2, additive): an optional `- Updated:` line in `## Now`. When the newest log entry is later, the project shows **Now behind log** in DASHBOARD.md, `chart.html` (filterable), `status` and the MCP tools. `dashboard.json` gains `updated`, `now_behind` and `kpi.behind` (schema stays 1).
- **`log_watch` `done`/`next`** (local server and beacon): set Done and Next, and Updated, in the same locked write as the log entry.
- **Global rule**: `waypoint refresh`, pass `done`/`next` to `log_watch`, set `Updated` when editing Now by hand. Run `waypoint rules --install`; until then the chart reports the rule as drifted.
- Fixes: a live project whose Status line says `archived` stays live; `remove --ignore` says what happened if the ignore write fails after the delete.
- **Per-project install**: `waypoint connect <cli> --project <repo>` and `waypoint disconnect`, with the rule as an import, a link or a copy, the MCP entry scoped to the repo, and a local git exclude.
- **MCP name clash**: Waypoint registers as `waypoint-local` when `waypoint` is already another server (it used to refuse), and registers its full path when `waypoint` isn't on PATH.
- **Codex sandbox**: adding the logbook to `writable_roots` is now a reviewed change instead of printed advice.
- **`waypoint status`**: each tool's rule method and per-project installs, plus problems for a rule loaded twice, a broken link and an untrusted Codex folder.

## 0.2.1 — "Draw the chart"

- **`chart.html`**: `waypoint fix` also writes the chart as one self-contained page you open from disk (`--open` opens it). No network requests; the data is embedded as inert JSON (spec §5.2, additive; schema stays 1).
- **Faster local MCP server**: the chart is reused for up to 30 seconds and rebuilt as soon as a project file changes.
- **`fix --push`** skips generated files you gitignore (for example a local-only `chart.html`).
- **Global rule**: also names `chart.html` as generated. Run `waypoint rules --install` to update your tools; until then the chart reports the rule as drifted.

## 0.2.0 — "Leave harbour" (first public release)

Logbook spec v1.1 (additive; existing logbooks keep working, `dashboard.json` stays schema 1).

- **`waypoint setup`**: guided fitting-out (logbook, roots, rules, MCP server); shows every change and asks once; remembers the logbook.
- **`waypoint add <folder>`**: create a project file (never a log entry).
- **`waypoint mcp`** (also `waypoint-mcp`): local stdio MCP server with `take_fix`, `read_chart` and `log_watch` (locked, compare-and-swap appends).
- **`waypoint connect <cli>`**: Claude Code and Codex (Supported), Gemini CLI and GitHub Copilot CLI (Preview), printed config for eight more.
- Safer installers: Claude Code gets its own rule file; marked blocks only; backups; refusal of anything Waypoint doesn't own.
- Project matching by folder at any depth; robust readers that report source health instead of failing.
- Beacon: fail-closed allowlist, configuration template.
- Example logbook (`examples/logbook/`), product README, `SECURITY.md`, `docs/PRIVACY.md`.

## 0.1.0

Private: logbook spec v1, engine, global rule, beacon for ChatGPT.
