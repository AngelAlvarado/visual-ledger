#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["faster-whisper>=1.0", "numpy"]
# ///
"""Local streaming dictation for the ledger panel, on faster-whisper.

Reads raw audio on stdin, writes JSON lines on stdout. Nothing leaves the
machine: no token, nothing that expires, no undocumented endpoint.

    stdin   raw PCM, signed 16-bit little-endian, 16 kHz, mono
    stdout  {"type":"ready"}
            {"type":"partial","text":"<everything heard so far>"}
            {"type":"final","text":"<everything heard>"}
            {"type":"error","message":"..."}

WHY IT RE-TRANSCRIBES RATHER THAN APPENDS

Every `interval` seconds it runs the model over the whole utterance again,
not over the new slice. That is deliberate and it is the behaviour Angel
asked for: a word decoded badly in isolation gets fixed once the sentence
around it exists, so "recognise" stops being "recognize" and a name heard
mid-phrase settles down. Appending per-slice would freeze each mistake.

The cost is that output is CUMULATIVE -- each frame restates everything --
which is exactly what the hosted backend did, and what the panel already
expects via cumulative=true. Do not "optimise" this into appending; the page
would then duplicate every frame.

WINDOW

Re-decoding grows with the utterance, so the audio considered is capped at
--window seconds. Past that the oldest audio is dropped from the rolling
buffer and its text is frozen into a committed prefix, so long dictation
stays responsive without losing what was already said.

DEPENDENCIES

Declared inline (PEP 723), so `uv run` fetches them into its own cache on
first use and there is nothing to install by hand and nothing to pollute a
system Python:

    uv run scripts/stt_local.py --selftest      # no audio: prove it loads
    uv run scripts/stt_local.py --model small.en --device cpu

Plain Python still works if the two packages are already importable:

    python3 scripts/stt_local.py --selftest
"""
import argparse
import json
import sys
import threading

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def die(message):
    emit({"type": "error", "message": message})
    sys.exit(1)


def load_model(args):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        die("faster-whisper is not installed. "
            "Install it with: pip install faster-whisper")
    try:
        return WhisperModel(args.model, device=args.device,
                            compute_type=args.compute_type)
    except Exception as e:
        # The usual first-run failure is the weights download, which is a
        # network problem wearing a library's clothes. Say which it is.
        die(f"Could not load model {args.model!r} on {args.device}: {e}. "
            f"First run downloads weights from HuggingFace -- if that host is "
            f"blocked, pre-fetch the model or point --model at a local path.")


def join(prefix, tail):
    """Concatenate two transcripts without repeating the word on the seam.

    Freezing audio out of the rolling window cuts wherever the cap falls,
    usually mid-word, so the same word is decoded at the end of the frozen
    chunk AND at the start of what remains. Measured: "w3 w3", "w5 w5".
    Trim the longest word-run that is both a suffix of one and a prefix of
    the other -- bounded, because a genuine repetition ("very very") must
    survive.
    """
    if not prefix:
        return tail
    if not tail:
        return prefix
    a, b = prefix.split(), tail.split()
    for n in range(min(6, len(a), len(b)), 0, -1):
        if [w.lower().strip(".,!?") for w in a[-n:]] == \
           [w.lower().strip(".,!?") for w in b[:n]]:
            b = b[n:]
            break
    return " ".join(a + b).strip()


def transcribe(model, audio, args, want_words=False):
    """The whole buffer as one string, and optionally per-word confidence.

    `word_timestamps` makes the model report a probability per word. A low
    one means the decoder was unsure -- usually poor audio, an unusual name,
    or a sound that did not land the way English expects. That is a free
    intelligibility signal on speech you were producing anyway, so it is
    collected on the final pass and shown back rather than thrown away.

    Only on the final pass: it costs alignment work, and interim words churn
    too fast to be worth reading.
    """
    segments, _ = model.transcribe(
        audio,
        language=None if args.language == "auto" else args.language,
        beam_size=args.beam_size,
        vad_filter=True,                 # skip silence rather than invent words
        condition_on_previous_text=False,  # stops one bad guess poisoning the rest
        word_timestamps=want_words,
    )
    segments = list(segments)
    text = " ".join(s.text.strip() for s in segments).strip()
    if not want_words:
        return text, []
    words = []
    for seg in segments:
        for w in (getattr(seg, "words", None) or []):
            token = (getattr(w, "word", "") or "").strip()
            prob = getattr(w, "probability", None)
            if token and prob is not None:
                words.append({"word": token, "p": round(float(prob), 3)})
    return text, words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small.en",
                    help="faster-whisper model name or a local path")
    ap.add_argument("--device", default="cpu", help="cpu or cuda")
    ap.add_argument("--compute-type", default="int8",
                    help="int8 on CPU, float16 on a GPU")
    ap.add_argument("--language", default="en", help="code, or 'auto'")
    ap.add_argument("--beam-size", type=int, default=1,
                    help="1 is greedy and fastest; dictation rarely needs more")
    ap.add_argument("--interval", type=float, default=1.2,
                    help="seconds of new audio between re-decodes")
    ap.add_argument("--window", type=float, default=30.0,
                    help="seconds of audio kept in the rolling buffer")
    ap.add_argument("--unclear-below", type=float, default=0.65,
                    help="report words the model scored below this")
    ap.add_argument("--selftest", action="store_true",
                    help="load the model and exit, without reading audio")
    args = ap.parse_args()

    try:
        import numpy as np
    except ImportError:
        die("numpy is not installed. Install it with: pip install numpy")

    model = load_model(args)
    if args.selftest:
        emit({"type": "ready", "selftest": True})
        return
    emit({"type": "ready"})

    buf = bytearray()
    committed = ""          # text for audio that has scrolled out of the window
    lock = threading.Lock()
    done = threading.Event()

    def reader():
        """stdin in its own thread, so decoding never stalls capture."""
        stream = sys.stdin.buffer
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            with lock:
                buf.extend(chunk)
        done.set()

    threading.Thread(target=reader, daemon=True).start()

    step = int(args.interval * SAMPLE_RATE * BYTES_PER_SAMPLE)
    cap = int(args.window * SAMPLE_RATE * BYTES_PER_SAMPLE)
    seen = 0
    last = ""
    last_words = []     # per-word confidence from the final pass

    def current_text(want_words=False):
        nonlocal committed
        with lock:
            if len(buf) > cap:
                # Freeze in HALF-WINDOW chunks, not by the sliver that just
                # overflowed. A fragment decoded alone is exactly the badly
                # guessed word this design exists to avoid, so what gets
                # frozen is a long stretch with context around it. Still
                # imperfect: the cut can land mid-word, and that one word may
                # come out wrong. Only bites past --window seconds of speech.
                take = len(buf) - cap + cap // 2
                overflow = bytes(buf[:take])
                del buf[:take]
                frozen, _ = transcribe(
                    model,
                    np.frombuffer(overflow, np.int16).astype(np.float32) / 32768.0,
                    args)
                if frozen:
                    committed = join(committed, frozen)
            audio = np.frombuffer(bytes(buf), np.int16).astype(np.float32) / 32768.0
        if audio.size < SAMPLE_RATE // 4:      # under 250ms: nothing new to say
            return committed
        text, words = transcribe(model, audio, args, want_words)
        last_words[:] = words
        return join(committed, text)

    while not done.is_set():
        done.wait(0.15)
        with lock:
            have = len(buf)
        if have - seen < step:
            continue
        seen = have
        try:
            text = current_text()
        except Exception as e:
            die(f"Transcription failed: {e}")
        if text and text != last:
            last = text
            emit({"type": "partial", "text": text})

    try:
        text = current_text(want_words=True)
    except Exception as e:
        die(f"Transcription failed: {e}")
    # Only the ones worth looking at. Everything scores high on clear speech,
    # so a full dump would be noise.
    unclear = sorted((w for w in last_words if w["p"] < args.unclear_below),
                     key=lambda w: w["p"])[:6]
    emit({"type": "final", "text": text or last, "unclear": unclear})


if __name__ == "__main__":
    main()
