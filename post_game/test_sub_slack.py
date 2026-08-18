"""Unit tests for substitution-derived on-field window slack.

The property that matters: a boundary from a CLEAN single tap must stay tight
(so the off-window filter keeps catching genuine misattribution), while a
boundary from a MESSY multi-player rotation gets widened by exactly that
rotation's own spread.
"""

from __future__ import annotations

from post_game.sub_slack import (
    cluster_sub_times,
    relax_intervals,
    slack_for_time,
)

BASE = 20.0
GAP = 90.0
CAP = 150.0


def _slack(t, clusters):
    return slack_for_time(t, clusters, base_s=BASE, per_cluster_cap_s=CAP)


# --- clustering -------------------------------------------------------------

def test_taps_within_the_gap_are_one_moment():
    assert cluster_sub_times([100, 130, 160], GAP) == [[100, 130, 160]]


def test_taps_beyond_the_gap_are_separate_moments():
    assert cluster_sub_times([100, 130, 400], GAP) == [[100, 130], [400]]


def test_clustering_sorts_unordered_input():
    assert cluster_sub_times([400, 100, 130], GAP) == [[100, 130], [400]]


def test_no_taps_yields_no_clusters():
    assert cluster_sub_times([], GAP) == []


# --- slack ------------------------------------------------------------------

def test_clean_single_tap_gets_only_the_base():
    """A boundary the coach entered immediately keeps a tight window."""
    assert _slack(500.0, cluster_sub_times([500.0], GAP)) == BASE


def test_messy_rotation_gets_its_own_spread():
    """Five taps spread over 61s widen their boundaries by 61s."""
    taps = [420.0, 440.0, 460.0, 470.0, 481.0]     # spread 61
    assert _slack(420.0, cluster_sub_times(taps, GAP)) == BASE + 61.0


def test_spread_is_capped():
    taps = [0.0, 900.0]     # absurd spread, but one cluster only if gap allows
    clusters = [[0.0, 900.0]]
    assert _slack(0.0, clusters) == BASE + CAP


def test_boundary_far_from_any_tap_gets_only_the_base():
    """Kickoff and final-whistle boundaries aren't sub taps — no extra room."""
    taps = cluster_sub_times([420.0, 481.0], GAP)
    assert _slack(0.0, taps) == BASE
    assert _slack(3000.0, taps) == BASE


def test_no_taps_at_all_gives_the_base():
    assert _slack(100.0, []) == BASE


# --- interval relaxation ----------------------------------------------------

def test_relax_widens_both_edges():
    ivs = {"p1": [(400.0, 900.0)]}
    out = relax_intervals(ivs, [400.0, 900.0], enabled=True,
                          base_s=BASE, gap_s=GAP, cap_s=CAP, log_fn=lambda m: None)
    lo, hi = out["p1"][0]
    assert lo == 380.0 and hi == 920.0


def test_relax_never_pushes_a_start_below_zero():
    out = relax_intervals({"p1": [(5.0, 100.0)]}, [5.0], enabled=True,
                          base_s=BASE, gap_s=GAP, cap_s=CAP, log_fn=lambda m: None)
    assert out["p1"][0][0] == 0.0


def test_disabled_returns_the_input_unchanged():
    ivs = {"p1": [(400.0, 900.0)]}
    assert relax_intervals(ivs, [400.0], enabled=False) is ivs


def test_empty_intervals_are_returned_unchanged():
    assert relax_intervals({}, [1.0, 2.0], enabled=True) == {}


def test_does_not_mutate_the_input():
    ivs = {"p1": [(400.0, 900.0)]}
    relax_intervals(ivs, [400.0, 900.0], enabled=True, base_s=BASE,
                    gap_s=GAP, cap_s=CAP, log_fn=lambda m: None)
    assert ivs == {"p1": [(400.0, 900.0)]}


def test_messy_rotation_widens_more_than_a_clean_tap():
    """The whole point: slack scales with how sloppy that moment actually was."""
    messy = [420.0, 445.0, 470.0, 481.0]      # spread 61
    clean = [1290.0]
    ivs = {"messy": [(420.0, 1000.0)], "clean": [(1290.0, 1400.0)]}
    out = relax_intervals(ivs, messy + clean, enabled=True, base_s=BASE,
                          gap_s=GAP, cap_s=CAP, log_fn=lambda m: None)
    messy_lo = out["messy"][0][0]
    clean_lo = out["clean"][0][0]
    assert 420.0 - messy_lo == BASE + 61.0
    assert 1290.0 - clean_lo == BASE
    assert (420.0 - messy_lo) > (1290.0 - clean_lo)


def test_multiple_intervals_per_player_all_widen():
    ivs = {"p1": [(400.0, 500.0), (900.0, 1000.0)]}
    out = relax_intervals(ivs, [400.0, 500.0, 900.0, 1000.0], enabled=True,
                          base_s=BASE, gap_s=GAP, cap_s=CAP, log_fn=lambda m: None)
    assert len(out["p1"]) == 2
    assert all(hi - lo > 100.0 for lo, hi in out["p1"])
