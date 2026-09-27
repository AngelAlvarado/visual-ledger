# Visual Ledger

A numbered list of every statement Claude makes that could be **wrong**, with a
review panel inside VS Code for arguing with them.

Long chat replies hide the load-bearing sentences. A bad assumption buried in
paragraph three survives until it is already code. The ledger pulls those
sentences out, numbers them, and lets you mark one wrong in four words.

```
C8  assumption  Chrome allows showDirectoryPicker on file:// pages
C17 open        Which attendance rule decides who gets flagged?
C23 risk        The gradeslam-api copy has no owner
```

`assumption` means Claude did not verify it. Those are the ones to attack.

---

## Install

Two pieces, because they install through different systems: the plugin (the
skill) and the VS Code panel. **Hooks are off by default** — see
[Enable the hooks](#enable-the-hooks-optional).

### 1. The plugin

This repository is both the plugin and a marketplace listing it, so it
installs from a local path with no hosting:

```bash
claude plugin validate  /path/to/visual-ledger     # expect: Validation passed
claude plugin marketplace add /path/to/visual-ledger
claude plugin install visual-ledger@visual-ledger-marketplace
claude plugin list                                  # expect: enabled
```

Pick a scope when prompted: **user** (every project on this machine),
**project** (committed, for everyone in the repo), or **local** (this repo,
just you).

Just trying it, or actively editing it? Skip installation entirely — this
loads it for one session and picks up edits on restart:

```bash
claude --plugin-dir /path/to/visual-ledger
```

To back out completely:

```bash
claude plugin marketplace remove visual-ledger-marketplace   # also uninstalls
```

### 2. The VS Code panel (manual)

```bash
bash /path/to/visual-ledger/scripts/install-extension.sh
# then in VS Code: Developer: Reload Window
```

Not packageable as a plugin component — plugins carry skills, agents, hooks,
commands and MCP servers, not `.vsix` files. Extensions live in
`~/.vscode-server`, which is often a Docker volume, so **re-run this after the
volume is recreated**.

> **Upgrading from the pre-plugin `claude-ledger` extension?** Uninstall it
> first — both register the same command IDs and two copies collide:
> `code --uninstall-extension local.claude-ledger`

If the panel cannot find `ledger.py` (likely when the plugin lives in
`~/.claude/plugins/` and the extension is looking in your workspace), set
`claudeLedger.scriptPath` in VS Code settings to the absolute path, or export
`$LEDGER_PY`.

### 3. Check

```bash
python3 /path/to/visual-ledger/scripts/ledger.py --where    # paths + ledgers found
python3 /path/to/visual-ledger/scripts/ledger.py --config   # resolved layout, as JSON
```

`--where` prints the project root it resolved. If that is the plugin's own
directory rather than your project, `$CLAUDE_PROJECT_DIR` was not set — export
`LEDGER_ROOT` instead.

### Enable the hooks (optional)

Hooks are what make the loop automatic: a ledger per conversation, comments
delivered without asking, a nudge when the ledger falls behind. They ship
**disabled** because an earlier version was reported to hang conversations and
the cause was never found.

To turn them on, rename the example file and restart Claude Code:

```bash
mv hooks/hooks.json.example hooks/hooks.json
```

Prefer to run them without the plugin? Copy the `hooks` object from that file
into `.claude/settings.json`, replacing `${CLAUDE_PLUGIN_ROOT}` with an
absolute path. The format is identical.

If a session stalls after enabling them, rename the file back and restart.

---

## Daily use

1. `Cmd+Shift+P` → **Ledger: review** — opens the panel beside the chat.
2. Hover a claim, click **+**, type, pick a button.
3. Say anything in the Claude chat. Your comments arrive automatically.

That is the whole loop. You never run a command in normal use.

### The buttons on a claim

| Button | Means | What Claude does |
|---|---|---|
| **Add note** | context or a correction | folds it in, replies |
| **Mark wrong** | the claim is false | fixes it in place, checks what depended on it |
| **Confirm** | it is right | nothing |
| **Park** | true, but not now | nothing — may come back. Moves to Archive |
| **Dismiss** | not a concern | nothing, and never raises it again. Moves to Archive |

### Reading the panel

- **Purple bar** — what is waiting on you, with jump chips. Claude sets this.
- **Waiting on you / Back with Claude** — whose turn each claim is on.
- **Conversation** chip — the actual replies, read from the session transcript.
- **Archive** (bottom) — collapsed. Dismissed and parked claims move here.
  Commenting on one brings it back; nothing is deleted.
- **Send bar** (bottom) — appears when something is on Claude's side.

Opened any other way (a preview tab, a `file://` page) the panel is **read-only**
and says so.

---

## Where ledgers live

Resolved, never assumed. First hit wins:

| # | Source | Layout |
|---|---|---|
| 1 | `$LEDGER_DIR` (+ `$LEDGER_MID`) | whatever you set |
| 2 | `<project>/.claude/ledger.json` | whatever it says |
| 3 | an existing `.claude-supporting-docs/prds/` | legacy `<prd>/conversations/<slug>` |
| 4 | **default** | `.claude/ledgers/<group>/<slug>/` |

`<group>` defaults to the current git branch when any of `.claude/epics/<b>`,
`.claude/prds/<b>`, `<b>.md` in either, or `<base>/<b>` exists — otherwise
`unfiled/`. A repo that organises work its own way gets sensible filing with no
configuration.

To pin it, `<project>/.claude/ledger.json`:

```json
{ "base": "docs/ledgers", "mid": "conversations", "groups": [".claude/epics"] }
```

`mid` is the segment between group and slug; empty means `<group>/<slug>`.

### Code location vs project location

These are different questions, and conflating them is the classic plugin bug.
The **code** lives wherever Claude Code installed the plugin, normally
`~/.claude/plugins/`, outside any repo. The **ledgers** live in the project you
are working in, found via `$CLAUDE_PROJECT_DIR`. Nothing resolves the project
from `__file__`.

Per conversation:

| File | Owner | Tracked |
|---|---|---|
| `ledger.md` | **Claude** — appends claims | yes |
| `comments.md` | **you** — written by the panel | no |
| `index.html` | generated | no |
| `.turns-cache.json` | extracted transcript turns | no |

Two files, two owners, and neither process rewrites the other's. That is what
makes a re-render unable to eat a comment.

Add to the consuming repo's `.gitignore`:

```gitignore
.claude/ledgers/**/comments.md
.claude/ledgers/**/index.html
.claude/ledgers/**/.turns-cache.json
```

---

## Commands

```bash
python3 scripts/ledger.py --where              # paths + every ledger found
python3 scripts/ledger.py --config             # resolved layout, as JSON
python3 scripts/ledger.py --ensure <session>   # ledger for one session
python3 scripts/ledger.py --new <group>/<slug> # manual, no session
python3 scripts/ledger.py <group>[/<slug>]     # re-render
python3 scripts/ledger.py <group> --serve      # localhost, no extension
python3 scripts/sync_conversations.py --list   # session ids + titles
```

`--where` is the one to reach for when something looks wrong.

---

## Writing a claim

```markdown
### C14 assumption
Makeups get a row via `attendance_status_id = 3`.
context: Came up while porting the attendance fact; the payload was the only evidence.
tags: attendance grain
turn: T7
> why: the payload carries the status, so absences are already rows
```

- `C<n>` ids are **permanent** — comments anchor to them. Never renumber.
- Kinds: `decision` `assumption` `finding` `open` `risk`.
- `context:` what prompted the claim — shown in italics on the card.
- `tags:` clickable chips that filter the ledger.
- `turn: T7` jumps to that reply in the Conversation view.

Frontmatter:

```yaml
---
title: Intake answer write path
project: assessment-builder
session: a6a78867-60e4-4558-8625-614511dadab8
focus: C17, C23
focus_note: Both block launch and neither is an engineering call.
---
```

`focus:` drives the purple bar. `session:` drives the Conversation view.

---

## The hooks

| Hook | Does | Cost when idle |
|---|---|---|
| `SessionStart` | creates a ledger for a new conversation | 0 chars |
| `UserPromptSubmit` | delivers your comments to Claude | 0 chars |
| `Stop` | re-renders this session's ledger, measures how much Claude said | 0 chars |

**They are disabled by default** — see [Enable the hooks](#enable-the-hooks-optional).
They print only when there is something to say. Your comments reach Claude when
you **next send a message** — nothing polls in the background.

If Claude produces roughly two substantial replies without recording a claim,
the next message carries a nudge. It measures Claude's output, not your message
count.

> **Known issue:** an earlier version of these hooks was reported to hang
> conversations. The cause was never identified; the Stop hook has since been
> narrowed to re-render only the current session's ledger instead of every
> ledger on disk. Treat them as unproven and disable the plugin if a session
> stalls.

---

## Files

| Path | What it is |
|---|---|
| `.claude-plugin/plugin.json` | the manifest |
| `.claude-plugin/marketplace.json` | lets this repo install itself |
| `skills/ledger/SKILL.md` | teaches Claude the workflow |
| `hooks/hooks.json.example` | hook registration — rename to enable |
| `hooks/_ledger_paths.py` | code-vs-project path resolution |
| `hooks/ledger-{ensure,comments,stop}.py` | the three hooks |
| `scripts/ledger.py` | renderer and CLI. Python stdlib only |
| `scripts/test_page.js` | runs the page's JS in a shim DOM after every render |
| `scripts/vscode-extension/` | the panel |
| `scripts/install-extension.sh` | builds a `.vsix` and installs it |
| `scripts/patch-claude-extension.py` | optional, **run by hand** |
| `scripts/sync_conversations.py` | list sessions |

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| "No ledger.md in this workspace" | none exists yet — `--ensure <session>` or `--new` |
| Ledgers written inside the plugin repo | `$CLAUDE_PROJECT_DIR` unset and cwd was the plugin. Set `LEDGER_ROOT` |
| Panel finds no script | set `claudeLedger.scriptPath`, or `$LEDGER_PY` |
| `--where` finds ledgers but the panel does not | VS Code's folder is not the git root. Set `LEDGER_ROOT` |
| Panel blank after reload | extension older than 0.10.0 — reinstall |
| Conversation view empty | no `session:` in frontmatter, or the transcript is in another container |
| Ledger filed under `unfiled/` | the branch matched no group directory |
| Hooks do nothing | run `--config`; set `$LEDGER_PY` if the path is wrong |
| Two sets of ledger commands | the old `claude-ledger` extension is still installed |
