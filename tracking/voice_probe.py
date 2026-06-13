"""Phase 3.5 voice probe — transcribe game-day audio and eyeball viability.

The gate question: on REAL sideline audio (AirPods mic, wind, crowd, a coach
who shouts and goes quiet), does Whisper produce transcripts where player
names/numbers are legible and timestamps line up with the logged events?
Pass → build 3.6 (LLM extraction → draft events). Fail → bookmarks + partial
re-watch instead.

Runtime is intentionally pluggable because the corp Artifactory mirror 404s
on every Whisper wheel (numba/faster-whisper/transformers all fetch-fail).
Install ONE of these off-VPN (toggle the corp VPN off for the pip, then back
on) and this script auto-detects it:
    pip install mlx-whisper        # best on Apple Silicon (M-series)
    pip install faster-whisper     # CTranslate2, fast on CPU/GPU
    pip install openai-whisper     # reference (needs numba)

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.voice_probe \
        --audio "voice memo/New Recording 5.m4a" --label game2 --model small
    # game 1 fragments live in R2 (voice_mqcf9axlvtuyt_*.m4a) — pass --r2-prefix
    .venv-post-game/bin/python -m tracking.voice_probe \
        --r2-prefix voice_mqcf9axlvtuyt_ --label game1 --model small
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

OUT_DIR = Path("tracking/outputs/voice_probe")


def _transcribe(audio_path: str, model_size: str):
    """Return list of {start, end, text} segments via whatever Whisper is
    installed. Tries fastest-on-Apple first."""
    # 1) mlx-whisper (Apple Silicon)
    try:
        import mlx_whisper  # type: ignore
        repo = f"mlx-community/whisper-{model_size}-mlx"
        r = mlx_whisper.transcribe(audio_path, path_or_hf_repo=repo, word_timestamps=False)
        return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in r["segments"]]
    except ImportError:
        pass
    # 2) faster-whisper
    try:
        from faster_whisper import WhisperModel  # type: ignore
        m = WhisperModel(model_size, device="auto", compute_type="int8")
        segs, _ = m.transcribe(audio_path, vad_filter=True)
        return [{"start": s.start, "end": s.end, "text": s.text} for s in segs]
    except ImportError:
        pass
    # 3) openai-whisper
    try:
        import whisper  # type: ignore
        m = whisper.load_model(model_size)
        r = m.transcribe(audio_path, fp16=False)
        return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in r["segments"]]
    except ImportError:
        raise SystemExit(
            "No Whisper runtime installed. Off the corp VPN, run one of:\n"
            "  pip install mlx-whisper | faster-whisper | openai-whisper\n"
            "then re-run this probe."
        )


# Closed roster — the parser/extractor only ever matches against THESE names
# (first names; last names disambiguate the collisions). Lifted from the PWA
# SEED_ROSTER so the probe reports legibility against the real vocabulary.
ROSTER_FIRST = [
    "Ben", "Vince", "Maverick", "Liam", "Nolan", "Khalid", "Issa",
    "Arian", "Alexander", "Alex", "Jason", "David", "Jaedyn", "Luca", "Gabriel",
]
EVENT_WORDS = ["goal", "shot", "save", "block", "turnover", "ball win",
               "key pass", "give", "corner", "foul", "pass", "cross", "tackle"]


def _score(segments):
    text = " ".join(s["text"] for s in segments).lower()
    name_hits = {n: text.count(n.lower()) for n in ROSTER_FIRST if n.lower() in text}
    event_hits = {w: text.count(w) for w in EVENT_WORDS if w in text}
    words = len(text.split())
    return {"total_words": words, "name_hits": name_hits, "event_hits": event_hits}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio", help="local audio file")
    ap.add_argument("--r2-prefix", help="R2 key prefix to pull + concat (e.g. voice_<gameId>_)")
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", default="small", help="tiny|base|small|medium|large-v3")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    audio = args.audio

    tmpdir = None
    if args.r2_prefix:
        from post_game import firestore_io
        s3 = firestore_io._r2_client()
        bucket = os.environ["R2_BUCKET"]
        keys = sorted(o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=args.r2_prefix).get("Contents", []))
        if not keys:
            raise SystemExit(f"no R2 objects under {args.r2_prefix}")
        tmpdir = tempfile.mkdtemp(prefix="voiceprobe_")
        local = []
        for k in keys:
            dst = os.path.join(tmpdir, os.path.basename(k))
            s3.download_file(bucket, k, dst)
            local.append(dst)
        # concat via ffmpeg (re-encode to one stream so timestamps are continuous)
        listf = os.path.join(tmpdir, "list.txt")
        Path(listf).write_text("".join(f"file '{p}'\n" for p in local))
        audio = os.path.join(tmpdir, "concat.m4a")
        os.system(f"ffmpeg -y -f concat -safe 0 -i {listf} -c copy {audio} >/dev/null 2>&1")
        print(f"concatenated {len(local)} R2 takes → {audio}")

    print(f"transcribing {audio} (model={args.model})…")
    segments = _transcribe(audio, args.model)
    summary = _score(segments)

    out = OUT_DIR / f"{args.label}.json"
    out.write_text(json.dumps({"audio": audio, "model": args.model,
                               "summary": summary, "segments": segments}, indent=2))
    print(f"\n=== {args.label}: {summary['total_words']} words ===")
    print("player-name hits:", summary["name_hits"] or "NONE — legibility concern")
    print("event-word hits:", summary["event_hits"] or "none")
    print(f"full transcript + timestamps → {out}")


if __name__ == "__main__":
    main()
