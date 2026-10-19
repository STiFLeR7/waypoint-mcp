"""The plugin files at the repo root (spec Part B): one repo serves Claude Code, Codex, Copilot CLI and Gemini CLI."""
import json
import re
import tomllib
import unittest
from pathlib import Path

from waypoint import clis

ROOT = Path(__file__).resolve().parent.parent
START, STOP = "waypoint hook session-start --tool auto", "waypoint hook stop --tool auto"


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def commands(hooks_file):
    """{event: [command, ...]} from a Claude-shaped hooks file."""
    return {ev: [h["command"] for m in groups for h in m["hooks"]] for ev, groups in load(hooks_file)["hooks"].items()}


@unittest.skipUnless((ROOT / ".claude-plugin").is_dir(), "plugin files ship in the repo, not the sdist")
class PluginFiles(unittest.TestCase):
    def test_manifest_versions_match_the_package(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(load(".claude-plugin/plugin.json")["version"], version)
        self.assertEqual(load("gemini-extension.json")["version"], version)

    def test_manifests_point_at_the_right_files(self):
        p = load(".claude-plugin/plugin.json")
        self.assertEqual((p["name"], p["hooks"]), ("waypoint", "./hooks/agents.json"))
        m = load(".claude-plugin/marketplace.json")
        self.assertEqual(m["name"], "waypoint-mcp")
        self.assertEqual([(x["name"], x["source"]) for x in m["plugins"]], [("waypoint", "./")])
        self.assertEqual(load("gemini-extension.json")["name"], "waypoint")

    def test_mcp_server_is_the_local_waypoint_server(self):
        self.assertEqual(load(".mcp.json"), {"mcpServers": {"waypoint": clis.SERVER}})
        self.assertEqual(load("gemini-extension.json")["mcpServers"], {"waypoint": clis.SERVER})

    def test_hook_files_have_the_right_events_and_one_shared_session_start(self):
        self.assertEqual(commands("hooks/agents.json"), {"SessionStart": [START], "Stop": [STOP]})
        self.assertEqual(commands("hooks/hooks.json"), {"SessionStart": [START], "AfterAgent": [STOP]})

    def test_commands_are_shell_neutral_and_no_timeouts(self):
        for name in ("hooks/agents.json", "hooks/hooks.json"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("timeout", text.lower(), name)
            for cmds in commands(name).values():
                for c in cmds:
                    self.assertFalse(re.search(r"[$\"'&<>|;`]", c), c)


class Registry(unittest.TestCase):
    def test_server_json_matches_the_package(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        s = load("server.json")
        self.assertEqual(s["$schema"], "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json")
        self.assertEqual(s["name"], "io.github.STiFLeR7/waypoint-mcp")
        self.assertLessEqual(len(s["description"]), 100)
        self.assertEqual(s["version"], version)
        [pkg] = s["packages"]
        self.assertEqual((pkg["registryType"], pkg["identifier"], pkg["version"], pkg["transport"]),
                         ("pypi", "waypoint-mcp", version, {"type": "stdio"}))
        self.assertNotIn("remotes", s)  # the beacon is a single-user server: never listed
        self.assertIn("<!-- mcp-name: io.github.STiFLeR7/waypoint-mcp -->", (ROOT / "README.md").read_text(encoding="utf-8"))
