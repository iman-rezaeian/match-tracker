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
# commercial system has (EIGHT_K_RETEST.md §2). KICK_OUT is this team's
# goal-kick / keeper-distribution tag (a dead-ball restart).
EVENT_FRAMING_TYPES: frozenset[str] = frozenset(
    {"CORNER", "GOAL_KICK", "KICK_OUT", "THROW_IN", "FREE_KICK", "GOAL", "SHOT_ON"}
)


@dataclass
class AimConfig:
    """All aim-quality knobs + feature flags in one place.

    Threaded from `tv_view._build_aim_stream` so a single object controls the
    whole chain and the diagnostic harness can sweep variants cheaply.
    """

    # --- stage selectors -------------------------------------------------
    aim_mode: str = "density_x"        # "density_x" | "sphere_heatmap".
                                       # density_x tracks which END of the field
                                       # play is in (the dominant broadcast
                                       # variable) and is laterally STABLE, so it
                                       # pairs best with the broadcast motion for
                                       # a calm tripod feel. sphere_heatmap is
                                       # more responsive but jitters side-to-side.
    # Camera MOTION model — how the per-time aim target becomes smooth camera
    # motion. NOTE: the original 3 s moving average ("legacy_boxcar") is the
    # DEFAULT on purpose — in side-by-side review it read as the most
    # broadcast-like. The fancier models below (dead-zone, smooth_damp,
    # edge-aware "broadcast") each tested WORSE (restless / stop-and-go) and are
    # kept only as opt-in experiments. Do not switch the default without a fresh
    # side-by-side that the user signs off on.
    #   "legacy_boxcar"   original 3 s moving average (DEFAULT — preferred).
    #   "broadcast"       edge-aware hold-then-pan (tested worse: too stationary).
    #   "smooth_damp"     continuous critically-damped follow (tested restless).
    #   "kalman_deadzone" Kalman lead + Schmitt dead-zone (abrupt stop-and-go).
    #   "learned"         Holt/learned smooth predictor.
    motion_model: str = "legacy_boxcar"
    use_kalman: bool = True            # used only by "kalman_deadzone" model
    use_dead_zone: bool = True         # used only by "kalman_deadzone" model
    use_event_framing: bool = True     # Phase 4: widen FOV on dead balls
    use_learned: bool = False          # legacy flag → maps to "learned" model
    use_dynamic_fov: bool = True       # auto-widen the FOV to keep wide / corner
                                       # / end-to-end play framed (no-ball fix)

    # --- geometry (mirrors tv_view constants; injected, not imported) ----
    base_fov_deg: float = 70.0         # TV_FOV_DEG
    out_w: int = 1920
    out_h: int = 1080
    aim_hz: float = 5.0

    # --- Motion: critically-damped follow + safe-zone (broadcast model) --
    smooth_time_s: float = 1.0         # approx time to commit a pan (lon). Lower
                                       # = snappier / less sluggish. This is the
                                       # "how slow are the pans" knob.
    smooth_time_lat_s: float = 1.6     # tilt damped a bit more (vertical moves
                                       # are more jarring) but no longer sluggish.
    max_pan_speed_deg_s: float = 24.0  # hard cap on horizontal pan rate
    max_tilt_speed_deg_s: float = 12.0 # hard cap on vertical (tilt) rate

    # --- Motion: edge-aware safe zone (the "broadcast" model) ------------
    # Half-width of the central HOLD zone, as a FRACTION of the half-FOV. While
    # the action stays within this central band the camera holds still; once it
    # heads past it (toward the frame edge) the camera pans to keep it in. THE
    # knob for "holds still vs never misses edge/corner action":
    #   smaller → follows sooner, never loses the ball at edges, moves more
    #   larger  → holds longer, calmer, but can let edge action slip
    safe_zone_lon_frac: float = 0.16   # ~0.16*35° ≈ 5.6° before it commits a pan
    safe_zone_lat_frac: float = 0.20   # vertical safe band (×~21.5° half-FOV)

    # --- Phase 1: dead-zone / Schmitt hysteresis -------------------------
    dead_zone_frac: float = 0.33       # fraction of the HALF-FOV the action may
                                       # drift before the camera re-centres
    dead_zone_lat_frac: float = 0.45   # vertical play spread is small → allow
                                       # a wider vertical dead zone
    max_pan_deg_s: float = 25.0        # slew ceiling on the catch-up move so the
                                       # re-centre still glides, never snaps

    # --- Phase 2: predictive lead (gentle; used by smooth_damp + kalman) -
    lead_s: float = 0.25               # seconds to extrapolate ahead of `now`.
                                       # Kept small: a big lead is what made the
                                       # camera feel like it "moves around a lot".
    kf_q: float = 4.0                  # process noise (deg²/s³-ish) — higher =
                                       # trust motion more, snappier, more overshoot
    kf_r: float = 6.0                  # measurement noise (deg²) — higher =
                                       # trust the noisy density aim less
    boxcar_window: int = 15            # legacy moving-average window when KF off

    # --- Phase 3: spherical heat-map -------------------------------------
    heat_sigma_deg: float = 3.5        # gaussian influence radius on the sphere

    # --- Dynamic FOV: auto-widen to keep wide / corner play framed -------
    # The single most effective lever for "misses the ball at corners" without
    # a ball track: when players spread toward the ends / a corner, widen the
    # virtual camera so it all stays in frame; tighten back when play is compact
    # (so players stay large). FOV = clamp(2*cover_halffov*margin, base, max).
    cover_window_m: float = 26.0       # only fit the ACTION: players within this
                                       # field-X window of the densest cluster.
                                       # Excludes the lone far-end keeper so it
                                       # doesn't pin the camera zoomed-out during
                                       # compact play (an attacking phase is
                                       # ~16-22 m; 26 m gives a little headroom).
    cover_percentile: float = 80.0     # percentile of player angular spread used
                                       # (not max → one stray det can't zoom out)
    dynamic_fov_margin: float = 1.15   # headroom multiplier on the measured spread
    dynamic_fov_max_deg: float = 110.0 # never zoom out past this (keeps players
                                       # recognisable; field spans ~165° here)
    dynamic_fov_smooth_s: float = 2.0  # smooth the FOV track so zoom breathes
                                       # slowly instead of pumping frame-to-frame

    # --- Phase 4: event framing ------------------------------------------
    event_widen_fov_deg: float = 84.0  # zoom OUT to this FOV around a restart.
                                       # Gentler than before so it doesn't read
                                       # as the camera lurching.
    event_pre_s: float = 1.5           # widen this long BEFORE the logged event
    event_post_s: float = 3.0          # …and this long after
    event_ramp_s: float = 1.2          # raised-cosine in/out ramp duration

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


# --- Motion model: critically-damped continuous follow (smooth_damp) ------

def smooth_damp(
    target: np.ndarray, dt: float, smooth_time_s: float, max_speed_deg_s: float,
) -> np.ndarray:
    """Critically-damped follow (Unity-style SmoothDamp) over a 1-D aim series.

    Produces buttery, CONTINUOUS camera motion that eases into and out of
    movement with no overshoot and — crucially — no dead-zone "stop-and-go".
    At every sample the camera is either smoothly accelerating toward the play
    or smoothly decelerating to a rest as the play settles, exactly like a good
    gimbal / Steadicam operator. This is the fix for "it stops and goes too
    much": there is no hold-then-lurch, only one smooth velocity that changes
    gradually.

    `smooth_time_s` is roughly the time to converge on the target (larger =
    calmer/slower). `max_speed_deg_s` caps the pan rate so a far target can't
    cause a violent whip. Standard critically-damped recurrence (no tuning
    beyond those two knobs).
    """
    z = np.asarray(target, dtype=np.float64)
    n = z.size
    if n == 0:
        return z
    st = max(1e-4, smooth_time_s)
    omega = 2.0 / st
    out = np.empty(n, dtype=np.float64)
    x = float(z[0])      # current camera position
    v = 0.0              # current camera velocity
    out[0] = x
    a = omega * dt
    exp = 1.0 / (1.0 + a + 0.48 * a * a + 0.235 * a * a * a)
    max_change = max_speed_deg_s * st
    for k in range(1, n):
        goal = float(z[k])
        change = x - goal
        if change > max_change:
            change = max_change
        elif change < -max_change:
            change = -max_change
        goal_adj = x - change
        temp = (v + omega * change) * dt
        v = (v - omega * temp) * exp
        new = goal_adj + (change + temp) * exp
        # Anti-overshoot: never cross the (un-clamped) goal.
        if (z[k] - x > 0.0) == (new > z[k]):
            new = float(z[k])
            v = (new - z[k]) / dt
        x = new
        out[k] = x
    return out


def hold_band(target: np.ndarray, band_deg: float) -> np.ndarray:
    """Latch the aim during small jitter; only let it move once the play has
    travelled beyond `band_deg` from the held point (Schmitt hysteresis).

    This is the "broadcast camera holds still" behaviour. A real operator does
    NOT chase every step a cluster centroid takes — they hold a framing and
    only re-frame when the action has clearly moved. Feeding the RESULT of this
    into `smooth_damp` gives the best of both: the camera HOLDS during the small
    back-and-forth wobble (no restless motion), and when play genuinely shifts
    it commits with a single smooth, eased pan (no lurch). The held series is
    piecewise-constant, so `smooth_damp` sits perfectly still between commits.
    """
    z = np.asarray(target, dtype=np.float64)
    n = z.size
    if n == 0:
        return z
    band = max(0.0, band_deg)
    held = float(z[0])
    out = np.empty(n, dtype=np.float64)
    for k in range(n):
        e = float(z[k]) - held
        if abs(e) > band:
            held = float(z[k]) - math.copysign(band, e)
        out[k] = held
    return out


def broadcast_follow(
    target: np.ndarray, dt: float, smooth_time_s: float, max_speed_deg_s: float,
    safe_deg: float,
) -> np.ndarray:
    """Edge-aware (safe-zone) broadcast camera motion.

    A real broadcast operator HOLDS a framing while the action sits comfortably
    inside the frame, and pans only when the action approaches the FRAME EDGE —
    so the action is NEVER lost off-frame (the corner / sideline / centre-line
    cases), yet the camera is still most of the time.

    `safe_deg` is the half-width of a central "safe zone" measured from the
    CURRENT camera centre (typically a fraction of the half-FOV). While the
    target stays within `safe_deg` of where the camera points, the camera holds
    perfectly still. The instant the target moves beyond the safe zone — i.e.
    heads toward the edge — the camera eases over (critically-damped) just
    enough to bring the action back to the safe-zone boundary, then settles.

    This is strictly better than a latch-to-a-point hold: because the trigger
    is relative to the camera and scaled to the FOV, action can never drift to
    the true frame edge unnoticed (fixes "misses the ball at corners / near the
    centre line"), while in-frame jitter still produces zero motion (keeps the
    calm tripod feel). `smooth_damp`-style integration keeps every commit smooth
    with no overshoot and no lurch.
    """
    z = np.asarray(target, dtype=np.float64)
    n = z.size
    if n == 0:
        return z
    st = max(1e-4, smooth_time_s)
    omega = 2.0 / st
    a = omega * dt
    exp = 1.0 / (1.0 + a + 0.48 * a * a + 0.235 * a * a * a)
    max_change = max_speed_deg_s * st
    safe = max(0.0, safe_deg)
    out = np.empty(n, dtype=np.float64)
    x = float(z[0])     # camera position
    v = 0.0             # camera velocity
    out[0] = x
    for k in range(1, n):
        tgt = float(z[k])
        e = tgt - x
        # Edge-aware goal: hold while inside the safe zone, else bring the
        # action back to the safe-zone boundary (never let it reach the edge).
        if abs(e) <= safe:
            goal = x
        else:
            goal = tgt - math.copysign(safe, e)
        # Critically-damped step toward goal (smooth, eased, no lurch).
        change = x - goal
        if change > max_change:
            change = max_change
        elif change < -max_change:
            change = -max_change
        goal_adj = x - change
        temp = (v + omega * change) * dt
        v = (v - omega * temp) * exp
        new = goal_adj + (change + temp) * exp
        if (goal - x > 0.0) == (new > goal):     # anti-overshoot
            new = goal
            v = (new - goal) / dt
        x = new
        out[k] = x
    return out


def velocity_lead(x: np.ndarray, dt: float, lead_s: float, vel_smooth: int = 5) -> np.ndarray:
    """Gentle predictive lead: extrapolate `lead_s` ahead using a SMOOTHED
    velocity estimate, so the smooth follow anticipates the play without the
    jitter a high-gain Kalman lead produced.

    The velocity is low-pass filtered before extrapolation precisely so that
    the discrete jumps in the per-time aim don't get amplified into the kind of
    restless motion the user objected to.
    """
    z = np.asarray(x, dtype=np.float64)
    n = z.size
    if n < 3 or lead_s <= 0:
        return z
    v = np.gradient(z, dt)
    k = max(1, int(vel_smooth))
    if k > 1:
        ker = np.ones(k) / k
        v = np.convolve(v, ker, mode="same")
    return z + v * lead_s


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
