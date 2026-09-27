/**
 * Claim ledger -- review comments on ledger.md, native VS Code Comments API.
 *
 * Why an extension rather than the served HTML page: Simple Browser still hands
 * off to the host browser inside a devcontainer, so "review the ledger" meant a
 * context switch to Firefox. This puts the threads in the editor gutter, where
 * the file already is.
 *
 * Why a SEPARATE extension rather than patching anthropic.claude-code: that one
 * is proprietary and auto-updates -- a sed patch has to be re-applied after
 * every update (see the voice-dictation patch in the Dockerfile). This owns its
 * own files and survives updates.
 *
 * Contract, identical to ledger.py so both front ends interoperate:
 *   ledger.md    ### C14 assumption      <- Claude appends, owns the file
 *   comments.md  ### C14 angel <iso> wrong  <- appended here, never rewritten
 */
const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const { execFileSync: _execFileSync } = require("child_process");

/** Run ledger.py, always telling it which project it is working on.
 *
 *  ledger.py resolves the project from $LEDGER_ROOT, then $CLAUDE_PROJECT_DIR,
 *  then the git root above its cwd. Claude Code sets CLAUDE_PROJECT_DIR for
 *  hooks; nothing sets it for us, and the extension host's cwd is not the
 *  workspace -- so without this the script resolved to wherever VS Code
 *  happened to be started and reported no ledgers at all.
 *
 *  The extension is the one component that reliably knows the workspace, so
 *  it is the one that has to say. */
function execFileSync(cmd, args, opts) {
  const root = workspaceRoot();
  return _execFileSync(cmd, args, {
    cwd: root,
    ...opts,
    env: { ...process.env, LEDGER_ROOT: process.env.LEDGER_ROOT || root },
  });
}

/** Resolved from the open workspace, not hardcoded, so the extension works in
 *  any container without editing a path. */
function workspaceRoot() {
  const f = (vscode.workspace.workspaceFolders || [])[0];
  return f ? f.uri.fsPath : process.env.LEDGER_ROOT || process.cwd();
}

/** Where ledger.py lives. It has moved twice already (scripts/ledger ->
 *  .claude/skills/ledger/scripts -> a plugin), so look rather than assume.
 *  Relative to the workspace, for a copy that lives inside the repo. */
const SCRIPT_CANDIDATES = [
  ["scripts", "ledger.py"],
  [".claude", "skills", "ledger", "scripts", "ledger.py"],
  ["scripts", "ledger", "ledger.py"],
];

/** Installed as a plugin, the code is NOT in the workspace -- Claude Code
 *  fetches it into ~/.claude/plugins/. The extension is a separate install
 *  with no knowledge of which plugin dir won, so scan for the one that
 *  carries our script rather than hardcoding a name that may be versioned. */
function pluginCandidates() {
  const base = path.join(require("os").homedir(), ".claude", "plugins");
  const out = [];
  const walk = (dir, depth) => {
    if (depth > 3) return;
    let entries = [];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (e) { return; }
    for (const d of entries.filter((d) => d.isDirectory())) {
      const here = path.join(dir, d.name);
      const hit = path.join(here, "scripts", "ledger.py");
      if (fs.existsSync(hit)) out.push(hit);
      else walk(here, depth + 1);
    }
  };
  walk(base, 0);
  return out;
}

function RENDER() {
  const cfg = vscode.workspace.getConfiguration("claudeLedger").get("scriptPath");
  if (cfg && fs.existsSync(cfg)) return cfg;
  if (process.env.LEDGER_PY && fs.existsSync(process.env.LEDGER_PY)) return process.env.LEDGER_PY;
  const root = workspaceRoot();
  for (const parts of SCRIPT_CANDIDATES) {
    const p = path.join(root, ...parts);
    if (fs.existsSync(p)) return p;
  }
  const plugin = pluginCandidates()[0];
  if (plugin) return plugin;
  return path.join(root, ...SCRIPT_CANDIDATES[0]);
}

/** Layout, straight from ledger.py so the panel and the renderer can never
 *  disagree about where ledgers are. Cached: this shells out. */
let _layout = null;
function layout() {
  if (_layout) return _layout;
  const fallback = { base: path.join(workspaceRoot(), ".claude", "ledgers"), mid: "" };
  try {
    const out = execFileSync("python3", [RENDER(), "--config"],
                                { encoding: "utf8", timeout: 5000 });
    const d = JSON.parse(out);
    _layout = { base: d.base, mid: d.mid || "" };
  } catch (e) {
    _layout = fallback;
  }
  return _layout;
}

/** Ledgers live at a known depth, so read the directory levels directly.
 *  findFiles("**\/conversations/...") walked the whole workspace -- the repo
 *  plus every mounted sibling, node_modules and .venv -- and took seconds. */
function findLedgers() {
  const out = [];
  const { base, mid } = layout();
  {
    let prds = [];
    try { prds = fs.readdirSync(base, { withFileTypes: true }); } catch (e) { return out; }
    for (const prd of prds.filter((d) => d.isDirectory())) {
      const convs = mid ? path.join(base, prd.name, mid) : path.join(base, prd.name);
      let slugs = [];
      try { slugs = fs.readdirSync(convs, { withFileTypes: true }); } catch (e) { continue; }
      for (const slug of slugs.filter((d) => d.isDirectory())) {
        const dir = path.join(convs, slug.name);
        const lg = path.join(dir, "ledger.md");
        try { out.push({ prd: prd.name, slug: slug.name, dir, at: fs.statSync(lg).mtimeMs }); }
        catch (e) { /* no ledger.md here */ }
      }
    }
  }
  return out.sort((a, b) => b.at - a.at);   // most recently touched first
}

async function pickLedger(placeHolder) {
  const found = findLedgers();
  if (!found.length) { vscode.window.showWarningMessage("No ledger.md in this workspace."); return null; }
  if (found.length === 1) return found[0].dir;
  const pick = await vscode.window.showQuickPick(
    found.map((f) => ({ label: f.slug, description: f.prd, dir: f.dir })), { placeHolder });
  return pick ? pick.dir : null;
}

const CLAIM = /^###\s+(C\d+)\s+(\w+)\s*$/;
const CMT = /^###\s+(C\d+)\s+(\S+)\s+(\S+)\s+(\w+)\s*$/;
const AUTHOR = process.env.LEDGER_AUTHOR || "angel";
const LABEL = { wrong: "Wrong", ok: "Confirmed", note: "Note", answered: "Answered",
                resolved: "Resolved" };

const isLedger = (doc) => doc && path.basename(doc.uri.fsPath) === "ledger.md";
const commentsPath = (doc) => path.join(path.dirname(doc.uri.fsPath), "comments.md");

/** Claim id -> zero-based line of its `### C<n> <kind>` header. */
function claimLines(doc) {
  const out = new Map();
  for (let i = 0; i < doc.lineCount; i++) {
    const m = CLAIM.exec(doc.lineAt(i).text);
    if (m) out.set(m[1], { line: i, kind: m[2].toLowerCase() });
  }
  return out;
}

function readComments(file) {
  if (!fs.existsSync(file)) return [];
  const out = [];
  let cur = null;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const m = CMT.exec(line);
    if (m) {
      cur = { claim: m[1], author: m[2], at: m[3], status: m[4].toLowerCase(), body: [] };
      out.push(cur);
    } else if (cur) cur.body.push(line);
  }
  return out.map((c) => ({ ...c, text: c.body.join("\n").trim() }));
}

/** Append-only: a comment written here can never clobber one written by hand
 *  or by the served page. Same rule ledger.py's POST handler follows. */
function appendComment(file, c) {
  const prev = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
  const sep = !prev.trim() ? "" : prev.endsWith("\n") ? "\n" : "\n\n";
  fs.appendFileSync(file, `${sep}### ${c.claim} ${c.author} ${c.at} ${c.status}\n${c.text}\n`);
}

const stamp = () => new Date().toISOString().slice(0, 19);

/** Has patch-claude-extension.py been run? Cached: the bundle is ~3MB and this
 *  is only re-checked when the answer might have changed (a reload). */
let _patched = null;
function claudePatched() {
  if (_patched !== null) return _patched;
  _patched = false;
  const dir = path.join(require("os").homedir(), ".vscode-server", "extensions");
  try {
    for (const d of fs.readdirSync(dir)) {
      if (!d.startsWith("anthropic.claude-code-")) continue;
      const f = path.join(dir, d, "extension.js");
      if (fs.existsSync(f) && fs.readFileSync(f, "utf8").includes("/*ledger-patch*/")) {
        _patched = true;
        break;
      }
    }
  } catch (e) { /* no extensions dir; leave false */ }
  return _patched;
}

/** Render with ledger.py, then show index.html in a webview panel.
 *  Why a webview and not the served page: Simple Browser hands off to the host
 *  browser in a devcontainer, which is the context switch we are removing.
 *  Why not the markdown gutter: the page carries filters, kind colours and
 *  folded detail that markdown cannot. */
function reviewPanel(context, conv, panels) {
  // One panel per PRD, not per conversation: the switcher moves between the
  // sibling ledgers inside it, so a project with five sessions is one tab.
  const key = path.dirname(conv);
  if (panels.has(key)) {
    const p = panels.get(key);
    p.current = conv; p.title = `Ledger: ${path.basename(conv)}`;
    p.reveal(); p.repaint();
    return p;
  }

  const panel = vscode.window.createWebviewPanel(
    "claudeLedger", `Ledger: ${path.basename(conv)}`,
    vscode.ViewColumn.Beside, { enableScripts: true, retainContextWhenHidden: true });
  return wirePanel(context, panel, conv, panels);
}

/** Attach behaviour to a panel -- newly created, or restored by VS Code after a
 *  window reload. Without this second path a reloaded window brought the tab
 *  back with no html and no listeners: a blank black panel. */
function wirePanel(context, panel, conv, panels) {
  const key = path.dirname(conv);
  panel.current = conv;
  panels.set(key, panel);
  panel.onDidDispose(() => panels.delete(key));

  // Repainting replaces webview.html, which destroys any open composer and the
  // scroll position. So only repaint when the underlying files actually differ
  // -- switching to the Claude tab and back must be a no-op.
  // Claude Code names its project dir after the workspace path: /app -> -app.
  const SESSIONS = path.join(require("os").homedir(), ".claude", "projects",
                             workspaceRoot().replace(/\//g, "-"));
  const sessionOf = (conv) => {
    try {
      const m = /^session:\s*(\S+)/m.exec(fs.readFileSync(path.join(conv, "ledger.md"), "utf8"));
      return m ? m[1] : null;
    } catch (e) { return null; }
  };
  const signature = (conv) => {
    const parts = ["ledger.md", "comments.md", "turns.md"].map((f) => {
      try { const s = fs.statSync(path.join(conv, f)); return `${f}:${s.mtimeMs}:${s.size}`; }
      catch (e) { return `${f}:none`; }
    });
    const sid = sessionOf(conv);
    if (sid) {
      try { parts.push(`jsonl:${fs.statSync(path.join(SESSIONS, sid + ".jsonl")).size}`); }
      catch (e) { /* transcript not in this container */ }
    }
    return parts.join("|");
  };
  let painted = null;

  const paint = (force) => {
    const conv = panel.current;
    const sig = conv + "||" + signature(conv);
    if (!force && sig === painted) return;
    painted = sig;
    try { execFileSync("python3", [RENDER(), conv], { encoding: "utf8" }); }
    catch (err) {
      panel.webview.html = `<body style="font-family:sans-serif;padding:24px">
        <h2>ledger.py failed</h2><pre>${String(err.stderr || err.message)
          .replace(/[<&]/g, (c) => ({ "<": "&lt;", "&": "&amp;" }[c]))}</pre></body>`;
      return;
    }
    let html = fs.readFileSync(path.join(conv, "index.html"), "utf8");
    // Webviews have no network: strip the Google Fonts links rather than let
    // them hang, and let the page fall back to the editor's own stack.
    html = html.replace(/<link rel="(preconnect|stylesheet)"[^>]*>/g, "");
    panel.webview.html = html;
  };
  panel.repaint = () => paint(true);

  panel.webview.onDidReceiveMessage((msg) => {
    if (msg && msg.type === "switch" && msg.dir) {
      panel.current = msg.dir;
      panel.title = `Ledger: ${path.basename(msg.dir)}`;
      paint();
    } else if (msg && msg.type === "comment" && msg.comment) {
      appendComment(path.join(panel.current, "comments.md"), msg.comment);
      painted = panel.current + "||" + signature(panel.current);
      vscode.workspace.textDocuments
        .filter((d) => path.dirname(d.uri.fsPath) === panel.current)
        .forEach((d) => vscode.commands.executeCommand("claudeLedger.reload"));
    } else if (msg && msg.type === "copy" && msg.text) {
      // Explicitly the manual route -- clipboard only, no command, no surprise.
      vscode.env.clipboard.writeText(msg.text).then(() =>
        vscode.window.showInformationMessage("Ledger: copied to clipboard."));
    } else if (msg && msg.type === "send" && msg.text) {
      // The Claude extension contributes no command that submits a prompt --
      // focus, insertAtMention and newConversation are the whole surface. So
      // the closest honest thing is: put it on the clipboard and focus the
      // input. One paste, rather than remembering what to type.
      // Only call focus when the patch is actually applied. Unpatched, that
      // command ignores the argument and focuses with the (usually empty)
      // editor selection -- so the button appeared to do nothing at all.
      // Better to copy and say so than to look broken.
      vscode.env.clipboard.writeText(msg.text).then(() => {
        if (!claudePatched()) {
          vscode.window.showInformationMessage(
            "Ledger: copied - paste into Claude. Run "
            + "scripts/ledger/patch-claude-extension.py to have Send fill the input directly.");
          return;
        }
        vscode.commands.executeCommand("claude-vscode.focus", msg.text).then(
          () => vscode.window.showInformationMessage(
            "Ledger: text is in the Claude input - press Enter. (Also copied.)"),
          () => vscode.window.showInformationMessage(
            "Ledger: comments copied - paste into Claude."));
      });
    } else if (msg && msg.type === "open" && msg.path) {
      vscode.window.showTextDocument(vscode.Uri.file(msg.path));
    }
  }, null, context.subscriptions);

  // Repaint when the tab is re-shown: ledger.md may have changed underneath it,
  // and a stale panel silently showing old claims is worse than a slow one.
  panel.onDidChangeViewState((ev) => { if (ev.webviewPanel.visible) paint(false); },
                             null, context.subscriptions);
  // Auto-refresh: Claude writes ledger.md while you are looking at the panel,
  // so the panel follows the file instead of waiting to be re-run. index.html
  // is deliberately NOT watched -- paint() writes it, which would loop.
  const watcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(path.dirname(conv), "*/{ledger.md,comments.md,turns.md}"));
  let pending = null;
  const bump = (uri) => {
    if (path.dirname(uri.fsPath) !== panel.current) return;   // sibling: counts only
    clearTimeout(pending);
    pending = setTimeout(() => paint(false), 150);   // debounce: a save fires twice
  };
  watcher.onDidChange(bump); watcher.onDidCreate(bump);
  context.subscriptions.push(watcher);
  panel.onDidDispose(() => watcher.dispose());

  paint(true);
  return panel;
}

function activate(context) {
  const panels = new Map();
  const ctrl = vscode.comments.createCommentController("claude-ledger", "Claim ledger");
  context.subscriptions.push(ctrl);

  // The "+" gutter affordance appears only on claim headers, so a comment can
  // never land somewhere that has no stable id to anchor to.
  ctrl.commentingRangeProvider = {
    provideCommentingRanges(doc) {
      if (!isLedger(doc)) return [];
      // Whole document rather than just the header lines: an empty Range on a
      // single line renders no gutter affordance, and demanding you hit the
      // exact header line is a bad target anyway. claimFor() snaps a comment
      // to the claim it sits inside.
      return [new vscode.Range(0, 0, Math.max(doc.lineCount - 1, 0), 0)];
    },
  };

  /** doc path -> threads, so a refresh disposes exactly what it created. */
  const threads = new Map();

  function refresh(doc) {
    if (!isLedger(doc)) return;
    const key = doc.uri.fsPath;
    (threads.get(key) || []).forEach((t) => t.dispose());
    threads.set(key, []);

    const claims = claimLines(doc);
    const byClaim = new Map();
    for (const c of readComments(commentsPath(doc))) {
      if (!claims.has(c.claim)) continue;   // claim deleted; comment stays on disk
      if (!byClaim.has(c.claim)) byClaim.set(c.claim, []);
      byClaim.get(c.claim).push(c);
    }

    for (const [id, list] of byClaim) {
      const { line } = claims.get(id);
      const thread = ctrl.createCommentThread(
        doc.uri, new vscode.Range(line, 0, line, 0),
        list.map((c) => ({
          body: new vscode.MarkdownString(c.text),
          mode: vscode.CommentMode.Preview,
          author: { name: c.author },
          label: LABEL[c.status] || c.status,
        })));
      thread.label = `${id} - ${list.length} comment${list.length > 1 ? "s" : ""}`;
      // An unaddressed "wrong" is the one thing worth expanding on open.
      const open = list.some((c) => c.status === "wrong") &&
                   !list.some((c) => c.status === "resolved");
      thread.collapsibleState = open
        ? vscode.CommentThreadCollapsibleState.Expanded
        : vscode.CommentThreadCollapsibleState.Collapsed;
      threads.get(key).push(thread);
    }
  }

  function claimFor(doc, line) {
    let best = null;
    for (const [id, c] of claimLines(doc)) if (c.line <= line) best = id;
    return best;   // null only above the first claim, e.g. in the frontmatter
  }

  function submit(reply, status, fallback) {
    const thread = reply.thread || reply;
    const doc = vscode.workspace.textDocuments.find(
      (d) => d.uri.fsPath === thread.uri.fsPath);
    if (!doc) return;
    const id = claimFor(doc, thread.range.start.line);
    if (!id) {
      vscode.window.showWarningMessage("That is above the first claim - comment on or below a ### C<n> line.");
      return;
    }
    const text = (reply.text || "").trim() || fallback;
    appendComment(commentsPath(doc), { claim: id, author: AUTHOR, at: stamp(), status, text });
    refresh(doc);
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("claudeLedger.markWrong", (r) => submit(r, "wrong")),
    vscode.commands.registerCommand("claudeLedger.addNote", (r) => submit(r, "note")),
    vscode.commands.registerCommand("claudeLedger.confirm", (t) => submit(t, "ok", "Confirmed.")),
    vscode.commands.registerCommand("claudeLedger.reload", () => {
      vscode.workspace.textDocuments.forEach(refresh);
      vscode.window.showInformationMessage("Ledger comments reloaded.");
    }),
    vscode.commands.registerCommand("claudeLedger.review", async (arg) => {
      let conv = arg && arg.fsPath ? path.dirname(arg.fsPath) : null;
      const active = vscode.window.activeTextEditor;
      if (!conv && active && isLedger(active.document)) conv = path.dirname(active.document.uri.fsPath);
      if (!conv) conv = await pickLedger("Review which ledger?");
      if (!conv) return;
      reviewPanel(context, conv, panels);
    }),
    vscode.commands.registerCommand("claudeLedger.open", async () => {
      const dir = await pickLedger("Open a claim ledger");
      if (dir) vscode.window.showTextDocument(
        await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(dir, "ledger.md"))));
    }),
    vscode.workspace.onDidOpenTextDocument(refresh),
    vscode.workspace.onDidSaveTextDocument(refresh),
    vscode.window.onDidChangeActiveTextEditor((e) => e && refresh(e.document)),
  );

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  status.command = "claudeLedger.review";
  context.subscriptions.push(status);
  function updateStatus(editor) {
    const doc = editor && editor.document;
    if (!isLedger(doc)) { status.hide(); return; }
    const n = readComments(commentsPath(doc)).length;
    status.text = `$(comment-discussion) Ledger: ${claimLines(doc).size} claims, ${n} comments`;
    status.tooltip = "Open the rich review panel";
    status.show();
  }
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(updateStatus),
    vscode.workspace.onDidSaveTextDocument(() => updateStatus(vscode.window.activeTextEditor)));
  updateStatus(vscode.window.activeTextEditor);

  // VS Code restores webview tabs after a window reload, but only rebuilds the
  // content if a serializer is registered. Without this the panel came back as
  // a blank black tab.
  if (vscode.window.registerWebviewPanelSerializer) {
    context.subscriptions.push(vscode.window.registerWebviewPanelSerializer("claudeLedger", {
      async deserializeWebviewPanel(panel, state) {
        const conv = state && state.conv;
        if (!conv || !fs.existsSync(path.join(conv, "ledger.md"))) {
          // The ledger moved or was deleted while the window was closed.
          panel.webview.html = `<body style="font-family:sans-serif;padding:24px;opacity:.7">
            <p>This ledger is no longer on disk.</p>
            <p>Run <b>Ledger: review</b> to pick another.</p></body>`;
          return;
        }
        panel.webview.options = { enableScripts: true };
        wirePanel(context, panel, conv, panels);
      },
    }));
  }

  vscode.workspace.textDocuments.forEach(refresh);
  console.log("claude-ledger activated");
}

function deactivate() {}
module.exports = { activate, deactivate };
