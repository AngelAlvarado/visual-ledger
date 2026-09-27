#!/usr/bin/env python3
"""Render a conversation claim-ledger into an annotatable HTML page.

    python3 scripts/ledger/ledger.py <prd-name> <conversation-slug>

Reads   .claude-supporting-docs/prds/<prd>/conversations/<slug>/ledger.md
        .claude-supporting-docs/prds/<prd>/conversations/<slug>/comments.md  (optional)
Writes  .claude-supporting-docs/prds/<prd>/conversations/<slug>/index.html

TWO FILES, TWO OWNERS -- this is the point of the split:
  ledger.md    Claude appends claims. Claude owns it.
  comments.md  Angel appends comments from the browser. Angel owns it.
Neither side rewrites the other's file, so a re-render can never eat a comment
and a new comment can never eat a claim.

ledger.md grammar (append-only, hand-editable):

    ### C14 assumption
    Makeups get a row via `attendance_status_id = 3`.
    > why: the payload carries the status, so absences are already rows

  id     C<n>, stable forever -- comments anchor to it, never to line numbers
  kind   decision | assumption | finding | open | risk
  body   free markdown until the next '### '
  '> '   detail, folded behind a disclosure in the HTML

comments.md grammar (the browser appends this; also hand-editable):

    ### C14 angel 2026-09-10T10:40:00 wrong
    Makeups are a separate table, not a status on this one.

  status  ok | wrong | note | answered | resolved
"""
import html
import json
import pathlib
import re
import sys

def _workspace():
    """The repo whose ledgers we manage -- NOT where this script lives.

    Hardcoding /app made this repo-specific. Walking up from __file__ then
    broke a second way once this shipped as a plugin: the plugin is its own
    git repo, living outside the user's project, so `__file__`'s git root is
    the plugin itself. Ledgers would be written into the tool.

    Order: $LEDGER_ROOT, then the project dir Claude Code exports, then the
    git root above the cwd, then the cwd. __file__ is deliberately not
    consulted -- where the code sits says nothing about which repo is in use.
    """
    import os
    env = os.environ.get("LEDGER_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return pathlib.Path(env)
    cwd = pathlib.Path.cwd().resolve()
    for d in [cwd, *cwd.parents]:
        if (d / ".git").exists():
            return d
    return cwd


ROOT = _workspace()


def _layout(root):
    """Where ledgers live, and how deep. One source of truth for every caller.

    Hardcoding `.claude-supporting-docs/prds/<prd>/conversations/<slug>` made
    this workflow specific to the repo it was born in. A second repo has no
    such directory, so every ledger landed in the `unfiled/` fallback.

    Resolution order, first hit wins:
      1. $LEDGER_DIR (+ $LEDGER_MID) -- explicit override, no file needed.
      2. <root>/.claude/ledger.json -- {"base": ..., "mid": ..., "groups": [...]}
      3. an existing legacy `.claude-supporting-docs/prds/` -- keeps old repos
         working with no migration.
      4. `.claude/ledgers/<group>/<slug>/` -- the zero-config default. Flat,
         because a repo that has never heard of a PRD should not have to
         invent one to get a ledger.

    `mid` is the segment between group and slug ("conversations" in the legacy
    layout, empty in the default one). `groups` are directories consulted to
    decide whether the current branch names a real group.
    """
    import json as _json
    import os
    env = os.environ.get("LEDGER_DIR")
    if env:
        base = pathlib.Path(env)
        base = base if base.is_absolute() else root / base
        return base, os.environ.get("LEDGER_MID", ""), _DEFAULT_GROUPS

    cfg = root / ".claude" / "ledger.json"
    if cfg.exists():
        try:
            d = _json.loads(cfg.read_text())
            base = pathlib.Path(d.get("base", ".claude/ledgers"))
            base = base if base.is_absolute() else root / base
            return base, d.get("mid", ""), d.get("groups", _DEFAULT_GROUPS)
        except Exception:
            pass          # a broken config must not stop a ledger rendering

    legacy = root / ".claude-supporting-docs" / "prds"
    if legacy.is_dir():
        return legacy, "conversations", _DEFAULT_GROUPS

    return root / ".claude" / "ledgers", "", _DEFAULT_GROUPS


# Directories that mean "this name is a real unit of work". Checked in order
# when defaulting a new ledger's group to the current branch.
_DEFAULT_GROUPS = [".claude/epics", ".claude/prds", ".claude-supporting-docs/prds"]

PRDS, MID, GROUPS = _layout(ROOT)
# Every glob and every path build goes through these two, so the layout is
# stated once. MID empty collapses <group>/<mid>/<slug> to <group>/<slug>.
GLOB = f"*/{MID}/*/ledger.md" if MID else "*/*/ledger.md"


def conv_dir(group, slug, base=None):
    """The directory for one conversation, under the configured layout."""
    b = base or PRDS
    return (b / group / MID / slug) if MID else (b / group / slug)


def group_of(conv):
    """The group name for a conversation dir -- inverse of conv_dir."""
    return conv.parent.parent.name if MID else conv.parent.name


def self_cmd():
    """How to invoke this script, as the reader's shell would see it."""
    me = pathlib.Path(__file__).resolve()
    try:
        return f"python3 {me.relative_to(ROOT)}"
    except ValueError:
        return f"python3 {me}"


def layout_desc():
    seg = f"<group>/{MID}/<slug>" if MID else "<group>/<slug>"
    return f"{PRDS}/{seg}/ledger.md"

# Claude Code names a project directory after its workspace path, with the
# separators turned into dashes: /app -> -app, /workspace -> -workspace.
SESSION_DIR = (pathlib.Path.home() / ".claude" / "projects"
               / str(ROOT).replace("/", "-"))

KINDS = {
    "decision":   ("Decision",   "var(--dec)",  "A choice that closes off alternatives."),
    "assumption": ("Assumption", "var(--asm)",  "Believed, not verified. The dangerous ones."),
    "finding":    ("Finding",    "var(--fnd)",  "Measured against something real."),
    "open":       ("Open",       "var(--opn)",  "Unresolved. Needs an answer to proceed."),
    "risk":       ("Risk",       "var(--rsk)",  "Known way this goes wrong later."),
}
STATUSES = {
    "ok":       ("Confirmed", "var(--ok)"),
    "wrong":    ("Wrong",     "var(--crit)"),
    "note":     ("Note",      "var(--accent)"),
    "answered": ("Answered",  "var(--ok)"),
    "resolved": ("Resolved",  "var(--faint)"),
    "dismissed": ("Dismissed", "var(--faint)"),
    "parked": ("Parked", "var(--warn)"),
}

e = lambda s: html.escape(str(s), quote=True)

# ------------------------------------------------------------------ parsing
CLAIM_RE = re.compile(r"^###\s+(C\d+)\s+(\w+)\s*$")
CMT_RE = re.compile(r"^###\s+(C\d+)\s+(\S+)\s+(\S+)\s+(\w+)\s*$")


def _blocks(text, header_re):
    """Split a markdown file into (match, body-lines) on '### ' headers."""
    out, cur, buf = [], None, []
    for line in text.splitlines():
        m = header_re.match(line)
        if m:
            if cur:
                out.append((cur, buf))
            cur, buf = m, []
        elif cur is not None:
            buf.append(line)
    if cur:
        out.append((cur, buf))
    return out


def parse_ledger(path):
    if not path.exists():
        sys.exit(f"no ledger at {path}")
    meta, text = {}, path.read_text()
    if text.startswith("---\n"):
        fm, _, text = text[4:].partition("\n---\n")
        for line in fm.splitlines():
            k, _, v = line.partition(":")
            if k.strip():
                meta[k.strip()] = v.strip()
    claims = []
    for m, buf in _blocks(text, CLAIM_RE):
        cid, kind = m.group(1), m.group(2).lower()
        if kind not in KINDS:
            sys.exit(f"{path.name}: {cid} has unknown kind {kind!r} "
                     f"(expected one of {', '.join(KINDS)})")
        FIELDS = ("turn:", "context:", "tags:")
        body = [l for l in buf
                if not l.startswith("> ") and not l.startswith(FIELDS)]
        detail = [l[2:] for l in buf if l.startswith("> ")]

        def field(name):
            return next((l.split(":", 1)[1].strip()
                         for l in buf if l.startswith(name + ":")), "")

        claims.append({
            "id": cid, "kind": kind, "turn": field("turn"),
            "context": field("context"),
            "tags": [x for x in re.split(r"[,\s]+", field("tags")) if x],
            "text": "\n".join(body).strip(),
            "detail": "\n".join(detail).strip(),
        })
    ids = [c["id"] for c in claims]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        sys.exit(f"{path.name}: duplicate claim ids {sorted(dup)} -- comments would "
                 f"attach to whichever rendered last. Renumber before re-running.")
    return meta, claims


def parse_comments(path):
    if not path.exists():
        return []
    out = []
    for m, buf in _blocks(path.read_text(), CMT_RE):
        out.append({
            "claim": m.group(1), "author": m.group(2), "at": m.group(3),
            "status": m.group(4).lower() if m.group(4).lower() in STATUSES else "note",
            "text": "\n".join(buf).strip(),
        })
    return out


# ------------------------------------------------------------------ tiny md
def md(s):
    """Inline-only markdown: code, bold, links. Claims are one paragraph."""
    s = e(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s.replace("\n\n", "<br><br>").replace("\n", " ")


# ------------------------------------------------------------------ turns
TURN_RE = re.compile(r"^##\s+(T\d+)\s+(\S+)\s+(\S+)\s*(.*)$")


def parse_turns(path):
    """turns.md -- the actual replies, kept beside the claims they produced.

    The claims are the distillation; without the turn itself the reasoning is
    gone and the ledger cannot be read back as a conversation.

        ## T3 claude 2026-09-10T23:05 Where lesson_order comes from
        <markdown body>
    """
    if not path.exists():
        return []
    out, cur = [], None
    for line in path.read_text().splitlines():
        m = TURN_RE.match(line)
        if m:
            cur = {"id": m.group(1), "author": m.group(2), "at": m.group(3),
                   "title": m.group(4).strip(), "body": []}
            out.append(cur)
        elif cur is not None:
            cur["body"].append(line)
    for c in out:
        c["text"] = "\n".join(c["body"]).strip()
    return out


NOISE = re.compile(r"<(system-reminder|ide_opened_file|ide_selection|local-command-[a-z]+|"
                   r"command-name|command-message|command-args)>.*?</\1>", re.S)


def session_turns(session_id, conv, limit=60):
    """Read turns straight out of the session transcript.

    Nothing is duplicated into the repo: the .jsonl is the record, it is already
    mounted, and it keeps growing while the session runs. Numbering is by
    position from the start, which is stable because the file is append-only --
    a claim's `turn: T7` keeps pointing at the same reply forever.

    Parsing a 17MB transcript on every render would be slow, so the extracted
    turns are cached beside the ledger and reused until the file changes.
    """
    src = SESSION_DIR / f"{session_id}.jsonl"
    if not src.exists():
        return []
    st = src.stat()
    cache = conv / ".turns-cache.json"
    key = f"{session_id}:{st.st_size}:{int(st.st_mtime)}:{limit}"
    try:
        blob = json.loads(cache.read_text())
        if blob.get("key") == key:
            return blob["turns"]
    except Exception:
        pass

    turns = []
    with src.open() as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            kind = d.get("type")
            if kind not in ("assistant", "user"):
                continue
            msg = d.get("message") or {}
            content = msg.get("content")
            text = ""
            if kind == "assistant" and isinstance(content, list):
                text = "\n\n".join(b.get("text", "") for b in content
                                    if isinstance(b, dict) and b.get("type") == "text")
            elif kind == "user" and isinstance(content, str):
                text = content
            if not text.strip():
                continue
            text = NOISE.sub("", text).strip()
            if not text:
                continue
            turns.append({"id": f"T{len(turns) + 1}",
                          "author": "claude" if kind == "assistant" else "angel",
                          "at": (d.get("timestamp") or "")[:19],
                          "title": "", "text": text})
    turns = turns[-limit:] if limit else turns
    try:
        cache.write_text(json.dumps({"key": key, "turns": turns}))
    except Exception:
        pass
    return turns


def read_turns(meta, conv):
    manual = parse_turns(conv / "turns.md")
    if manual:
        return manual
    sid = meta.get("session")
    return session_turns(sid, conv) if sid else []


def block_md(text):
    """Block-level markdown: headings, lists, tables, fenced code, quotes."""
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("```"):
            buf, i = [], i + 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i]); i += 1
            out.append(f'<pre class="cb"><code>{e(chr(10).join(buf))}</code></pre>'); i += 1
        elif re.match(r"^#{1,4} ", l):
            n = len(l) - len(l.lstrip("#"))
            h = min(n + 2, 5)
            out.append(f"<h{h}>{md(l[n + 1:])}</h{h}>"); i += 1
        elif l.startswith("|") and i + 1 < len(lines) and \
                set(lines[i + 1].replace("|", "").strip()) <= set("-: "):
            hdr = [c.strip() for c in l.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")]); i += 1
            th = "".join(f"<th>{md(c)}</th>" for c in hdr)
            tb = "".join("<tr>" + "".join(f"<td>{md(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f'<div class="tw"><table><thead><tr>{th}</tr></thead>'
                       f"<tbody>{tb}</tbody></table></div>")
        elif re.match(r"^\s*[-*] ", l) or re.match(r"^\s*\d+\. ", l):
            ordered = bool(re.match(r"^\s*\d+\. ", l))
            items = []
            while i < len(lines) and (re.match(r"^\s*[-*] ", lines[i])
                                      or re.match(r"^\s*\d+\. ", lines[i])):
                items.append(re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", lines[i])); i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{md(x)}</li>" for x in items) + f"</{tag}>")
        elif l.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].startswith("> "):
                buf.append(lines[i][2:]); i += 1
            out.append(f"<blockquote>{md(' '.join(buf))}</blockquote>")
        elif not l.strip():
            i += 1
        else:
            buf = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|", "```", "> ")) \
                    and not re.match(r"^\s*(?:[-*]|\d+\.) ", lines[i]):
                buf.append(lines[i]); i += 1
            out.append(f"<p>{md(' '.join(buf))}</p>")
    return "".join(out)


def turns_html(turns, claims):
    if not turns:
        return ('<p class="empty">No turns recorded yet. Claude writes them to '
                '<code>turns.md</code> beside the claims.</p>')
    by_turn = {}
    for c in claims:
        if c.get("turn"):
            by_turn.setdefault(c["turn"], []).append(c["id"])
    out = []
    for tn in turns:
        cids = "".join('<button class="fjump" data-goto="%s">%s</button>' % (e(i), e(i))
                       for i in by_turn.get(tn["id"], []))
        title = '<span class="ttitle">%s</span>' % e(tn["title"]) if tn["title"] else ""
        out.append(
            '<article class="turn-card" id="turn-%s"><div class="tmeta">'
            '<strong>%s</strong><span>%s</span>%s<span class="spacer"></span>%s</div>'
            '<div class="tbody">%s</div></article>'
            % (e(tn["id"]), e(tn["author"]), e(tn["at"].replace("T", " ")[:16]),
               title, cids, block_md(tn["text"])))
    return "".join(out)


# ------------------------------------------------------------------ styles
CSS = """
:root{--bg:#FBFCFD;--panel:#FFF;--ink:#141A1D;--muted:#5A676E;--faint:#8C979D;--line:#E2E7EA;
 --line2:#EDF1F3;--accent:#1F6F78;--code:#F3F6F8;--thread:#F7F9FA;
 --shadow:0 1px 2px rgba(20,26,29,.05),0 8px 24px -12px rgba(20,26,29,.14);
 --ok:#3F7A36;--warn:#9C6413;--crit:#A8283A;
 --dec:#1F6F78;--asm:#9C6413;--fnd:#3F7A36;--opn:#8244A0;--rsk:#A8283A}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0E1214;--panel:#161C1F;
 --ink:#E8EDEF;--muted:#9AA7AD;--faint:#6F7C82;--line:#242D31;--line2:#1D2528;--accent:#5FBFC7;
 --code:#111719;--thread:#12181A;--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
 --ok:#7FC471;--warn:#E0A44B;--crit:#E8798A;
 --dec:#5FBFC7;--asm:#E0A44B;--fnd:#7FC471;--opn:#C08FDB;--rsk:#E8798A}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-size:15px;line-height:1.55;
 font-family:"IBM Plex Sans",ui-sans-serif,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
code,.mono{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:.9em}
a{color:var(--accent)}
.wrap{max-width:940px;margin:0 auto;padding:0 24px}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
header.top{border-bottom:1px solid var(--line);padding:44px 0 26px;background:var(--panel)}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);
 font-weight:600;display:block;margin-bottom:12px}
h1{font-size:clamp(26px,3.6vw,36px);font-weight:600;letter-spacing:-.022em;line-height:1.1;margin:0}
.lede{color:var(--muted);max-width:66ch;margin:12px 0 0}

/* sticky control bar ------------------------------------------------ */
.bar{position:sticky;top:0;z-index:40;background:var(--panel);border-bottom:1px solid var(--line);
 padding:10px 0;box-shadow:var(--shadow)}
.barin{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.chip{border:1px solid var(--line);background:transparent;color:var(--muted);border-radius:999px;
 padding:5px 12px;font:inherit;font-size:12.5px;cursor:pointer;display:inline-flex;gap:7px;align-items:center}
.chip:hover{border-color:var(--accent);color:var(--ink)}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.chip i{width:7px;height:7px;border-radius:50%;background:var(--k);display:inline-block}
.chip b{font-variant-numeric:tabular-nums;font-weight:600}
.spacer{flex:1 1 auto}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:7px;
 padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}
.btn:hover{border-color:var(--accent)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn:disabled{opacity:.45;cursor:not-allowed}
.sync{font-size:12px;color:var(--faint);display:inline-flex;align-items:center;gap:6px}
.sync b{color:var(--ink);font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;background:var(--faint);flex:none}
.dot.live{background:var(--ok)}.dot.buffer{background:var(--warn)}

/* claims ------------------------------------------------------------ */
.claim{border:1px solid var(--line);border-radius:10px;background:var(--panel);margin:14px 0;
 box-shadow:var(--shadow);overflow:hidden}
.claim.hide{display:none}
.crow{display:grid;grid-template-columns:64px 1fr auto;gap:14px;padding:14px 16px;align-items:start}
.cid{font-size:12.5px;color:var(--faint);font-weight:600;padding-top:2px}
.ctext{min-width:0}
.kind{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;font-weight:600;color:var(--k);
 display:block;margin-bottom:3px}
.detail{margin-top:8px}
.detail summary{font-size:12.5px;color:var(--faint);cursor:pointer;list-style:none}
.detail summary::-webkit-details-marker{display:none}
.detail summary::before{content:"\\25B8 ";color:var(--faint)}
.detail[open] summary::before{content:"\\25BE "}
.detail p{margin:8px 0 0;color:var(--muted);font-size:14px;border-left:2px solid var(--line);padding-left:12px}
.ctx{margin:7px 0 0;font-size:12.5px;color:var(--faint);font-style:italic}
.cmeta2{margin-top:9px;display:flex;gap:6px;flex-wrap:wrap}
.tag{border:1px solid var(--line);background:transparent;color:var(--faint);border-radius:5px;
 padding:1px 8px;font:inherit;font-size:11px;cursor:pointer}
.tag:hover{border-color:var(--accent);color:var(--accent)}
.tag.src{border-style:dashed}
.tag.on{background:var(--accent);border-color:var(--accent);color:#fff}
.acts{display:flex;gap:5px;flex:none}
.addbtn{opacity:0;border:1px solid var(--line);background:var(--panel);color:var(--muted);
 border-radius:6px;width:28px;height:28px;font-size:16px;line-height:1;cursor:pointer;flex:none}
.claim:hover .addbtn,.addbtn:focus{opacity:1}
.xbtn:hover{border-color:var(--crit);color:var(--crit)}
.cact{display:flex;gap:8px;align-items:center;padding:10px 16px 12px 78px;
 background:var(--thread);border-top:1px solid var(--line2);font-size:12.5px;color:var(--accent)}
.cact .btn{padding:4px 12px;font-size:12.5px}
.cact .btn.send{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.addbtn:hover{border-color:var(--accent);color:var(--accent)}
.claim.commented{border-left:3px solid var(--accent)}
.claim.wrongf{border-left:3px solid var(--crit)}
.turn{font-size:10px;letter-spacing:.07em;text-transform:uppercase;font-weight:600;color:var(--t);
 border:1px solid var(--t);border-radius:999px;padding:0 7px;margin-left:8px;white-space:nowrap}
.fback{font-size:12.5px;color:var(--accent);border:1px solid var(--accent);
 border-radius:999px;padding:1px 9px}
.claim.focus{border-left:3px solid var(--opn);box-shadow:0 0 0 1px var(--opn),var(--shadow)}
.claim.focus.flash{animation:flash 1.4s ease-out}
@keyframes flash{0%{box-shadow:0 0 0 4px var(--opn)}100%{box-shadow:0 0 0 1px var(--opn),var(--shadow)}}
.focusbar{background:var(--panel);border-bottom:1px solid var(--opn)}
.focusin{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding-top:10px;padding-bottom:10px}
.focusbar strong{font-size:13px;color:var(--opn)}
.fdot{width:8px;height:8px;border-radius:50%;background:var(--opn);flex:none;
 box-shadow:0 0 0 4px color-mix(in srgb,var(--opn) 22%,transparent)}
.fnote{font-size:12.5px;color:var(--muted)}
.fjump{border:1px solid var(--opn);background:transparent;color:var(--opn);border-radius:6px;
 padding:3px 10px;font:inherit;font-size:12px;font-weight:600;cursor:pointer}
.fjump:hover{background:var(--opn);color:var(--bg)}
.claim.dim{opacity:.62;border-left:3px solid var(--faint)}
.claim.dim:hover{opacity:1}
.archive{margin:26px 0 10px;border-top:1px solid var(--line);padding-top:14px}
.archive[hidden]{display:none}
.archive > summary{cursor:pointer;font-size:12.5px;color:var(--faint);letter-spacing:.05em;
 text-transform:uppercase;font-weight:600;list-style:none}
.archive > summary::-webkit-details-marker{display:none}
.archive > summary::before{content:"▸ "}
.archive[open] > summary::before{content:"▾ "}
.archive > summary b{font-variant-numeric:tabular-nums;color:var(--muted)}
.arcnote{font-size:12px;color:var(--faint);margin:8px 0 0}

/* threads ----------------------------------------------------------- */
.thread{background:var(--thread);border-top:1px solid var(--line2);padding:0}
.cmt{padding:12px 16px 12px 78px;border-bottom:1px solid var(--line2)}
.cmt:last-child{border-bottom:0}
.cmeta{font-size:12px;color:var(--faint);margin-bottom:4px;display:flex;gap:8px;align-items:center}
.cmeta strong{color:var(--ink);font-weight:600}
.pill{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;font-weight:600;
 border:1px solid var(--p);color:var(--p);border-radius:999px;padding:1px 8px}
.cbody{font-size:14.2px;white-space:pre-wrap;overflow-x:auto}
.cbody.clip{max-height:9.5em;overflow:hidden;position:relative;
 -webkit-mask-image:linear-gradient(#000 60%,transparent);mask-image:linear-gradient(#000 60%,transparent)}
.more{margin-top:6px;border:1px solid var(--line);background:var(--panel);color:var(--muted);
 border-radius:6px;padding:3px 10px;font:inherit;font-size:11.5px;cursor:pointer}
.more:hover{border-color:var(--accent);color:var(--ink)}
.pend{font-size:10.5px;color:var(--warn);border:1px dashed var(--warn);border-radius:999px;padding:1px 7px}

/* composer ---------------------------------------------------------- */
.compose{padding:12px 16px 14px 78px;background:var(--thread);border-top:1px solid var(--line2)}
.compose.batch{padding:0 0 12px;background:transparent;border-top:0}
.sendbar{position:sticky;bottom:0;z-index:30;background:var(--panel);
 border-top:1px solid var(--accent);box-shadow:0 -8px 24px -14px rgba(0,0,0,.5)}
.sendbar[hidden]{display:none}
.sendin{display:flex;gap:14px;align-items:center;padding:13px 24px}
.sendtxt{font-size:13px;color:var(--muted)}
.sendtxt strong{color:var(--ink);display:block;font-size:14px}
.sendin .btn{padding:9px 18px;font-size:14px}
.sendin #copy2{margin-left:auto}
.btn.send{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.btn.send:hover{filter:brightness(1.1)}
.turn-card{border:1px solid var(--line);border-radius:10px;background:var(--panel);
 margin:16px 0;box-shadow:var(--shadow);overflow:hidden}
.tmeta{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:11px 18px;
 border-bottom:1px solid var(--line2);font-size:12px;color:var(--faint);background:var(--thread)}
.tmeta strong{color:var(--ink);font-size:13px}
.ttitle{color:var(--muted);font-weight:500}
.tbody{padding:6px 18px 18px}
.tbody h3,.tbody h4,.tbody h5{margin:20px 0 6px;font-size:15px;font-weight:600;letter-spacing:-.01em}
.tbody p{margin:9px 0;color:var(--muted)}
.tbody li{color:var(--muted);margin:3px 0}
.tbody blockquote{margin:10px 0;padding-left:13px;border-left:2px solid var(--line);color:var(--faint)}
.cb{background:var(--code);border:1px solid var(--line2);border-radius:8px;padding:12px 14px;
 overflow-x:auto;font-size:12.5px;margin:10px 0}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:8px;margin:10px 0}
.tw table{border-collapse:collapse;width:100%}
.tw th{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--faint);
 text-align:left;padding:9px 13px;border-bottom:1px solid var(--line)}
.tw td{padding:9px 13px;border-bottom:1px solid var(--line2);font-size:13.5px;color:var(--muted)}
.tw tr:last-child td{border-bottom:0}
.compose textarea{width:100%;min-height:74px;resize:vertical;border:1px solid var(--line);
 border-radius:7px;background:var(--panel);color:var(--ink);font:inherit;font-size:14px;padding:9px 11px}
.compose textarea:focus{outline:2px solid var(--accent);outline-offset:-1px;border-color:var(--accent)}
/* Dictation. Three states, because a mic that does not visibly hear you is
   indistinguishable from a broken one: idle, connecting, listening. */
.btn.mic{display:inline-flex;align-items:center;gap:6px}
.btn.mic .dot{width:8px;height:8px;border-radius:50%;background:var(--faint);flex:none}
.btn.mic[data-state="connecting"] .dot{background:var(--warn);animation:micblink .8s infinite}
.btn.mic[data-state="listening"]{border-color:var(--crit);color:var(--crit)}
.btn.mic[data-state="listening"] .dot{background:var(--crit);animation:micpulse 1.2s infinite}
@keyframes micblink{0%,100%{opacity:1}50%{opacity:.25}}
@keyframes micpulse{0%{box-shadow:0 0 0 0 rgba(220,80,80,.55)}
                    70%{box-shadow:0 0 0 7px rgba(220,80,80,0)}
                    100%{box-shadow:0 0 0 0 rgba(220,80,80,0)}}
/* Interim text is not yours yet -- the service can still revise it, so it
   reads as provisional until an endpoint frame commits it into the textarea. */
.compose textarea.dictating{border-color:var(--crit)}
.interim{color:var(--faint);font-style:italic;padding:5px 2px 0;font-size:12px;min-height:1em}
.micerr{color:var(--crit);font-size:12px;padding:5px 2px 0}
.crow2{display:flex;gap:8px;margin-top:9px;align-items:center;flex-wrap:wrap}
.hint{font-size:11.5px;color:var(--faint)}
.empty{color:var(--faint);text-align:center;padding:40px 0;font-size:14px}
.convs{background:var(--panel);border-bottom:1px solid var(--line)}
.convsin{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding-top:9px;padding-bottom:9px}
.convlbl{font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--faint);
 font-weight:600;margin-right:6px}
.ctab{border:1px solid var(--line);background:transparent;color:var(--muted);border-radius:7px;
 padding:5px 11px;font:inherit;font-size:12.5px;cursor:pointer;display:inline-flex;gap:8px;align-items:center}
.ctab:hover{border-color:var(--accent);color:var(--ink)}
.ctab.on{border-color:var(--accent);color:var(--ink);background:var(--thread);font-weight:600}
.cnt{font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}
.cnt.op{color:var(--opn)}.cnt.wr{color:var(--crit);font-weight:600}
.src{margin:14px 0 0;font-size:12.5px;color:var(--faint)}
.src a{color:var(--accent);text-decoration:none}.src a:hover{text-decoration:underline}
footer{border-top:1px solid var(--line);padding:26px 0 60px;color:var(--faint);font-size:12.5px}
@media(max-width:640px){.crow{grid-template-columns:44px 1fr auto;gap:9px}
 .cmt,.compose{padding-left:20px}.addbtn{opacity:1}}
"""


# ------------------------------------------------------------------ behavior
JS = r"""
const CLAIMS=__CLAIMS__, DISK=__COMMENTS__, DISK_MD=__DISKMD__;
const SLUG=__SLUG__, RENDER_ID=__RENDERID__, AUTHOR=__AUTHOR__;
const STATUSES=__STATUSES__, FOCUS=__FOCUS__, TAGS=__TAGS__;
/* Baked at render time, then recomputed after every comment: submitting adds to
   DISK client-side without re-running ledger.py, so a server-side snapshot goes
   stale the moment you comment -- the turn would never flip and the Send bar
   would never appear. Must mirror turns() in the python exactly. */
let TURNS=__TURNS__;
const CLOSERS=["dismissed","ok","parked"];
function computeTurns(){
  const last={}; all().forEach(c=>{last[c.claim]=c;});
  const out={};
  CLAIMS.forEach(cl=>{
    const c=last[cl.id];
    if(!c) out[cl.id]=(cl.kind==="open")?"you":"closed";
    else if(CLOSERS.indexOf(c.status)>=0) out[cl.id]="closed";
    else if(String(c.author).toLowerCase()==="claude") out[cl.id]=(cl.kind==="open")?"you":"closed";
    else out[cl.id]="claude";
  });
  /* Mirrors the python: an archived claim is closed, and focus cannot
     override that. Otherwise dismissing a focused card leaves the chip
     claiming it still needs you. */
  const arch=new Set(CLAIMS.filter(c=>{const l=last[c.id];
    return l&&(l.status==="dismissed"||l.status==="parked");}).map(c=>c.id));
  FOCUS.forEach(i=>{if(out[i]!=="claude"&&!arch.has(i)) out[i]="you";});
  arch.forEach(i=>{out[i]="closed";});
  return out;
}

/* Pending comments are keyed by RENDER_ID. When ledger.py re-renders, the id
   changes and any buffer from an older render is dropped -- by then those
   comments are in comments.md and rendering them again would double them. */
const PKEY = `ledger:${SLUG}:${RENDER_ID}`;
for(let i=localStorage.length-1;i>=0;i--){const k=localStorage.key(i);
  if(k.startsWith(`ledger:${SLUG}:`) && k!==PKEY) localStorage.removeItem(k);}
let pending=[];
try{pending=JSON.parse(localStorage.getItem(PKEY)||"[]");}catch(e){pending=[];}
const savePending=()=>{try{localStorage.setItem(PKEY,JSON.stringify(pending));}catch(e){}};

/* ---- disk layer -----------------------------------------------------
   Two hosts, both of which write to comments.md without asking permission:
     VSCODE  rendered by the extension -> postMessage, extension appends
     SERVED  ledger.py --serve          -> POST /api/comment
   Anything else (a file:// page, a preview tab) is read-only and says so.
   A third branch used to try the File System Access API. It was removed: it
   cannot work in Firefox at all, and its failure showed up as a bare
   "Folder not connected: TypeError". */
const SERVED = location.protocol==="http:" || location.protocol==="https:";
let VSC=null;
try{ if(typeof acquireVsCodeApi!=="undefined") VSC=acquireVsCodeApi(); }catch(err){ VSC=null; }
if(VSC) try{ VSC.setState({conv:__CONVDIR__}); }catch(err){}
const MODE = VSC ? "vscode" : (SERVED ? "served" : "readonly");

async function post(c){
  const r=await fetch("api/comment",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify(c)});
  if(!r.ok) throw new Error("HTTP "+r.status);
  return r.json();
}

function setStatus(msg,live){
  const d=document.getElementById("dot"), t=document.getElementById("stxt");
  d.className="dot"+(live?" live":(pending.length?" buffer":""));
  t.textContent=msg;
}
function statusLine(){
  const n=pending.length;
  if(MODE==="vscode") return ["VS Code panel - writing to comments.md",true];
  if(MODE==="served") return ["Served - writing to comments.md",true];
  return [n?`${n} comment${n>1?"s":""} held in this browser - use Copy`
           :"Read-only: open with 'Ledger: review' in VS Code to comment",false];
}

/* ---- rendering ---------------------------------------------------- */
const all=()=>DISK.concat(pending.map(p=>({...p,pending:true})));
/* Default to the open claims: they are why the page is open. Clicking the
   pressed chip clears it and shows everything. */
let tagFilter=null;
let filter=null, onlyUncommented=false, onlyClaude=false,
    onlyMine=CLAIMS.some(c=>TURNS[c.id]==="you");

const MAIN=document.getElementById("cardlist");
const ARC=document.getElementById("arcbody");

const ARCHIVED=new Set();

function render(){
  TURNS=computeTurns();
  ARCHIVED.clear();
  let nArc=0;
  const byClaim={};
  all().forEach(c=>{(byClaim[c.claim]=byClaim[c.claim]||[]).push(c);});
  let shown=0;
  CLAIMS.forEach(cl=>{
    const el=document.getElementById("claim-"+cl.id); if(!el) return;
    const cs=byClaim[cl.id]||[];
    /* Archived = the last word on it was Dismiss or Park. A later comment
       un-archives it, which is what makes this reversible rather than a delete. */
    const last=cs.length?cs[cs.length-1]:null;
    const archived=!!last&&(last.status==="dismissed"||last.status==="parked");
    const hit=(!filter||cl.kind===filter)&&(!onlyUncommented||!cs.length)
              &&(!onlyClaude||TURNS[cl.id]==="claude")
              &&(!onlyMine||TURNS[cl.id]==="you")
              &&(!tagFilter||(TAGS[cl.id]||[]).indexOf(tagFilter)>=0);
    el.classList.toggle("hide", archived ? false : !hit);
    if(hit && !archived) shown++;
    /* appendChild moves the node, so re-appending every claim in order keeps
       both lists correctly sorted without rebuilding any html. */
    const target=archived?ARC:MAIN;
    if(el.parentElement!==target) target.appendChild(el);
    if(archived){ nArc++; ARCHIVED.add(cl.id); }
    el.classList.toggle("commented",cs.length>0);
    el.classList.toggle("wrongf",cs.some(c=>c.status==="wrong"));
    el.classList.toggle("dim",archived);
    const tb=el.querySelector(".turn");
    if(tb){const L={you:["Your turn","var(--opn)"],claude:["Claude's turn","var(--accent)"],
                    closed:["Closed","var(--faint)"]}[TURNS[cl.id]];
      tb.textContent=L[0]; tb.style.setProperty("--t",L[1]);}
    const t=el.querySelector(".thread");
    t.innerHTML=cs.map(c=>{
      const st=STATUSES[c.status]||STATUSES.note;
      return `<div class="cmt"><div class="cmeta"><strong>${esc(c.author)}</strong>
        <span class="pill" style="--p:${st[1]}">${esc(st[0])}</span>
        <span>${esc(c.at.replace("T"," ").slice(0,16))}</span>
        ${c.pending?'<span class="pend">unsaved</span>':""}</div>
        <div class="cbody${c.text.length>420?" clip":""}">${esc(c.text)}</div>
        ${c.text.length>420?`<button class="more">Show all ${c.text.length.toLocaleString()} characters</button>`:""}
        </div>`;}).join("");
    /* The page-bottom Send bar is easy to miss from a card halfway down, so
       the claim that needs sending carries its own button. */
    if(TURNS[cl.id]==="claude"){
      t.innerHTML+=`<div class="cact"><span>Waiting to reach Claude</span>
        <button class="btn send" data-act="send" data-claim="${cl.id}">Send</button>
        <button class="btn" data-act="copy" data-claim="${cl.id}">Copy</button></div>`;
    }
    t.style.display=cs.length?"":"none";
  });
  document.getElementById("empty").style.display=shown?"none":"";
  const arc=document.getElementById("archive");
  arc.hidden=!nArc; document.getElementById("arcn").textContent=nArc;
  const [msg,live]=statusLine(); setStatus(msg,live);
  /* Claude does not watch the filesystem: he sees comments on your next chat
     message. This bar makes that explicit instead of leaving you wondering. */
  const owed=CLAIMS.filter(c=>TURNS[c.id]==="claude").map(c=>c.id);
  const nyou=CLAIMS.filter(c=>TURNS[c.id]==="you").length;
  const mb=document.querySelector("#mine b"); if(mb) mb.textContent=nyou;
  /* Kind counts are rendered server-side and went stale the moment a card was
     archived. Recount the live list on every pass. */
  document.querySelectorAll(".chip[data-kind]").forEach(ch=>{
    const n=CLAIMS.filter(c=>c.kind===ch.dataset.kind&&!ARCHIVED.has(c.id)).length;
    const b=ch.querySelector("b"); if(b) b.textContent=n;
    ch.style.display=n?"":"none";});
  const cb=document.querySelector("#tcl b"); if(cb) cb.textContent=owed.length;
  const sb=document.getElementById("sendbar");
  if(sb){ sb.hidden=!owed.length;
    document.getElementById("sendn").textContent=
      owed.length===1?`1 reply on ${owed[0]}`:`${owed.length} replies: ${owed.join(", ")}`; }
  const n=pending.length;
  const cp=document.getElementById("copy");
  if(cp){ cp.style.display=(MODE==="readonly")?"":"none"; cp.disabled=!pending.length; }
}
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

const DEFAULT_TEXT={dismissed:"Not a concern here.",parked:"Parked - revisit later.",
                    ok:"Confirmed."};

async function submitOne(id,st,text){
  const cm={claim:id,author:AUTHOR,at:new Date().toISOString().slice(0,19),status:st,text:text};
  if(VSC){VSC.postMessage({type:"comment",comment:cm});DISK.push(cm);
    setStatus("Saved to comments.md",true);return;}
  if(SERVED){
    try{await post(cm);DISK.push(cm);setStatus("Saved to comments.md",true);}
    catch(err){pending.push(cm);savePending();
      setStatus("Server unreachable ("+err.message+") - held in this browser.",false);}
    return;}
  pending.push(cm);savePending();          /* read-only host: Copy hands it over */
}

function jump(id){
  const el=document.getElementById("claim-"+id); if(!el) return;
  if(el.classList.contains("hide")){filter=null;onlyUncommented=false;tagFilter=null;
    onlyMine=false;onlyClaude=false;
    const mb=document.getElementById("mine"); if(mb) mb.setAttribute("aria-pressed","false");
    const cb=document.getElementById("tcl"); if(cb) cb.setAttribute("aria-pressed","false");
    render();}
  el.scrollIntoView({behavior:"smooth",block:"center"});
  el.classList.add("flash"); setTimeout(()=>el.classList.remove("flash"),1500);
}

/* ---- composer ------------------------------------------------------ */
function openComposer(id){
  const el=document.getElementById("claim-"+id);
  let c=el.querySelector(".compose");
  if(c){c.querySelector("textarea").focus();return;}
  c=document.createElement("div"); c.className="compose";
  c.innerHTML=`<textarea placeholder="What's wrong with this, or what does it need?"></textarea>
    <div class="crow2">
      <button class="btn primary" data-s="note">Add note</button>
      <button class="btn" data-s="wrong">Mark wrong</button>
      <button class="btn" data-s="ok">Confirm</button>
      <button class="btn" data-s="parked">Park</button>
      <button class="btn" data-s="dismissed">Dismiss</button>
      <button class="btn" data-s="cancel">Cancel</button>
      <button class="btn mic" data-mic="1" type="button" data-state="idle"
        title="Dictate (Alt+D)"><span class="dot"></span>Dictate</button>
      <span class="hint">&#8984;/Ctrl + Enter to add a note</span></div>
    <div class="interim" hidden></div>
    `;
  el.appendChild(c);
  const ta=c.querySelector("textarea"); ta.focus();
  attachDictation(c,ta);
  const submit=async st=>{
    const text=ta.value.trim();
    if(!text && st!=="ok" && st!=="dismissed" && st!=="parked"){ta.focus();return;}
    stopDictation(c);
    c.remove();
    await submitOne(id,st,text||DEFAULT_TEXT[st]||"Confirmed.");
    render();
  };
  c.querySelectorAll("button").forEach(b=>b.onclick=()=>{
    if(b.dataset.mic) return;            // the mic owns its own handler
    const s=b.dataset.s;
    if(s==="cancel"){stopDictation(c);c.remove();return;} submit(s);});
  ta.onkeydown=ev=>{if((ev.metaKey||ev.ctrlKey)&&ev.key==="Enter")submit("note");
                    if(ev.key==="Escape")c.remove();};
}

/* ---- dictation ------------------------------------------------------
   Typing a comment is the friction this whole panel exists to remove, so the
   composer can be spoken into instead.

   The page owns the INTERACTION only; it never touches a microphone or a
   network. It asks the extension to start a channel and renders what comes
   back. That split is deliberate: a webview cannot capture audio here, and
   keeping the transport behind one message boundary means the backend can be
   swapped -- today a fake, later a real transcriber -- without the page
   changing at all.

   Three visible states, because a mic that does not visibly hear you is
   indistinguishable from a broken one:
     idle        grey dot, "Dictate"
     connecting  amber blinking dot -- asked, nothing heard yet
     listening   red pulsing dot, red textarea border

   Interim text renders BELOW the box in grey italic rather than in it. The
   service revises interim results, and watching your own sentence rewrite
   itself under the cursor is unpleasant; only committed text lands in the
   textarea. */
const DICT={ch:null, box:null, ta:null, seq:0};

function micBtn(box){ return box.querySelector(".btn.mic"); }

function micState(box,state,err){
  const b=micBtn(box); if(!b) return;
  b.dataset.state=state;
  b.lastChild.textContent = state==="listening" ? "Stop"
                          : state==="connecting" ? "Starting" : "Dictate";
  if(DICT.ta) DICT.ta.classList.toggle("dictating",state==="listening");
  const iv=box.querySelector(".interim");
  if(iv && state==="idle"){ iv.hidden=true; iv.textContent=""; }
  let e=box.querySelector(".micerr");
  if(err){ if(!e){e=document.createElement("div");e.className="micerr";box.appendChild(e);}
           e.textContent=err; }
  else if(e) e.remove();
}

function attachDictation(box,ta){
  const b=micBtn(box); if(!b) return;
  if(!VSC){            // file:// or a preview tab: no extension to ask
    b.disabled=true;
    b.title="Dictation needs the VS Code panel";
    b.style.opacity=.45;
    return;
  }
  b.onclick=()=>{ (DICT.box===box && DICT.ch) ? stopDictation(box) : startDictation(box,ta); };
  ta.addEventListener("keydown",ev=>{
    if(ev.altKey && (ev.key==="d"||ev.key==="D")){ev.preventDefault();b.onclick();}
  });
}

function startDictation(box,ta){
  if(DICT.ch) stopDictation(DICT.box);       // one mic, one composer
  DICT.ch="d"+(++DICT.seq); DICT.box=box; DICT.ta=ta;
  micState(box,"connecting");
  VSC.postMessage({type:"dictate:start",channel:DICT.ch});
}

function stopDictation(box){
  if(!DICT.ch || (box && DICT.box!==box)) return;
  VSC.postMessage({type:"dictate:stop",channel:DICT.ch});
  micState(DICT.box,"idle");
  DICT.ch=null; DICT.box=null; DICT.ta=null;
}

/* Commit a finished utterance into the textarea, spacing it from whatever is
   already there and leaving the caret at the end so typing continues to work
   mid-dictation. */
function commitSpeech(text){
  const ta=DICT.ta; if(!ta||!text) return;
  const cur=ta.value;
  ta.value = cur && !/\s$/.test(cur) ? cur+" "+text : cur+text;
  ta.selectionStart=ta.selectionEnd=ta.value.length;
}

window.addEventListener("message",ev=>{
  const m=ev.data||{};
  if(!m.type || m.type.indexOf("dictate:")!==0) return;
  if(m.channel && m.channel!==DICT.ch) return;      // a stale channel
  if(m.type==="dictate:state"){ micState(DICT.box,m.state,m.error); return; }
  if(m.type==="dictate:error"){
    micState(DICT.box,"idle",m.error||"Dictation failed.");
    DICT.ch=null; DICT.box=null; DICT.ta=null; return;
  }
  if(m.type==="dictate:text"){
    if(DICT.box) micState(DICT.box,"listening");
    const iv=DICT.box && DICT.box.querySelector(".interim");
    if(m.done){ commitSpeech(m.text); if(iv){iv.hidden=true;iv.textContent="";} }
    else if(iv){ iv.hidden=false; iv.textContent=m.text; }
  }
});

/* One action for every claim still on your side -- so finishing a turn does
   not mean typing the same thing into five composers. */
function openBatch(){
  const box=document.getElementById("batchbox");
  if(!box || box.firstChild){box.innerHTML="";return;}
  const ids=CLAIMS.filter(c=>TURNS[c.id]==="you").map(c=>c.id);
  if(!ids.length){return;}
  const d=document.createElement("div"); d.className="compose batch";
  d.innerHTML=`<textarea placeholder="One reply for all ${ids.length}: ${ids.join(", ")}"></textarea>
    <div class="crow2">
      <button class="btn primary" data-s="note">Note on all ${ids.length}</button>
      <button class="btn" data-s="parked">Park all</button>
      <button class="btn" data-s="dismissed">Dismiss all</button>
      <button class="btn" data-s="cancel">Cancel</button>
      <button class="btn mic" data-mic="1" type="button" data-state="idle"
        title="Dictate (Alt+D)"><span class="dot"></span>Dictate</button>
      <span class="hint">Ends your turn on every one of them at once.</span></div>
    <div class="interim" hidden></div>`;
  box.appendChild(d);
  const ta=d.querySelector("textarea"); ta.focus();
  attachDictation(d,ta);
  d.querySelectorAll("button").forEach(b=>b.onclick=async()=>{
    if(b.dataset.mic) return;
    const st=b.dataset.s;
    if(st==="cancel"){stopDictation(d);box.innerHTML="";return;}
    const text=ta.value.trim();
    if(!text && st==="note"){ta.focus();return;}
    for(const id of ids){
      await submitOne(id,st,text||DEFAULT_TEXT[st]||"Noted.");
    }
    box.innerHTML=""; render();
  });
}

/* ---- wiring -------------------------------------------------------- */
document.addEventListener("click",ev=>{
  const xb0=ev.target.closest("[data-dismiss]");
  if(xb0){ submitOne(xb0.dataset.dismiss,"dismissed",DEFAULT_TEXT.dismissed).then(render); return; }
  const a=ev.target.closest(".addbtn[data-id]"); if(a){openComposer(a.dataset.id);return;}
  /* A webview cannot follow a file:// link; ask the extension to open it. */
  const o=ev.target.closest("[data-open]");
  if(o && VSC){ev.preventDefault(); VSC.postMessage({type:"open",path:o.dataset.open});return;}
  const ac=ev.target.closest("[data-act]");
  if(ac){
    const ids=[ac.dataset.claim];
    const text=ac.dataset.act==="send"?pointerText(ids):fullText(ids);
    if(VSC) VSC.postMessage({type:ac.dataset.act,text:text});
    else if(navigator.clipboard) navigator.clipboard.writeText(text);
    ac.textContent=ac.dataset.act==="send"?"Handed over":"Copied";
    return;
  }
  const tg=ev.target.closest(".tag[data-tag]");
  if(tg){ tagFilter=(tagFilter===tg.dataset.tag)?null:tg.dataset.tag;
    onlyMine=false; document.getElementById("mine").setAttribute("aria-pressed","false");
    document.querySelectorAll(".tag[data-tag]").forEach(x=>
      x.classList.toggle("on",x.dataset.tag===tagFilter));
    render(); return; }
  const ts=ev.target.closest(".tag[data-turn]");
  if(ts){ const tv=document.getElementById("turnsview");
    tv.hidden=false; document.getElementById("claimsview").hidden=true;
    document.getElementById("vturns").setAttribute("aria-pressed","true");
    const el=document.getElementById("turn-"+ts.dataset.turn);
    if(el){el.scrollIntoView({behavior:"smooth",block:"start"});}
    return; }
  const mo=ev.target.closest(".more");
  if(mo){const b=mo.previousElementSibling;b.classList.toggle("clip");
    mo.textContent=b.classList.contains("clip")
      ?`Show all ${b.textContent.length.toLocaleString()} characters`:"Collapse";return;}
  const j=ev.target.closest("[data-goto]");
  if(j){ if(!document.getElementById("turnsview").hidden){
           document.getElementById("turnsview").hidden=true;
           document.getElementById("claimsview").hidden=false;
           document.getElementById("vturns").setAttribute("aria-pressed","false");}
         jump(j.dataset.goto);return;}
  const c=ev.target.closest(".ctab");
  if(c && !c.classList.contains("on")){
    if(VSC) VSC.postMessage({type:"switch",dir:c.dataset.conv});
    else location.href="../"+c.dataset.slug+"/index.html";
  }});
document.querySelectorAll(".chip[data-kind]").forEach(ch=>{
  ch.setAttribute("aria-pressed",String(ch.dataset.kind===filter));});
document.querySelectorAll(".chip[data-kind]").forEach(ch=>ch.onclick=()=>{
  const k=ch.dataset.kind; filter=(filter===k)?null:k;
  if(filter){onlyMine=false;document.getElementById("mine").setAttribute("aria-pressed","false");}
  document.querySelectorAll(".chip[data-kind]").forEach(o=>
    o.setAttribute("aria-pressed",String(o.dataset.kind===filter)));
  render();});
document.getElementById("unc").onclick=function(){
  onlyUncommented=!onlyUncommented; this.setAttribute("aria-pressed",String(onlyUncommented)); render();};
document.getElementById("mine").onclick=function(){
  onlyMine=!onlyMine; if(onlyMine){onlyClaude=false;
    document.getElementById("tcl").setAttribute("aria-pressed","false");}
  this.setAttribute("aria-pressed",String(onlyMine)); render();};
document.getElementById("tcl").onclick=function(){
  onlyClaude=!onlyClaude;
  if(onlyClaude){filter=null; onlyMine=false;
    document.getElementById("mine").setAttribute("aria-pressed","false");
    document.querySelectorAll(".chip[data-kind]").forEach(o=>o.setAttribute("aria-pressed","false"));}
  this.setAttribute("aria-pressed",String(onlyClaude)); render();};

/* Claims are the distillation; the turns are what produced them. Same page,
   two readings of the same conversation. */
document.getElementById("vturns").onclick=function(){
  const on=document.getElementById("turnsview").hidden;
  document.getElementById("turnsview").hidden=!on;
  document.getElementById("claimsview").hidden=on;
  this.setAttribute("aria-pressed",String(on));};
/* Two buttons on purpose: Copy is the one that always works, Send is the one
   that needs the extension patch. Guessing which you wanted was the mistake. */
function owedIds(){ return CLAIMS.filter(c=>TURNS[c.id]==="claude").map(c=>c.id); }

/* Send only needs a pointer -- Claude reads comments.md off disk. Copy has to
   carry the whole thing, because it is for pasting somewhere with no access to
   this repo: another tab, a ticket, a message. */
function pointerText(ids){
  ids=ids||owedIds();
  return `Read my ledger comments on ${SLUG}: ${ids.join(", ")}`;
}
function fullText(ids){
  ids=ids||owedIds();
  const byClaim={};
  all().forEach(c=>{(byClaim[c.claim]=byClaim[c.claim]||[]).push(c);});
  const parts=[`Ledger comments on ${SLUG}:`,""];
  ids.forEach(id=>{
    const cl=CLAIMS.find(c=>c.id===id)||{};
    const el=document.getElementById("claim-"+id);
    const claimText=el?el.querySelector(".ctext > div").textContent.trim():"";
    parts.push(`## ${id} (${cl.kind||""})`);
    if(claimText) parts.push(`> ${claimText}`);
    (byClaim[id]||[]).forEach(c=>{
      parts.push(`${c.author} [${(STATUSES[c.status]||["Note"])[0]}]: ${c.text}`);});
    parts.push("");
  });
  return parts.join("\n").trim();
}
const cp2=document.getElementById("copy2");
if(cp2) cp2.onclick=function(){
  const text=fullText();
  if(VSC) VSC.postMessage({type:"copy",text:text});
  else if(navigator.clipboard) navigator.clipboard.writeText(text);
  this.textContent="Copied in full"; setTimeout(()=>this.textContent="Copy",2200);};
const sd=document.getElementById("send");
if(sd) sd.onclick=function(){
  const text=pointerText();
  if(VSC) VSC.postMessage({type:"send",text:text});
  else if(navigator.clipboard) navigator.clipboard.writeText(text);
  this.textContent="Handed over"; setTimeout(()=>this.textContent="Send to Claude",2400);};
const bb=document.getElementById("batch"); if(bb) bb.onclick=openBatch;
document.getElementById("copy").onclick=async function(){
  const md=pending.map(c=>
    `### ${c.claim} ${c.author} ${c.at} ${c.status}\n${c.text}\n`).join("\n");
  try{await navigator.clipboard.writeText(md);}
  catch(e){const t=document.createElement("textarea");t.value=md;document.body.appendChild(t);
    t.select();document.execCommand("copy");t.remove();}
  this.textContent="Copied - paste into chat"; setTimeout(()=>this.textContent="Copy new comments",2200);};

render();
"""


# ------------------------------------------------------------------ assemble
SESSIONS = SESSION_DIR          # one definition; both derive from the workspace


def source_line(meta):
    """Point at the transcript in the volume rather than copying it.

    The .jsonl is live -- Claude appends to it while the session runs -- and it
    is already mounted here. Copying it into the repo would duplicate megabytes
    and go stale the moment the conversation continued.
    """
    sid = meta.get("session")
    if not sid:
        return ""
    f = SESSIONS / f"{sid}.jsonl"
    if not f.exists():
        return (f'<p class="src">Transcript <code>{e(sid[:8])}</code> is not in this '
                f'container\'s volume.</p>')
    mb = f.stat().st_size / 1048576
    return (f'<p class="src">Transcript: <a href="{e(f)}" data-open="{e(f)}">'
            f'<code>{e(sid[:8])}.jsonl</code></a> &middot; {mb:.1f} MB &middot; '
            f'live in the container volume, not copied</p>')


TURN = {"you": ("Your turn", "var(--opn)"),
        "claude": ("Claude's turn", "var(--accent)"),
        "closed": ("Closed", "var(--faint)")}


def turns(claims, comments, author):
    """Whose move it is on each claim, from the last comment on it.

    A ledger is a conversation, so a claim is never just open or shut -- it is
    waiting on one of us. Without this, a claim Angel answered looks identical
    to one nobody has touched, and his reply sits unanswered.
    """
    last = {}
    for c in comments:
        last[c["claim"]] = c          # comments.md is append-only, so last wins
    out = {}
    for cl in claims:
        c = last.get(cl["id"])
        if c is None:
            out[cl["id"]] = "you" if cl["kind"] == "open" else "closed"
        elif c["status"] in ("dismissed", "ok", "parked"):
            out[cl["id"]] = "closed"
        elif c["author"].lower() == "claude":
            # Claude replied: back to Angel only if the question is still live.
            out[cl["id"]] = "you" if cl["kind"] == "open" else "closed"
        else:
            out[cl["id"]] = "claude"   # Angel spoke last and wants something
    return out


def focus_ids(meta, claims, comments):
    """Which claims Claude is asking Angel to act on.

    Explicit `focus: C17, C23` in the frontmatter wins. With none set, every
    `open` claim that has no comment yet is implicitly waiting on him -- an
    open question nobody has answered IS the ask.
    """
    raw = meta.get("focus", "").replace(",", " ").split()
    ids = {c["id"] for c in claims}
    explicit = [i for i in raw if i in ids]
    if explicit:
        return explicit
    answered = {c["claim"] for c in comments}
    return [c["id"] for c in claims if c["kind"] == "open" and c["id"] not in answered]


def focus_banner(ids, meta, n_you=0, n_cl=0):
    if not (ids or n_cl):
        return ""
    note = meta.get("focus_note", "")
    chips = "".join(f'<button class="fjump" data-goto="{e(i)}">{e(i)}</button>' for i in ids)
    left = f'<span class="fdot"></span><strong>{len(ids)} waiting on you</strong>'
    if n_cl:
        left += f'<span class="fback">{n_cl} back with Claude</span>'
    return (f'<div class="focusbar"><div class="wrap focusin">{left}'
            f'{f"<span class=fnote>{md(note)}</span>" if note else ""}'
            f'<span class="spacer"></span>{chips}'
            f'<button class="btn" id="batch">Answer all</button>'
            f'</div><div class="wrap" id="batchbox"></div></div>')


def siblings(conv):
    """Every conversation under the same PRD, for the switcher.

    A PRD is not one conversation -- sources, materialization and architecture
    were each their own session -- and a claim made in one is regularly the
    thing another one is about to contradict.
    """
    out = []
    for d in sorted(conv.parent.iterdir()):
        lg = d / "ledger.md"
        if not (d.is_dir() and lg.exists()):
            continue
        try:
            m, cl = parse_ledger(lg)
        except SystemExit:
            continue
        cs = parse_comments(d / "comments.md")
        out.append({
            "slug": d.name, "title": m.get("title", d.name), "dir": str(d),
            "claims": len(cl),
            "open": sum(1 for c in cl if c["kind"] == "open"),
            "wrong": sum(1 for c in cs if c["status"] == "wrong"),
            "current": d.resolve() == conv.resolve(),
        })
    return out


def claim_meta(c):
    """Tag chips, and a link back to the turn that produced the claim.

    A claim read cold is hard to judge: `context:` says what prompted it and
    `tags:` says what area it belongs to, which is what makes 60 claims
    navigable instead of a wall.
    """
    bits = "".join(f'<button class="tag" data-tag="{e(x)}">{e(x)}</button>' for x in c.get("tags", []))
    if c.get("turn"):
        bits += (f'<button class="tag src" data-turn="{e(c["turn"])}" '
                 f'title="Show the reply this came from">from {e(c["turn"])}</button>')
    return f'<div class="cmeta2">{bits}</div>' if bits else ""


def conv_nav(convs, prd):
    if len(convs) < 2:
        return ""
    tabs = []
    for c in convs:
        on = " on" if c["current"] else ""
        aria = ' aria-current="page"' if c["current"] else ""
        badges = f'<span class="cnt">{c["claims"]}</span>'
        if c["open"]:
            badges += f'<span class="cnt op">{c["open"]} open</span>'
        if c["wrong"]:
            badges += f'<span class="cnt wr" title="marked wrong">{c["wrong"]}</span>'
        tabs.append(f'<button class="ctab{on}"{aria} data-conv="{e(c["dir"])}" '
                    f'data-slug="{e(c["slug"])}">{e(c["title"])}{badges}</button>')
    return ('<nav class="convs"><div class="wrap convsin">'
            f'<span class="convlbl">{e(prd)}</span>{"".join(tabs)}</div></nav>')


def build(meta, claims, comments, slug, disk_md, author, render_id, convs=None,
          turns_list=None, convdir=None):
    focus = focus_ids(meta, claims, comments)
    turn = turns(claims, comments, author)
    # Anything Claude has explicitly focused is Angel's move by definition --
    # unless Angel has archived it. Dismissing a claim has to beat Claude's
    # opinion that it needs attention, or the count says "1 waiting on you"
    # pointing at a card sitting in the Archive.
    last_status = {}
    for c in comments:
        last_status[c["claim"]] = c["status"]
    archived = {k for k, v in last_status.items() if v in ("dismissed", "parked")}
    for i in focus:
        if turn.get(i) != "claude" and i not in archived:
            turn[i] = "you"
    for i in archived:
        turn[i] = "closed"
    # The banner lists every claim on his side, focused or not -- a focus list
    # that is a subset of the count is what made the numbers disagree.
    mine = [c["id"] for c in claims if turn[c["id"]] == "you"]
    n_you = len(mine)
    n_cl = sum(1 for v in turn.values() if v == "claude")
    counts = {}
    for c in claims:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    chips = "".join(
        f'<button class="chip" data-kind="{k}" aria-pressed="false" style="--k:{col}" '
        f'title="{e(desc)}"><i></i>{e(label)}<b>{counts[k]}</b></button>'
        for k, (label, col, desc) in KINDS.items() if counts.get(k))

    body = "".join(f'''<article class="claim{' focus' if c["id"] in focus else ''}" id="claim-{c["id"]}" data-kind="{c["kind"]}">
  <div class="crow">
    <div class="cid mono">{e(c["id"])}</div>
    <div class="ctext">
      <span class="kind" style="--k:{KINDS[c["kind"]][1]}">{e(KINDS[c["kind"]][0])}</span>
      <span class="turn" style="--t:{TURN[turn[c["id"]]][1]}">{e(TURN[turn[c["id"]]][0])}</span>
      <div>{md(c["text"])}</div>
      {f'<p class="ctx">{md(c["context"])}</p>' if c.get("context") else ""}
      {f'<details class="detail"><summary>why</summary><p>{md(c["detail"])}</p></details>'
       if c["detail"] else ""}
      {claim_meta(c)}
    </div>
    <div class="acts">
      <button class="addbtn" data-id="{c["id"]}" title="Comment on {e(c["id"])}"
              aria-label="Comment on {e(c["id"])}">+</button>
      <button class="addbtn xbtn" data-dismiss="{c["id"]}" title="Dismiss {e(c["id"])} - moves to Archive"
              aria-label="Dismiss {e(c["id"])}">&#10005;</button>
    </div>
  </div>
  <div class="thread" style="display:none"></div>
</article>''' for c in claims)

    title = meta.get("title", slug)
    js = (JS.replace("__CLAIMS__", json.dumps([{"id": c["id"], "kind": c["kind"]} for c in claims]))
            .replace("__COMMENTS__", json.dumps(comments))
            .replace("__DISKMD__", json.dumps(disk_md))
            .replace("__SLUG__", json.dumps(slug))
            .replace("__RENDERID__", json.dumps(render_id))
            .replace("__AUTHOR__", json.dumps(author))
            .replace("__STATUSES__", json.dumps(STATUSES))
            .replace("__FOCUS__", json.dumps(focus))
            .replace("__TURNS__", json.dumps(turn))
            .replace("__TAGS__", json.dumps({c["id"]: c.get("tags", []) for c in claims}))
            .replace("__CONVDIR__", json.dumps(convdir or "")))

    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<!-- GENERATED by scripts/ledger/ledger.py from ledger.md + comments.md. Do not hand-edit:
     comment in the browser (writes comments.md), or edit ledger.md and re-render. -->
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} - claim ledger</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">
<style>{CSS}</style>
{conv_nav(convs or [], meta.get("project") or slug)}
{focus_banner(mine, meta, n_you, n_cl)}
<header class="top"><div class="wrap">
  <span class="eyebrow">{e(meta.get("project", ""))}{" &middot; " if meta.get("project") else ""}claim ledger &middot; {len(claims)} claims</span>
  <h1>{e(title)}</h1>
  <p class="lede">{md(meta.get("lede", "Every load-bearing statement from this conversation, one line each. "
      "Hover a claim and press + to comment. Wrong beats polite -- a bad assumption caught here "
      "costs a sentence; caught in code it costs a rebuild."))}</p>
  {source_line(meta)}
</div></header>

<div class="bar"><div class="wrap barin">
  {chips}
  <button class="chip" id="mine" aria-pressed="true" style="--k:var(--opn)">
    <i></i>Waiting on you<b>{n_you}</b></button>
  <button class="chip" id="unc" aria-pressed="false">No comment yet</button>

  <button class="chip" id="tcl" aria-pressed="false" style="--k:var(--accent)">
    <i></i>Back with Claude<b>{n_cl}</b></button>
  <button class="chip" id="vturns" aria-pressed="false">Conversation<b>{len(turns_list or [])}</b></button>
  <span class="spacer"></span>
  <span class="sync"><span class="dot" id="dot"></span><span id="stxt">&#8203;</span></span>
  <button class="btn" id="copy" disabled>Copy new comments</button>
</div></div>

<main class="wrap" id="claimsview"><div id="cardlist">{body}</div>
  <p class="empty" id="empty" style="display:none">Nothing matches that filter.</p>
  <details class="archive" id="archive" hidden>
    <summary>Archive <b id="arcn">0</b></summary>
    <p class="arcnote">Dismissed and parked claims. Out of the way, not deleted --
      comment on one and it comes back.</p>
    <div id="arcbody"></div>
  </details>
</main>
<section class="wrap" id="turnsview" hidden>{turns_html(turns_list or [], claims)}</section>

<div class="sendbar" id="sendbar"><div class="wrap sendin">
  <div class="sendtxt"><strong id="sendn">0 replies</strong>
    <span>waiting to go back to Claude. He only reads them when you next write in the chat.</span></div>
  <button class="btn" id="copy2" title="Copy the claims and your replies in full, for pasting anywhere">Copy</button>
  <button class="btn send" id="send">Send to Claude</button>
</div></div>

<footer><div class="wrap">
  Generated by <code>scripts/ledger/ledger.py</code> from <code>ledger.md</code>.
  Claims are Claude's file; <code>comments.md</code> is yours -- neither rewrites the other.
</div></footer>
<script>{js}</script>
</html>'''


# ------------------------------------------------------------------ paths
def repo_root(start):
    p = start.resolve()
    for c in [p, *p.parents]:
        if (c / ".git").exists():
            return c
    return p


def resolve(target):
    """Accept a full path to a conversation dir, or <prd>/<slug>, or <prd>."""
    p = pathlib.Path(target)
    if p.is_dir() and (p / "ledger.md").exists():
        return p
    parts = target.strip("/").split("/")
    if len(parts) > 1:
        return conv_dir(parts[0], parts[1])
    base = (PRDS / parts[0] / MID) if MID else (PRDS / parts[0])
    cands = sorted(d for d in base.glob("*") if (d / "ledger.md").exists()) if base.is_dir() else []
    if not cands:
        sys.exit(f"no conversation ledger under {base}")
    if len(cands) > 1:
        sys.exit("several conversations here -- name one:\n  " +
                 "\n  ".join(c.name for c in cands))
    return cands[0]


def serve(conv, author, port):
    """Localhost server so a comment is on disk before the composer closes.

    Exists because the browser-side alternatives both fail for this user:
    Firefox has no File System Access API at all, and a download lands in
    ~/Downloads, which the container cannot read. A loopback POST has neither
    problem and works inside the VS Code Simple Browser.
    """
    import http.server
    import socketserver

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            b = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path.split("?")[0] not in ("/", "/index.html"):
                return self._send(404, "not found", "text/plain")
            render(conv, author, served=True)          # pick up hand edits
            self._send(200, (conv / "index.html").read_text(), "text/html; charset=utf-8")

        def do_POST(self):
            if self.path.rstrip("/") != "/api/comment":
                return self._send(404, "not found", "text/plain")
            try:
                n = int(self.headers.get("Content-Length", 0))
                c = json.loads(self.rfile.read(n))
                for k in ("claim", "author", "at", "status", "text"):
                    if not isinstance(c.get(k), str) or not c[k]:
                        raise ValueError(f"missing field {k}")
                if c["status"] not in STATUSES:
                    raise ValueError(f"bad status {c['status']}")
                if not re.fullmatch(r"C\d+", c["claim"]):
                    raise ValueError("bad claim id")
                # Append only -- the browser never rewrites this file, so a
                # comment typed here cannot clobber one you wrote by hand.
                p = conv / "comments.md"
                prev = p.read_text() if p.exists() else ""
                sep = "" if not prev.strip() else ("\n" if prev.endswith("\n") else "\n\n")
                with p.open("a") as f:
                    f.write(f"{sep}### {c['claim']} {c['author']} {c['at']} {c['status']}\n"
                            f"{c['text'].strip()}\n")
                print(f"  {c['claim']} {c['status']}: {c['text'].splitlines()[0][:60]}")
                self._send(200, json.dumps({"ok": True}), "application/json")
            except Exception as err:
                self._send(400, json.dumps({"error": str(err)}), "application/json")

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), H) as srv:
        print(f"ledger: {conv.name}\n  http://localhost:{port}\n"
              f"  VS Code: Cmd+Shift+P -> 'Simple Browser: Show' -> that URL\n"
              f"  comments append to {conv / 'comments.md'}\nCtrl+C to stop.")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


def render(conv, author, served=False):
    lpath, cpath = conv / "ledger.md", conv / "comments.md"
    meta, claims = parse_ledger(lpath)
    comments = parse_comments(cpath)
    disk_md = cpath.read_text() if cpath.exists() else ""
    # Render id = the ledger+comments state this page was built from. The page
    # drops any browser buffer stamped with a different one, which is what stops
    # a comment already written to disk from being appended a second time.
    tpath = conv / "turns.md"
    sid = meta.get("session", "")
    jl = SESSION_DIR / f"{sid}.jsonl"
    sig = f"{jl.stat().st_size}" if sid and jl.exists() else ""
    stamp = str(abs(hash((lpath.read_text(), disk_md,
                          tpath.read_text() if tpath.exists() else "", sig))) % (10 ** 12))
    out = conv / "index.html"
    out.write_text(build(meta, claims, comments, conv.name, disk_md, author, stamp,
                         siblings(conv), read_turns(meta, conv), str(conv.resolve())))
    check_page(out)
    return out, claims, comments


def check_page(out):
    """Run the page's JavaScript in a shim DOM and complain if it is broken.

    Three regressions shipped in a row that were only visible by clicking --
    a deleted variable still referenced, a button shadowed by a CSS-class
    selector, and two functions deleted along with a block that still called
    them. Rendering without checking is what let each of them out.

    Never fatal: a broken harness must not stop a ledger from rendering.
    """
    import shutil
    import subprocess
    node = shutil.which("node")
    harness = pathlib.Path(__file__).with_name("test_page.js")
    if not node or not harness.exists():
        return
    try:
        r = subprocess.run([node, str(harness), str(out)],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return
    if r.returncode:
        print(f"  PAGE CHECK FAILED for {out.parent.name}:\n"
              + "\n".join("    " + l for l in (r.stdout + r.stderr).strip().splitlines()),
              file=sys.stderr)


def session_meta(sid):
    """Title and start date for a session, read from its transcript."""
    f = SESSION_DIR / f"{sid}.jsonl"
    if not f.exists():
        return None
    title, first_ts, first_prompt = None, None, None
    with f.open() as fh:
        for line in fh:
            if '"ai-title"' in line:
                try:
                    title = json.loads(line).get("aiTitle") or title
                except Exception:
                    pass
            elif first_ts is None and '"timestamp"' in line:
                try:
                    d = json.loads(line)
                    first_ts = (d.get("timestamp") or "")[:10] or None
                    if d.get("type") == "user" and isinstance(d.get("message"), dict):
                        c = d["message"].get("content")
                        if isinstance(c, str):
                            first_prompt = c
                except Exception:
                    pass
    return {"title": title or (first_prompt or sid)[:60],
            "date": first_ts or "0000-00-00"}


def known_sessions():
    """session id -> conversation dir, for every ledger already on disk."""
    out = {}
    if not PRDS.exists():
        return out
    for lg in PRDS.glob(GLOB):
        m = re.search(r"^session:\s*(\S+)", lg.read_text(), re.M)
        if m:
            out[m.group(1)] = lg.parent
    return out


def current_prd():
    """Default group for a new ledger: the current branch, if it names one.

    "Names one" used to mean `PRDS/<branch>` existed, which is only true in a
    repo that already files ledgers by PRD. Any other repo fell straight to
    `unfiled/`, so every conversation piled into one bucket. Now the branch
    counts if ANY configured group directory knows it -- `.claude/epics/<b>`,
    `.claude/prds/<b>` or `<b>.md` in either -- which is how a repo that
    organises work its own way gets sensibly-filed ledgers for free.
    """
    import subprocess
    try:
        b = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        b = ""
    # A branch may be `feature/x`; the group is one path segment.
    b = re.sub(r"[^A-Za-z0-9._-]+", "-", b.split("/")[-1]).strip("-")
    if b and b != MID:
        if (PRDS / b).is_dir():
            return b
        for g in GROUPS:
            d = ROOT / g
            if (d / b).is_dir() or (d / f"{b}.md").exists():
                return b
    # Never the MID segment: <group>/<mid>/<slug> would render mid/mid/slug.
    # The fallback fires whenever the branch names nothing, e.g. mid-switch.
    return "unfiled"


def ensure(sid, prd=None):
    """Create a ledger for this session if it has none. Idempotent."""
    have = known_sessions()
    if sid in have:
        return have[sid], False
    meta = session_meta(sid)
    if not meta:
        return None, False
    slug = re.sub(r"[^a-z0-9]+", "-", meta["title"].lower()).strip("-")[:48] or sid[:8]
    target = f"{prd or current_prd()}/{meta['date']}-{slug}"
    conv = conv_dir(target.split("/")[0], target.split("/", 1)[1])
    if (conv / "ledger.md").exists():
        return conv, False
    scaffold(target, sid, title=meta["title"])
    return conv, True


def scaffold(target, session=None, title=None):
    """Create a starter ledger so the panel has something to open.

    The empty state was a paragraph of instructions telling Angel to hand-write
    frontmatter -- which is exactly the kind of thing a command should do.
    """
    parts = target.strip("/").split("/")
    if len(parts) != 2:
        sys.exit("usage: ledger.py --new <prd>/<conversation-slug>")
    conv = conv_dir(parts[0], parts[1])
    lg = conv / "ledger.md"
    if lg.exists():
        sys.exit(f"already exists: {lg}")
    conv.mkdir(parents=True, exist_ok=True)
    sid = session or ""
    lg.write_text(
        "---\n"
        f"title: {title or parts[1].replace('-', ' ')}\n"
        f"project: {parts[0]}\n"
        + (f"session: {sid}\n" if sid else "")
        + "focus:\n"
        "focus_note:\n"
        "---\n\n"
        "### C1 decision\n"
        "Replace this with the first real claim.\n"
        "> The `> ` line is the reasoning, folded behind a disclosure in the panel.\n\n"
        "### C2 open\n"
        "An unanswered question shows up as waiting on you.\n"
        "> Kinds: decision | assumption | finding | open | risk.\n")
    print(f"created {lg}")
    return conv


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    if argv[1] == "--config":
        # The hooks and the VS Code extension must agree with this script about
        # where ledgers live. They ask, rather than each re-deriving it.
        import json as _j
        print(_j.dumps({"root": str(ROOT), "base": str(PRDS), "mid": MID,
                        "glob": GLOB, "groups": GROUPS,
                        "script": str(pathlib.Path(__file__).resolve()),
                        "sessions": str(SESSION_DIR)}, indent=2))
        return
    if argv[1] == "--where":
        # The extension scans the VS Code workspace folder; this script resolves
        # the git root. When those differ, ledger.py writes somewhere the panel
        # never looks and you get "No ledger.md in this workspace".
        print(f"workspace root : {ROOT}")
        print(f"looks for      : {layout_desc()}")
        print(f"transcripts    : {SESSION_DIR}  (exists: {SESSION_DIR.exists()})")
        found = sorted(PRDS.glob(GLOB)) if PRDS.exists() else []
        print(f"ledgers found  : {len(found)}")
        for f in found:
            print(f"   {f}")
        if not found:
            print("\nnothing yet -- create one with:")
            print(f"   {self_cmd()} --new demo/2026-09-11-smoke-test")
        print("\nIn VS Code, check this matches the folder you have open:")
        print(f"   the extension scans {PRDS}")
        print(f"   if your workspace is not {ROOT}, set LEDGER_ROOT to it.")
        return
    if argv[1] in ("--ensure", "--ensure-all"):
        sids = ([argv[2]] if argv[1] == "--ensure" and len(argv) > 2
                else [f.stem for f in sorted(SESSION_DIR.glob("*.jsonl"))]
                if argv[1] == "--ensure-all" else [])
        if not sids:
            sys.exit("usage: ledger.py --ensure <session-id> | --ensure-all")
        made = 0
        for sid in sids:
            conv, created = ensure(sid, argv[3] if len(argv) > 3 else None)
            if created:
                render(conv, "angel")
                print(f"created {conv}")
                made += 1
        print(f"{made} new ledger(s); {len(sids) - made} already had one")
        return
    if argv[1] == "--new":
        if len(argv) < 3:
            sys.exit("usage: ledger.py --new <prd>/<conversation-slug> [session-id]")
        conv = scaffold(argv[2], argv[3] if len(argv) > 3 else None)
        out, claims, _ = render(conv, "angel")
        print(f"wrote {out}  ({len(claims)} placeholder claims)")
        print("Open it with: Ledger: review")
        return
    args = [a for a in argv[1:] if not a.startswith("-")]
    flags = [a for a in argv[1:] if a.startswith("-")]
    if not args:
        # An unrecognised flag used to reach resolve(args[0]) and IndexError.
        sys.exit(f"unknown option {' '.join(flags)!r}\n\n"
                 "  ledger.py <prd>[/<slug>]        render\n"
                 "  ledger.py --new <prd>/<slug>    create a starter ledger\n"
                 "  ledger.py --where               show paths and find ledgers\n"
                 "  ledger.py --serve               serve it on localhost")
    conv = resolve(args[0])
    author = args[1] if len(args) > 1 else "angel"
    out, claims, comments = render(conv, author)
    kinds = ", ".join(f"{sum(1 for c in claims if c['kind']==k)} {k}"
                      for k in KINDS if any(c["kind"] == k for c in claims))
    print(f"wrote {out}\n  {len(claims)} claims ({kinds}), {len(comments)} comments on disk")

    port = 8787
    for f in flags:
        if f.startswith("--port="):
            port = int(f.split("=", 1)[1])
    if "--serve" in flags:
        serve(conv, author, port)
    else:
        print(f"  open: file://{out}\n"
              f"  or `--serve` to comment straight to disk (no browser permissions)")


if __name__ == "__main__":
    main(sys.argv)
