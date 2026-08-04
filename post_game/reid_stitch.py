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
    """Mean HSV over a track's jersey samples.

    Each element is one detection's `sample_jersey_hsv` result — an (N_i, 3) array
    whose row count N_i (pixels sampled) VARIES with box size. So collapse each
    detection to its own (3,) mean first, then average those; stacking the raw
    ragged (N_i, 3) arrays directly raises an inhomogeneous-shape ValueError.
    (Only reached when a track has no OSNet embedding — e.g. the pitch tracker,
    which is motion-only; the boxmot path populated embeddings and skipped here.)
    """
    if not samples:
        return None
    per_det = []
    for s in samples:
        a = np.asarray(s, dtype=np.float32)
        if a.size == 0:
            continue
        if a.ndim == 2:      # (N_i, 3) pixel samples -> per-detection mean
            per_det.append(a.mean(axis=0))
        elif a.ndim == 1:    # already a (3,) vector
            per_det.append(a)
    if not per_det:
        return None
    return np.asarray(per_det, dtype=np.float32).mean(axis=0)


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
    mode: Optional[str] = None,
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

    def _edge_cost(a: int, b: int) -> Optional[float]:
        """Cost of chaining a→b if the pair passes every gate, else None.

        Identical gates + cost formula for both greedy and global modes — only the
        chaining strategy that consumes these differs. `a` precedes `b` in time.
        """
        sa, sb = summ[a], summ[b]
        gap = sb["t0"] - sa["t1"]
        if gap < -0.5:
            return None  # overlap — not a continuation
        if gap > max_gap_s:
            return None
        dx = sb["p0"][0] - sa["p1"][0]
        dy = sb["p0"][1] - sa["p1"][1]
        dist = float(np.hypot(dx, dy))
        max_move = min(config.MAX_PLAUSIBLE_SPEED_MS * max(gap, 0.0) + slack_m,
                       geom_dist_cap_m)
        if dist > max_move:
            return None
        # CANNOT-LINK: never merge two fragments with different confirmed identities.
        ia, ib = comp_ident.get(find(a)), comp_ident.get(find(b))
        if ia is not None and ib is not None and ia != ib:
            return None
        ok, cos = appearance_ok(a, b)
        if not ok:
            return None
        return dist + config.STITCH_GAP_WEIGHT * gap + config.STITCH_APP_WEIGHT * (1.0 - cos)

    stitch_mode = (mode or config.STITCH_MODE or "greedy").lower()

    if stitch_mode == "global":
        # Global min-cost path cover via min-cost FLOW (the GTA-Link formulation).
        # Greedy commits each a to its cheapest successor in start-time order, which
        # orphans a fragment whose only plausible successor was already taken. Flow
        # optimizes TOTAL link cost instead, and — unlike a forced full matching —
        # leaves a fragment UNLINKED when linking it would cost more than the value
        # of the link, so it never fabricates a bad chain to satisfy the solver.
        #
        # Graph: for each fragment f, a node f_out and f_in. Edge f_out→g_in with
        # capacity 1 and cost = gated _edge_cost(f, g) minus a per-link REWARD, so a
        # link is only "worth it" when its cost is below the reward (i.e. a genuinely
        # plausible continuation). A super source→every f_out and every f_in→sink with
        # capacity 1, cost 0, let a fragment start/end a chain freely. Min-cost flow of
        # value = #fragments then selects the globally cheapest set of continuations.
        import networkx as nx
        # REWARD sets how eager we are to link: a link is chosen only if its cost is
        # below it. Use the plausible-move ceiling (speed*max_gap + slack) so any gated
        # edge (all of which are below that geometrically) can be selected, and the
        # solver picks the CHEAPEST consistent set. Scale to int (flow needs ints).
        SCALE = 1000
        reward = int((config.MAX_PLAUSIBLE_SPEED_MS * max_gap_s + slack_m) * SCALE) + 1
        edges = []
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if summ[b]["t0"] - summ[a]["t1"] > max_gap_s:
                    break
                c = _edge_cost(a, b)
                if c is not None:
                    edges.append((a, b, int(round(c * SCALE)) - reward))
        if edges:
            G = nx.DiGraph()
            SRC, SNK = "__src__", "__snk__"
            for t in ids:
                G.add_edge(SRC, ("out", t), capacity=1, weight=0)
                G.add_edge(("in", t), SNK, capacity=1, weight=0)
                # a fragment need not be continued — allow its out/in to pass through
                # at zero cost so the flow can route around it (start/end of a chain).
                G.add_edge(("out", t), SNK, capacity=1, weight=0)
                G.add_edge(SRC, ("in", t), capacity=1, weight=0)
            for a, b, w in edges:
                G.add_edge(("out", a), ("in", b), capacity=1, weight=w)
            G.nodes[SRC]["demand"] = -len(ids)
            G.nodes[SNK]["demand"] = len(ids)
            flow = nx.min_cost_flow(G)
            # union along chosen continuation edges (out,a)->(in,b) with flow 1
            for a in ids:
                for tgt, f in flow.get(("out", a), {}).items():
                    if f == 1 and isinstance(tgt, tuple) and tgt[0] == "in":
                        b = tgt[1]
                        if find(a) != find(b):
                            union(a, b)
    else:
        for i, a in enumerate(ids):
            best_b, best_cost = None, float("inf")
            for b in ids[i + 1:]:
                if summ[b]["t0"] - summ[a]["t1"] > max_gap_s:
                    break  # sorted by t0 → no later b can be closer
                if b in used_succ or find(a) == find(b):
                    continue
                c = _edge_cost(a, b)
                if c is not None and c < best_cost:
                    best_cost, best_b = c, b
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
