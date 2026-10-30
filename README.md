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

Two installs, because they go through different systems: the **plugin** (what
teaches Claude the workflow) and the **VS Code panel** (what you review in).
Neither can carry the other -- plugins hold skills and hooks, not editor
extensions.

```bash
# 1. Get the code. The panel and the renderer both live here.
git clone git@github.com:AngelAlvarado/visual-ledger.git ~/visual-ledger

# 2. Tell Claude Code where to find plugins. This repo is both the plugin
#    and a one-entry marketplace listing it, so there is nothing to host.
claude plugin marketplace add ~/visual-ledger

# 3. Install the plugin itself. This is what puts the skill in Claude's
#    context, so it knows the workflow exists. Pick a scope when asked:
#    user (every project), project (committed, whole team), local (just here).
claude plugin install visual-ledger@visual-ledger-marketplace

# 4. Build and install the review panel as a VS Code extension. It also
#    bakes this clone's ledger.py path in, so the panel needs no settings.
bash ~/visual-ledger/scripts/install-extension.sh

# 5. OPTIONAL. Lets the panel's "Send to Claude" button put text straight in
#    the chat box instead of only the clipboard. You run it, not Claude:
#    it patches the Claude Code extension, which Claude is blocked from
#    touching. Re-run after each Claude Code update. --revert undoes it.
python3 ~/visual-ledger/scripts/patch-claude-extension.py
```

Then **`Developer: Reload Window`** so VS Code loads the extension.

Verify:

```bash
# Prints the project root it resolved, where it looks, and what it found.
# The first thing to run whenever anything looks wrong.
python3 ~/visual-ledger/scripts/ledger.py --where
```

### Notes on those steps

**Developing it rather than using it?** Skip the marketplace entirely --
`claude --plugin-dir ~/visual-ledger` loads the plugin straight from the
clone for one session and picks up edits on restart. An installed plugin is
a *cached copy*, so edits to the clone do not reach it until you bump the
version and run `claude plugin update visual-ledger`.

**The panel is a manual `.vsix` on purpose.** Plugins carry skills, agents,
hooks, commands and MCP servers -- not editor extensions. Extensions live in
`~/.vscode-server`, often a Docker volume, so **re-run step 4 if that volume
is recreated**.

**Upgrading from the pre-plugin `claude-ledger` extension?** Uninstall it
first -- both register the same command IDs:
`code --uninstall-extension local.claude-ledger`

**To back out:** `claude plugin marketplace remove visual-ledger-marketplace`
also uninstalls the plugin.

### One conversation, one ledger

`Ledger: new` binds the ledger to the conversation you have open, so Claude
finds it instead of making its own. If it cannot tell which conversation that
is, it says so.

A ledger that ends up unbound anyway is not stranded: when Claude next runs
`--ensure`, it **adopts** an empty hand-made ledger rather than creating a
second one -- same name, same path, `session:` filled in. With more than one
unbound ledger it refuses to guess and lists them.

### Your first ledger

A fresh workspace has none, and with the hooks off nothing creates one, so
`Ledger: review` will say so. Either take its **Create one** offer, run
`Ledger: new ledger for this conversation`, or just ask Claude to start one
-- which is better, because Claude binds it to the conversation and the
**Conversation** tab then works.

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

## How Claude starts using it

Installing the plugin puts the skill's name and description in Claude's
context every turn, so it knows the workflow exists. It invokes itself when
a conversation starts producing decisions, assumptions or findings -- or
when you say so: *"start a ledger"*, *"log that claim"*, *"read my
comments"*.

**It will not create one silently.** The `SessionStart` hook that used to do
that ships disabled, so if nothing happens, say **"start a ledger for this"**
and Claude will make one bound to the conversation. You can also make it
yourself with `Ledger: new ledger for this conversation`, though one made
that way has no session attached and the **Conversation** tab stays empty.

Claude is told to re-render after every claim and every reply. That is also
what prints the `waiting on Angel` line it reads before composing a reply --
the mechanism that stops it offering you decisions in chat prose instead of
as claims you can answer in the panel.

If Claude keeps writing long explanatory replies instead of claims, the
nudge that used to catch that was a hook, and hooks are off. Say *"put that
in the ledger"*.

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

### Dictating a comment

Every composer has a **Dictate** button (or `Alt+D` in the textarea). The dot
tells you where you are: grey idle, amber connecting, red pulsing listening.
Interim words appear in grey under the box and only land in the textarea once
a phrase finishes, so your sentence never rewrites itself under the cursor.

**The default is a fake.** It emits scripted placeholder text and never
touches the microphone, so the interaction is real while the transcription
question stays open. `claudeLedger.dictation.backend: "off"` hides the button.

#### The interface is the stable part

Four backends ship or are supported, and the page cannot tell them apart:

| `backend` | What it is |
|---|---|
| `fake` | scripted placeholder text (default) |
| `anthropic` | the hosted socket. Undocumented; the token expires in hours |
| `custom` | your own module, via `dictation.backendModule` |
| `off` | no button |

All of them satisfy one contract:

```js
start(channel, { onText, onState, onError }, opts) -> stop()

onText(text, done, cumulative)
   done=false      interim, may be revised
   done=true       commit into the textarea
   cumulative=true `text` is the WHOLE transcript so far, so the page
                   replaces rather than appends
onState("connecting" | "listening")
onError(message)
```

A backend touches nothing else -- not the panel, not the page, not
`dictation.js` if you use `custom`. The module is re-required per use, so
editing one needs no reinstall.

**Expect to need `cumulative: true`.** A streaming recogniser normally
restates the whole utterance as it refines it rather than emitting each word
once, and may send no per-phrase commit at all -- in which case commit on
stop. That is measured behaviour from the hosted socket, not a guess.
Appending cumulative text duplicates it quadratically; `test_page.js` has a
case for exactly that.

A **local** recogniser is the obvious next backend: no token, nothing that
expires, no undocumented endpoint to track. It needs no change to this
extension -- write the module, point `backendModule` at it.

### Reading the panel

- **Purple bar** — what is waiting on you, with jump chips. Claude sets this.
- **Waiting on you / Back with Claude** — whose turn each claim is on.
- **Conversation** chip — the actual replies, read from the session transcript.
- **Archive** (bottom) — collapsed. Dismissed and parked claims move here.
  Commenting on one brings it back; nothing is deleted.
- **Send bar** (bottom) — appears when something is on Claude's side.
- **Refresh** (top right) — re-reads the ledger from disk. It turns amber and
  says *Updated — refresh* when Claude changed something while you were
  mid-comment.

### Your typing is never lost to a repaint

Claude edits `ledger.md` while you are looking at the panel, and the panel
answers by replacing its whole HTML — which destroys the DOM, and with it a
half-typed comment, the caret, the scroll position and your filters.

Two defences, both automatic:

- While a composer holds unsaved text, **repaints are held back**. The
  Refresh button goes amber so you know the panel is behind.
- Every keystroke mirrors the draft into the webview's own store, which
  *does* survive an HTML replacement. A repaint that happens anyway restores
  the draft, caret, scroll and filters.

A draft is only restored if its claim still exists — Claude may have removed
it, and restoring onto the wrong card would misfile the comment silently.

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

Every one takes the project from `$LEDGER_ROOT`, then `$CLAUDE_PROJECT_DIR`,
then the git root above the current directory -- so run them from inside the
project you are working on.

```bash
L=~/visual-ledger/scripts/ledger.py

# Where am I, and what exists? Run this first whenever anything is wrong.
# Prints the resolved project root, the layout, and every ledger found.
python3 $L --where

# The same answers as JSON. The panel and the hooks read this, which is how
# all three stay agreed about where ledgers live.
python3 $L --config

# Make a ledger bound to a conversation, named after it, with the transcript
# attached so the Conversation tab works. Idempotent -- safe to re-run.
python3 $L --ensure <session-id>

# Make one by hand, with no conversation attached. The group defaults to the
# current branch; pass <group>/<slug> to place it yourself.
python3 $L --new <slug>

# Re-render. Rewrites index.html from ledger.md + comments.md, prints the
# claim counts, any artifact drift, and what is waiting on you.
python3 $L <group>/<slug>

# Same, but served on localhost instead of written to a file. The fallback
# when the VS Code panel is not available.
python3 $L <group> --serve

# Session ids and titles, for --ensure.
python3 ~/visual-ledger/scripts/sync_conversations.py --list
```

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
- `artifact: <path>[#anchor]` the file this claim governs (see below).

### Keeping claims and artifacts together

Resolving a claim usually *means* editing something durable -- a spec, a task
file, code. Nothing enforced that, so a claim could be marked resolved while
the artifact kept saying the old thing, and the artifact is what becomes code.

A claim carrying `artifact:` gets checked at render time: if it has a `claude
... resolved` reply and the file has not changed since that reply, the CLI
prints a `drift` line and the card shows a red badge. Touch the file and
re-render and it clears.

This rides on rendering rather than a hook or a timer. Drift is not a function
of elapsed time but of one event -- claim resolved, artifact untouched -- and
rendering already happens on every claim edit, every comment and every panel
open, in every environment where the panel works at all.

Details worth knowing:

- Only `resolved` triggers it. `answered` is usually "I answered your
  question" and carries no artifact obligation.
- Uncommitted edits count. `git log` alone reports the last *commit*, which in
  this workflow is routinely weeks behind the working tree.
- The check is **file-level**. The `#anchor` is a jump link; a tick means the
  file moved, not that the right section did.
- One direction only. Claims with `artifact:` owe an update when resolved; no
  one is required to write a claim for an arbitrary edit.
- Fail-soft everywhere: no git, a deleted path or an empty `comments.md`
  degrade to no badge, never an exception.

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
| `scripts/vscode-extension/dictation.js` | dictation backends (fake by default) |
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
