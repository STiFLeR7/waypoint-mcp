# Developing Waypoint

This repo is the **engine**: spec, CLI, templates and tests. It must never contain personal data. Logbooks (users' project files, `dashboard.json`) live in their own repos.

## Commands
- Tests: `python -m unittest` (stdlib only; one test is POSIX-only). Run one test: `python -m unittest tests.test_waypoint.Chart.test_pulse_window_and_feed`.
- Chart page in a real browser (local only, needs Playwright + Edge; CI runs no browser): `python scripts/chart_browser_check.py <shots dir> [chart.html ...]`.
- CLI from source: `python -m waypoint fix --logbook <dir>`, or `pip install -e .` and then `waypoint …`.
- Test against real data **without touching it**: copy a logbook to a temp folder and run `waypoint fix --logbook <copy>`.
- End to end: `tests/test_end_to_end.py` runs one full story (setup, add, fix, MCP, archive, uninstall) on a temp machine. `python scripts/e2e_live.py [logbook]` does the same read-only checks on a copy of a real logbook (not run in CI).

## Architecture
- `docs/LOGBOOK-SPEC.md` is the contract. Change the spec first, then the code, then the tests. Cite spec sections in code comments.
- **Sources** (`waypoint/sources/`): one module per tool (`claude.py`, `codex.py`, `git.py`). Each returns `(sessions, health)` and **never raises on bad data**. Bad records count as `degraded`; a source that can't be read is `unreadable`.
- `paths.py`: normalization and folder mapping (longest root wins). `logbook.py`: read-only parser with section scoping (Log is always last; first occurrence wins).
- `chart.py`: builds `dashboard.json` (contract, schema 1), `DASHBOARD.md` and `chart.html` (presentation, from `templates/chart.html`), written atomically under one lock. `rules.py`: renders `templates/GLOBAL-RULES.md`, checks drift against the render, and installs a managed block.
- `cli.py`: `setup`, `add`, `archive`/`restore`/`remove`/`ignore` (`manage.py`), `refresh` (alias `fix`) `[--push]`, `status` (`status.py`), `connect`/`disconnect` (`clis.py`; per repo: `project.py`), `rules`, `email`, `mcp`, `hook` (`hook.py`), `init`. `waypoint` alone opens the screen (`tui/`, optional extra).
- `mcp.py`: the local stdio MCP server (spec §8.2): tools `take_fix`, `read_chart`, `log_watch` (`watch.py`, locked append), `status`, `eod_report` (`eod.py`: the HTML and text end-of-day email, no Attention); prompts `where_am_i`, `log_this`, `send_eod`.
- Plugin packaging: `.claude-plugin/`, `gemini-extension.json` and `hooks/` install the hooks and MCP server (spec §7.2).
- `beacon/` (TypeScript, Cloudflare Worker): the remote MCP server for ChatGPT, behind GitHub OAuth with an allowlist. Tools `take_fix`, `read_chart`, `show_chart` (the chart card, MCP Apps), `log_watch` (append-only, through the GitHub Contents API). Tests: `cd beacon && npm test && npm run typecheck`. Contract: spec §8; runbook: `beacon/README.md`.
- Versions: the package version lives in `pyproject.toml`, `waypoint/__init__.py`, `server.json` (twice), `gemini-extension.json` and `.claude-plugin/plugin.json`; bump all together, and push a matching `v*` tag to release (`.github/workflows/release.yml`). The spec version is `SPEC` in `status.py` and the `--version` string in `cli.py`. Changes per release: `CHANGELOG.md`.

## Rules
- Python ≥ 3.11, **stdlib only**, no dependencies.
- Waypoint never changes Status, never archives and never invents log entries. The engine writes only what the spec's "Who writes what" lists (chart files, Waypoint's own rule files and marked blocks, project files only on the user's command). The beacon only appends log entries.
- Treat every string from sources or project files as untrusted. Escape it in Markdown and UI output.
- The navigation story belongs in the README, docs prose, CLI output and MCP tool descriptions. The spec, the rule text, field names and dashboard UI labels use plain words (`docs/STORY.md`).
- Each phase gets a read-only Codex review. Record the findings and their resolutions in `docs/reviews/`.
