"""Highlight clip extraction.

For each coach-logged event of interest (GOAL/ASSIST/SAVE/SHOT_ON/KEY_PASS):
  1. Compute the source-video timestamp from (period, elapsed).
  2. Determine where to look — the involved player's foot position in field
     meters at that timestamp, mapped back to equirectangular pixels via the
     inverse homography, then to (lon_deg, lat_deg) for the perspective camera.
     Smoothed across the clip window so the camera glides instead of jitters.
  3. Render a 1920×1080 perspective MP4 with audio muxed from the source.
     FOV widens slightly (CLIP_FOV_DEG) so context is visible.
  4. Upload to R2 under `clips/<gameId>/<eventId>.mp4`.
  5. Write metadata to Firestore under `teams/main/games/<gameId>/clips/<eventId>`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
import pandas as pd

from . import config, firestore_io
from .firestore_io import CoachEvent
from .video import H264PipeWriter, render_perspective

log = logging.getLogger(__name__)


@dataclass
class ClipMeta:
    event_id: str
    event_type: str
    player_id: Optional[str]
    period: int
    elapsed: int
    video_time_s: float
    duration_s: float
    r2_url: str
    width: int
    height: int


# --- aim computation -----------------------------------------------------

def _equirect_pixel_to_lonlat(u_px: float, v_px: float, eq_w: int, eq_h: int) -> tuple[float, float]:
    lon_deg = (u_px / eq_w - 0.5) * 360.0
    lat_deg = (0.5 - v_px / eq_h) * 180.0
    return lon_deg, lat_deg


def _field_to_equirect_pixel(H_inv: np.ndarray, x_m: float, y_m: float) -> tuple[float, float]:
    v = H_inv @ np.array([x_m, y_m, 1.0])
    if abs(v[2]) < 1e-12:
        return 0.0, 0.0
    return float(v[0] / v[2]), float(v[1] / v[2])


def _player_track_id(identity_by_track: dict[int, str], player_id: Optional[str]) -> Optional[int]:
    if not player_id:
        return None
    for tid, pid in identity_by_track.items():
        if pid == player_id:
            return int(tid)
    return None


def _aim_for_time(
    tracks_field_df: pd.DataFrame,
    track_id: Optional[int],
    t_video: float,
    projector,  # FieldProjector (forward decl to avoid circular import)
    fallback: tuple[float, float],
) -> tuple[float, float]:
    """(lon_deg, lat_deg) to aim the virtual camera at time t_video.

    Prefer the involved player's foot pos; else the centroid of all tracks in
    a ±1s window; else `fallback`.
    """
    if track_id is not None and "x_m" in tracks_field_df.columns:
        sub = tracks_field_df[tracks_field_df["track_id"] == track_id]
        if not sub.empty:
            idx = (sub["time_s"] - t_video).abs().idxmin()
            if abs(float(sub.loc[idx, "time_s"]) - t_video) <= 2.0:
                x_m = float(sub.loc[idx, "x_m"])
                y_m = float(sub.loc[idx, "y_m"])
                return projector.field_to_lonlat(x_m, y_m)

    if "x_m" in tracks_field_df.columns:
        mask = (tracks_field_df["time_s"] >= t_video - 1.0) & (tracks_field_df["time_s"] <= t_video + 1.0)
        win = tracks_field_df[mask]
        if not win.empty:
            x_m = float(win["x_m"].mean())
            y_m = float(win["y_m"].mean())
            return projector.field_to_lonlat(x_m, y_m)

    return fallback


def _smooth_aim_stream(aims: list[tuple[float, float]], window: int = 7) -> list[tuple[float, float]]:
    if len(aims) < 3 or window <= 1:
        return aims
    lons = np.array([a[0] for a in aims], dtype=np.float64)
    lats = np.array([a[1] for a in aims], dtype=np.float64)
    lons_uw = np.degrees(np.unwrap(np.radians(lons)))
    k = np.ones(window) / window
    lons_s = np.convolve(lons_uw, k, mode="same")
    lats_s = np.convolve(lats, k, mode="same")
    lons_s = ((lons_s + 180.0) % 360.0) - 180.0
    return [(float(a), float(b)) for a, b in zip(lons_s, lats_s)]


# --- per-clip render -----------------------------------------------------

def _render_clip(
    video_path: str,
    event: CoachEvent,
    t_video_s: float,
    track_id: Optional[int],
    tracks_field_df: pd.DataFrame,
    projector,  # FieldProjector
    out_path: Path,
) -> tuple[float, int, int]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eq_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    eq_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    start_s = max(0.0, t_video_s - config.CLIP_PRE_SECONDS)
    end_s = t_video_s + config.CLIP_POST_SECONDS
    start_f = int(round(start_s * fps))
    end_f = min(total_frames, int(round(end_s * fps)))
    if end_f <= start_f:
        cap.release()
        raise RuntimeError(f"Event {event.id} out of range (video too short).")

    aim_dt = 0.2
    aim_times = np.arange(start_s, end_s, aim_dt)
    raw_aims = [
        _aim_for_time(tracks_field_df, track_id, float(t), projector, (0.0, 0.0))
        for t in aim_times
    ]
    aims = _smooth_aim_stream(raw_aims, window=7)
    aim_lons_uw = np.degrees(np.unwrap(np.radians(np.array([a[0] for a in aims]))))
    aim_lats = np.array([a[1] for a in aims]) + config.CLIP_LAT_TILT_DEG

    out_w, out_h = config.CLIP_RESOLUTION
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = H264PipeWriter(out_path, fps, out_w, out_h,
                            audio_source=video_path, audio_start_s=start_s)

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    for f in range(start_f, end_f):
        ok, frame = cap.read()
        if not ok:
            break
        t = f / fps
        lon_uw = float(np.interp(t, aim_times, aim_lons_uw))
        lat = float(np.interp(t, aim_times, aim_lats))
        lon = ((lon_uw + 180.0) % 360.0) - 180.0
        crop = render_perspective(frame, lon, lat, config.CLIP_FOV_DEG, out_w, out_h)
        writer.write(crop)

    writer.close()
    cap.release()
    duration_s = (end_f - start_f) / fps
    return duration_s, out_w, out_h


# --- public entry --------------------------------------------------------

def extract_clips(
    video_path: str,
    events: list[CoachEvent],
    tracks_field_df: pd.DataFrame,
    identity_by_track: dict[int, str],
    projector,  # FieldProjector
    period_clock_to_video_time: Callable[[int, int], float],
    game_id: str,
    upload: bool = True,
) -> list[ClipMeta]:
    if not events:
        return []

    out: list[ClipMeta] = []
    clips_dir = config.OUTPUTS_DIR / game_id / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    for ev in events:
        if ev.type not in config.CLIP_EVENT_TYPES:
            continue
        t_video_s = float(period_clock_to_video_time(ev.period, ev.elapsed))
        if t_video_s < 0:
            continue
        track_id = _player_track_id(identity_by_track, ev.player_id)
        local_path = clips_dir / f"{ev.id}.mp4"
        try:
            duration_s, w, h = _render_clip(
                video_path=video_path,
                event=ev,
                t_video_s=t_video_s,
                track_id=track_id,
                tracks_field_df=tracks_field_df,
                projector=projector,
                out_path=local_path,
            )
        except Exception as e:
            log.warning("Clip %s (%s) failed: %s", ev.id, ev.type, e)
            continue

        r2_url = ""
        if upload:
            try:
                key = f"clips/{game_id}/{ev.id}.mp4"
                r2_url = firestore_io.upload_clip(str(local_path), key)
            except Exception as e:
                log.warning("Upload failed for %s: %s", ev.id, e)

        meta = ClipMeta(
            event_id=ev.id,
            event_type=ev.type,
            player_id=ev.player_id,
            period=ev.period,
            elapsed=ev.elapsed,
            video_time_s=t_video_s,
            duration_s=float(duration_s),
            r2_url=r2_url,
            width=w,
            height=h,
        )
        try:
            firestore_io.write_clip_metadata(game_id, ev.id, {
                "eventId": meta.event_id,
                "eventType": meta.event_type,
                "playerId": meta.player_id,
                "period": meta.period,
                "elapsed": meta.elapsed,
                "videoTimeS": meta.video_time_s,
                "durationS": meta.duration_s,
                "r2Url": meta.r2_url,
                "width": meta.width,
                "height": meta.height,
            })
        except Exception as e:
            log.warning("Firestore clip-meta write failed for %s: %s", ev.id, e)
        out.append(meta)
        log.info("Clip %s (%s) %.1fs -> %s", ev.id, ev.type, duration_s, r2_url or local_path)

    return out
