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
    def test_check_stdio_ok(self):
        srv = {"command": sys.executable,
               "args": [os.path.join(REPO_ROOT, "tests", "fake_mcp_server.py")],
               "env": {}}
        ok, detail, ms = mcp.check_stdio(srv, timeout=10)
        self.assertTrue(ok, detail)
        self.assertGreaterEqual(ms, 0)

    def test_check_stdio_spawn_failure(self):
        srv = {"command": "/nonexistent/mcp-server-binary", "args": [], "env": {}}
        ok, detail, _ = mcp.check_stdio(srv, timeout=5)
        self.assertFalse(ok)
        self.assertIn("spawn failed", detail)

    def test_check_stdio_silent_server_times_out(self):
        srv = {"command": sys.executable,
               "args": ["-c", "import time; time.sleep(30)"], "env": {}}
        ok, detail, _ = mcp.check_stdio(srv, timeout=1)
        self.assertFalse(ok)
        self.assertIn("no reply", detail)

    def test_check_remote_ok(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                body = b'{"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            srv = {"url": f"http://127.0.0.1:{server.server_port}/mcp",
                   "headers": {}}
            ok, detail, _ = mcp.check_remote(srv, timeout=10)
            self.assertTrue(ok, detail)
        finally:
            server.shutdown()

    def test_check_remote_connection_refused(self):
        srv = {"url": "http://127.0.0.1:1/mcp", "headers": {}}
        ok, detail, _ = mcp.check_remote(srv, timeout=5)
        self.assertFalse(ok)
        self.assertTrue(detail)

    def test_cmd_check_exit_code_reflects_health(self):
        mcp.save_settings({"mcp_servers": {
            "good": {"transport": "stdio", "command": sys.executable,
                     "args": [os.path.join(REPO_ROOT, "tests", "fake_mcp_server.py")],
                     "enabled": True, "mode": "optional"},
            "bad": {"transport": "stdio", "command": "/nonexistent/bin",
                    "args": [], "enabled": True, "mode": "optional"},
            "off": {"transport": "stdio", "command": "/nonexistent/bin",
                    "args": [], "enabled": False, "mode": "optional"},
        }})
        parser = mcp.build_parser()
        args = parser.parse_args(["check"])
        self.assertEqual(args.func, mcp.cmd_check)
        with open(os.devnull, "w") as devnull:
            old = sys.stdout
            sys.stdout = devnull
            try:
                rc = args.func(args)
            finally:
                sys.stdout = old
        self.assertEqual(rc, 1)



if __name__ == "__main__":
    sys.exit(unittest.main())
