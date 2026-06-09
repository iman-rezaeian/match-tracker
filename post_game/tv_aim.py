"""Aim-quality layer for the TV-reel virtual camera.

`tv_view.py` owns the expensive perspective RENDER; this module owns the cheap
AIM MATH that decides where the virtual camera points each 5 Hz sample. Keeping
the transforms here — as pure functions over numpy arrays — means every aim
upgrade can be A/B-tested on the aim stream in milliseconds, WITHOUT paying the
multi-hour render (the single most important fact about this pipeline; see
`EIGHT_K_RETEST.md` §2 caveat).

Pipeline order applied by `tv_view._build_aim_stream`:

    per-time aim  →  smoother/lead  →  dead-zone hold  →  reversal safety net
                  →  tilt + clamp   →  event-framing FOV overrides

Each stage is gated by a flag on `AimConfig` so it can be disabled to isolate a
regression. Defaults reproduce the historical behaviour except where a stage is
explicitly switched on.

Stages
------
- Phase 0  `summarize_aim`            — diagnostic stats (span / velocity / reversals)
- Phase 1  `apply_dead_zone`          — Schmitt-trigger hysteresis (kills micro-pan)
- Phase 2  `kalman_lead`              — constant-velocity KF + predictive lead
- Phase 3  `densest_lonlat`           — spherical heat-map mode (mean-shift on the sphere)
- Phase 4  `event_framing`            — widen FOV around coach-logged dead balls
- Phase 5  `HoltLeadSmoother`/`LearnedSmoothPredictor` — learned-style smooth predictor
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

# Event types that warrant a wide "establish the restart" framing. These are
# the dead-ball restarts the coach logs from the touchline — the free edge no
# commercial system has (EIGHT_K_RETEST.md §2).
EVENT_FRAMING_TYPES: frozenset[str] = frozenset(
    {"CORNER", "GOAL_KICK", "THROW_IN", "FREE_KICK", "GOAL", "SHOT_ON"}
)


@dataclass
class AimConfig:
    """All aim-quality knobs + feature flags in one place.

    Threaded from `tv_view._build_aim_stream` so a single object controls the
    whole chain and the diagnostic harness can sweep variants cheaply.
    """

    # --- stage selectors -------------------------------------------------
    aim_mode: str = "density_x"        # "density_x" (legacy) | "sphere_heatmap"
    use_kalman: bool = True            # Phase 2: KF lead replaces the boxcar
    use_dead_zone: bool = True         # Phase 1: hysteresis hold
    use_event_framing: bool = True     # Phase 4: widen FOV on dead balls
    use_learned: bool = False          # Phase 5: Holt/learned smooth predictor

    # --- geometry (mirrors tv_view constants; injected, not imported) ----
    base_fov_deg: float = 70.0         # TV_FOV_DEG
    out_w: int = 1920
    out_h: int = 1080
    aim_hz: float = 5.0

    # --- Phase 1: dead-zone / Schmitt hysteresis -------------------------
    dead_zone_frac: float = 0.33       # fraction of the HALF-FOV the action may
                                       # drift before the camera re-centres
    dead_zone_lat_frac: float = 0.45   # vertical play spread is small → allow
                                       # a wider vertical dead zone
    max_pan_deg_s: float = 25.0        # slew ceiling on the catch-up move so the
                                       # re-centre still glides, never snaps

    # --- Phase 2: Kalman predictive lead ---------------------------------
    lead_s: float = 0.4                # seconds to extrapolate ahead of `now`
    kf_q: float = 4.0                  # process noise (deg²/s³-ish) — higher =
                                       # trust motion more, snappier, more overshoot
    kf_r: float = 6.0                  # measurement noise (deg²) — higher =
                                       # trust the noisy density aim less
    boxcar_window: int = 15            # legacy moving-average window when KF off

    # --- Phase 3: spherical heat-map -------------------------------------
    heat_sigma_deg: float = 3.5        # gaussian influence radius on the sphere

    # --- Phase 4: event framing ------------------------------------------
    event_widen_fov_deg: float = 92.0  # zoom OUT to this FOV around a restart
    event_pre_s: float = 2.0           # widen this long BEFORE the logged event
    event_post_s: float = 4.0          # …and this long after
    event_ramp_s: float = 1.0          # raised-cosine in/out ramp duration

    # --- Phase 5: learned predictor --------------------------------------
    learned_model_path: Optional[str] = None  # optional pickled regressor
    holt_alpha: float = 0.4            # Holt level smoothing
    holt_beta: float = 0.2             # Holt trend smoothing

    @property
    def half_fov_lon_deg(self) -> float:
        return self.base_fov_deg / 2.0

    @property
    def half_fov_lat_deg(self) -> float:
        """Vertical half-FOV derived from the horizontal FOV + aspect ratio.

        `render_perspective` treats `fov_deg` as the HORIZONTAL field of view
        (f = out_w / (2 tan(fov/2))), so the vertical FOV is the aspect-scaled
        angle, not the same number.
        """
        f = self.out_w / (2.0 * math.tan(math.radians(self.base_fov_deg) / 2.0))
        return math.degrees(math.atan((self.out_h / 2.0) / f))


# --- Phase 0: diagnostics ------------------------------------------------

def summarize_aim(
    times: np.ndarray, lons: np.ndarray, lats: np.ndarray,
    fallback_lon: Optional[float] = None,
) -> dict:
    """Cheap health stats for an aim stream — run BEFORE any render.

    A stream that silently collapsed to the field-center fallback looks like a
    slow pan after smoothing; these numbers expose it in 30 s instead of after
    a multi-hour render (see `/memories/repo/tv-view-aim-stream.md`). Healthy
    aim over an active 30 s window: span ≥ 30°, mean|v| ≥ 2°/s.
    """
    times = np.asarray(times, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)
    if lons.size < 2:
        return {
            "n": int(lons.size), "lon_span_deg": 0.0, "lat_span_deg": 0.0,
            "mean_abs_v_deg_s": 0.0, "reversals": 0, "fallback_frac": 0.0,
        }
    dt = float(np.median(np.diff(times))) if times.size > 1 else 1.0
    dlon = np.diff(lons)
    v = dlon / max(dt, 1e-9)
    # Direction reversals in the lon series (turning points).
    sign = np.sign(dlon)
    sign[sign == 0] = 1
    reversals = int(np.sum(sign[1:] != sign[:-1]))
    fallback_frac = 0.0
    if fallback_lon is not None:
        fallback_frac = float(np.mean(np.isclose(lons, fallback_lon, atol=1e-6)))
    return {
        "n": int(lons.size),
        "lon_span_deg": float(np.ptp(lons)),
        "lat_span_deg": float(np.ptp(lats)),
        "mean_abs_v_deg_s": float(np.mean(np.abs(v))),
        "max_abs_v_deg_s": float(np.max(np.abs(v))),
        "reversals": reversals,
        "fallback_frac": fallback_frac,
    }


# --- Phase 1: dead-zone / Schmitt-trigger hysteresis ---------------------

def apply_dead_zone(
    x: np.ndarray, half_fov_deg: float, dead_zone_frac: float,
    max_pan_deg_s: float, dt: float,
) -> np.ndarray:
    """Hold the committed aim until the target leaves a dead zone, then glide.

    Classic Schmitt-trigger / dead-band camera control: the virtual camera
    does NOT chase every sub-degree wobble of the target. It stays put while
    the action is within `dead_zone_frac · half_FOV` of the current centre.
    Once the target crosses that band, the camera moves just enough to put the
    target back ON the band edge (not the centre) — the hysteresis that stops
    re-trigger chatter — rate-limited by `max_pan_deg_s` so the catch-up still
    glides. Removes micro-pan WITHOUT adding lag, because inside the band there
    is nothing to lag.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    dz = max(0.0, dead_zone_frac * half_fov_deg)
    max_step = max(1e-6, max_pan_deg_s * dt)
    c = float(x[0])
    out = np.empty_like(x)
    for i in range(x.size):
        e = float(x[i]) - c
        if abs(e) > dz:
            desired = float(x[i]) - math.copysign(dz, e)
            step = desired - c
            if step > max_step:
                step = max_step
            elif step < -max_step:
                step = -max_step
            c += step
        out[i] = c
    return out


# --- Phase 2: constant-velocity Kalman filter + predictive lead ----------

def kalman_lead(
    x: np.ndarray, dt: float, lead_s: float, q: float, r: float,
) -> np.ndarray:
    """Constant-velocity Kalman smoother that outputs a `lead_s`-ahead aim.

    State = [position, velocity]. The filter both DENOISES the discrete
    density-aim jumps (so it can replace the boxcar moving average) and exposes
    a velocity estimate, which we extrapolate `lead_s` into the future so the
    camera ANTICIPATES the play instead of trailing it. Tuned by `q` (trust in
    motion) and `r` (trust in the measurement).
    """
    z = np.asarray(x, dtype=np.float64)
    n = z.size
    if n == 0:
        return z
    F = np.array([[1.0, dt], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    # Continuous white-noise-acceleration process covariance.
    Q = q * np.array([[dt**3 / 3.0, dt**2 / 2.0], [dt**2 / 2.0, dt]])
    R = np.array([[r]])
    xk = np.array([[z[0]], [0.0]])
    P = np.array([[r, 0.0], [0.0, 1.0]])
    out = np.empty(n, dtype=np.float64)
    for k in range(n):
        # predict
        xk = F @ xk
        P = F @ P @ F.T + Q
        # update
        y = z[k] - (H @ xk)[0, 0]
        S = (H @ P @ H.T + R)[0, 0]
        K = (P @ H.T) / S
        xk = xk + K * y
        P = (np.eye(2) - K @ H) @ P
        # lead-extrapolated output: position + velocity · lead
        out[k] = xk[0, 0] + xk[1, 0] * lead_s
    return out


# --- Phase 3: spherical heat-map aim -------------------------------------

def densest_lonlat(
    lons: np.ndarray, lats: np.ndarray, sigma_deg: float,
) -> Optional[tuple[float, float]]:
    """Mean-shift mode of player positions ON THE CAMERA SPHERE → (lon, lat).

    Native to the 360 equirect geometry: instead of binning only along field-X
    (which frames end-to-end play but mis-frames width and corners), score each
    player by a Gaussian kernel over its neighbours in (lon, lat), pick the
    densest player as a seed, then take one kernel-weighted mean-shift step. A
    lone keeper at the far end can't drag the aim to midfield, and corner/wide
    play is framed where it actually is. With ≤ ~16 players the pairwise cost
    is negligible.
    """
    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)
    n = lons.size
    if n == 0:
        return None
    if n == 1:
        return float(lons[0]), float(lats[0])
    pts = np.column_stack([lons, lats])
    diff = pts[:, None, :] - pts[None, :, :]
    d2 = np.sum(diff * diff, axis=-1)
    w = np.exp(-d2 / (2.0 * sigma_deg * sigma_deg))
    score = w.sum(axis=1)
    seed = int(np.argmax(score))
    wk = w[seed]
    wsum = wk.sum()
    if wsum < 1e-12:
        return float(lons[seed]), float(lats[seed])
    cx = float((wk * lons).sum() / wsum)
    cy = float((wk * lats).sum() / wsum)
    return cx, cy


# --- Phase 4: event-aware framing ----------------------------------------

def event_framing(
    times: np.ndarray, fovs: np.ndarray,
    events_vt: Sequence[tuple[float, str]], cfg: AimConfig,
) -> np.ndarray:
    """Widen the FOV around coach-logged dead balls so the restart is framed.

    Returns a per-sample FOV array. Inside `[t-pre, t+post]` of each qualifying
    event the FOV ramps (raised-cosine) from the base up to
    `event_widen_fov_deg` and back, so the zoom-out is smooth, never abrupt.
    Overlapping windows take the widest FOV. Aim direction is intentionally
    left untouched — widening alone keeps the restart in frame without risking
    a bad corner-coordinate guess.
    """
    times = np.asarray(times, dtype=np.float64)
    out = np.asarray(fovs, dtype=np.float64).copy()
    if times.size == 0 or not events_vt:
        return out
    base = cfg.base_fov_deg
    widen = cfg.event_widen_fov_deg
    pre, post, ramp = cfg.event_pre_s, cfg.event_post_s, max(1e-6, cfg.event_ramp_s)
    for t_ev, typ in events_vt:
        if typ not in EVENT_FRAMING_TYPES:
            continue
        lo, hi = t_ev - pre, t_ev + post
        in_win = (times >= lo - ramp) & (times <= hi + ramp)
        if not np.any(in_win):
            continue
        idx = np.where(in_win)[0]
        for i in idx:
            t = times[i]
            if t < lo:                      # ramp up
                f = (t - (lo - ramp)) / ramp
            elif t > hi:                    # ramp down
                f = ((hi + ramp) - t) / ramp
            else:                           # full widen
                f = 1.0
            f = max(0.0, min(1.0, f))
            # raised-cosine ease
            ease = 0.5 - 0.5 * math.cos(math.pi * f)
            fov_here = base + ease * (widen - base)
            if fov_here > out[i]:
                out[i] = fov_here
    return out


# --- Phase 5: learned-style smooth predictor -----------------------------

def holt_lead(x: np.ndarray, alpha: float, beta: float, lead_s: float, dt: float) -> np.ndarray:
    """Holt's linear (double-exponential) smoother with forecast lead.

    A training-free "smooth online predictor": it maintains a level + trend and
    forecasts `lead_s / dt` steps ahead, giving smoothness AND anticipation in a
    single causal pass. Used as the concrete fallback for the learned-predictor
    slot when no trained operator model is supplied.
    """
    z = np.asarray(x, dtype=np.float64)
    n = z.size
    if n == 0:
        return z
    steps = lead_s / max(dt, 1e-9)
    level = z[0]
    trend = 0.0
    out = np.empty(n, dtype=np.float64)
    for k in range(n):
        prev_level = level
        level = alpha * z[k] + (1.0 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1.0 - beta) * trend
        out[k] = level + trend * steps
    return out


class LearnedSmoothPredictor:
    """Slot for a trained human-operator pan/tilt regressor (EIGHT_K_RETEST §2).

    The highest-quality aim upgrade regresses pan/tilt from player+ball+event
    features against HUMAN-OPERATED training footage — which does not exist yet
    for this project. This class is the integration seam so that capability can
    drop in later without touching the render pipeline:

      * If `model_path` points at a pickled regressor exposing `predict(X)`, it
        is loaded and used.
      * Otherwise it falls back to `holt_lead` — a real, training-free smooth
        predictor — so enabling `use_learned` always does something sensible.

    Train a model by collecting (feature, operator-aim) pairs and pickling any
    estimator with sklearn's `predict` API to `model_path`.
    """

    def __init__(self, cfg: AimConfig):
        self.cfg = cfg
        self.model = None
        path = cfg.learned_model_path
        if path and Path(path).exists():
            try:
                import pickle
                with open(path, "rb") as fh:
                    self.model = pickle.load(fh)
            except Exception:
                self.model = None

    def smooth(self, x: np.ndarray, dt: float) -> np.ndarray:
        """Smooth + lead a 1-D aim axis."""
        if self.model is not None and hasattr(self.model, "predict"):
            x = np.asarray(x, dtype=np.float64)
            # Feature window: [position, lag1, lag2] → next-aim regression.
            lag1 = np.concatenate([x[:1], x[:-1]])
            lag2 = np.concatenate([x[:2], x[:-2]])
            X = np.column_stack([x, lag1, lag2])
            try:
                return np.asarray(self.model.predict(X), dtype=np.float64)
            except Exception:
                pass
        return holt_lead(x, self.cfg.holt_alpha, self.cfg.holt_beta,
                         self.cfg.lead_s, dt)
