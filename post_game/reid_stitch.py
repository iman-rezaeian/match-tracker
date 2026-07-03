"""Tracklet stitching — merge fragmented tracks of the same physical player.

The tracker (BoT-SORT) breaks a single player into many short fragments across
occlusions, tile boundaries, and crossings (~100 fragments/player over a game).
Identity assignment is far easier on a handful of long tracklets than on
hundreds of fragments, so we stitch first.

Two fragments A→B are merged when:
  * B starts shortly after A ends (small temporal gap), and
  * the A-end → B-start move is physically plausible (<= MAX_PLAUSIBLE_SPEED_MS,
    with a small slack radius for near-zero gaps), and
  * their appearance agrees — OSNet Re-ID cosine similarity (preferred) or
    jersey-HSV similarity (fallback when embeddings are absent).

Greedy chaining (each fragment links to at most one successor / predecessor)
produces player-consistent tracklets. Output: {track_id: tracklet_id}.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from . import config

log = logging.getLogger(__name__)


def _track_summaries(tracks_df: pd.DataFrame, track_ids: set[int]) -> dict[int, dict]:
    """Per-track start/end time + start/end field position (foot, meters)."""
    out: dict[int, dict] = {}
    for tid, sub in tracks_df.groupby("track_id"):
        tid = int(tid)
        if tid not in track_ids:
            continue
        sub = sub.sort_values("time_s")
        t = sub["time_s"].to_numpy()
        x = sub["x_m"].to_numpy()
        y = sub["y_m"].to_numpy()
        out[tid] = {
            "t0": float(t[0]), "t1": float(t[-1]),
            "p0": (float(x[0]), float(y[0])),
            "p1": (float(x[-1]), float(y[-1])),
            "n": int(len(sub)),
        }
    return out


def _hsv_mean(samples: list) -> Optional[np.ndarray]:
    if not samples:
        return None
    arr = np.asarray([np.asarray(s, dtype=np.float32) for s in samples], dtype=np.float32)
    return arr.mean(axis=0) if len(arr) else None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def stitch_tracklets(
    tracks_df: pd.DataFrame,
    team_of_track: dict[int, int],
    track_embeddings: Optional[dict[int, np.ndarray]] = None,
    track_jersey_samples: Optional[dict[int, list]] = None,
    *,
    target_team: int = 0,
    max_gap_s: float = config.STITCH_MAX_GAP_S,
    appearance_thresh: float = config.STITCH_APPEARANCE_COS,
    slack_m: float = config.STITCH_SLACK_M,
    geom_dist_cap_m: float = config.STITCH_DIST_CAP_M,
    must_link: Optional[dict[int, object]] = None,
    must_link_dist_cap_m: float = config.STITCH_DIST_CAP_M,
) -> dict[int, int]:
    """Return {track_id: tracklet_id} merging `target_team` fragments.

    Tracks not in `target_team` (opponents/refs/unknown) are left as their own
    singleton tracklets. Appearance uses Re-ID embeddings if available, else
    falls back to jersey-HSV; if neither, gating is purely spatiotemporal.

    Identity constraints (iterative_identity coupling):
      * ``must_link`` maps track_id → an identity label (e.g. player_id). It is
        applied in two ways:
          - MUST-LINK pre-pass: fragments sharing an identity are chained in time
            order, bridging gaps beyond ``max_gap_s`` that geometry alone can't
            (the player left frame and returned), gated only by no-overlap and
            ``must_link_dist_cap_m`` — since a confident identity says it IS the
            same player.
          - CANNOT-LINK: the geometric pass never merges two fragments carrying
            different identities (cost = +inf).
      Fragments with no identity label are unconstrained. Passing ``must_link``
      as None (default) reproduces the original geometry-only behaviour exactly.
    """
    if "x_m" not in tracks_df.columns or tracks_df.empty:
        return {int(t): int(t) for t in tracks_df.get("track_id", pd.Series([], dtype=int)).unique()}

    track_embeddings = track_embeddings or {}
    track_jersey_samples = track_jersey_samples or {}

    our = {int(t) for t, team in team_of_track.items() if team == target_team}
    summ = _track_summaries(tracks_df, our)
    # Order candidate fragments by start time so we always link forward in time.
    ids = sorted(summ.keys(), key=lambda t: summ[t]["t0"])

    parent: dict[int, int] = {t: t for t in ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Identity of each union component (root → label), seeded from must_link.
    # Used for the MUST-LINK pre-pass and the CANNOT-LINK guard below.
    must_link = must_link or {}
    comp_ident: dict[int, object] = {}
    for t in ids:
        lbl = must_link.get(t)
        if lbl is not None:
            comp_ident[t] = lbl

    def union(a: int, b: int) -> None:
        """Merge b's component into a's, carrying any identity label forward."""
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        parent[rb] = ra
        lbl = comp_ident.pop(rb, None)
        if lbl is not None and ra not in comp_ident:
            comp_ident[ra] = lbl

    # ---- MUST-LINK pre-pass -------------------------------------------------
    # Chain same-identity fragments in time order across arbitrary gaps (the
    # player left frame and returned), gated only by no-overlap + dist-cap.
    if must_link:
        by_ident: dict[object, list[int]] = {}
        for t in ids:
            lbl = must_link.get(t)
            if lbl is not None:
                by_ident.setdefault(lbl, []).append(t)
        for lbl, members in by_ident.items():
            members.sort(key=lambda t: summ[t]["t0"])
            prev = members[0]
            for cur in members[1:]:
                sp, sc = summ[prev], summ[cur]
                gap = sc["t0"] - sp["t1"]
                if gap < -0.5:
                    continue  # temporal overlap → can't be the same instance; skip
                dist = float(np.hypot(sc["p0"][0] - sp["p1"][0], sc["p0"][1] - sp["p1"][1]))
                if dist > must_link_dist_cap_m:
                    prev = cur  # too far to bridge even for a confident identity
                    continue
                union(prev, cur)
                prev = cur

    used_succ: set[int] = set()  # a fragment already chained as someone's successor
    hsv_cache: dict[int, Optional[np.ndarray]] = {}

    def appearance_ok(a: int, b: int) -> tuple[bool, float]:
        ea, eb = track_embeddings.get(a), track_embeddings.get(b)
        if ea is not None and eb is not None:
            c = _cosine(ea, eb)
            return (c >= appearance_thresh, c)
        # HSV fallback: looser threshold on normalized HSV-mean cosine
        if a not in hsv_cache:
            hsv_cache[a] = _hsv_mean(track_jersey_samples.get(a, []))
        if b not in hsv_cache:
            hsv_cache[b] = _hsv_mean(track_jersey_samples.get(b, []))
        ha, hb = hsv_cache[a], hsv_cache[b]
        if ha is not None and hb is not None:
            c = _cosine(ha, hb)
            return (c >= config.STITCH_HSV_COS, c)
        return (True, 0.0)  # no appearance signal → rely on spatiotemporal gate

    for i, a in enumerate(ids):
        sa = summ[a]
        best_b, best_cost = None, float("inf")
        for b in ids[i + 1:]:
            sb = summ[b]
            gap = sb["t0"] - sa["t1"]
            if gap < -0.5:
                continue  # b starts before a ends (overlap) — not a continuation
            if gap > max_gap_s:
                break  # ids sorted by t0 → no later b can be closer
            if b in used_succ:
                continue
            # plausible displacement during the gap
            dx = sb["p0"][0] - sa["p1"][0]
            dy = sb["p0"][1] - sa["p1"][1]
            dist = float(np.hypot(dx, dy))
            max_move = min(config.MAX_PLAUSIBLE_SPEED_MS * max(gap, 0.0) + slack_m,
                           geom_dist_cap_m)
            if dist > max_move:
                continue
            # CANNOT-LINK: never merge two fragments with different confirmed
            # identities, however geometrically plausible the move looks.
            ia, ib = comp_ident.get(find(a)), comp_ident.get(find(b))
            if ia is not None and ib is not None and ia != ib:
                continue
            ok, cos = appearance_ok(a, b)
            if not ok:
                continue
            if find(a) == find(b):
                continue
            cost = dist + config.STITCH_GAP_WEIGHT * gap + config.STITCH_APP_WEIGHT * (1.0 - cos)
            if cost < best_cost:
                best_cost, best_b = cost, b
        if best_b is not None:
            union(a, best_b)
            used_succ.add(best_b)

    # Build {track_id: tracklet_id} for ALL tracks (non-our-team = singleton).
    mapping: dict[int, int] = {}
    for t in tracks_df["track_id"].unique():
        t = int(t)
        mapping[t] = find(t) if t in parent else t
    return mapping


def stitch_stats(mapping: dict[int, int], team_of_track: dict[int, int], target_team: int = 0) -> dict:
    """Summary for logging: how much fragmentation we collapsed."""
    our = [t for t, team in team_of_track.items() if team == target_team]
    our_tracklets = {mapping.get(int(t), int(t)) for t in our}
    sizes: dict[int, int] = {}
    for t in our:
        r = mapping.get(int(t), int(t))
        sizes[r] = sizes.get(r, 0) + 1
    multi = sum(1 for v in sizes.values() if v > 1)
    return {
        "our_fragments": len(our),
        "our_tracklets": len(our_tracklets),
        "merged_tracklets": multi,
        "largest_tracklet_fragments": max(sizes.values()) if sizes else 0,
    }
