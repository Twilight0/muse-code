#!/usr/bin/env python3
"""Regression tests for muse-import (run: python3 -m unittest discover -s tests)."""

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_helper():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("muse_import", os.path.join(REPO_ROOT, "muse-import"))
    spec = importlib.util.spec_from_loader("muse_import", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


mi = load_helper()


def omp_fixture(path):
    header = {"type": "session", "version": 3, "id": "omp-test-1",
              "timestamp": "2026-10-20T10:00:00.000Z",
              "cwd": "/tmp/fixture-ws", "title": "Fixture work"}
    user = {"type": "message", "id": "m1", "timestamp": "2026-10-20T10:01:00.000Z",
            "message": {"role": "user",
                        "content": [{"type": "text", "text": "Fix the flaky test"}]}}
    asst = {"type": "message", "id": "m2", "timestamp": "2026-10-20T10:01:05.000Z",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "On it."},
                                    {"type": "toolCall", "id": "call_1",
                                     "name": "grep", "arguments": {"pattern": "flaky"},
                                     "thoughtSignature": "opaque"}]}}
    result = {"type": "message", "id": "m3", "timestamp": "2026-10-20T10:01:06.000Z",
              "message": {"role": "assistant",
                          "content": [{"type": "text", "text": "Found it in test_x.py"}]}}
    sys_init = {"type": "session_init", "id": "s0",
                "timestamp": "2026-10-20T09:59:00.000Z",
                "systemPrompt": "SECRET SYSTEM PROMPT - must never be imported"}
    pin = {"type": "credential_pin", "id": "c0",
           "timestamp": "2026-10-20T10:00:30.000Z",
           "provider": "x", "hash": "deadbeef"}
    with open(path, "w", encoding="utf-8") as f:
        for rec in (header, sys_init, pin, user, asst, result):
            f.write(json.dumps(rec) + "\n")
        f.write("not json\n")
    return {"id": "omp-test-1", "title": "Fixture work",
            "cwd": "/tmp/fixture-ws",
            "timestamp": "2026-10-20T10:00:00.000Z"}


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "s.jsonl")
        self.meta = omp_fixture(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts(self):
        _, stats = mi.convert_omp(self.path, self.meta, None, None, None)
        self.assertEqual(stats["user"], 1)
        self.assertEqual(stats["assistant"], 2)
        self.assertEqual(stats["tool"], 1)
        # session_init + credential_pin + garbage line skipped
        self.assertGreaterEqual(stats["skipped"], 3)

    def test_user_prompt_becomes_started(self):
        conv, _ = mi.convert_omp(self.path, self.meta, None, None, None)
        started = [p for (_, pt, p, _) in conv["records"]
                   if isinstance(p, dict) and p.get("event", {}).get("kind") == "started"]
        self.assertEqual(len(started), 1)
        self.assertIn("flaky test", started[0]["event"]["prompt"])

    def test_tool_history_stays_plain_text(self):
        """No synthesized tool-call payloads: provenance rule."""
        conv, _ = mi.convert_omp(self.path, self.meta, None, None, None)
        kinds = {(pt, p.get("event", {}).get("kind"))
                 for (_, pt, p, _) in conv["records"]}
        self.assertNotIn(("runtime.session", "assistant_tool_calls_committed"), kinds)
        self.assertNotIn(("runtime.session", "tool_result_batch_committed"), kinds)
        texts = [p["event"]["text"] for (_, pt, p, _) in conv["records"]
                 if p.get("event", {}).get("kind") == "assistant_message_committed"]
        self.assertTrue(any("[omp tool call: grep id=call_1]" in t for t in texts))

    def test_system_prompt_and_credentials_dropped(self):
        conv, _ = mi.convert_omp(self.path, self.meta, None, None, None)
        blob = json.dumps(conv["records"])
        self.assertNotIn("SECRET SYSTEM PROMPT", blob)
        self.assertNotIn("deadbeef", blob)
        self.assertNotIn("thoughtSignature", blob)

    def test_accepted_payload_has_source_session(self):
        conv, _ = mi.convert_omp(self.path, self.meta, None, None, None)
        acc = [p for (_, pt, p, _) in conv["records"]
               if pt == "runtime.user_intent.accepted"]
        self.assertEqual(len(acc), 1)
        self.assertEqual(acc[0]["source_session_id"], conv["sid"])
        self.assertEqual(acc[0]["refill_blocks"][0]["text"][:17], "Fix the flaky tes")

    def test_metadata_workspace_and_sequence(self):
        conv, _ = mi.convert_omp(self.path, self.meta, "/tmp/fixture-ws", None, None)
        self.assertEqual(conv["workspace"], "/tmp/fixture-ws")
        metas = [p for (_, pt, p, _) in conv["records"]
                 if pt == "runtime.session.metadata"]
        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0]["record"]["workspace_root"], "/tmp/fixture-ws")


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._mods = {}
        for key in ("MUSE_SESSIONS", "MUSE_DATA", "MUSE_INDEX"):
            self._mods[key] = getattr(mi, key)
        mi.MUSE_DATA = os.path.join(self.tmp.name, "muse")
        mi.MUSE_SESSIONS = os.path.join(mi.MUSE_DATA, "sessions")
        mi.MUSE_INDEX = os.path.join(mi.MUSE_DATA, "session-index.db")
        os.makedirs(mi.MUSE_DATA, exist_ok=True)
        # Minimal schema covering the columns write_session inserts.
        con = sqlite3.connect(mi.MUSE_INDEX)
        con.execute("""CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, session_stream_id TEXT, session_dir TEXT,
            session_log_path TEXT, layout TEXT, workspace_root TEXT, workspace_key TEXT,
            provider_id TEXT, model_id TEXT, git_branch TEXT, title TEXT,
            first_user_prompt TEXT, search_text TEXT, created_at_us INTEGER,
            updated_at_us INTEGER, prompt_count INTEGER, status TEXT, status_rank INTEGER,
            source_fingerprint TEXT, indexed_at_us INTEGER, latest_segment_terminated INTEGER,
            session_name TEXT, session_name_revision INTEGER,
            msp_created_at_us INTEGER, msp_updated_at_us INTEGER, msp_turn_count INTEGER,
            msp_fork_source_session_id TEXT, msp_fork_cut_cursor TEXT,
            msp_fork_cut_explicit INTEGER, msp_fork_command_id TEXT,
            msp_provider_id TEXT, msp_model_id TEXT, msp_source_fingerprint TEXT,
            msp_title TEXT, msp_first_user_prompt TEXT, msp_branch TEXT,
            effective_long_title TEXT, title_provenance TEXT, title_name_fingerprint TEXT,
            title_source_fingerprint TEXT, session_name_description_dependency TEXT,
            msp_last_activity_at_us INTEGER, msp_order_key_us INTEGER, msp_kind TEXT,
            msp_parent_session_id TEXT, msp_created_by_client_name TEXT,
            msp_created_by_client_version TEXT, logged_session_name TEXT,
            logged_session_name_revision INTEGER, logged_session_name_seeded INTEGER)""")
        con.commit()
        con.close()
        self.src = os.path.join(self.tmp.name, "s.jsonl")
        self.meta = omp_fixture(self.src)

    def tearDown(self):
        for key, val in self._mods.items():
            setattr(mi, key, val)
        self.tmp.cleanup()

    def test_write_creates_log_title_and_index_row(self):
        conv, _ = mi.convert_omp(self.src, self.meta, None, None, None)
        sdir, sid = mi.write_session(conv)
        log = os.path.join(sdir, "session.jsonl")
        self.assertTrue(os.path.isfile(log))
        with open(os.path.join(sdir, ".session.title")) as f:
            self.assertEqual(f.read().strip(), "Fixture work")
        lines = [json.loads(l) for l in open(log) if l.strip()]
        self.assertTrue(all(r["stream"]["id"] == sid for r in lines))
        self.assertEqual([r["sequence"] for r in lines],
                         list(range(1, len(lines) + 1)))
        con = sqlite3.connect(mi.MUSE_INDEX)
        row = con.execute("SELECT title, prompt_count, status FROM sessions "
                          "WHERE session_id=?", (sid,)).fetchone()
        con.close()
        self.assertEqual(row, ("Fixture work", 1, "valid"))

    def test_resolve_missing_exits(self):
        with self.assertRaises(SystemExit):
            mi.resolve_omp("definitely-not-here-12345")

    def test_iso_to_us(self):
        self.assertEqual(mi.iso_to_us("2026-10-20T10:00:00.000Z"),
                         1792490400000000)


if __name__ == "__main__":
    unittest.main()
