"""Unit tests for the ball-aware aim math (tv_ball) — no video, no model."""
from __future__ import annotations

import numpy as np

from post_game.tv_ball import (BallAimConfig, _BallKalman, _nearest_wrap,
                               blend_ball_aim)


def _aim(n=100, hz=5.0, lon=10.0):
    t = np.arange(n) / hz
    return t, np.full(n, lon), np.full(n, -12.0), np.full(n, 70.0)


def test_nearest_wrap():
    assert _nearest_wrap(-179.0, 179.0) == 181.0
    assert _nearest_wrap(179.0, -179.0) == -181.0
    assert _nearest_wrap(10.0, 12.0) == 10.0


def test_kalman_tracks_constant_velocity():
    kf = _BallKalman()
    kf.init(0.0, 0.0)
    # Ball moving 4 deg/s in lon; feed 10 updates at 2 Hz.
    for i in range(1, 11):
        kf.predict(0.5)
        kf.update(4.0 * 0.5 * i, 0.0)
    lon_pred, _ = kf.predict(0.5)
    # After convergence the 0.5 s prediction should be near 4*0.5*11 = 22 deg.
    assert abs(lon_pred - 22.0) < 1.5


def test_blend_identity_without_confirmed_ball():
    t, lons, lats, fovs = _aim()
    records = [{"t": float(x), "det": None, "pred": None, "confirmed": False}
               for x in np.arange(0.0, 20.0, 0.5)]
    l2, la2, f2, stats = blend_ball_aim(t, lons, lats, fovs, records)
    assert np.allclose(l2, lons) and np.allclose(f2, fovs)
    assert stats["confirmed_frac"] == 0.0


def test_blend_pulls_toward_confirmed_ball_and_clamps():
    cfg = BallAimConfig()
    t, lons, lats, fovs = _aim(n=100, lon=10.0)
    # Ball confirmed the whole window, 60 deg away — far beyond the clamp.
    records = [{"t": float(x), "det": [70.0, -12.0, 0.9],
                "pred": [70.0, -12.0], "confirmed": True}
               for x in np.arange(0.0, 20.0, 0.5)]
    l2, la2, f2, stats = blend_ball_aim(t, lons, lats, fovs, records, cfg)
    # Pulled toward the ball but never past clamp*blend_w.
    mid = l2[40:]                     # after the smooth ramp settles
    assert np.all(mid > lons[40:])
    assert np.max(l2) <= 10.0 + cfg.max_bias_deg * cfg.blend_w + 1e-6
    # FOV widened to keep the distant ball framed, capped at the ceiling.
    assert np.max(f2) > 70.0
    assert np.max(f2) <= cfg.fov_max_deg + 1e-6
    assert stats["mean_abs_bias_deg"] > 2.0


def test_blend_ramps_in_and_out():
    t, lons, lats, fovs = _aim(n=150, lon=0.0)
    records = []
    for x in np.arange(0.0, 30.0, 0.5):
        on = 10.0 <= x <= 20.0
        records.append({"t": float(x),
                        "det": [15.0, -12.0, 0.8] if on else None,
                        "pred": [15.0, -12.0] if on else None,
                        "confirmed": on})
    l2, _, _, _ = blend_ball_aim(t, lons, lats, fovs, records)
    # No bias well before the confirmed span; bias inside it; released after.
    assert abs(l2[10] - 0.0) < 0.5          # t=2 s
    assert l2[80] > 3.0                     # t=16 s, inside span
    assert abs(l2[-1] - 0.0) < 1.5          # t=29.8 s, released
