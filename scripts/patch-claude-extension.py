#!/usr/bin/env python3
"""Let `claude-vscode.focus` accept text, so a tool can put a prompt in the box.

WHY
The Claude Code extension contributes no command that composes or submits a
prompt -- `focus`, `blur`, `insertAtMention`, `newConversation` and the diff
commands are the whole surface. So the ledger panel's "Send to Claude" button
can only reach the clipboard, and you still have to paste.

But `claude-vscode.focus` and `claude-vscode.insertAtMention` both funnel into
one internal helper that inserts text into the chat input and reveals the tab.
`focus` simply always passes the current editor selection. This patch adds an
optional argument, so the ledger panel can hand text straight to the input:

    vscode.commands.executeCommand("claude-vscode.focus", "read my comments")

You still press Enter. Nothing here submits on your behalf.

RUN IT YOURSELF
Claude cannot run this -- patching the Claude Code extension is blocked as
self-modification, which is the right default. Run it by hand:

    python3 scripts/ledger/patch-claude-extension.py
    # then: Developer: Reload Window
    python3 scripts/ledger/patch-claude-extension.py --revert   # to undo

NOTES
- The helper's name is minified and changes between releases, so it is read
  back out of the handler body rather than hard-coded. If the anchor is gone,
  this refuses to patch rather than corrupting the bundle.
- Every version installs into its own directory and the extension auto-updates,
  so re-run after an update. Same maintenance shape as the voice-dictation
  patch.
- Idempotent: an already-patched file is detected and skipped.
- A `.js.ledger-bak` backup is written next to each patched file.
"""
import pathlib
import re
import shutil
import sys

EXT = pathlib.Path.home() / ".vscode-server" / "extensions"
ANCHOR = '"claude-vscode.focus",async()=>{'
MARK = "/*ledger-patch*/"


def patch(f):
    s = f.read_text()
    if MARK in s:
        return "already patched"
    i = s.find(ANCHOR)
    if i < 0:
        return "ANCHOR NOT FOUND -- extension internals changed; NOT patched"
    m = re.search(r"await (\w+)\(\w+\)\}", s[i:i + 400])
    if not m:
        return "helper call not found; NOT patched"
    helper = m.group(1)
    shutil.copy2(f, f.with_suffix(".js.ledger-bak"))
    s = s.replace(
        ANCHOR,
        '"claude-vscode.focus",async(_lt)=>{' + MARK
        + f'if(typeof _lt==="string"&&_lt){{await {helper}(_lt);return}}',
        1)
    f.write_text(s)
    return f"patched (helper `{helper}`), backup written alongside"


def revert(f):
    bak = f.with_suffix(".js.ledger-bak")
    if not bak.exists():
        return "no backup to revert to"
    shutil.copy2(bak, f)
    return "reverted"


def main():
    files = sorted(EXT.glob("anthropic.claude-code-*/extension.js"))
    if not files:
        sys.exit(f"no Claude extension found under {EXT}")
    act = revert if "--revert" in sys.argv else patch
    for f in files:
        print(f"{f.parent.name}: {act(f)}")
    print("\nNow run: Cmd+Shift+P -> Developer: Reload Window")


if __name__ == "__main__":
    main()
