#!/usr/bin/env python3
"""Would tracking at a HIGHER frame rate reduce ID-swaps? Answer it offline.

The pipeline samples 1-of-`SAMPLE_RATE` frames (default 3 => 10 Hz from a 30 fps
source), so 2/3 of the recorded frames are discarded before the tracker ever sees
them. The coach asked whether recording/tracking at a higher rate would help.

The swap mechanism (measured): a tracker following identical same-kit players
must decide "which detection is the one I was following?" It gets that wrong when,
within ONE time step, a player can move far enough to reach where a DIFFERENT
player is. So the quantity that decides swap risk is:

      per-step displacement   vs   nearest-neighbour spacing

Both are measurable from the cached tracks, with no video decode and no re-track:
  * displacement per step scales ~linearly with dt (halve dt => halve the step),
    so 30 Hz and 60 Hz steps can be derived from the observed 10 Hz steps;
  * spacing is a property of the game (how tightly U10s cluster), independent of
    frame rate.

Reports, per candidate rate, the AMBIGUITY RATE: the share of steps where the
player's own motion exceeds the distance to their nearest neighbour — i.e. where
the tracker could legally land on the wrong body. Lower is better.

Read-only; no Firestore writes, no video. Usage:
  python -m tracking.framerate_evidence --game-id mri01pvelv46d \
      --ckpt-suffix equirect_forrestore
"""
from __future__ import annotations

import argparse

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--ckpt-suffix", default="equirect_forrestore")
    ap.add_argument("--rates", default="10,20,30,60",
                    help="candidate sampling rates (Hz) to evaluate")
    args = ap.parse_args()

    import pandas as pd
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector

    ckpt = config.OUTPUTS_DIR / args.game_id
    sfx = f".{args.ckpt_suffix}" if args.ckpt_suffix else ""
    tp = ckpt / f"tracks_raw{sfx}.parquet"
    if not tp.exists():
        raise SystemExit(f"no tracks at {tp}")
    df = pd.read_parquet(tp)

    cal = firestore_io.get_game_calibration(args.game_id)
    proj = FieldProjector(cal)
    L, W = cal.length_m, cal.width_m
    xy = proj.pixel_to_field_batch(df[["foot_x_eq", "foot_y_eq"]].to_numpy())
    df["x_m"], df["y_m"] = xy[:, 0], xy[:, 1]
    df = df[np.isfinite(df["x_m"]) & np.isfinite(df["y_m"])]
    # keep on-field only: off-field spectators/coaches would pollute both spacing
    # and displacement (they're not in the swarm the tracker is confusing).
    df = df[(df["x_m"] >= -1.5) & (df["x_m"] <= L + 1.5)
            & (df["y_m"] >= -1.5) & (df["y_m"] <= W + 1.5)].reset_index(drop=True)

    # ---- observed per-step displacement at the CURRENT sampling rate ----
    s = df.sort_values(["track_id", "time_s"])
    dt = s.groupby("track_id")["time_s"].diff()
    dx = s.groupby("track_id")["x_m"].diff()
    dy = s.groupby("track_id")["y_m"].diff()
    step = np.hypot(dx, dy)
    base_dt = float(dt[(dt > 0) & (dt < 1)].median())
    ok = (dt > 0.5 * base_dt) & (dt < 1.5 * base_dt) & np.isfinite(step)
    # speed per step (m/s) — the frame-rate-independent quantity. Drop the
    # physically impossible tail (teleports/swaps already in the data) so we
    # measure REAL motion, not the artifacts we're trying to prevent.
    spd = (step[ok] / dt[ok]).to_numpy()
    spd = spd[np.isfinite(spd) & (spd <= config.MAX_PLAUSIBLE_SPEED_MS)]

    # ---- nearest-neighbour spacing per frame (the swarm tightness) ----
    gaps = []
    for _, g in df.groupby("frame"):
        if len(g) < 2:
            continue
        p = g[["x_m", "y_m"]].to_numpy()
        d = np.hypot(p[:, None, 0] - p[None, :, 0], p[:, None, 1] - p[None, :, 1])
        np.fill_diagonal(d, np.inf)
        gaps.append(d.min(axis=1))
    nn = np.concatenate(gaps) if gaps else np.array([np.nan])
    nn = nn[np.isfinite(nn)]

    print(f"\n==== FRAME-RATE EVIDENCE · {args.game_id} ({args.ckpt_suffix}) ====")
    print(f"current sampling: {1/base_dt:.1f} Hz (dt={base_dt:.3f}s) "
          f"| SAMPLE_RATE={config.SAMPLE_RATE} of a 30 fps source")
    print(f"on-field detections: {len(df)}  | real-motion steps: {len(spd)}")
    print(f"\nplayer SPEED (m/s, real steps): median {np.median(spd):.2f}  "
          f"p90 {np.percentile(spd,90):.2f}  p99 {np.percentile(spd,99):.2f}")
    print(f"nearest-neighbour SPACING (m): median {np.median(nn):.2f}  "
          f"p10 {np.percentile(nn,10):.2f}  p25 {np.percentile(nn,25):.2f}")

    # ---- ambiguity rate per candidate rate ----
    # A step is AMBIGUOUS when the distance the player covers in one step is
    # >= the distance to their nearest neighbour: the tracker's motion prediction
    # can then reach a different body. Sample spacing independently of speed
    # (they're both game properties; pairing them randomly estimates the joint
    # exposure without assuming a correlation we haven't measured).
    rng = np.random.default_rng(0)
    n = min(len(spd), 200_000)
    spd_s = rng.choice(spd, n, replace=len(spd) < n)
    nn_s = rng.choice(nn, n, replace=len(nn) < n)
    print(f"\n{'rate':>6} {'dt':>7} {'p90 step':>10} {'p99 step':>10} {'AMBIGUOUS':>11}")
    rows = []
    for hz in [float(x) for x in args.rates.split(",")]:
        d = 1.0 / hz
        steps = spd_s * d
        amb = float((steps >= nn_s).mean()) * 100
        p90 = np.percentile(spd_s, 90) * d
        p99 = np.percentile(spd_s, 99) * d
        rows.append((hz, amb))
        print(f"{hz:>5.0f}Hz {d:>7.3f} {p90:>9.2f}m {p99:>9.2f}m {amb:>10.2f}%")

    base = dict(rows).get(round(1 / base_dt), rows[0][1])
    print("\nINTERPRETATION")
    print("  'AMBIGUOUS' = share of steps where a player's own movement in ONE step")
    print("  reaches at least as far as their nearest neighbour — the moments the")
    print("  tracker can legally grab the wrong same-kit body. Lower = fewer swaps.")
    for hz, amb in rows:
        if abs(hz - 1 / base_dt) < 0.5:
            continue
        rel = (base - amb) / base * 100 if base else 0
        print(f"  {hz:.0f} Hz cuts ambiguous steps by {rel:.0f}% vs the current "
              f"{1/base_dt:.0f} Hz ({base:.2f}% -> {amb:.2f}%)")
    print("\n  NOTE: 30 Hz needs NO new recording — the source is already 30 fps and")
    print("  the pipeline discards 2 of every 3 frames (config.SAMPLE_RATE=3).")
    print("  60 Hz would require re-recording, and only helps if the camera can hold")
    print("  8K at 60 — dropping resolution would shrink already-small far players.")


if __name__ == "__main__":
    main()
