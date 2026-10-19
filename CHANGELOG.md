# Changelog

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
