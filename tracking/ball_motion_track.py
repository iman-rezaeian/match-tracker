"""Prototype #1: static-camera background subtraction + Kalman ball tracker.

The tripod is fixed, so the equirectangular background is truly static — frame
differencing isolates moving objects almost for free, and (bonus) the static
white field lines vanish in the motion mask. We:

  Pass 1: player-centroid aim per sampled frame (reuse), for the TV crop only.
  Pass 2: MOG2 background subtraction on the equirect FIELD BAND (spectators
          cropped out by latitude) -> small/round/bright moving blobs ->
          Kalman track with motion gating + coasting through misses.

Outputs an annotated TV-crop preview (green=measured, yellow=coasting, faint
dots=raw motion candidates) + continuity stats + a weak cross-check vs COCO
ball detections on the crop.
"""
from __future__ import annotations

import math
import sys
import cv2
import numpy as np

from post_game import config
from post_game.ball import _player_centroid_lonlat, _ema_smooth, _moving_avg
from post_game.detection import Detector
from post_game.video import render_perspective

VIDEO = "/Users/irezaeian/Movies/stompers/20260603/stompers-20260603.mp4"
OUT = "/Users/irezaeian/match-tracker/post_game/outputs/ball_motion_h2early.mp4"

WIN_START, WIN_END = 2250, 2310           # H2-early (ball known visible)
FIELD_LAT_TOP, FIELD_LAT_BOT = -5.0, -48.0  # exclude spectators above horizon

# Blob filter (equirect band pixels)
AREA_MIN, AREA_MAX = 5, 500
# Kalman / track
GATE_BASE = 80.0          # px gating radius around prediction
MAX_MISS = 15             # coast up to 0.5s before declaring lost
COCO_MATCH_PX = 60.0      # crop-px threshold for "agrees with COCO"


def lonlat_to_crop_px(lon_b, lat_b, cam_lon, cam_lat, fov, out_w, out_h):
    """Inverse of render_perspective for one point. Returns (px,py) or None if behind."""
    # ball world unit vector (matches render: lon=atan2(X,Z), lat=asin(Y))
    lar, lor = math.radians(lat_b), math.radians(lon_b)
    X = math.cos(lar) * math.sin(lor)
    Y = math.sin(lar)
    Z = math.cos(lar) * math.cos(lor)
    # inverse rotations: ray = Rpitch(-lat_r) @ Ryaw(-lon_r) @ world
    lon_r = math.radians(cam_lon)
    lat_r = math.radians(-cam_lat)
    co, so = math.cos(lon_r), math.sin(lon_r)
    # Ryaw(-lon_r) @ (X,Y,Z)
    x1 = X * co - Z * so
    y1 = Y
    z1 = X * so + Z * co
    cb, sb = math.cos(lat_r), math.sin(lat_r)
    # Rpitch(-lat_r) @ (x1,y1,z1)
    xr = x1
    yr = y1 * cb + z1 * sb
    zr = -y1 * sb + z1 * cb
    if zr <= 1e-6:
        return None
    f = out_w / (2 * math.tan(math.radians(fov) / 2))
    xv = xr / zr * f
    yv = yr / zr * f
    return xv + out_w / 2, out_h / 2 - yv


def make_kalman():
    kf = cv2.KalmanFilter(4, 2)
    kf.transitionMatrix = np.array([[1, 0, 1, 0], [0, 1, 0, 1],
                                    [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
    kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], np.float32)
    kf.processNoiseCov = np.diag([1, 1, 16, 16]).astype(np.float32)   # ball accelerates on kicks
    kf.measurementNoiseCov = np.diag([9, 9]).astype(np.float32)
    return kf


def main():
    cap = cv2.VideoCapture(VIDEO)
    if not cap.isOpened():
        sys.exit("cannot open video")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eq_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    eq_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    start_f, end_f = int(WIN_START * fps), int(WIN_END * fps)
    band_top = int((0.5 - FIELD_LAT_TOP / 180.0) * eq_h)
    band_bot = int((0.5 - FIELD_LAT_BOT / 180.0) * eq_h)

    detector = Detector()

    # Pass 1: aims on sample stride -> smooth -> per-frame lookup.
    stride = config.SAMPLE_RATE
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    aims_raw, samp_idx, idx = [], [], start_f
    while idx < end_f:
        ok, frame = cap.read()
        if not ok:
            break
        if (idx - start_f) % stride == 0:
            aims_raw.append(_player_centroid_lonlat(frame, detector))
            samp_idx.append(idx)
        idx += 1
    aims = _moving_avg(_ema_smooth(aims_raw, 0.04, 4.0), 15)

    def aim_at(i):
        k = min(range(len(samp_idx)), key=lambda j: abs(samp_idx[j] - i))
        return aims[k]

    # Pass 2: MOG2 motion -> candidates -> Kalman.
    mog = cv2.createBackgroundSubtractorMOG2(history=250, varThreshold=30, detectShadows=False)
    kf = make_kalman()
    track_active = False
    misses = 0
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(OUT, fourcc, fps, (config.CROP_W, config.CROP_H))

    n = active = 0
    coco_pairs = []   # (dist_px) when both COCO + track present
    coco_frames = coco_hits = 0

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    idx = start_f
    gray_band_prev = None
    while idx < end_f:
        ok, frame = cap.read()
        if not ok:
            break
        band = frame[band_top:band_bot]
        fg = mog.apply(band)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.dilate(fg, np.ones((3, 3), np.uint8), iterations=1)
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)

        ncomp, _, stats, cents = cv2.connectedComponentsWithStats(fg, connectivity=8)
        cands = []  # (u_full, v_full, score)
        for c in range(1, ncomp):
            area = stats[c, cv2.CC_STAT_AREA]
            if area < AREA_MIN or area > AREA_MAX:
                continue
            w_, h_ = stats[c, cv2.CC_STAT_WIDTH], stats[c, cv2.CC_STAT_HEIGHT]
            if max(w_, h_) > 40:        # too elongated/large to be the ball
                continue
            cx, cy = cents[c]
            roundness = area / (w_ * h_ + 1e-6)
            bright = gray[int(cy), int(cx)] / 255.0
            score = (0.5 + bright) * (0.3 + roundness) / (1 + area / 40.0)
            cands.append((cx, band_top + cy, score))

        # Kalman predict
        pred = kf.predict()
        px, py = float(pred[0]), float(pred[1])

        chosen = None
        if track_active:
            gate = GATE_BASE + 8 * misses
            # pick nearest candidate within gate
            best = None
            bestd = gate
            for (u, v, s) in cands:
                d = ((u - px) ** 2 + (v - py) ** 2) ** 0.5
                if d <= bestd:
                    bestd, best = d, (u, v)
            if best is not None:
                kf.correct(np.array([[best[0]], [best[1]]], np.float32))
                chosen = best
                misses = 0
            else:
                misses += 1
                chosen = (px, py)  # coast
                if misses > MAX_MISS:
                    track_active = False
        else:
            # acquire: highest-scoring ball-like candidate
            if cands:
                u, v, s = max(cands, key=lambda c: c[2])
                kf.statePost = np.array([[u], [v], [0], [0]], np.float32)
                track_active = True
                misses = 0
                chosen = (u, v)

        # ---- render annotated crop ----
        lon, lat = aim_at(idx)
        crop = render_perspective(frame, lon, lat, config.CROP_FOV_DEG, config.CROP_W, config.CROP_H)
        # faint raw candidates
        for (u, v, s) in cands:
            p = lonlat_to_crop_px((u / eq_w - 0.5) * 360, (0.5 - v / eq_h) * 180,
                                  lon, lat, config.CROP_FOV_DEG, config.CROP_W, config.CROP_H)
            if p and 0 <= p[0] < config.CROP_W and 0 <= p[1] < config.CROP_H:
                cv2.circle(crop, (int(p[0]), int(p[1])), 3, (120, 120, 120), 1)
        ball_crop_px = None
        if track_active and chosen is not None:
            active += 1
            lon_b = (chosen[0] / eq_w - 0.5) * 360
            lat_b = (0.5 - chosen[1] / eq_h) * 180
            p = lonlat_to_crop_px(lon_b, lat_b, lon, lat, config.CROP_FOV_DEG,
                                  config.CROP_W, config.CROP_H)
            if p:
                ball_crop_px = p
                color = (0, 255, 255) if misses else (0, 255, 0)
                cv2.circle(crop, (int(p[0]), int(p[1])), 12, color, 2)
                cv2.line(crop, (int(p[0]) - 16, int(p[1])), (int(p[0]) + 16, int(p[1])), color, 1)
                cv2.line(crop, (int(p[0]), int(p[1]) - 16), (int(p[0]), int(p[1]) + 16), color, 1)

        # ---- COCO cross-check on sample stride ----
        if (idx - start_f) % stride == 0:
            dets = detector.detect_ball([crop])
            best = max(dets[0], key=lambda d: d.confidence) if (dets and dets[0]) else None
            if best is not None:
                coco_frames += 1
                cxb = (best.bbox_crop[0] + best.bbox_crop[2]) / 2
                cyb = (best.bbox_crop[1] + best.bbox_crop[3]) / 2
                cv2.rectangle(crop, (int(best.bbox_crop[0]), int(best.bbox_crop[1])),
                              (int(best.bbox_crop[2]), int(best.bbox_crop[3])), (0, 140, 255), 1)
                if ball_crop_px is not None:
                    d = ((cxb - ball_crop_px[0]) ** 2 + (cyb - ball_crop_px[1]) ** 2) ** 0.5
                    coco_pairs.append(d)
                    if d <= COCO_MATCH_PX:
                        coco_hits += 1

        cv2.putText(crop, f"t={idx/fps:6.1f}s  motion-track {'ACTIVE' if track_active else 'lost'}",
                    (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        writer.write(crop)
        n += 1
        idx += 1

    cap.release()
    writer.release()

    print(f"\nWindow {WIN_START}-{WIN_END}s  frames={n}")
    print(f"  Track continuity (ball-following frames): {active}/{n} = {100*active/n:.1f}%")
    if coco_pairs:
        arr = np.array(coco_pairs)
        print(f"  COCO cross-check: {coco_frames} frames where COCO saw a ball; "
              f"of those co-present with our track, median dist={np.median(arr):.0f}px, "
              f"agree(<{int(COCO_MATCH_PX)}px)={100*coco_hits/len(arr):.0f}% (n={len(arr)})")
    else:
        print("  COCO cross-check: no co-present frames.")
    print(f"  Preview: {OUT}")


if __name__ == "__main__":
    main()
