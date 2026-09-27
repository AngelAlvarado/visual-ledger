#!/usr/bin/env python3
"""Copy Claude transcripts out of the container volume, next to their PRD.

    python3 scripts/ledger/sync_conversations.py --list
    python3 scripts/ledger/sync_conversations.py <prd>[/<slug>]
    python3 scripts/ledger/sync_conversations.py --all

Transcripts live in /home/node/.claude/projects/-app/, which is the Docker
named volume `claude-code-config-shared` -- NOT on the host. `docker volume rm`
loses them and nothing on the Mac ever sees them. `/app` is the host bind, so
copying into the repo is what actually moves a conversation into the house.

This COPIES rather than moving or symlinking the live directory on purpose:
Claude Code appends to the .jsonl of a running session, and a bind mount over
projects/-app has already broken this container once (Docker created the
parents as root, sessions-index.json became unwritable, and the history picker
went blank). A copy cannot break a live session.

The PRD link is explicit, not guessed. Put the session id in ledger.md:

    ---
    title: ...
    session: a6a78867-60e4-4558-8625-614511dadab8
    ---
"""
import json
import pathlib
import shutil
import sys

PROJ = pathlib.Path("/home/node/.claude/projects/-app")
ROOT = pathlib.Path("/app")
PRDS = ROOT / ".claude-supporting-docs" / "prds"


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def index():
    p = PROJ / "sessions-index.json"
    if not p.exists():
        return []
    return json.loads(p.read_text()).get("entries", [])


def ledgers():
    for lg in sorted(PRDS.glob("*/conversations/*/ledger.md")):
        meta = {}
        t = lg.read_text()
        if t.startswith("---\n"):
            for line in t[4:].partition("\n---\n")[0].splitlines():
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        yield lg.parent, meta


def do_list():
    linked = {m.get("session"): d for d, m in ledgers() if m.get("session")}
    rows = sorted(index(), key=lambda e: e.get("fileMtime", 0), reverse=True)
    if not rows:
        sys.exit(f"no sessions-index.json under {PROJ}")
    print(f"{len(rows)} sessions in the volume\n")
    for e in rows[:40]:
        sid = e.get("sessionId", "?")
        f = pathlib.Path(e.get("fullPath", ""))
        size = human(f.stat().st_size) if f.exists() else "-"
        mark = f"-> {linked[sid].parent.parent.name}/{linked[sid].name}" if sid in linked else ""
        first = (e.get("firstPrompt") or "").replace("\n", " ")[:58]
        print(f"  {sid[:8]}  {size:>7}  {first:<60} {mark}")
    print("\nAdd `session: <full-id>` to a ledger.md, then re-run without --list.")


def sync(conv, sid):
    src = PROJ / f"{sid}.jsonl"
    if not src.exists():
        print(f"  {conv.name}: no transcript for {sid[:8]} in the volume")
        return 0
    dst = conv / "transcript.jsonl"
    shutil.copy2(src, dst)
    n = dst.stat().st_size
    extra = PROJ / sid
    if extra.is_dir():
        shutil.copytree(extra, conv / "session-artifacts", dirs_exist_ok=True)
        n += sum(f.stat().st_size for f in (conv / "session-artifacts").rglob("*") if f.is_file())
    print(f"  {conv.parent.parent.name}/{conv.name}: {human(n)} <- {sid[:8]}")
    return n


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    flags = [a for a in argv[1:] if a.startswith("-")]
    if "-h" in flags or "--help" in flags:
        sys.exit(__doc__)
    if "--list" in flags:
        return do_list()

    targets = [(d, m) for d, m in ledgers()
               if "--all" in flags or (args and str(d).endswith(tuple(args)))
               or (args and args[0].split("/")[0] == d.parent.parent.name)]
    if not targets:
        sys.exit("nothing matched. Try --list, or --all.")
    total = sum(sync(d, m["session"]) for d, m in targets if m.get("session")) or 0
    missing = [d.name for d, m in targets if not m.get("session")]
    print(f"copied {human(total)} into the repo (host-backed, gitignored)")
    for m in missing:
        print(f"  no `session:` in {m}/ledger.md -- run --list to find its id")


if __name__ == "__main__":
    main(sys.argv)
