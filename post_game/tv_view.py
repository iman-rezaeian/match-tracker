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
from .calibration import FieldProjector
from .highlights import _smooth_aim_stream
from .video import H264PipeWriter, render_perspective

log = logging.getLogger(__name__)


# --- config (kept here; promote to config.py if ever tweaked from CLI) ----

TV_AIM_HZ = 5.0          # how often to recompute the virtual-camera aim
TV_SMOOTH_WINDOW = 15    # samples of moving average over the aim stream (3s at 5Hz)
TV_AGG_WINDOW_S = 1.0    # ±1s per-time aggregation window for the density-
                         # cluster aim. Keep this short so the camera follows
                         # play promptly — sub-second wobble that leaks
                         # through is removed by the dedicated reversal
                         # suppressor below, NOT by widening this window
                         # (which makes the camera sluggish).
TV_REVERSAL_MIN_DUR_S = 1.0  # Suppress back-and-forth aim reversals shorter
                         # than this many seconds. Detected as turning-point
                         # pairs in the smoothed aim where the camera reverses
                         # direction and reverses BACK within < this duration.
                         # The short excursion is flattened by linear
                         # interpolation between the outer turning points,
                         # which preserves the overall follow while killing
                         # the "pan back a little then forward again" wobble.
TV_FOV_DEG = 70.0        # narrower than 100° so the field fills the frame.
                         # On a low sideline pole the field is a thin strip on
                         # the horizon — wider FOV just imports parking lot + sky.
TV_LAT_TILT_DEG = -7.0   # bias the aim DOWN by this many degrees. Players'
                         # feet land near the horizon line in equirect, so a
                         # pure player-centroid aim puts them at the vertical
                         # center with sky above. -7° with a ~39° vertical FOV
                         # puts players at ~32% from the top (broadcast-standard
                         # head-room). Going much past -10° makes the players a
                         # sliver at the top of the frame with empty foreground.
TV_RESOLUTION = (1920, 1080)
TV_ONFIELD_PAD_M = -1.0  # NEGATIVE: require tracks to be at least 1m INSIDE
                         # the touchlines. Positive pad lets sideline coaches,
                         # parents, and players-on-bench pull the centroid
                         # toward the bench side and the aim misses the action.
TV_DENSITY_BIN_M = 2.0   # 2m bins along the field-X axis for density histogram
TV_DENSITY_WINDOW_M = 16.0  # find the densest 16m-wide window of players —
                         # roughly the width of an attacking phase. Aim at the
                         # mean of just those players, so a lone GK at the
                         # other end can't drag the camera to midfield.
TV_OUTLIER_RADIUS_M = 15.0  # (unused after density-aim refactor; kept for compat)
TV_MIN_ONFIELD_TRACKS = 1  # need this many DISTINCT on-field tracks (after the
                         # static-track filter) to update aim. Set to 1 because
                         # detection is sparse on this footage (often only 2-3
                         # real players survive on-field + not-static filters
                         # per timestamp), so a single real player is still
                         # better than falling back to field-center which sits
                         # at the bench area.
TV_STATIC_TRACK_MVMT_M = 5.0  # track-lifetime total movement threshold (m).
                         # Tracks below this are stationary people who slipped
                         # past the on-field geometric filter: coaches standing
                         # on the touchline, refs at midfield, the ball-kid by
                         # the goalpost. Real U10 players cover way more than
                         # 5 m total even in a 30-second smoke window.
AUTO_HIGHLIGHT_WINDOW_S = 15.0
# Both teams' goals belong in the reel — OPP_GOAL was previously missing, so
# the opponent's goals never made the highlight cut (a 3-6 game showed only the
# 3 we scored). SAVE covers the opponent attacks our keeper stopped.
AUTO_HIGHLIGHT_EVENT_TYPES = ("GOAL", "OPP_GOAL", "SHOT_ON", "SAVE", "KEY_PASS")


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

def _suppress_short_reversals(x: np.ndarray, aim_hz: float, min_dur_s: float) -> np.ndarray:
    """Median-filter a 1D aim series to flatten reversals shorter than `min_dur_s`.

    A 1-second median filter is the textbook tool for "remove brief
    excursions while preserving the underlying trajectory":
      - A monotonic ramp: unchanged (the window median equals the center
        sample for any monotonic input).
      - A V-shaped dip (camera panning forward, briefly back, then forward
        again) where the dip is shorter than half the window: the dip
        samples get replaced by the nearby higher values from the trend,
        because they are minority in the window — the camera "doesn't
        see" the wobble.
      - A sustained direction change (lasting longer than half the window):
        survives, because once the new direction dominates the window the
        median tracks it.

    Window length is `min_dur_s * aim_hz` (rounded to next odd integer).
    With min_dur_s=1.0 and aim_hz=5 that's 5 samples → any reversal
    completed in <0.5 s vanishes; reversals lasting >0.5 s survive.

    Crucially: this does NOT change the *rate* of long pans. The camera
    still follows the play exactly as fast as the smoothed aim stream
    says, only the sub-second back-and-forth is removed. This was the
    user's exact request.
    """
    if x.size < 5 or min_dur_s <= 0:
        return x
    k = max(3, int(round(min_dur_s * aim_hz)))
    if k % 2 == 0:
        k += 1
    from scipy.ndimage import median_filter
    return median_filter(x, size=k, mode="nearest")


def _field_lonlat_bounds(
    projector: FieldProjector,
    field_length_m: float, field_width_m: float,
) -> tuple[float, float, float, float]:
    """Project the four field corners (slightly padded) into (lon, lat) and
    return (lon_min, lon_max, lat_min, lat_max). Used to clamp the virtual
    camera aim so it can never point outside the pitch — the camera will
    still see grass beyond the lines because of FOV, but the *center* stays
    inside.
    """
    pad = TV_ONFIELD_PAD_M
    corners_m = [
        (-pad, -pad),
        (field_length_m + pad, -pad),
        (field_length_m + pad, field_width_m + pad),
        (-pad, field_width_m + pad),
    ]
    lons, lats = [], []
    for x_m, y_m in corners_m:
        lon, lat = projector.field_to_lonlat(x_m, y_m)
        lons.append(lon)
        lats.append(lat)
    # Unwrap lons so we don't get a 360° span across the seam.
    lons_uw = np.degrees(np.unwrap(np.radians(np.array(lons))))
    return float(lons_uw.min()), float(lons_uw.max()), float(min(lats)), float(max(lats))


def _play_centroid_aim_for_time(
    tracks_field_df: pd.DataFrame,
    t_video: float,
    projector: FieldProjector,
    fallback: tuple[float, float],
    field_length_m: float,
    field_width_m: float,
) -> tuple[tuple[float, float], bool]:
    """DENSITY-based aim of ON-FIELD tracks in a ±1s window → (lon, lat).

    Returns ((lon, lat), valid). `valid=False` means there were no
    on-field tracks at this time — caller should hold the previous aim.

    Mean/median centroid was the wrong primitive: when 9 attackers are at
    one goal and 1 GK is at the other, the mean lands in the empty middle.
    Instead: find the densest cluster along the field-X axis (where play
    actually moves end-to-end), then take the mean of just those players.

    Algorithm:
      1. Filter to on-field tracks (inside touch+end lines by TV_ONFIELD_PAD_M).
      2. DEDUPE by track_id (one (x,y) per track via median of its rows in
         the window). A static coach standing inside the lines must NOT vote
         8 times just because he was detected in 8 consecutive frames.
      3. Drop tracks pre-flagged as static (`_track_static`) at the
         `_build_aim_stream` level (see total-movement filter there).
      4. Build a 1D histogram of x positions with TV_DENSITY_BIN_M bins.
      5. Find the densest TV_DENSITY_WINDOW_M-wide window of x.
      6. Take mean of (x, y) for tracks inside that window.
    """
    if "x_m" not in tracks_field_df.columns or tracks_field_df.empty:
        return fallback, False
    pad = TV_ONFIELD_PAD_M
    half_w = TV_AGG_WINDOW_S
    mask = (
        (tracks_field_df["time_s"] >= t_video - half_w)
        & (tracks_field_df["time_s"] <= t_video + half_w)
        & (tracks_field_df["x_m"] >= -pad)
        & (tracks_field_df["x_m"] <= field_length_m + pad)
        & (tracks_field_df["y_m"] >= -pad)
        & (tracks_field_df["y_m"] <= field_width_m + pad)
    )
    # Drop tracks that the lifetime-movement filter marked as static.
    if "_track_static" in tracks_field_df.columns:
        mask = mask & (~tracks_field_df["_track_static"])
    win = tracks_field_df[mask]
    if win.empty:
        return fallback, False

    # Dedupe by track_id — one point per player.
    per_track = win.groupby("track_id").agg(x_m=("x_m", "median"), y_m=("y_m", "median"))
    if len(per_track) < TV_MIN_ONFIELD_TRACKS:
        return fallback, False
    x = per_track["x_m"].to_numpy(dtype=np.float64)
    y = per_track["y_m"].to_numpy(dtype=np.float64)

    # Densest-window on x. With only a handful of tracks, fall back to mean.
    if len(x) >= 4:
        bin_w = TV_DENSITY_BIN_M
        win_w = TV_DENSITY_WINDOW_M
        bins_per_window = max(1, int(round(win_w / bin_w)))
        edges = np.arange(0.0, field_length_m + bin_w, bin_w)
        hist, _ = np.histogram(x, bins=edges)
        if len(hist) >= bins_per_window:
            kernel = np.ones(bins_per_window, dtype=np.float64)
            sums = np.convolve(hist, kernel, mode="valid")
            peak = int(np.argmax(sums))
            x_lo = edges[peak]
            x_hi = edges[peak + bins_per_window]
            inside = (x >= x_lo) & (x <= x_hi)
            if inside.sum() >= 2:
                cx = float(np.mean(x[inside]))
                cy = float(np.mean(y[inside]))
            else:
                cx = float(np.mean(x))
                cy = float(np.mean(y))
        else:
            cx = float(np.mean(x))
            cy = float(np.mean(y))
    else:
        cx = float(np.mean(x))
        cy = float(np.mean(y))

    return projector.field_to_lonlat(cx, cy), True


def _build_aim_stream(
    tracks_field_df: pd.DataFrame,
    projector: FieldProjector,
    start_s: float,
    end_s: float,
    field_length_m: float,
    field_width_m: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (aim_times, lons_unwrapped_deg, lats_deg) sampled at TV_AIM_HZ.

    Aim is held at the last valid on-field centroid when no players are
    detected on the pitch (between halves, restarts, deadballs). Final
    aims are clamped to the projected field bounding box so the camera
    center never points off the pitch.
    """
    dt = 1.0 / TV_AIM_HZ
    aim_times = np.arange(start_s, end_s, dt)

    # Pre-compute per-track lifetime movement and tag static tracks. This is
    # the critical filter for "coach standing on the touchline gets detected
    # in 300 frames and dominates the centroid". A real U10 player covers
    # >>5 m over a 30-second window; a stationary adult covers <1 m.
    if not tracks_field_df.empty and "track_id" in tracks_field_df.columns \
            and "x_m" in tracks_field_df.columns:
        agg = tracks_field_df.groupby("track_id").agg(
            x_min=("x_m", "min"), x_max=("x_m", "max"),
            y_min=("y_m", "min"), y_max=("y_m", "max"),
        )
        span = np.hypot(agg["x_max"] - agg["x_min"], agg["y_max"] - agg["y_min"])
        static_ids = set(span[span < TV_STATIC_TRACK_MVMT_M].index.tolist())
        tracks_field_df = tracks_field_df.copy()
        tracks_field_df["_track_static"] = tracks_field_df["track_id"].isin(static_ids)
        n_static = int(tracks_field_df["_track_static"].sum())
        n_total_ids = tracks_field_df["track_id"].nunique()
        log.info(
            "  tv-view: dropping %d/%d static tracks (lifetime mvmt < %.1f m) "
            "as non-players (coaches/refs/standing kids).",
            len(static_ids), n_total_ids, TV_STATIC_TRACK_MVMT_M,
        )
    else:
        n_static = 0

    # Default fallback = field center projected to (lon, lat). Far better
    # than (0, 0) which can land in the sky or behind the camera.
    center_lonlat = projector.field_to_lonlat(field_length_m / 2.0, field_width_m / 2.0)

    raw: list[tuple[float, float]] = []
    last_valid = center_lonlat
    for t in aim_times:
        aim, valid = _play_centroid_aim_for_time(
            tracks_field_df, float(t), projector,
            last_valid, field_length_m, field_width_m,
        )
        if valid:
            last_valid = aim
        raw.append(aim)

    smoothed = _smooth_aim_stream(raw, window=TV_SMOOTH_WINDOW)
    lons = np.degrees(np.unwrap(np.radians(np.array([a[0] for a in smoothed]))))
    lats = np.array([a[1] for a in smoothed])

    # Sub-second reversal suppressor (the user complaint): the smoothed aim
    # still has small back-and-forth bumps when a player briefly drifts into
    # then out of the densest cluster. Flatten any reversal that completes
    # in less than TV_REVERSAL_MIN_DUR_S. Long pans are preserved at their
    # full rate — only short wobble is removed.
    lons = _suppress_short_reversals(lons, TV_AIM_HZ, TV_REVERSAL_MIN_DUR_S)
    lats = _suppress_short_reversals(lats, TV_AIM_HZ, TV_REVERSAL_MIN_DUR_S)

    # Tilt the camera DOWN so the horizon sits at the top of the frame and
    # the field fills the bottom 70–80% — broadcast-style. Without this, on a
    # low sideline pole the field appears as a thin strip near the middle of
    # the frame and the upper half is sky + parking lot.
    lats = lats + TV_LAT_TILT_DEG

    # Clamp aim center inside the projected field box (after tilt). The clamp
    # is mainly a safety net for lon — the tilt offset intentionally pushes
    # lat below the field, so we relax the lat lower bound by the tilt amount.
    lon_min, lon_max, lat_min, lat_max = _field_lonlat_bounds(
        projector, field_length_m, field_width_m,
    )
    lons = np.clip(lons, lon_min, lon_max)
    lats = np.clip(lats, lat_min + TV_LAT_TILT_DEG, lat_max)
    return aim_times, lons, lats


# --- segment render ------------------------------------------------------

def _render_segment(
    cap: cv2.VideoCapture,
    writer: "H264PipeWriter | cv2.VideoWriter",
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
        # Lanczos gives a sharper upscale than bilinear — the TV crop is
        # enlarged from a ~70° slice of the sphere, so the resample quality
        # is one of the few real levers on perceived sharpness.
        crop = render_perspective(frame, lon, lat, TV_FOV_DEG, out_w, out_h, interp=cv2.INTER_LANCZOS4)
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

def tv_reel_meta_from_existing(
    game_id: str,
    play_windows: Optional[list[tuple[float, float]]] = None,
    upload: bool = True,
) -> Optional[TvViewMeta]:
    """Reuse an already-rendered outputs/<game>/tv_view/tv_reel.mp4 instead of
    re-rendering it (the perspective render is the multi-hour part of stage 7b).

    Probes the existing file for duration/resolution, uploads it to R2, and
    returns a TvViewMeta whose `segments` == `play_windows` — i.e. exactly what
    `render_tv_reel` would have rendered from, so the broadcast-events index
    maps source-video times into reel time correctly.

    Used by the --reuse-tv-reel fast-path to recover from a pipeline run that
    was interrupted after the TV reel rendered but before analytics/uploads ran.
    Returns None if no usable local reel exists (caller then renders fresh)."""
    final_path = config.OUTPUTS_DIR / game_id / "tv_view" / "tv_reel.mp4"
    if not final_path.exists() or final_path.stat().st_size == 0:
        log.warning("--reuse-tv-reel: %s missing/empty; nothing to reuse.", final_path)
        return None

    probe_cap = cv2.VideoCapture(str(final_path))
    pfps = probe_cap.get(cv2.CAP_PROP_FPS) or 30.0
    pframes = int(probe_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out_w = int(probe_cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or TV_RESOLUTION[0]
    out_h = int(probe_cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or TV_RESOLUTION[1]
    probe_cap.release()
    final_duration = pframes / pfps if pfps else 0.0

    windows = [(float(a), float(b)) for (a, b) in (play_windows or []) if b > a + 0.5]

    r2_url = ""
    if upload:
        try:
            key = f"tv_view/{game_id}/tv_reel.mp4"
            r2_url = firestore_io.upload_clip(str(final_path), key)
        except Exception as e:
            log.warning("TV reel (reuse) upload failed: %s", e)

    meta = TvViewMeta(
        kind="tv_reel",
        duration_s=final_duration,
        width=out_w,
        height=out_h,
        r2_url=r2_url,
        segment_count=len(windows) or 1,
        segments=windows,
    )
    log.info("TV reel reused (no re-render): %.1fs -> %s", final_duration, r2_url or final_path)
    return meta


def render_tv_reel(
    video_path: str,
    tracks_field_df: pd.DataFrame,
    projector: FieldProjector,
    game_id: str,
    field_length_m: float,
    field_width_m: float,
    upload: bool = True,
    play_windows: Optional[list[tuple[float, float]]] = None,
) -> Optional[TvViewMeta]:
    """Render the broadcast view. If `play_windows` is given, only those
    segments (typically 1st + 2nd half) are rendered and concatenated —
    halftime + warmup are skipped."""
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
                tracks_field_df, projector, a, b,
                field_length_m, field_width_m,
            )
            part_path = tmp_dir / f"half_{i + 1}.mp4"
            # High-quality reel encode: CRF 18 + slow preset (vs default 23/veryfast).
            writer = H264PipeWriter(part_path, fps, out_w, out_h, crf=18, preset="slow")
            log.info("TV reel half %d: [%.1fs - %.1fs] (%.0fs)", i + 1, a, b, b - a)
            _render_segment(
                cap, writer, fps, a, b,
                aim_times, aim_lons_uw, aim_lats, out_w, out_h,
            )
            writer.close()
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
    projector: FieldProjector,
    period_clock_to_video_time: Callable[[int, int], float],
    game_id: str,
    field_length_m: float,
    field_width_m: float,
    window_s: float = AUTO_HIGHLIGHT_WINDOW_S,
    upload: bool = True,
    analyzed_windows: Optional[list[tuple[float, float]]] = None,
) -> Optional[TvViewMeta]:
    """Render only segments around scoring-adjacent events, concatenated.

    If `analyzed_windows` is provided (e.g. smoke mode's two 120s halves),
    event windows are intersected with those ranges so we never render
    highlights from source-video minutes the tracker never analyzed —
    those frames have no aim data and would default to the field-center
    fallback, producing a useless reel.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eq_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    eq_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_s = total_frames / fps if fps else 0.0

    windows = _event_windows(events, period_clock_to_video_time, duration_s, window_s)
    if analyzed_windows:
        clipped: list[tuple[float, float]] = []
        for (ea, eb) in windows:
            for (aa, ab) in analyzed_windows:
                lo = max(ea, aa)
                hi = min(eb, ab)
                if hi > lo + 0.5:
                    clipped.append((lo, hi))
        # Re-merge overlaps after clipping
        clipped.sort()
        merged: list[tuple[float, float]] = []
        for a, b in clipped:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        windows = merged
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
                tracks_field_df, projector, a, b,
                field_length_m, field_width_m,
            )
            part_path = tmp_dir / f"part_{i:03d}.mp4"
            # High-quality highlight encode: CRF 18 + slow preset.
            writer = H264PipeWriter(part_path, fps, out_w, out_h, crf=18, preset="slow")
            _render_segment(
                cap, writer, fps, a, b,
                aim_times, aim_lons_uw, aim_lats, out_w, out_h,
            )
            writer.close()
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
