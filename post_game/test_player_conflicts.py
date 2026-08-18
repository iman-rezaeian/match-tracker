"""Unit tests for post_game/player_conflicts — the one-player-two-places check.

Pure arithmetic; no I/O. Run: python -m post_game.test_player_conflicts
"""
from __future__ import annotations

from post_game.player_conflicts import (
    find_conflicts, conflict_summary, conflicted_times, blame_tracks,
)


def test_single_track_never_conflicts():
    s = [("p1", t / 10, 7, 10.0, 5.0) for t in range(50)]
    assert find_conflicts(s) == {}


def test_two_tracks_far_apart_at_same_instant_is_a_conflict():
    s = [("p1", 1.0, 7, 10.0, 5.0), ("p1", 1.0, 9, 30.0, 5.0)]
    c = find_conflicts(s)
    assert "p1" in c and len(c["p1"]) == 1
    t, sep, n = c["p1"][0]
    assert t == 1.0 and abs(sep - 20.0) < 1e-6 and n == 2


def test_two_boxes_on_the_same_body_are_not_a_conflict():
    # duplicate detections of ONE child sit well inside the threshold
    s = [("p1", 1.0, 7, 10.0, 5.0), ("p1", 1.0, 9, 10.6, 5.2)]
    assert find_conflicts(s) == {}


def test_threshold_boundary():
    s = [("p1", 1.0, 7, 0.0, 0.0), ("p1", 1.0, 9, 1.4, 0.0)]
    assert find_conflicts(s) == {}                     # under 1.5 m
    s2 = [("p1", 1.0, 7, 0.0, 0.0), ("p1", 1.0, 9, 1.6, 0.0)]
    assert "p1" in find_conflicts(s2)                  # over 1.5 m


def test_different_players_at_same_instant_are_independent():
    # two DIFFERENT players in different places is normal football
    s = [("p1", 1.0, 7, 10.0, 5.0), ("p2", 1.0, 9, 40.0, 5.0)]
    assert find_conflicts(s) == {}


def test_different_instants_are_not_a_conflict():
    # same player, far apart, but at different times = he ran there
    s = [("p1", 1.0, 7, 10.0, 5.0), ("p1", 9.0, 9, 40.0, 5.0)]
    assert find_conflicts(s) == {}


def test_nan_positions_are_ignored():
    nan = float("nan")
    s = [("p1", 1.0, 7, 10.0, 5.0), ("p1", 1.0, 9, nan, nan)]
    assert find_conflicts(s) == {}


def test_three_way_conflict_reports_widest_separation():
    s = [("p1", 1.0, 1, 0.0, 0.0), ("p1", 1.0, 2, 5.0, 0.0), ("p1", 1.0, 3, 25.0, 0.0)]
    c = find_conflicts(s)
    t, sep, n = c["p1"][0]
    assert n == 3 and abs(sep - 25.0) < 1e-6


def test_summary_counts_seconds():
    s = []
    for k in range(30):                       # 30 conflicting instants at 10 Hz = 3.0s
        s += [("p1", k / 10, 1, 0.0, 0.0), ("p1", k / 10, 2, 20.0, 0.0)]
    summ = conflict_summary(find_conflicts(s), dt_s=0.1)
    assert summ["p1"]["conflict_instants"] == 30
    assert abs(summ["p1"]["conflict_seconds"] - 3.0) < 1e-6
    assert abs(summ["p1"]["median_separation_m"] - 20.0) < 1e-6


def test_conflicted_times_are_exclusion_keys():
    s = [("p1", 1.0, 1, 0.0, 0.0), ("p1", 1.0, 2, 20.0, 0.0),
         ("p1", 2.0, 1, 0.0, 0.0)]
    bad = conflicted_times(find_conflicts(s))
    assert bad["p1"] == {1.0}
    assert 2.0 not in bad["p1"]               # the clean instant survives


def test_blame_tracks_ranks_the_worst_offender():
    s = []
    for k in range(20):                       # track 2 present at every conflict
        s += [("p1", k / 10, 1, 0.0, 0.0), ("p1", k / 10, 2, 20.0, 0.0)]
    s += [("p1", 99.0, 3, 0.0, 0.0), ("p1", 99.0, 4, 30.0, 0.0)]
    blame = blame_tracks(s, find_conflicts(s))
    top = blame["p1"][0]
    assert top[1] == 20 and top[0] in (1, 2)
    assert len(blame["p1"]) <= 10


def test_empty_input():
    assert find_conflicts([]) == {}
    assert conflict_summary({}) == {}
    assert conflicted_times({}) == {}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} player-conflict tests passed.")
