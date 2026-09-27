/** Dictation backends for the ledger panel.
 *
 *  The panel knows nothing about microphones or networks. It posts
 *  `dictate:start` / `dictate:stop` and renders `dictate:text` frames. Every
 *  backend below implements that same small contract:
 *
 *      start(channel, { onText, onState, onError }) -> stop()
 *
 *      onText(text, done)   done=false is interim and may be revised;
 *                           done=true commits into the textarea
 *      onState("connecting" | "listening")
 *      onError(message)
 *
 *  Today only the fake backend exists, so the interaction can be built and
 *  judged before any transcription question is settled. Swapping in a real
 *  one means adding a module here and changing `pick()` -- the panel does not
 *  change at all.
 */

/** Scripted speech, emitted word by word with interim revisions, so the panel
 *  exercises exactly the frame sequence a real service produces: a run of
 *  interim frames that grow, then one committed frame per utterance.
 *
 *  Deliberately obvious placeholder text. A fake that reads like real
 *  transcription invites someone to believe dictation works. */
const FAKE_UTTERANCES = [
  "this is fake dictation, the microphone is not connected",
  "each phrase commits into the box when it finishes",
  "swap the backend and this becomes real speech",
];

function fakeBackend(channel, cb) {
  let timer = null;
  let stopped = false;
  let utterance = 0;

  cb.onState("connecting");

  const speak = () => {
    if (stopped) return;
    const words = FAKE_UTTERANCES[utterance % FAKE_UTTERANCES.length].split(" ");
    utterance += 1;
    let i = 0;
    cb.onState("listening");
    const tick = () => {
      if (stopped) return;
      i += 1;
      if (i <= words.length) {
        cb.onText(words.slice(0, i).join(" "), false);   // interim, growing
        timer = setTimeout(tick, 140);
      } else {
        cb.onText(words.join(" "), true);                // commit
        timer = setTimeout(speak, 900);                  // next utterance
      }
    };
    timer = setTimeout(tick, 120);
  };

  // A real service takes a moment to open its channel; mimic that so the
  // "connecting" state is actually visible rather than a one-frame flicker.
  timer = setTimeout(speak, 450);

  return () => { stopped = true; if (timer) clearTimeout(timer); };
}

/** Which backend to use. `claudeLedger.dictation.backend`: "fake" (default)
 *  or "off". A real backend registers here when one exists. */
function pick(vscode) {
  const mode = vscode.workspace.getConfiguration("claudeLedger")
    .get("dictation.backend") || "fake";
  if (mode === "off") return null;
  return fakeBackend;
}

/** Wire dictation into one webview panel. Returns a disposer.
 *
 *  One channel at a time per panel: a second start cancels the first, which
 *  matches the page (it stops the old composer before starting a new one) and
 *  means a forgotten stop can never leave a backend running. */
function attach(vscode, panel) {
  let active = null;   // { channel, stop }

  const post = (msg) => { try { panel.webview.postMessage(msg); } catch (e) { /* disposed */ } };

  const end = () => {
    if (!active) return;
    try { active.stop(); } catch (e) { /* already gone */ }
    active = null;
  };

  const handle = (msg) => {
    if (!msg || typeof msg.type !== "string") return false;
    if (msg.type === "dictate:stop") { end(); return true; }
    if (msg.type !== "dictate:start") return false;

    end();
    const channel = msg.channel;
    const backend = pick(vscode);
    if (!backend) {
      post({ type: "dictate:error", channel, error: "Dictation is turned off." });
      return true;
    }
    try {
      const stop = backend(channel, {
        onText: (text, done) => { if (active && active.channel === channel)
                                    post({ type: "dictate:text", channel, text, done }); },
        onState: (state) => { if (active && active.channel === channel)
                                post({ type: "dictate:state", channel, state }); },
        onError: (error) => { if (active && active.channel === channel) {
                                post({ type: "dictate:error", channel, error }); end(); } },
      });
      active = { channel, stop };
    } catch (e) {
      post({ type: "dictate:error", channel, error: String(e.message || e) });
    }
    return true;
  };

  return { handle, dispose: end };
}

module.exports = { attach };
