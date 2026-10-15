# R0 exit criteria: evidence (2026-10-15)

Released as `waypoint-mcp` 0.2.0 on PyPI and GitHub release `v0.2.0` on the public repo (fresh history).

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | CI green on Ubuntu, Windows, macOS × 3.11, 3.13; beacon; artifact gate | ✅ | Public repo CI and the `v0.2.0` release run: all jobs green |
| 2 | Installed-package tests (wheel and sdist, outside the checkout) | ✅ | `scripts/smoke_installed.py` in CI (wheel on 3 OSes, sdist on Linux); also against the PyPI release itself |
| 3 | Timed newcomer runs on Windows, macOS, Linux (< 5 min) | ✅ **owner waiver** (2026-10-15): the scripted runs stand as evidence | **Scripted** clean runs from the PyPI release, install to a logged entry: Linux (fresh container) 7 s, Windows (fresh venv) 18 s; macOS covered by the CI smoke test. **Human first-time runs not yet recorded.** |
| 4 | Supported CLIs: a real session calls `take_fix` over stdio and logs with its own id; rules installed | ✅ | Fresh Claude Code (`claude -p`) and Codex (`codex exec`) sessions via `waypoint-local`; `waypoint rules`: all in sync. Preview: GitHub Copilot CLI 1.0.98 wired with `waypoint connect copilot-cli`; a fresh `copilot -p` session called `take_fix` and logged a `copilot-cli` entry ✅. Gemini CLI 0.64.0 wired with `waypoint connect gemini-cli` (rule in sync, MCP registered); its live session is covered by an **owner waiver** (2026-10-15); Gemini stays Preview |
| 5 | `examples/logbook` regenerates byte-identically | ✅ | CI `example` job |
| 5a | Session facts: nested, sibling-prefix, symlinked, outside-root folders | ✅ | `tests/test_waypoint.py` (Matching) |
| 5b | Persistence across CLIs | ✅ | Codex read Claude Code's entry via `read_chart`; `waypoint fix` showed both (last tool codex, 0 unlogged) |
| 6 | Codex review per step, findings closed by id | ✅ | `docs/reviews/r0.*` (private repo) |
| 7 | PyPI release with attestations; install line works | ✅ | PyPI provenance endpoint returns the attestation; `pip install waypoint-mcp` passes the smoke test |
