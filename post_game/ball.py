"""Phase-0 hybrid ball detector + step-6.5 hit-rate gate.

Port of `tracking/phase0_validation.ipynb` — gated module that the pipeline
only consumes after we measure ≥40% ball hit-rate on a real game.

Pass 1 (cheap): YOLO person detection on a 1280×720 center-crop of the
equirectangular frame → confidence-weighted player centroid → coarse camera
aim (lon, lat). EMA-smoothed with dead-zone, then 15-sample moving average.

Pass 2: YOLO ball-only detection on a 1280×720 perspective crop rendered at
the aim from pass 1. Cropped viewport makes the ball ~40-60 px (vs ~10 px
in the full sphere) so YOLO actually sees it.

`hit_rate_report` returns a gate verdict: go / go-with-finetune / shelve.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pandas as pd

from . import config
from .detection import Detector
from .video import crop_to_equirect_pixel, render_perspective

log = logging.getLogger(__name__)


@dataclass
class BallHitRateReport:
    total_sampled_frames: int
    frames_with_ball: int
    hit_rate: float                    # 0..1
    avg_gap_seconds: float
    longest_gap_seconds: float
    verdict: str                       # "go" | "go-with-finetune" | "shelve"
    notes: str = ""


# --- pass 1: player centroid -> camera aim --------------------------------

def _player_centroid_lonlat(
    eq_frame: np.ndarray, detector: Detector
) -> Optional[tuple[float, float]]:
    """Quick center-crop person detection → confidence-weighted centroid in
    equirectangular (lon_deg, lat_deg)."""
    h, w = eq_frame.shape[:2]
    x1, x2 = int(w * 0.25), int(w * 0.75)
    y1, y2 = int(h * 0.25), int(h * 0.75)
    center = eq_frame[y1:y2, x1:x2]
    dets = detector.detect_persons([center])
    if not dets or not dets[0]:
        return None
    xs, ys, ws = [], [], []
    for d in dets[0]:
        bx1, by1, bx2, by2 = d.bbox_crop
        # Centroid in original equirectangular coords (offset back by crop origin)
        cx = x1 + (bx1 + bx2) / 2.0
        cy = y1 + by2  # foot point
        xs.append(cx); ys.append(cy); ws.append(d.confidence)
    if not ws:
        return None
    w_arr = np.array(ws, dtype=np.float64)
    cx = float(np.average(xs, weights=w_arr))
    cy = float(np.average(ys, weights=w_arr))
    lon_deg = (cx / w - 0.5) * 360.0
    lat_deg = (0.5 - cy / h) * 180.0
    return lon_deg, lat_deg


def _ema_smooth(values: list[Optional[tuple[float, float]]], alpha: float, dead_zone_deg: float):
    out: list[Optional[tuple[float, float]]] = []
    last_lon = None
    last_lat = None
    for v in values:
        if v is None:
            out.append((last_lon, last_lat) if last_lon is not None else None)
            continue
        lon, lat = v
        if last_lon is None:
            last_lon, last_lat = lon, lat
        else:
            # Unwrap longitude jump across the seam
            d = lon - last_lon
            if d > 180: d -= 360
            elif d < -180: d += 360
            if abs(d) > dead_zone_deg:
                last_lon = (last_lon + alpha * d + 540) % 360 - 180
            dl = lat - last_lat
            if abs(dl) > dead_zone_deg:
                last_lat = last_lat + alpha * dl
        out.append((last_lon, last_lat))
    return out


def _moving_avg(values: list[Optional[tuple[float, float]]], window: int):
    arr_lon = np.array([v[0] if v else np.nan for v in values], dtype=np.float64)
    arr_lat = np.array([v[1] if v else np.nan for v in values], dtype=np.float64)
    arr_lon_uw = np.unwrap(np.radians(np.where(np.isnan(arr_lon), 0.0, arr_lon)))
    arr_lon_uw = np.degrees(arr_lon_uw)
    if window > 1:
        k = np.ones(window) / window
        arr_lon_uw = np.convolve(arr_lon_uw, k, mode="same")
        arr_lat = np.convolve(np.where(np.isnan(arr_lat), 0.0, arr_lat), k, mode="same")
    arr_lon_uw = ((arr_lon_uw + 180.0) % 360.0) - 180.0
    return [(float(lo), float(la)) for lo, la in zip(arr_lon_uw, arr_lat)]


# --- pass 2: ball detection on perspective crop --------------------------

def detect_ball(video_path: str, sample_rate: int = config.SAMPLE_RATE) -> pd.DataFrame:
    """Run the phase-0 hybrid ball detector over an entire video.

    Returns DataFrame with columns: frame, time_s, x_eq, y_eq, confidence.
    Frames where no ball was found are still present, with NaN x/y/conf.
    """
    detector = Detector()
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eq_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    eq_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Pass 1: collect player-centroid aims for every sampled frame.
    aims_raw: list[Optional[tuple[float, float]]] = []
    sample_indices: list[int] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % sample_rate == 0:
            aims_raw.append(_player_centroid_lonlat(frame, detector))
            sample_indices.append(idx)
        idx += 1
    cap.release()

    aims = _ema_smooth(aims_raw, alpha=0.04, dead_zone_deg=4.0)
    aims = _moving_avg(aims, window=15)

    # Pass 2: re-read video, render perspective crop at aim, detect ball.
    cap = cv2.VideoCapture(str(video_path))
    rows = []
    idx = 0
    sample_i = 0
    while sample_i < len(sample_indices):
        ok, frame = cap.read()
        if not ok:
            break
        if idx == sample_indices[sample_i]:
            lon, lat = aims[sample_i]
            crop = render_perspective(frame, lon, lat, config.CROP_FOV_DEG, config.CROP_W, config.CROP_H)
            ball_dets = detector.detect_ball([crop])
            best = max(ball_dets[0], key=lambda d: d.confidence) if (ball_dets and ball_dets[0]) else None
            if best:
                cx_crop = (best.bbox_crop[0] + best.bbox_crop[2]) / 2.0
                cy_crop = (best.bbox_crop[1] + best.bbox_crop[3]) / 2.0
                u_eq, v_eq = crop_to_equirect_pixel(
                    cx_crop, cy_crop, lon, lat, config.CROP_FOV_DEG,
                    eq_w, eq_h, config.CROP_W, config.CROP_H,
                )
                rows.append({
                    "frame": idx, "time_s": idx / fps,
                    "x_eq": u_eq, "y_eq": v_eq, "confidence": best.confidence,
                })
            else:
                rows.append({
                    "frame": idx, "time_s": idx / fps,
                    "x_eq": np.nan, "y_eq": np.nan, "confidence": np.nan,
                })
            sample_i += 1
        idx += 1
    cap.release()
    return pd.DataFrame(rows)


# --- step 6.5 gate -------------------------------------------------------

def hit_rate_report(ball_df: pd.DataFrame, total_frames: int, fps: float) -> BallHitRateReport:
    if ball_df.empty or total_frames <= 0:
        return BallHitRateReport(0, 0, 0.0, 0.0, 0.0, "shelve", "Empty ball detection output.")

    sampled = len(ball_df)
    hits_mask = ball_df["confidence"].notna()
    hits = int(hits_mask.sum())
    rate = hits / sampled if sampled else 0.0

    # Gap analysis: contiguous runs of NaN.
    times = ball_df["time_s"].to_numpy()
    flags = hits_mask.to_numpy()
    gaps: list[float] = []
    in_gap = False
    gap_start_t = 0.0
    for i, hit in enumerate(flags):
        if not hit:
            if not in_gap:
                in_gap = True
                gap_start_t = float(times[i])
        else:
            if in_gap:
                gaps.append(float(times[i]) - gap_start_t)
                in_gap = False
    if in_gap and len(times):
        gaps.append(float(times[-1]) - gap_start_t)
    avg_gap = float(np.mean(gaps)) if gaps else 0.0
    longest_gap = float(np.max(gaps)) if gaps else 0.0

    if rate >= 0.40:
        verdict = "go"
        notes = "Above 40% — wire ball into possession + pass network."
    elif rate >= 0.20:
        verdict = "go-with-finetune"
        notes = "20-40% — consider fine-tuning TrackNetV3 on a few labeled minutes before step 7."
    else:
        verdict = "shelve"
        notes = "Below 20% — shelve step 7. Revisit with yolov8m or a soccer-specific ball model."

    return BallHitRateReport(
        total_sampled_frames=sampled,
        frames_with_ball=hits,
        hit_rate=float(rate),
        avg_gap_seconds=avg_gap,
        longest_gap_seconds=longest_gap,
        verdict=verdict,
        notes=notes,
    )


def write_report_md(report: BallHitRateReport, game_id: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    md = (
        f"# Ball detection report — game `{game_id}`\n\n"
        f"- Sampled frames: **{report.total_sampled_frames}**\n"
        f"- Frames with ball: **{report.frames_with_ball}**\n"
        f"- **Hit rate: {report.hit_rate * 100:.1f}%**\n"
        f"- Avg gap: {report.avg_gap_seconds:.2f}s\n"
        f"- Longest gap: {report.longest_gap_seconds:.2f}s\n\n"
        f"## Verdict: `{report.verdict}`\n\n"
        f"{report.notes}\n"
    )
    out_path.write_text(md)
    return out_path
