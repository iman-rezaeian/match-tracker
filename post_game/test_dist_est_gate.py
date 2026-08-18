"""Unit tests for the distance-estimate coverage-fraction floor (stats.py).

A rate measured on a thin, activity-biased sliver of tracked time must NOT be
extrapolated to a whole game: the tracker preferentially holds a player while
they're MOVING, so a low-coverage slice skews fast and the projection reads as
fact in the UI. Below `config.DIST_EST_MIN_COVERAGE` we report the real tracked
distance and flag it instead.

Pure arithmetic over the guard's decision table — no video/Firestore/pandas.
Run: python -m post_game.test_dist_est_gate
"""
from __future__ import annotations

from post_game import config


def _dist_est(dist_raw, tracked_min, coach_min, sprint_count=10):
    """Mirror of the stats.py guard chain (3 guards, in order)."""
    coverage_frac = (tracked_min / coach_min) if coach_min > 0 else 0.0
    dist_est_capped = False
    if coverage_frac < config.DIST_EST_MIN_COVERAGE:
        return dist_raw, int(sprint_count), True
    if tracked_min >= 3.0 and coach_min > 0:
        mult = coach_min / tracked_min
        capped = min(mult, config.DIST_EST_MAX_MULT)
        dist_est_capped = capped < mult
        return dist_raw * capped, int(round(sprint_count * capped)), dist_est_capped
    return dist_raw, int(sprint_count), dist_est_capped


def test_thin_coverage_is_not_extrapolated():
    # GK-like: 20% coverage. Must report the RAW tracked distance, flagged.
    est, spr, capped = _dist_est(dist_raw=649.0, tracked_min=9.9, coach_min=49.1)
    assert est == 649.0, est          # no extrapolation at all
    assert spr == 10
    assert capped is True             # UI marks it indicative, not measured


def test_healthy_coverage_still_extrapolates():
    # 90% coverage: multiplier ~1.11, well under the 2.0 cap → scales normally.
    est, _, capped = _dist_est(dist_raw=2344.0, tracked_min=25.8, coach_min=28.8)
    assert est > 2344.0
    assert abs(est - 2344.0 * (28.8 / 25.8)) < 1e-6
    assert capped is False


def test_mid_coverage_binds_the_max_mult_cap():
    # ~39% coverage → naive mult 2.56 > cap 2.0, so the cap binds and flags.
    est, _, capped = _dist_est(dist_raw=870.0, tracked_min=11.2, coach_min=28.6)
    assert abs(est - 870.0 * config.DIST_EST_MAX_MULT) < 1e-6
    assert capped is True


def test_floor_boundary_is_inclusive_above():
    # exactly at the floor → NOT below it → normal (capped) extrapolation path
    cov = config.DIST_EST_MIN_COVERAGE
    coach = 40.0
    tracked = cov * coach
    est, _, _ = _dist_est(dist_raw=1000.0, tracked_min=tracked, coach_min=coach)
    assert est > 1000.0, "at the floor we should still extrapolate (only BELOW is blocked)"


def test_zero_coach_minutes_is_safe():
    est, spr, _ = _dist_est(dist_raw=500.0, tracked_min=5.0, coach_min=0.0)
    assert est == 500.0 and spr == 10        # no divide-by-zero, no projection


def test_floor_never_inflates():
    # The floor may only REDUCE a reported estimate vs the old cap-only behaviour.
    raw, tracked, coach = 649.0, 9.9, 49.1
    new, _, _ = _dist_est(raw, tracked, coach)
    old = raw * min(coach / tracked, config.DIST_EST_MAX_MULT)   # pre-floor behaviour
    assert new <= old


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} dist-est-gate tests passed.")
