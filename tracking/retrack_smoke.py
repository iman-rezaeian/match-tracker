#!/usr/bin/env python3
"""Stage-2-only re-track on a smoke window — writes .smoke checkpoints, NO
Firestore writes, NO analytics. Purpose: validate a tracker change (e.g. the
PitchTracker team-color gate) on a short dense window in minutes, then measure
with `eval_stitch_assign --ckpt-suffix smoke` + `eval_swap_mix --npz smoke`,
WITHOUT running the full pipeline (which would overwrite the game's analytics
doc and the full-game cache).

It mirrors post_game.pipeline stage 2 (detection + tracking + jersey sampling)
exactly — same Detector, tile aims, dedupe, sample_jersey_hsv, to_dataframe —
so the .smoke checkpoints are byte-comparable to what a real run would produce
on that window. The tracker is built from config flags (TRACK_PITCH etc.), so
set them via env to A/B:

  # pitch baseline (color gate off, uncapped, legacy px gate)
  TRACK_PITCH=1 PITCH_COLOR_GATE=0 PITCH_GATE_CAP_M=1e9 PITCH_PX_GATE=150 \
    python -m tracking.retrack_smoke --game-id mri01pvelv46d --window 760-880 --tag pbase
  # full fix (defaults: color gate on, cap 6, px 80)
  TRACK_PITCH=1 \
    python -m tracking.retrack_smoke --game-id mri01pvelv46d --window 760-880 --tag pfix

--tag names a distinct checkpoint set (tracks_raw.<tag>.parquet + jersey_samples.<tag>.npz)
so two A/B runs don't clobber each other; pass the SAME tag to the eval tools
via --ckpt-suffix / --npz.
"""
from __future__ import annotations

import argparse
import logging
import os
import time


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--window", required=True, help="'a-b' video seconds, e.g. 760-880")
    ap.add_argument("--tag", default="smoke",
                    help="checkpoint suffix: writes tracks_raw.<tag>.parquet + "
                         "jersey_samples.<tag>.npz (default 'smoke')")
    args = ap.parse_args()
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("retrack_smoke")

    import numpy as np
    from post_game import config, firestore_io
    from post_game.calibration import (
        FieldProjector, compute_tile_aims, dedupe_detections_by_field_position,
    )
    from post_game.detection import Detector
    from post_game.pipeline import _our_color
    from post_game.team_classifier import sample_jersey_hsv
    from post_game.tracking import Tracker, TrackedDetection, to_dataframe
    from post_game.video import crop_bbox_to_equirect, iter_frames, open_video, render_perspective

    a, b = (float(x) for x in args.window.split("-"))
    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    if cal is None:
        raise SystemExit("No calibration.")
    projector = FieldProjector(cal)
    video_path = firestore_io_local_video(game, args.game_id)
    meta = open_video(str(video_path))
    eq_w, eq_h = meta["width"], meta["height"]
    fps_sampled = meta["fps"] / config.SAMPLE_RATE
    tile_aims = compute_tile_aims(projector, cal.length_m, cal.width_m,
                                  n_tiles=config.DETECT_N_TILES, fov_deg=config.DETECT_TILE_FOV_DEG)

    # Build the tracker exactly as pipeline._new_tracker does (flag-gated).
    def _new_tracker():
        if config.TRACK_PITCH:
            from post_game.tracking_pitch import PitchTracker
            return PitchTracker(projector, frame_rate=max(1, int(round(fps_sampled))),
                                track_buffer_frames=int(config.TRACK_BUFFER_S * fps_sampled),
                                our_color_hex=_our_color(game), opp_color_hex=game.away_color)
        if config.TRACK_FIELD_SPACE:
            from post_game.tracking_field import FieldSpaceTracker
            return FieldSpaceTracker(projector, frame_rate=max(1, int(round(fps_sampled))),
                                     track_buffer_frames=int(config.TRACK_BUFFER_S * fps_sampled))
        return Tracker(frame_rate=max(1, int(round(fps_sampled))),
                       track_buffer_frames=int(config.TRACK_BUFFER_S * fps_sampled))

    log.info("Re-track smoke: game=%s window=%.0f-%.0fs TRACK_PITCH=%s COLOR_GATE=%s CAP=%.1f PX=%.0f tag=%s",
             args.game_id, a, b, config.TRACK_PITCH, config.PITCH_COLOR_GATE,
             config.PITCH_GATE_CAP_M, config.PITCH_PX_GATE, args.tag)

    detector = Detector()
    tracker = _new_tracker()
    all_tracks: list[TrackedDetection] = []
    track_jersey_samples: dict[int, list[np.ndarray]] = {}
    t0 = time.time()
    n = 0
    for sample in iter_frames(str(video_path), sample_rate=config.SAMPLE_RATE,
                              windows=[(a, b)], render_crop=False):
        n += 1
        tile_crops = [render_perspective(sample.eq_frame, lon, lat, fov, config.CROP_W, config.CROP_H)
                      for (lon, lat, fov) in tile_aims]
        det_lists = detector.detect_persons(tile_crops)
        dets = []
        for crop_idx, det_list in enumerate(det_lists):
            lon, lat, fov = tile_aims[crop_idx]
            for d in det_list:
                d.frame_index = sample.frame_index
                d.bbox_eq = crop_bbox_to_equirect(d.bbox_crop, lon, lat, fov,
                                                   eq_w, eq_h, config.CROP_W, config.CROP_H)
                d.bbox_crop = d.bbox_eq
                dets.append(d)
        dets = dedupe_detections_by_field_position(dets, projector, config.DETECT_TILE_DEDUPE_M)
        tracked = tracker.update(sample.eq_frame, dets, time_s=sample.time_s)
        for t in tracked:
            all_tracks.append(t)
            hsv = sample_jersey_hsv(sample.eq_frame, t.bbox_eq)
            if len(hsv) > 0:
                track_jersey_samples.setdefault(t.track_id, []).append(hsv)
        if n % 200 == 0:
            log.info("  %d samples, %d tracks, %.0fs elapsed", n,
                     len({t.track_id for t in all_tracks}), time.time() - t0)

    tracks_df = to_dataframe(all_tracks, fps=fps_sampled)
    ckpt = config.OUTPUTS_DIR / args.game_id
    ckpt.mkdir(parents=True, exist_ok=True)
    tp = ckpt / f"tracks_raw.{args.tag}.parquet"
    jp = ckpt / f"jersey_samples.{args.tag}.npz"
    tracks_df.to_parquet(tp)
    # Match pipeline.py:398-401 exactly (np.savez + object arrays) so the .smoke
    # checkpoints read back identically through the eval tools.
    np.savez(jp, **{str(k): np.array(v, dtype=object)
                    for k, v in track_jersey_samples.items()})
    n_kept = getattr(tracker, "n_kept_unprojectable", None)
    log.info("WROTE %s (%d rows, %d tracks) + %s%s", tp, len(tracks_df),
             tracks_df["track_id"].nunique(), jp,
             f"  | kept_unprojectable={n_kept}" if n_kept is not None else "")


def firestore_io_local_video(game, game_id):
    """Resolve the local video path the same way pipeline._ensure_local_video does
    for a file:// url (this smoke tool assumes the raw file is on disk)."""
    from post_game import pipeline
    return pipeline._ensure_local_video(game.video_url, game_id)


if __name__ == "__main__":
    main()
