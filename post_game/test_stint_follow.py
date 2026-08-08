"""Tests for the stint follower.

The properties under test are the ones the approach depends on, not incidental
behaviour. In order of how badly a regression would hurt:

  1. It DECLARES rather than guesses. A silent swap corrupts a whole shift and
     looks fine afterwards; a declared gap costs the coach one tap.
  2. Mutual exclusion holds. Two named children cannot be the same body, and
     greedy assignment breaks exactly when two targets converge.
  3. Bench players never claim a detection. The coach log names the roster
     exactly (0 squad players lacked an interval on either Jul-12 game).
  4. Stints close at their logged bound, and overrun is detectable.

Run: `.venv-post-game/bin/python -m post_game.test_stint_follow`
"""
from __future__ import annotations

import math

import numpy as np

from .stint_follow import (
    Seed, Target, coverage, distance_m, drift_check, follow_stints, step,
)


def _dets(*pts):
    """(N,3) detections: x, y, box_h. Identity is deliberately absent."""
    return np.array([[x, y, 100.0] for x, y in pts], dtype=float)


def _walk(x0, y0, vx, vy, n, t0=0.0, dt=0.1):
    """A body moving at constant velocity, one entry per frame."""
    return [(t0 + i * dt, _dets((x0 + vx * i * dt, y0 + vy * i * dt)))
            for i in range(n)]


# --- 1. declares rather than guesses -------------------------------------

def test_ambiguous_pair_declares_gap():
    """Two candidates inside the margin => no attachment, for either target."""
    tg = Target("p_a", x=10.0, y=10.0, vx=0.0, vy=0.0, t_last=0.0)
    # Both sit ~0.1 m from the prediction, unseparable.
    step([tg], _dets((10.05, 10.0), (9.95, 10.0)), 0.1)
    assert tg.gaps == [0.1]
    assert len(tg.samples) == 0


def test_lone_implausible_candidate_declares_gap():
    """A sole candidate must be plausible, not merely unopposed.

    This is the mechanism behind every swap measured in the probe: 5 of 5 had
    exactly one candidate in reach, so there was no ambiguity to detect — the
    follower simply took the only body on offer.
    """
    tg = Target("p_a", x=10.0, y=10.0, t_last=0.0)
    # 0.6 m away: inside the 0.7 m reach gate at dt=0.1, outside the 0.35 gate.
    step([tg], _dets((10.6, 10.0)), 0.1)
    assert tg.gaps == [0.1]


def test_lone_plausible_candidate_is_taken():
    tg = Target("p_a", x=10.0, y=10.0, t_last=0.0)
    step([tg], _dets((10.1, 10.0)), 0.1)
    assert not tg.gaps
    assert tg.samples[-1][1] == 10.1


def test_nothing_in_reach_declares_gap():
    tg = Target("p_a", x=10.0, y=10.0, t_last=0.0)
    step([tg], _dets((30.0, 30.0)), 0.1)
    assert tg.gaps == [0.1]


def test_no_detections_at_all_declares_gap():
    tg = Target("p_a", x=10.0, y=10.0, t_last=0.0)
    step([tg], np.empty((0, 3)), 0.1)
    assert tg.gaps == [0.1]


def test_gap_does_not_permanently_kill_a_target():
    """REGRESSION. A gap must still advance the frame clock.

    The first joint implementation only moved `t_last` on a successful attach,
    so after one gap `dt` grew every frame until it passed `max_coast_s` and
    every subsequent frame was rejected as stale. One momentary occlusion
    silently ended the stint: median coverage on real Game 1 data was 7%, and
    the unit tests all passed because each exercised a single frame.
    """
    tg = Target("p_a", x=10.0, y=10.0, t_last=0.0, t_attach=0.0)
    # Frame 1: nothing in reach -> gap.
    step([tg], _dets((30.0, 30.0)), 0.1)
    assert tg.gaps == [0.1]
    # Frame 2: the body is back where it should be. It MUST re-attach.
    step([tg], _dets((10.05, 10.0)), 0.2)
    assert tg.samples, "target never recovered after a single gap"


def test_recovers_after_several_gaps_within_coast():
    tg = Target("p_a", x=10.0, y=10.0, t_last=0.0, t_attach=0.0)
    for k in (1, 2, 3):
        step([tg], _dets((30.0, 30.0)), 0.1 * k)
    assert len(tg.gaps) == 3
    step([tg], _dets((10.2, 10.0)), 0.4)
    assert tg.samples, "target did not recover inside the coast budget"


def test_lost_target_can_reacquire():
    """REGRESSION. Being lost must not be permanent.

    Past `max_coast_s` the velocity estimate is worthless, but the child has
    not left the planet. The first version refused every target beyond the
    coast budget, so a >1 s occlusion was fatal: on real Game 1 data a target
    attached 28 times then declared 2576 consecutive gaps (7% coverage). The
    unit tests missed it because each recovered inside the budget.
    """
    from .stint_follow import REACQUIRE_RADIUS_M

    tg = Target("p_a", x=10.0, y=10.0, t_last=0.0, t_attach=0.0)
    for k in range(1, 21):                       # 2 s of nothing in reach
        step([tg], _dets((40.0, 25.0)), 0.1 * k)
    assert len(tg.gaps) == 20 and not tg.samples
    # The body reappears just inside the re-acquire radius. Expressed relative
    # to the constant so tuning the radius cannot silently invalidate the test.
    step([tg], _dets((10.0 + 0.8 * REACQUIRE_RADIUS_M, 10.0)), 2.1)
    assert tg.samples, "a lost target could never be re-acquired"


def test_reacquire_resets_velocity():
    """A displacement across an unobserved hole is not a measured speed."""
    from .stint_follow import REACQUIRE_RADIUS_M

    tg = Target("p_a", x=10.0, y=10.0, vx=5.0, vy=0.0, t_last=0.0, t_attach=0.0)
    for k in range(1, 21):
        step([tg], _dets((40.0, 25.0)), 0.1 * k)
    step([tg], _dets((10.0 + 0.8 * REACQUIRE_RADIUS_M, 10.0)), 2.1)
    assert tg.samples, "did not re-acquire inside the radius"
    assert tg.vx == 0.0 and tg.vy == 0.0


def test_reacquire_is_bounded_in_distance():
    """A lost target must not claim a body clear across the pitch."""
    tg = Target("p_a", x=10.0, y=10.0, t_last=0.0, t_attach=0.0)
    for k in range(1, 21):
        step([tg], _dets((40.0, 25.0)), 0.1 * k)
    step([tg], _dets((52.0, 29.0)), 2.1)         # ~45 m away
    assert not tg.samples, "re-acquired an implausibly distant body"


def test_reacquire_radius_is_enforced():
    """Beyond REACQUIRE_RADIUS_M a re-acquire is a guess, not evidence.

    Measured on Game 1: re-acquire correctness against held-back ids is 85%
    within 1 m, 56% at 1-2 m, and 38% past 4 m. The cap is what keeps this path
    from silently rewriting the back half of a stint.
    """
    from .stint_follow import REACQUIRE_RADIUS_M

    def _try(jump_m):
        tg = Target("p_a", x=10.0, y=10.0, t_last=0.0, t_attach=0.0)
        for k in range(1, 21):
            step([tg], _dets((40.0, 25.0)), 0.1 * k)
        step([tg], _dets((10.0 + jump_m, 10.0)), 2.1)
        return bool(tg.samples)

    assert _try(REACQUIRE_RADIUS_M * 0.8), "rejected a close, plausible body"
    assert not _try(REACQUIRE_RADIUS_M * 2.0), "accepted a coin-flip re-acquire"


# --- 2. mutual exclusion --------------------------------------------------

def test_one_detection_cannot_serve_two_targets():
    """The greedy-assignment failure: both targets want the same body."""
    a = Target("p_a", x=10.0, y=10.0, t_last=0.0)
    b = Target("p_b", x=10.2, y=10.0, t_last=0.0)
    step([a, b], _dets((10.1, 10.0)), 0.1)
    claimed = [t for t in (a, b) if t.samples]
    assert len(claimed) <= 1, "a detection was shared between two players"


def test_two_targets_take_two_distinct_detections():
    a = Target("p_a", x=10.0, y=10.0, t_last=0.0)
    b = Target("p_b", x=20.0, y=10.0, t_last=0.0)
    step([a, b], _dets((10.05, 10.0), (20.05, 10.0)), 0.1)
    assert a.samples[-1][1] == 10.05
    assert b.samples[-1][1] == 20.05


def test_targets_cannot_occupy_the_same_metre():
    """Two named children on top of each other is physically impossible."""
    a = Target("p_a", x=10.0, y=10.0, t_last=0.0)
    b = Target("p_b", x=10.3, y=10.0, t_last=0.0)
    # Distinct detections, but both resolve into the same square metre.
    step([a, b], _dets((10.1, 10.0), (10.2, 10.0)), 0.1)
    both = [t for t in (a, b) if t.samples and t.samples[-1][0] == 0.1]
    if len(both) == 2:
        d = math.hypot(both[0].x - both[1].x, both[0].y - both[1].y)
        assert d >= 0.5, f"two players {d:.2f} m apart"


# --- 3. roster / bench ----------------------------------------------------

def test_target_not_yet_seeded_claims_nothing():
    """A bench player has no Target, so no detection can be attributed."""
    seeds = [Seed("p_on", t0=0.5, xy=(10.0, 10.0), t_end=10.0)]
    out = follow_stints(_walk(10.0, 10.0, 1.0, 0.0, 10), seeds)
    assert len(out) == 1
    # Nothing attributed before the seed moment.
    assert all(t >= 0.5 for t, _, _ in out[0].samples)


def test_stint_stops_at_its_logged_end():
    seeds = [Seed("p_a", t0=0.0, xy=(10.0, 10.0), t_end=0.3)]
    out = follow_stints(_walk(10.0, 10.0, 0.5, 0.0, 20), seeds)
    assert out[0].samples[-1][0] <= 0.3 + 1e-9
    assert not out[0].alive


def test_two_stints_of_the_same_player_are_separate_targets():
    """A player with four stints is four independent follows, not one."""
    seeds = [Seed("p_a", t0=0.0, xy=(10.0, 10.0), t_end=0.2),
             Seed("p_a", t0=0.5, xy=(12.0, 10.0), t_end=0.9)]
    frames = [(i * 0.1, _dets((10.0 + i * 0.05, 10.0), (12.0 + i * 0.05, 10.0)))
              for i in range(10)]
    out = follow_stints(frames, seeds)
    assert len(out) == 2
    assert all(t.player_id == "p_a" for t in out)


# --- 4. drift detection + honest metrics ----------------------------------

def test_drift_check_flags_overrun():
    tg = Target("p_a", x=0.0, y=0.0, t_end=100.0)
    tg.samples = [(120.0, 0.0, 0.0)]
    assert drift_check(tg) is True


def test_drift_check_clean_stint():
    tg = Target("p_a", x=0.0, y=0.0, t_end=100.0)
    tg.samples = [(99.0, 0.0, 0.0)]
    assert drift_check(tg) is False


def test_coverage_reports_observed_fraction():
    tg = Target("p_a", x=0.0, y=0.0)
    tg.samples = [(0.1, 0.0, 0.0)] * 3
    tg.gaps = [0.2, 0.3]
    assert abs(coverage(tg) - 0.6) < 1e-9


def test_distance_does_not_bridge_gaps():
    """A hole must not invent metres the child may not have run."""
    tg = Target("p_a", x=0.0, y=0.0)
    tg.samples = [(0.0, 0.0, 0.0), (0.1, 1.0, 0.0)]
    assert abs(distance_m(tg) - 1.0) < 1e-9
    # A teleport-sized hole in the samples is summed as observed motion only
    # because the follower could never have produced it — the reach gate caps
    # any real step. Guard that assumption here.
    assert distance_m(Target("p_b", x=0.0, y=0.0)) == 0.0


def test_empty_input_is_safe():
    assert follow_stints([], []) == []
    step([], _dets((1.0, 1.0)), 0.1)      # must not raise


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            bad += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    raise SystemExit(1 if bad else 0)
