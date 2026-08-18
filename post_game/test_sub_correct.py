"""Unit tests for post_game/sub_correct — pure on-field-window correction from
accepted tracklet spans. No Firestore / video / pandas.

Run: python -m post_game.test_sub_correct  (or pytest)
"""
from __future__ import annotations

from post_game.sub_correct import (
    compute_sub_corrections, apply_corrections_to_intervals,
    video_time_to_period_clock_factory, clock_or_none_factory,
)

# Two halves, video seconds: H1 [0,1500], H2 [1600,3100].
HW = [(0.0, 1500.0), (1600.0, 3100.0)]


# ---- compute_sub_corrections --------------------------------------------
def test_union_of_accepted_tracklets():
    # player accepted 3 fragments; window = [earliest start, latest end]
    acc = {"p1": [(300, 500), (520, 900), (100, 250)]}
    logged = {"p1": [(240, 940)]}  # logged close-ish so shifts pass the guard
    c = compute_sub_corrections(acc, HW, logged)
    assert c["p1"]["onS"] == 100.0 and c["p1"]["offS"] == 900.0
    assert c["p1"]["loggedOnS"] == 240.0 and c["p1"]["loggedOffS"] == 940.0


def test_no_accepts_returns_empty():
    assert compute_sub_corrections({}, HW, {}) == {}


def test_clamp_span_crossing_halftime_to_one_half():
    # a chimera spanning halftime (1400..1700); midpoint 1550 → nearest half.
    # after clamping it should sit within a single half, not span the gap.
    acc = {"p1": [(1400, 1700)]}
    logged = {"p1": [(1400, 1700)]}
    c = compute_sub_corrections(acc, HW, logged)
    if "p1" in c:
        on, off = c["p1"]["onS"], c["p1"]["offS"]
        # both edges (if present) must lie within ONE half window
        in_h1 = (on is None or on >= 0) and (off is None or off <= 1500)
        in_h2 = (on is None or on >= 1600) and (off is None or off <= 3100)
        assert in_h1 or in_h2, (on, off)


def test_implausible_shift_edge_skipped():
    # accepted span says on at 100, but logged on at 1200 → 1100s shift >> 300s
    # cap → the ON edge must be declined (None); OFF (small shift) accepted.
    acc = {"p1": [(100, 1000)]}
    logged = {"p1": [(1200, 1050)]}   # logged_on=1200, logged_off=1050 (degenerate but tests edges)
    c = compute_sub_corrections(acc, HW, logged, max_shift_s=300.0)
    # on: |100-1200|=1100 > 300 → None ; off: |1000-1050|=50 <= 300 → 1000
    assert c["p1"]["onS"] is None
    assert c["p1"]["offS"] == 1000.0


def test_chimera_covering_whole_half_skipped():
    # span covers ~100% of H1 → not one stint → declined entirely (omitted).
    acc = {"p1": [(5, 1495)]}
    logged = {"p1": [(5, 1495)]}
    c = compute_sub_corrections(acc, HW, logged)
    assert "p1" not in c


def test_no_logged_interval_accepts_both_edges():
    # coach logged nothing for p1 → no baseline to be biased against → trust span.
    acc = {"p1": [(400, 800)]}
    c = compute_sub_corrections(acc, HW, {})
    assert c["p1"]["onS"] == 400.0 and c["p1"]["offS"] == 800.0
    assert c["p1"]["loggedOnS"] is None


# ---- apply_corrections_to_intervals -------------------------------------
def test_apply_replaces_first_start_and_last_end():
    intervals = {"p1": [(240.0, 600.0), (700.0, 940.0)]}
    corr = {"p1": {"onS": 100.0, "offS": 900.0}}
    out = apply_corrections_to_intervals(intervals, corr)
    assert out["p1"][0] == (100.0, 600.0)     # first interval's START moved
    assert out["p1"][-1] == (700.0, 900.0)    # last interval's END moved


def test_apply_one_edge_only_leaves_other():
    intervals = {"p1": [(240.0, 940.0)]}
    out = apply_corrections_to_intervals(intervals, {"p1": {"onS": 100.0, "offS": None}})
    assert out["p1"] == [(100.0, 940.0)]


def test_apply_synthesizes_when_no_logged_interval():
    out = apply_corrections_to_intervals({}, {"p1": {"onS": 400.0, "offS": 800.0}})
    assert out["p1"] == [(400.0, 800.0)]


def test_apply_empty_corrections_is_noop():
    intervals = {"p1": [(1.0, 2.0)]}
    assert apply_corrections_to_intervals(intervals, {}) == intervals


# ---- video_time_to_period_clock -----------------------------------------
def test_video_to_clock_inverse():
    # forward: P1 video = 0 + elapsed ; P2 video = 1600 + elapsed
    fwd = lambda p, e: (0.0 + e) if p == 1 else (1600.0 + e)
    inv = video_time_to_period_clock_factory(HW, fwd)
    assert inv(300.0) == (1, 300.0)          # H1
    p, e = inv(1750.0)                        # H2: 1750-1600 = 150
    assert p == 2 and abs(e - 150.0) < 1e-6
    # round-trip a few points
    for (p, e) in [(1, 0.0), (1, 900.0), (2, 0.0), (2, 1200.0)]:
        pp, ee = inv(fwd(p, e))
        assert pp == p and abs(ee - e) < 1e-6


# ---- clock_or_none (echo, with played-to-end sentinel) ------------------
def test_clock_or_none_regular_edge():
    fwd = lambda p, e: (0.0 + e) if p == 1 else (1600.0 + e)
    clk = clock_or_none_factory(HW, fwd)
    assert clk(300.0) == {"period": 1, "elapsed": 300.0}
    assert clk(1750.0) == {"period": 2, "elapsed": 150.0}


def test_clock_or_none_none_in_none_out():
    fwd = lambda p, e: (0.0 + e) if p == 1 else (1600.0 + e)
    assert clock_or_none_factory(HW, fwd)(None) is None


def test_clock_or_none_played_to_end_sentinel():
    # the 1e9 "never subbed off" marker (and anything past the last half's end)
    # must echo as None, NOT a 999998311s garbage clock.
    fwd = lambda p, e: (0.0 + e) if p == 1 else (1600.0 + e)
    clk = clock_or_none_factory(HW, fwd)
    assert clk(1e9) is None
    assert clk(3100.0 + 61.0) is None      # just past end+pad → sentinel
    assert clk(3100.0) is not None         # exactly at final whistle → real


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} sub_correct tests passed.")
