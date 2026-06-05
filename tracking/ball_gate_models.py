"""Compare ball detectors on real game windows (step-6.5 gate).

Pass-1 aim is identical for every candidate (COCO yolo11s person centroid), so
the only variable is the pass-2 ball model. Each window is decoded twice total
(pass1 + pass2); all candidate ball models run on the SAME rendered crop, so
adding models costs no extra video decoding.

Candidates:
  - COCO yolo11s          (baseline, class 32 'sports ball')
  - soccer_ball_yolo11n   (martinjolif, fine-tuned single-class 'ball')
  - football_players_yolov8 (uisikdag, ball class auto-detected by name)
"""
from __future__ import annotations

import cv2
import numpy as np
from ultralytics import YOLO

from post_game import config
from post_game.ball import _player_centroid_lonlat, _ema_smooth, _moving_avg
from post_game.detection import Detector
from post_game.video import render_perspective

VIDEO = "/Users/irezaeian/Movies/stompers/20260603/stompers-20260603.mp4"
MODELS_DIR = "/Users/irezaeian/match-tracker/post_game/models"

WINDOWS = [
    ("H1 early", 200, 260),
    ("H1 mid", 1000, 1060),
    ("H2 early", 2250, 2310),
    ("H2 mid", 3000, 3060),
]

BALL_CONF = 0.15  # same low threshold as production detect_ball


def ball_class_ids(model) -> list[int] | None:
    """Class ids whose name mentions 'ball'. None means single-class → all."""
    names = model.names
    if len(names) == 1:
        return None
    ids = [i for i, n in names.items() if "ball" in str(n).lower()]
    return ids or None


def best_ball_conf(model, crop, classes):
    res = model.predict(crop, classes=classes, conf=BALL_CONF,
                        device=config.DEVICE, verbose=False)
    if not res or res[0].boxes is None or len(res[0].boxes) == 0:
        return None
    return float(max(b.conf[0].item() for b in res[0].boxes))


def main():
    aim_detector = Detector()  # COCO yolo11s for pass-1 person centroid

    candidates = []
    coco = YOLO(config.YOLO_MODEL)
    candidates.append(("COCO yolo11s", coco, [config.BALL_CLASS_ID]))
    soccer = YOLO(f"{MODELS_DIR}/soccer_ball_yolo11n.pt")
    candidates.append(("soccer_ball_y11n", soccer, ball_class_ids(soccer)))
    players = YOLO(f"{MODELS_DIR}/football_players_yolov8.pt")
    candidates.append(("uisikdag_v8(ball)", players, ball_class_ids(players)))

    print("Candidates + ball classes:")
    for name, m, cls in candidates:
        print(f"  {name:18s} classes={cls}  names={m.names}")
    print()

    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    stride = config.SAMPLE_RATE

    totals = {name: 0 for name, _, _ in candidates}
    grand_n = 0
    per_window = []

    for label, a, b in WINDOWS:
        start_f, end_f = int(a * fps), int(b * fps)

        # Pass 1: person-centroid aim per sampled frame.
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
        aims_raw, idx = [], start_f
        while idx < end_f:
            ok, frame = cap.read()
            if not ok:
                break
            if (idx - start_f) % stride == 0:
                aims_raw.append(_player_centroid_lonlat(frame, aim_detector))
            idx += 1
        aims = _moving_avg(_ema_smooth(aims_raw, 0.04, 4.0), 15)

        # Pass 2: re-decode, render crop once, run every candidate on it.
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
        hits = {name: 0 for name, _, _ in candidates}
        n, idx, si = 0, start_f, 0
        while idx < end_f and si < len(aims):
            ok, frame = cap.read()
            if not ok:
                break
            if (idx - start_f) % stride == 0:
                lon, lat = aims[si]
                crop = render_perspective(frame, lon, lat, config.CROP_FOV_DEG,
                                          config.CROP_W, config.CROP_H)
                for name, model, classes in candidates:
                    if best_ball_conf(model, crop, classes) is not None:
                        hits[name] += 1
                n += 1
                si += 1
            idx += 1

        grand_n += n
        for name in totals:
            totals[name] += hits[name]
        per_window.append((label, n, dict(hits)))
        line = f"  {label:9s} [{a:4d}-{b:4d}s] n={n:3d}  " + "  ".join(
            f"{name}={100*hits[name]/n:5.1f}%" for name, _, _ in candidates)
        print(line)

    cap.release()
    print(f"\n  {'OVERALL':9s}            n={grand_n:3d}  " + "  ".join(
        f"{name}={100*totals[name]/grand_n:5.1f}%" for name, _, _ in candidates))

    print("\n  Gate (>=20% finetune, >=40% go):")
    for name, _, _ in candidates:
        r = totals[name] / grand_n if grand_n else 0
        v = "go" if r >= 0.40 else ("go-with-finetune" if r >= 0.20 else "shelve")
        print(f"    {name:18s} {100*r:5.1f}%  -> {v}")


if __name__ == "__main__":
    main()
