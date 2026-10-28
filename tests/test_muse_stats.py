#!/usr/bin/env python3
"""Regression tests for muse-stats aggregation (run: python3 -m unittest discover -s tests)."""

import importlib.util
import json
import os
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_helper():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("muse_stats", os.path.join(REPO_ROOT, "muse-stats"))
    spec = importlib.util.spec_from_loader("muse_stats", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


stats = load_helper()


def completion(model, inp, out, reasoning=0):
    return {
        "payload_type": "runtime.session",
        "payload": {
            "event": {
                "kind": "model_completed",
                "model": model,
                "usage": {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "reasoning_tokens": reasoning,
                },
            },
            "kind": "run",
        },
    }


class AggregationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.tmp.name
        base = os.path.join(self.tmp.name, "muse", "sessions")
        self.sdir = os.path.join(base, "2026", "10", "28", "sess-aaaa")
        os.makedirs(self.sdir)
        with open(os.path.join(self.sdir, "session.jsonl"), "w") as f:
            f.write(json.dumps(completion("muse-spark-1.3", 1000, 100, 10)) + "\n")
            f.write(json.dumps(completion("muse-spark-1.3", 500, 50)) + "\n")
            f.write(json.dumps({"payload_type": "runtime.session",
                                "payload": {"event": {"kind": "tool_calls"}}}) + "\n")
            f.write("garbage line\n")
        # Subagent run attributed to the parent session.
        sub = os.path.join(self.sdir, "subagent", "child-1")
        os.makedirs(sub)
        with open(os.path.join(sub, "session.jsonl"), "w") as f:
            f.write(json.dumps(completion("muse-spark-1.3", 200, 20)) + "\n")
        other = os.path.join(base, "2026", "10", "27", "sess-bbbb")
        os.makedirs(other)
        with open(os.path.join(other, "session.jsonl"), "w") as f:
            f.write(json.dumps(completion("other-model", 10, 1)) + "\n")

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._orig
        self.tmp.cleanup()

    def test_totals_include_subagents(self):
        data = stats.collect()
        self.assertEqual(data["total"]["input"], 1710)
        self.assertEqual(data["total"]["output"], 171)
        self.assertEqual(data["total"]["reasoning"], 10)
        self.assertEqual(data["total"]["steps"], 4)

    def test_non_completion_events_ignored(self):
        data = stats.collect()
        # 3 model_completed in sess-aaaa (incl. subagent) + 1 in sess-bbbb.
        self.assertEqual(data["total"]["steps"], 4)

    def test_by_day_and_model(self):
        data = stats.collect()
        self.assertEqual(data["by_day"]["2026-10-28"]["input"], 1700)
        self.assertEqual(data["by_day"]["2026-10-27"]["input"], 10)
        self.assertEqual(data["by_model"]["muse-spark-1.3"]["total"], 1870)
        self.assertEqual(data["by_model"]["other-model"]["total"], 11)

    def test_since_filters_old_days(self):
        data = stats.collect(since="2026-10-28")
        self.assertNotIn("2026-10-27", data["by_day"])
        self.assertEqual(data["total"]["input"], 1700)

    def test_top_session_ranked_first(self):
        data = stats.collect()
        top = list(data["by_session"])[0]
        self.assertEqual(top, "sess-aaaa")
        self.assertEqual(data["by_session"][top]["day"], "2026-10-28")

    def test_dir_date_rejects_bad_layout(self):
        self.assertIsNone(stats.dir_date("/tmp/no-date-here"))


if __name__ == "__main__":
    unittest.main()
