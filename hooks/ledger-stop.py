#!/usr/bin/env python3
"""Stop hook: re-render the ledgers, and measure how much Claude said.

Replaces ledger-render.sh. Two jobs at end of turn:

1. Re-render every ledger, so index.html is current even with no panel open.
2. Record how much assistant text was produced since `ledger.md` last changed.

(2) is the fix for counting Angel's messages instead of Claude's output. Three
short exchanges that produce nothing worth recording used to trigger a nudge;
one long reply full of decisions did not. The Stop hook fires exactly when
Claude finishes writing, so it can measure the thing that actually matters.

Only the tail of the transcript is read -- these files reach 17MB, and the last
assistant turn is always at the end.

Output is discarded: a Stop hook's stdout is not the place to talk. The
UserPromptSubmit hook reads the counter and speaks if it has gone too far.
"""
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _ledger_paths as P          # noqa: E402

ROOT = P.ROOT
# Per-project, NOT next to this file: as a plugin, this file lives in a shared
# install directory that may be read-only, and one state file there would mix
# the counters of every project on the machine.
STATE = ROOT / ".claude" / "hooks" / ".ledger-state.json"
SESSIONS = pathlib.Path.home() / ".claude" / "projects" / str(ROOT).replace("/", "-")
TAIL = 512 * 1024          # enough for the last turn, never the whole file


def render_all(sid=None):
    """Re-render ledgers. Only this session's, unless it has none.

    Re-rendering EVERY ledger on every turn is what made this hook scale with
    the repo's history rather than with the work in front of it: each render
    re-parses a transcript and spawns node for the page check, and a Stop hook
    blocks the turn from ending while it runs.
    """
    script = P.script()
    if not script:
        return
    mine = ledger_for(sid) if sid else None
    targets = [mine] if mine else P.ledgers()
    for lg in targets:
        try:
            subprocess.run(["python3", str(script), str(lg.parent)],
                           capture_output=True, timeout=15, cwd=str(ROOT))
        except Exception:
            pass


def last_assistant_chars(sid):
    """Characters of assistant text in the final turn of the transcript."""
    f = SESSIONS / f"{sid}.jsonl"
    try:
        size = f.stat().st_size
        with f.open("rb") as fh:
            fh.seek(max(0, size - TAIL))
            chunk = fh.read().decode("utf-8", "ignore")
    except Exception:
        return 0
    for line in reversed(chunk.split("\n")):
        if '"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        text = "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            return len(text)
    return 0


def ledger_for(sid):
    for lg in P.ledgers():
        try:
            for line in lg.read_text().splitlines():
                if line.startswith("session:") and line.split(":", 1)[1].strip() == sid:
                    return lg
        except OSError:
            pass
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    sid = payload.get("session_id") or payload.get("sessionId")
    render_all(sid)
    lg = ledger_for(sid) if sid else None
    if not lg:
        return
    try:
        mtime = int(lg.stat().st_mtime)
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
    except Exception:
        return
    prev = state.get(sid) or {}
    # A changed ledger means Claude recorded something: start counting again.
    if prev.get("mtime") != mtime:
        entry = {"mtime": mtime, "said": 0, "turns": 0}
    else:
        entry = {"mtime": mtime,
                 "said": int(prev.get("said", 0)) + last_assistant_chars(sid),
                 "turns": int(prev.get("turns", 0)) + 1}
    state[sid] = entry
    try:
        STATE.write_text(json.dumps(state))
    except Exception:
        pass


if __name__ == "__main__":
    main()
