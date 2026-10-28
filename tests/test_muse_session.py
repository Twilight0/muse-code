#!/usr/bin/env python3
"""Regression tests for muse-session row wrapping (run: python3 -m unittest discover -s tests)."""

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_helper():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("muse_session", os.path.join(REPO_ROOT, "muse-session"))
    spec = importlib.util.spec_from_loader("muse_session", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


helper = load_helper()


def make_session(prompt="fix the failing tests", workspace="/home/user/projects/app",
                 title=None, size=12345):
    return {
        "uuid": "01a07b12-aaf3-78a0-a819-1cf8ec571c09",
        "created_at": datetime(2026, 10, 18, 12, 0),
        "size_bytes": size,
        "workspace": workspace,
        "prompt": prompt,
        "custom_title": title,
        "path": "/tmp/does-not-matter",
    }


class WrappingTest(unittest.TestCase):
    def test_no_line_exceeds_width(self):
        """Long workspace + long prompt stay inside narrow terminals."""
        s = make_session(prompt="Audit the codebase for memory leaks and race conditions",
                         workspace="/home/user/some/very/deep/project/directory")
        for width in (40, 60, 80, 120):
            for line in helper.session_visual_lines(s, width):
                self.assertLessEqual(len(line), width - 1)

    def test_long_prompt_wraps_to_continuation(self):
        """Overflow text continues below instead of being cut off."""
        s = make_session(prompt="word " * 40)
        lines = helper.session_visual_lines(s, 50)
        self.assertGreater(len(lines), 1)
        self.assertTrue(lines[1].startswith("     "))
        self.assertIn("word", lines[1])

    def test_very_long_prompt_is_capped(self):
        """Pathological prompts cap at header + two wrapped lines."""
        s = make_session(prompt="word " * 400)
        lines = helper.session_visual_lines(s, 50)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].rstrip().endswith("..."))

    def test_custom_title_head_without_emoji(self):
        """Tagged rows use a text marker; continuation shows the prompt."""
        s = make_session(prompt="the original prompt", title="My Title")
        lines = helper.session_visual_lines(s, 80)
        self.assertIn("[tag] My Title", lines[0])
        self.assertNotIn("\U0001f3f7", "".join(lines))
        self.assertTrue(any("the original prompt" in line for line in lines[1:]))

    def test_empty_prompt_is_single_line(self):
        s = make_session(prompt="   ")
        self.assertEqual(len(helper.session_visual_lines(s, 80)), 1)

class SearchTest(unittest.TestCase):
    """Full-text search over fixture session logs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp.name
        self.sdir = os.path.join(
            self.tmp.name, "muse", "sessions", "2026", "10", "28",
            "01a07b12-aaf3-78a0-a819-1cf8ec571c09")
        os.makedirs(self.sdir)
        self._write_log([
            {"payload_type": "runtime.user_intent.accepted",
             "payload": {"model_messages": [{"content": [
                 {"kind": "text", "text": "Fix the failing webhook handler"}]}]}},
            {"payload_type": "runtime.session",
             "payload": {"event": {"kind": "tool_calls", "command": "pytest tests/",
                                   "output": "3 failed, 10 passed"}}},
        ])

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig
        self.tmp.cleanup()

    def _write_log(self, records):
        with open(os.path.join(self.sdir, "session.jsonl"), "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
            f.write("not json at all\n")

    def test_finds_user_prompt(self):
        res = helper.search_sessions("webhook")
        self.assertEqual(len(res), 1)
        self.assertIn("webhook", res[0]["snippet"].lower())

    def test_finds_tool_output(self):
        res = helper.search_sessions("pytest")
        self.assertEqual(len(res), 1)
        self.assertGreaterEqual(res[0]["matches"], 1)

    def test_case_insensitive(self):
        self.assertEqual(len(helper.search_sessions("WEBHOOK")), 1)

    def test_no_match_returns_empty(self):
        self.assertEqual(helper.search_sessions("zzz-no-such-string"), [])

    def test_skips_malformed_lines(self):
        # "not json at all" line must not break the scan.
        self.assertEqual(len(helper.search_sessions("failed")), 1)

    def test_results_sorted_by_match_count(self):
        other = os.path.join(os.path.dirname(self.sdir), "02b07b12-aaf3-78a0-a819-1cf8ec571c10")
        os.makedirs(other)
        with open(os.path.join(other, "session.jsonl"), "w") as f:
            f.write(json.dumps({"payload_type": "x",
                                "payload": {"text": "webhook webhook webhook"}}) + "\n")
        res = helper.search_sessions("webhook")
        self.assertEqual(len(res), 2)
        self.assertGreaterEqual(res[0]["matches"], res[1]["matches"])


class BookmarkTest(unittest.TestCase):
    """Bookmark dotfile round-trip and resolver behavior."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sdir = os.path.join(self.tmp.name, "sess")
        os.makedirs(self.sdir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        self.assertFalse(helper.is_bookmarked(self.sdir))
        helper.set_bookmark(self.sdir, True)
        self.assertTrue(helper.is_bookmarked(self.sdir))
        helper.set_bookmark(self.sdir, False)
        self.assertFalse(helper.is_bookmarked(self.sdir))

    def test_resolve_by_prefix(self):
        sessions = [{"uuid": "01a07b12-aaf3-78a0-a819-1cf8ec571c09"}]
        self.assertIsNotNone(helper.resolve_session(sessions, "01a07b12"))

    def test_resolve_missing_returns_none(self):
        self.assertIsNone(helper.resolve_session([], "deadbeef"))

    def test_resolve_ambiguous_returns_none(self):
        sessions = [{"uuid": "01a07b12-aaf3-78a0-a819-1cf8ec571c09"},
                    {"uuid": "01a07b12-bbf3-78a0-a819-1cf8ec571c10"}]
        self.assertIsNone(helper.resolve_session(sessions, "01a07b12"))

    def test_parse_iso_date_valid(self):
        self.assertEqual(str(helper.parse_iso_date("2026-10-28", "after")), "2026-10-28")

    def test_parse_iso_date_invalid_exits(self):
        with self.assertRaises(SystemExit) as cm:
            helper.parse_iso_date("28/10/2026", "after")
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
