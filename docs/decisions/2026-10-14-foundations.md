# Foundations: decisions of 2026-10-14

The founding choices for Waypoint, made with the user while turning the PROGRESS-CHECK prototype into a product. The full phase plan is [`docs/PLAN.md`](../PLAN.md); the reviews behind each phase are in `docs/reviews/`.

| # | Decision | Why |
|---|---|---|
| 1 | **Files are the database:** Markdown project files in a git repo, no DB server | Durable and diffable, and every AI tool can read them without an integration |
| 2 | **Three hulls:** engine (this repo), logbook (private data repo), playground (experiments), never mixed | The engine stays shippable; personal data stays private |
| 3 | **The contract is the product:** `docs/LOGBOOK-SPEC.md` is versioned (v1); code serves the spec | Any tool, current or future, can follow the same rules |
| 4 | **Facts automatic, meaning authored:** harvest Claude Code, Codex and git activity; never invent log entries | Facts are free to capture, meaning isn't; the gap between them shows up as *unlogged watches* |
| 5 | **Navigators are adapters:** one reader module per tool, no plugin framework | A new tool means one new module |
| 6 | **Reading before writing:** Status changes and archiving only on the user's say-so; remote writes are append-only | The logbook is the user's record, not the agent's |
| 7 | **One shared global rule** (`GLOBAL-RULES.md`): Claude imports it, Codex holds a copy, and drift is detected | The rules had already drifted once (the Codex-expanded version and the Claude version) |
| 8 | **Pasted text is quoted with `> `** | Plain indentation still renders as a Markdown heading (CommonMark allows up to 3 spaces) |
| 9 | **Story in prose, plain words in contracts** (`docs/STORY.md`) | The metaphor frames things without making the rules ambiguous |
| 10 | **Beacon = remote MCP with OAuth** from day one (Cloudflare Worker, GitHub OAuth, allowlist) | ChatGPT has no local history, and remote access to private data needs real auth |
| 11 | **Generative UI deferred:** if ever, MCP Apps (`ui://waypoint/fix`) rather than A2UI | MCP Apps renders in ChatGPT and Claude today; A2UI renders in neither |
| 12 | **Claude builds, Codex reviews each phase read-only**; findings are fixed before the next phase | Two independent models catch different classes of bugs (see `docs/reviews/`) |
| 13 | **Velocity** = active projects touched in the last 7 days | Simple and computable from data already harvested |
| 14 | **Dashboard UI labels use plain words** (Projects, Active, Attention, Stale, Activity, Archive, System health); the story stays in the prose around them | The dashboard is a working tool; supersedes the plan's "story in labels" |
| 15 | **The global rule template ships in the package** (`waypoint/templates/GLOBAL-RULES.md`), rendered into each logbook | `waypoint init` and `rules` need it at run time |
| 16 | **Phase 6 (dashboard UI/UX) is owned by ChatGPT** (`docs/ui/HANDOFF-CHATGPT.md`); Claude runs the test harness and Codex reviews before it ships | User decision; the schema stays owned by the engine |
| 17 | **Codex is also connected to the beacon** over OAuth, besides Claude Code and ChatGPT | It works; until R1 the beacon writes every entry as `chatgpt`, so Claude Code and Codex log locally instead |

## First public release (R0), decisions of 2026-10-14

Scope: [`docs/release/R0-SCOPE.md`](../release/R0-SCOPE.md) (Codex-reviewed twice; resolutions in `docs/reviews/r0-scope-resolution.md`).

| # | Decision | Why |
|---|---|---|
| 18 | **PyPI name `waypoint-mcp`**; the CLI stays `waypoint`, plus a `waypoint-mcp` entry point | `waypoint` is taken on PyPI; `uvx waypoint-mcp` works in MCP configs. Trademark overlap with HashiCorp/IBM "Waypoint" noted and accepted |
| 19 | **Licence: Apache-2.0** | Explicit patent grant; widely accepted |
| 20 | **The public repo starts from a fresh history** of the reviewed tree; this private repo stays as the archive | History holds local paths and personal context |
| 21 | **Support tiers:** Supported = Claude Code, Codex; Preview = Gemini CLI, Copilot CLI; Snippet = the rest; ChatGPT through the beacon | Only claim what passes acceptance checks |
| 22 | **Local MCP excludes Attention by default**; `setup` asks | Connected clients forward results to their model providers |
| 23 | **The owner's beacon URL isn't published** | It's a private deployment |
| 24 | **Parity evidence is saved** (`docs/reviews/parity-2026-10-14.md`) rather than waived | Closes the Phase 2/3 exit criterion with evidence |
