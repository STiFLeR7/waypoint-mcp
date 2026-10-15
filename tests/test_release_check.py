"""The public-tree gate (scripts/release_check.py): names that must never ship."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("release_check", Path(__file__).resolve().parents[1] / "scripts" / "release_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


class TreeNames(unittest.TestCase):
    def test_secrets_local_config_and_logbook_data_are_refused(self):
        bad = [".env", "beacon/.env.production", "beacon/.dev.vars", "beacon/wrangler.local.jsonc", ".private-patterns",
               "keys/deploy.pem", "projects/customer.md", "logbook/projects/_archive/old.md", "DASHBOARD.md",
               "data/dashboard.json", ".waypoint.lock", "chart.html", "logbook/chart.html"]
        self.assertEqual(len(rc.check_names(bad)), len(bad))

    def test_the_example_logbook_and_templates_are_allowed(self):
        ok = ["examples/logbook/projects/aurora.md", "examples/logbook/DASHBOARD.md", "examples/logbook/dashboard.json", "examples/logbook/chart.html",
              "waypoint/templates/chart.html",
              "waypoint/templates/logbook/_template.md", "beacon/wrangler.jsonc", "docs/PRIVACY.md", "README.md"]
        self.assertEqual(rc.check_names(ok), [])


if __name__ == "__main__":
    unittest.main()
