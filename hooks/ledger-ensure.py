#!/usr/bin/env python3
"""SessionStart hook: guarantee this conversation has a ledger.

A ledger per conversation should not be something Angel remembers to create.
Claude Code passes the hook a JSON payload on stdin containing `session_id`;
this hands that to `ledger.py --ensure`, which is idempotent -- it does nothing
if a ledger already names this session.

The PRD defaults to the current git branch when a matching PRD directory
exists, otherwise a `conversations/` bucket. The title comes from the
transcript's own ai-title record, so the ledger is named after the conversation
rather than after a timestamp.

Nothing is printed on success: a new ledger is not news, and the hook's stdout
would land in the context of every session start.
"""
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _ledger_paths as P          # noqa: E402  -- resolves ROOT and ledger.py

ROOT = P.ROOT


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    sid = payload.get("session_id") or payload.get("sessionId")
    if not sid:
        return
    script = P.script()
    if not script:
        return        # skill not installed here; nothing to ensure
    try:
        subprocess.run(
            ["python3", str(script), "--ensure", sid],
            capture_output=True, text=True, timeout=10, cwd=str(ROOT))
    except Exception:
        pass          # never let a missing ledger block a session from starting


if __name__ == "__main__":
    main()
