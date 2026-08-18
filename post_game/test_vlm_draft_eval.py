"""Scoring VLM drafts against blind ground truth.

Run-to-run consistency (46% of span-matched tracklets change player) bounds
reliability but cannot say which read was right. Only a label made by eye,
blind to the pipeline, can — and the number the coach needs is precision BY
CONFIDENCE BAND, because the chip he taps shows 0.97 and he has no other way to
know what that is worth.
"""

from __future__ import annotations

from tracking.vlm_draft_eval import NON_PLAYER, score


def _d(tid, pid, conf, minutes=1.0):
    return {"trackletId": tid, "suggestedPlayerId": pid,
            "confidence": conf, "minutes": minutes}


def _gt(tid, true=None, label="", minutes=1.0):
    return {str(tid): {"true": true, "label": label, "minutes": minutes}}


def test_a_correct_draft_scores_correct():
    res = score([_d(1, "p_a", 0.95)], _gt(1, "p_a"))
    assert res["rows"][0][5] is True


def test_a_wrong_player_scores_incorrect():
    res = score([_d(1, "p_a", 0.95)], _gt(1, "p_b"))
    assert res["rows"][0][5] is False


def test_drafting_a_referee_is_wrong():
    """A draft on a non-player is a false positive, not an abstention."""
    res = score([_d(1, "p_a", 0.95)], _gt(1, None, "__referee__"))
    assert res["rows"][0][5] is False


def test_every_non_player_sentinel_counts_as_wrong():
    for lbl in NON_PLAYER:
        res = score([_d(1, "p_a", 0.9)], _gt(1, None, lbl))
        assert res["rows"][0][5] is False, lbl


def test_unlabelled_tracklets_are_skipped_not_counted_wrong():
    """No opinion is not evidence of failure."""
    res = score([_d(1, "p_a", 0.9), _d(2, "p_b", 0.9)], _gt(1, "p_a"))
    assert len(res["rows"]) == 1


def test_confidence_bands_partition_without_overlap():
    drafts = [_d(1, "p_a", 0.95), _d(2, "p_a", 0.85),
              _d(3, "p_a", 0.70), _d(4, "p_a", 0.30)]
    gt = {}
    for i in (1, 2, 3, 4):
        gt.update(_gt(i, "p_a"))
    res = score(drafts, gt)
    assert sum(len(v) for v in res["bands"].values()) == 4
    assert len(res["bands"][(0.9, 1.01)]) == 1
    assert len(res["bands"][(0.8, 0.9)]) == 1
    assert len(res["bands"][(0.6, 0.8)]) == 1
    assert len(res["bands"][(0.0, 0.6)]) == 1


def test_confidence_of_exactly_one_lands_in_the_top_band():
    res = score([_d(1, "p_a", 1.0)], _gt(1, "p_a"))
    assert len(res["bands"][(0.9, 1.01)]) == 1


def test_the_case_that_motivated_this():
    """Two runs, same tracklet, both 0.97, different children — at most one is
    right, and GT is the only way to learn which."""
    gt = _gt(1023, "p_hassoun")
    a = score([_d(1023, "p_yaacoub", 0.97)], gt)
    b = score([_d(1023, "p_hassoun", 0.97)], gt)
    assert a["rows"][0][5] is False and b["rows"][0][5] is True


def test_minutes_come_from_the_draft_then_the_label():
    res = score([_d(1, "p_a", 0.9, minutes=4.0)], _gt(1, "p_a", minutes=9.0))
    assert res["rows"][0][6] == 4.0
    res2 = score([{"trackletId": 2, "suggestedPlayerId": "p_a", "confidence": 0.9}],
                 _gt(2, "p_a", minutes=9.0))
    assert res2["rows"][0][6] == 9.0


def test_no_drafts_yields_no_rows():
    assert score([], _gt(1, "p_a"))["rows"] == []


def test_missing_confidence_is_treated_as_zero():
    res = score([{"trackletId": 1, "suggestedPlayerId": "p_a"}], _gt(1, "p_a"))
    assert len(res["bands"][(0.0, 0.6)]) == 1
