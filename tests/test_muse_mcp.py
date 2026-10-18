#!/usr/bin/env python3
"""Regression tests for muse-mcp (run: python3 -m unittest discover -s tests)."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_helper():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("muse_mcp", os.path.join(REPO_ROOT, "muse-mcp"))
    spec = importlib.util.spec_from_loader("muse_mcp", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


mcp = load_helper()


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = os.path.join(self.tmp.name, "settings.json")
        self._orig = mcp.MUSE_SETTINGS
        mcp.MUSE_SETTINGS = self.settings

    def tearDown(self):
        mcp.MUSE_SETTINGS = self._orig
        self.tmp.cleanup()

    def read(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def test_fresh_save_ensures_schema_version(self):
        mcp.save_settings({"mcp_servers": {}})
        self.assertEqual(self.read()["schema_version"], 1)

    def test_existing_schema_version_preserved(self):
        mcp.save_settings({"schema_version": 1, "other": True})
        data = self.read()
        self.assertEqual(data["schema_version"], 1)
        self.assertTrue(data["other"])

    def test_canonical_key_is_documented_snake_case(self):
        self.assertEqual(mcp.CANONICAL_KEY, "mcp_servers")
        self.assertEqual(mcp.LEGACY_KEY, "mcpServers")

    def test_alias_migrates_to_canonical_on_write(self):
        mcp.save_settings({
            "schema_version": 1,
            "mcpServers": {
                "srv": {"transport": "stdio", "command": "true",
                        "args": [], "enabled": True, "mode": "optional"},
            },
        })
        settings = mcp.load_settings()
        moved, _ = mcp.migrate_legacy_key(settings)
        mcp.save_settings(settings)
        self.assertEqual(moved, ["srv"])
        data = self.read()
        self.assertIn("srv", data["mcp_servers"])
        self.assertNotIn("mcpServers", data)

    def test_conflict_keeps_canonical_entry(self):
        settings = {
            "mcp_servers": {"s": {"transport": "stdio", "command": "new"}},
            "mcpServers": {"s": {"transport": "stdio", "command": "old"}},
        }
        moved, conflicts = mcp.migrate_legacy_key(settings)
        self.assertEqual(conflicts, ["s"])
        self.assertEqual(settings["mcp_servers"]["s"]["command"], "new")
        self.assertNotIn("mcpServers", settings)

    def test_add_uses_canonical_key_and_schema(self):
        parser = mcp.build_parser()
        args = parser.parse_args(["add", "fetch", "uvx", "mcp-server-fetch"])
        self.assertEqual(args.func, mcp.cmd_add)
        args.func(args)
        data = self.read()
        self.assertEqual(data["schema_version"], 1)
        entry = data["mcp_servers"]["fetch"]
        self.assertEqual(entry["command"], "uvx")
        self.assertEqual(entry["args"], ["mcp-server-fetch"])

    def test_import_sources_are_positional(self):
        parser = mcp.build_parser()
        args = parser.parse_args(["import", "claude"])
        self.assertEqual(args.items, ["claude"])
        args = parser.parse_args(["import", "--from", "/tmp/x.json"])
        self.assertEqual(args.from_path, "/tmp/x.json")
        self.assertEqual(args.items, [])

    def test_remote_maps_to_streamable_http(self):
        srv = mcp._norm_remote("r", "http://x/mcp", {}, True)
        self.assertEqual(mcp.to_muse_entry(srv)["transport"], "streamable_http")


if __name__ == "__main__":
    sys.exit(unittest.main())
