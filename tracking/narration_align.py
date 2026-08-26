#!/usr/bin/env python3
"""Align Mac-narration events from AUDIO time to the game clock.

The coach narrates at the Mac while scrubbing the local game video
(workbench Narrate page). The recorder writes a session JSON holding the
recording's wall-clock start plus the player's tick log — every
play/pause/seek/rate event with (wall_ms, video_t). That makes the mapping
audio-second → video-second exact, including pauses and rewinds, with no
kickoff-word anchors or cross-correlation.

voice_union._concat_to_clock maps `videoTimeS` to (period, elapsed) as
`period = first boundary >= t`, `elapsed = t - prev_boundary`. So we emit
    videoTimeS' = video_t - h1_kickoff_offset
and the workbench calls union with
    --boundaries (h2_off - h1_off),(video_end)
which yields elapsed = video_t - h{n}_kickoff_offset for both halves —
the game clock — without touching voice_union.

Input : voice_extract .events.json (events[].t = audio seconds)
        narration session JSON (t0_wall_ms + ticks)
Output: <events>.aligned.json with events[].videoTimeS set as above.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def audio_to_wall(audio_t: float, sess: dict) -> float:
    """Map a second of the (possibly concatenated) audio file to wall-clock ms.

    Segmented sessions (recorder pauses with the video, 2026-08-26) carry a
    `segments` table: each recorded chunk's wall-clock start plus its offset
    (`virtual_start_s`) inside the concatenated audio.m4a. Older single-shot
    sessions have no table and map linearly from t0_wall_ms.
    """
    segs = sess.get("segments")
    if not segs:
        return float(sess["t0_wall_ms"]) + audio_t * 1000.0
    for s in segs:
        if audio_t < s["virtual_start_s"] + s["dur_s"] or s is segs[-1]:
            return (float(s["t0_wall_ms"])
                    + max(0.0, audio_t - s["virtual_start_s"]) * 1000.0)
    raise AssertionError("unreachable")


def wall_to_video(wall: float, ticks: list[dict]) -> float | None:
    """Map a wall-clock ms to a video second via the tick log.

    Between ticks the video advances at the last known rate while playing and
    holds still while paused. Ticks are (w=wall_ms, v=video_t, r=rate,
    k=play|pause|seek|rate|hb|end). Returns None before the first tick.
    """
    ts = sorted(ticks, key=lambda t: t["w"])
    if not ts or wall < ts[0]["w"]:
        return None
    prev = ts[0]
    playing = prev.get("k") == "play"
    for t in ts[1:]:
        if t["w"] >= wall:
            break
        prev, playing = t, t.get("k") in ("play", "hb", "rate") or (
            t.get("k") == "seek" and playing)
        if t.get("k") == "pause" or t.get("k") == "end":
            playing = False
    dt = max(0.0, (wall - prev["w"]) / 1000.0)
    rate = float(prev.get("r") or 1.0)
    return float(prev["v"]) + (dt * rate if playing else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events", required=True, help="voice_extract .events.json")
    ap.add_argument("--session", required=True, help="narration session JSON")
    ap.add_argument("--h1-off", type=float, required=True,
                    help="video_offset_h1_kickoff_s from the game doc")
    args = ap.parse_args()

    doc = json.loads(Path(args.events).read_text())
    sess = json.loads(Path(args.session).read_text())
    ticks = sess.get("ticks") or []

    kept, dropped = [], 0
    for e in doc.get("events", []):
        v = wall_to_video(audio_to_wall(float(e["t"]), sess), ticks)
        if v is None:
            dropped += 1
            continue
        kept.append({**e, "videoTimeS": max(0.0, v - args.h1_off)})
    out = Path(args.events).with_suffix("").as_posix() + ".aligned.json"
    Path(out).write_text(json.dumps({**doc, "events": kept}, indent=2))
    print(f"aligned {len(kept)} events ({dropped} before first video tick) -> {out}")


if __name__ == "__main__":
    main()
