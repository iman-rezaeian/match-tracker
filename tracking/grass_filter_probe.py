#!/usr/bin/env python3
"""READ-ONLY: does the grass filter erase our green kit and mix the teams?

`team_classifier.sample_jersey_hsv` drops pixels in the grass band
(35 <= H <= 85, S > 60, V > 50). Our kit #16a34a is H71 S221 V163 — INSIDE that
band. The opponent's #2563eb is H110 — outside it. So the filter can only damage
the green team, which is the asymmetry that matters.

This probe re-renders real frames from the source video, and for each detected
player runs BOTH colour paths on the SAME pixels:

  A. sample_jersey_hsv  (production)      -> median hue after the grass drop
  B. _det_kit_color     (tracking_pitch)  -> nearest-kit-hue, no grass drop

Then it reports how each path splits the pitch. The ground truth we can lean on
without labels: a 7v7 game has ~7 children per side on the pitch at any moment,
so a correct split is ~1:1. Production currently reports 3.9:1.

Read-only: opens the video and the cached tracks, writes nothing.

Run: python -m tracking.grass_filter_probe --game-id mri01pvelv46d
"""
from __future__ import annotations

import argparse
import warnings
from collections import Counter

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--frames", type=int, default=40,
                    help="how many sampled frames to probe")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import cv2
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector
    from post_game.team_classifier import sample_jersey_hsv
    from post_game.tracking_pitch import _det_kit_color, _hue_from_hex
    from post_game.video import iter_frames, open_video
    from post_game.pipeline import _our_color, _ensure_local_video

    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    proj = FieldProjector(cal)
    our_hex = _our_color(game)
    opp_hex = game.away_color
    our_h = _hue_from_hex(our_hex)
    opp_h = _hue_from_hex(opp_hex)
    print(f"our kit {our_hex} -> H{our_h:.0f}    opp kit {opp_hex} -> H{opp_h:.0f}")
    print(f"grass band dropped by sample_jersey_hsv: 35<=H<=85 & S>60 & V>50")
    print(f"  our kit inside the band? {35 <= our_h <= 85}")
    print(f"  opp kit inside the band? {35 <= opp_h <= 85}\n")

    tracks = pd.read_parquet(config.OUTPUTS_DIR / args.game_id / "tracks_raw.parquet")
    xy = proj.pixel_to_field_batch(tracks[["foot_x_eq", "foot_y_eq"]].to_numpy())
    tracks["x_m"], tracks["y_m"] = xy[:, 0], xy[:, 1]
    L, W = cal.length_m, cal.width_m
    tracks = tracks[(tracks.x_m >= -1.5) & (tracks.x_m <= L + 1.5)
                    & (tracks.y_m >= -1.5) & (tracks.y_m <= W + 1.5)]

    # SEEK to each wanted frame instead of decoding the file front-to-back: the
    # source is 80 GB, so a sequential scan to reach scattered frames takes
    # longer than the whole tracking pass. Frames are spread evenly across the
    # game so both halves and both ends of the pitch are represented.
    all_frames = np.array(sorted(tracks.frame.unique()))
    want_list = [int(f) for f in
                 all_frames[np.linspace(0, len(all_frames) - 1, args.frames).astype(int)]]

    video_path = _ensure_local_video(game.video_url, args.game_id)
    meta = open_video(str(video_path))
    print(f"video {meta['width']}x{meta['height']} @ {meta['fps']:.2f} fps — "
          f"probing {len(want_list)} frames spread across the game\n", flush=True)

    prod_hues: list[float] = []
    prod_empty = 0
    pitch_votes: Counter = Counter()
    per_frame: list[tuple[int, int]] = []   # (n_our, n_opp) by the pitch sampler

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video_path}")
    for i, fidx in enumerate(want_list, 1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fidx))
        ok, eq_frame = cap.read()
        if not ok:
            continue
        print(f"  frame {i}/{len(want_list)} (idx {fidx})", flush=True)
        rows = tracks[tracks.frame == fidx]
        n_our = n_opp = 0
        for _, r in rows.iterrows():
            bbox = (r.x1_eq, r.y1_eq, r.x2_eq, r.y2_eq)
            sample = type("S", (), {"eq_frame": eq_frame})()
            # --- A. production sampler
            px = sample_jersey_hsv(sample.eq_frame, bbox)
            if len(px) == 0:
                prod_empty += 1
            else:
                prod_hues.append(float(np.median(px[:, 0])))
            # --- B. nearest-kit-hue, no grass drop
            vote = _det_kit_color(sample.eq_frame, bbox, our_h, opp_h,
                                  config.PITCH_COLOR_MIN_S,
                                  config.PITCH_COLOR_MIN_PIXELS,
                                  config.PITCH_COLOR_MARGIN_DEG)
            pitch_votes[vote] += 1
            if vote == 1:
                n_our += 1
            elif vote == -1:
                n_opp += 1
        per_frame.append((n_our, n_opp))
    cap.release()

    print("=== A. PRODUCTION sample_jersey_hsv (after the grass drop) ===")
    if prod_hues:
        ph = np.array(prod_hues)
        near_our = int((np.abs(ph - our_h) < np.abs(ph - opp_h)).sum())
        print(f"  detections sampled: {len(ph)}  (empty: {prod_empty})")
        print(f"  median hue {np.median(ph):.0f}   "
              f"nearer OUR kit: {near_our} ({100*near_our/len(ph):.0f}%)  "
              f"nearer OPP kit: {len(ph)-near_our} ({100*(len(ph)-near_our)/len(ph):.0f}%)")
        band = int(((ph >= 35) & (ph <= 85)).sum())
        print(f"  still in the grass/green band: {band} ({100*band/len(ph):.0f}%) "
              f"<- our kit's own hue, mostly deleted")

    print("\n=== B. NEAREST-KIT-HUE (tracking_pitch._det_kit_color, no grass drop) ===")
    tot = sum(pitch_votes.values())
    o, p, u = pitch_votes.get(1, 0), pitch_votes.get(-1, 0), pitch_votes.get(0, 0)
    print(f"  detections voted: {tot}")
    print(f"    ours  : {o:6d}  ({100*o/max(tot,1):5.1f}%)")
    print(f"    opp   : {p:6d}  ({100*p/max(tot,1):5.1f}%)")
    print(f"    abstain:{u:6d}  ({100*u/max(tot,1):5.1f}%)")
    if p:
        print(f"    ours:opp ratio = {o/p:.2f} : 1        (7v7 target ~1:1)")

    if per_frame:
        arr = np.array(per_frame)
        print(f"\n  per-frame bodies — ours median {np.median(arr[:,0]):.0f}   "
              f"opp median {np.median(arr[:,1]):.0f}   (target ~7 and ~7)")

    print("\n=== VERDICT ===")
    print("  Production reported 2479 ours : 634 opp = 3.9:1 for this game.")
    if p and abs((o / p) - 1.0) < abs(3.9 - 1.0):
        print(f"  The no-grass-drop path splits {o/p:.2f}:1 — closer to the 7v7 truth.")
    else:
        print("  The no-grass-drop path does NOT improve the split; the grass filter")
        print("  is not the (only) cause. Do not re-track on this hypothesis.")


if __name__ == "__main__":
    main()
