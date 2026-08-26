"""Equirectangular -> perspective-crop frame iterator.

Port of the proven math from `tracking/phase0_validation.ipynb`. Two functions
are critical:

  - `render_perspective`: virtual camera looking at (lon, lat) with `fov_deg`,
    rendering an `out_w x out_h` flat image from the equirectangular frame.
  - `crop_to_equirect_pixel`: inverse — given a pixel in the crop, where did it
    come from in the equirectangular frame? Needed because the calibration
    homography was tapped on the equirectangular image, but YOLO detects in
    the crop.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np

from . import config


# --- H.264 encoder pipe --------------------------------------------------
#
# OpenCV's bundled VideoWriter ships with `mp4v` (MPEG-4 Part 2) as the only
# reliable cross-platform codec. That codec is ~25 years old, ~2-3x larger
# than H.264 at equal quality, and not playable in some mobile browsers
# (older iOS Safari in particular). All reels/clips render through this
# helper instead: pipe raw BGR frames into ffmpeg → libx264 (yuv420p,
# +faststart) so the output streams instantly in any HTML5 <video> tag.


_VT_AVAILABLE: Optional[bool] = None


def _videotoolbox_available() -> bool:
    """Probe once whether this ffmpeg build has the VideoToolbox H.264 encoder."""
    global _VT_AVAILABLE
    if _VT_AVAILABLE is None:
        ffmpeg = shutil.which("ffmpeg")
        try:
            r = subprocess.run([ffmpeg, "-hide_banner", "-h",
                                "encoder=h264_videotoolbox"],
                               capture_output=True, text=True, timeout=10)
            _VT_AVAILABLE = r.returncode == 0 and "videotoolbox" in r.stdout.lower()
        except Exception:
            _VT_AVAILABLE = False
    return _VT_AVAILABLE


class H264PipeWriter:
    """ffmpeg-backed replacement for cv2.VideoWriter.

    Usage mirrors cv2.VideoWriter — `.write(bgr_frame)` per frame, `.close()`
    at the end. Frames must be uint8 BGR of shape (height, width, 3) and must
    match the (width, height) passed at construction.

    `encoder=None` resolves from config.TV_ENCODER: "videotoolbox" hands the
    encode to Apple's media engine (near-zero CPU, rate-controlled by
    config.TV_VT_BITRATE — VideoToolbox has no CRF), "x264" is the original
    software path where `crf`/`preset` apply. Falls back to x264 silently
    when the ffmpeg build lacks the VT encoder, so callers never branch.
    """

    def __init__(self, path: str | Path, fps: float, width: int, height: int,
                 crf: int = 23, preset: str = "veryfast",
                 audio_source: "str | Path | None" = None,
                 audio_start_s: float = 0.0,
                 encoder: Optional[str] = None) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg not on PATH — required for H.264 encoding.")
        self._path = str(path)
        self._w = int(width)
        self._h = int(height)
        enc = (encoder or config.TV_ENCODER).lower()
        if enc == "videotoolbox" and not _videotoolbox_available():
            enc = "x264"
        if enc == "videotoolbox":
            vcodec = ["-c:v", "h264_videotoolbox", "-b:v", config.TV_VT_BITRATE]
        else:
            vcodec = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
        cmd = [
            ffmpeg, "-y",
            "-loglevel", "error",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self._w}x{self._h}",
            "-r", f"{fps}",
            "-i", "-",
        ]
        if audio_source is not None:
            cmd += ["-ss", str(audio_start_s), "-i", str(audio_source)]
            cmd += [
                "-map", "0:v",
                "-map", "1:a?",
                *vcodec,
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-c:a", "aac",
                "-shortest",
                self._path,
            ]
        else:
            cmd += [
                "-an",
                *vcodec,
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                self._path,
            ]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def isOpened(self) -> bool:  # noqa: N802 — mirror cv2 API
        return self._proc is not None and self._proc.poll() is None

    def write(self, frame: np.ndarray) -> None:
        if frame.shape[0] != self._h or frame.shape[1] != self._w:
            frame = cv2.resize(frame, (self._w, self._h))
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)
        try:
            self._proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            err = self._proc.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg encoder died mid-stream: {err}")

    def release(self) -> None:  # cv2-style alias
        self.close()

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except BrokenPipeError:
            pass
        rc = self._proc.wait()
        if rc != 0:
            err = self._proc.stderr.read().decode("utf-8", errors="replace")
            self._proc = None
            raise RuntimeError(f"ffmpeg encode failed (rc={rc}): {err}")
        self._proc = None


@dataclass
class FrameSample:
    frame_index: int
    time_s: float
    eq_frame: np.ndarray
    crop: np.ndarray
    crop_lon_deg: float
    crop_lat_deg: float
    crop_fov_deg: float


def open_video(video_path: str) -> dict:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    return {
        "fps": float(fps),
        "width": w,
        "height": h,
        "total_frames": total,
        "duration_s": total / fps if fps else 0.0,
    }


def render_perspective(
    eq_frame: np.ndarray,
    lon_deg: float,
    lat_deg: float,
    fov_deg: float,
    out_w: int,
    out_h: int,
    interp: int = cv2.INTER_LINEAR,
    map_scale: int = 1,
) -> np.ndarray:
    """Render a perspective crop. Note: latitude is negated — positive lat
    means "look up from equator" in our convention (matches phase0 notebook).

    `interp` is the cv2 interpolation for the resample. INTER_LINEAR is fine
    (and fast) for detection tiles; the broadcast reel used to pass
    INTER_LANCZOS4 for sharpness (config.TV_REMAP_INTERP restores it).

    `map_scale > 1` computes the (u, v) sampling maps at 1/map_scale
    resolution and bilinearly upscales them before the remap. The maps are
    smooth trigonometric fields, so the upscale error is subpixel — but the
    per-frame trig cost drops ~map_scale². The remap itself still samples the
    full-resolution equirect frame, so output sharpness is unchanged.
    """
    h_eq, w_eq = eq_frame.shape[:2]
    s = max(1, int(map_scale))
    mw, mh = max(2, out_w // s), max(2, out_h // s)
    f = out_w / (2 * math.tan(math.radians(fov_deg) / 2))

    # Sample the map grid at the CENTers of s-sized blocks scaled back to
    # full-res pixel coordinates, so the upscaled map stays aligned.
    x = (np.arange(mw, dtype=np.float32) + 0.5) * (out_w / mw) - 0.5 - out_w / 2
    # Output image-y grows DOWNWARD, but world-Y grows UPWARD. Negate so the
    # top of the output crop maps to the upper hemisphere of the sphere
    # (otherwise every crop comes out vertically mirrored — sky at bottom,
    # players upside-down, YOLO catches almost nothing).
    y = -((np.arange(mh, dtype=np.float32) + 0.5) * (out_h / mh) - 0.5 - out_h / 2)
    xv, yv = np.meshgrid(x, y)
    z = np.full_like(xv, f, dtype=np.float32)

    lon_r = math.radians(lon_deg)
    lat_r = math.radians(-lat_deg)
    cos_lat, sin_lat = math.cos(lat_r), math.sin(lat_r)
    cos_lon, sin_lon = math.cos(lon_r), math.sin(lon_r)

    y1 = yv * cos_lat - z * sin_lat
    z1 = yv * sin_lat + z * cos_lat
    x2 = xv * cos_lon + z1 * sin_lon
    z2 = -xv * sin_lon + z1 * cos_lon

    r = np.sqrt(x2 * x2 + y1 * y1 + z2 * z2)
    lon_out = np.arctan2(x2, z2)
    lat_out = np.arcsin(np.clip(y1 / np.maximum(r, 1e-9), -1.0, 1.0))

    u = (lon_out / (2 * math.pi) + 0.5) * w_eq
    v = (0.5 - lat_out / math.pi) * h_eq

    if s > 1:
        # ⚠ A crop that straddles the equirect seam wraps u from w_eq back to
        # 0 — bilinear upscaling across that jump would sweep the map through
        # the whole frame. Unwrap before resizing, re-wrap after.
        useam = (u.max() - u.min()) > w_eq / 2
        if useam:
            u = np.where(u < w_eq / 2, u + w_eq, u)
        u = cv2.resize(u.astype(np.float32), (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        v = cv2.resize(v.astype(np.float32), (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        if useam:
            u = np.mod(u, w_eq)
    return cv2.remap(
        eq_frame,
        u.astype(np.float32),
        v.astype(np.float32),
        interp,
        borderMode=cv2.BORDER_WRAP,
    )


def crop_to_equirect_pixel(
    x_crop: float,
    y_crop: float,
    crop_lon_deg: float,
    crop_lat_deg: float,
    crop_fov_deg: float,
    eq_w: int,
    eq_h: int,
    out_w: int,
    out_h: int,
) -> tuple[float, float]:
    """Inverse of `render_perspective` for a single point."""
    f = out_w / (2 * math.tan(math.radians(crop_fov_deg) / 2))
    x = x_crop - out_w / 2
    # Mirror of render_perspective: image-y down → world-y up.
    y = -(y_crop - out_h / 2)
    z = f

    lon_r = math.radians(crop_lon_deg)
    lat_r = math.radians(-crop_lat_deg)
    cos_lat, sin_lat = math.cos(lat_r), math.sin(lat_r)
    cos_lon, sin_lon = math.cos(lon_r), math.sin(lon_r)

    y1 = y * cos_lat - z * sin_lat
    z1 = y * sin_lat + z * cos_lat
    x2 = x * cos_lon + z1 * sin_lon
    z2 = -x * sin_lon + z1 * cos_lon

    r = math.sqrt(x2 * x2 + y1 * y1 + z2 * z2)
    lon_out = math.atan2(x2, z2)
    lat_out = math.asin(max(-1.0, min(1.0, y1 / max(r, 1e-9))))

    u = (lon_out / (2 * math.pi) + 0.5) * eq_w
    v = (0.5 - lat_out / math.pi) * eq_h
    u = u % eq_w
    return float(u), float(v)


def crop_bbox_to_equirect(
    bbox_crop: tuple[float, float, float, float],
    crop_lon_deg: float,
    crop_lat_deg: float,
    crop_fov_deg: float,
    eq_w: int,
    eq_h: int,
    out_w: int,
    out_h: int,
) -> tuple[float, float, float, float]:
    """Map a (x1, y1, x2, y2) crop bbox to an equirectangular bbox.

    Maps the 4 corners individually; result is an axis-aligned bbox in
    equirectangular pixel space (with longitude unwrapping if it straddles
    the seam).
    """
    x1, y1, x2, y2 = bbox_crop
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    pts = [
        crop_to_equirect_pixel(cx, cy, crop_lon_deg, crop_lat_deg, crop_fov_deg, eq_w, eq_h, out_w, out_h)
        for cx, cy in corners
    ]
    us = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    if max(us) - min(us) > eq_w / 2:
        us = [u + eq_w if u < eq_w / 2 else u for u in us]
    return (min(us), min(vs), max(us), max(vs))


def iter_frames(
    video_path: str,
    sample_rate: int,
    aim_stream: Optional[list[tuple[float, float, float]]] = None,
    aim: Optional[tuple[float, float, float]] = None,
    crop_w: int = config.CROP_W,
    crop_h: int = config.CROP_H,
    windows: Optional[list[tuple[float, float]]] = None,
    render_crop: bool = True,
) -> Iterator[FrameSample]:
    """Yield perspective crops.

    Aim resolution order, highest priority first:
      1. `aim_stream[i]` if provided (per-sample pan).
      2. `aim` if provided (fixed lon/lat/fov for whole video) — typical
         use: aim at the field centroid computed from calibration corners.
      3. Default forward (0°, 0°, CROP_FOV_DEG).

    If `windows` is given (list of (start_s, end_s) in video seconds), frames
    outside any window are skipped — used to trim warmup, halftime, and
    post-game from a continuous game recording. The pipeline seeks the capture
    to the start of each window so we don't decode every dead frame.

    If `render_crop=False` the perspective crop is skipped entirely (saves
    ~30ms per frame) and `sample.crop` is set to `sample.eq_frame`. Use
    this when the caller will render its own tiles from `sample.eq_frame`
    (e.g. multi-tile detection).
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    spans: list[tuple[int, int]]
    if windows:
        spans = []
        for a, b in windows:
            sf = max(0, int(round(a * fps)))
            ef = int(round(b * fps))
            if ef > sf:
                spans.append((sf, ef))
    else:
        spans = [(0, 10 ** 12)]  # effectively unbounded; loop ends at EOF

    sample_i = 0
    for sf, ef in spans:
        cap.set(cv2.CAP_PROP_POS_FRAMES, sf)
        idx = sf
        while idx < ef:
            ok, frame = cap.read()
            if not ok:
                break
            if (idx - sf) % sample_rate == 0:
                if aim_stream is not None and sample_i < len(aim_stream):
                    lon, lat, fov = aim_stream[sample_i]
                elif aim is not None:
                    lon, lat, fov = aim
                else:
                    lon, lat, fov = 0.0, 0.0, config.CROP_FOV_DEG
                if render_crop:
                    crop = render_perspective(frame, lon, lat, fov, crop_w, crop_h)
                else:
                    crop = frame  # alias; caller will tile from eq_frame
                yield FrameSample(
                    frame_index=idx,
                    time_s=idx / fps,
                    eq_frame=frame,
                    crop=crop,
                    crop_lon_deg=lon,
                    crop_lat_deg=lat,
                    crop_fov_deg=fov,
                )
                sample_i += 1
            idx += 1
    cap.release()
