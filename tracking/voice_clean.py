"""Phase 3.6 — clean game narration for the LLM extractor by dropping the
coach's on-field instructions ("get back!", "mark up!", "press!") and keeping
only play-by-play narration.

WHY content, not loudness: a magnitude probe on the Amherstburg memo showed
shouts ARE louder (a long tail to ~-3 dBFS) but do NOT separate cleanly — the
loudness histogram is unimodal, only ~1.5% of audio is clearly loud, excited
narration ("GOAL!") is just as loud as a shout, and spectral "vocal effort"
(centroid) overlaps almost entirely. So a loudness gate would both over-cut
(loud narration) and under-cut (calm instructions). Instead we classify by
CONTENT with an LLM (the coach already wants LLMs across the stack), and feed
the loudness tag as a weak hint the model can use to break ties.

Pipeline:
  1. loudness tag   — per-segment mean/peak dBFS from the audio (ffmpeg+numpy).
                      Runs with no extra deps (this is the part validated today).
  2. classify       — claude-opus-4-8 labels each segment narration|instruction|
                      other, with the loudness hint + the closed roster.
  3. emit           — narration-only transcript (the cleaner LLM input) + a
                      fully-annotated JSON; optionally a muted .m4a with the
                      instruction spans silenced (timeline preserved).

Usage:
    set -a; source .env; set +a            # ANTHROPIC_API_KEY for step 2
    # transcript comes from tracking.voice_probe (Whisper) → segments JSON:
    .venv-post-game/bin/python -m tracking.voice_clean \
        --segments tracking/outputs/voice_probe/game2.json \
        --audio "voice memo/New Recording 5.m4a" --label game2 --mute-audio

    # loudness only (no transcript / no API needed — validates the audio path):
    .venv-post-game/bin/python -m tracking.voice_clean \
        --audio "voice memo/New Recording 5.m4a" --label game2 --loudness-only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import numpy as np

# Reuse the closed roster the probe uses, so the classifier matches against the
# real vocabulary (first names; last names disambiguate collisions).
try:
    from tracking.voice_probe import ROSTER_FIRST
except Exception:  # pragma: no cover - probe should always be importable
    ROSTER_FIRST = []

OUT_DIR = Path("tracking/outputs/voice_clean")
MODEL = "claude-opus-4-8"
SR = 16000
FRAME_S = 0.05  # 50 ms loudness frames


def _decode_pcm(audio_path: str) -> np.ndarray:
    """Decode any audio file to mono 16 kHz float32 PCM in [-1, 1] via ffmpeg."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", audio_path, "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"],
        stdout=subprocess.PIPE, check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _frame_db(x: np.ndarray) -> np.ndarray:
    """Per-50ms-frame RMS loudness in dBFS."""
    fl = int(FRAME_S * SR)
    n = len(x) // fl
    if n == 0:
        return np.array([-120.0])
    rms = np.sqrt((x[:n * fl].reshape(n, fl) ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def loudness_report(audio_path: str, loud_db: float = -14.0, min_burst_s: float = 0.3):
    """Standalone loudness summary + sustained-loud spans (no transcript needed)."""
    db = _frame_db(_decode_pcm(audio_path))
    n = len(db)
    sm = np.convolve(db, np.ones(5) / 5, mode="same")  # 0.25 s smoothing
    loud = sm > loud_db
    spans, i, min_frames = [], 0, int(min_burst_s / FRAME_S)
    while i < n:
        if loud[i]:
            j = i
            while j < n and loud[j]:
                j += 1
            if j - i >= min_frames:
                spans.append((round(i * FRAME_S, 2), round(j * FRAME_S, 2),
                              round(float(sm[i:j].max()), 1)))
            i = j
        else:
            i += 1
    return {
        "duration_min": round(n * FRAME_S / 60, 1),
        "p50_dbfs": round(float(np.percentile(db, 50)), 1),
        "p90_dbfs": round(float(np.percentile(db, 90)), 1),
        "p99_dbfs": round(float(np.percentile(db, 99)), 1),
        "loud_gate_dbfs": loud_db,
        "loud_spans": spans,
        "loud_time_s": round(sum(e - s for s, e, _ in spans), 1),
        "loud_pct": round(100 * sum(e - s for s, e, _ in spans) / (n * FRAME_S), 2),
    }


def tag_segment_loudness(segments: list[dict], audio_path: str) -> list[dict]:
    """Add mean/peak dBFS + a 0-1 relative-loudness rank to each segment."""
    db = _frame_db(_decode_pcm(audio_path))
    # speech floor so the percentile rank ignores silent gaps
    floor = np.percentile(db, 20)
    speech = db[db > floor + 8]
    lo, hi = (float(np.percentile(speech, 5)), float(np.percentile(speech, 99))) if len(speech) else (-50.0, -3.0)
    out = []
    for s in segments:
        a, b = int(float(s["start"]) / FRAME_S), int(float(s["end"]) / FRAME_S)
        seg = db[a:max(a + 1, b)]
        mean_db = float(seg.mean()) if len(seg) else -120.0
        peak_db = float(seg.max()) if len(seg) else -120.0
        rank = float(np.clip((peak_db - lo) / max(1e-6, hi - lo), 0, 1))
        out.append({**s, "mean_dbfs": round(mean_db, 1), "peak_dbfs": round(peak_db, 1),
                    "loud_rank": round(rank, 2)})
    return out


CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer", "description": "segment index"},
                    "label": {"type": "string", "enum": ["narration", "instruction", "other"]},
                },
                "required": ["i", "label"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}

SYSTEM = (
    "You label a youth-soccer coach's sideline audio, transcribed into timed "
    "segments. The coach NARRATES the game for later analysis, but sometimes "
    "SHOUTS tactical instructions to his own players on the field. We want only "
    "the narration.\n\n"
    "For each segment choose one label:\n"
    "- narration: play-by-play or analysis ABOUT the game — who has the ball, "
    "shots, goals, saves, turnovers, tactical observations, scorelines.\n"
    "- instruction: a command directed AT players on the field — e.g. 'get back', "
    "'mark up', 'man on', 'press', 'spread out', 'switch it', 'drop', 'time', "
    "'turn', encouragement like 'good job keep going', or a player's name used as "
    "a shout to act. These are spoken TO the players, not about the game.\n"
    "- other: crowd noise, filler, unintelligible, or off-topic.\n\n"
    "CONTENT is the deciding signal. Each segment includes a loud_rank in [0,1] "
    "(1 = unusually loud for this recording); shouts skew louder, so use it only "
    "to break genuine ties — do NOT label something an instruction just because "
    "it is loud (excited narration like 'GOAL!' is loud too), and do NOT keep a "
    "calm instruction just because it is quiet.\n"
    f"Players (first names): {', '.join(ROSTER_FIRST)}.\n"
    "Return a label for every segment index you are given."
)


def classify(segments: list[dict], model: str = MODEL, chunk: int = 300) -> list[str]:
    """Label each segment narration|instruction|other via claude-opus-4-8."""
    import anthropic  # imported lazily so loudness-only runs without the SDK

    client = anthropic.Anthropic()
    labels: dict[int, str] = {}
    for start in range(0, len(segments), chunk):
        batch = segments[start:start + chunk]
        lines = [
            {"i": start + k, "loud_rank": s.get("loud_rank"),
             "text": (s.get("text") or "").strip()}
            for k, s in enumerate(batch)
        ]
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content":
                       "Label these segments:\n" + json.dumps(lines, ensure_ascii=False)}],
            output_config={"format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        for row in json.loads(text).get("labels", []):
            labels[int(row["i"])] = row["label"]
    # default anything the model skipped to narration (safer to keep than drop)
    return [labels.get(i, "narration") for i in range(len(segments))]


def mute_audio(audio_path: str, drop_spans: list[tuple], out_path: str) -> None:
    """Write a copy of the audio with drop_spans silenced (timeline preserved)."""
    if not drop_spans:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", audio_path, "-c", "copy", out_path], check=True)
        return
    enable = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in drop_spans)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", audio_path,
                    "-af", f"volume=enable='{enable}':volume=0", out_path], check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio", required=True, help="audio file (for loudness + muting)")
    ap.add_argument("--segments", help="voice_probe segments JSON (transcript)")
    ap.add_argument("--label", required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--loudness-only", action="store_true",
                    help="just report loudness (no transcript / no API needed)")
    ap.add_argument("--mute-audio", action="store_true",
                    help="also write a cleaned .m4a with instruction spans silenced")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.loudness_only:
        rep = loudness_report(args.audio)
        (OUT_DIR / f"{args.label}.loudness.json").write_text(json.dumps(rep, indent=2))
        print(f"=== {args.label} loudness ===")
        print(f"{rep['duration_min']} min · p50 {rep['p50_dbfs']} / p90 {rep['p90_dbfs']} / "
              f"p99 {rep['p99_dbfs']} dBFS")
        print(f"loud (> {rep['loud_gate_dbfs']} dBFS, ≥0.3s): {len(rep['loud_spans'])} spans, "
              f"{rep['loud_time_s']}s ({rep['loud_pct']}% of audio)")
        print(f"→ {OUT_DIR / (args.label + '.loudness.json')}")
        return

    if not args.segments:
        raise SystemExit("--segments (voice_probe transcript JSON) required unless --loudness-only. "
                         "Run tracking.voice_probe first (needs Whisper, off-VPN install).")
    doc = json.loads(Path(args.segments).read_text())
    segments = doc["segments"] if isinstance(doc, dict) and "segments" in doc else doc

    segments = tag_segment_loudness(segments, args.audio)
    labels = classify(segments, model=args.model)
    for s, lab in zip(segments, labels):
        s["label"] = lab

    keep = [s for s in segments if s["label"] == "narration"]
    drop = [s for s in segments if s["label"] != "narration"]
    counts = {k: sum(1 for s in segments if s["label"] == k)
              for k in ("narration", "instruction", "other")}

    # cleaned narration transcript (the cleaner input for the extraction LLM)
    narration_txt = "\n".join(f"[{float(s['start']):.1f}] {(s.get('text') or '').strip()}" for s in keep)
    (OUT_DIR / f"{args.label}.narration.txt").write_text(narration_txt)
    (OUT_DIR / f"{args.label}.annotated.json").write_text(
        json.dumps({"model": args.model, "counts": counts, "segments": segments}, indent=2,
                   ensure_ascii=False))

    print(f"=== {args.label} ===")
    print(f"segments: {len(segments)}  narration={counts['narration']}  "
          f"instruction={counts['instruction']}  other={counts['other']}")
    print(f"dropped {len(drop)} non-narration segments → cleaner LLM input")
    print(f"→ {OUT_DIR / (args.label + '.narration.txt')}")
    print(f"→ {OUT_DIR / (args.label + '.annotated.json')}")

    if args.mute_audio:
        out_audio = str(OUT_DIR / f"{args.label}.cleaned.m4a")
        mute_audio(args.audio, [(float(s["start"]), float(s["end"])) for s in drop], out_audio)
        print(f"→ {out_audio}  (instruction/other spans silenced, timeline preserved)")


if __name__ == "__main__":
    main()
