"""Tests for the per-source voiceDrafts merge (voice-notes design, Part 2).

The bugs these pin were a pair that together made a second capture source
destructive: draft ids `vd_{period}_{elapsed}_{type}` collided across sources
(a live note and a post-game narration of the same moment silently replaced
each other), and `write_voice_drafts` overwrote the whole array (narrating
post-game DELETED the live notes' drafts). The fix keys ids and the merge by
source: re-running one source's extraction replaces only that source's drafts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from post_game.firestore_io import merge_voice_drafts


def _d(id_, source=None, **kw):
    d = {"id": id_, "type": "BALL_WIN", "period": 1, "elapsed": 100, **kw}
    if source is not None:
        d["source"] = source
    return d


def test_post_run_preserves_live_drafts():
    """The headline bug: writing post-game drafts must not delete live notes."""
    existing = [_d("vd_live_1_100_BALL_WIN", "live")]
    out = merge_voice_drafts(existing, [_d("vd_post_1_400_KEY_PASS")], "post")
    assert existing[0] in out
    assert [d["id"] for d in out] == ["vd_live_1_100_BALL_WIN", "vd_post_1_400_KEY_PASS"]


def test_rerun_replaces_only_its_own_source():
    existing = [_d("vd_live_1_100_BALL_WIN", "live"),
                _d("vd_post_1_400_KEY_PASS", "post")]
    out = merge_voice_drafts(existing, [_d("vd_post_2_50_GOAL")], "post")
    assert [d["id"] for d in out] == ["vd_live_1_100_BALL_WIN", "vd_post_2_50_GOAL"]


def test_rerun_with_no_drafts_clears_only_that_source():
    existing = [_d("vd_live_1_100_BALL_WIN", "live"),
                _d("vd_post_1_400_KEY_PASS", "post")]
    out = merge_voice_drafts(existing, [], "live")
    assert [d["id"] for d in out] == ["vd_post_1_400_KEY_PASS"]


def test_legacy_drafts_count_as_live():
    """Pre-two-source drafts carry source='voice_draft' (or nothing) — the only
    capture path back then was the live recorder, so a live re-run replaces
    them and a post-game run leaves them alone."""
    legacy = [_d("vd_1_100_BALL_WIN", "voice_draft"), _d("vd_1_200_CLEAR")]
    assert merge_voice_drafts(legacy, [], "post") == legacy
    out = merge_voice_drafts(legacy, [_d("vd_live_1_100_BALL_WIN")], "live")
    assert [d["id"] for d in out] == ["vd_live_1_100_BALL_WIN"]


def test_written_drafts_are_stamped_with_source():
    """Every draft must carry source, even if a caller forgets to set it —
    otherwise it would read as legacy-live on the next merge."""
    out = merge_voice_drafts([], [_d("vd_post_1_400_KEY_PASS")], "post")
    assert out[0]["source"] == "post"


def test_rejects_unknown_source():
    with pytest.raises(ValueError):
        merge_voice_drafts([], [], "voice_draft")
