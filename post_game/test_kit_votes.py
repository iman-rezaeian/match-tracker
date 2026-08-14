"""Team assignment from per-detection kit-hue votes.

The property that matters: a track only gets a team when the evidence is both
sufficient and one-sided. An ambiguous track must come back UNKNOWN rather than
be guessed onto a side — a wrong team is worse than no team, because it puts an
opponent into our roster's candidate pool for the whole game.
"""

from __future__ import annotations

import pandas as pd
import pytest

from post_game.team_classifier import classify_from_kit_votes


def _df(*track_ids):
    return pd.DataFrame({"track_id": list(track_ids)})


def test_clear_majority_ours():
    out = classify_from_kit_votes(_df(1), {1: (20, 1)})
    assert out[1] == 0


def test_clear_majority_opp():
    out = classify_from_kit_votes(_df(1), {1: (1, 20)})
    assert out[1] == 1


def test_split_vote_is_unknown_not_a_guess():
    out = classify_from_kit_votes(_df(1), {1: (10, 9)})
    assert out[1] == -1


def test_too_few_votes_is_unknown():
    """Two decisive detections is not enough to commit a whole track."""
    out = classify_from_kit_votes(_df(1), {1: (2, 0)}, min_votes=3)
    assert out[1] == -1


def test_no_votes_at_all_is_unknown():
    out = classify_from_kit_votes(_df(1), {})
    assert out[1] == -1


def test_exactly_at_the_margin_commits():
    out = classify_from_kit_votes(_df(1), {1: (6, 4)}, min_votes=3, min_margin=0.60)
    assert out[1] == 0


def test_just_under_the_margin_abstains():
    out = classify_from_kit_votes(_df(1), {1: (59, 41)}, min_votes=3, min_margin=0.60)
    assert out[1] == -1


def test_every_track_in_the_frame_gets_a_verdict():
    out = classify_from_kit_votes(_df(1, 2, 3), {1: (9, 0), 2: (0, 9)})
    assert set(out) == {1, 2, 3}
    assert out[1] == 0 and out[2] == 1 and out[3] == -1


@pytest.mark.parametrize("per_side", [7, 9])
def test_a_realistic_frame_splits_about_evenly(per_side):
    """The whole point: ~even sides, not 14 v 2.

    Parameterised over both match formats — 7v7 for Canadian festivals and 9v9
    for US tournaments. Both teams always field the same count, so the ~1:1
    expectation is format-independent; only the absolute number changes.
    """
    n = per_side * 2
    votes = {i: (12, 1) for i in range(per_side)}       # ours
    votes.update({i: (1, 12) for i in range(per_side, n)})  # theirs
    out = classify_from_kit_votes(_df(*range(n)), votes)
    ours = sum(1 for v in out.values() if v == 0)
    opp = sum(1 for v in out.values() if v == 1)
    assert ours == per_side and opp == per_side


def test_votes_for_absent_tracks_are_ignored():
    out = classify_from_kit_votes(_df(1), {1: (9, 0), 99: (9, 0)})
    assert set(out) == {1}
