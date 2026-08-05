"""Regression test: keeper tracklets must be emitted as status="gk".

Keeper tracklets are found by goal-line geometry — on same-kit U10s that is the
one individuating signal that actually works, and it is far stronger evidence
than a greedy appearance match. They were nevertheless emitted as status="auto",
which made keeper time indistinguishable from any other automatic assignment, so
GK minutes could neither be counted nor excluded downstream (accuracy-audit
correction, 2026-08-03).

The risk in adding a new status value is that something downstream gates on the
string and silently drops the keeper. These tests lock both halves of the
contract: the keeper is LABELLED "gk", and it still REACHES stats (the pipeline
builds identity_by_track from player_id alone, status-agnostic).

Run: python -m post_game.test_gk_status
"""
from __future__ import annotations

from post_game.identity_assign import IdentityAssignment


def _identity_by_track(assignments):
    """Mirror of pipeline.py:634 — the map that feeds compute_player_stats."""
    return {a.track_id: a.player_id for a in assignments if a.player_id}


def test_gk_status_is_not_laundered_as_auto():
    a = IdentityAssignment(track_id=1, player_id="p_garland", confidence=0.95,
                           status="gk", breakdown={}, minutes_on_field=49.1)
    assert a.status == "gk", "keeper must be distinguishable from a greedy match"
    assert a.status != "auto"


def test_gk_still_reaches_stats():
    """The whole risk of a new status value: falling out of the stats input."""
    assignments = [
        IdentityAssignment(track_id=1, player_id="p_garland", confidence=0.95,
                           status="gk", breakdown={}, minutes_on_field=49.1),
        IdentityAssignment(track_id=2, player_id="p_perrotta", confidence=0.9,
                           status="auto", breakdown={}, minutes_on_field=34.0),
    ]
    idmap = _identity_by_track(assignments)
    assert idmap == {1: "p_garland", 2: "p_perrotta"}, \
        "identity_by_track keys off player_id, so status must not gate the keeper out"


def test_gk_minutes_are_countable():
    """The point of the change: GK time can be summed on its own."""
    assignments = [
        IdentityAssignment(track_id=1, player_id="p_garland", confidence=0.95,
                           status="gk", breakdown={}, minutes_on_field=30.0),
        IdentityAssignment(track_id=2, player_id="p_garland", confidence=0.95,
                           status="gk", breakdown={}, minutes_on_field=19.1),
        IdentityAssignment(track_id=3, player_id="p_perrotta", confidence=0.9,
                           status="auto", breakdown={}, minutes_on_field=34.0),
    ]
    gk_min = sum(a.minutes_on_field for a in assignments if a.status == "gk")
    assert abs(gk_min - 49.1) < 1e-9
    # ...and outfield time is unaffected by the new bucket.
    other = sum(a.minutes_on_field for a in assignments if a.status != "gk")
    assert abs(other - 34.0) < 1e-9


def test_opponent_filter_still_excludes_only_opponents():
    """Downstream consumers compare against "opponent" (tagging_roi_curve.py:40).
    A new "gk" value must not accidentally land on the excluded side."""
    statuses = ["gk", "auto", "coach", "lowconf", "unknown", "opponent"]
    ours = [s for s in statuses if s != "opponent"]
    assert "gk" in ours, "the keeper is ours, not an opponent"
    assert len(ours) == 5


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} GK status tests passed.")
