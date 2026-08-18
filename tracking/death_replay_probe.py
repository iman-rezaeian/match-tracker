#!/usr/bin/env python3
"""READ-ONLY: at the frame a track dies, did YOLO still see the body?

Stage-2 measurement says 65% of raw tracks die mid-field, and at the next
sampled frame the nearest emitted detection is a median 2.72 m away — 30x the
0.08 m a continuing track actually moves per frame. So the body is absent from
the TRACKER OUTPUT. That is as far as the cached parquet can take us, because it
stores accepted tracks only: a detection YOLO found but BotSort declined to
confirm never appears in it.

Two very different bugs produce the same hole:

  (a) YOLO missed the body     -> a detector problem (imgsz, tiling, occlusion)
  (b) YOLO found it, BotSort   -> an association/threshold problem
      withheld or reassigned it   (new_track_thresh=0.5 vs DETECT_CONFIDENCE=0.3
                                   means a re-appearing player must clear 0.5 to
                                   restart, and 16% of emitted dets are below it)

Only re-running the detector on the actual frames separates them, so this
re-renders the same three tiles the pipeline uses, runs the same YOLO weights at
the same confidence, and asks: was there a person box within a metre of where
the track died, on the frame AFTER it died?

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.death_replay_probe \
        --game-id mrhvbvwi1gjpn --n 40
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np
import pandas as pd


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--n", type=int, default=40, help="deaths to replay")
    ap.add_argument("--min-bbox-h", type=float, default=60.0,
                    help="only replay unambiguous (large) bodies")
    ap.add_argument("--radius-m", type=float, default=1.5)
    args = ap.parse_args()

    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector, compute_tile_aims
    from post_game.calibration import dedupe_detections_by_field_position
    from post_game.detection import Detector
    from post_game.video import iter_frames, render_perspective, crop_bbox_to_equirect

    game = firestore_io.get_game(args.game_id)
    fc = firestore_io.get_game_calibration(args.game_id)
    L, W = fc.length_m, fc.width_m
    proj = FieldProjector(fc)

    out_dir = config.OUTPUTS_DIR / args.game_id
    tr = pd.read_parquet(out_dir / "tracks_raw.parquet")
    xy = proj.pixel_to_field_batch(tr[["foot_x_eq", "foot_y_eq"]].to_numpy())
    tr["x_m"], tr["y_m"] = xy[:, 0], xy[:, 1]
    on = tr[(tr.x_m >= -1.5) & (tr.x_m <= L + 1.5)
            & (tr.y_m >= -1.5) & (tr.y_m <= W + 1.5)].copy()

    frames = np.sort(tr.frame.unique())
    nxt = dict(zip(frames[:-1], frames[1:]))
    last = on.sort_values("time_s").groupby("track_id").tail(1).copy()
    last["h"] = last.y2_eq - last.y1_eq
    edge = 2.0
    interior = last[(last.x_m >= edge) & (last.x_m <= L - edge)
                    & (last.y_m >= edge) & (last.y_m <= W - edge)
                    & (last.h >= args.min_bbox_h)]
    # Deterministic spread across the game rather than the biggest boxes only,
    # which would over-sample the near touchline.
    interior = interior.sort_values("time_s")
    pick = interior.iloc[:: max(1, len(interior) // args.n)].head(args.n)
    print(f"replaying {len(pick)} interior deaths (bbox >= {args.min_bbox_h:.0f}px) "
          f"of {len(interior)} candidates")

    # Key the work by the successor frame's TIME, not its index: frame_index
    # counts source frames (29.97 fps) and the cached run started at the
    # kickoff, so an iterator opened at t=0 produces a different index sequence
    # entirely. Matching on time and windowing each seek keeps the two aligned.
    t_of_frame = tr.groupby("frame")["time_s"].first()
    want = {}
    for _, r in pick.iterrows():
        nf = nxt.get(r.frame)
        if nf is None or nf not in t_of_frame.index:
            continue
        want[float(t_of_frame.loc[nf])] = (float(r.x_m), float(r.y_m),
                                           int(r.track_id), float(r.h))
    if not want:
        raise SystemExit("no successor frames to replay")

    tile_aims = compute_tile_aims(proj, L, W, config.DETECT_N_TILES,
                                  config.DETECT_TILE_FOV_DEG)
    detector = Detector()
    eq_w, eq_h = fc.video_frame_size
    video = str(game.video_url or "").replace("file://", "")

    found = missed = 0
    conf_found = []
    seen_frames = 0
    # One tight window per target time, so the decoder seeks to each frame
    # instead of walking all 80 GB. Matching on TIME rather than frame_index is
    # what makes this work at all: frame_index counts source frames (29.97 fps)
    # and the cached run began at the kickoff, so an iterator opened at t=0
    # produces a completely different index sequence and matches nothing.
    targets = sorted(want)
    windows = [(t - 0.05, t + 0.05) for t in targets]
    for sample in iter_frames(video, sample_rate=1, windows=windows,
                              render_crop=False):
        j = int(np.argmin([abs(sample.time_s - t) for t in targets]))
        if abs(sample.time_s - targets[j]) > 0.08:
            continue
        seen_frames += 1
        crops = [render_perspective(sample.eq_frame, lon, lat, fov,
                                    config.CROP_W, config.CROP_H)
                 for (lon, lat, fov) in tile_aims]
        dets = []
        for ci, dl in enumerate(detector.detect_persons(crops)):
            lon, lat, fov = tile_aims[ci]
            for d in dl:
                d.bbox_eq = crop_bbox_to_equirect(d.bbox_crop, lon, lat, fov,
                                                  eq_w, eq_h,
                                                  config.CROP_W, config.CROP_H)
                d.bbox_crop = d.bbox_eq
                dets.append(d)
        dets = dedupe_detections_by_field_position(dets, proj,
                                                   config.DETECT_TILE_DEDUPE_M)
        if dets:
            feet = np.array([[(d.bbox_eq[0] + d.bbox_eq[2]) / 2.0, d.bbox_eq[3]]
                             for d in dets])
            gxy = proj.pixel_to_field_batch(feet)
        tx, ty, tid, h = want[targets[j]]
        if not dets:
            missed += 1
        else:
            dd = np.hypot(gxy[:, 0] - tx, gxy[:, 1] - ty)
            k = int(np.argmin(dd))
            if dd[k] <= args.radius_m:
                found += 1
                conf_found.append(float(dets[k].confidence))
            else:
                missed += 1
        if seen_frames >= len(want):
            break

    tot = found + missed
    if tot == 0:
        # The first version of this probe matched on frame_index against an
        # iterator opened at t=0, replayed nothing, and printed "0% / 0%" with
        # exit code 0 — a result that reads like a finding. Fail loudly instead.
        raise SystemExit(
            f"replayed NOTHING: 0 of {len(want)} target frames matched. "
            f"This is a probe bug, not a measurement — do not read the zeros "
            f"as 'the detector found nothing'.")
    print(f"\nframes replayed: {seen_frames}   deaths checked: {tot}")
    print(f"  YOLO DID find a person within {args.radius_m} m : {found} "
          f"({100*found/max(tot,1):.0f}%)  <- tracker dropped it")
    print(f"  YOLO found nothing there                  : {missed} "
          f"({100*missed/max(tot,1):.0f}%)  <- detector miss")
    if conf_found:
        c = np.array(conf_found)
        print(f"\n  confidence of the re-found boxes: median {np.median(c):.2f}, "
              f"p25 {np.percentile(c,25):.2f}, min {c.min():.2f}")
        print(f"  below BotSort new_track_thresh 0.50: "
              f"{100*(c<0.50).mean():.0f}%  (cannot start a new track)")
        print(f"  below track_high_thresh 0.45       : {100*(c<0.45).mean():.0f}%")


if __name__ == "__main__":
    main()
