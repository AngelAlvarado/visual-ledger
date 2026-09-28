/** Dictation against Anthropic's speech-to-text socket.
 *
 *  STATUS: STRUCTURE ONLY, UNVERIFIED END TO END.
 *
 *  This talks to an endpoint that is not public API. It is what the Claude
 *  Code extension uses for its own dictation, which means:
 *
 *    - it can change or disappear without notice, and nothing will warn us;
 *    - a third-party client using it may not be within Anthropic's terms;
 *    - it wants a live claude.ai session token, not a scoped API key, so a
 *      leak is a leak of the whole account;
 *    - the token is short-lived. Expect this to stop working after hours,
 *      not weeks, and there is no refresh flow here.
 *
 *  Three details of the handshake could not be read out of the extension
 *  bundle and are marked UNKNOWN below. Any of them may be required. If the
 *  socket opens and then goes silent, or closes after ~30s, suspect those
 *  first.
 *
 *  Shape of the exchange, as far as it is known:
 *
 *    connect  wss://api.anthropic.com/api/ws/speech_to_text/voice_stream
 *             ?encoding=linear16&sample_rate=16000&channels=1
 *             &endpointing_ms=300&utterance_end_ms=1000
 *             &language=<code>&use_conversation_engine=true
 *    headers  Authorization: Bearer <claude.ai token>
 *             x-app: vscode
 *    send     raw linear16 PCM frames, 16 kHz, mono, little-endian
 *    receive  {type:"TranscriptText", data:"..."}   the transcript SO FAR
 *             {type:"TranscriptError", description:"..."}
 *             {type:"error", message:"..."}
 *
 *  OBSERVED (probe run, 2026-09-27): `data` is CUMULATIVE -- every frame
 *  restates the whole session, not the latest phrase ("Hello," then
 *  "Hello, hello," then "Hello, hello, hello."). No TranscriptEndpoint frame
 *  ever arrived across a 12s run. So there is no per-utterance commit signal:
 *  the text is one growing string, and the commit point is when the user
 *  stops. Frames are emitted with cumulative=true and the page replaces
 *  rather than appends.
 *
 *  Audio capture is `rec` (SoX), which is present in this devcontainer and
 *  verified to produce 16 kHz mono (64000 bytes in 2s). It is NOT present on
 *  a plain macOS or Windows machine, so this backend is devcontainer-shaped
 *  until something portable replaces it.
 */
const { spawn } = require("child_process");
const fs = require("fs");

const ENDPOINT = "wss://api.anthropic.com/api/ws/speech_to_text/voice_stream";

// ANSWERED by the probe: a config frame on open is NOT required -- the socket
// transcribed immediately without one. The real client does send something on
// open, but it is not a precondition, so nothing is sent here.
const OPEN_FRAME = null;

// STILL UNKNOWN: whether an idle socket is dropped, and after how long. The
// probe only ran 12s, which proves nothing about a ~30s timeout. Left unset
// rather than guessed; if long dictations die at a consistent interval, this
// is the first thing to try.
const KEEPALIVE_MS = 0;           // 0 = send nothing

// ANSWERED by the probe: the `anthropic-client-platform` header is NOT
// required. Authorization plus x-app was enough to open and transcribe.

/** Read the token from a file, and never log it or return it to a caller.
 *  A path is used rather than a settings string so the secret does not end
 *  up in a synced settings.json. */
function readToken(tokenPath) {
  if (!tokenPath) throw new Error(
    "Set claudeLedger.dictation.tokenPath to a file holding a claude.ai token.");
  let t;
  try { t = fs.readFileSync(tokenPath, "utf8").trim(); }
  catch (e) { throw new Error(`Cannot read the token file at ${tokenPath}.`); }
  if (!t) throw new Error(`The token file at ${tokenPath} is empty.`);
  return t;
}

/** 16 kHz mono signed 16-bit PCM on stdout. */
function startRecorder(onChunk, onFail) {
  const rec = spawn("rec", [
    "-q",
    "-c", "1",                    // mono
    "-r", "16000",                // 16 kHz
    "-b", "16",                   // 16-bit
    "-e", "signed-integer",
    "-t", "raw", "-",             // raw PCM to stdout
  ]);
  rec.stdout.on("data", onChunk);
  rec.on("error", (e) => onFail(
    e.code === "ENOENT" ? "`rec` (SoX) is not installed." : String(e.message)));
  // SoX is noisy on stderr even when healthy, so only a non-zero exit counts.
  let err = "";
  rec.stderr.on("data", (d) => { err += String(d); });
  rec.on("close", (code) => {
    if (code) onFail(`Recorder exited ${code}. ${err.trim().slice(0, 200)}`);
  });
  return () => { try { rec.kill("SIGTERM"); } catch (e) { /* gone */ } };
}

/** Same contract as the fake backend:
 *      start(channel, { onText, onState, onError }) -> stop()  */
function anthropicBackend(channel, cb, opts) {
  let token;
  try { token = readToken(opts && opts.tokenPath); }
  catch (e) { cb.onError(e.message); return () => {}; }

  const qs = new URLSearchParams({
    encoding: "linear16",
    sample_rate: "16000",
    channels: "1",
    endpointing_ms: "300",
    utterance_end_ms: "1000",
    language: (opts && opts.language) || "en",
    use_conversation_engine: "true",
  });

  cb.onState("connecting");

  let ws, stopRec = null, keepalive = null, closed = false;
  // The running transcript. Committed when the user stops, because the
  // service sends no per-phrase commit signal (see OBSERVED above).
  let transcript = "";
  // Audio recorded before the socket opens would otherwise be dropped, and
  // the first word is exactly what people lose.
  const pending = [];

  const shutdown = () => {
    if (closed) return;
    closed = true;
    // Stopping is the only commit point there is: hand over whatever was
    // transcribed, or pressing stop would throw the whole dictation away.
    if (transcript) { try { cb.onText(transcript, true, true); } catch (e) { /* gone */ } }
    if (keepalive) clearInterval(keepalive);
    if (stopRec) stopRec();
    try { if (ws && ws.readyState <= 1) ws.close(); } catch (e) { /* gone */ }
  };

  try {
    ws = new WebSocket(`${ENDPOINT}?${qs}`, {
      headers: { Authorization: `Bearer ${token}`, "x-app": "vscode" },
    });
  } catch (e) {
    cb.onError(`Could not open the socket: ${e.message}`);
    return shutdown;
  }
  ws.binaryType = "arraybuffer";

  ws.addEventListener("open", () => {
    if (closed) return;
    if (OPEN_FRAME) {
      ws.send(OPEN_FRAME);
      if (KEEPALIVE_MS) keepalive = setInterval(() => {
        if (ws.readyState === 1) ws.send(OPEN_FRAME);
      }, KEEPALIVE_MS);
    }
    while (pending.length) ws.send(pending.shift());
    cb.onState("listening");
  });

  ws.addEventListener("message", (ev) => {
    if (closed) return;
    let m;
    try { m = JSON.parse(typeof ev.data === "string" ? ev.data : ""); }
    catch (e) { return; }                       // binary or malformed: ignore
    switch (m.type) {
      case "TranscriptInterim":
      case "TranscriptText":
        // Cumulative: this IS the transcript, not an addition to it.
        if (m.data) { transcript = m.data; cb.onText(m.data, false, true); }
        break;
      case "TranscriptEndpoint":
        // Never seen in practice, but honour it if the service starts
        // sending one -- it would be a genuine phrase boundary.
        if (m.data || transcript) {
          transcript = m.data || transcript;
          cb.onText(transcript, true, true);
        }
        break;
      case "TranscriptError":
        cb.onError(m.description || "Transcription error."); shutdown(); break;
      case "error":
        cb.onError(m.message || "Socket error."); shutdown(); break;
      default:
        break;                                  // unknown frame: ignore
    }
  });

  ws.addEventListener("error", () => {
    if (!closed) { cb.onError("Socket error -- the token may have expired."); shutdown(); }
  });

  ws.addEventListener("close", (ev) => {
    if (closed) return;
    // 1008/4401-style closes are the shape an expired token takes.
    const why = ev && ev.code && ev.code !== 1000
      ? `Socket closed (${ev.code}${ev.reason ? ": " + ev.reason : ""}).`
      : null;
    if (why) cb.onError(why);
    shutdown();
  });

  stopRec = startRecorder(
    (chunk) => {
      if (closed) return;
      if (ws.readyState === 1) ws.send(chunk);
      else if (ws.readyState === 0 && pending.length < 200) pending.push(chunk);
    },
    (msg) => { if (!closed) { cb.onError(msg); shutdown(); } });

  return shutdown;
}

module.exports = { anthropicBackend, ENDPOINT };
