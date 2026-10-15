# Changelog

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
