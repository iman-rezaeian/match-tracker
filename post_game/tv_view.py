"""TV-view + auto-highlight reel renderer.

Two outputs from one pass of the equirectangular game video:

  1. **TV reel** — continuous 1920×1080 virtual broadcast camera over the
     entire game, aimed at the play centroid (mean foot position of all
     on-field tracks at each timestamp). Smoothed so the camera glides
     instead of jitters.

  2. **Auto-highlight reel** — same virtual camera, but only the segments
     within ±`window_s` seconds of GOAL / SHOT_ON / SAVE / KEY_PASS events,
     concatenated via ffmpeg.

Reuses the aim-smoothing + perspective-render pipeline that
`highlights.extract_clips` already proves out.

Aim source today: player centroid (validated in `tracking/phase0_validation.ipynb`).
When Phase 0 ball tracking clears its 40% gate, swap in ball position as the
primary aim with player centroid as fallback.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
import pandas as pd

from . import config, firestore_io
from .firestore_io import CoachEvent
from .highlights import _equirect_pixel_to_lonlat, _field_to_equirect_pixel, _smooth_aim_stream
from .video import render_perspective

log = logging.getLogger(__name__)


# --- config (kept here; promote to config.py if ever tweaked from CLI) ----

TV_AIM_HZ = 5.0          # how often to recompute the virtual-camera aim
TV_SMOOTH_WINDOW = 15    # samples of moving average over the aim stream
TV_FOV_DEG = 100.0       # broadcast feel — wider than per-clip 95° highlights
TV_RESOLUTION = (1920, 1080)
AUTO_HIGHLIGHT_WINDOW_S = 15.0
AUTO_HIGHLIGHT_EVENT_TYPES = ("GOAL", "SHOT_ON", "SAVE", "KEY_PASS")


@dataclass
class TvViewMeta:
    kind: str               # "tv_reel" | "auto_highlights"
    duration_s: float
    width: int
    height: int
    r2_url: str
    segment_count: int      # 1 for tv_reel; N events kept for auto_highlights
    # Source-video [start_s, end_s] windows that were rendered, IN ORDER.
    # For tv_reel: the play_windows (typically the two halves).
    # For auto_highlights: the merged event windows.
    # The pipeline uses this to map each source-video timestamp into
    # reel-relative time for the on-screen overlay layer.
    segments: list[tuple[float, float]] = field(default_factory=list)


# --- aim stream ----------------------------------------------------------

def _play_centroid_aim_for_time(
    tracks_field_df: pd.DataFrame,
    t_video: float,
    H_inv: np.ndarray,
    eq_w: int,
    eq_h: int,
    fallback: tuple[float, float],
) -> tuple[float, float]:
    """Mean foot position of all tracks in a ±1s window → equirect → (lon, lat)."""
    if "x_m" not in tracks_field_df.columns or tracks_field_df.empty:
        return fallback
    mask = (tracks_field_df["time_s"] >= t_video - 1.0) & (tracks_field_df["time_s"] <= t_video + 1.0)
    win = tracks_field_df[mask]
    if win.empty:
        return fallback
    x_m = float(win["x_m"].mean())
    y_m = float(win["y_m"].mean())
    u, v = _field_to_equirect_pixel(H_inv, x_m, y_m)
    return _equirect_pixel_to_lonlat(u, v, eq_w, eq_h)


def _build_aim_stream(
    tracks_field_df: pd.DataFrame,
    H_inv: np.ndarray,
    eq_w: int,
    eq_h: int,
    start_s: float,
    end_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (aim_times, lons_unwrapped_deg, lats_deg) sampled at TV_AIM_HZ."""
    dt = 1.0 / TV_AIM_HZ
    aim_times = np.arange(start_s, end_s, dt)
    raw = [
        _play_centroid_aim_for_time(tracks_field_df, float(t), H_inv, eq_w, eq_h, (0.0, 0.0))
        for t in aim_times
    ]
    smoothed = _smooth_aim_stream(raw, window=TV_SMOOTH_WINDOW)
    lons = np.degrees(np.unwrap(np.radians(np.array([a[0] for a in smoothed]))))
    lats = np.array([a[1] for a in smoothed])
    return aim_times, lons, lats


# --- segment render ------------------------------------------------------

def _render_segment(
    cap: cv2.VideoCapture,
    writer: cv2.VideoWriter,
    fps: float,
    start_s: float,
    end_s: float,
    aim_times: np.ndarray,
    aim_lons_uw: np.ndarray,
    aim_lats: np.ndarray,
    out_w: int,
    out_h: int,
) -> int:
    start_f = max(0, int(round(start_s * fps)))
    end_f = int(round(end_s * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    written = 0
    for f in range(start_f, end_f):
        ok, frame = cap.read()
        if not ok:
            break
        t = f / fps
        lon_uw = float(np.interp(t, aim_times, aim_lons_uw))
        lat = float(np.interp(t, aim_times, aim_lats))
        lon = ((lon_uw + 180.0) % 360.0) - 180.0
        crop = render_perspective(frame, lon, lat, TV_FOV_DEG, out_w, out_h)
        writer.write(crop)
        written += 1
    return written


# --- merge overlapping windows -------------------------------------------

def _event_windows(
    events: list[CoachEvent],
    period_clock_to_video_time: Callable[[int, int], float],
    video_duration_s: float,
    window_s: float,
) -> list[tuple[float, float]]:
    raw: list[tuple[float, float]] = []
    for ev in events:
        if ev.type not in AUTO_HIGHLIGHT_EVENT_TYPES:
            continue
        t = float(period_clock_to_video_time(ev.period, ev.elapsed))
        if t < 0:
            continue
        a = max(0.0, t - window_s)
        b = min(video_duration_s, t + window_s)
        if b > a:
            raw.append((a, b))
    if not raw:
        return []
    raw.sort()
    merged: list[tuple[float, float]] = [raw[0]]
    for a, b in raw[1:]:
        last_a, last_b = merged[-1]
        if a <= last_b:
            merged[-1] = (last_a, max(last_b, b))
        else:
            merged.append((a, b))
    return merged


# --- public entry points -------------------------------------------------

def render_tv_reel(
    video_path: str,
    tracks_field_df: pd.DataFrame,
    H: np.ndarray,
    game_id: str,
    upload: bool = True,
    play_windows: Optional[list[tuple[float, float]]] = None,
) -> Optional[TvViewMeta]:
    """Render the broadcast view. If `play_windows` is given, only those
    segments (typically 1st + 2nd half) are rendered and concatenated —
    halftime + warmup are skipped."""
    try:
        H_inv = np.linalg.inv(np.asarray(H, dtype=np.float64))
    except np.linalg.LinAlgError as e:
        raise RuntimeError("Homography is singular — cannot invert for TV reel aim.") from e

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eq_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    eq_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_s = total_frames / fps if fps else 0.0

    # Fallback: whole video as one window
    windows = play_windows or [(0.0, duration_s)]
    # Filter degenerate windows
    windows = [(a, b) for (a, b) in windows if b > a + 0.5]
    if not windows:
        cap.release()
        log.info("TV reel: no usable play windows; skipping.")
        return None

    out_w, out_h = TV_RESOLUTION
    out_dir = config.OUTPUTS_DIR / game_id / "tv_view"
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / "tv_reel.mp4"

    with tempfile.TemporaryDirectory(prefix="tv_reel_") as td:
        tmp_dir = Path(td)
        part_paths: list[Path] = []
        for i, (a, b) in enumerate(windows):
            aim_times, aim_lons_uw, aim_lats = _build_aim_stream(
                tracks_field_df, H_inv, eq_w, eq_h, a, b,
            )
            part_path = tmp_dir / f"half_{i + 1}.mp4"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(part_path), fourcc, fps, (out_w, out_h))
            if not writer.isOpened():
                log.warning("TV reel: cannot open writer for half %d, skipping", i + 1)
                continue
            log.info("TV reel half %d: [%.1fs - %.1fs] (%.0fs)", i + 1, a, b, b - a)
            _render_segment(
                cap, writer, fps, a, b,
                aim_times, aim_lons_uw, aim_lats, out_w, out_h,
            )
            writer.release()
            part_paths.append(part_path)
        cap.release()

        if not part_paths:
            log.info("TV reel: no segments rendered.")
            return None

        # Single-half case: just rename. Multi-half: ffmpeg concat.
        if len(part_paths) == 1:
            shutil.copy(part_paths[0], final_path)
        else:
            concat_list = tmp_dir / "concat.txt"
            with open(concat_list, "w") as f:
                for p in part_paths:
                    f.write(f"file '{p.as_posix()}'\n")
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg is None:
                raise RuntimeError("ffmpeg not on PATH — required to concat TV reel halves.")
            cmd = [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list), "-c", "copy", str(final_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg concat (TV reel) failed: {result.stderr}")

    probe_cap = cv2.VideoCapture(str(final_path))
    pfps = probe_cap.get(cv2.CAP_PROP_FPS) or fps
    pframes = int(probe_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    probe_cap.release()
    final_duration = pframes / pfps if pfps else 0.0

    r2_url = ""
    if upload:
        try:
            key = f"tv_view/{game_id}/tv_reel.mp4"
            r2_url = firestore_io.upload_clip(str(final_path), key)
        except Exception as e:
            log.warning("TV reel upload failed: %s", e)

    # IMPORTANT: do NOT write to the per-event clips/ subcollection here —
    # the PWA per-event clip list reads from there and would render a broken
    # row for the full-game reel. The pipeline persists tv_reel_url +
    # auto_highlights_url on the analytics doc instead.
    meta = TvViewMeta(
        kind="tv_reel",
        duration_s=final_duration,
        width=out_w,
        height=out_h,
        r2_url=r2_url,
        segment_count=len(part_paths),
        segments=[(float(a), float(b)) for (a, b) in windows[:len(part_paths)]],
    )
    log.info("TV reel done: %d halves, %.1fs -> %s",
             len(part_paths), final_duration, r2_url or final_path)
    return meta


def extract_auto_highlights(
    video_path: str,
    events: list[CoachEvent],
    tracks_field_df: pd.DataFrame,
    H: np.ndarray,
    period_clock_to_video_time: Callable[[int, int], float],
    game_id: str,
    window_s: float = AUTO_HIGHLIGHT_WINDOW_S,
    upload: bool = True,
) -> Optional[TvViewMeta]:
    """Render only segments around scoring-adjacent events, concatenated."""
    try:
        H_inv = np.linalg.inv(np.asarray(H, dtype=np.float64))
    except np.linalg.LinAlgError as e:
        raise RuntimeError("Homography is singular — cannot invert for auto-highlights.") from e

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eq_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    eq_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_s = total_frames / fps if fps else 0.0

    windows = _event_windows(events, period_clock_to_video_time, duration_s, window_s)
    if not windows:
        cap.release()
        log.info("Auto-highlights: no qualifying events; skipping.")
        return None

    out_w, out_h = TV_RESOLUTION
    out_dir = config.OUTPUTS_DIR / game_id / "tv_view"
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / "auto_highlights.mp4"

    # Render each window into its own temp mp4 with mp4v (cheap), then
    # ffmpeg-concat into the final. Per-window aim streams keep memory low.
    with tempfile.TemporaryDirectory(prefix="tv_view_") as td:
        tmp_dir = Path(td)
        part_paths: list[Path] = []
        for i, (a, b) in enumerate(windows):
            aim_times, aim_lons_uw, aim_lats = _build_aim_stream(
                tracks_field_df, H_inv, eq_w, eq_h, a, b,
            )
            part_path = tmp_dir / f"part_{i:03d}.mp4"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(part_path), fourcc, fps, (out_w, out_h))
            if not writer.isOpened():
                log.warning("Auto-highlights: cannot write part %d, skipping", i)
                continue
            _render_segment(
                cap, writer, fps, a, b,
                aim_times, aim_lons_uw, aim_lats, out_w, out_h,
            )
            writer.release()
            part_paths.append(part_path)
            log.info("  segment %d/%d: [%.1fs - %.1fs]", i + 1, len(windows), a, b)
        cap.release()

        if not part_paths:
            log.info("Auto-highlights: no segments rendered.")
            return None

        # ffmpeg concat-demuxer (no re-encode — all parts share codec/fps/res)
        concat_list = tmp_dir / "concat.txt"
        with open(concat_list, "w") as f:
            for p in part_paths:
                f.write(f"file '{p.as_posix()}'\n")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg not on PATH — required to concat highlight segments.")
        cmd = [
            ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-c", "copy", str(final_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr}")

    # Probe final duration
    probe_cap = cv2.VideoCapture(str(final_path))
    pfps = probe_cap.get(cv2.CAP_PROP_FPS) or fps
    pframes = int(probe_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    probe_cap.release()
    final_duration = pframes / pfps if pfps else 0.0

    r2_url = ""
    if upload:
        try:
            key = f"tv_view/{game_id}/auto_highlights.mp4"
            r2_url = firestore_io.upload_clip(str(final_path), key)
        except Exception as e:
            log.warning("Auto-highlights upload failed: %s", e)

    # See note in render_tv_reel: do NOT write to clips/ here either.
    meta = TvViewMeta(
        kind="auto_highlights",
        duration_s=final_duration,
        width=out_w,
        height=out_h,
        r2_url=r2_url,
        segment_count=len(windows),
        segments=[(float(a), float(b)) for (a, b) in windows],
    )
    log.info("Auto-highlights done: %d segments, %.1fs -> %s",
             len(windows), final_duration, r2_url or final_path)
    return meta
