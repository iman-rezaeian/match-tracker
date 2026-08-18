#!/usr/bin/env python3
"""The other three refusals, tested rather than asserted.

The coach objected to four limits. The heatmap one is answered (KDE at bw~6 m
lifts 12x8 agreement from 0.15 to 0.67 while keeping players distinct). This
covers the rest:

  A. RANKING players within ~5 m -- the earlier claim used each player's own
     error bar independently. The right test is whether the ORDER is stable:
     resample and see how often two players keep their relative position. A pair
     can be 2 m apart and still order correctly every time if the difference is
     consistent.

  B. DRIFT under ~5 m -- same mistake. Test the half-to-half difference directly
     by resampling within each half, instead of comparing two point estimates.

  C. DISTANCE -- clicks cannot integrate a path, and no estimator changes that.
     But the question behind it is answerable: is this child working harder than
     that one? Two proxies that ARE sampleable:
       * area covered (convex hull of his positions)
       * mean displacement between consecutive samples, which is a lower bound
         on real movement and, crucially, comparable BETWEEN players because
         every player is sampled on the same frame grid.
     Both are scored for reliability the same way. If they rank players stably
     they answer the coaching question without pretending to be metres run.

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.click_deep_research \
        --game-id mrhvbvwi1gjpn
"""

from __future__ import annotations

import argparse
import itertools
from pathlib import Path

import numpy as np

from tracking.click_kde_research import _load


def _boot_mean(vals, trials, rng):
    """Bootstrap distribution of a mean."""
    v = np.asarray(vals, dtype=float)
    n = len(v)
    return np.array([v[rng.integers(0, n, n)].mean() for _ in range(trials)])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--trials", type=int, default=600)
    args = ap.parse_args()
    root = Path(args.dir or f"tracking/outputs/click_samples/{args.game_id}")
    rng = np.random.default_rng(0)

    from post_game import firestore_io
    from post_game.click_orientation import our_net_at_x0_from_keeper
    from post_game.click_samples import load_clicks, to_field

    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    roster = {p.id: (p.jersey_number, p.name) for p in firestore_io.get_roster()}
    h2 = float(getattr(game, "video_offset_h2_kickoff_s", 0.0) or 0.0)
    L, W = float(cal.length_m), float(cal.width_m)
    raw = to_field(load_clicks(root / "clicks.jsonl"), cal)
    per = lambda t: 1 if t < h2 else 2                     # noqa: E731
    net = our_net_at_x0_from_keeper(raw, game.gk_player_id, L, per)

    tidy: dict[str, list[dict]] = {}
    for p in raw:
        flip = not (net or {}).get(per(p["video_time_s"]), True)
        tidy.setdefault(str(p["player_id"]), []).append({
            "t": float(p["video_time_s"]),
            "d": (L - p["x_m"]) if flip else p["x_m"],
            "w": (W - p["y_m"]) if flip else p["y_m"],
            "half": per(p["video_time_s"]),
        })
    h1 = {k: [x for x in v if x["half"] == 1] for k, v in tidy.items()}
    h1 = {k: v for k, v in h1.items() if len(v) >= 15}
    name = lambda pid: roster.get(pid, ("?", pid))[1]      # noqa: E731
    num = lambda pid: roster.get(pid, ("?", pid))[0]       # noqa: E731

    # ---- A. is the ORDER stable, even for close pairs? -------------------
    print("A. RANKING — how often does a pair keep its order under resampling?")
    boots = {k: _boot_mean([x["d"] for x in v], args.trials, rng)
             for k, v in h1.items()}
    means = {k: np.mean([x["d"] for x in v]) for k, v in h1.items()}
    rows = []
    for a, b in itertools.combinations(h1, 2):
        gap = means[a] - means[b]
        agree = float(np.mean((boots[a] - boots[b]) * np.sign(gap) > 0))
        rows.append((abs(gap), agree, a, b))
    rows.sort()
    print(f"   {'gap':>6} {'order held':>11}   pair")
    for gap, agree, a, b in rows[:10]:
        mark = "OK" if agree >= 0.95 else ("weak" if agree >= 0.8 else "NO")
        print(f"   {gap:5.1f}m {100*agree:9.0f}% {mark:>5}  "
              f"#{num(a)} {name(a)} vs #{num(b)} {name(b)}")
    close = [r for r in rows if r[0] < 5.0]
    ok = [r for r in close if r[1] >= 0.95]
    print(f"   => of {len(close)} pairs under 5 m apart, {len(ok)} order "
          f"reliably (>=95%)")

    # ---- B. drift, tested directly --------------------------------------
    print("\nB. DRIFT — bootstrap the H1->H2 difference itself")
    print(f"   {'#':>3} {'name':16s} {'drift':>7} {'95% CI':>16} verdict")
    for pid, v in sorted(tidy.items(), key=lambda kv: kv[0]):
        a = [x["d"] for x in v if x["half"] == 1]
        b = [x["d"] for x in v if x["half"] == 2]
        if len(a) < 8 or len(b) < 5:
            continue
        ba, bb = _boot_mean(a, args.trials, rng), _boot_mean(b, args.trials, rng)
        dif = bb - ba
        lo, hi = np.percentile(dif, [2.5, 97.5])
        real = "REAL" if (lo > 0) == (hi > 0) else "not significant"
        print(f"   {num(pid):>3} {name(pid):16s} {np.mean(dif):+6.1f}m "
              f"[{lo:+5.1f},{hi:+5.1f}]  {real}")

    # ---- C. work-rate proxies -------------------------------------------
    print("\nC. WORK RATE — proxies that ARE sampleable (not metres run)")
    print(f"   {'#':>3} {'name':16s} {'area m2':>9} {'±':>6} "
          f"{'step m':>7} {'±':>6}")
    hull_rel, step_rel = [], []
    for pid, v in h1.items():
        d = np.array([x["d"] for x in v])
        w = np.array([x["w"] for x in v])
        t = np.array([x["t"] for x in v])
        o = np.argsort(t)
        d, w, t = d[o], w[o], t[o]
        # area: bounding ellipse proxy (2 sd in each axis) -- robust with few pts
        area = np.pi * 2 * d.std() * 2 * w.std()
        step = float(np.mean(np.hypot(np.diff(d), np.diff(w)))) if len(d) > 1 else 0.0
        # reliability of each via split-half
        ah, sh = [], []
        for _ in range(120):
            idx = rng.permutation(len(d))
            for part in (idx[: len(d) // 2], idx[len(d) // 2:]):
                pd, pw = d[part], w[part]
                ah.append(np.pi * 2 * pd.std() * 2 * pw.std())
            p1, p2 = idx[: len(d) // 2], idx[len(d) // 2:]
            for part in (p1, p2):
                q = np.sort(part)
                sh.append(float(np.mean(np.hypot(np.diff(d[q]), np.diff(w[q])))))
        a_err = float(np.std(ah[::2]))
        s_err = float(np.std(sh[::2]))
        hull_rel.append(a_err / max(area, 1e-9))
        step_rel.append(s_err / max(step, 1e-9))
        print(f"   {num(pid):>3} {name(pid):16s} {area:9.0f} {a_err:6.0f} "
              f"{step:7.1f} {s_err:6.1f}")
    print(f"   area   typical relative error {100*np.median(hull_rel):.0f}%")
    print(f"   step   typical relative error {100*np.median(step_rel):.0f}%")
    print("\n   'step' is mean distance between consecutive 30 s samples. It is a")
    print("   LOWER BOUND on movement, not distance run, but it is comparable")
    print("   between players because everyone shares the same sample grid.")


if __name__ == "__main__":
    main()
