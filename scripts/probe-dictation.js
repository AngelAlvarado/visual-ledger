#!/usr/bin/env node
/** Probe the speech socket without VS Code in the way.
 *
 *   node scripts/probe-dictation.js <token-file> [seconds] [language]
 *
 * Opens the socket, streams microphone audio, and prints every frame that
 * comes back. The point is to answer the three UNKNOWNs in
 * dictation-anthropic.js -- whether a config frame is required on open,
 * whether a keepalive is needed, and whether the client-platform header
 * matters -- by watching what actually happens rather than reasoning about it.
 *
 * It prints frames and socket events, never the token.
 *
 * Reading the outcome:
 *
 *   closes immediately, 401/403      the token is wrong, expired, or this
 *                                    endpoint wants something we are not
 *                                    sending
 *   opens, then silence              likely the missing config frame
 *                                    (UNKNOWN 1)
 *   opens, transcribes, dies ~30s    likely the missing keepalive (UNKNOWN 2)
 *   opens and transcribes            the unknowns do not matter; wire it up
 *
 * Requires `rec` (SoX) and a working microphone. Node 18+ for the built-in
 * WebSocket; verified on the Node 22 that ships with VS Code, whose undici
 * does send custom headers.
 */
const fs = require("fs");
const { spawn } = require("child_process");

const [, , tokenFile, secsArg, langArg] = process.argv;
if (!tokenFile) {
  console.error("usage: probe-dictation.js <token-file> [seconds] [language]");
  process.exit(2);
}
const SECONDS = Number(secsArg || 12);
const LANG = langArg || "en";

let token;
try { token = fs.readFileSync(tokenFile, "utf8").trim(); }
catch (e) { console.error(`cannot read ${tokenFile}: ${e.message}`); process.exit(2); }
if (!token) { console.error(`${tokenFile} is empty`); process.exit(2); }

const qs = new URLSearchParams({
  encoding: "linear16", sample_rate: "16000", channels: "1",
  endpointing_ms: "300", utterance_end_ms: "1000",
  language: LANG, use_conversation_engine: "true",
});
const url = `wss://api.anthropic.com/api/ws/speech_to_text/voice_stream?${qs}`;

const t0 = Date.now();
const at = () => `${String((Date.now() - t0) / 1000).padStart(6)}s`;
const log = (...a) => console.log(at(), ...a);

log("connecting", `(token ${token.length} chars, not shown)`);

let ws;
try {
  ws = new WebSocket(url, {
    headers: { Authorization: `Bearer ${token}`, "x-app": "vscode" },
  });
} catch (e) { console.error("constructor threw:", e.message); process.exit(1); }
ws.binaryType = "arraybuffer";

let rec = null, sent = 0, frames = 0;

ws.addEventListener("open", () => {
  log("OPEN -- speak now");
  rec = spawn("rec", ["-q", "-c", "1", "-r", "16000", "-b", "16",
                      "-e", "signed-integer", "-t", "raw", "-"]);
  rec.on("error", (e) => log("recorder failed:",
    e.code === "ENOENT" ? "`rec` (SoX) not installed" : e.message));
  rec.stdout.on("data", (c) => {
    if (ws.readyState === 1) { ws.send(c); sent += c.length; }
  });
});

ws.addEventListener("message", (ev) => {
  frames += 1;
  if (typeof ev.data !== "string") { log("binary frame", ev.data.byteLength, "bytes"); return; }
  let m; try { m = JSON.parse(ev.data); } catch (e) { log("non-JSON:", ev.data.slice(0, 120)); return; }
  const body = m.data || m.description || m.message || "";
  log(`frame ${m.type || "?"}`, body ? JSON.stringify(body).slice(0, 120) : "");
});

ws.addEventListener("error", () => log("ERROR event (no detail is exposed by the API)"));

ws.addEventListener("close", (ev) => {
  log(`CLOSE code=${ev.code}${ev.reason ? " reason=" + ev.reason : ""}`);
  finish();
});

function finish() {
  if (rec) { try { rec.kill("SIGTERM"); } catch (e) { /* gone */ } rec = null; }
  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  console.log(`\n  ${frames} frames in ${secs}s, ${(sent / 1024).toFixed(0)} KiB audio sent`);
  if (!frames && sent) {
    console.log("  Audio went out and nothing came back -- suspect UNKNOWN 1,");
    console.log("  the config frame the real client sends on open.");
  }
  if (!sent) console.log("  No audio captured -- check `rec` and the microphone.");
  process.exit(0);
}

setTimeout(() => { log("time up"); try { ws.close(); } catch (e) { finish(); } }, SECONDS * 1000);
