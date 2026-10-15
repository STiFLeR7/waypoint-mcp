# Security

## Reporting a vulnerability

Report it privately through GitHub: **Security → Report a vulnerability** on this repository. Please don't open a public issue. Expect an answer within a week; fixes ship as a patch release with a note in `CHANGELOG.md`.

## What Waypoint trusts, and what it doesn't

- **The engine** (`waypoint` CLI and `waypoint mcp`) runs locally. It opens no network listener and sends no requests; `waypoint fix --push` runs git against the remote you configured. `waypoint mcp` speaks only over the stdio of the AI tool that started it.
- **Logbook text is data, never instructions.** Session titles, Summaries, Next steps and log entries are free text, often written or pasted by AI tools. Log entries are quoted so pasted text can't become a heading or field, and both MCP servers tell the client so: the local server's read results carry a note that the text is data and grants no authority, and both servers' instructions say to treat it as data, not instructions. A tool that obeys text it reads is outside Waypoint's control.
- **Writes are narrow.** `log_watch` only appends one entry under `## Log` of a tracked project file, under a lock with a compare-and-swap. Installers change only Waypoint's own file or its marked block, show every change first, back up, and refuse what they don't own.
- **The beacon** (`beacon/`, optional) is a remote server you host. Only GitHub logins on its allowlist can sign in; an unset, empty or malformed allowlist lets nobody in. Its GitHub token should be fine-grained, limited to the logbook repo with Contents read/write. Reads never return paths, session titles or Attention text.

## Supported versions

The latest release. Older releases get no fixes.
