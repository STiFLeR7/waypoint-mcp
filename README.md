# Waypoint
<!-- mcp-name: io.github.STiFLeR7/waypoint-mcp -->

[![M8ven Score](https://m8ven.ai/badge/mcp/stifler7/waypoint-mcp)](https://m8ven.ai/mcp/stifler7/waypoint-mcp?s=readme)

**Waypoint keeps one project log across your AI coding tools, so you can see where work stopped and what comes next.**

**Website:** [waypoint.stifler.in](https://waypoint.stifler.in)

You switch between projects and between Claude Code, Codex and ChatGPT. Each tool has its own memory, but none of them knows what the others did. Waypoint gives every tool the same record per project (what it is, where you left off, what's next, what needs attention), and shows where that record has fallen behind the actual activity.

*The story:* each project is a voyage, each AI tool a navigator, and the logbook is the ship's log they all write to. The dashboard is the chart.

It deals with two kinds of truth:

- **Facts** (where and when): which project, which tool, when, the last commit. Waypoint **harvests these automatically** from the tools' own local history and from git.
- **Meaning** (what and why): what got done, what's next, what's in the way. The AI tool **writes this down** as a short log entry, following one shared rule.

Where the two disagree, the chart says so. A tool that worked on a project but logged nothing leaves an **unlogged watch** (session). That's how the chart keeps itself honest, even when a tool forgets the rule.

```
$ waypoint refresh
Dashboard refreshed: 3 projects, 1 active this week, 1 attention item, 1 with unlogged sessions, 1 stale, 1 untracked folder.
```

That's the fictional logbook in [`examples/logbook/`](examples/logbook/): its [chart](examples/logbook/DASHBOARD.md) shows each case. `waypoint refresh` also writes `chart.html`, the same chart as a page you open in your browser.

## Get started

Requires Python 3.11 or newer; no other dependencies. The arrow-key screen is optional: install `waypoint-mcp[tui]` (adds Rich and prompt_toolkit) and run `waypoint`.

**macOS / Linux**
```bash
pipx install waypoint-mcp         # or: python -m pip install waypoint-mcp
waypoint setup                    # fitting out: logbook, roots, the rule and MCP server for each AI CLI it finds
waypoint add ~/code/my-project    # chart a voyage (project); once per project
waypoint refresh                  # refresh the dashboard: DASHBOARD.md, dashboard.json and chart.html (--open opens it)
waypoint status                   # everything at a glance, with fixes for problems
waypoint archive <project>        # hide a finished project, keep its log (also: restore, remove, ignore)
```

**Windows (PowerShell)**
```powershell
py -m pip install waypoint-mcp
waypoint setup
waypoint add $HOME\code\my-project
waypoint refresh
```

- **`waypoint` not found?** Its install folder isn't on PATH, and AI tools need it there too (they start `waypoint mcp`). Installed with pipx: run `pipx ensurepath`, then open a new terminal. Installed with pip: add the scripts folder pip warned about to PATH; until then `python -m waypoint …` (Windows: `py -m waypoint …`) runs it with that same Python.
- **The screen** (`pipx install "waypoint-mcp[tui]"`): `waypoint setup` walks through logbook, roots, projects and AI tools, shows every change in a Review and writes nothing before Apply; `waypoint` alone then manages projects (archive, restore, remove, ignore, track) and tools (connect, disconnect, fix drift) and shows status. Keys: ↑↓ move, Space toggle, Enter next, ← back, `/` search, Esc quit. Every action is also a plain command; without the extra, or with any `setup` flag, you get numbered prompts. `NO_COLOR` is honoured.
- `setup` shows every file it would change and asks once. It remembers the logbook, so later commands need no `--logbook`. It never edits your own `CLAUDE.md` or `AGENTS.md` text: it adds Waypoint's own file or a marked block, and `waypoint rules --uninstall` removes exactly those.
- Make the logbook a **private** git repo, then `waypoint refresh --push` pulls, redraws, commits only logbook data, and pushes.
- From a clone of this repo: `python -m pip install -e .`

## Who's aboard

Automatic session history (which project, which tool, when) comes from **Claude Code and Codex** only. The other tools follow the rule and can use the MCP tools, but their sessions don't show up on their own.

| Navigator (AI CLI) | Tier | What Waypoint wires |
|---|---|---|
| **Claude Code** | Supported | the rule, the local MCP server, session facts |
| **Codex** (CLI and desktop) | Supported | the rule, the local MCP server, session facts |
| Gemini CLI | Preview | the rule, the local MCP server |
| GitHub Copilot CLI | Preview | the rule, the local MCP server |
| Cursor, Windsurf, Kiro, Cline, Claude Desktop, Goose, opencode, Amp | Snippet | `waypoint connect <cli>` prints the config to paste; writes nothing |
| ChatGPT | Beacon | remote MCP server you host ([Raise the beacon](#raise-the-beacon)) |

- **The rule** tells the tool to add a log entry after meaningful work. **The local MCP server** (`waypoint mcp`, stdio) gives it five tools: `take_fix` (the overview), `read_chart` (one project), `log_watch` (append one log entry, with the tool's own id, under a lock; with `done`/`next` it also updates Done, Next and Updated in Now), `status` (whether Waypoint is set up right: states and counts only, no paths), and `eod_report` (today's end-of-day email, built and read-only). It also offers three prompts (slash commands in Claude Code, as `/mcp__waypoint__<name>`, and Gemini CLI, as `/<name>`): `where_am_i`, `log_this` and `send_eod`.
- `waypoint connect <cli>` wires one CLI at any time, for every project. Add `--project <repo>` (repeatable) to wire only that repo: the rule goes into the repo (an import, a link to the logbook's rule, or a copied block) and the MCP entry is scoped to it; files Waypoint creates there stay out of git through `.git/info/exclude`. `waypoint disconnect <cli> [--project <repo>]` removes only what Waypoint added.
- MCP servers are registered through each CLI's own `mcp add` command where it has one (Codex and Copilot CLI per repo: Waypoint edits their repo file). If another server already uses the name `waypoint`, Waypoint registers as `waypoint-local`; `--no-mcp` installs only the rule. For Codex, Waypoint also offers to add the logbook to its sandbox's `writable_roots` (`--no-writable-root` skips it).
- `waypoint rules` reports each tool's rule: in sync, drifted, missing, or shadowed (Codex reads an `AGENTS.override.md` instead).
- **Known limits:** Claude Code deletes transcripts after 30 days by default. The Claude Code and Codex history formats are unofficial; the chart's source health says when a reader breaks.

## The plugin: hooks that remind

A rule asks the agent to log; the plugin's hooks remind it. At session start the agent sees the project's Now and newest log entry; at the end of a turn where the repo changed and the project file didn't, it's asked once to log (or to say there's nothing to log). The hooks only remind: the agent still writes the entry.

Install Waypoint first (`pipx install waypoint-mcp`, so `waypoint` is on PATH), run `waypoint setup --no-mcp` (or, after `waypoint setup`, `waypoint connect <cli> --plugin`, which removes the separate MCP entry `setup` made), then:

| CLI | Install |
|---|---|
| Claude Code | `claude plugin marketplace add STiFLeR7/waypoint-mcp` then `claude plugin install waypoint@waypoint-mcp` |
| Codex | `codex plugin marketplace add STiFLeR7/waypoint-mcp`, `codex plugin add waypoint@waypoint-mcp`, then trust the hooks in `/hooks` (again after each update) |
| Copilot CLI | `copilot plugin install STiFLeR7/waypoint-mcp` |
| Gemini CLI | `gemini extensions install https://github.com/STiFLeR7/waypoint-mcp` |

If you wrote Waypoint hooks into a tool's own settings by hand, remove them once the plugin is installed, or the reminder can fire twice.

Under `--tool auto` the hook tells tools apart by the plugin environment (`GEMINI_SESSION_ID`, `COPILOT_PLUGIN_ROOT`, `PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT`); don't set those globally.

The plugin brings the MCP server too, so `waypoint connect <cli> --plugin` removes Waypoint's own separate MCP entry. `waypoint status` shows when each tool's hooks last ran. Turn parts off in `waypoint.toml`: `[hooks] context = false`, `nudge = false`, or `quiet = ["project"]`.

## End-of-day email

Optional. At the end of the day, ask your AI tool to send the day's report: type `/mcp__waypoint__send_eod` in Claude Code (`/mcp__waypoint-local__send_eod` if Waypoint is registered as `waypoint-local`) or `/send_eod` in Gemini CLI, or just say "send my EoD". Waypoint builds the email from your logbook (`eod_report`): a dark, email-safe HTML page with a plain-text twin, the Waypoint mark attached, and every log entry dated today. Your AI tool sends exactly that through [AgentMail](https://agentmail.to), which you connect once. Waypoint itself sends nothing and never sees your inbox.

- `waypoint setup` can ask for your address (default No; `--yes` never asks), or run `waypoint email --to you@example.com`. It is kept in one local file; `waypoint email --forget` deletes it.
- `waypoint email` prints the AgentMail steps for each AI tool it finds, and the address it remembers.
- ChatGPT: type "send my EoD" in a chat that has both the Waypoint beacon and AgentMail apps. Using both apps in one chat is not yet confirmed.
- Attention lines are never in the email; Waypoint leaves them out itself. See [Privacy](docs/PRIVACY.md).

## Reading the chart

| On the chart | Plain meaning |
|---|---|
| **Voyage**: underway / coasting / at anchor / in port | project: active / occasional / paused / done |
| **Hazards** | `- Attention:` lines, plus drifted rules and unreadable sources |
| **Unlogged watches** | AI sessions after the day of the project's last log entry |
| **Now behind log** | `## Now` was last updated before the newest log entry |
| **Adrift** | idle past its threshold (21 days if active, 60 if occasional) |
| **Uncharted waters** | folders with recent AI activity but no project file |
| **Harbour** | `projects/_archive/`: finished voyages, never deleted |

Waypoint never changes a project's Status, never archives, and never writes a log entry on its own: it flags, and you decide.

## What the signals mean

- **Activity** means a tool touched the project (a session, a commit or a log entry). It doesn't mean work got done.
- **Unlogged sessions** are Claude Code and Codex sessions from the day after the newest log entry onward. Log entries carry a date, not a time, so work later on the same day as an entry counts as logged.
- **Now behind log** means the project's `## Now` was last updated (its `- Updated:` date) before the newest log entry, so *Where you left off* and *Next* may be out of date. Projects without an `Updated` line never show it; `log_watch` sets it when you pass `done`/`next`.
- **A log entry** records what someone wrote down. It doesn't verify the code.
- **Available context** isn't proof that an agent read it.
- **Freshness:** `chart.html` and `DASHBOARD.md` are snapshots from the last `waypoint refresh`. The local MCP server reads the logbook live (AI activity is cached for up to 30 seconds). The beacon's dashboard fields are as of the last push; its log entries are read live.

## Privacy

- **Nothing leaves the machine on its own.** The engine has no listener, sends no network requests, and keeps no telemetry. `--push` runs git against the remote you configured.
- **The local MCP server answers only the AI tool that started it.** That tool sends the results to its model provider, as it does with anything it reads. Results hold project names, statuses, Summary, Done, Next and recent log entries. **Attention lines are left out** unless `waypoint.toml` sets `[mcp] include_attention = true`. Session titles, file paths, untracked folders and the feed are never returned.
- **The end-of-day email is sent by your AI tool, only when you ask,** through AgentMail (a third party). The address stays in one local file and is given only to the tool that runs `send_eod`. Details: [`docs/PRIVACY.md`](docs/PRIVACY.md).
- Log text is free text: what you or your tools write there goes wherever those results go. Returned text is data for the tool, never instructions.

## The three hulls

| Hull | What | Where |
|---|---|---|
| **Engine** | This repo: the spec, the `waypoint` CLI, templates, tests | installed from PyPI |
| **Logbook** | *Your* data: one Markdown file per project, plus the chart | its own private git repo |
| **Playground** | Experiments and prototypes | wherever you like |

They're never mixed: the engine ships, and the logbook stays yours.

## Raise the beacon

ChatGPT keeps no local history and can't start a local server. The **beacon** (`beacon/`, advanced) is a remote MCP server you host on Cloudflare Workers behind GitHub OAuth, answering only to your GitHub account. `take_fix` and `read_chart` read the chart, `show_chart` shows it as a card inside ChatGPT and Claude (MCP Apps); `log_watch` appends one `chatgpt` log entry to the logbook repo. Setup takes a Cloudflare account, a GitHub OAuth app and a fine-grained token: [`beacon/README.md`](beacon/README.md). Without it, paste a ChatGPT summary into the project's file as a `chatgpt` entry.

## Spec

The logbook format, the rule, the chart's JSON and every access contract are in [`docs/LOGBOOK-SPEC.md`](docs/LOGBOOK-SPEC.md) (v1.5). The story's glossary and voice are in [`docs/STORY.md`](docs/STORY.md).

## Development

```bash
python -m unittest                # stdlib only (the screen's tests skip)
python -m pip install -e ".[tui]" && python -m unittest   # with the screen's tests
python examples/build.py --check  # the example chart (Linux or macOS)
cd beacon && npm test             # the beacon
```

Design decisions are in [`docs/decisions/`](docs/decisions/); the release plan is [`docs/release/R0-SCOPE.md`](docs/release/R0-SCOPE.md).
