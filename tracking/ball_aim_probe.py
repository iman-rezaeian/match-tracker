#!/usr/bin/env python3
"""Probe the ball-aware aim (post_game/tv_ball.py) on raw footage, no tracks.

Validates the detect→track→bias chain end-to-end on a real video window
WITHOUT a pipeline run: the base aim comes from the quick center-crop player
centroid (same bootstrap build_ball_dataset uses), then extract_ball_records
+ blend_ball_aim run exactly as the reel render would. Writes annotated JPEGs
(detection box + track state) so a human can confirm the confirmed track sits
on the actual ball, and prints fix/confirmed rates + bias stats.

Usage:
  python -m tracking.ball_aim_probe --video ~/Movies/x.mp4 --start 600 --dur 60
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import typer

from post_game import config
from post_game.ball import _player_centroid_lonlat
from post_game.detection import Detector
from post_game.tv_ball import (BallAimConfig, blend_ball_aim,
                               extract_ball_records)
from post_game.video import render_perspective

app = typer.Typer(add_completion=False)


@app.command()
def probe(
    video: Path = typer.Option(..., "--video"),
    start: float = typer.Option(600.0, "--start"),
    dur: float = typer.Option(60.0, "--dur"),
    out_dir: Path = typer.Option(Path("/tmp/ball_aim_probe"), "--out"),
):
    a, b = float(start), float(start + dur)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise typer.BadParameter(f"cannot open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # Base aim: centroid samples at 0.5 Hz, linearly interped to 5 Hz.
    det = Detector()
    cs_t, cs_lon, cs_lat = [], [], []
    for t in np.arange(a, b, 2.0):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        aim = _player_centroid_lonlat(frame, det)
        if aim:
            cs_t.append(t)
            cs_lon.append(aim[0])
            cs_lat.append(aim[1])
    if len(cs_t) < 3:
        raise typer.BadParameter("no player centroid found in window")
    # Unwrap the centroid lons so interp can't sweep through the seam.
    cs_lon = np.degrees(np.unwrap(np.radians(np.array(cs_lon))))
    aim_times = np.arange(a, b, 0.2)
    aim_lons = np.interp(aim_times, cs_t, cs_lon)
    aim_lats = np.interp(aim_times, cs_t, np.array(cs_lat))
    aim_fovs = np.full_like(aim_times, 70.0)

    records = extract_ball_records(cap, a, b, aim_times, aim_lons, aim_lats)
    lons2, lats2, fovs2, stats = blend_ball_aim(
        aim_times, aim_lons, aim_lats, aim_fovs, records)
    n = len(records)
    n_det = sum(1 for r in records if r["det"])
    n_conf = sum(1 for r in records if r["confirmed"])
    print(f"window [{a:.0f},{b:.0f}]s: {n} samples | fixes {n_det} "
          f"({100 * n_det / n:.0f}%) | confirmed {n_conf} ({100 * n_conf / n:.0f}%)")
    print(f"blend: confirmed_frac={stats['confirmed_frac']:.2f} "
          f"mean|bias|={stats['mean_abs_bias_deg']:.1f} deg "
          f"fov {fovs2.min():.0f}-{fovs2.max():.0f}")

    # Annotated stills at confirmed samples: render the FINAL aim crop and
    # mark the tracked ball so a human can verify it is the actual ball.
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for r in records:
        if not r["confirmed"] or saved >= 12:
            continue
        t = r["t"]
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        lon = float(np.interp(t, aim_times, lons2))
        lat = float(np.interp(t, aim_times, lats2))
        fov = float(np.interp(t, aim_times, fovs2))
        crop = render_perspective(frame, ((lon + 180) % 360) - 180, lat,
                                  fov, 1280, 720)
        # Project the tracked ball into this crop (forward math from
        # tv_view's chip projection): rotate the ball ray into camera frame.
        import math as m
        bl, bb = r["pred"]
        f = 1280 / (2.0 * m.tan(m.radians(fov) / 2.0))
        dlon = m.radians((((bl - lon) + 180) % 360) - 180)
        lat_r = m.radians(-lat)
        bx = m.cos(m.radians(bb)) * m.sin(dlon)
        by = m.sin(m.radians(bb))
        bz = m.cos(m.radians(bb)) * m.cos(dlon)
        y1 = by * m.cos(-lat_r) - bz * m.sin(-lat_r)
        z1 = by * m.sin(-lat_r) + bz * m.cos(-lat_r)
        if z1 > 0.01:
            px = int(1280 / 2 + f * bx / z1)
            py = int(720 / 2 - f * y1 / z1)
            cv2.circle(crop, (px, py), 24, (0, 0, 255), 3)
        cv2.putText(crop, f"t={t:.1f}s conf_track", (16, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        cv2.imwrite(str(out_dir / f"conf_{t:07.1f}.jpg"), crop,
                    [cv2.IMWRITE_JPEG_QUALITY, 88])
        saved += 1
    print(f"annotated stills: {saved} -> {out_dir}")
    cap.release()


if __name__ == "__main__":
    app()
