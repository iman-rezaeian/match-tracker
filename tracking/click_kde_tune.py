#!/usr/bin/env python3
"""Pick the KDE bandwidth honestly: agreement ALONE is a trap.

Widening the kernel always raises split-half agreement, because in the limit
every player's map becomes the same featureless blob and two halves of a blob
agree perfectly. So a bandwidth must be judged on TWO axes at once:

  * reliability -- do two independent halves of one player's clicks agree?
  * discrimination -- do two DIFFERENT players still look different?

The useful bandwidth maximises reliability subject to players staying distinct.
Reported here as a skill score: reliability minus between-player similarity. That
rewards a map that is both stable and specific, and it is the same logic that
stopped the panel-count and teleport metrics earlier in this project -- a number
that improves by destroying the signal it measures is not an improvement.

Also reports Silverman's rule per player, the standard data-driven bandwidth, as
a sanity check on whatever the sweep prefers.

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.click_kde_tune \
        --game-id mrhvbvwi1gjpn
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from tracking.click_kde_research import _load, kde_grid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--trials", type=int, default=150)
    ap.add_argument("--grid", default="12x8")
    args = ap.parse_args()
    gx, gy = (int(v) for v in args.grid.split("x"))

    root = Path(args.dir or f"tracking/outputs/click_samples/{args.game_id}")
    h1, roster, L, W = _load(args.game_id, root, both_halves=False)
    players = {k: v for k, v in h1.items() if len(v["d"]) >= 15}
    rng = np.random.default_rng(0)

    # Silverman's rule of thumb per player, averaged over the two axes.
    sils = []
    for v in players.values():
        d, w = np.array(v["d"]), np.array(v["w"])
        n = len(d)
        for arr in (d, w):
            sd = arr.std(ddof=1)
            iqr = np.subtract(*np.percentile(arr, [75, 25]))
            a = min(sd, iqr / 1.349) if iqr > 0 else sd
            sils.append(0.9 * a * n ** (-1 / 5))
    print(f"grid {gx}x{gy} — {len(players)} players, median "
          f"{int(np.median([len(v['d']) for v in players.values()]))} clicks")
    print(f"Silverman's rule suggests bw ~ {np.median(sils):.1f} m\n")

    print(f"{'bw (m)':>7} {'reliability':>12} {'distinctness':>13} {'skill':>7}")
    print("-" * 44)
    best = None
    for bw in (2, 3, 4, 5, 6, 8, 10, 12, 16):
        rel = []
        for v in players.values():
            d, w = np.array(v["d"]), np.array(v["w"])
            n = len(d)
            loc = []
            for _ in range(args.trials):
                idx = rng.permutation(n)
                a, b = idx[: n // 2], idx[n // 2: 2 * (n // 2)]
                A = kde_grid(d[a], w[a], L, W, gx, gy, bw)
                B = kde_grid(d[b], w[b], L, W, gx, gy, bw)
                if A.std() > 0 and B.std() > 0:
                    loc.append(np.corrcoef(A.ravel(), B.ravel())[0, 1])
            if loc:
                rel.append(np.median(loc))
        grids = {k: kde_grid(np.array(v["d"]), np.array(v["w"]), L, W, gx, gy, bw)
                 for k, v in players.items()}
        ks = list(grids)
        btw = [np.corrcoef(grids[a].ravel(), grids[b].ravel())[0, 1]
               for i, a in enumerate(ks) for b in ks[i + 1:]]
        r, s = float(np.median(rel)), float(np.median(btw))
        skill = r - s
        flag = ""
        if best is None or skill > best[1]:
            best, flag = (bw, skill), ""
        print(f"{bw:>7} {r:12.2f} {1-s:13.2f} {skill:7.2f}")
    print(f"\nbest skill at bw = {best[0]} m")
    print("reliability = split-half agreement for one player")
    print("distinctness = 1 - similarity between different players")
    print("skill = reliability - similarity; a blob scores high on the first and")
    print("        terribly on the second, which is why both are needed.")


if __name__ == "__main__":
    main()
