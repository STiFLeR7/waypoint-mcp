# Privacy

Waypoint keeps everything in files you own. This page lists what it reads, what it writes, and what can leave your machine.

## What it reads

- Your logbook folder (project files, `waypoint.toml`).
- Your AI tools' local history: Claude Code transcripts and `history.jsonl`; Codex's `state_N.sqlite` (read-only). Only the working folder, time and session title are used.
- `git log -1` in each tracked project folder.

## What it writes

- `waypoint fix`: `DASHBOARD.md`, `dashboard.json` and `chart.html` in the logbook. `chart.html` loads nothing from the network.
- `waypoint add`: one new project file. `log_watch`: one log entry, and with `done`/`next` the Done, Next and Updated lines in that project's `## Now`.
- `waypoint setup`, `connect`, `rules`: Waypoint's own rule file or marked block in each AI tool's config folder, and the tool's MCP registration through its own `mcp add` command. Each is shown before it's written and backed up.
- The logbook's location, in `%APPDATA%\waypoint\logbook` (Windows) or `~/.config/waypoint/logbook`.
- If you choose, your end-of-day email address, in `%APPDATA%\waypoint\eod-email` or `~/.config/waypoint/eod-email` (`setup` or `waypoint email --to`; `waypoint email --forget` deletes it).

## What can leave your machine

- **Nothing, on its own.** No telemetry, no network listener, no requests.
- **`--push`** sends the logbook to the git remote you set up. Keep that repo private.
- **The local MCP server** answers the AI tool that started it, and **that tool sends the results to its model provider**, as with anything else it reads. Results include project names, statuses, idle days, Summary, Done, Next and recent log entries. **Attention lines are left out** unless `waypoint.toml` sets:
  ```toml
  [mcp]
  include_attention = true
  ```
  Session titles, file paths, untracked folders, source details and the feed are never returned.
- **The beacon**, if you host one, serves the same kind of fields (no paths, no session titles, no Attention) to clients signed in as an allowlisted GitHub login, and those clients send them to their providers.

Whatever you or your tools write into Summaries and log entries goes wherever those results go. Don't put secrets there.

## The end-of-day email

Optional, and sent only when you ask your AI tool for it (the `send_eod` prompt, or "send my EoD").

- **Your AI tool sends it, not Waypoint.** Waypoint sends nothing and has no network code.
- **It goes through AgentMail**, a third party. AgentMail keeps the email until you delete it, keeps backups for 35 days and logs for up to 365 days, and processes data in the US. The free tier adds a footer.
- **It contains** project names, statuses, unlogged-session counts, each included project's Summary, Done and Next, and every log entry dated today with its tool id. Other projects are named only if they are active or occasional and have a Next. It is HTML plus plain text, with the Waypoint mark attached. Waypoint builds it (`eod_report`) and leaves Attention lines out itself, even with `[mcp] include_attention = true`. Your address is not in it.
- **Your address** stays in one local file (above). It is given only to the AI tool that runs `send_eod`, in the prompt text. It is not in the logbook, `waypoint.toml`, `dashboard.json`, `waypoint status` or the MCP `status` tool.
