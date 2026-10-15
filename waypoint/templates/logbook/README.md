# Logbook

The ship's log for every project, kept by Waypoint.

- `projects/`: one file per project (voyage). Copy `_template.md` to start one.
- `DASHBOARD.md` / `dashboard.json`: the chart. Regenerate it with `waypoint fix --logbook .`.
- `GLOBAL-RULES.md`: the rule Claude Code and Codex follow to log their work here.
- `waypoint.toml`: where projects live (`roots`) and the thresholds.

See `AGENTS.md` for the exact rules.
