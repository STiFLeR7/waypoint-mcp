# Logbook Spec, v1.3

The **logbook** is the contract every tool (Claude Code, Codex, ChatGPT, or you) agrees on. It's plain files in a folder, ideally a private git repo. Tools write project files following these rules.

**Who writes what (v1.2).** Nothing else writes to a logbook or to a tool's files:
- `waypoint refresh` (alias `fix`) writes only the chart files (§5).
- `waypoint add` creates one project file from the template, with no log entry, and never overwrites (§1).
- `waypoint init` / `setup` create missing logbook files; `setup` also records which logbook to use (§6).
- Gemini CLI's per-repo MCP entry is in `<repo>/.gemini/settings.json`, written by `gemini mcp add --scope project`; when Waypoint created the file, it is listed in the repo's `.git/info/exclude` block (§7.1).
- Agents, following the rule (§7), append log entries and maintain `## Now` (setting `- Updated:` when they edit it) and `- Attention:` lines. They never change Status and never archive without the user's say-so. `log_watch` (§8) adds one entry and, when asked, sets Done, Next and Updated in `## Now` (§8.3).
- `waypoint archive` / `restore` move one project file between `projects/` and `projects/_archive/`; `waypoint remove` deletes one project file at the user's request (`remove --ignore` also adds one line to `projects/_ignore.txt`); `waypoint ignore` adds or removes one line in `projects/_ignore.txt`. All hold the logbook lock and never change a project file's contents.
- Rule installers write only Waypoint's own rule file, the managed block between Waypoint's markers, and links Waypoint made (§7). Per-project installs (§7.1) also write Waypoint's marked blocks in a repo's `.codex/config.toml` and `.git/info/exclude`, and one key in `.github/mcp.json`.

Key words: **must**, **should** and **may** carry their usual RFC meaning. Story terms (see `STORY.md`) never appear in rules; this spec uses plain terms.

---

## 1. Layout

```
<logbook>/
├── waypoint.toml          # config (§6)
├── GLOBAL-RULES.md        # canonical rule text the tools follow (§7)
├── AGENTS.md, CLAUDE.md   # rules for agents working inside the logbook itself
├── projects/
│   ├── <name>.md          # one file per project
│   ├── _template.md       # copied to start a new project
│   ├── _ignore.txt        # folders that are not projects (§4.3)
│   └── _archive/<name>.md # archived projects (moved here, never deleted)
├── DASHBOARD.md           # chart for people (generated, never edit)
├── dashboard.json         # chart for programs (generated, never edit)
└── chart.html             # chart for a browser (generated, never edit)
```

- A project's **name** is its file stem, case preserved. Files starting with `_` are not projects.
- Supported project records are directly under `projects/` or `projects/_archive/`. Nested records and duplicate names are reported as degraded logbook input rather than advertised as remotely addressable projects. Readers preserve existing valid identifiers verbatim; the CLI's 100-character creation limit does not truncate an existing name.
- `waypoint add <folder>` names the file after the folder (or `--name`) and refuses: a name starting with `_` or `.`, a name that isn't a valid file name on Windows, a name already used in `projects/` or `_archive/` (case-insensitive), a folder already tracked (§4.2 comparison) and a folder listed in `_ignore.txt`.
- All files are UTF-8. Writers should keep a file's existing line endings.
- An archived file still counts as **tracked**: its folder never appears as untracked.

## 2. Project file grammar

```markdown
# <name>
- Path: <absolute folder path>
- Status: active | occasional | paused | done
- Summary: <one line>
- Docs: <comma-separated paths, relative to Path>
- Attention: <one item>            # zero or more lines

## Now
- Done: <latest milestone>
- Next: <next step>
- Updated: YYYY-MM-DD             # optional (v1.2): when Now was last brought up to date

## Log
### YYYY-MM-DD — <tool id>
- <1–3 short lines; link the repo doc with details>
```

### 2.1 Sections and scoping (safety rule)
- **Header** is everything before the first line matching `^## `.
  - `Path`, `Status`, `Summary`, `Docs` and `Attention` are read **only** from the header.
  - For single-value fields, the **first** occurrence wins.
  - `Attention` collects every occurrence.
- **Now** is the body of the first `## Now` heading before `## Log`. `Done`, `Next` and `Updated` (v1.2) are read only there, first occurrence wins. `Updated` must be a calendar date `YYYY-MM-DD`; any other text reads as empty and is reported as a logbook diagnostic (§4.1).
- **Log:** the first line matching `^## Log[ \t]*$` starts the log, and **everything after it is log body**, including any later `## ` lines. A heading pasted inside the log can't replace Now or create header fields.
- Section heading names are matched case-insensitively. Other `## ` sections before Log are allowed and ignored.
- Field lines match `^- <Field>:[ \t]*(.+)$`. A missing field reads as empty string.
- `Status` defaults to `active`. A file under `_archive/` is `archived` whatever the field says. Unknown Status values are kept as written and shown in their own group.
- `Docs` is split on `,` and each entry trimmed. Commas inside paths aren't supported. Relative entries resolve against `Path`. Absolute entries (for example a plan file kept outside the repo) are allowed and used as they are. Entries such as `(none)` are kept as text and never linked.

### 2.2 Log entries
- Heading: `^### (\d{4}-\d{2}-\d{2}) [—-] (\S+)[ \t]*$`. Both `—` and `-` are accepted. The tool id is lowercased. A heading with an impossible date is ignored, along with its body.
- Calendar years are 0001–9999. A date that the engine's local timestamp implementation cannot represent is skipped with a logbook diagnostic; it must not abort the chart or silently establish complete history.
- **Body** = the non-blank lines up to the next valid heading. Each line is trimmed and a leading `- ` is stripped. Lines are joined with a single space to form `text`.
- **Order:** the newest entry goes **first**, directly below `## Log`. Readers sort by date descending, keeping file order for entries with the same date (stable sort).
- **Writers must quote any pasted or untrusted text**: prefix every line with `> `. That covers user-pasted summaries, text copied from repo docs and tool output. It's the one injection the grammar can't prevent by itself: an unquoted pasted `### date — x` line would parse as an entry. Quoting works for both readers. Waypoint only matches headings and fields at the start of a line, and in Markdown a `>` line is a blockquote, never a heading or list item outside the quote. (Plain indentation is **not** enough: CommonMark still treats a line indented up to three spaces as a heading.) When building `text`, readers strip one leading `> ` from each line.
- Nothing ever creates a log entry for work that didn't happen. Starting a project from `_template.md` adds **no** entry.

### 2.3 Tool ids
`claude-code`, `codex`, `chatgpt`. Others may be added (lowercase, no spaces). `git` and `log` are reserved for derived values.

**Writers (v1.1)** that validate input (`log_watch`, §8.2) accept only ids matching `^[a-z][a-z0-9-]{1,30}$` from the known list: `claude-code`, `codex`, `chatgpt`, `gemini-cli`, `copilot-cli`, `cursor`, `windsurf`, `kiro`, `goose`, `cline`, `claude-desktop`, `opencode`, `amp`. **Readers** accept and keep any id already in a file.

## 3. Status lifecycle and time

`active` → `occasional` → `paused` → `done`. Archiving means moving the file to `_archive/`.

| Status | Stale when idle **>** (default) |
|---|---|
| active | 21 days |
| occasional | 60 days |
| paused, done, other | never |

- **Time:** all dates use the machine's **local timezone**.
  - `idle` = `floor((now − last) / 86400)` whole days.
  - A project is **stale** when `idle` is strictly greater than its threshold.
- Stale projects are only flagged. Status changes and archiving happen **only** on your instruction, or after asking you.

## 4. Facts the engine derives (read-only sources)

| Source | Reads | Timestamp |
|---|---|---|
| Claude Code | `~/.claude/projects/*/*.jsonl` transcripts. The session id is the file stem, cwd comes from the first record that has `cwd`, and the title from the **last** `ai-title` record. `~/.claude/history.jsonl` adds sessions whose transcript is gone (keyed by `sessionId`, latest `timestamp`, no title). | transcript mtime / last prompt time |
| Codex | `~/.codex/state_N.sqlite`: the highest N whose `threads` table has columns `cwd`, `title`, `updated_at_ms`, `archived`. If it fails that check, the next lower N is tried. Archived threads are **included**. Title = first line, at most 80 characters. | `updated_at_ms` |
| git | `git -C <Path> log -1 --format=%ct` | last commit |
| Log | newest log entry | local midnight of its date |

### 4.1 Source health
- `absent`: the source doesn't exist (fine).
- `ok`: read with no problems.
- `degraded`: some records were skipped (bad JSON line, non-numeric timestamp, missing columns in a row). The detail gives a count.
- `unreadable`: the source exists but nothing could be read (locked DB, no compatible schema).

A source problem must never stop the chart.

`sources` also has a `logbook` entry (v1.1): `ok`, or `degraded` with diagnostics for duplicate Paths (§4.2), unsupported or ambiguous identities, skipped unreadable project records, unsupported dates, and future-dated log entries. One unreadable project file must not stop other readable projects from being charted. Derived counts describe the successfully collected records; readers must qualify completeness when a contributing source is degraded or unreadable. Zero collected sessions is not proof that every session was logged.

### 4.2 Folder mapping
- Paths are compared after normalization: strip the `\\?\` prefix, follow symlinks (absolute paths only), use one slash direction, remove trailing separators, compare case-insensitively on Windows only.
- **Tracked projects first (v1.1).** A cwd belongs to the tracked project whose `- Path:` equals it or is a **separator-bounded ancestor** of it; the longest such Path wins. `/code/app` matches `/code/app/src`, never `/code/app-old`. This works at any depth and outside the roots.
  - Archived projects take part in matching (so their sessions never count and their folders never show as untracked), but a live project wins over an archived one with the same Path.
  - Two files with the same Path: the first by file path wins, and the chart reports the duplicate as a `degraded` `logbook` source (§4.1).
- **Otherwise roots.** A cwd maps to the **first path component below the matching root**, which is how untracked folders are found (§4.5). Example: root `D:\`, cwd `D:\memex\src` gives `D:\memex`. If roots overlap, the **longest** matching root wins. A cwd that is the root itself, or outside every root, maps to nothing.

### 4.3 `_ignore.txt`
- One entry per line. Lines are trimmed, and blank lines or lines starting with `#` are skipped.
- Entries are absolute folder paths, normalized as in §4.2, and match **exactly** (no prefixes).

### 4.4 Derived values per tracked (non-archived) project
- `claude`, `codex`, `git`: newest timestamp from each source (0 if none).
- `last` and `last_tool`: the newest of `(claude, "claude-code")`, `(codex, "codex")`, `(git, "git")` and `(log, <tool id of newest entry>)`. On equal timestamps the priority is log, claude-code, codex, git.
- `unlogged` = the number of Claude Code and Codex sessions on the project whose timestamp is **≥ local midnight following the newest log entry's date**. If the project has **no** log entry, every such session counts.
- `updated` (v1.2): the Now section's `Updated` date, or `""`.
- `now_behind` (v1.2) is true when `updated` is set and the newest authored (not future-dated) log entry's date is later than it. An entry on the same day as `Updated` is not behind. No `Updated` line means `false`.
- Entries are dated, not timed: activity later on the same day as the newest entry counts as logged.
- Future-dated authored log entries remain available in `logs` but are excluded when choosing the latest activity and the unlogged cutoff. They are flagged in logbook health. The pulse and feed also exclude future events (§5.1).

### 4.5 Untracked folders
Mapped folders (§4.2) with a Claude Code or Codex session within `untracked_days` that aren't tracked (§1) and aren't ignored (§4.3).

### 4.6 What the signals mean
- Activity means a tool touched the project (a session, a commit or a log entry). It doesn't mean work got done.
- Unlogged is counted per day: sessions after the day of the newest log entry. Entries have dates, not times.
- Now behind log means the Now section was last updated before the newest log entry, so Where you left off and Next may be out of date. Only projects with an `Updated` line can show it.
- A log entry records what someone wrote. It doesn't verify the code.
- Available context isn't proof that an agent read it.
- `chart.html` and `DASHBOARD.md` are snapshots from the last `waypoint refresh`. The local MCP server reads the logbook live (AI activity is cached for up to 30 seconds). The beacon's dashboard fields are as of the last push; its log entries are read live.

## 5. The chart (generated)

The engine writes `DASHBOARD.md` (for people), `dashboard.json` (for programs) and `chart.html` (for a browser, §5.2) from the same data, each **atomically** (temp file plus rename).
- All three carry the same `generated` time.
- Readers may briefly see two different generations and should compare `generated` if that matters.
- Concurrent publishers for the same logbook must serialize publication of the chart files with a bounded wait. Individual replacements remain atomic; an interrupted publication or failure of a later replacement must be reported rather than described as a fully published chart.
- `DASHBOARD.md`'s and `chart.html`'s layouts are presentation, not contract. `dashboard.json` is the contract.

### 5.1 `dashboard.json`, schema v1
```jsonc
{
  "schema": 1,
  "generated": 1760430000.0,                       // epoch seconds
  "config": { "stale_days": {"active": 21, "occasional": 60}, "pulse_days": 35, "untracked_days": 30 },
  "sources": { "claude-code": {"state": "ok", "detail": ""}, "codex": {...}, "git": {...}, "logbook": {...} },
  "rules":   { "logbook": "in-sync|drifted|missing", "claude-code": "...", "codex": "...|shadowed" },
  "kpi": { "tracked": 14, "attention": 7, "velocity": 12, "stale": 0, "untracked": 20, "unlogged": 5, "behind": 1 },
  "projects": [ {
      "name", "file", "path", "status", "summary", "docs": [], "done", "next", "updated": "", "attention": [],
      "last", "last_tool", "idle", "claude", "codex", "git", "stale": false, "unlogged": 0, "now_behind": false,
      "logs": [ {"ts", "date", "tool", "text"} ]
  } ],
  "archived":  [ {"name", "file", "summary", "done", "logs"} ],
  "untracked": [ {"path", "last", "title"} ],
  "pulse": { "YYYY-MM-DD": {"claude-code": 0, "codex": 0, "chatgpt": 0} },
  "feed":  [ {"ts", "tool", "kind": "session|log", "project", "text"} ]
}
```
- **KPIs:**
  - `tracked` = non-archived projects.
  - `attention` = number of Attention lines.
  - `velocity` = `active` projects with `last ≥ now − 7×86400`.
  - `stale` = projects past their threshold.
  - `untracked` = untracked folders.
  - `unlogged` = projects with `unlogged > 0`.
  - `behind` (v1.2) = projects with `now_behind`.
- **Pulse:** one key per local calendar day, today and the `pulse_days − 1` days before it, **zeros included**. It counts Claude Code and Codex sessions (by timestamp) and log entries (by date, per tool id) for tracked projects and untracked folders. Archived projects, ignored folders and future-dated events are excluded.
- **Feed:** the same events within the same window, keeping only those with text. `kind` says where an event came from: `session` (a Claude Code or Codex session, with its title as `text`) or `log` (a log entry in a project file; a log entry's `ts` is local midnight of its date). Sorted newest first, ties broken by project name, then tool. At most `feed_max` items.
- **Ordering:**
  - `projects`: by status order (active, occasional, paused, done, then others), then `last` descending, then name.
  - `untracked`: by `last` descending, then path.
  - `archived`: by name.
- **Untrusted text:** session titles are raw prompts, and summaries and log text are free text. Every renderer (Markdown or UI) must escape them. They may appear in the **local** chart; for remote access see §8.

### 5.2 `chart.html`
A single self-contained page, opened straight from disk (`file://`).
- It embeds the same document as `dashboard.json` in `<script type="application/json" id="waypoint-data">`. In that copy, `<`, `>`, `&`, U+2028 and U+2029 are written as `\u003c`, `\u003e`, `\u0026`, `\u2028` and `\u2029`, so no value can end the element. `JSON.parse` of the element's text gives back the dashboard.
- The page renders every value as text, never as HTML. It makes no network requests and loads nothing from outside the file; a Content-Security-Policy enforces this.
- It shows only the embedded data. It may also open another `dashboard.json` the user picks; it never writes files.
- An empty logbook shows an empty state, not sample data.
- `waypoint refresh --open` opens the page in the default browser.

## 6. `waypoint.toml`

Configuration is validated before collection: roots are nonempty path strings in a list; thresholds are nonnegative integers for `active` and `occasional` only. `untracked_days` is an integer from 1 to 3650, `pulse_days` from 1 to 365, and `feed_max` from 1 to 1000. Invalid types, unsupported threshold statuses, and out-of-range values produce an actionable configuration error. `[hooks]` may hold only `context` and `nudge` (booleans) and `quiet` (a list of strings).
```toml
roots = ["D:\\"]                 # projects are top-level folders under these roots
stale_days = { active = 21, occasional = 60 }
untracked_days = 30
pulse_days = 35
feed_max = 60
[mcp]
include_attention = false        # §8.2
[hooks]
context = true                   # §7.2: session-start prints the project's Now
nudge = true                     # §7.2: stop may ask once per session for a log entry
quiet = []                       # §7.2: project names never nudged
```
Every key is optional and the defaults are shown above. `roots` defaults to the folder that contains the logbook; a root that is the home folder or a drive/filesystem root is allowed but flagged as very broad by `init` and `setup`.

**Which logbook (v1.1).** Commands use, in order: `--logbook`; the `WAYPOINT_LOGBOOK` environment variable; the path `waypoint setup` saved in `<config>/waypoint/logbook` (`%APPDATA%` on Windows, else `$XDG_CONFIG_HOME` or `~/.config`); the current folder.

**Tool folders.** `~/.claude` and `~/.codex` stand for each tool's config folder: `CLAUDE_CONFIG_DIR` and `CODEX_HOME` move them when set.

## 7. Global rules
- `<logbook>/GLOBAL-RULES.md` is the **canonical, rendered** rule text: the engine's template with `{logbook}` replaced by the logbook path.
- Every check compares against the **template rendered for this logbook**, never against whatever file is on disk. `rules.logbook` reports whether `<logbook>/GLOBAL-RULES.md` itself matches the rendered text.
- **Claude Code (v1.1):** Waypoint's own file `~/.claude/rules/waypoint.md` holds an import line `@<logbook>/GLOBAL-RULES.md`, with a backslash before each space in the path. (v1 put the import in a managed block in `~/.claude/CLAUDE.md`; that is still recognised, and install moves it into the rules file.)
  - `in-sync` = the import line is present **and** `rules.logbook` is `in-sync` (an import of a stale file is `drifted`).
  - If a file holds a pasted copy instead, `in-sync` = the copy equals the canonical text.
  - Otherwise `drifted`, or `missing` when neither the import nor any rule text is found.
- **Codex** has no import syntax and no rules folder, so `~/.codex/AGENTS.md` holds a copy in a managed block. `in-sync` = it contains the canonical text exactly. Compare after normalizing line endings and trailing whitespace; other content in the file is allowed.
  - If `~/.codex/AGENTS.override.md` exists and isn't empty, Codex reads it **instead of** `AGENTS.md`: the state is `in-sync` only if the override holds the canonical text, else `shadowed`.
- **Gemini CLI** (`<GEMINI_CLI_HOME or ~>/.gemini/GEMINI.md`) and **GitHub Copilot CLI** (`<COPILOT_HOME or ~/.copilot>/copilot-instructions.md`) hold a copy in a managed block, checked like Codex (no override file). They are wired only when their executable is on PATH.
- **Local MCP registration (v1.1):** for Claude Code, Codex, Gemini CLI and Copilot CLI, `setup` and `connect` register `waypoint` (`waypoint mcp`), or `waypoint-local` when `waypoint` is another server (for example, a remote beacon). Waypoint refuses registration only when both names are already taken by servers it does not own. A foreign entry is never replaced or removed; uninstall removes only Waypoint's own entry under either name. Global registration uses user scope; per-project registration uses repo scope (§7.1). `--no-mcp` skips registration. Global registration runs only against the real home folder. The CLI's config file is backed up before its command runs; the CLI writes the file itself, so that write is only as atomic as the CLI makes it. Other CLIs get printed config (`waypoint connect <cli>`); Waypoint writes nothing for them.
- **Installers** plan every change and show it before writing; an unchanged file is not written; bytes outside the managed block (including line endings) are kept; a file whose markers are malformed (not exactly one begin before one end, or none) is refused; changed files are backed up, then replaced atomically. Uninstall removes only Waypoint's own file and blocks.
- Drift is shown as an Attention-level item on the chart.

### 7.1 Per-project install (v1.2, additive)
`waypoint connect <cli> --project <repo>` wires one repo and nothing in the home config. `waypoint disconnect <cli> [--project <repo>]` removes exactly what was added.
- **Rule, best method first:** *import* (Claude Code: `<repo>/.claude/rules/waypoint.md` holds Waypoint's marker and `@<logbook>/GLOBAL-RULES.md`; Claude Code asks once per repo before loading a file from outside it); *symlink* (Codex `AGENTS.md`, Copilot CLI `.github/copilot-instructions.md`, only when the file doesn't exist and the OS allows links); *copy* (a managed block, as in §7; always for Gemini CLI `GEMINI.md`, which only imports files inside the repo). A link pointing at any `GLOBAL-RULES.md` is Waypoint's; any other link or file in the way is refused. `--copy` turns Waypoint's link into a file with a copied block. `waypoint rules --install` refreshes drifted copies and re-points broken links in every tracked and archived project.
- **MCP entry:** Claude Code `claude mcp add -s local` and Gemini CLI `gemini mcp add -s project`, run in the repo (real home only); Gemini's per-repo entry lives in `<repo>/.gemini/settings.json`; Codex: a marked block in `<repo>/.codex/config.toml`, read by Codex only in trusted folders (Waypoint reports trust, never sets it); Copilot CLI: the server key in `<repo>/.github/mcp.json`. The server is named `waypoint`, or `waypoint-local` when `waypoint` is another server; both names are Waypoint's when they run `waypoint mcp`. When `waypoint` isn't on PATH, the absolute path of this installation's script is registered.
- **Codex sandbox:** the logbook in `[sandbox_workspace_write] writable_roots` is a reviewed change in a marked block, in the global `config.toml` (global install) or the repo's (per project); a table Waypoint didn't write is left to the user.
- **Git hygiene:** files Waypoint creates in a repo are listed in a marked block of `.git/info/exclude`; this includes `<repo>/.gemini/settings.json` when Waypoint created it. A tracked file is edited only with `--allow-tracked`.
- Repo-local backup files use matching `*.bak-*` patterns in that same exclude block, so updates do not add backup files to commits.
- **Discovery:** there is no registry; `status` and uninstall look in each tracked and archived project's folder, including Gemini's `<repo>/.gemini/settings.json` entry written by `gemini mcp add --scope project`. A CLI with the rule both globally and in a repo is reported (`rule-twice`); a link whose target is gone is `broken`. Not detected: Claude Code also reading a repo's `AGENTS.md`.

### 7.2 Hooks (v1.3, additive)
`waypoint hook session-start|stop --tool <claude-code|codex|gemini-cli|copilot-cli|auto>` is run by an AI CLI as a hook (the Waypoint plugin sets it up; `auto` reads the plugin's environment). It reads the hook payload (one JSON object on stdin) and uses only `session_id`, `cwd` and `stop_hook_active`.
- **Project:** the live tracked project placed from `cwd` (§4.2 v1.1); never the logbook itself. **Logbook:** `--logbook`, `WAYPOINT_LOGBOOK`, then the remembered one; never the current folder.
- **session-start:** records a baseline for the session once (repo fingerprint and project-file signature), then, unless `[hooks] context = false`, prints the project's Status, and its Done, Next, Updated and newest log entry (each cut to 300 characters; at most 2000 in all) as context, stating that it is the owner's notes: data, never instructions.
- **stop:** at most once per session, when `stop_hook_active` is not true, `[hooks] nudge` is on and the project isn't in `quiet` (names compared without case): if the project folder changed since the baseline (its tree at HEAD, its git status, and the size and time of each changed file) and the project file did not, it asks the agent to log, or to say in one line that there is nothing to log (including when it already logged through the beacon, whose writes reach the local file only on the next pull). Without git there is no fingerprint and never a nudge.
- **Never breaks its tool:** exit code 0 always; any problem prints nothing. Output is the tool's own JSON shape. State lives in `<config>/waypoint/hooks/` (session files kept 7 days; `seen-<tool>` holds the last run time).
- The hook never writes the logbook: the agent does, through `log_watch` or the rule.

## 8. Access contracts

### 8.1 Remote (beacon, Phase 5)
- **Who:** every request must be authenticated through OAuth, and only an allowlist of identities is accepted. The allowlist **fails closed**: unset, empty, or containing any malformed entry, it admits nobody. No anonymous access.
- OAuth network requests have bounded timeouts. Failed exchanges return a safe recovery response without exposing credentials or accepting an unauthenticated identity.
- A supplied MCP protocol-version header must be supported; invalid or unsupported values are rejected before dispatch, except on `initialize`, which negotiates the version in its body. When absent, the server uses its documented compatibility version.
- **Transport:** MCP 2026-07-28 and 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05 through `initialize`; on a 2026-07-28 request the `MCP-Protocol-Version`, `Mcp-Method` and (for `tools/call` and `resources/read`) `Mcp-Name` headers must match the body.
- **Scope:** exactly one logbook. Projects are addressed **by name only**, and the name must be in the server's own list of tracked projects. Paths, traversal and unknown names are rejected.
- Existing names are preserved verbatim, including names longer than the CLI creation limit. Writes target exactly `projects/<name>.md`; names that cannot safely identify that direct child are rejected.
- **Read results may include:** KPIs; and for each project its name, status, idle, last_tool, unlogged, now_behind, Summary, Done, Next and log entries, and in `read_chart` its `updated` date (v1.2). These are owner-authored text, returned only to the authenticated owner.
- **`show_chart()`** (read-only, v1.3): the same fields as `take_fix`, with Summary and Next cut to 300 characters, as `structuredContent` for the card view `ui://waypoint/chart-v1` (MCP Apps). The view is inline HTML with no external requests, renders text only, and can't call `log_watch` (`visibility: ["model"]`).
- Reads may include a privacy-safe aggregate collection-health cue `data_complete` (`true`, `false`, or `null` for unknown) without source names or diagnostics. Counts describe observed records; successful collection and zero unlogged sessions are not proof of complete history.
- `read_chart` identifies snapshot fields and their generation time separately from logs read from the current project file. These sources can have different freshness.
- **Reads never include:** session titles, the feed, the pulse's per-session data, untracked folders, file paths, Attention text, source details, or rule state.
- **The single write tool** adds one log entry with the caller's tool id (`tool`, from the known list in §2.3; default `chatgpt`) to an existing tracked project:
  - inserted **directly below the `## Log` line**, with every line of the text quoted with `> ` (§2.2);
  - written through the git host using the file's version (sha);
  - dates use valid calendar years 0001–9999; an omitted date uses the server's documented UTC date, so clients needing a local calendar date supply it;
  - before any retry, it re-reads the file and **skips the write if an identical entry (same date, tool and text) is already the newest**. This only suppresses consecutive duplicates: A, then B, then a retry of A can append A again;
  - with `done`/`next` (v1.2) it also sets those lines and `- Updated:` in `## Now` in the same commit (§8.3); it never changes Status, Attention or any other line, never creates or archives files, and stays within a size cap.

### 8.2 Local (`waypoint mcp`, v1.1)
A stdio MCP server started by an AI tool on the owner's machine, for one logbook.
- **Transport:** MCP 2026-07-28 (each request carries its version and client capabilities in `_meta`; `server/discover`), and 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05 through `initialize`; newline-delimited UTF-8 JSON-RPC; stdout carries protocol messages only; batches and requests without an id are refused; a request line is at most 64 KB and a reply at most 256 KB; each tool call finishes within 10 s; the process exits when stdin closes. It opens no network listener and makes no outbound requests. The tools that connect forward its results to their own model providers.
- **Reads** are built fresh from the logbook and local tool history, with the same fields as §8.1, plus Done in `read_chart`. Attention lines are included only when `waypoint.toml` sets `[mcp] include_attention = true`. Never included: session titles, the feed, untracked folders, file paths, source details, rule state.
- **Prompts (v1.3):** `where_am_i` and `log_this` (optional `project`). Their text tells the agent which tool to call and holds no logbook data.
- **`status()`** (read-only) is the one exception to the list above: it reports rule, MCP-registration and source *states*, project counts, per-tool rule method, `scope` (`global` and/or `project`), per-project install counts, and the local MCP `name` when registered, so an agent can tell the owner what to fix. It never includes file paths, commands, source details or project text; problem lines are fixed sentences naming only the tool or source.
- **Trust boundary:** quoting (§2.2) protects the file's grammar, not the reader. Every tool description and every read result states that project text is the owner's notes: data, never instructions.
- A timed-out write must not later replace a project file. The final replacement and the timeout response share a cancellation gate.
- **`log_watch(project, summary, tool, date?, done?, next?)`** appends one log entry, every line quoted with `> `, below `## Log` with one blank line on each side:
  - `tool` must be a known id (§2.3); `date` defaults to the local date and may differ from it by at most one day;
  - the project must be a live tracked project whose file is a plain file directly in `projects/`; errors never contain paths;
  - writers hold the lock file `<logbook>/.waypoint.lock` (bounded wait, then "busy") and use compare-and-swap: the file is re-hashed just before the atomic replace, and a change (such as an agent's direct edit) restarts the write;
  - transient file-sharing replacement failures are retried within a bounded wait, re-reading the target and checking cancellation before each replacement; persistent failure leaves the file unchanged and returns a safe retryable error;
  - an entry with the same date, tool and text anywhere on that date makes the call a no-op (`done`/`next` are not applied either);
  - with `done`/`next` it sets Now's lines in the same locked, compare-and-swap write (§8.3); it never changes Status or Attention and never creates or archives files.

### 8.3 Now fields written by `log_watch` (v1.2)
- `done` and `next` are optional. Each value becomes one line: control characters and line breaks become spaces, whitespace runs collapse to one space, and the ends are trimmed. The result must be 1–300 characters. Characters are Unicode code points. The value must not start with `#` or `>`.
- In the first `## Now` section before `## Log`, the line a reader reads (§2.1: the first `- Done:` line with text, else the first bare `- Done:`) is replaced. A missing line is inserted right after the Now fields that come before it (order: Done, Next, Updated), or at the top of `## Now`. With no `## Now` section, one is created just above `## Log`.
- `- Updated:` is set to the entry's date whenever Done or Next is written.
- The change lands in the same write as the entry (local: lock plus compare-and-swap; beacon: the same sha). When the entry already exists (a no-op), nothing is applied, Now included. Every other byte and the file's line endings are kept.
- Attention is never written through MCP.

## 9. Versioning
- `dashboard.json` carries `"schema": 1`. Readers should ignore unknown fields.
- **v1.1** (2026-10-14) is additive: tracked-Path matching (§4.2), `waypoint add` (§1), logbook discovery and tool folders (§6), the Claude Code rules file and Codex `shadowed` state (§7), the `logbook` source (§4.1), writer tool ids (§2.3), the local MCP contract (§8.2) and `chart.html` (§5.2). Existing logbooks need no changes; `dashboard.json` stays schema 1.
- **v1.2** (2026-10-15) is additive: the optional `Updated` line in `## Now` (§2.1), `updated`, `now_behind` and `kpi.behind` (§4.4, §5.1), `log_watch` `done`/`next` (§8.3), and per-project install (§7.1). Existing logbooks need no changes; `dashboard.json` stays schema 1.
- **v1.3** (2026-10-19) is additive: hooks and `[hooks]` (§6, §7.2); MCP 2026-07-28, prompts and `show_chart` and its view (§8.1, §8.2). Existing logbooks need no changes; `dashboard.json` stays schema 1.
- Grammar changes that break existing readers bump this spec's major version.
