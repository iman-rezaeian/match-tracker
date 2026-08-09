#!/usr/bin/env python3
"""Sample tracklets for a COMPOSITION judgement: one child, or several?

Why this exists, and why it is not the old GT sampler
------------------------------------------------------
`tracking/labels/mri01pvelv46d_player_gt/gt.csv` was read all week as showing
"87% chain impurity — 26 of 30 biggest tracklets hold more than one child". It
shows nothing of the kind. The file holds 30 rows: 26 `__cant_tell__` and 4
`player`. The 87% is literally `26/30` — the share the coach could not
IDENTIFY — and the notes column is empty on every row, so **nothing in it
asserts that any tracklet contains two children.** The number is retired.

The mistake was in the question. `player_gt_app` asks "who is this?", which
conflates two failures: a tracklet can be unnameable because it is mixed, or
because it is small, distant, back-turned or blurred. Since every VLM success
reads the number on the BACK, "unnameable because facing the camera" is a large
population, and it swamped the signal.

Composition is judgeable WITHOUT naming anyone. "Does this strip show one child
or several?" survives a player you cannot name, which is most of them.

Three sampling constraints, each earned
---------------------------------------
1. **Blind to tracklet length.** The obvious sample is "the biggest tracklets",
   and that is what produced the retired number. Length does NOT predict
   nameability — the 4 named rows ranked 3, 6, 21 and 28 of 30, named median
   3.15 min vs can't-tell 3.11, Mann-Whitney p=0.415 — so sampling by size
   bakes in a selection that is uninformative for nameability and unknown for
   composition. Draw across the whole size range.

2. **Blind to prior nameability.** Do not re-draw from the same 30 strips the
   coach already saw. He would recognise the ones he could not name, and that
   is his prior, not an independent composition judgement.

3. **Ask composition, not identity.** "One child or several?" — and offer
   "can't tell" as a first-class answer so an illegible strip is recorded as
   illegible rather than forced into a composition verdict.

What it emits
-------------
Per sampled tracklet, a contact-sheet strip of crops spread across its life,
plus a manifest. A tracklet that IS mixed shows the change between crops; that
is the whole point of a strip rather than a single frame.

Reads cached Stage-2 checkpoints and the raw video. Never writes Firestore.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.composition_sampler \\
        --game-id mrhvbvwi1gjpn --n 12
"""
from __future__ import annotations

import argparse
import json
import subprocess
import warnings
from pathlib import Path

import numpy as np

LABELS_ROOT = Path(__file__).resolve().parent / "labels"
# Crops per strip. Enough to see a change of child across the tracklet's life;
# few enough that the coach reads one strip in a few seconds.
N_CROPS = 6
# A tracklet needs to last this long for "one child or several" to be a
# meaningful question — below it there is nothing for identity to drift across.
MIN_TRACKLET_S = 20.0


def stratified_sample(sizes: dict[int, float], n: int, rng) -> list[int]:
    """Draw `n` tracklets spread ACROSS the size range, not from the top.

    Sorts by duration and takes one from each of `n` equal-count strata, so a
    12-strip sample covers short, middling and long tracklets alike. Sampling
    the biggest is what produced the retired 87% figure.
    """
    ids = sorted(sizes, key=lambda t: sizes[t])
    if len(ids) <= n:
        return ids
    edges = np.linspace(0, len(ids), n + 1).astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b > a:
            out.append(ids[int(rng.integers(a, b))])
    return out


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--tag", default="", help="cached checkpoint tag ('' = untagged)")
    ap.add_argument("--n", type=int, default=12, help="how many strips")
    ap.add_argument("--seed", type=int, default=0, help="RNG seed, for reproducibility")
    ap.add_argument("--exclude-prior", action="store_true", default=True,
                    help="skip tracklets already labelled in *_player_gt/gt.csv")
    args = ap.parse_args()

    import cv2
    import pandas as pd
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector
    from post_game.pipeline import _ensure_local_video

    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    L, W = cal.length_m, cal.width_m
    proj = FieldProjector(cal)

    path = (config.OUTPUTS_DIR / args.game_id /
            (f"tracks_raw.{args.tag}.parquet" if args.tag else "tracks_raw.parquet"))
    tr = pd.read_parquet(path)
    xy = proj.pixel_to_field_batch(tr[["foot_x_eq", "foot_y_eq"]].to_numpy())
    tr["x_m"], tr["y_m"] = xy[:, 0], xy[:, 1]
    tr = tr[(tr.x_m >= 0) & (tr.x_m <= L) & (tr.y_m >= 0) & (tr.y_m <= W)]

    # Track-level touchline cut: a strip of a parent under a gazebo is not a
    # composition question. Same rule as the labeling renderer.
    core = ((tr.x_m > 1.5) & (tr.x_m < L - 1.5)
            & (tr.y_m > 1.5) & (tr.y_m < W - 1.5))
    per = tr.assign(_c=core).groupby("track_id")["_c"].agg(["mean", "size"])
    tr = tr[tr.track_id.isin(set(per.index[per["mean"] >= 0.5]))]

    life = tr.groupby("track_id").time_s.agg(["min", "max"])
    life["dur"] = life["max"] - life["min"]
    cand = life[life["dur"] >= MIN_TRACKLET_S]
    if cand.empty:
        raise SystemExit(f"no track lasts {MIN_TRACKLET_S:.0f}s — nothing to judge.")

    # Constraint 2: never re-draw what the coach has already seen.
    prior: set[int] = set()
    gt = LABELS_ROOT / f"{args.game_id}_player_gt" / "gt.csv"
    if args.exclude_prior and gt.exists():
        import csv
        with open(gt) as f:
            prior = {int(r["tracklet_id"]) for r in csv.DictReader(f)
                     if r.get("tracklet_id", "").strip().isdigit()}
        cand = cand[~cand.index.isin(prior)]
        print(f"excluded {len(prior)} tracklets the coach has already judged")

    rng = np.random.default_rng(args.seed)
    picked = stratified_sample(cand["dur"].to_dict(), args.n, rng)
    print(f"game {args.game_id}: {len(cand)} eligible tracks "
          f"(>= {MIN_TRACKLET_S:.0f}s), sampling {len(picked)} "
          f"STRATIFIED across the size range")
    d = cand.loc[picked, "dur"]
    print(f"   sampled durations: min {d.min():.0f}s  median {d.median():.0f}s  "
          f"max {d.max():.0f}s   (eligible pool: {cand['dur'].min():.0f}-"
          f"{cand['dur'].max():.0f}s)")

    video = _ensure_local_video(game.video_url, args.game_id)
    cap = cv2.VideoCapture(str(video))
    outdir = LABELS_ROOT / f"{args.game_id}_composition"
    outdir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for k, tid in enumerate(picked):
        sub = tr[tr.track_id == tid].sort_values("time_s")
        idx = np.linspace(0, len(sub) - 1, min(N_CROPS, len(sub))).astype(int)
        crops = []
        for i in idx:
            r = sub.iloc[int(i)]
            cap.set(cv2.CAP_PROP_POS_MSEC, float(r.time_s) * 1000)
            ok, fr = cap.read()
            if not ok:
                continue
            # Pad the box so the child is in context, not cut out of it.
            h = r.y2_eq - r.y1_eq
            pad = h * 0.35
            y0, y1 = int(max(0, r.y1_eq - pad)), int(r.y2_eq + pad)
            x0, x1 = int(max(0, r.x1_eq - pad)), int(r.x2_eq + pad)
            c = fr[y0:y1, x0:x1]
            if c.size:
                crops.append(cv2.resize(c, (180, 300)))
        if len(crops) < 2:
            continue
        strip = np.hstack(crops)
        name = f"tl_{int(tid)}.jpg"
        cv2.imwrite(str(outdir / name), strip)
        manifest.append({
            "tracklet_id": int(tid), "image": name,
            "game_id": args.game_id,
            "t_start_s": float(sub.time_s.iloc[0]),
            "t_end_s": float(sub.time_s.iloc[-1]),
            "duration_s": float(sub.time_s.iloc[-1] - sub.time_s.iloc[0]),
            "n_det": int(len(sub)),
            "n_crops": len(crops),
        })
        print(f"   [{k+1}/{len(picked)}] tl_{int(tid)} "
              f"{manifest[-1]['duration_s']:.0f}s, {len(crops)} crops", flush=True)
    cap.release()

    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"\nwrote {len(manifest)} strips to {outdir}")
    print("Label them with: streamlit run tracking/composition_app.py")
    print("\nThe question is ONE CHILD OR SEVERAL — not who it is. A strip whose")
    print("crops show the same child throughout is clean; one where the child")
    print("changes partway is mixed. 'Can't tell' is a real answer and must not")
    print("be forced into either bucket — that conflation is what produced the")
    print("retired 87% figure.")


if __name__ == "__main__":
    main()
