#!/usr/bin/env python3
"""Spatio-temporal stitch probe — measure how far PURE motion/position continuity
(no appearance) merges the raw tracklets, vs the pipeline's appearance-weighted
stitch. Read-only on the cached tracks_raw.parquet; no GPU, no rerun.

Diagnosis context (METRICS_RELEVANCE_PLAN.md): at 8K detection is ~86% but raw
tracking shatters into ~2887 fragments; the pipeline's reid_stitch (gap<=10s +
appearance, STITCH_APP_WEIGHT=5.0) merged our 1537 -> 499. Appearance can't
discriminate identical kits, so this asks: does dropping appearance and/or
loosening the gap cap merge materially more, using fixed-camera continuity alone?

Run:
  python -m tracking.st_stitch_probe --game-id mqcf9axlvtuyt
"""
from __future__ import annotations
import argparse
import numpy as np
import pandas as pd


def tracklet_endpoints(df: pd.DataFrame, k: int = 5) -> dict[int, dict]:
    """Per track_id: denoised start/end (t, x_m, y_m) using median of first/last k valid pts."""
    out = {}
    for tid, g in df.groupby("track_id"):
        g = g.sort_values("time_s")
        xm, ym, t = g["x_m"].to_numpy(), g["y_m"].to_numpy(), g["time_s"].to_numpy()
        ok = np.isfinite(xm) & np.isfinite(ym)
        if ok.sum() < 1:
            continue
        xm, ym, t = xm[ok], ym[ok], t[ok]
        kk = min(k, len(t))
        out[tid] = dict(
            t0=float(t[0]), t1=float(t[-1]), dur=float(t[-1] - t[0]), n=int(len(t)),
            x0=float(np.median(xm[:kk])), y0=float(np.median(ym[:kk])),
            x1=float(np.median(xm[-kk:])), y1=float(np.median(ym[-kk:])),
        )
    return out


def greedy_st_stitch(eps: dict[int, dict], max_gap_s: float, speed_ms: float,
                     slack_m: float, gap_weight: float, dist_cap_m: float = float("inf"),
                     allowed: set[int] | None = None) -> dict[int, int]:
    """Greedy one-to-one continuity chaining. Returns track_id -> chain_root_id."""
    tids = sorted((t for t in eps if allowed is None or t in allowed), key=lambda i: eps[i]["t0"])
    root = {t: t for t in tids}            # chain root each tracklet currently belongs to
    tail = {t: t for t in tids}            # current END tracklet of the chain rooted at t
    used_succ: set[int] = set()            # tracklets already consumed as a successor
    # For each tracklet (by start), attach it to the best compatible chain-tail before it.
    chain_tails = []                       # list of root ids whose tail can still extend
    for b in tids:
        eb = eps[b]
        best, best_cost = None, None
        for r in chain_tails:
            a = tail[r]
            ea = eps[a]
            gap = eb["t0"] - ea["t1"]
            if gap < -0.5 or gap > max_gap_s:        # overlap or too far in time
                continue
            dist = np.hypot(eb["x0"] - ea["x1"], eb["y0"] - ea["y1"])
            if dist > min(speed_ms * max(gap, 0.0) + slack_m, dist_cap_m):
                continue
            cost = dist + gap_weight * gap
            if best_cost is None or cost < best_cost:
                best, best_cost = r, cost
        if best is not None:
            root[b] = best
            tail[best] = b                  # chain now ends at b
            used_succ.add(b)
        else:
            chain_tails.append(b)           # b starts a new chain
    # path-compress roots
    def find(x):
        while root[x] != x:
            x = root[x]
        return x
    return {t: find(t) for t in tids}


def summarize(eps, mapping, label):
    from collections import defaultdict
    members = defaultdict(list)
    for t, r in mapping.items():
        members[r].append(t)
    # chain span = min t0 .. max t1 of members
    spans = []
    for r, ms in members.items():
        t0 = min(eps[m]["t0"] for m in ms)
        t1 = max(eps[m]["t1"] for m in ms)
        spans.append(t1 - t0)
    spans = np.array(sorted(spans, reverse=True))
    total_person_s = spans.sum()
    cum = np.cumsum(spans)
    n90 = int(np.searchsorted(cum, 0.9 * total_person_s) + 1)
    print(f"  {label:<26} chains={len(members):>4}  median_span={np.median(spans):>5.0f}s  "
          f">=120s={int((spans>=120).sum()):>3}  >=300s={int((spans>=300).sum()):>3}  "
          f"chains_for_90%_time={n90:>3}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    args = ap.parse_args()
    import sys, os
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from post_game import firestore_io, config
    from post_game.calibration import FieldProjector

    df = pd.read_parquet(config.OUTPUTS_DIR / args.game_id / "tracks_raw.parquet")
    cal = firestore_io.get_game_calibration(args.game_id)
    proj = FieldProjector(cal)
    xy = proj.pixel_to_field_batch(df[["foot_x_eq", "foot_y_eq"]].to_numpy())
    df["x_m"], df["y_m"] = xy[:, 0], xy[:, 1]
    # keep on/near field only (drops above-horizon NaN + way-off-field)
    L, W = cal.length_m, cal.width_m
    on = (df["x_m"] >= -3) & (df["x_m"] <= L + 3) & (df["y_m"] >= -3) & (df["y_m"] <= W + 3)
    df = df.loc[on]
    eps = tracklet_endpoints(df)
    print(f"raw tracklets (on/near field, finite proj): {len(eps)}  "
          f"(pipeline saw 2887 total; team-classified 1537 ours -> 499 after appearance-stitch)")
    print(f"current config: max_gap={config.STITCH_MAX_GAP_S}s speed={config.MAX_PLAUSIBLE_SPEED_MS} "
          f"slack={config.STITCH_SLACK_M} (appearance weight={config.STITCH_APP_WEIGHT}, NOT used here)\n")

    print("PURE spatio-temporal (no appearance), gap sweep:")
    for gap in [config.STITCH_MAX_GAP_S, 20.0, 30.0, 60.0]:
        m = greedy_st_stitch(eps, max_gap_s=gap, speed_ms=config.MAX_PLAUSIBLE_SPEED_MS,
                             slack_m=config.STITCH_SLACK_M, gap_weight=config.STITCH_GAP_WEIGHT)
        summarize(eps, m, f"max_gap={gap:.0f}s")


if __name__ == "__main__":
    main()
