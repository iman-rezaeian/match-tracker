"""Tag opponents, prune tracks — the replacement for the filter that ate our team.

`TRACK_DROP_OPPONENTS` deleted detections DURING tracking using the raw kit
hexes, while `fit_value_anchors` only derives the real threshold at stage 2b.
On a value-axis game that ordering removed ~a third of our own players: a black
`#0a0a0a` shirt photographs at V~145 in sun, the hex midpoint sits at 98, and
every one of our kids read as an opponent.

The replacement fixes three things, and each has a test here:

  ORDER         nothing is removed until the anchors are fitted
  GRANULARITY   whole tracks are judged, not single detections
  AUDITABILITY  the prune is reported, and defaults OFF until measured

Run: `.venv-post-game/bin/python -m post_game.test_tag_opponents`
"""
from __future__ import annotations

import inspect

import pandas as pd

from . import config, pipeline


def _prune(kit_votes, tracks_df,
           majority=None, min_votes=None):
    """The stage-2c rule, mirrored (pipeline needs a video to run end-to-end)."""
    majority = config.KIT_TAG_TRACK_MAJORITY if majority is None else majority
    min_votes = config.KIT_TAG_MIN_VOTES if min_votes is None else min_votes
    opp = set()
    for t, (o, p) in kit_votes.items():
        decisive = o + p
        if decisive < min_votes:
            continue
        if p / decisive >= majority:
            opp.add(t)
    return tracks_df[~tracks_df["track_id"].isin(opp)], opp


def _df(*tids):
    return pd.DataFrame({"track_id": [t for t in tids for _ in range(3)]})


# --- defaults -------------------------------------------------------------

def test_drop_filter_is_off_by_default():
    """The destructive path must not come back on by accident."""
    assert config.TRACK_DROP_OPPONENTS is False


def test_tag_filter_is_off_until_measured():
    assert config.TRACK_TAG_OPPONENTS is False
    assert config.KIT_TAG_TRACK_MAJORITY == 0.8
    assert config.KIT_TAG_MIN_VOTES == 10


# --- granularity ----------------------------------------------------------

def test_confident_opponent_track_is_pruned():
    kept, opp = _prune({1: (0, 20)}, _df(1, 2))
    assert opp == {1}
    assert set(kept.track_id) == {2}


def test_our_player_with_a_few_bad_frames_survives():
    """The exact failure of the drop version, at track level.

    18 frames read ours, 4 read opponent (shadow, turn, bad crop). A
    per-detection filter deletes those 4 outright; the track vote keeps all 22.
    """
    kept, opp = _prune({1: (18, 4)}, _df(1))
    assert opp == set()
    assert len(kept) == 3


def test_a_bare_majority_is_not_enough():
    """Asymmetric on purpose: a lost stint costs more than a spare candidate."""
    _, opp = _prune({1: (9, 11)}, _df(1))
    assert opp == set()


def test_too_few_votes_never_prunes():
    """Nine decisive frames is not a kit reading, however lopsided."""
    _, opp = _prune({1: (0, 9)}, _df(1))
    assert opp == set()


def test_abstains_do_not_count_as_evidence():
    """Only decisive votes are in the denominator.

    A track seen 200 times but readable 10 times, all opponent, IS pruned —
    abstains are absence of evidence, not evidence of ours.
    """
    _, opp = _prune({1: (0, 10)}, _df(1))
    assert opp == {1}


def test_nothing_is_pruned_when_no_track_meets_the_bar():
    kept, opp = _prune({1: (5, 5), 2: (20, 0)}, _df(1, 2))
    assert opp == set()
    assert len(kept) == 6


# --- ordering + wiring ----------------------------------------------------

def test_prune_runs_after_the_anchors_are_fitted():
    """The whole point: pruning must not precede fit_value_anchors.

    If this ever inverts, the prune is back on hex anchors and the original bug
    returns — silently, since both orderings produce a plausible-looking run.
    """
    src = inspect.getsource(pipeline)
    assert src.index("fit_value_anchors") < src.index("TRACK_TAG_OPPONENTS")


def test_new_keys_move_the_cache_fingerprint():
    """A knob that changes Stage-2 output but not the fingerprint silently
    reuses a stale cache. That gap has already bitten three times."""
    for key in ("TRACK_TAG_OPPONENTS", "KIT_TAG_TRACK_MAJORITY",
                "KIT_TAG_MIN_VOTES", "DROP_NEVER_OUTSIDE_FRAC",
                "DROP_NEVER_ONFIELD", "DROP_NEVER_MIN_DETS"):
        assert key in pipeline._TRACKING_CONFIG_KEYS, key

    before = pipeline._tracking_fingerprint()["config"]
    old = config.KIT_TAG_TRACK_MAJORITY
    try:
        config.KIT_TAG_TRACK_MAJORITY = 0.6
        after = pipeline._tracking_fingerprint()["config"]
    finally:
        config.KIT_TAG_TRACK_MAJORITY = old
    assert before != after


def test_prune_reports_what_it_removed():
    """Auditability: the count of removed tracks/detections must be logged.

    The drop version was only measurable because a pre-filter cache happened to
    survive on disk. A filter should not need luck to be reviewable.
    """
    src = inspect.getsource(pipeline)
    i = src.index("opponent track prune")
    assert "removed %d tracks" in src[i:i + 200]


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
