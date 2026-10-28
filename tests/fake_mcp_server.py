#!/usr/bin/env python3
"""Minimal fake MCP server for tests: answers one initialize request, then exits."""
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    resp = {"jsonrpc": "2.0", "id": req.get("id"),
            "result": {"protocolVersion": "2024-11-05",
                       "capabilities": {},
                       "serverInfo": {"name": "fake", "version": "0"}}}
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()
    break
