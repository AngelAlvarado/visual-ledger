#!/usr/bin/env node
/**
 * Exercise the generated page's JavaScript without a browser.
 *
 *   node scripts/ledger/test_page.js <index.html>
 *
 * Three regressions in a row shipped because the only way to run this code was
 * to click it: a `showDismissed` reference left behind by a deleted chip, an X
 * button shadowed by a `.addbtn` selector, and `flush()`/`serialize()` deleted
 * along with the File System Access block. All three were ReferenceErrors or
 * dead handlers -- exactly what a five-minute harness catches.
 *
 * This is not a browser. It is the smallest DOM that lets render() and the
 * click handlers run: enough to catch "throws on load", "throws on click" and
 * "filter does nothing", which is where the bugs actually were.
 */
const fs = require("fs");

function makeDom(html) {
  const listeners = [];
  const byId = {};
  const all = [];

  function el(tag, attrs = {}) {
    const node = {
      tagName: tag.toUpperCase(),
      children: [],
      parentElement: null,
      dataset: {},
      style: { setProperty() {}, display: "" },
      attributes: {},
      _classes: new Set(),
      textContent: "",
      innerHTML: "",
      hidden: false,
      disabled: false,
      value: "",
      classList: {
        add: (c) => node._classes.add(c),
        remove: (c) => node._classes.delete(c),
        contains: (c) => node._classes.has(c),
        toggle: (c, on) => (on === undefined
          ? (node._classes.has(c) ? node._classes.delete(c) : node._classes.add(c))
          : (on ? node._classes.add(c) : node._classes.delete(c))),
      },
      setAttribute: (k, v) => { node.attributes[k] = String(v); },
      getAttribute: (k) => node.attributes[k],
      appendChild: (c) => { c.parentElement = node; node.children.push(c); return c; },
      removeChild: (c) => { node.children = node.children.filter((x) => x !== c); },
      remove() { if (node.parentElement) node.parentElement.removeChild(node); },
      scrollIntoView() {},
      focus() {},
      querySelector: (sel) => all.find((n) => matches(n, sel) && isUnder(n, node)) || null,
      querySelectorAll: (sel) => all.filter((n) => matches(n, sel) && isUnder(n, node)),
      closest: (sel) => { let n = node; while (n) { if (matches(n, sel)) return n; n = n.parentElement; } return null; },
      addEventListener() {},
      previousElementSibling: null,
      firstChild: null,
    };
    Object.assign(node, attrs);
    all.push(node);
    return node;
  }

  const isUnder = (n, root) => { let p = n; while (p) { if (p === root) return true; p = p.parentElement; } return false; };

  function matches(node, sel) {
    // Enough selector support for what the page actually uses.
    return sel.split(",").some((part) => {
      part = part.trim();
      const m = /^([a-z]*)((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/i.exec(part);
      if (!m) return false;
      const [, tag, classes, attrs] = m;
      if (tag && node.tagName !== tag.toUpperCase()) return false;
      for (const c of classes.split(".").filter(Boolean)) if (!node._classes.has(c)) return false;
      for (const a of attrs.match(/\[[^\]]+\]/g) || []) {
        const kv = /\[([\w-]+)(?:=["']?([^"'\]]+)["']?)?\]/.exec(a);
        const key = kv[1];
        const camel = key.replace(/^data-/, "").replace(/-(\w)/g, (_, c) => c.toUpperCase());
        const have = key.startsWith("data-") ? node.dataset[camel] : node.attributes[key];
        if (have === undefined) return false;
        if (kv[2] !== undefined && String(have) !== kv[2]) return false;
      }
      return true;
    });
  }

  // Build nodes from the emitted markup -- ids, classes and data-* are all the
  // handlers look at, so a regex pass is sufficient and keeps this dependency-free.
  const VOID = ["br", "meta", "link", "input", "hr", "img", "i"];
  const tagRe = /<(\/?)(\w+)([^>]*?)(\/?)>/g;
  let m;
  const stack = [];
  const root = el("body");
  while ((m = tagRe.exec(html))) {
    const [, closing, tag, rawAttrs, selfClose] = m;
    if (VOID.includes(tag)) continue;
    if (closing) { stack.pop(); continue; }          // nesting matters: the page
    const node = el(tag);                            // queries within elements
    for (const a of rawAttrs.match(/([\w-]+)="([^"]*)"/g) || []) {
      const [, k, v] = /([\w-]+)="([^"]*)"/.exec(a);
      if (k === "id") { node.attributes.id = v; byId[v] = node; }
      else if (k === "class") v.split(/\s+/).filter(Boolean).forEach((c) => node._classes.add(c));
      else if (k.startsWith("data-")) node.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v;
      else node.attributes[k] = v;
    }
    (stack[stack.length - 1] || root).appendChild(node);
    if (!selfClose) stack.push(node);
  }

  const doc = {
    body: root,
    getElementById: (id) => byId[id] || null,
    querySelector: (s) => all.find((n) => matches(n, s)) || null,
    querySelectorAll: (s) => all.filter((n) => matches(n, s)),
    createElement: (t) => el(t),
    addEventListener: (type, fn) => listeners.push({ type, fn }),
  };
  return { doc, byId, all, listeners, matches };
}

function main() {
  const file = process.argv[2];
  if (!file) { console.error("usage: test_page.js <index.html>"); process.exit(2); }
  const html = fs.readFileSync(file, "utf8");
  const script = (html.match(/<script>([\s\S]*)<\/script>/) || [])[1];
  if (!script) { console.error("no <script> in page"); process.exit(2); }

  const { doc, byId, all, listeners } = makeDom(html);
  const store = {};
  const sandbox = {
    document: doc,
    // The page listens on `window` for the extension's dictation frames, so
    // the shim has to accept listeners and be able to deliver one -- a stub
    // that only exists would let a broken handler pass unnoticed.
    window: {
      showDirectoryPicker: undefined,
      _msg: [],
      addEventListener(type, fn) { if (type === "message") this._msg.push(fn); },
      removeEventListener(type, fn) {
        if (type === "message") this._msg = this._msg.filter((f) => f !== fn);
      },
      postMessage(data) { this._msg.forEach((fn) => fn({ data })); },
    },
    location: { protocol: "vscode-webview:", href: "" },
    navigator: { clipboard: { writeText: async () => {} } },
    localStorage: {
      length: 0, key: () => null,
      getItem: (k) => store[k] ?? null,
      setItem: (k, v) => { store[k] = v; },
      removeItem: (k) => { delete store[k]; },
    },
    acquireVsCodeApi: () => ({ postMessage() {}, setState() {} }),
    // The page's own functions are local to the new Function() scope below,
    // so anything worth testing directly has to be handed out deliberately.
    __export: {},
    setTimeout: () => 0, clearTimeout: () => {},
    console,
  };

  const fail = [];
  try {
    const epilogue = ";try{__export.commitSpeech=commitSpeech;__export.DICT=DICT;}"
                   + "catch(e){/* page without dictation */}";
    new Function(...Object.keys(sandbox), script + epilogue)(...Object.values(sandbox));
  } catch (e) {
    console.error(`LOAD FAILED: ${e.name}: ${e.message}`);
    process.exit(1);
  }

  // WHICH cards are hidden, not how many. Counting was a false-failure
  // generator: clicking a kind chip also clears "Waiting on you", so the
  // hidden set can swap entirely while its size stays put -- which the page
  // does correctly and the harness called "changed nothing" on every render.
  const hiddenIds = () => all
    .filter((n) => n._classes.has("claim") && n._classes.has("hide"))
    .map((n) => n.attributes.id).sort().join(",");
  const cards = all.filter((n) => n._classes.has("claim")).length;
  const click = (node) => {
    const ev = { target: node, preventDefault() {} };
    listeners.filter((l) => l.type === "click").forEach((l) => l.fn(ev));
  };

  console.log(`  loaded, ${cards} cards rendered`);

  // Every top chip must change what is shown, or it is not wired up.
  for (const chip of doc.querySelectorAll(".chip[data-kind]")) {
    const before = hiddenIds();
    try { chip.onclick && chip.onclick(); } catch (e) { fail.push(`chip ${chip.dataset.kind}: ${e.message}`); continue; }
    if (hiddenIds() === before) fail.push(`chip ${chip.dataset.kind} changed nothing`);
    chip.onclick && chip.onclick();          // toggle back off
  }

  // The X must be reachable without the comment handler swallowing it.
  const x = doc.querySelector("[data-dismiss]");
  if (!x) fail.push("no dismiss button rendered");
  else { try { click(x); } catch (e) { fail.push(`dismiss click: ${e.message}`); } }

  // Tag chips filter too.
  const tag = doc.querySelector(".tag[data-tag]");
  if (tag) {
    const before = hiddenIds();
    try { click(tag); } catch (e) { fail.push(`tag click: ${e.message}`); }
    if (hiddenIds() === before) fail.push("tag chip changed nothing");
  }

  // An archived card must never be counted as waiting on you -- dismissing a
  // focused claim used to leave the chip claiming it still needed attention.
  const arcBody = doc.getElementById("arcbody");
  const inArchive = arcBody ? arcBody.children.filter((n) => n._classes.has("claim")) : [];
  const mineCount = parseInt((doc.querySelector("#mine b") || {}).textContent || "0", 10);
  const listed = all.filter((n) => n._classes.has("claim") && !n._classes.has("hide")
                                   && n.parentElement && n.parentElement.attributes.id === "cardlist");
  if (inArchive.some((n) => n._classes.has("focus") && mineCount > listed.length)) {
    fail.push("archived card still counted in 'Waiting on you'");
  }
  if (arcBody && inArchive.some((n) => n._classes.has("hide"))) {
    fail.push("archived card is hidden inside the Archive");
  }

  // The cumulative-vs-append bug, tested directly against the page's own
  // commitSpeech. The live backend restates the WHOLE transcript in every
  // frame; appending that instead of replacing duplicates it quadratically,
  // and it is invisible until someone actually dictates. Driving this through
  // a real composer would need an HTML parser in this shim, so call the
  // function the vm context already exposes.
  const X = sandbox.__export;
  if (typeof X.commitSpeech === "function" && X.DICT) {
    const ta = { value: "", selectionStart: 0, selectionEnd: 0,
                 classList: { toggle() {} } };
    X.DICT.ta = ta; X.DICT.base = "typed "; X.DICT.said = "";
    X.commitSpeech("one two", true);
    X.commitSpeech("one two three", true);          // cumulative: replaces
    if (ta.value !== "typed one two three") {
      fail.push(`cumulative commit gave ${JSON.stringify(ta.value)}, expected "typed one two three"`);
    }
    ta.value = ""; X.DICT.base = ""; X.DICT.said = "";
    X.commitSpeech("first", false);
    X.commitSpeech("second", false);                // incremental: appends
    if (ta.value !== "first second") {
      fail.push(`incremental commit gave ${JSON.stringify(ta.value)}, expected "first second"`);
    }
    X.DICT.ta = null;
  }

  // Dictation: the page must survive a frame from the extension without
  // throwing. A handler that references a deleted element is invisible until
  // someone actually dictates, which is exactly the class of regression this
  // harness exists to catch.
  try {
    sandbox.window.postMessage({ type: "dictate:state", channel: "x", state: "listening" });
    sandbox.window.postMessage({ type: "dictate:text", channel: "x", text: "hello", done: false });
    sandbox.window.postMessage({ type: "dictate:text", channel: "x", text: "hello", done: true });
    sandbox.window.postMessage({ type: "dictate:error", channel: "x", error: "nope" });
  } catch (e) {
    fail.push(`dictation frame threw: ${e.message}`);
  }
  if (!sandbox.window._msg.length) fail.push("page registered no message listener");

  if (fail.length) {
    console.error("FAIL");
    fail.forEach((f) => console.error("  - " + f));
    process.exit(1);
  }
  console.log("  chips filter, dismiss fires, tags filter, dictation frames -- OK");
}

main();
