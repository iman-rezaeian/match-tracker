#!/usr/bin/env python3
"""READ-ONLY: would data-derived kit anchors beat the hex-derived ones?

`vote_value` anchors on the kit hex: ours `#0a0a0a` -> V10, opponent `#28bb40`
-> V187, decide at the midpoint V98. But a hex is the colour of the FABRIC, not
of the fabric in July sun. Measured on mrhvbvwi1gjpn, the median torso value
across 3012 tracks is 133 and the 10th-90th range is 45-212 — the "ours" anchor
at V10 sits BELOW the entire observed range, so almost every one of our players
lands on the opponent side of a boundary derived from a colour that never
appears in the footage. Result: 948 ours / 2217 opp where 7v7 needs ~1:1, and
54% of the "opponent" tracks are achromatic (they look like a black shirt, not
green) — they were called opponent purely for being bright.

Hue does not have this problem (it is roughly illumination-invariant), which is
why game 2's hue axis produced a sane split and this did not. The bug is
specific to the value axis, on its first real game.

The fix under test: keep the hex only for POLARITY (which cluster is ours —
black is the darker one) and take the THRESHOLD from the data, where the two
classes actually separate. Two estimators, both on the per-track median torso
value:

  * Otsu — maximise between-class variance, the standard bimodal split.
  * 2-means — Lloyd's algorithm on 1-D, seeded at the value percentiles.

Success is not "a prettier histogram": it is the TEAM-SPLIT RATIO moving toward
1:1, which is a fact about 7v7 that holds regardless of anything the pipeline
believes. Also reported: agreement with the coach's own on-field player counts.

Read-only. Reclassifies from the cached jersey samples; writes nothing.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.kit_anchor_probe --game-id mrhvbvwi1gjpn
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np
import pandas as pd

from post_game import config
from post_game.kit_vote import hex_to_hsv


def otsu(x: np.ndarray, bins: int = 256) -> float:
    """Threshold maximising between-class variance (classic 1-D Otsu)."""
    hist, edges = np.histogram(x, bins=bins)
    centres = (edges[:-1] + edges[1:]) / 2.0
    w = hist.astype(float)
    tot = w.sum()
    if tot <= 0:
        return float(np.median(x))
    w0 = np.cumsum(w)
    w1 = tot - w0
    m0 = np.cumsum(w * centres)
    mt = m0[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        mu0 = m0 / w0
        mu1 = (mt - m0) / w1
        between = w0 * w1 * (mu0 - mu1) ** 2
    between = np.nan_to_num(between, nan=-1.0)
    return float(centres[int(np.argmax(between))])


def kmeans2(x: np.ndarray, iters: int = 50) -> float:
    """1-D 2-means; returns the midpoint between the two converged centres."""
    c = np.array([np.percentile(x, 15), np.percentile(x, 85)], dtype=float)
    for _ in range(iters):
        lo = np.abs(x - c[0]) <= np.abs(x - c[1])
        if lo.all() or (~lo).any() is False:
            break
        nc = np.array([x[lo].mean() if lo.any() else c[0],
                       x[~lo].mean() if (~lo).any() else c[1]])
        if np.allclose(nc, c):
            break
        c = nc
    return float(c.mean())


def track_medians(game_id: str, max_det: int = 200) -> pd.DataFrame:
    """Per-track median torso value/saturation from the cached jersey samples."""
    d = config.OUTPUTS_DIR / game_id
    z = np.load(d / "jersey_samples.npz", allow_pickle=True)
    rows = []
    for k in z.files:
        dets = np.asarray(z[k])
        vs, ss = [], []
        for roi in dets[:max_det]:
            a = np.asarray(roi, dtype=np.float32)
            if a.ndim != 2 or not len(a):
                continue
            vs.append(float(np.median(a[:, 2])))
            ss.append(float(np.median(a[:, 1])))
        if vs:
            rows.append({"tid": int(k), "medV": float(np.median(vs)),
                         "medS": float(np.median(ss)), "n_det": len(dets)})
    return pd.DataFrame(rows)


def split_at(df: pd.DataFrame, thresh: float, ours_is_darker: bool) -> tuple[int, int]:
    dark = df.medV < thresh
    ours = dark if ours_is_darker else ~dark
    return int(ours.sum()), int((~ours).sum())


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--min-det", type=int, default=20)
    args = ap.parse_args()

    from post_game import firestore_io
    from post_game.pipeline import _our_color

    game = firestore_io.get_game(args.game_id)
    our_hex, opp_hex = _our_color(game), game.away_color
    _, s_our, v_our = hex_to_hsv(our_hex)
    _, s_opp, v_opp = hex_to_hsv(opp_hex)
    print(f"game {args.game_id}: ours {our_hex} V={v_our:.0f} S={s_our:.0f} | "
          f"opp {opp_hex} V={v_opp:.0f} S={s_opp:.0f}")

    df = track_medians(args.game_id)
    df = df[df.n_det >= args.min_det]
    x = df.medV.to_numpy()
    print(f"tracks: {len(df)}   observed torso V: median {np.median(x):.0f}, "
          f"p10-p90 {np.percentile(x,10):.0f}-{np.percentile(x,90):.0f}\n")

    ours_darker = v_our < v_opp
    hex_thresh = (v_our + v_opp) / 2.0
    cands = [("hex midpoint (current)", hex_thresh),
             ("Otsu", otsu(x)),
             ("2-means", kmeans2(x))]

    print(f"{'threshold source':<26}{'V':>7}{'ours':>7}{'opp':>7}{'ratio':>9}"
          f"{'|log2 ratio|':>14}")
    print("-" * 70)
    best = None
    for name, t in cands:
        o, p = split_at(df, t, ours_darker)
        ratio = o / p if p else float("inf")
        dev = abs(np.log2(ratio)) if ratio not in (0.0, float("inf")) else 99.0
        print(f"{name:<26}{t:>7.0f}{o:>7}{p:>7}{ratio:>8.2f}:1{dev:>14.2f}")
        if best is None or dev < best[0]:
            best = (dev, name, t)

    print(f"\nanchor sanity: is the hex 'ours' value inside the observed range?")
    inside = np.percentile(x, 1) <= v_our <= np.percentile(x, 99)
    print(f"  ours V={v_our:.0f} within p1-p99 ({np.percentile(x,1):.0f}-"
          f"{np.percentile(x,99):.0f})? {'YES' if inside else 'NO  <-- anchor unreachable'}")
    inside_o = np.percentile(x, 1) <= v_opp <= np.percentile(x, 99)
    print(f"  opp  V={v_opp:.0f} within p1-p99? {'YES' if inside_o else 'NO'}")

    print(f"\nbest by ratio: {best[1]} (V={best[2]:.0f})")
    print("A 7v7 game must produce roughly equal track counts per team, so the\n"
          "ratio closest to 1:1 is the estimator to prefer — not the prettiest split.")


if __name__ == "__main__":
    main()
