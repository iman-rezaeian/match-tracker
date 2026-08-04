#!/usr/bin/env python3
"""Read-only SWAP gauge: measure ID-swap contamination in a cached track set.

The pitch tracker's fragment win (775->90) was partly achieved by GLUING
different players into one track (an our-player[green kit] and an opponent
[blue kit] under the same track id). Fragment count and named-coverage both
HIDE this -- a swapped track still looks like "one long track" and, if the
blue frames win the color vote, classifies cleanly as opponent. The honest
gauge is the MIXED-SECOND FRACTION: the share of colored track-seconds spent
in tracks that contain BOTH substantial green and substantial blue frames.

This reads a game's jersey_samples npz (per-track lists of per-frame HSV pixel
arrays) and, for each track, classifies every frame-sample green / blue / other
by its high-saturation median hue, then buckets each track as pure-green,
pure-blue, or MIXED. ZERO Firestore writes; ZERO pipeline coupling.

Usage:
  python -m tracking.eval_swap_mix --game-id mri01pvelv46d               # live jersey_samples.npz
  python -m tracking.eval_swap_mix --game-id mri01pvelv46d --npz pitch   # jersey_samples.pitch.npz
  python -m tracking.eval_swap_mix --game-id mri01pvelv46d --npz smoke   # jersey_samples.smoke.npz

Hues are OpenCV (0-180): green kit ~55-85, blue kit ~95-128. Kits are
game-specific but the two-team hue separation is what matters, not the exact
bands -- pass --green-lo/--green-hi/--blue-lo/--blue-hi to retune per game.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def _frame_team(a: np.ndarray, glo: float, ghi: float, blo: float, bhi: float,
                sat_gate: float) -> str | None:
    """Classify one frame's HSV pixel array as 'G' / 'B' / None.

    Use only vivid pixels (the kit color, not the grass/skin/desaturated wash
    that pulls a whole track's median toward a common neutral center). Falls
    back to all pixels when too few are vivid, so a genuinely dim frame still
    votes rather than silently abstaining."""
    if len(a) < 5:
        return None
    hi = a[a[:, 1] >= sat_gate]
    use = hi if len(hi) >= 5 else a
    mh = float(np.median(use[:, 0]))
    if glo <= mh <= ghi:
        return "G"
    if blo <= mh <= bhi:
        return "B"
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--npz", default="",
                    help="jersey_samples suffix: '' (live), 'pitch', 'smoke', "
                         "'equirect_forrestore', or a full path to a .npz")
    ap.add_argument("--dt", type=float, default=0.1, help="seconds per frame-sample")
    ap.add_argument("--pure-frac", type=float, default=0.70,
                    help="a track is pure if >= this fraction of its colored frames are one team")
    ap.add_argument("--sat-gate", type=float, default=90.0)
    ap.add_argument("--green-lo", type=float, default=45.0)
    ap.add_argument("--green-hi", type=float, default=88.0)
    ap.add_argument("--blue-lo", type=float, default=95.0)
    ap.add_argument("--blue-hi", type=float, default=128.0)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    from post_game import config

    ckpt = config.OUTPUTS_DIR / args.game_id
    if args.npz and Path(args.npz).exists():
        npz = Path(args.npz)
    else:
        stem = "jersey_samples" if not args.npz else f"jersey_samples.{args.npz}"
        npz = ckpt / f"{stem}.npz"
    if not npz.exists():
        raise SystemExit(f"no jersey npz at {npz}")

    pure_g = pure_b = mixed = tiny = 0
    sec_g = sec_b = sec_mixed = 0.0
    n_tracks = 0
    with np.load(npz, allow_pickle=True) as nz:
        for k in nz.files:
            n_tracks += 1
            samples = list(nz[k])
            g = b = 0
            for s in samples:
                t = _frame_team(np.asarray(s, dtype=np.float32),
                                args.green_lo, args.green_hi, args.blue_lo, args.blue_hi,
                                args.sat_gate)
                if t == "G":
                    g += 1
                elif t == "B":
                    b += 1
            sec = len(samples) * args.dt
            col = g + b
            if col < 5:
                tiny += 1
                continue
            gf = g / col
            if gf >= args.pure_frac:
                pure_g += 1
                sec_g += sec
            elif gf <= (1.0 - args.pure_frac):
                pure_b += 1
                sec_b += sec
            else:
                mixed += 1
                sec_mixed += sec

    tot = sec_g + sec_b + sec_mixed
    label = args.label or npz.name
    print(f"\n==== SWAP GAUGE: {label} ({args.game_id}) ====")
    print(f"tracks: {n_tracks}  (pure-green {pure_g}, pure-blue {pure_b}, MIXED {mixed}, "
          f"tiny/nocolor {tiny})")
    print(f"colored track-seconds: pure-green {sec_g:.0f}s ({sec_g/max(1,tot):.0%}), "
          f"pure-blue {sec_b:.0f}s ({sec_b/max(1,tot):.0%}), "
          f"MIXED {sec_mixed:.0f}s ({sec_mixed/max(1,tot):.0%})")
    print(f"  >>> MIXED-second fraction = {100*sec_mixed/max(1,tot):.1f}%  "
          f"(swap gauge; equirect baseline ~16%; lower is better)")


if __name__ == "__main__":
    main()
