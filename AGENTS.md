# Developing Waypoint

This repo is the **engine**: spec, CLI, templates and tests. It must never contain personal data. Logbooks (users' project files, `dashboard.json`) live in their own repos.

## Commands
- Tests: `python -m unittest` (stdlib only; one test is POSIX-only). Run one test: `python -m unittest tests.test_waypoint.Chart.test_pulse_window_and_feed`.
- CLI from source: `python -m waypoint fix --logbook <dir>`, or `pip install -e .` and then `waypoint …`.
- Test against real data **without touching it**: copy a logbook to a temp folder and run `waypoint fix --logbook <copy>`.

## Architecture
- `docs/LOGBOOK-SPEC.md` is the contract. Change the spec first, then the code, then the tests. Cite spec sections in code comments.
- **Sources** (`waypoint/sources/`): one module per tool (`claude.py`, `codex.py`, `git.py`). Each returns `(sessions, health)` and **never raises on bad data**. Bad records count as `degraded`; a source that can't be read is `unreadable`.
- `paths.py`: normalization and folder mapping (longest root wins). `logbook.py`: read-only parser with section scoping (Log is always last; first occurrence wins).
- `chart.py`: builds `dashboard.json` (contract, schema 1) and `DASHBOARD.md` (presentation), written atomically. `rules.py`: renders `templates/GLOBAL-RULES.md`, checks drift against the render, and installs a managed block.
- `cli.py`: `init`, `fix [--push]`, `rules [--install]`.
- `beacon/` (TypeScript, Cloudflare Worker): the remote MCP server for ChatGPT, behind GitHub OAuth with an allowlist. Tools `take_fix`, `read_chart`, `log_watch` (append-only, through the GitHub Contents API). Tests: `cd beacon && npm test && npm run typecheck`. Contract: spec §8; runbook: `beacon/README.md`.
- Release scope in progress: `docs/release/R0-SCOPE.md`.

## Rules
- Python ≥ 3.11, **stdlib only**, no dependencies.
- Waypoint never changes Status, never archives and never invents log entries. The engine writes only the chart files, and the user's global rule files on `rules --install` (managed block, backups). The beacon only appends log entries.
- Treat every string from sources or project files as untrusted. Escape it in Markdown and UI output.
- The navigation story belongs in the README, docs prose, CLI output and MCP tool descriptions. The spec, the rule text, field names and dashboard UI labels use plain words (`docs/STORY.md`).
- Each phase gets a read-only Codex review. Record the findings and their resolutions in `docs/reviews/`.
