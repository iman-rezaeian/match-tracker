#!/usr/bin/env python3
"""Can a smarter estimator beat the raw histogram on the SAME clicks?

The premise I got wrong
----------------------
A histogram throws away everything except which cell a click fell in. Two clicks
1 cm apart either side of a boundary land in different cells and contribute
nothing to each other. With ~25 clicks over 24 cells that is hopeless -- but the
weakness is the ESTIMATOR, not the data.

Three ideas, each measured by split-half agreement on the coach's real clicks:

1. **KDE (kernel density)** -- every click contributes a smooth bump, so nearby
   clicks reinforce and the estimate is defined everywhere rather than only where
   a click landed. Bandwidth is the honest knob: too small and it is a histogram
   with extra steps, too large and every player looks the same.

2. **Pooling both halves** -- H1 and H2 mirrored into one frame doubles a
   player's sample. Only valid for a metric that is stable across the match; it
   destroys any half-to-half comparison, so it is a trade rather than a free win.

3. **Shrinkage toward the team** -- a player's grid is pulled toward the squad
   average in proportion to how little data he has. Standard hierarchical/
   empirical-Bayes move: it reduces variance at the cost of a little bias, which
   is exactly the right trade when the alternative is noise.

Scored the same way throughout: split each player's clicks into two disjoint
halves, build the estimate from each, and correlate. Higher agreement means more
of the picture is signal rather than the particular moments clicked.

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.click_kde_research \
        --game-id mrhvbvwi1gjpn
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def _load(game_id: str, root: Path, both_halves: bool = False):
    from post_game import firestore_io
    from post_game.click_orientation import our_net_at_x0_from_keeper
    from post_game.click_samples import load_clicks, to_field

    game = firestore_io.get_game(game_id)
    cal = firestore_io.get_game_calibration(game_id)
    h2 = float(getattr(game, "video_offset_h2_kickoff_s", 0.0) or 0.0)
    pts = to_field(load_clicks(root / "clicks.jsonl"), cal)
    per = lambda t: 1 if t < h2 else 2            # noqa: E731
    net = our_net_at_x0_from_keeper(pts, game.gk_player_id,
                                    float(cal.length_m), per)
    L, W = float(cal.length_m), float(cal.width_m)
    out: dict[str, dict] = {}
    for p in pts:
        if not both_halves and p["video_time_s"] >= h2:
            continue
        flip = not (net or {}).get(per(p["video_time_s"]), True)
        d = (L - p["x_m"]) if flip else p["x_m"]
        w = (W - p["y_m"]) if flip else p["y_m"]
        e = out.setdefault(str(p["player_id"]), {"d": [], "w": []})
        e["d"].append(d)
        e["w"].append(w)
    roster = {q.id: (q.jersey_number, q.name) for q in firestore_io.get_roster()}
    return out, roster, L, W


def kde_grid(d, w, L, W, gx, gy, bw):
    """Gaussian KDE evaluated on a gx x gy grid. bw in metres."""
    ys, xs = np.mgrid[0:gy, 0:gx]
    cy = (ys + 0.5) * (W / gy)
    cx = (xs + 0.5) * (L / gx)
    out = np.zeros((gy, gx))
    for dd, ww in zip(d, w):
        out += np.exp(-(((cx - dd) ** 2 + (cy - ww) ** 2) / (2 * bw * bw)))
    s = out.sum()
    return out / s if s > 0 else out


def hist_grid(d, w, L, W, gx, gy):
    H, _, _ = np.histogram2d(d, w, bins=(gx, gy), range=[[0, L], [0, W]])
    s = H.sum()
    return (H.T / s) if s > 0 else H.T


def agreement(players, L, W, gx, gy, build, trials, rng):
    cs = []
    for v in players.values():
        d, w = np.array(v["d"]), np.array(v["w"])
        n = len(d)
        loc = []
        for _ in range(trials):
            idx = rng.permutation(n)
            a, b = idx[: n // 2], idx[n // 2: 2 * (n // 2)]
            A = build(d[a], w[a], L, W, gx, gy)
            B = build(d[b], w[b], L, W, gx, gy)
            if A.std() > 0 and B.std() > 0:
                loc.append(np.corrcoef(A.ravel(), B.ravel())[0, 1])
        if loc:
            cs.append(np.median(loc))
    return float(np.median(cs)) if cs else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--trials", type=int, default=120)
    args = ap.parse_args()
    root = Path(args.dir or f"tracking/outputs/click_samples/{args.game_id}")
    rng = np.random.default_rng(0)

    h1, roster, L, W = _load(args.game_id, root, both_halves=False)
    players = {k: v for k, v in h1.items() if len(v["d"]) >= 15}
    print(f"H1 only: {len(players)} players, median "
          f"{int(np.median([len(v['d']) for v in players.values()]))} clicks each\n")

    print("=== 1. KDE vs histogram, by grid and bandwidth ===")
    print(f"{'grid':>8} {'histogram':>10} " + "".join(
        f"{'kde bw=' + str(b) + 'm':>12}" for b in (3, 5, 8, 12)))
    for gx, gy in ((4, 3), (6, 4), (12, 8)):
        hh = agreement(players, L, W, gx, gy, hist_grid, args.trials, rng)
        row = f"{gx:>3}x{gy:<4} {hh:10.2f} "
        for bw in (3, 5, 8, 12):
            k = agreement(players, L, W, gx, gy,
                          lambda d, w, L, W, gx, gy, _b=bw: kde_grid(d, w, L, W, gx, gy, _b),
                          args.trials, rng)
            row += f"{k:12.2f}"
        print(row)

    print("\n=== 2. Pooling BOTH halves (more samples, no drift metric) ===")
    both, _, _, _ = _load(args.game_id, root, both_halves=True)
    bp = {k: v for k, v in both.items() if len(v["d"]) >= 15}
    print(f"median clicks/player: H1 {int(np.median([len(v['d']) for v in players.values()]))}"
          f" -> pooled {int(np.median([len(v['d']) for v in bp.values()]))}")
    for gx, gy in ((6, 4), (12, 8)):
        a1 = agreement(players, L, W, gx, gy,
                       lambda d, w, L, W, gx, gy: kde_grid(d, w, L, W, gx, gy, 5),
                       args.trials, rng)
        a2 = agreement(bp, L, W, gx, gy,
                       lambda d, w, L, W, gx, gy: kde_grid(d, w, L, W, gx, gy, 5),
                       args.trials, rng)
        print(f"  {gx}x{gy} kde bw=5: H1 {a1:.2f} -> pooled {a2:.2f}")

    print("\n=== 3. Shrinkage toward the squad average (12x8, kde bw=5) ===")
    gx, gy = 12, 8
    team = np.mean([kde_grid(np.array(v["d"]), np.array(v["w"]), L, W, gx, gy, 5)
                    for v in players.values()], axis=0)

    def shrunk(d, w, L, W, gx, gy, lam):
        k = kde_grid(d, w, L, W, gx, gy, 5)
        return (1 - lam) * k + lam * team

    for lam in (0.0, 0.2, 0.4, 0.6):
        a = agreement(players, L, W, gx, gy,
                      lambda d, w, L, W, gx, gy, _l=lam: shrunk(d, w, L, W, gx, gy, _l),
                      args.trials, rng)
        print(f"  lambda={lam:.1f}  agreement {a:.2f}"
              + ("   <- pure player" if lam == 0 else ""))
    print("\n  NOTE shrinkage inflates agreement by making every player more like")
    print("  the team, so it must be judged on whether players stay DISTINCT too.")

    print("\n=== 4. Do shrunk maps still tell players apart? ===")
    for lam in (0.0, 0.2, 0.4, 0.6):
        gs = {k: shrunk(np.array(v["d"]), np.array(v["w"]), L, W, gx, gy, lam)
              for k, v in players.items()}
        ks = list(gs)
        between = [np.corrcoef(gs[a].ravel(), gs[b].ravel())[0, 1]
                   for i, a in enumerate(ks) for b in ks[i + 1:]]
        print(f"  lambda={lam:.1f}  between-player similarity {np.median(between):.2f}"
              "   (lower = more distinct)")


if __name__ == "__main__":
    main()
