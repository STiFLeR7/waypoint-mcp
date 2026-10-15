# Waypoint

**Every voyage (project), every navigator (AI tool), one chart.**

## Dead reckoning

A dozen projects at once, and three or four AI tools taking turns at the helm: Claude Code in the morning, Codex in the afternoon, ChatGPT for the hard question at night. None of them remembers the previous watch (session). So every session starts with *dead reckoning*: where was this, what changed, who touched it last? Projects drift. Side work goes uncharted. Finished work never makes it into port.

Waypoint is the **ship's log** (one Markdown file per project) that every navigator writes to, and the **chart** (dashboard) that shows all of it at once.

It deals with two kinds of truth:

- **Facts** (where and when): which project, which tool, when, the last commit. Waypoint **harvests these automatically** from the tools' own local history and from git.
- **Meaning** (what and why): what got done, what's next, what's in the way. The AI tool **writes this down** as a short log entry, following one shared rule.

Where the two disagree, the chart says so. A tool that worked on a project but logged nothing leaves an **unlogged watch** (session). That's how the chart keeps itself honest, even when a tool forgets the rule.

```
$ waypoint fix
Fix taken (sync): 3 voyages (projects), 1 underway (active) this week, 1 hazard (attention item), 1 with unlogged watches (sessions), 1 adrift (stale), 1 uncharted (untracked folders).
```

That's the fictional logbook in [`examples/logbook/`](examples/logbook/): its [chart](examples/logbook/DASHBOARD.md) shows each case. `waypoint fix` also writes `chart.html`, the same chart as a page you open in your browser.

## Take a fix

Requires Python 3.11 or newer; no other dependencies.

**macOS / Linux**
```bash
pipx install waypoint-mcp         # or: python -m pip install waypoint-mcp
waypoint setup                    # fitting out: logbook, roots, the rule and MCP server for each AI CLI it finds
waypoint add ~/code/my-project    # chart a voyage (project); once per project
waypoint fix                      # take a fix (sync): redraws DASHBOARD.md, dashboard.json and chart.html (--open opens it)
```

**Windows (PowerShell)**
```powershell
py -m pip install waypoint-mcp
waypoint setup
waypoint add $HOME\code\my-project
waypoint fix
```

- **`waypoint` not found?** Its install folder isn't on PATH, and AI tools need it there too (they start `waypoint mcp`). Installed with pipx: run `pipx ensurepath`, then open a new terminal. Installed with pip: add the scripts folder pip warned about to PATH; until then `python -m waypoint …` (Windows: `py -m waypoint …`) runs it with that same Python.
- `setup` shows every file it would change and asks once. It remembers the logbook, so later commands need no `--logbook`. It never edits your own `CLAUDE.md` or `AGENTS.md` text: it adds Waypoint's own file or a marked block, and `waypoint rules --uninstall` removes exactly those.
- Make the logbook a **private** git repo, then `waypoint fix --push` pulls, redraws, commits only logbook data, and pushes.
- From a clone of this repo: `python -m pip install -e .`

## Who's aboard

| Navigator (AI CLI) | Tier | What Waypoint wires |
|---|---|---|
| **Claude Code** | Supported | the rule, the local MCP server, session facts |
| **Codex** (CLI and desktop) | Supported | the rule, the local MCP server, session facts |
| Gemini CLI | Preview | the rule, the local MCP server |
| GitHub Copilot CLI | Preview | the rule, the local MCP server |
| Cursor, Windsurf, Kiro, Cline, Claude Desktop, Goose, opencode, Amp | Snippet | `waypoint connect <cli>` prints the config to paste; writes nothing |
| ChatGPT | Beacon | remote MCP server you host ([Raise the beacon](#raise-the-beacon)) |

- **The rule** tells the tool to add a log entry after meaningful work. **The local MCP server** (`waypoint mcp`, stdio) gives it three tools: `take_fix` (the overview), `read_chart` (one project), `log_watch` (append one log entry, with the tool's own id, under a lock).
- `waypoint connect <cli>` wires one CLI at any time. MCP servers are registered through each CLI's own `mcp add` command. An existing server already named `waypoint` is never replaced; `--no-mcp` installs only the rule.
- `waypoint rules` reports each tool's rule: in sync, drifted, missing, or shadowed (Codex reads an `AGENTS.override.md` instead).
- **Known limits:** Claude Code deletes transcripts after 30 days by default. The Claude Code and Codex history formats are unofficial; the chart's source health says when a reader breaks.

## Reading the chart

| On the chart | Plain meaning |
|---|---|
| **Voyage**: underway / coasting / at anchor / in port | project: active / occasional / paused / done |
| **Hazards** | `- Attention:` lines, plus drifted rules and unreadable sources |
| **Unlogged watches** | AI sessions after the day of the project's last log entry |
| **Adrift** | idle past its threshold (21 days if active, 60 if occasional) |
| **Uncharted waters** | folders with recent AI activity but no project file |
| **Harbour** | `projects/_archive/`: finished voyages, never deleted |

Waypoint never changes a project's Status, never archives, and never writes a log entry on its own: it flags, and you decide.

## Privacy

- **Nothing leaves the machine on its own.** The engine has no listener, sends no network requests, and keeps no telemetry. `--push` runs git against the remote you configured.
- **The local MCP server answers only the AI tool that started it.** That tool sends the results to its model provider, as it does with anything it reads. Results hold project names, statuses, Summary, Done, Next and recent log entries. **Attention lines are left out** unless `waypoint.toml` sets `[mcp] include_attention = true`. Session titles, file paths, untracked folders and the feed are never returned.
- Log text is free text: what you or your tools write there goes wherever those results go. Returned text is data for the tool, never instructions.

## The three hulls

| Hull | What | Where |
|---|---|---|
| **Engine** | This repo: the spec, the `waypoint` CLI, templates, tests | installed from PyPI |
| **Logbook** | *Your* data: one Markdown file per project, plus the chart | its own private git repo |
| **Playground** | Experiments and prototypes | wherever you like |

They're never mixed: the engine ships, and the logbook stays yours.

## Raise the beacon

ChatGPT keeps no local history and can't start a local server. The **beacon** (`beacon/`, advanced) is a remote MCP server you host on Cloudflare Workers behind GitHub OAuth, answering only to your GitHub account. `take_fix` and `read_chart` read the chart; `log_watch` appends one `chatgpt` log entry to the logbook repo. Setup takes a Cloudflare account, a GitHub OAuth app and a fine-grained token: [`beacon/README.md`](beacon/README.md). Without it, paste a ChatGPT summary into the project's file as a `chatgpt` entry.

## Spec

The logbook format, the rule, the chart's JSON and every access contract are in [`docs/LOGBOOK-SPEC.md`](docs/LOGBOOK-SPEC.md) (v1.1). The story's glossary and voice are in [`docs/STORY.md`](docs/STORY.md).

## Development

```bash
python -m unittest                # stdlib only
python examples/build.py --check  # the example chart (Linux or macOS)
cd beacon && npm test             # the beacon
```

Design decisions are in [`docs/decisions/`](docs/decisions/); the release plan is [`docs/release/R0-SCOPE.md`](docs/release/R0-SCOPE.md).
