"""Unit tests for the TV-reel aim-quality transforms (`post_game.tv_aim`).

Pure-function tests — no video, no Firestore, no rendering. Runnable either
under pytest or directly (`python -m post_game.test_tv_aim`) so they validate
even where pytest isn't installed.
"""

from __future__ import annotations

import math

import numpy as np

from .tv_aim import (
    AimConfig,
    apply_dead_zone,
    broadcast_follow,
    consensus_velocity,
    densest_lonlat,
    event_framing,
    fov_for_player_size,
    holt_lead,
    kalman_lead,
    slew_limit_fov,
    smooth_damp,
    summarize_aim,
    velocity_lead,
)


# --- Phase 0: summarize_aim ----------------------------------------------

def test_summarize_aim_monotonic_ramp():
    t = np.arange(0, 30, 0.2)
    lons = 0.5 * t            # 0.5 deg/s ramp, 15 deg total span over 30s
    lats = np.zeros_like(t)
    s = summarize_aim(t, lons, lats)
    assert s["reversals"] == 0
    assert abs(s["lon_span_deg"] - np.ptp(lons)) < 1e-6
    assert abs(s["mean_abs_v_deg_s"] - 0.5) < 1e-6  # the ramp rate


def test_summarize_aim_detects_fallback():
    t = np.arange(0, 10, 0.2)
    lons = np.full_like(t, 12.0)   # held at the fallback the whole time
    lats = np.zeros_like(t)
    s = summarize_aim(t, lons, lats, fallback_lon=12.0)
    assert s["fallback_frac"] == 1.0
    assert s["lon_span_deg"] == 0.0


# --- Phase 1: dead-zone hysteresis ---------------------------------------

def test_dead_zone_holds_inside_band():
    # Target jitters ±2 deg around 0; half-FOV=35, frac=0.33 → band ±11.5 deg.
    rng = np.random.default_rng(0)
    x = rng.uniform(-2.0, 2.0, size=200)
    out = apply_dead_zone(x, half_fov_deg=35.0, dead_zone_frac=0.33,
                          max_pan_deg_s=25.0, dt=0.2)
    # Camera should never move: every sample stays at the initial commit.
    assert np.allclose(out, out[0])


def test_dead_zone_follows_large_move_to_band_edge():
    # Step the target far past the band; camera must catch up but leave the
    # target sitting on the band EDGE (hysteresis), not dead-centre.
    half_fov, frac = 35.0, 0.33
    dz = frac * half_fov
    x = np.concatenate([np.zeros(5), np.full(200, 100.0)])
    out = apply_dead_zone(x, half_fov_deg=half_fov, dead_zone_frac=frac,
                          max_pan_deg_s=1000.0, dt=0.2)  # high slew → settles fast
    # After settling, committed aim trails the target by exactly the dead zone.
    assert abs((x[-1] - out[-1]) - dz) < 1e-6


def test_dead_zone_respects_slew_limit():
    x = np.concatenate([np.zeros(2), np.full(50, 100.0)])
    out = apply_dead_zone(x, half_fov_deg=35.0, dead_zone_frac=0.33,
                          max_pan_deg_s=10.0, dt=0.2)  # max 2 deg/step
    steps = np.abs(np.diff(out))
    assert steps.max() <= 2.0 + 1e-9


# --- Phase 2: Kalman lead -------------------------------------------------

def test_kalman_lead_tracks_constant_velocity():
    dt = 0.2
    t = np.arange(0, 20, dt)
    truth = 3.0 * t                      # 3 deg/s
    noisy = truth + np.random.default_rng(1).normal(0, 0.5, size=t.size)
    out = kalman_lead(noisy, dt=dt, lead_s=0.4, q=4.0, r=6.0)
    # On a constant-velocity track the lead output should sit AHEAD of truth by
    # ~v*lead once converged (second half of the series).
    half = t.size // 2
    lead_gap = np.mean(out[half:] - truth[half:])
    assert lead_gap > 0.0                # genuinely leading, not trailing
    assert abs(lead_gap - 3.0 * 0.4) < 0.8


def test_kalman_lead_denoises():
    dt = 0.2
    t = np.arange(0, 20, dt)
    truth = np.zeros_like(t)
    noisy = truth + np.random.default_rng(2).normal(0, 1.0, size=t.size)
    out = kalman_lead(noisy, dt=dt, lead_s=0.0, q=1.0, r=10.0)
    assert np.std(out[10:]) < np.std(noisy)   # output is smoother than input


# --- Phase 3: spherical heat-map -----------------------------------------

def test_densest_lonlat_ignores_far_outlier():
    # 5 players clustered near (10, 2) + a lone keeper at (-40, 0). Mode must
    # land on the cluster, not the midpoint.
    lons = np.array([9.0, 10.0, 11.0, 10.5, 9.5, -40.0])
    lats = np.array([2.0, 2.0, 1.5, 2.5, 2.0, 0.0])
    aim = densest_lonlat(lons, lats, sigma_deg=3.5)
    assert aim is not None
    assert abs(aim[0] - 10.0) < 2.0       # near the cluster centre
    assert aim[0] > 0.0                    # NOT dragged toward the keeper


def test_densest_lonlat_empty():
    assert densest_lonlat(np.array([]), np.array([]), 3.5) is None


# --- Phase 4: event framing ----------------------------------------------

def test_event_framing_widens_inside_window_only():
    cfg = AimConfig(base_fov_deg=70.0, event_widen_fov_deg=92.0,
                    event_pre_s=2.0, event_post_s=4.0, event_ramp_s=1.0)
    t = np.arange(0, 30, 0.2)
    fovs = np.full_like(t, cfg.base_fov_deg)
    out = event_framing(t, fovs, [(15.0, "CORNER")], cfg)
    # Far from the event: untouched.
    assert math.isclose(out[0], 70.0)
    # At the event centre: fully widened.
    centre = int(round(15.0 / 0.2))
    assert math.isclose(out[centre], 92.0, abs_tol=1e-6)
    # Never narrower than base, never wider than widen.
    assert out.min() >= 70.0 - 1e-9
    assert out.max() <= 92.0 + 1e-9


def test_event_framing_ignores_non_dead_ball_events():
    cfg = AimConfig()
    t = np.arange(0, 10, 0.2)
    fovs = np.full_like(t, cfg.base_fov_deg)
    out = event_framing(t, fovs, [(5.0, "SUB")], cfg)
    assert np.allclose(out, cfg.base_fov_deg)


# --- Phase 5: Holt lead ---------------------------------------------------

def test_holt_lead_smooths_and_leads():
    dt = 0.2
    t = np.arange(0, 20, dt)
    truth = 2.0 * t
    noisy = truth + np.random.default_rng(3).normal(0, 0.4, size=t.size)
    out = holt_lead(noisy, alpha=0.4, beta=0.2, lead_s=0.4, dt=dt)
    half = t.size // 2
    assert np.std(np.diff(out[half:])) < np.std(np.diff(noisy[half:]))  # smoother
    assert np.mean(out[half:] - truth[half:]) > 0.0                     # leading


def test_half_fov_lat_smaller_than_lon():
    cfg = AimConfig(base_fov_deg=70.0, out_w=1920, out_h=1080)
    assert cfg.half_fov_lat_deg < cfg.half_fov_lon_deg


# --- Motion model: smooth_damp (the stop-and-go fix) ---------------------

def test_smooth_damp_no_stop_and_go_on_step():
    # A step target: the camera must ease over smoothly (monotonic, no holds,
    # no overshoot) — the opposite of the dead-zone's hold-then-lurch.
    dt = 0.2
    x = np.concatenate([np.zeros(5), np.full(80, 40.0)])
    out = smooth_damp(x, dt, smooth_time_s=1.3, max_speed_deg_s=16.0)
    seg = out[5:]                              # the response to the step
    d = np.diff(seg)
    assert np.all(d >= -1e-9)                  # monotonic — never reverses
    assert out.max() <= 40.0 + 1e-6            # no overshoot past the goal
    # "No stop-and-go": once moving, it doesn't freeze for long stretches while
    # still far from the goal. Check it makes continuous progress early on.
    assert seg[10] > seg[1] > seg[0]
    assert abs(out[-1] - 40.0) < 0.5           # converges


def test_smooth_damp_respects_max_speed():
    dt = 0.2
    x = np.concatenate([np.zeros(2), np.full(60, 200.0)])
    out = smooth_damp(x, dt, smooth_time_s=1.0, max_speed_deg_s=10.0)
    # Per-step change capped near max_speed*dt (=2.0 deg) with a small margin
    # for the damped recurrence.
    assert np.abs(np.diff(out)).max() <= 2.0 + 0.5


def test_smooth_damp_settles_flat_input():
    out = smooth_damp(np.full(40, 7.0), 0.2, 1.3, 16.0)
    assert np.allclose(out, 7.0)               # nothing to chase → no motion


def test_velocity_lead_leads_constant_velocity():
    dt = 0.2
    t = np.arange(0, 20, dt)
    truth = 2.5 * t
    out = velocity_lead(truth, dt, lead_s=0.3)
    mid = t.size // 2
    assert np.mean(out[mid:] - truth[mid:]) > 0.0   # ahead of the present


# --- Broadcast motion: edge-aware safe-zone follow -----------------------

def test_broadcast_holds_inside_safe_zone():
    # Target jitters +/-4 deg around 0 with safe=8 -> camera must stay still.
    rng = np.random.default_rng(7)
    x = rng.uniform(-4.0, 4.0, size=300)
    out = broadcast_follow(x, 0.2, smooth_time_s=1.0, max_speed_deg_s=24.0, safe_deg=8.0)
    assert np.ptp(out) < 2.0                     # essentially still


def test_broadcast_keeps_action_within_safe_zone():
    # A ramp toward an edge: the camera must never let the action get further
    # than ~safe_deg from frame centre (so a corner is never lost off-frame).
    dt = 0.2
    t = np.arange(0, 30, dt)
    target = 1.5 * t                              # steady drift toward the edge
    safe = 8.0
    out = broadcast_follow(target, dt, smooth_time_s=1.0, max_speed_deg_s=24.0, safe_deg=safe)
    # After the initial catch-up, the action stays within safe+margin of centre.
    err = np.abs(target - out)[t > 4]
    assert err.max() <= safe + 4.0                # never drifts to the true edge


def test_broadcast_commit_is_smooth_monotonic():
    dt = 0.2
    rng = np.random.default_rng(9)
    jit = rng.uniform(-3.0, 3.0, size=30)
    x = np.concatenate([jit, np.full(80, 30.0)])
    out = broadcast_follow(x, dt, smooth_time_s=1.0, max_speed_deg_s=24.0, safe_deg=8.0)
    seg = out[30:]
    assert np.all(np.diff(seg) >= -1e-6)          # no back-and-forth on the commit


def test_broadcast_far_fewer_reversals_than_raw():
    rng = np.random.default_rng(11)
    t = np.arange(0, 40, 0.2)
    raw = 10.0 * np.sin(t / 6.0) + rng.normal(0, 3.0, size=t.size)
    out = broadcast_follow(raw, 0.2, smooth_time_s=1.0, max_speed_deg_s=24.0, safe_deg=8.0)
    def reversals(d):
        s = np.sign(np.diff(d)); s[s == 0] = 1
        return int(np.sum(s[1:] != s[:-1]))
    assert reversals(out) < reversals(raw) / 3

# --- Consensus-velocity lead --------------------------------------------

def test_consensus_velocity_agrees_on_direction():
    # All players drifting +x at ~1 m/s with noise → consensus points +x.
    rng = np.random.default_rng(5)
    vx = 1.0 + rng.normal(0, 0.3, size=10)
    vy = rng.normal(0, 0.3, size=10)
    lvx, lvy = consensus_velocity(vx, vy, deadband_ms=0.3)
    assert lvx > 0.7
    assert abs(lvy) < abs(lvx)


def test_consensus_velocity_deadband_zeros_calm_play():
    # Random milling about with no net direction → below deadband → zero.
    rng = np.random.default_rng(6)
    vx = rng.normal(0, 0.15, size=12)
    vy = rng.normal(0, 0.15, size=12)
    assert consensus_velocity(vx, vy, deadband_ms=0.3) == (0.0, 0.0)


def test_consensus_velocity_ignores_nans():
    vx = np.array([np.nan, 1.0, 1.0, np.nan, 1.0])
    vy = np.array([np.nan, 0.0, 0.0, np.nan, 0.0])
    lvx, lvy = consensus_velocity(vx, vy, deadband_ms=0.3)
    assert abs(lvx - 1.0) < 1e-9 and lvy == 0.0


def test_consensus_velocity_all_nan_returns_zero():
    nan = np.full(4, np.nan)
    assert consensus_velocity(nan, nan, deadband_ms=0.3) == (0.0, 0.0)


# --- FOV slew limiter (anti-pump) ----------------------------------------

def test_slew_limit_fov_caps_rate():
    x = np.concatenate([np.full(5, 70.0), np.full(40, 100.0)])
    out = slew_limit_fov(x, dt=0.2, max_rate_deg_s=4.0, deadzone_deg=6.0)
    assert np.abs(np.diff(out)).max() <= 4.0 * 0.2 + 1e-9


def test_slew_limit_fov_deadzone_holds_through_jitter():
    rng = np.random.default_rng(3)
    x = 80.0 + rng.uniform(-3.0, 3.0, size=200)
    out = slew_limit_fov(x, dt=0.2, max_rate_deg_s=4.0, deadzone_deg=6.0)
    assert np.ptp(out) < 1.0


def test_slew_limit_fov_fewer_reversals_than_input():
    rng = np.random.default_rng(8)
    t = np.arange(0, 40, 0.2)
    x = 80.0 + 8.0 * np.sin(t / 5.0) + rng.normal(0, 4.0, size=t.size)
    out = slew_limit_fov(x, dt=0.2, max_rate_deg_s=4.0, deadzone_deg=6.0)
    def rev(d):
        s = np.sign(np.diff(d)); s[s == 0] = 1
        return int(np.sum(s[1:] != s[:-1]))
    assert rev(out) < rev(x) / 2


# --- Distance/size FOV (equalize on-screen player size) ------------------

def test_fov_for_player_size_far_zooms_in_near_zooms_out():
    far = fov_for_player_size(1.4, target_frac=0.08, out_w=1920, out_h=1080,
                              fov_min_deg=50.0, fov_max_deg=95.0)
    near = fov_for_player_size(8.0, target_frac=0.08, out_w=1920, out_h=1080,
                               fov_min_deg=50.0, fov_max_deg=95.0)
    assert far < near


def test_fov_for_player_size_respects_clamps():
    tiny = fov_for_player_size(0.3, 0.08, 1920, 1080, 50.0, 95.0)
    huge = fov_for_player_size(40.0, 0.08, 1920, 1080, 50.0, 95.0)
    assert tiny == 50.0
    assert huge == 95.0


def test_fov_for_player_size_midfield_near_base():
    fov = fov_for_player_size(3.4, 0.08, 1920, 1080, 50.0, 95.0)
    assert 64.0 <= fov <= 78.0

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} aim-transform tests passed.")
