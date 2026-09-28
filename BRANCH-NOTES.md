# Branch: dictation-anthropic

Structure for pointing the composer's mic at Anthropic's speech socket, the
one the Claude Code extension uses for its own dictation.

**Not merged, not verified end to end, and not the default.** `main` keeps the
fake backend. This branch adds a second option behind a setting.

## Why it might not work

Three parts of the handshake could not be read out of the extension bundle:

1. **A frame sent on open, and repeated on an interval.** Contents unknown.
   Plausibly a keepalive, plausibly a config frame the service requires before
   it will transcribe anything. `OPEN_FRAME = null` in the code.
2. **That interval.** 15s is a guess.
3. **An `anthropic-client-platform` header.** Value unknown, so it is omitted
   rather than guessed -- a wrong value is likelier to be rejected than a
   missing one.

What *is* known: the URL and its query parameters, the `Authorization: Bearer`
and `x-app: vscode` headers, linear16/16 kHz/mono audio, and the frame types
(`TranscriptInterim`, `TranscriptText`, `TranscriptEndpoint`,
`TranscriptError`, `error`).

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
