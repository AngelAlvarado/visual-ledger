---
name: ledger
description: Keep a claim ledger for a working conversation and review it with inline comments. Use whenever a conversation is producing design decisions, assumptions or findings that Angel will need to check -- architecture and schema design, investigations, PRD work, reconciliation results. Also use when asked to "start a ledger", "log that claim", "read my comments", or when a reply is turning into several paragraphs of reasoning.
---

# Claim ledger

Angel reads code and data all day and will not read walls of prose. Long
explanatory replies are where wrong assumptions hide: by the time one surfaces
it is already in a model. The ledger inverts that -- every statement that could
be wrong becomes one scannable line he can comment on before it becomes code.

## The rule

**Prose in chat stays short. Claims go in the ledger.**

If a reply is heading past ~6 lines of reasoning, stop and write claims instead.
The chat reply then becomes: what changed, and a pointer to the ledger.

## Writing a claim

    ### C14 assumption
    Makeups get a row via `attendance_status_id = 3`.
    context: Came up while porting the attendance fact; the payload was the only evidence.
    tags: attendance grain
    turn: T7
    > why: the payload carries the status, so absences are already rows

Five rules, in the order they are usually broken:

1. **`assumption` means you did not verify it. `finding` means you measured it.**
   Flag anything unverified as `assumption`. This distinction is the whole
   value of the ledger -- it is the list of what to attack.
2. **Write it at the moment you decide**, not reconstructed at the end of the
   turn. Reconstruction is lossy and silently drops the assumptions you did not
   notice making.
3. **Always write `context:`** -- what prompted the claim: the question asked,
   the thing being built, the measurement that surprised you. A claim read cold
   cannot be judged without it.
4. **`C<n>` ids are permanent.** Comments anchor to them. Never renumber, never
   reuse. Fixing a wrong claim means editing it in place under the same id.
5. **One sentence.** The `> why:` line holds the reasoning and folds away.

Kinds: `decision` `assumption` `finding` `open` `risk`.
`tags:` are 1-3 lowercase words naming the area; they render as chips that
filter the ledger, which is what keeps 60 claims navigable.

## The artifact a claim governs

A claim may carry `artifact: <path>[#anchor]` -- the file this claim governs.
When such a claim is resolved, updating that artifact in the same pass is part
of resolving it: reply with what changed, or say explicitly why nothing needed
to change. The renderer flags resolved claims whose artifact has not changed
since the reply -- treat that flag as unfinished work, not a suggestion.
Claims without `artifact:` owe nothing.

    ### C8 decision
    V0 screens ship English-only.
    context: Scoping the first release.
    tags: scope i18n
    artifact: docs/spec.md#NFR6b

Add it to claims that govern something durable -- a spec section, a task file,
a module. Leave it off findings, risks and process questions, which usually
govern nothing.

The check is **file-level**: it sees that the file changed, not that your
section did. A tick means something in that file moved after you replied, so
it can be satisfied by an unrelated edit. The anchor is a jump link, not part
of the check.

## Asking questions

Set `focus:` in the frontmatter to say what Angel should act on **now**, with a
one-line `focus_note:` saying why:

    focus: C17, C23
    focus_note: Both block launch and neither is an engineering call.

**Prefer this to asking in chat.** A question logged as an `open` claim is
durable, answerable in place, and adds no prose to the transcript. Reserve chat
questions for what blocks all further work. Keep `focus` to two or three claims
-- highlighting everything highlights nothing. The panel refreshes on write, so
setting `focus:` mid-turn moves his attention with nothing to re-run.

With no `focus:`, every `open` claim without a comment is implicitly the ask.

## Turn-end gate

Before sending any reply, scan it for sentences that wait on Angel — a
question, an option menu, "say the word / your call / on your word / want me
to…", or an either/or ending. Each one must already exist as an `open` claim
(with `focus:` set if it is actionable now); the chat sentence may then only
*point at the panel*, never restate the choice. A turn that ends by offering
Angel decisions in prose has skipped the ledger no matter how short it is —
the ~6-line rule is about reasoning; this gate is about *pending decisions*,
and it has no length threshold.

The renderer backs this up mechanically: after every render it prints a
`⏳ waiting on Angel:` line naming the open, uncommented claims (focus
first). That line lands in your context right before you compose the reply —
it is the list chat must defer to. If something you are about to ask is not
on it, log the claim and re-render before replying.

## Acting on comments

Comments arrive automatically in a `<ledger-comments>` block on the next
prompt. When they do, or at the start of a session on a ledger that has them:

1. Read `comments.md`.
2. For each `wrong`: fix the claim in `ledger.md` **in place** (same id), then
   append a reply `### C14 claude <iso> resolved`.
3. For each `note` / `answered`: fold the correction into the work, then reply.
4. Re-render. Report what changed in a few lines -- not a thread-by-thread summary.

| Status | Means | What you do |
|---|---|---|
| `wrong` | The claim is false | Fix in place, reply `resolved`, **check what depended on it** |
| `note` | Context or a correction | Fold into the work, reply |
| `ok` | Confirmed | Nothing |
| `parked` | True, but not now | Nothing. May come back |
| `dismissed` | Accepted, not a concern here | **Do not act, do not re-raise.** Never delete the claim -- the comment is the record of why |

A `wrong` on an assumption usually means downstream work is wrong too. Check
what else depended on it before replying.

A `dismissed` risk is closed. Raising it again in a later ledger without new
evidence is the thing that status exists to prevent.

## Ownership -- the one rule you must not break

| File | Owner |
|---|---|
| `ledger.md` | **You.** Append claims; edit a claim in place to fix it |
| `comments.md` | **Angel.** Append replies only -- **never rewrite it** |
| `index.html` | generated -- never hand-edit, always regenerate |

Separate owners is what makes this safe: a re-render cannot eat a comment, and
a comment cannot eat a claim.

The transcript is **referenced, never copied**. `session: <uuid>` in the
frontmatter makes the panel read turns straight from the live `.jsonl`. Never
transcribe replies into the repo, and never add a sync step. Link a claim to
the reply it came from with `turn: T7`.

## Starting one

**Usually you don't.** A `SessionStart` hook gives every conversation a ledger
named after itself, bound to its transcript. Your job is to fill it. The first
real claim replaces the two placeholders the scaffold writes.

    python3 .claude/skills/ledger/scripts/ledger.py --where     paths + every ledger found
    python3 .claude/skills/ledger/scripts/ledger.py --config    resolved layout, as JSON
    python3 .claude/skills/ledger/scripts/ledger.py <group>/<slug>   re-render
    python3 .claude/skills/ledger/scripts/ledger.py --ensure <sid>   this conversation
    python3 .claude/skills/ledger/scripts/ledger.py --new <group>/<slug>   manual, no session

Where ledgers live is resolved, not assumed -- `--config` prints it. Do not
hardcode a path; ask.

When starting a ledger, check the siblings first: a claim made in one session
is often exactly what another is about to contradict.

## Reviewing (Angel's side -- do not re-explain this to him)

The rich panel is `Cmd+Shift+P` -> `Ledger: review`. Never suggest `--serve` as
the main path: Simple Browser hands off to the host browser in this
devcontainer, which is the context switch the ledger exists to avoid. Never
suggest the File System Access API (Firefox lacks it) or a download loop
(`~/Downloads` is unreachable here).

Setup, install and troubleshooting live in `scripts/README.md`. Send him there
rather than reciting it.
