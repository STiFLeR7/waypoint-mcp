# Waypoint: progress logging

The user tracks every project in a logbook at `{logbook}`. In any session, follow `{logbook}/AGENTS.md`; `{logbook}/README.md` explains the logbook. Each project's own docs are the source of truth; the logbook summarizes them and links to them.

## When to log
- Log only for projects under the roots listed in `{logbook}/waypoint.toml`.
- After **meaningful work** in such a project (a feature, fix, release, decision or change of plan), update its logbook file as the **last step** of the task, and confirm the update in one line.
- Don't log read-only or trivial sessions.

## How to log
- **Find the file:** `{logbook}/projects/*.md` whose `- Path:` matches the project folder.
  - If none matches, copy `projects/_template.md`, fill in the header, and tell the user you created it.
  - Never add a log entry for work that didn't happen.
- **If the `waypoint` MCP server is connected, add the entry with its `log_watch` tool** (project name, 1–3 lines, your tool id): it quotes, checks and locks the file for you. Otherwise edit the file as described next. Either way, update `## Now` and Attention by editing the file.
- **Add an entry** directly below the `## Log` line, so the newest entry comes first. Use the absolute date and your tool id: `claude-code` for Claude Code, `codex` for Codex.

  ```markdown
  ### YYYY-MM-DD — <tool id>
  - <what was done, 1–3 short lines; link the repo doc with details>
  ```

- **Quote copied text:** start every line of pasted or copied text (user-pasted summaries, text from repo docs, tool output) with `> `, so it can't be read as a heading or field.
- **ChatGPT summaries:** when the user pastes a ChatGPT work summary, log it as its own entry with tool id `chatgpt`.
- **Update the rest:** update `## Now` (Done, Next) and the header's `- Attention:` lines. Remove Attention lines that are resolved.
- **Keep the structure:** header (Path, Status, Summary, Docs, Attention), then `## Now`, then `## Log` last.

## Never
- Never change a project's Status, archive it or delete it without the user's say-so. The lifecycle is `active` → `occasional` → `paused` → `done`, or moving the file to `projects/_archive/`. Projects idle over 21 days (active) or 60 days (occasional) are flagged, not changed.
- Never edit `DASHBOARD.md` or `dashboard.json`; they're generated.
- Never track or ignore an untracked folder without asking. `projects/_ignore.txt` lists folders that aren't projects.

## Status requests ("where am I?", "what's going stale?")
1. Run `waypoint fix --logbook {logbook}`.
2. Read `DASHBOARD.md` and lead with Needs attention items and unlogged sessions.
3. Where a project's activity is newer than its last log entry, re-read the repo's Docs and git log before updating its file.

## If you can't write to the logbook
Print the exact log entry and the exact `## Now` changes for the user to paste, and say the logbook wasn't updated.
