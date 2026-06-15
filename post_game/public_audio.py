"""Public-reel audio swap — replace a reel's original audio (coach voice, kids'
names, sideline chatter) with a stadium-ambience bed + crowd roars on goals, so
the PUBLIC reel is privacy-safe while the DUGOUT reel keeps the original.

This is a fast REMUX (video is stream-copied, only audio is rebuilt), so it runs
in a minute or two on an already-rendered reel — no perspective re-render. The
output is a separate file (e.g. tv_reel_public.mp4); the original tv_reel.mp4 is
untouched and stays the coach/dugout copy. Privacy: the public file contains ONLY
the ambience/roar audio — the original track is dropped, not muted.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from . import config

log = logging.getLogger(__name__)


def _segments_to_reel_time(t: float, segs: list[tuple[float, float]]) -> Optional[float]:
    """Source-video second -> reel second, accounting for the trimmed-out gaps
    (warmup/halftime). Mirrors pipeline._build_broadcast_events_index."""
    if not segs:
        return None
    acc = 0.0
    for (a, b) in segs:
        if a <= t <= b:
            return acc + (t - a)
        acc += max(0.0, b - a)
    return None


def render_public_audio(
    reel_path: str,
    out_path: str,
    segments: list[tuple[float, float]],
    goal_video_times: list[float],
    *,
    ambience_path: str = None,
    roar_path: str = None,
    bed_db: float = None,
    roar_db: float = None,
    lead_s: float = None,
    fade_s: float = None,
) -> Optional[str]:
    """Write `out_path` = reel video (copied) + stadium bed (looped, ducked) with
    a crowd roar layered at each goal. Returns out_path on success, else None.

    `goal_video_times` are SOURCE-video seconds; they're mapped to reel time via
    `segments` (the reel's rendered windows) so a roar lands on the goal as the
    viewer sees it.
    """
    ambience_path = ambience_path or config.PUBLIC_AMBIENCE_PATH
    roar_path = roar_path or config.PUBLIC_ROAR_PATH
    bed_db = config.PUBLIC_BED_DB if bed_db is None else bed_db
    roar_db = config.PUBLIC_ROAR_DB if roar_db is None else roar_db
    lead_s = config.PUBLIC_ROAR_LEAD_S if lead_s is None else lead_s
    fade_s = config.PUBLIC_ROAR_FADE_S if fade_s is None else fade_s

    if not Path(reel_path).exists():
        log.warning("public-audio: reel %s missing; skipping.", reel_path)
        return None
    if not Path(ambience_path).exists():
        log.warning("public-audio: ambience asset %s missing; skipping.", ambience_path)
        return None

    have_roar = bool(roar_path) and Path(roar_path).exists()
    goal_rts = sorted(r for r in (_segments_to_reel_time(t, segments) for t in (goal_video_times or [])) if r is not None)
    if not have_roar:
        goal_rts = []

    # Build the filtergraph: ducked looped bed + one delayed roar per goal -> amix.
    inputs = ["-i", reel_path, "-stream_loop", "-1", "-i", ambience_path]
    filt = [f"[1:a]volume={bed_db}dB[bed]"]
    mix_labels = ["[bed]"]
    idx = 2
    for i, rt in enumerate(goal_rts):
        inputs += ["-i", roar_path]
        ms = max(0, int((rt - lead_s) * 1000))   # lead: start the roar before the tap
        # afade=in builds the roar over fade_s so a few seconds of timing slop is
        # masked (crowd swelling), instead of a sharp hit at the wrong instant.
        filt.append(f"[{idx}:a]afade=t=in:d={fade_s},adelay={ms}|{ms},volume={roar_db}dB[g{i}]")
        mix_labels.append(f"[g{i}]")
        idx += 1
    # normalize=0 keeps our chosen levels (amix otherwise attenuates each input).
    filt.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:normalize=0[a]")

    cmd = (["ffmpeg", "-y", "-v", "error", "-nostdin"] + inputs
           + ["-filter_complex", ";".join(filt),
              "-map", "0:v", "-map", "[a]",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
              "-shortest", out_path])
    log.info("public-audio: %s -> %s (bed %gdB, %d goal roars)",
             Path(reel_path).name, Path(out_path).name, bed_db, len(goal_rts))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        log.warning("public-audio remux failed: %s", e)
        return None
    return out_path


def goal_video_times(game, clock_to_video) -> list[float]:
    """Source-video seconds of every GOAL / OPP_GOAL in the coach log."""
    out: list[float] = []
    for e in (game.events or []):
        if (getattr(e, "type", "") or "").upper() in ("GOAL", "OPP_GOAL", "OPPONENT_GOAL", "GOAL_AGAINST"):
            try:
                out.append(float(clock_to_video(e.period, e.elapsed)))
            except Exception:
                continue
    return out
