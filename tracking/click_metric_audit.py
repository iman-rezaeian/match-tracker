#!/usr/bin/env python3
"""Which per-player metrics do the clicks actually support, and at what error?

Why an audit rather than a list
-------------------------------
"Position metrics work" is too coarse. A MEAN needs n samples; a HEATMAP needs
n per CELL, which is a far harder ask from the same clicks. Each candidate metric
is therefore scored the same way -- split-half resampling on the real first-half
clicks -- so the answer is measured on this data instead of assumed from the
sampling model.

The yardstick differs per metric and is stated in the output:
  * distances (mean position, territory edges) in metres, against the ~13 m gap
    between two different players
  * fractions (thirds, side preference) in percentage points
  * heatmaps by how stable the grid is between two halves of the sample
    (correlation), plus how many cells hold any sample at all

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.click_metric_audit \
        --game-id mrhvbvwi1gjpn
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def _load_h1(game_id: str, root: Path):
    from post_game import firestore_io
    from post_game.click_orientation import our_net_at_x0_from_keeper
    from post_game.click_samples import load_clicks, to_field

    game = firestore_io.get_game(game_id)
    cal = firestore_io.get_game_calibration(game_id)
    h2 = float(getattr(game, "video_offset_h2_kickoff_s", 0.0) or 0.0)
    pts = to_field(load_clicks(root / "clicks.jsonl"), cal)
    net = our_net_at_x0_from_keeper(pts, game.gk_player_id, float(cal.length_m),
                                   lambda t: 1 if t < h2 else 2)
    flip = not (net or {}).get(1, True)
    L, W = float(cal.length_m), float(cal.width_m)
    out: dict[str, dict] = {}
    for p in pts:
        if p["video_time_s"] >= h2:
            continue
        d = (L - p["x_m"]) if flip else p["x_m"]
        w = (W - p["y_m"]) if flip else p["y_m"]
        out.setdefault(str(p["player_id"]), {"d": [], "w": []})
        out[str(p["player_id"])]["d"].append(d)
        out[str(p["player_id"])]["w"].append(w)
    roster = {q.id: (q.jersey_number, q.name) for q in firestore_io.get_roster()}
    return out, roster, L, W


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--trials", type=int, default=300)
    args = ap.parse_args()

    root = Path(args.dir or f"tracking/outputs/click_samples/{args.game_id}")
    by, roster, L, W = _load_h1(args.game_id, root)
    rng = np.random.default_rng(0)
    players = {k: v for k, v in by.items() if len(v["d"]) >= 15}
    print(f"FIRST HALF — {len(players)} players with >=15 clicks\n")

    # metric definitions: name -> (fn(d,w) -> scalar, unit)
    def mean_depth(d, w):
        return d.mean()

    def mean_width(d, w):
        return w.mean()

    def p90_depth(d, w):
        return np.quantile(d, 0.90)

    def p10_depth(d, w):
        return np.quantile(d, 0.10)

    def depth_range(d, w):
        return np.quantile(d, 0.90) - np.quantile(d, 0.10)

    def pct_att(d, w):
        return 100.0 * (d >= L * 2 / 3).mean()

    def pct_def(d, w):
        return 100.0 * (d < L / 3).mean()

    def pct_left(d, w):
        return 100.0 * (w < W / 3).mean()

    metrics = [
        ("mean depth", mean_depth, "m"),
        ("mean width", mean_width, "m"),
        ("p10 depth (deepest)", p10_depth, "m"),
        ("p90 depth (highest)", p90_depth, "m"),
        ("depth range (roam)", depth_range, "m"),
        ("% attacking third", pct_att, "pp"),
        ("% defensive third", pct_def, "pp"),
        ("% left channel", pct_left, "pp"),
    ]

    print(f"{'metric':22s} {'median err':>11} {'worst':>8}   verdict")
    print("-" * 72)
    for name, fn, unit in metrics:
        errs = []
        for v in players.values():
            d, w = np.array(v["d"]), np.array(v["w"])
            n = len(d)
            gaps = []
            for _ in range(args.trials):
                idx = rng.permutation(n)
                a, b = idx[: n // 2], idx[n // 2: 2 * (n // 2)]
                gaps.append(abs(fn(d[a], w[a]) - fn(d[b], w[b])))
            errs.append(np.median(gaps) / 2.0)
        med, worst = float(np.median(errs)), float(np.max(errs))
        if unit == "m":
            ok = "USABLE" if med <= 3.5 else ("MARGINAL" if med <= 6 else "TOO NOISY")
        else:
            ok = "USABLE" if med <= 8 else ("MARGINAL" if med <= 15 else "TOO NOISY")
        print(f"{name:22s} {med:8.1f} {unit:2s} {worst:6.1f} {unit:2s}   {ok}")

    # ---- heatmap stability -------------------------------------------------
    print("\nHEATMAP — how stable is the grid between two halves of the sample?")
    print(f"{'grid':>9} {'cells hit':>10} {'clicks/cell':>12} {'corr':>7}   verdict")
    print("-" * 60)
    # Scores the SHIPPED estimator (post_game.click_samples.kde_heatmap) rather
    # than a histogram built here. This audit originally rolled its own binning,
    # so it kept reporting the pre-KDE numbers after the pipeline had moved on --
    # a measurement of code that no longer ran.
    from post_game.click_samples import kde_heatmap
    for gx, gy in ((3, 2), (4, 3), (6, 4), (12, 8)):
        corrs, hits, per = [], [], []
        for v in players.values():
            d, w = np.array(v["d"]), np.array(v["w"])
            n = len(d)
            H, _, _ = np.histogram2d(d, w, bins=(gx, gy),
                                     range=[[0, L], [0, W]])
            hits.append(100.0 * (H > 0).sum() / H.size)
            per.append(n / H.size)
            cs = []
            for _ in range(args.trials // 3):
                idx = rng.permutation(n)
                a, b = idx[: n // 2], idx[n // 2: 2 * (n // 2)]
                A = kde_heatmap(d[a], w[a], L, W, (gx, gy))
                B = kde_heatmap(d[b], w[b], L, W, (gx, gy))
                if A.std() > 0 and B.std() > 0:
                    cs.append(np.corrcoef(A.ravel(), B.ravel())[0, 1])
            corrs.append(np.median(cs) if cs else 0.0)
        c = float(np.median(corrs))
        verdict = ("USABLE" if c >= 0.7 else
                   "MARGINAL" if c >= 0.5 else "TOO NOISY")
        print(f"{gx:>4}x{gy:<4} {np.median(hits):8.0f}% {np.median(per):11.1f} "
              f"{c:7.2f}   {verdict}")
    print("\ncorr = how well two independent halves of the same player's clicks")
    print("agree on the grid. Below ~0.5 the picture is mostly sampling noise.")


if __name__ == "__main__":
    main()
