"""Tests for measured clock->video anchors.

The bug: Caboto's `videoOffsetH1KickoffS` was 0.0 and marked CONFIRMED, because
pressing "Confirm 1st-half kickoff" with an empty box wrote 0.0 as a deliberate
choice. Every first-half highlight clip was then cut around a moment 7-33 s before
the goal it existed to show, and the scorebug credited each goal early.

The error GREW through the half (+11, +20, +35 s), so a single offset cannot repair
it — the phone clock and the camera clock disagree by ~1.8%. These tests pin the
two-parameter (offset + rate) fit and, importantly, that a game WITHOUT anchors is
completely unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from post_game.identity import (_fit_anchors, clock_video_anchors,
                                period_clock_to_video_time_factory)

# The coach's real readings off VID_20260712_Game2.mp4.
CABOTO = [(148.0, 155.0), (598.0, 624.0), (1467.0, 1500.0)]


def _game(**kw):
    base = dict(
        video_offset_h1_kickoff_s=0.0, video_offset_h2_kickoff_s=0.0,
        half_length_min=25, pause_periods=[], started_at=0,
        video_clock_anchors={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_game_with_no_anchors_is_untouched():
    """The fallback must stay bit-identical, or every existing game shifts."""
    g = _game(video_offset_h1_kickoff_s=40.9)
    f = period_clock_to_video_time_factory(g)
    assert f(1, 100) == 140.9


def test_two_anchors_recover_offset_and_rate():
    off, rate = _fit_anchors([(0.0, 10.0), (1000.0, 1030.0)])
    assert abs(off - 10.0) < 1e-6
    assert abs(rate - 1.02) < 1e-6


def test_one_anchor_pins_the_offset_and_assumes_unit_rate():
    """Better than nothing: a single anchor still beats 'video starts at kickoff'."""
    off, rate = _fit_anchors([(500.0, 530.0)])
    assert abs(off - 30.0) < 1e-6
    assert rate == 1.0


def test_the_caboto_fit_lands_every_goal_within_skim_tolerance():
    """The coach read these off a scrub bar, so ~7 s residuals are expected.

    What must hold is that all three land far closer than the 11-35 s error the
    single-offset map produced.
    """
    off, rate = _fit_anchors(CABOTO)
    for el, vid in CABOTO:
        assert abs(off + rate * el - vid) < 8.0


def test_the_caboto_fit_beats_the_stored_offset_on_every_goal():
    off, rate = _fit_anchors(CABOTO)
    for el, vid in CABOTO:
        anchored_err = abs(off + rate * el - vid)
        stored_err = abs(0.0 + el - vid)        # the shipped map
        assert anchored_err < stored_err


def test_anchors_drive_the_factory():
    g = _game(video_clock_anchors={
        "1": [{"elapsed_s": e, "video_s": v} for e, v in CABOTO]})
    f = period_clock_to_video_time_factory(g)
    # Goal 3 shifts from 1467 (stored map) to ~1502.
    assert 1494 < f(1, 1467) < 1510


def test_a_period_without_anchors_falls_back_even_when_another_has_them():
    """H1 anchored, H2 not: H2 must still use the wallclock-derived path."""
    g = _game(video_clock_anchors={"1": [{"elapsed_s": 148, "video_s": 155}]},
              video_offset_h2_kickoff_s=1688.94)
    f = period_clock_to_video_time_factory(g)
    assert abs(f(2, 213) - (1688.94 + 213)) < 0.01


def test_malformed_anchors_are_ignored_not_fatal():
    """A half-written doc must not break every clip in the game."""
    g = _game(video_clock_anchors={"1": [{"elapsed_s": "x"}, {"video_s": 5}],
                                   "bogus": [{"elapsed_s": 1, "video_s": 2}]},
              video_offset_h1_kickoff_s=40.9)
    assert clock_video_anchors(g) == {}
    assert period_clock_to_video_time_factory(g)(1, 100) == 140.9


def test_negative_video_times_are_rejected():
    g = _game(video_clock_anchors={"1": [{"elapsed_s": 10, "video_s": -5}]})
    assert clock_video_anchors(g) == {}


def test_the_map_never_returns_a_negative_video_time():
    g = _game(video_clock_anchors={"1": [{"elapsed_s": 600, "video_s": 10},
                                         {"elapsed_s": 1200, "video_s": 20}]})
    assert period_clock_to_video_time_factory(g)(1, 0) >= 0.0


def test_duplicate_clock_times_do_not_explode():
    """Two readings for the same elapsed: degenerate slope, must not divide by 0."""
    off, rate = _fit_anchors([(100.0, 110.0), (100.0, 112.0)])
    assert rate == 1.0
    assert off == 11.0


if __name__ == "__main__":
    import traceback
    bad = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            try:
                v()
                print(f"ok   {k}")
            except Exception:
                bad += 1
                print(f"FAIL {k}")
                traceback.print_exc()
    raise SystemExit(1 if bad else 0)
