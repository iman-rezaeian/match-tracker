#!/usr/bin/env python3
"""How accurate are the FIRST-HALF per-player positions, from the clicks alone?

Why measure it this way
-----------------------
The 7%-at-50-clicks figure quoted earlier came from SIMULATED sampling of
tracker trajectories. It is a reasonable prior but it is not this data: it
assumed random instants, whereas the coach clicks whoever he can identify at a
fixed grid of frames, and it says nothing about how much of the pitch each child
actually covers.

This estimates the error from the clicks themselves, by SPLIT-HALF resampling:
repeatedly divide one player's H1 clicks into two random halves, compute the mean
position of each, and look at how far the two estimates sit apart. That spread is
a direct read on sampling noise -- no ground truth needed, and no assumption
about how the clicks were chosen. The standard error of the full-sample mean is
then that half-sample gap scaled by 1/sqrt(2) per half, i.e. sd/sqrt(n).

Reported against two yardsticks that make the number meaningful:
  * the player's OWN positional spread (is the estimate tight relative to how
    much he roams?)
  * the gap between two DIFFERENT players (can we tell these children apart?)

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.click_h1_accuracy \
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
    ap.add_argument("--trials", type=int, default=400)
    ap.add_argument("--half", choices=["1", "2", "both"], default="both",
                    help="which half to score; 'both' pools the whole game")
    args = ap.parse_args()

    from post_game import firestore_io
    from post_game.click_orientation import our_net_at_x0_from_keeper
    from post_game.click_samples import load_clicks, to_field

    root = Path(args.dir or f"tracking/outputs/click_samples/{args.game_id}")
    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    roster = {p.id: (p.jersey_number, p.name) for p in firestore_io.get_roster()}
    h2 = float(getattr(game, "video_offset_h2_kickoff_s", 0.0) or 0.0)

    pts = to_field(load_clicks(root / "clicks.jsonl"), cal)
    net = our_net_at_x0_from_keeper(pts, game.gk_player_id,
                                   float(cal.length_m), lambda t: 1 if t < h2 else 2)
    # Pooling both halves is safe for a POSITION estimate only because each half
    # is mirrored into a common frame first; without that flip the two halves
    # would disagree by the length of the pitch and the "error" would be the end
    # change. It is NOT safe for drift, which needs the halves kept apart.
    L, W = float(cal.length_m), float(cal.width_m)
    per = (lambda t: 1 if t < h2 else 2)
    if args.half == "1":
        h1 = [p for p in pts if p["video_time_s"] < h2]
    elif args.half == "2":
        h1 = [p for p in pts if p["video_time_s"] >= h2]
    else:
        h1 = list(pts)
    for p in h1:
        flip = not (net or {}).get(per(p["video_time_s"]), True)
        p["d"] = (L - p["x_m"]) if flip else p["x_m"]
        p["w"] = (W - p["y_m"]) if flip else p["y_m"]

    by: dict[str, list[dict]] = {}
    for p in h1:
        by.setdefault(str(p["player_id"]), []).append(p)

    rng = np.random.default_rng(0)
    rows = []
    for pid, ps in by.items():
        d = np.array([p["d"] for p in ps])
        w = np.array([p["w"] for p in ps])
        n = len(d)
        if n < 8:
            continue
        # split-half: two disjoint random halves, gap between their means
        gaps = []
        for _ in range(args.trials):
            idx = rng.permutation(n)
            a, b = idx[: n // 2], idx[n // 2: 2 * (n // 2)]
            gaps.append(np.hypot(d[a].mean() - d[b].mean(), w[a].mean() - w[b].mean()))
        # each half holds n/2 samples; the full-sample standard error is the
        # half-to-half gap divided by 2 (two independent halves, each sqrt(2)
        # noisier than the whole).
        se = float(np.median(gaps)) / 2.0
        rows.append({
            "pid": pid, "n": n, "se": se,
            "d": float(d.mean()), "w": float(w.mean()),
            "roam": float(np.hypot(d.std(), w.std())),
        })

    # how far apart are two different players, on average?
    cent = np.array([[r["d"], r["w"]] for r in rows])
    sep = [np.hypot(*(cent[i] - cent[j]))
           for i in range(len(cent)) for j in range(i + 1, len(cent))]
    med_sep = float(np.median(sep)) if sep else float("nan")

    lbl={"1":"FIRST HALF","2":"SECOND HALF","both":"FULL GAME"}[args.half]
    print(f"{lbl} — {len(h1)} clicks, {len(rows)} players with >=8\n")
    print(f"{'#':>3} {'name':17s} {'n':>3} {'pos err':>8} {'roam':>6} "
          f"{'err/roam':>9} {'err/sep':>8}")
    print("-" * 62)
    for r in sorted(rows, key=lambda r: r["se"]):
        num, nm = roster.get(r["pid"], ("?", r["pid"]))
        print(f"{num:>3} {nm:17s} {r['n']:>3} {r['se']:6.1f} m {r['roam']:5.1f} m "
              f"{100*r['se']/max(r['roam'],1e-9):8.0f}% {100*r['se']/med_sep:7.0f}%")

    ses = np.array([r["se"] for r in rows])
    print(f"\nmedian position error: {np.median(ses):.1f} m  "
          f"(range {ses.min():.1f}-{ses.max():.1f} m)")
    print(f"median gap between two different players: {med_sep:.1f} m")
    print(f"=> a player's mean position is pinned to about "
          f"{100*np.median(ses)/med_sep:.0f}% of the distance between two players")
    print("\nRead 'pos err' as the 1-sigma uncertainty on that player's AVERAGE")
    print("position. It is not how much he moves (that is 'roam'), and it says")
    print("nothing about distance run, which clicks cannot measure.")


if __name__ == "__main__":
    main()
