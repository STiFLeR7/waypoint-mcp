# Logbook Spec, v1.1

The **logbook** is the contract every tool (Claude Code, Codex, ChatGPT, or you) agrees on. It's plain files in a folder, ideally a private git repo. Tools write project files following these rules.

**Who writes what (v1.1).** Nothing else writes to a logbook or to a tool's files:
- `waypoint fix` writes only the two chart files (§5).
- `waypoint add` creates one project file from the template, with no log entry, and never overwrites (§1).
- `waypoint init` / `setup` create missing logbook files; `setup` also records which logbook to use (§6).
- Agents, following the rule (§7), append log entries and maintain `## Now` and `- Attention:` lines. They never change Status and never archive without the user's say-so.
- Rule installers write only Waypoint's own rule file and the managed block between Waypoint's markers (§7).

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
└── dashboard.json         # chart for programs (generated, never edit)
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

## Log
### YYYY-MM-DD — <tool id>
- <1–3 short lines; link the repo doc with details>
```

### 2.1 Sections and scoping (safety rule)
- **Header** is everything before the first line matching `^## `.
  - `Path`, `Status`, `Summary`, `Docs` and `Attention` are read **only** from the header.
  - For single-value fields, the **first** occurrence wins.
  - `Attention` collects every occurrence.
- **Now** is the body of the first `## Now` heading before `## Log`. `Done` and `Next` are read only there, first occurrence wins.
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
- Future-dated authored log entries remain available in `logs` but are excluded when choosing the latest activity and the unlogged cutoff. They are flagged in logbook health. The pulse and feed also exclude future events (§5.1).

### 4.5 Untracked folders
Mapped folders (§4.2) with a Claude Code or Codex session within `untracked_days` that aren't tracked (§1) and aren't ignored (§4.3).

## 5. The chart (generated)

The engine writes `DASHBOARD.md` (for people) and `dashboard.json` (for programs) from the same data, each **atomically** (temp file plus rename).
- Both files carry the same `generated` time.
- Readers may briefly see two different generations and should compare `generated` if that matters.
- Concurrent publishers for the same logbook must serialize publication of the pair with a bounded wait. Individual replacements remain atomic; an interrupted publication or failure of the second replacement must be reported rather than described as a fully published chart.
- `DASHBOARD.md`'s layout is presentation, not contract. `dashboard.json` is the contract.

### 5.1 `dashboard.json`, schema v1
```jsonc
{
  "schema": 1,
  "generated": 1760430000.0,                       // epoch seconds
  "config": { "stale_days": {"active": 21, "occasional": 60}, "pulse_days": 35, "untracked_days": 30 },
  "sources": { "claude-code": {"state": "ok", "detail": ""}, "codex": {...}, "git": {...}, "logbook": {...} },
  "rules":   { "logbook": "in-sync|drifted|missing", "claude-code": "...", "codex": "...|shadowed" },
  "kpi": { "tracked": 14, "attention": 7, "velocity": 12, "stale": 0, "untracked": 20, "unlogged": 5 },
  "projects": [ {
      "name", "file", "path", "status", "summary", "docs": [], "done", "next", "attention": [],
      "last", "last_tool", "idle", "claude", "codex", "git", "stale": false, "unlogged": 0,
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
- **Pulse:** one key per local calendar day, today and the `pulse_days − 1` days before it, **zeros included**. It counts Claude Code and Codex sessions (by timestamp) and log entries (by date, per tool id) for tracked projects and untracked folders. Archived projects, ignored folders and future-dated events are excluded.
- **Feed:** the same events within the same window, keeping only those with text. `kind` says where an event came from: `session` (a Claude Code or Codex session, with its title as `text`) or `log` (a log entry in a project file; a log entry's `ts` is local midnight of its date). Sorted newest first, ties broken by project name, then tool. At most `feed_max` items.
- **Ordering:**
  - `projects`: by status order (active, occasional, paused, done, then others), then `last` descending, then name.
  - `untracked`: by `last` descending, then path.
  - `archived`: by name.
- **Untrusted text:** session titles are raw prompts, and summaries and log text are free text. Every renderer (Markdown or UI) must escape them. They may appear in the **local** chart; for remote access see §8.

## 6. `waypoint.toml`

Configuration is validated before collection: roots are nonempty path strings in a list; thresholds are nonnegative integers for `active` and `occasional` only. `untracked_days` is an integer from 1 to 3650, `pulse_days` from 1 to 365, and `feed_max` from 1 to 1000. Invalid types, unsupported threshold statuses, and out-of-range values produce an actionable configuration error.
```toml
roots = ["D:\\"]                 # projects are top-level folders under these roots
stale_days = { active = 21, occasional = 60 }
untracked_days = 30
pulse_days = 35
feed_max = 60
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
- **Local MCP registration (v1.1):** for Claude Code, Codex, Gemini CLI and Copilot CLI, `setup` and `connect` register the server `waypoint` (`waypoint mcp`) through the CLI's own `mcp add` command, user scope. An existing `waypoint` registration that isn't `waypoint mcp` is refused, never replaced (`--no-mcp` skips registration). Uninstall removes only a registration that is `waypoint mcp`. Registration runs only against the real home folder. The CLI's config file is backed up before its command runs; the CLI writes the file itself, so that write is only as atomic as the CLI makes it. Other CLIs get printed config (`waypoint connect <cli>`); Waypoint writes nothing for them.
- **Installers** plan every change and show it before writing; an unchanged file is not written; bytes outside the managed block (including line endings) are kept; a file whose markers are malformed (not exactly one begin before one end, or none) is refused; changed files are backed up, then replaced atomically. Uninstall removes only Waypoint's own file and blocks.
- Drift is shown as an Attention-level item on the chart.

## 8. Access contracts

### 8.1 Remote (beacon, Phase 5)
- **Who:** every request must be authenticated through OAuth, and only an allowlist of identities is accepted. The allowlist **fails closed**: unset, empty, or containing any malformed entry, it admits nobody. No anonymous access.
- OAuth network requests have bounded timeouts. Failed exchanges return a safe recovery response without exposing credentials or accepting an unauthenticated identity.
- A supplied MCP protocol-version header must be supported; invalid or unsupported values are rejected before dispatch. When absent, the server uses its documented compatibility version.
- **Scope:** exactly one logbook. Projects are addressed **by name only**, and the name must be in the server's own list of tracked projects. Paths, traversal and unknown names are rejected.
- Existing names are preserved verbatim, including names longer than the CLI creation limit. Writes target exactly `projects/<name>.md`; names that cannot safely identify that direct child are rejected.
- **Read results may include:** KPIs; and for each project its name, status, idle, last_tool, unlogged, Summary, Done, Next and log entries. These are owner-authored text, returned only to the authenticated owner.
- Reads may include a privacy-safe aggregate collection-health cue `data_complete` (`true`, `false`, or `null` for unknown) without source names or diagnostics. Counts describe observed records; successful collection and zero unlogged sessions are not proof of complete history.
- `read_chart` identifies snapshot fields and their generation time separately from logs read from the current project file. These sources can have different freshness.
- **Reads never include:** session titles, the feed, the pulse's per-session data, untracked folders, file paths, Attention text, source details, or rule state.
- **The single write tool** adds one log entry (tool id `chatgpt`) to an existing tracked project:
  - inserted **directly below the `## Log` line**, with every line of the text quoted with `> ` (§2.2);
  - written through the git host using the file's version (sha);
  - dates use valid calendar years 0001–9999; an omitted date uses the server's documented UTC date, so clients needing a local calendar date supply it;
  - before any retry, it re-reads the file and **skips the write if an identical entry (same date, tool and text) is already the newest**. This only suppresses consecutive duplicates: A, then B, then a retry of A can append A again;
  - it never changes Status, Now, Attention or any other line, never creates or archives files, and stays within a size cap.

### 8.2 Local (`waypoint mcp`, v1.1)
A stdio MCP server started by an AI tool on the owner's machine, for one logbook.
- **Transport:** MCP 2025-06-18 only; newline-delimited UTF-8 JSON-RPC; stdout carries protocol messages only; batches and requests without an id are refused; a request line is at most 64 KB and a reply at most 256 KB; each tool call finishes within 10 s; the process exits when stdin closes. It opens no network listener and makes no outbound requests. The tools that connect forward its results to their own model providers.
- **Reads** are built fresh from the logbook and local tool history, with the same fields as §8.1, plus Done in `read_chart`. Attention lines are included only when `waypoint.toml` sets `[mcp] include_attention = true`. Never included: session titles, the feed, untracked folders, file paths, source details, rule state.
- **Trust boundary:** quoting (§2.2) protects the file's grammar, not the reader. Every tool description and every read result states that project text is the owner's notes: data, never instructions.
- A timed-out write must not later replace a project file. The final replacement and the timeout response share a cancellation gate.
- **`log_watch(project, summary, tool, date?)`** appends one log entry, every line quoted with `> `, below `## Log` with one blank line on each side:
  - `tool` must be a known id (§2.3); `date` defaults to the local date and may differ from it by at most one day;
  - the project must be a live tracked project whose file is a plain file directly in `projects/`; errors never contain paths;
  - writers hold the lock file `<logbook>/.waypoint.lock` (bounded wait, then "busy") and use compare-and-swap: the file is re-hashed just before the atomic replace, and a change (such as an agent's direct edit) restarts the write;
  - transient file-sharing replacement failures are retried within a bounded wait, re-reading the target and checking cancellation before each replacement; persistent failure leaves the file unchanged and returns a safe retryable error;
  - an entry with the same date, tool and text anywhere on that date makes the call a no-op;
  - it never changes Status, Now or Attention and never creates or archives files.

## 9. Versioning
- `dashboard.json` carries `"schema": 1`. Readers should ignore unknown fields.
- **v1.1** (2026-10-14) is additive: tracked-Path matching (§4.2), `waypoint add` (§1), logbook discovery and tool folders (§6), the Claude Code rules file and Codex `shadowed` state (§7), the `logbook` source (§4.1), writer tool ids (§2.3) and the local MCP contract (§8.2). Existing logbooks need no changes; `dashboard.json` stays schema 1.
- Grammar changes that break existing readers bump this spec's major version.
