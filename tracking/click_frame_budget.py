#!/usr/bin/env python3
"""How few FRAMES would have given the same first-half numbers?

The question
------------
The coach clicked 48 of the 50 first-half frames. If 20 would have produced the
same answers, the other 28 were free labour and every future game should be
sampled at a wider interval.

Method
------
Treat the full 48-frame result as the reference, then repeatedly draw a random
SUBSET of k frames, recompute every metric from just those clicks, and measure
how far the subset lands from the reference. Frames are drawn as whole units --
not individual clicks -- because a frame is what actually costs the coach time,
and clicks within one frame are correlated (same instant, same phase of play).

Two things are reported per k:
  * error vs the full-sample answer, in metres or percentage points
  * whether the positional ORDER of the squad survives (Spearman correlation
    against the full-sample depth ranking)

The subsets are drawn EVENLY across the half rather than uniformly at random,
mirroring how a wider interval would actually sample -- every second frame, every
third, and so on. Random subsets flatter the result by sometimes clustering.

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.click_frame_budget \
        --game-id mrhvbvwi1gjpn
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--trials", type=int, default=200)
    args = ap.parse_args()

    from post_game import firestore_io
    from post_game.click_orientation import our_net_at_x0_from_keeper
    from post_game.click_samples import kde_heatmap, load_clicks, to_field

    root = Path(args.dir or f"tracking/outputs/click_samples/{args.game_id}")
    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    h2 = float(getattr(game, "video_offset_h2_kickoff_s", 0.0) or 0.0)
    L, W = float(cal.length_m), float(cal.width_m)

    pts = to_field(load_clicks(root / "clicks.jsonl"), cal)
    net = our_net_at_x0_from_keeper(pts, game.gk_player_id, L,
                                    lambda t: 1 if t < h2 else 2)
    flip = not (net or {}).get(1, True)
    rows = []
    for p in pts:
        if p["video_time_s"] >= h2:
            continue
        rows.append({
            "t": round(float(p["video_time_s"]), 2),
            "pid": str(p["player_id"]),
            "d": (L - p["x_m"]) if flip else p["x_m"],
            "w": (W - p["y_m"]) if flip else p["y_m"],
        })
    frames = sorted({r["t"] for r in rows})
    print(f"FIRST HALF: {len(rows)} clicks over {len(frames)} frames "
          f"({len(rows)/len(frames):.1f} clicks/frame)\n")

    MIN_N = 8

    def metrics(sub):
        """Per-player metrics from a subset of clicks."""
        by: dict[str, dict] = {}
        for r in sub:
            e = by.setdefault(r["pid"], {"d": [], "w": []})
            e["d"].append(r["d"])
            e["w"].append(r["w"])
        out = {}
        for pid, v in by.items():
            if len(v["d"]) < MIN_N:
                continue
            d, w = np.array(v["d"]), np.array(v["w"])
            out[pid] = {
                "depth": d.mean(), "width": w.mean(),
                "p90": np.quantile(d, 0.90), "p10": np.quantile(d, 0.10),
                "att": 100.0 * (d >= L * 2 / 3).mean(),
                "dfn": 100.0 * (d < L / 3).mean(),
                "area": np.pi * 2 * d.std() * 2 * w.std(),
                "hm": kde_heatmap(d, w, L, W, (12, 8)),
            }
        return out

    ref = metrics(rows)
    ref_ids = sorted(ref)
    ref_order = np.array([ref[p]["depth"] for p in ref_ids])
    print(f"reference: {len(ref)} players with >={MIN_N} clicks\n")

    rng = np.random.default_rng(0)
    print(f"{'frames':>7} {'interval':>9} {'depth':>7} {'width':>7} {'p90':>6} "
          f"{'%att':>6} {'area':>6} {'heatmap':>8} {'order':>6} {'players':>8}")
    print("-" * 82)
    for k in (4, 6, 8, 10, 12, 16, 20, 24, 32, 40):
        if k > len(frames):
            continue
        e_d, e_w, e_p, e_a, e_ar, e_hm, sp, npl = [], [], [], [], [], [], [], []
        for _ in range(args.trials):
            # even sampling with a random phase = what a wider interval gives
            off = rng.integers(0, max(1, len(frames) // k))
            pick = set(np.array(frames)[np.linspace(
                off, len(frames) - 1, k).round().astype(int)].tolist())
            sub = [r for r in rows if r["t"] in pick]
            m = metrics(sub)
            shared = [p for p in ref_ids if p in m]
            npl.append(len(m))
            if len(shared) < 3:
                continue
            e_d.append(np.median([abs(m[p]["depth"] - ref[p]["depth"]) for p in shared]))
            e_w.append(np.median([abs(m[p]["width"] - ref[p]["width"]) for p in shared]))
            e_p.append(np.median([abs(m[p]["p90"] - ref[p]["p90"]) for p in shared]))
            e_a.append(np.median([abs(m[p]["att"] - ref[p]["att"]) for p in shared]))
            e_ar.append(np.median([abs(m[p]["area"] - ref[p]["area"])
                                   / max(ref[p]["area"], 1e-9) for p in shared]))
            e_hm.append(np.median([np.corrcoef(m[p]["hm"].ravel(),
                                               ref[p]["hm"].ravel())[0, 1]
                                   for p in shared]))
            a = np.array([m[p]["depth"] for p in shared])
            b = np.array([ref[p]["depth"] for p in shared])
            ra = np.argsort(np.argsort(a))
            rb = np.argsort(np.argsort(b))
            sp.append(np.corrcoef(ra, rb)[0, 1])
        if not e_d:
            continue
        print(f"{k:>7} {50/k*30/60:8.1f}m {np.median(e_d):6.1f}m "
              f"{np.median(e_w):6.1f}m {np.median(e_p):5.1f}m "
              f"{np.median(e_a):5.1f}p {100*np.median(e_ar):5.0f}% "
              f"{np.median(e_hm):8.2f} {np.median(sp):6.2f} "
              f"{np.median(npl):8.0f}")

    print("\ninterval = minutes of match between sampled frames")
    print("depth/width/p90 = error vs the 48-frame answer, in metres")
    print("%att = error in attacking-third share, percentage points")
    print("heatmap/order = agreement with the 48-frame answer (1.0 = identical)")
    print("players = how many still clear the 8-click bar")


if __name__ == "__main__":
    main()
