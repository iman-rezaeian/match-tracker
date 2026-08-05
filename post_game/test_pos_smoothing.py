"""Unit tests for edge-safe position smoothing (stats._smooth_edge_safe).

Distance was a raw sum of per-frame steps, and projection jitter never cancels —
it only adds path length. Measured on W8 over 7,121 windows where a player barely
moved (net 0.18 m over 2 s) the summed path was 1.27 m: a 7.2x over-count, i.e. a
STANDING player credited ~1.3 m of running every 2 seconds.

The obvious fix (reuse the existing `_smooth`) is WRONG and was caught here:
np.convolve(mode="same") ZERO-PADS, so a player standing at x=10 m smooths to 6 m
at the first sample. On a coordinate that is then differenced into distance that
invents metres at every run boundary — it made total distance RISE from 86 km to
249 km. `_smooth_edge_safe` edge-replicates instead.

Run: python -m post_game.test_pos_smoothing
"""
from __future__ import annotations

import numpy as np

from post_game.stats import _smooth, _smooth_edge_safe


def test_constant_signal_is_unchanged():
    # THE regression: the old smoother dragged the ends toward zero.
    x = np.full(20, 10.0)
    assert np.allclose(_smooth_edge_safe(x, 7), 10.0)


def test_old_smoother_really_does_distort_the_ends():
    # Documents WHY a separate function exists (guards against someone
    # "simplifying" this back to _smooth).
    x = np.full(10, 10.0)
    bad = _smooth(x, 5)
    assert bad[0] < 9.0, "if this fails, _smooth no longer zero-pads and the two can merge"


def test_length_is_preserved():
    for n in (3, 5, 10, 101):
        x = np.arange(n, dtype=float)
        assert len(_smooth_edge_safe(x, 7)) == n


def test_straight_line_motion_survives():
    # A player running in a straight line must keep (nearly) all their distance:
    # smoothing may not eat real displacement.
    x = np.arange(0, 20, 0.5)                 # constant velocity
    sm = _smooth_edge_safe(x, 7)
    raw_len = float(np.abs(np.diff(x)).sum())
    sm_len = float(np.abs(np.diff(sm)).sum())
    assert sm_len > 0.95 * raw_len, (sm_len, raw_len)


def test_jitter_around_a_fixed_point_is_suppressed():
    rng = np.random.default_rng(0)
    x = 10.0 + rng.normal(0, 0.25, 200)       # standing still + noise
    raw = float(np.abs(np.diff(x)).sum())
    sm = float(np.abs(np.diff(_smooth_edge_safe(x, 7))).sum())
    assert sm < 0.5 * raw, f"expected big suppression, got {sm:.2f} vs {raw:.2f}"


def test_even_window_is_reduced_to_odd():
    x = np.full(9, 4.0)
    assert np.allclose(_smooth_edge_safe(x, 6), 4.0)


def test_window_1_and_tiny_arrays_are_noops():
    x = np.array([1.0, 5.0, 2.0])
    assert np.allclose(_smooth_edge_safe(x, 1), x)
    assert np.allclose(_smooth_edge_safe(np.array([7.0, 8.0]), 7), [7.0, 8.0])


def test_window_longer_than_array_is_clamped():
    x = np.full(5, 3.0)
    assert np.allclose(_smooth_edge_safe(x, 99), 3.0)


def test_a_single_step_change_is_not_erased():
    # a genuine move from 0 to 10 must still show ~10 m of net displacement
    x = np.concatenate([np.zeros(20), np.full(20, 10.0)])
    sm = _smooth_edge_safe(x, 7)
    assert abs((sm[-1] - sm[0]) - 10.0) < 1e-6


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} position-smoothing tests passed.")
