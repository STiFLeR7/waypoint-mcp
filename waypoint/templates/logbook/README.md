# Logbook

The ship's log for every project, kept by Waypoint.

- `projects/`: one file per project (voyage). Copy `_template.md` to start one.
- `DASHBOARD.md` / `dashboard.json` / `chart.html`: the chart. Regenerate it with `waypoint fix --logbook .`; open `chart.html` in a browser.
- `GLOBAL-RULES.md`: the rule Claude Code and Codex follow to log their work here.
- `waypoint.toml`: where projects live (`roots`) and the thresholds.

See `AGENTS.md` for the exact rules.
