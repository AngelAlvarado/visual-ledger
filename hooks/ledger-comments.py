#!/usr/bin/env python3
"""UserPromptSubmit hook: surface new ledger comments to Claude automatically.

Claude does not watch the filesystem. Without this, a comment written in the
review panel is invisible until someone remembers to say "read my comments" --
which defeats the point of writing it in the panel.

Printed stdout is added to the conversation context, so every prompt carries
whatever Angel has commented since the last time this fired. Seen comments are
recorded in .ledger-seen.json so they are announced exactly once.

Registered in .claude/settings.json as a UserPromptSubmit hook.
"""
import json
import os
import pathlib
import re
import sys

# .claude/hooks/<this file> -> the repo root is two levels up. Keeps the hook
# portable to another container without editing a path.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _ledger_paths as P          # noqa: E402

ROOT = P.ROOT
SEEN = ROOT / ".claude" / "hooks" / ".ledger-seen.json"
CMT = re.compile(r"^###\s+(C\d+)\s+(\S+)\s+(\S+)\s+(\w+)\s*$")
ACTIONABLE = {"wrong", "note", "answered"}
# A comment is a place to paste a big blob -- query output, a schema dump. That
# is the point of the panel. But inlining it here would put the whole thing back
# into the chat context, which is exactly what the panel exists to avoid.
MAX_BODY = 400


def _group(conv):
    """Group name for a conversation dir, under whatever layout is configured."""
    mod = P.module()
    return mod.group_of(conv) if mod else conv.parent.name


def parse(path):
    out, cur = [], None
    for line in path.read_text().splitlines():
        m = CMT.match(line)
        if m:
            cur = {"claim": m.group(1), "author": m.group(2), "at": m.group(3),
                   "status": m.group(4).lower(), "body": []}
            out.append(cur)
        elif cur is not None:
            cur["body"].append(line)
    return out


def claim_text(conv, cid):
    lg = conv / "ledger.md"
    if not lg.exists():
        return ""
    keep, txt = False, []
    for line in lg.read_text().splitlines():
        if line.startswith("### "):
            if keep:
                break
            keep = line.startswith(f"### {cid} ")
        elif keep and line.strip() and not line.startswith("> "):
            txt.append(line.strip())
    return " ".join(txt)[:160]


def ensure_current():
    """Guarantee the conversation sending this prompt has a ledger.

    SessionStart only fires for NEW conversations, so one already in progress
    when the workflow was installed would never get a ledger. This hook runs on
    every message, so it catches those on their next turn. Idempotent.
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    sid = payload.get("session_id") or payload.get("sessionId")
    if not sid:
        return
    try:
        import contextlib
        import io
        ledger = P.module()
        if not ledger:
            return
        # ledger.py prints for a human at a terminal; here stdout IS the model's
        # context, so swallow it and emit one tagged line instead.
        with contextlib.redirect_stdout(io.StringIO()):
            conv, created = ledger.ensure(sid)
            if created:
                ledger.render(conv, "angel")
        if created:
            print(f"<ledger-new>This conversation had no ledger; created "
                  f"{conv}. Record claims there as you work.</ledger-new>")
        if conv:
            staleness(sid, conv)
    except Exception:
        pass          # a missing ledger must never block a prompt


# Must match ledger-stop.py: per-project, not beside this file.
STATE = ROOT / ".claude" / "hooks" / ".ledger-state.json"
PLACEHOLDER = "Replace this with the first real claim"
# Characters of Claude's own output since the ledger last changed. Roughly two
# substantial replies. Measuring what Claude said beats counting Angel's
# messages: three short exchanges produce nothing worth recording, while one
# long reply can contain five decisions.
NUDGE_CHARS = 4000


def staleness(sid, conv):
    """Say something when the ledger stops keeping up.

    Creating a ledger is automatic; filling it is not, and "trust Claude to
    remember" has failed twice -- an auto-created scaffold in another container
    and this very conversation. So count messages since `ledger.md` last
    changed, and speak up after a few.

    Counting prompts rather than parsing the transcript keeps this O(1): a 17MB
    .jsonl would otherwise be read on every single message.
    """
    lg = conv / "ledger.md"
    try:
        body = lg.read_text()
        mtime = int(lg.stat().st_mtime)
    except OSError:
        return
    if PLACEHOLDER in body:
        print(f"<ledger-stale>{conv.name} is still the empty scaffold. Write the "
              f"real claims from this conversation into ledger.md before "
              f"continuing -- one line each, and mark anything you did not "
              f"verify as `assumption`.</ledger-stale>")
        return
    # The Stop hook accumulates this: characters Claude produced since the
    # ledger last changed. Reading it here is O(1).
    try:
        entry = (json.loads(STATE.read_text()) or {}).get(sid) or {}
    except Exception:
        return
    if entry.get("mtime") != mtime:
        return          # ledger changed since that count; nothing owed
    said, turns = int(entry.get("said", 0)), int(entry.get("turns", 0))
    if said >= NUDGE_CHARS:
        print(f"<ledger-stale>{said:,} characters of your own output across "
              f"{turns} turns since {conv.name}/ledger.md last changed. Record "
              f"the decisions, findings and open questions from those turns "
              f"now, then re-render.</ledger-stale>")


def main():
    ensure_current()
    try:
        seen = set(json.loads(SEEN.read_text()))
    except Exception:
        seen = set()

    fresh, now_seen = [], set(seen)
    for cf in P.ledgers("comments.md"):
        conv = cf.parent
        label = f"{_group(conv)}/{conv.name}"
        for c in parse(cf):
            key = f"{label}|{c['claim']}|{c['at']}|{c['author']}"
            now_seen.add(key)
            if key in seen or c["author"].lower() == "claude":
                continue
            fresh.append((label, conv, c))

    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(now_seen)))

    # Claims where Angel spoke last: these are Claude's move, whether or not
    # they are new. A reply that never comes is the failure mode here.
    owed = []
    for cf in P.ledgers("comments.md"):
        conv = cf.parent
        last = {}
        for c in parse(cf):
            last[c["claim"]] = c
        for cid, c in last.items():
            if c["author"].lower() != "claude" and c["status"] in ACTIONABLE:
                owed.append(f"{_group(conv)}/{conv.name} {cid} ({c['status']})")

    if not fresh:
        if owed:
            print("<ledger-turn>")
            print("Awaiting a reply from you (Angel commented last, you have not "
                  "answered): " + "; ".join(owed))
            print("Answer in ledger.md + comments.md, not only in chat.")
            print("</ledger-turn>")
        return
    act = [f for f in fresh if f[2]["status"] in ACTIONABLE]
    print(f"<ledger-comments count=\"{len(fresh)}\">")
    print("Angel reviewed the claim ledger. These are new since the last prompt.")
    for label, conv, c in fresh:
        raw = "\n".join(c["body"]).strip()
        body = " ".join(raw.split())
        clipped = len(body) > MAX_BODY
        print(f"\n[{label}] {c['claim']} -> {c['status'].upper()}")
        ct = claim_text(conv, c["claim"])
        if ct:
            print(f"  claim: {ct}")
        if body:
            print(f"  {c['author']}: {body[:MAX_BODY]}{'...' if clipped else ''}")
        if clipped:
            print(f"  [{len(raw):,} chars, {raw.count(chr(10)) + 1} lines truncated -- "
                  f"read {conv / 'comments.md'} only if you need the rest]")
    if act:
        print("\nAct on these now: fix any WRONG claim in ledger.md in place (same id), "
              "check what depended on it, then append a `### <id> claude <iso> resolved` "
              "reply to comments.md and re-render. "
              "DISMISSED means closed -- do not act and do not re-raise it.")
    print("</ledger-comments>")


if __name__ == "__main__":
    main()
