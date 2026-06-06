"""Prototype #1b: motion + PLAYER-MASKING + ballistic multi-track.

Adds the two ingredients prototype #1 lacked:
  (a) Player masking — persons detected on the undistorted TV crop; any motion
      blob that projects onto a player box is dropped. Motion already killed the
      static white lines; this kills the running-limb blobs. What survives is a
      moving blob in OPEN space ≈ the ball (pass/shot/loose ball).
  (b) Ballistic multi-track — keep several candidate tracks, extend them with
      gated candidates, and output the CONFIRMED track that moves most smoothly,
      instead of greedily snapping a single Kalman to the nearest blob.

Tracking is in pan-invariant equirect pixel space; the crop is used only for
player masking + drawing + the COCO cross-check.
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
from tracking.ball_motion_track import lonlat_to_crop_px, make_kalman

VIDEO = "/Users/irezaeian/Movies/stompers/20260603/stompers-20260603.mp4"
OUT = "/Users/irezaeian/match-tracker/post_game/outputs/ball_motion2_h2early.mp4"

WIN_START, WIN_END = 2250, 2310
FIELD_LAT_TOP, FIELD_LAT_BOT = -5.0, -48.0

AREA_MIN, AREA_MAX = 5, 500
MAX_BLOB_WH = 40
PERSON_PAD = 14            # px dilation on player boxes (crop space)
GATE_BASE = 70.0          # equirect px
MAX_MISS = 12
MIN_HITS = 5              # frames before a track is "confirmed" as ball-eligible
MIN_TRAVEL = 40.0         # a real ball track must move >= this over its life (equirect px)
COCO_MATCH_PX = 60.0


class Track:
    _next = 0

    def __init__(self, u, v):
        self.kf = make_kalman()
        self.kf.statePost = np.array([[u], [v], [0], [0]], np.float32)
        self.hits = 1
        self.misses = 0
        self.age = 1
        self.pos = (u, v)
        self.hist = [(u, v)]
        self.id = Track._next
        Track._next += 1

    def predict(self):
        p = self.kf.predict()
        return float(p[0, 0]), float(p[1, 0])

    def correct(self, u, v):
        self.kf.correct(np.array([[u], [v]], np.float32))
        self.pos = (u, v)
        self.hits += 1
        self.misses = 0
        self.age += 1
        self.hist.append((u, v))

    def coast(self, pu, pv):
        self.pos = (pu, pv)
        self.misses += 1
        self.age += 1
        self.hist.append((pu, pv))

    def travel(self):
        xs = [p[0] for p in self.hist]
        ys = [p[1] for p in self.hist]
        return ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5

    def jitter(self):
        """Mean direction-change between consecutive steps (lower = smoother/ballistic)."""
        h = self.hist[-12:]
        if len(h) < 3:
            return 1.0
        angs = []
        for i in range(2, len(h)):
            a = np.array(h[i - 1]) - np.array(h[i - 2])
            b = np.array(h[i]) - np.array(h[i - 1])
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if na < 1e-3 or nb < 1e-3:
                continue
            cosang = np.clip(np.dot(a, b) / (na * nb), -1, 1)
            angs.append(math.acos(cosang))
        return float(np.mean(angs)) if angs else 1.0

    def score(self):
        return self.hits / (1.0 + self.jitter()) - 1.5 * self.misses


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
    stride = config.SAMPLE_RATE

    # Pass 1: aim per sampled frame.
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

    # Pass 2.
    mog = cv2.createBackgroundSubtractorMOG2(history=250, varThreshold=30, detectShadows=False)
    tracks: list[Track] = []
    writer = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                            (config.CROP_W, config.CROP_H))

    n = active = 0
    coco_frames = coco_hits = 0
    coco_pairs = []

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    idx = start_f
    while idx < end_f:
        ok, frame = cap.read()
        if not ok:
            break
        lon, lat = aim_at(idx)
        crop = render_perspective(frame, lon, lat, config.CROP_FOV_DEG, config.CROP_W, config.CROP_H)

        # players on the undistorted crop
        pdets = detector.detect_persons([crop])
        pboxes = []
        for d in (pdets[0] if pdets else []):
            x1, y1, x2, y2 = d.bbox_crop
            pboxes.append((x1 - PERSON_PAD, y1 - PERSON_PAD, x2 + PERSON_PAD, y2 + PERSON_PAD))

        # motion candidates in equirect band
        band = frame[band_top:band_bot]
        fg = mog.apply(band)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.dilate(fg, np.ones((3, 3), np.uint8), 1)
        ncomp, _, stats, cents = cv2.connectedComponentsWithStats(fg, 8)

        cands = []  # (u_full, v_full, crop_px, crop_py)
        for c in range(1, ncomp):
            area = stats[c, cv2.CC_STAT_AREA]
            if area < AREA_MIN or area > AREA_MAX:
                continue
            if max(stats[c, cv2.CC_STAT_WIDTH], stats[c, cv2.CC_STAT_HEIGHT]) > MAX_BLOB_WH:
                continue
            cx, cy = cents[c]
            u_full, v_full = cx, band_top + cy
            p = lonlat_to_crop_px((u_full / eq_w - 0.5) * 360, (0.5 - v_full / eq_h) * 180,
                                  lon, lat, config.CROP_FOV_DEG, config.CROP_W, config.CROP_H)
            if not p or not (0 <= p[0] < config.CROP_W and 0 <= p[1] < config.CROP_H):
                continue  # outside the action view
            if any(bx1 <= p[0] <= bx2 and by1 <= p[1] <= by2 for (bx1, by1, bx2, by2) in pboxes):
                continue  # on a player
            cands.append((u_full, v_full, p[0], p[1]))

        # ---- ballistic multi-track ----
        preds = [t.predict() for t in tracks]
        used = [False] * len(cands)
        order = sorted(range(len(tracks)), key=lambda k: -tracks[k].hits)
        for ti in order:
            pu, pv = preds[ti]
            gate = GATE_BASE + 6 * tracks[ti].misses
            best, bestd = None, gate
            for ci, (u, v, _, _) in enumerate(cands):
                if used[ci]:
                    continue
                d = ((u - pu) ** 2 + (v - pv) ** 2) ** 0.5
                if d <= bestd:
                    bestd, best = d, ci
            if best is not None:
                u, v, _, _ = cands[best]
                tracks[ti].correct(u, v)
                used[best] = True
            else:
                tracks[ti].coast(pu, pv)
        # spawn new tracks from unassigned candidates
        for ci, (u, v, _, _) in enumerate(cands):
            if not used[ci]:
                tracks.append(Track(u, v))
        # prune
        tracks = [t for t in tracks if t.misses <= MAX_MISS]
        tracks.sort(key=lambda t: -t.score())
        tracks = tracks[:25]

        # choose the ball: confirmed, currently measured, travelled, smoothest
        eligible = [t for t in tracks if t.hits >= MIN_HITS and t.misses == 0
                    and t.travel() >= MIN_TRAVEL]
        ball = max(eligible, key=lambda t: t.score()) if eligible else None

        # ---- draw ----
        for (_, _, cpx, cpy) in cands:
            cv2.circle(crop, (int(cpx), int(cpy)), 3, (130, 130, 130), 1)
        ball_crop = None
        if ball is not None:
            active += 1
            p = lonlat_to_crop_px((ball.pos[0] / eq_w - 0.5) * 360, (0.5 - ball.pos[1] / eq_h) * 180,
                                  lon, lat, config.CROP_FOV_DEG, config.CROP_W, config.CROP_H)
            if p:
                ball_crop = p
                cv2.circle(crop, (int(p[0]), int(p[1])), 12, (0, 255, 0), 2)
                cv2.line(crop, (int(p[0]) - 16, int(p[1])), (int(p[0]) + 16, int(p[1])), (0, 255, 0), 1)
                cv2.line(crop, (int(p[0]), int(p[1]) - 16), (int(p[0]), int(p[1]) + 16), (0, 255, 0), 1)
                # draw recent trail
                for h in ball.hist[-15:]:
                    tp = lonlat_to_crop_px((h[0] / eq_w - 0.5) * 360, (0.5 - h[1] / eq_h) * 180,
                                           lon, lat, config.CROP_FOV_DEG, config.CROP_W, config.CROP_H)
                    if tp:
                        cv2.circle(crop, (int(tp[0]), int(tp[1])), 1, (0, 200, 0), -1)

        if (idx - start_f) % stride == 0:
            dets = detector.detect_ball([crop])
            best = max(dets[0], key=lambda d: d.confidence) if (dets and dets[0]) else None
            if best is not None:
                coco_frames += 1
                cxb = (best.bbox_crop[0] + best.bbox_crop[2]) / 2
                cyb = (best.bbox_crop[1] + best.bbox_crop[3]) / 2
                cv2.rectangle(crop, (int(best.bbox_crop[0]), int(best.bbox_crop[1])),
                              (int(best.bbox_crop[2]), int(best.bbox_crop[3])), (0, 140, 255), 1)
                if ball_crop is not None:
                    d = ((cxb - ball_crop[0]) ** 2 + (cyb - ball_crop[1]) ** 2) ** 0.5
                    coco_pairs.append(d)
                    if d <= COCO_MATCH_PX:
                        coco_hits += 1

        cv2.putText(crop, f"t={idx/fps:6.1f}s  cands={len(cands)} tracks={len(tracks)} "
                          f"ball={'Y' if ball else '-'}", (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        writer.write(crop)
        n += 1
        idx += 1

    cap.release()
    writer.release()

    print(f"\nWindow {WIN_START}-{WIN_END}s  frames={n}")
    print(f"  Ball-following frames: {active}/{n} = {100*active/n:.1f}%")
    if coco_pairs:
        arr = np.array(coco_pairs)
        print(f"  COCO cross-check: {coco_frames} COCO-ball frames; co-present={len(arr)}, "
              f"median dist={np.median(arr):.0f}px, agree(<{int(COCO_MATCH_PX)}px)={100*coco_hits/len(arr):.0f}%")
    else:
        print(f"  COCO cross-check: {coco_frames} COCO-ball frames, no co-present track frames.")
    print(f"  Preview: {OUT}")


if __name__ == "__main__":
    main()
