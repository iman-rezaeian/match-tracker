"""Unit tests for the global (min-cost-flow) tracklet stitch mode.

The B2 lesson: a synthetic case with a few tidy, well-separated targets hides the
swarm failure. So the core test builds a fragment graph where the shipped GREEDY
stitcher makes a locally-cheap link that ORPHANS a fragment, and asserts the
global mode recovers it (fewer tracklets) — while proving global does NOT change
the answer on easy inputs and still honors the cannot-link gate.

Run: `python -m post_game.test_reid_stitch_global` (or pytest). No Firestore/video.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import reid_stitch as rs


def _frag(tid: int, t0: float, t1: float, x: float, y: float) -> pd.DataFrame:
    ts = np.arange(t0, t1 + 1e-9, 0.5)
    n = len(ts)
    return pd.DataFrame({
        "track_id": tid, "time_s": ts,
        "x_m": np.full(n, x), "y_m": np.full(n, y), "conf": 0.9,
    })


def _groups(mapping: dict[int, int]) -> set[frozenset]:
    from collections import defaultdict
    g = defaultdict(set)
    for k, v in mapping.items():
        g[v].add(k)
    return {frozenset(s) for s in g.values()}


def test_global_recovers_a_fragment_greedy_orphans():
    """The discriminating swarm case. A ends near both B and B'; A' ends near only
    B. Greedy (start-time order) links A→B (cheapest), which strands A' (B taken,
    B' out of A''s reach) → 3 tracklets. Global links A→B' + A'→B → 2 tracklets."""
    df = pd.concat([
        _frag(1, 0.0, 2.0, 10.0, 10.0),   # A
        _frag(2, 0.2, 2.0, 10.6, 10.0),   # A' (starts just after A)
        _frag(3, 2.5, 4.0, 10.2, 10.0),   # B  (reachable by A and A')
        _frag(4, 2.5, 4.0, 9.5, 10.0),    # B' (reachable by A only, given the cap)
    ], ignore_index=True)
    team = {1: 0, 2: 0, 3: 0, 4: 0}

    greedy = rs.stitch_tracklets(df, team, mode="greedy", geom_dist_cap_m=0.7)
    glob = rs.stitch_tracklets(df, team, mode="global", geom_dist_cap_m=0.7)

    g_greedy = rs.stitch_stats(greedy, team)["our_tracklets"]
    g_glob = rs.stitch_stats(glob, team)["our_tracklets"]
    assert g_glob < g_greedy, f"global ({g_glob}) should beat greedy ({g_greedy})"
    assert g_glob == 2, g_glob
    # global should have linked all four into two pairs (no orphan singletons)
    assert _groups(glob) == {frozenset({1, 4}), frozenset({2, 3})}, _groups(glob)


def test_global_same_as_greedy_on_easy_chain():
    """A clean A→B→C chain with no contention: both modes give one tracklet."""
    df = pd.concat([
        _frag(1, 0.0, 2.5, 10.0, 10.0),
        _frag(2, 3.0, 5.5, 10.5, 10.0),
        _frag(3, 6.0, 8.5, 11.0, 10.0),
    ], ignore_index=True)
    team = {1: 0, 2: 0, 3: 0}
    greedy = rs.stitch_tracklets(df, team, mode="greedy")
    glob = rs.stitch_tracklets(df, team, mode="global")
    assert _groups(greedy) == _groups(glob) == {frozenset({1, 2, 3})}


def test_global_honors_cannot_link():
    """Two fragments carrying DIFFERENT confirmed identities must never merge,
    however geometrically plausible — under global mode too."""
    df = pd.concat([
        _frag(1, 0.0, 2.0, 10.0, 10.0),
        _frag(2, 2.5, 4.0, 10.2, 10.0),   # geometrically an easy continuation of 1
    ], ignore_index=True)
    team = {1: 0, 2: 0}
    must_link = {1: "playerA", 2: "playerB"}  # different identities → cannot-link
    glob = rs.stitch_tracklets(df, team, mode="global", must_link=must_link)
    assert _groups(glob) == {frozenset({1}), frozenset({2})}, _groups(glob)


def test_global_does_not_merge_across_teams():
    """Only target_team (0) fragments are candidates; an opponent stays singleton."""
    df = pd.concat([
        _frag(1, 0.0, 2.0, 10.0, 10.0),
        _frag(2, 2.5, 4.0, 10.2, 10.0),
        _frag(9, 0.0, 4.0, 30.0, 20.0),   # opponent, far away
    ], ignore_index=True)
    team = {1: 0, 2: 0, 9: 1}
    glob = rs.stitch_tracklets(df, team, mode="global")
    assert glob[9] == 9  # opponent untouched
    assert glob[1] == glob[2]  # our two fragments chained


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} global-stitch tests passed.")
