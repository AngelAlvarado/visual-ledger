# Branch: dictation-anthropic

Structure for pointing the composer's mic at Anthropic's speech socket, the
one the Claude Code extension uses for its own dictation.

**Not merged, not verified end to end, and not the default.** `main` keeps the
fake backend. This branch adds a second option behind a setting.

## It works. Probe run, 2026-09-27

```
 0.49s OPEN -- speak now
 3.699s frame TranscriptText "Hello,"
 4.698s frame TranscriptText "Hello, hello, hello."
 9.811s frame TranscriptText "Hello, hello, hello. One, two, three. This seems to be working."
12.018s time up
```

Two of the three unknowns turned out not to matter:

| Unknown | Answer |
|---|---|
| Config frame on open | **Not required.** It transcribed without one |
| `anthropic-client-platform` header | **Not required.** `Authorization` + `x-app` sufficed |
| Keepalive interval | **Still open.** 12s proves nothing about a ~30s idle timeout |

### The finding that mattered

`data` is **cumulative** -- every frame restates the whole session, not the
latest phrase -- and **no `TranscriptEndpoint` ever arrived**. So there is no
per-phrase commit signal. Two consequences, both now handled:

- The transcript is committed when the user presses **stop**, since that is
  the only commit point the service offers.
- Frames carry `cumulative: true` and the page **replaces** rather than
  appends. Appending cumulative text duplicates everything before it, and
  `test_page.js` now fails on exactly that (verified by mutation: it produces
  `"typed one two one two three"`).

Without the probe this would have shipped as a composer that showed grey text
forever and never put a word in the box.

### Still to establish

Whether an idle socket is dropped, and after how long. Run the probe for 60s
with a long silence in the middle; if it dies at a consistent interval,
`KEEPALIVE_MS` is the knob.

## Find out in one command

Run it from anywhere, with an absolute path, and with a Node 22 — the `node`
on PATH in this devcontainer is v20 and has no global `WebSocket`:

```bash
/home/node/.vscode-server/bin/*/node \
  /workspace/visual-ledger/scripts/probe-dictation.js \
  /path/to/token-file 12
```

Streams your microphone for 12 seconds and prints every frame. Reading it:

| What happens | What it means |
|---|---|
| Closes at once, 401/403 | token wrong or expired, or something else is missing |
| Opens, then silence | probably the config frame (unknown 1) |
| Transcribes, dies ~30s | probably the keepalive (unknown 2) |
| Transcribes and keeps going | the unknowns do not matter — wire it up |

The probe prints the token's length, never the token.

## Using it in the panel

```json
"claudeLedger.dictation.backend": "anthropic",
"claudeLedger.dictation.tokenPath": "/absolute/path/to/token-file"
```

A path, not the secret, so the token never lands in a synced settings.json.

## Before this merges

- The endpoint is undocumented. It can change without notice, and a
  third-party client using it may fall outside Anthropic's terms.
- The token is a **live claude.ai session credential**, not a scoped API key.
- It **expires in hours**. There is no refresh flow here, so dictation will
  stop working and the only symptom will be a socket that closes.
- Capture is `rec` (SoX), which exists in this devcontainer but not on a
  stock macOS or Windows machine.

The local-model route avoids every one of these. Worth pricing before this
becomes the default.
