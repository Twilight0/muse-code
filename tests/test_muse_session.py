#!/usr/bin/env python3
"""Regression tests for muse-session row wrapping (run: python3 -m unittest discover -s tests)."""

import importlib.util
import os
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


if __name__ == "__main__":
    unittest.main()
