/** Local dictation: faster-whisper on this machine, nothing over the wire.
 *
 *  Two processes. `rec` (SoX) captures 16 kHz mono PCM; scripts/stt_local.py
 *  holds the model and turns that into JSON lines. This wires them together
 *  and speaks the panel's backend contract:
 *
 *      start(channel, { onText, onState, onError }, opts) -> stop()
 *
 *  Output is CUMULATIVE. The Python side re-decodes the whole utterance each
 *  interval rather than appending slices, so earlier words get corrected once
 *  the sentence around them exists -- which is the behaviour Angel wanted and
 *  what the hosted backend also produced. Frames therefore carry
 *  cumulative=true, and the page replaces rather than appends.
 *
 *  Compared with the anthropic backend: no token, nothing that expires, no
 *  undocumented endpoint, and it keeps working on a plane. It costs model
 *  weights on disk and some CPU while you speak.
 */
const { spawn } = require("child_process");
const path = require("path");

/** Where stt_local.py sits, given where ledger.py was found. They ship
 *  together, so one locates the other. */
function helperFor(renderPath) {
  return path.join(path.dirname(renderPath), "stt_local.py");
}

/** How to launch the helper. `uv run` reads the PEP 723 block at the top of
 *  stt_local.py and fetches faster-whisper into its own cache, so there is
 *  nothing to install by hand and nothing lands in a system Python. Falling
 *  back to a plain interpreter keeps it working where uv is absent. */
function launcher(opts, helper) {
  if ((opts.runner || "uv") === "uv") {
    return { cmd: opts.uv || "uv", args: ["run", "--script", helper] };
  }
  return { cmd: opts.python || "python3", args: [helper] };
}

function localBackend(channel, cb, opts) {
  const helper = helperFor(opts.scriptPath || "");
  const { cmd, args: launchArgs } = launcher(opts, helper);
  let stopped = false;
  let rec = null;
  let stt = null;
  let last = "";

  const fail = (msg) => {
    if (stopped) return;
    stopped = true;
    cleanup();
    cb.onError(msg);
  };

  function cleanup() {
    for (const p of [rec, stt]) {
      try { if (p && !p.killed) p.kill("SIGTERM"); } catch (e) { /* gone */ }
    }
    rec = stt = null;
  }

  cb.onState("connecting");

  // The model loads on first use and a cold start is seconds, not
  // milliseconds -- "connecting" stays up until the helper says ready, so
  // the pulsing mic is not claiming to hear anything yet.
  try {
    stt = spawn(cmd, [
      ...launchArgs,
      "--model", opts.model || "small.en",
      "--device", opts.device || "cpu",
      "--compute-type", opts.computeType || "int8",
      "--language", opts.language || "en",
    ], { stdio: ["pipe", "pipe", "pipe"] });
  } catch (e) {
    return fail(`Could not start ${cmd}: ${e.message}`), cleanup;
  }

  stt.on("error", (e) => fail(
    e.code === "ENOENT"
      ? `${cmd} not found. Install uv, or set claudeLedger.dictation.runner `
        + `to "python" and claudeLedger.dictation.python to an interpreter `
        + `that has faster-whisper.`
      : String(e.message)));

  let out = "";
  stt.stdout.on("data", (d) => {
    out += String(d);
    let nl;
    while ((nl = out.indexOf("\n")) >= 0) {
      const line = out.slice(0, nl).trim();
      out = out.slice(nl + 1);
      if (!line) continue;
      let m;
      try { m = JSON.parse(line); } catch (e) { continue; }
      if (m.type === "ready") {
        startRecorder();
      } else if (m.type === "partial" && m.text) {
        last = m.text;
        cb.onText(m.text, false, true);
      } else if (m.type === "final") {
        if (m.text) { last = m.text; cb.onText(m.text, true, true); }
      } else if (m.type === "error") {
        fail(m.message || "Local transcription failed.");
      }
    }
  });

  // The helper's own diagnostics are the only clue when a model fails to
  // load, so keep the tail rather than discarding it.
  let errTail = "";
  stt.stderr.on("data", (d) => { errTail = (errTail + String(d)).slice(-500); });

  stt.on("close", (code) => {
    if (stopped) return;
    if (code) fail(`Transcriber exited ${code}. ${errTail.trim()}`.trim());
  });

  function startRecorder() {
    if (stopped || rec) return;
    try {
      rec = spawn("rec", ["-q", "-c", "1", "-r", "16000", "-b", "16",
                          "-e", "signed-integer", "-t", "raw", "-"]);
    } catch (e) { return fail(`Could not start rec: ${e.message}`); }
    rec.on("error", (e) => fail(
      e.code === "ENOENT"
        ? "`rec` (SoX) is not installed. Install sox."
        : String(e.message)));
    rec.stdout.on("data", (chunk) => {
      if (!stopped && stt && stt.stdin.writable) stt.stdin.write(chunk);
    });
    cb.onState("listening");
  }

  return function stop() {
    if (stopped) return;
    stopped = true;
    // Close the recorder first and let the helper drain: ending stdin is
    // what makes it emit the final pass over everything heard, which is the
    // only commit point there is. Killing both at once would throw away the
    // last utterance.
    try { if (rec) rec.kill("SIGTERM"); } catch (e) { /* gone */ }
    rec = null;
    try { if (stt && stt.stdin.writable) stt.stdin.end(); } catch (e) { /* gone */ }
    const grace = setTimeout(() => { cleanup(); }, 4000);
    if (stt) stt.on("close", () => clearTimeout(grace));
    // Whatever the helper managed, the page keeps what it last showed.
    if (last) { try { cb.onText(last, true, true); } catch (e) { /* gone */ } }
  };
}

module.exports = { localBackend, helperFor };
