#!/usr/bin/env python3
"""READ-ONLY: what does the opponent pre-filter actually vote, by box size?

Why this exists
---------------
`TRACK_DROP_OPPONENTS` deletes a detection before `tracker.update()` whenever
`vote_detection` returns -1. On Game 1 that filter removes about two thirds of
the on-pitch bodies that survive a touchline cut, and the far third of the
pitch loses nearly half its share — a body is present in the far goal area in
39% of frames against 100% before the filter. Those are not the marks of a
filter that removes only opponents.

Two hypotheses were live:

  * **size gate** — small boxes lack the pixels to vote, so they abstain and
    are dropped. Predicts the UNKNOWN rate rises steeply as boxes shrink.
  * **anchor ordering** — the pre-filter runs during tracking with the RAW kit
    hexes, while `fit_value_anchors` (which exists precisely because a black
    `#0a0a0a` shirt photographs at V150-200, nowhere near its nominal V10) only
    runs afterwards at stage 2b and can relabel surviving tracks but cannot
    restore a deleted detection. Predicts a high confident-OPPONENT rate at
    every size, not abstention.

The parallel session's G1 audit settled it for the value axis: unknown is a
flat 3-8% across every size band while OPPONENT runs at 68% on a roughly 1:1
game. Confident mislabelling, not abstention.

This tool runs the same audit on any game so the two colour axes can be
compared. The sharp prediction: `fit_value_anchors` touches only the VALUE
axis and leaves hue games byte-identical, so a hue game (Game 2, green vs
blue) should vote near 1:1 if the anchor-ordering bug is the whole story. If a
hue game ALSO votes ~68% opponent, the diagnosis is incomplete.

Reads the raw video and a cached Stage-2 parquet for box geometry. Calls the
same `vote_detection` production calls, with the same config values, so the
numbers describe the shipped path rather than a reimplementation of it.

Never writes anything.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.kit_vote_audit \\
        --game-id mri01pvelv46d --window 700-1000 --frames 60
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np

BINS = [0, 40, 60, 80, 120, 200, 10**9]
LABELS = ["<40", "40-60", "60-80", "80-120", "120-200", ">200"]


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--tag", default="", help="cached checkpoint tag ('' = untagged)")
    ap.add_argument("--window", default=None, help="'a-b' video seconds")
    ap.add_argument("--frames", type=int, default=60,
                    help="how many frames to sample across the window")
    args = ap.parse_args()

    import pandas as pd
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector
    from post_game.kit_vote import pick_axis, vote_detection
    from post_game.pipeline import _our_color
    from post_game.video import open_video

    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    proj = FieldProjector(cal)
    L, W = cal.length_m, cal.width_m

    path = (config.OUTPUTS_DIR / args.game_id /
            (f"tracks_raw.{args.tag}.parquet" if args.tag else "tracks_raw.parquet"))
    tr = pd.read_parquet(path)
    xy = proj.pixel_to_field_batch(tr[["foot_x_eq", "foot_y_eq"]].to_numpy())
    tr["x_m"], tr["y_m"] = xy[:, 0], xy[:, 1]
    if args.window:
        a, b = (float(v) for v in args.window.split("-"))
        tr = tr[(tr.time_s >= a) & (tr.time_s <= b)]
    tr = tr[(tr.x_m >= 0) & (tr.x_m <= L) & (tr.y_m >= 0) & (tr.y_m <= W)]
    if tr.empty:
        raise SystemExit("no on-pitch detections in that window.")

    our_hex = _our_color(game)
    opp_hex = game.away_color or "#ffffff"
    if our_hex == game.home_color and opp_hex == game.home_color:
        opp_hex = "#ffffff"
    # Production derives the axis the same way, with no config override
    # (pipeline.py:308). Mirror it exactly rather than inventing a knob.
    axis = pick_axis(our_hex, opp_hex)

    print(f"game {args.game_id} vs {game.opponent}")
    print(f"  ours={our_hex}  opponent={opp_hex}  AXIS={axis}")
    print(f"  window {tr.time_s.min():.0f}-{tr.time_s.max():.0f}s, "
          f"{tr.time_s.nunique()} frames available")

    from post_game.pipeline import _ensure_local_video
    import cv2
    video = _ensure_local_video(game.video_url, args.game_id)
    cap = cv2.VideoCapture(str(video))

    ts = np.array(sorted(tr.time_s.unique()))
    pick = ts[np.linspace(0, len(ts) - 1, min(args.frames, len(ts))).astype(int)]

    tally = {lab: [0, 0, 0] for lab in LABELS}      # ours, opp, unknown
    n = 0
    for k, t in enumerate(pick):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        for _, d in tr[tr.time_s == t].iterrows():
            bbox = (d.x1_eq, d.y1_eq, d.x2_eq, d.y2_eq)
            v = vote_detection(frame, bbox, our_hex, opp_hex, axis=axis,
                               min_s=config.PITCH_COLOR_MIN_S,
                               min_px=config.PITCH_COLOR_MIN_PIXELS,
                               hue_margin=config.PITCH_COLOR_MARGIN_DEG,
                               value_margin=config.KIT_VOTE_VALUE_MARGIN)
            h = float(d.bbox_h_crop)
            lab = LABELS[int(np.digitize(h, BINS[1:-1]))]
            tally[lab][0 if v == 1 else (1 if v == -1 else 2)] += 1
            n += 1
        if (k + 1) % 20 == 0:
            print(f"   ...{k+1}/{len(pick)} frames", flush=True)
    cap.release()

    print(f"\n{n} on-pitch detections voted through the PRODUCTION path\n")
    print(f"{'box h':>9}{'n':>7}{'ours +1':>10}{'opp -1':>9}{'unk 0':>8}{'KEPT':>8}")
    tot = [0, 0, 0]
    for lab in LABELS:
        o, p, u = tally[lab]
        s = o + p + u
        tot = [tot[i] + [o, p, u][i] for i in range(3)]
        if not s:
            continue
        print(f"{lab:>9}{s:>7}{100*o/s:>9.0f}%{100*p/s:>8.0f}%"
              f"{100*u/s:>7.0f}%{100*(o+u)/s:>7.0f}%")
    s = sum(tot)
    if s:
        o, p, u = tot
        print(f"{'ALL':>9}{s:>7}{100*o/s:>9.0f}%{100*p/s:>8.0f}%"
              f"{100*u/s:>7.0f}%{100*(o+u)/s:>7.0f}%")

    print("\nHow to read this. A 7v7 is roughly 1:1, so 'ours' far below 50% means")
    print("the filter is confidently calling OUR players opponents and deleting")
    print("them. A flat 'unknown' across sizes rules out the size-gate story: the")
    print("abstain path is not what is removing small boxes.")
    if axis == "hue":
        print("\nThis is a HUE game. `fit_value_anchors` touches only the value axis,")
        print("so if the value-anchor ordering bug is the whole story this should")
        print("sit near 1:1. A lopsided result here means the diagnosis is")
        print("INCOMPLETE and something beyond anchor choice is wrong.")


if __name__ == "__main__":
    main()
