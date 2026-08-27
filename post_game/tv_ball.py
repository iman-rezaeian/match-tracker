"""Ball-aware aim for the TV reel: detect → track → bias.

The fine-tuned ball detector (models/ball_finetuned.pt, trained on OUR rendered
crops — see tracking/BALL_FINETUNE.md; off-the-shelf detectors measurably fail
on this rig from domain gap) upgrades the player-density aim into a ball-aware
one. This is the "swap in ball position when it clears the gate" seam tv_view
has carried since phase 0, done as a BIAS rather than a swap: the player aim
stays the backbone (and the fallback whenever the ball is unseen), and the
confirmed ball pulls the camera toward the play the players haven't reached
yet — the long balls and counters the coach loses when narrating.

Flow, per play window (inside tv_view.render_tv_reel, BEFORE the render):

  1. `extract_ball_records` decodes the window once at BALL_HZ (sequential
     grab/retrieve — no random seeks), renders a detection crop in the model's
     training geometry (config.CROP_W/H/FOV_DEG) at the current best aim — the
     live ball prediction while tracked, else the player aim — runs the
     detector, and maps the best box to sphere (lon, lat) via the exact
     inverse of the render (video.crop_to_equirect_pixel).
  2. `_BallKalman` (constant-velocity on lon/lat) turns raw fixes into a
     tentative→confirmed track with innovation gating, so isolated false
     positives (model precision ~0.76) can never steer the camera; only a
     temporally consistent ball does.
  3. `blend_ball_aim` biases the aim toward the confirmed ball (clamped,
     confidence-ramped, re-eased through tv_aim.smooth_damp) and widens the
     FOV just enough to keep BOTH the ball and the player cluster framed
     (slew-limited like the dynamic FOV, so the lens never pumps).

Records are cached per window (tv_view/ball_records_<i>.json) and the caller
caches the FINAL aim stream (tv_view/aim_stream_<i>.npz): the review-label
chips must be projected through the exact aim the reel rendered with, and the
ball pass makes the aim no longer re-derivable from tracks alone.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from . import config
from . import tv_aim
from .video import crop_to_equirect_pixel, render_perspective

log = logging.getLogger("post_game.tv_ball")

BALL_MODEL_PATH = config.MODELS_DIR / "ball_finetuned.pt"


@dataclass
class BallAimConfig:
    """Knobs for the ball pass. Defaults tuned for a 0.76-precision detector."""

    hz: float = 2.0                    # detection sample rate (video decodes
                                       # the window once regardless; this only
                                       # sets model-call count)
    conf: float = 0.35                 # min detection confidence
    confirm_hits: int = 3              # accepted fixes before the track is
                                       # CONFIRMED (may steer the camera)
    drop_misses: int = 4               # consecutive miss samples (~2 s at 2 Hz)
                                       # before the track dies
    gate_deg: float = 14.0             # innovation gate: a fix further than
                                       # this from the prediction is a miss
                                       # (false positive / other white blob)
    max_bias_deg: float = 20.0         # bias clamp: ball may pull the aim at
                                       # most this far from the player aim
    blend_w: float = 0.65              # bias strength at full confidence
    lat_blend_w: float = 0.3           # vertical pull is gentler (tilt is
                                       # mostly geometry, not action)
    ramp_s: float = 1.0                # ease the bias in/out over this long
    smooth_time_s: float = 1.2         # re-ease the biased aim (smooth_damp)
    max_follow_offset_deg: float = 45.0  # crop-follow clamp around player aim
                                         # so a runaway track can't take the
                                         # detector off the pitch entirely
    fov_margin_deg: float = 4.0        # framing headroom past the ball
    fov_max_deg: float = 115.0         # same rectilinear-render ceiling as
                                       # tv_aim.dynamic_fov_max_deg
    fov_rate_deg_s: float = 6.0        # zoom slew for the ball widen
    fov_deadzone_deg: float = 5.0


def ball_aim_available() -> bool:
    return BALL_MODEL_PATH.exists()


class _BallKalman:
    """Constant-velocity Kalman on (lon, lat) degrees, variable dt.

    State [lon, lat, vlon, vlat]. Longitude is UNWRAPPED by the caller (mapped
    to the nearest wrap of the local aim), so no seam handling here.
    """

    def __init__(self, q: float = 6.0, r: float = 2.5):
        self.q = q          # process noise (deg²/s³-ish): a kicked ball turns
        self.r = r          # measurement noise (deg²): crop-pixel + box jitter
        self.x: Optional[np.ndarray] = None
        self.P: Optional[np.ndarray] = None

    def init(self, lon: float, lat: float) -> None:
        self.x = np.array([lon, lat, 0.0, 0.0], dtype=np.float64)
        self.P = np.diag([self.r, self.r, 25.0, 25.0])

    def predict(self, dt: float) -> tuple[float, float]:
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        q = self.q
        G = np.array([[dt * dt / 2, 0], [0, dt * dt / 2], [dt, 0], [0, dt]])
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + q * (G @ G.T)
        return float(self.x[0]), float(self.x[1])

    def update(self, lon: float, lat: float) -> None:
        H = np.zeros((2, 4))
        H[0, 0] = H[1, 1] = 1.0
        R = np.eye(2) * self.r
        z = np.array([lon, lat])
        y = z - H @ self.x
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ H) @ self.P


def _nearest_wrap(lon_deg: float, ref_uw_deg: float) -> float:
    """Map a wrapped longitude onto the unwrapped axis, nearest to `ref`."""
    return ref_uw_deg + ((lon_deg - ref_uw_deg + 180.0) % 360.0) - 180.0


def _load_model():
    from ultralytics import YOLO
    return YOLO(str(BALL_MODEL_PATH))


def extract_ball_records(
    cap: cv2.VideoCapture,
    start_s: float,
    end_s: float,
    aim_times: np.ndarray,
    aim_lons_uw: np.ndarray,
    aim_lats: np.ndarray,
    cfg: Optional[BallAimConfig] = None,
    cache_path: Optional[Path] = None,
    model=None,
) -> list[dict]:
    """One record per BALL_HZ sample: {"t", "det" | None, "pred", "confirmed"}.

    `det` = [lon_uw, lat, conf] accepted fix; `pred` = tracked ball prediction
    (present while a track is alive). The detection crop follows the CONFIRMED
    track's prediction (clamped near the player aim) so a counter's long ball
    stays in the detector's view; otherwise it sits on the player aim — the
    same framing the training crops used.

    Caches to `cache_path` (JSON) and returns the cache when it already exists,
    so re-renders and the review-label pass never pay the decode twice.
    """
    cfg = cfg or BallAimConfig()
    if cache_path is not None and cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eq_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    eq_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if model is None:
        model = _load_model()

    ts = np.arange(start_s, end_s, 1.0 / cfg.hz)
    records: list[dict] = []
    kf = _BallKalman()
    alive = False
    hits = 0
    misses = 0
    last_t: Optional[float] = None

    f0 = max(0, int(round(start_s * fps)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    consumed = f0 - 1        # index of the last frame grab() consumed
    for t in ts:
        target = int(round(t * fps))
        # Sequential grab-only skip to the sample frame (no random seeks);
        # retrieve() then decodes the most recently grabbed frame.
        while consumed < target:
            if not cap.grab():
                break
            consumed += 1
        ok, frame = cap.retrieve() if consumed == target else (False, None)
        if not ok or frame is None:
            records.append({"t": float(t), "det": None, "pred": None,
                            "confirmed": False})
            continue

        aim_lon = float(np.interp(t, aim_times, aim_lons_uw))
        aim_lat = float(np.interp(t, aim_times, aim_lats))

        pred = None
        if alive and last_t is not None:
            pred = kf.predict(max(1e-3, t - last_t))
            last_t = t
        confirmed = alive and hits >= cfg.confirm_hits

        # Crop center: follow the confirmed ball, clamped near the player aim.
        if confirmed and pred is not None:
            c_lon = aim_lon + float(np.clip(pred[0] - aim_lon,
                                            -cfg.max_follow_offset_deg,
                                            cfg.max_follow_offset_deg))
            c_lat = float(np.clip(pred[1], aim_lat - 15.0, aim_lat + 15.0))
        else:
            c_lon, c_lat = aim_lon, aim_lat

        crop = render_perspective(frame, ((c_lon + 180.0) % 360.0) - 180.0,
                                  c_lat, config.CROP_FOV_DEG,
                                  config.CROP_W, config.CROP_H)
        res = model.predict(crop, conf=cfg.conf, device=config.DEVICE,
                            verbose=False)
        best = None
        if res and res[0].boxes is not None and len(res[0].boxes):
            b = max(res[0].boxes, key=lambda bb: float(bb.conf[0].item()))
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
            u, v = crop_to_equirect_pixel(
                (x1 + x2) / 2.0, (y1 + y2) / 2.0,
                ((c_lon + 180.0) % 360.0) - 180.0, c_lat, config.CROP_FOV_DEG,
                eq_w, eq_h, config.CROP_W, config.CROP_H)
            lon = _nearest_wrap((u / eq_w - 0.5) * 360.0, aim_lon)
            lat = (0.5 - v / eq_h) * 180.0
            best = (lon, lat, float(b.conf[0].item()))

        det = None
        if best is not None:
            lon, lat, bc = best
            if not alive:
                kf.init(lon, lat)
                alive, hits, misses, last_t = True, 1, 0, t
                det = [lon, lat, bc]
            else:
                gate_ref = pred if pred is not None else (kf.x[0], kf.x[1])
                if math.hypot(lon - gate_ref[0], lat - gate_ref[1]) <= cfg.gate_deg:
                    kf.update(lon, lat)
                    hits += 1
                    misses = 0
                    last_t = t
                    det = [lon, lat, bc]
                else:
                    misses += 1
        else:
            if alive:
                misses += 1
        if alive and misses >= cfg.drop_misses:
            alive, hits, misses, last_t = False, 0, 0, None

        confirmed = alive and hits >= cfg.confirm_hits
        records.append({
            "t": float(t),
            "det": det,
            "pred": [float(kf.x[0]), float(kf.x[1])] if alive else None,
            "confirmed": bool(confirmed),
        })

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(records))
    n_det = sum(1 for r in records if r["det"])
    n_conf = sum(1 for r in records if r["confirmed"])
    log.info("  ball pass: %d samples, %d fixes (%.0f%%), %d confirmed (%.0f%%)",
             len(records), n_det, 100.0 * n_det / max(1, len(records)),
             n_conf, 100.0 * n_conf / max(1, len(records)))
    return records


def blend_ball_aim(
    aim_times: np.ndarray,
    aim_lons_uw: np.ndarray,
    aim_lats: np.ndarray,
    aim_fovs: np.ndarray,
    records: list[dict],
    cfg: Optional[BallAimConfig] = None,
    aim_hz: float = 5.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Pure math: bias the aim toward the confirmed ball; widen FOV to fit it.

    Returns (lons, lats, fovs, stats). With no confirmed samples the aim is
    returned unchanged (identity) — the player aim is always the fallback.
    """
    cfg = cfg or BallAimConfig()
    lons = np.asarray(aim_lons_uw, dtype=np.float64)
    lats = np.asarray(aim_lats, dtype=np.float64)
    fovs = np.asarray(aim_fovs, dtype=np.float64)
    stats = {"confirmed_frac": 0.0, "mean_abs_bias_deg": 0.0}
    if not records or lons.size == 0:
        return lons, lats, fovs, stats

    rt = np.array([r["t"] for r in records])
    conf = np.array([1.0 if r["confirmed"] else 0.0 for r in records])
    ball_lon = np.array([r["pred"][0] if (r["confirmed"] and r["pred"]) else np.nan
                         for r in records])
    ball_lat = np.array([r["pred"][1] if (r["confirmed"] and r["pred"]) else np.nan
                         for r in records])
    if not np.any(conf > 0):
        return lons, lats, fovs, stats

    # Confidence weight, eased over ramp_s so the bias never snaps in/out.
    k = max(1, int(round(cfg.ramp_s * cfg.hz)))
    w = np.convolve(conf, np.ones(k) / k, mode="same")

    # Hold the last ball position through short unconfirmed gaps (weight is
    # already decaying there); forward/back fill inside the record grid.
    idx = np.where(~np.isnan(ball_lon))[0]
    filled_lon = np.interp(np.arange(len(records)), idx, ball_lon[idx])
    filled_lat = np.interp(np.arange(len(records)), idx, ball_lat[idx])

    w_t = np.interp(aim_times, rt, w)
    ball_lon_t = np.interp(aim_times, rt, filled_lon)
    ball_lat_t = np.interp(aim_times, rt, filled_lat)

    dt = 1.0 / aim_hz
    bias_lon = np.clip(ball_lon_t - lons, -cfg.max_bias_deg, cfg.max_bias_deg) \
        * cfg.blend_w * w_t
    bias_lat = np.clip(ball_lat_t - lats, -8.0, 8.0) * cfg.lat_blend_w * w_t
    lons2 = tv_aim.smooth_damp(lons + bias_lon, dt, cfg.smooth_time_s, 30.0)
    lats2 = tv_aim.smooth_damp(lats + bias_lat, dt, cfg.smooth_time_s, 12.0)

    # Widen the FOV so the ball stays inside the frame even when the bias
    # clamp keeps the center closer to the players.
    need_half = np.abs(ball_lon_t - lons2) + cfg.fov_margin_deg
    fov_needed = np.where(w_t > 0.4, 2.0 * need_half, fovs)
    fov_target = np.clip(np.maximum(fovs, fov_needed), None, cfg.fov_max_deg)
    fovs2 = tv_aim.slew_limit_fov(fov_target, dt, cfg.fov_rate_deg_s,
                                  cfg.fov_deadzone_deg)

    stats["confirmed_frac"] = float(np.mean(conf))
    stats["mean_abs_bias_deg"] = float(np.mean(np.abs(lons2 - lons)))
    return lons2, lats2, fovs2, stats
