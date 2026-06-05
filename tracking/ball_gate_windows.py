"""Windowed phase-0 ball-detection gate.

Runs the EXACT production hybrid detector (post_game.ball logic, yolo11s) on a
handful of representative in-play windows of a full game instead of the whole
67 GB file. Seeks to each window via CAP_PROP_POS_FRAMES so we never decode the
whole video. Prints per-window + overall hit-rate and the step-6.5 verdict.

Usage:
  python -m tracking.ball_gate_windows            # default: Windsor Fury game
"""
from __future__ import annotations

import sys
import cv2
import numpy as np

from post_game import config
from post_game.ball import _player_centroid_lonlat, _ema_smooth, _moving_avg, hit_rate_report
from post_game.detection import Detector
from post_game.video import render_perspective, crop_to_equirect_pixel

VIDEO = "/Users/irezaeian/Movies/stompers/20260603/stompers-20260603.mp4"

# In-play windows (video seconds). H1 kickoff=98s, H2 kickoff=2140s, halves=30min.
WINDOWS = [
    ("H1 early", 200, 260),
    ("H1 mid", 1000, 1060),
    ("H2 early", 2250, 2310),
    ("H2 mid", 3000, 3060),
]


def run_window(detector: Detector, cap, fps: float, eq_w: int, eq_h: int,
               start_s: float, end_s: float):
    start_f = int(start_s * fps)
    end_f = int(end_s * fps)
    stride = config.SAMPLE_RATE

    # Read every frame in [start_f, end_f), keep ones on the sample stride.
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    frames, idxs = [], []
    idx = start_f
    while idx < end_f:
        ok, frame = cap.read()
        if not ok:
            break
        if (idx - start_f) % stride == 0:
            frames.append(frame)
            idxs.append(idx)
        idx += 1

    # Pass 1: player-centroid aim per sampled frame.
    aims_raw = [_player_centroid_lonlat(f, detector) for f in frames]
    aims = _ema_smooth(aims_raw, alpha=0.04, dead_zone_deg=4.0)
    aims = _moving_avg(aims, window=15)

    # Pass 2: ball detection on perspective crop at the aim.
    hits = 0
    confs = []
    for frame, (lon, lat) in zip(frames, aims):
        crop = render_perspective(frame, lon, lat, config.CROP_FOV_DEG,
                                  config.CROP_W, config.CROP_H)
        ball_dets = detector.detect_ball([crop])
        best = max(ball_dets[0], key=lambda d: d.confidence) if (ball_dets and ball_dets[0]) else None
        if best:
            hits += 1
            confs.append(best.confidence)
    n = len(frames)
    return n, hits, confs


def main():
    detector = Detector()
    cap = cv2.VideoCapture(VIDEO)
    if not cap.isOpened():
        sys.exit(f"Cannot open {VIDEO}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eq_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    eq_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    print(f"Model: {config.YOLO_MODEL}  device: {config.DEVICE}  "
          f"sample_rate: {config.SAMPLE_RATE} (~{fps/config.SAMPLE_RATE:.0f}Hz)")
    print(f"Crop: {config.CROP_W}x{config.CROP_H} @ {config.CROP_FOV_DEG}deg FOV\n")

    tot_n = tot_hits = 0
    all_confs = []
    for label, a, b in WINDOWS:
        n, hits, confs = run_window(detector, cap, fps, eq_w, eq_h, a, b)
        tot_n += n
        tot_hits += hits
        all_confs += confs
        rate = 100 * hits / n if n else 0
        mc = np.mean(confs) if confs else 0
        print(f"  {label:9s} [{a:4d}-{b:4d}s]  {hits:4d}/{n:4d} = {rate:5.1f}%   "
              f"avg_conf={mc:.3f}")

    cap.release()
    overall = 100 * tot_hits / tot_n if tot_n else 0
    print(f"\n  OVERALL    {tot_hits}/{tot_n} = {overall:.1f}%   "
          f"avg_conf={np.mean(all_confs) if all_confs else 0:.3f}")

    # Verdict using the same thresholds as the production gate.
    if overall / 100 >= 0.40:
        verdict = "go — wire ball into possession + pass network"
    elif overall / 100 >= 0.20:
        verdict = "go-with-finetune — fine-tune a ball model before step 7"
    else:
        verdict = "shelve — below 20%, revisit with a soccer-specific ball model"
    print(f"\n  VERDICT: {verdict}")


if __name__ == "__main__":
    main()
