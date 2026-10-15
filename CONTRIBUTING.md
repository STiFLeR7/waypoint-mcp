# Contributing

Issues and pull requests are welcome.

- **The spec comes first.** Behaviour lives in [`docs/LOGBOOK-SPEC.md`](docs/LOGBOOK-SPEC.md). A change to the logbook format, the rule or an access contract updates the spec in the same pull request.
- **Standard library only** for the engine (Python 3.11+). The beacon's only runtime dependency is `@cloudflare/workers-oauth-provider`.
- **Tests:** `python -W error -m unittest`, plus `python examples/build.py --check` on Linux or macOS. Beacon: `cd beacon && npm ci && npm test && npm run typecheck`. Tests never touch your real home folder or run your AI CLIs.
- **Voice:** story terms (voyage, watch, hazard) in prose, CLI output and MCP descriptions, each paired with its plain term on first use; plain terms only in the spec, installed rules and UI labels. See [`docs/STORY.md`](docs/STORY.md).
- **A new AI CLI** is one row in `waypoint/clis.py`; Snippet tier first, Preview once its rule and MCP registration are tested.
