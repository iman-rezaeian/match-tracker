"""Physics validator: one player cannot be in two places at once.

A tracker/stitcher merge can weld two different children into one track. Nothing
downstream notices: the coach labels the track "my son", and from then on another
kid's running is silently inside his distance, speed and heatmap. Measured on W8
(against the coach's OWN accepted labels): **12.2% of player-frames have that
player detected in two or more places more than 1.5 m apart, a median 15.2 m
apart** — far too distant to be a duplicate box on one body.

That is provable corruption from physics alone: it needs no appearance model, no
jersey number and no schedule. This module finds those moments so the pipeline
can (a) EXCLUDE them from distance/speed/heatmap instead of quietly averaging
them in, and (b) show the coach which of his labels are in conflict.

Design choices, deliberate:
  * A conflict is judged per INSTANT, not per track. The corruption is
    concentrated (on W8, 74% of bad time sat in 10 tracklets), so excluding whole
    tracks would throw away good minutes; excluding the conflicting instants
    keeps the clean remainder.
  * We do NOT try to pick the "real" one of the two positions. There is no signal
    that could (appearance is a coin flip on same-kit teammates). Guessing would
    re-introduce exactly the silent error we are removing, so we drop the instant.
  * The threshold is a physical plausibility bound, not a tuning knob: two boxes
    on the SAME child sit within ~1 m, so anything beyond `max_sep_m` is two
    bodies.

Pure: no pandas/Firestore/video. Times are video seconds.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

# Two detections of ONE child (duplicate boxes, tile seams) fall well inside this.
# Beyond it they are two different bodies. U10 players are ~1.4 m tall, so 1.5 m
# is already generous for "same body, sloppy box".
DEFAULT_MAX_SEP_M = 1.5


def find_conflicts(
    samples: Iterable[tuple[str, float, int, float, float]],
    *,
    max_sep_m: float = DEFAULT_MAX_SEP_M,
) -> dict[str, list[tuple[float, float, float]]]:
    """Find instants where one player is in two places at once.

    `samples`: iterable of (player_id, time_s, track_id, x_m, y_m) — every
        detection already attributed to a player.
    Returns {player_id: [(time_s, separation_m, n_tracks), ...]} sorted by time,
    listing only the instants that violate physics.
    """
    by_pt: dict[tuple[str, float], list[tuple[int, float, float]]] = defaultdict(list)
    for pid, t, tid, x, y in samples:
        if x is None or y is None:
            continue
        if x != x or y != y:        # NaN (unprojectable)
            continue
        by_pt[(str(pid), float(t))].append((int(tid), float(x), float(y)))

    out: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    for (pid, t), pts in by_pt.items():
        if len(pts) < 2:
            continue
        # widest separation among the co-present detections
        worst = 0.0
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx = pts[i][1] - pts[j][1]
                dy = pts[i][2] - pts[j][2]
                d = (dx * dx + dy * dy) ** 0.5
                if d > worst:
                    worst = d
        if worst > max_sep_m:
            out[pid].append((t, worst, len(pts)))
    return {p: sorted(v) for p, v in out.items()}


def conflict_summary(conflicts: dict[str, list[tuple[float, float, float]]],
                     dt_s: float = 0.1) -> dict[str, dict]:
    """Per-player rollup for the coach: how much time is provably corrupted."""
    out: dict[str, dict] = {}
    for pid, rows in conflicts.items():
        seps = [r[1] for r in rows]
        out[str(pid)] = {
            "conflict_instants": len(rows),
            "conflict_seconds": round(len(rows) * dt_s, 1),
            "median_separation_m": round(sorted(seps)[len(seps) // 2], 1) if seps else 0.0,
            "max_separation_m": round(max(seps), 1) if seps else 0.0,
            "first_s": round(rows[0][0], 1) if rows else None,
            "last_s": round(rows[-1][0], 1) if rows else None,
        }
    return out


def conflicted_times(conflicts: dict[str, list[tuple[float, float, float]]]
                     ) -> dict[str, set[float]]:
    """{player_id: {time_s, ...}} — the instants to EXCLUDE from that player's
    distance/speed/heatmap. Exact float times, matched against the same
    `time_s` values the trajectory rows carry."""
    return {pid: {r[0] for r in rows} for pid, rows in conflicts.items()}


def blame_tracks(
    samples: Iterable[tuple[str, float, int, float, float]],
    conflicts: dict[str, list[tuple[float, float, float]]],
    *,
    top_n: int = 10,
) -> dict[str, list[tuple[int, int]]]:
    """Which TRACKS are implicated in a player's conflicts, worst first.

    The coach fixes labels per tracklet, so tell him where to look: on W8 74% of
    the impossible time sat in 10 tracklets. Returns
    {player_id: [(track_id, n_conflict_instants), ...]}.
    """
    bad_times = conflicted_times(conflicts)
    tally: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for pid, t, tid, x, y in samples:
        pid = str(pid)
        if pid in bad_times and float(t) in bad_times[pid]:
            tally[pid][int(tid)] += 1
    return {pid: sorted(d.items(), key=lambda kv: -kv[1])[:top_n]
            for pid, d in tally.items()}
