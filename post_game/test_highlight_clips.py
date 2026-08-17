"""Highlight clips: enough build-up, and a scorebug minute that tells the truth.

Two defects the coach hit on the 3-5 Belle River game:

1. "the opponent first goal was captured only a 2-3 seconds before they score"
   The clip window was symmetric +-15 s around the TAP. But a tap marks the coach's
   REACTION -- after the ball crosses the line -- and the interesting part of a
   goal is the move that created it, which starts 20-30 s earlier. A symmetric
   window is the wrong SHAPE, not merely too small.

2. "the minute indicator in score bug is not sync ... just shows regular minutes"
   Only events inside a rendered window carry a reel timestamp. Measured on the
   games that have highlight reels: 18 of 121, 21 of 106, and 8 of 112 events have
   an `autoHighlightsTimeS`. Reading the clock from "the last event before the
   playhead" therefore showed a minute from a different part of the match.
   Segments now carry their own game-clock position, which covers the whole reel.
"""

from __future__ import annotations

import re
from pathlib import Path

from post_game.tv_view import (AUTO_HIGHLIGHT_POST_S, AUTO_HIGHLIGHT_PRE_S,
                               _event_windows)

REPO = Path(__file__).resolve().parent.parent
JSX = REPO / "soccer_team_app.jsx"


class _Ev:
    def __init__(self, period, elapsed, type_):
        self.period, self.elapsed, self.type = period, elapsed, type_


# The real map for the 3-5 Belle River game: H1 kickoff 40.92 s into the video.
def _clock_to_video(period, elapsed):
    return (40.92 if period == 1 else 1753.08) + float(elapsed)


def test_a_goal_keeps_its_celebration_and_a_shot_does_not():
    """Tails are per-type, sized to the football rather than to timing slop.

    History worth keeping: this assertion has been all three ways round. First
    `PRE > POST`, on the theory that a tap trails the action. Then `POST >= 30` as
    deliberate SLOP, after Caboto's kickoff offset shipped as 0.0 and put goals up to
    33 s later in the video than the map claimed, so a 10 s tail ended the clip before
    the ball crossed. Exact per-event video times removed that uncertainty, so the
    tail no longer has to hide it — and a 40 s tail on a saved shot is a minute of
    dead footage per clip.
    """
    from post_game.tv_view import (AUTO_HIGHLIGHT_LONG_TAIL_TYPES,
                                   AUTO_HIGHLIGHT_POST_OTHER_S)
    assert AUTO_HIGHLIGHT_POST_S > AUTO_HIGHLIGHT_POST_OTHER_S
    assert "GOAL" in AUTO_HIGHLIGHT_LONG_TAIL_TYPES
    assert "SHOT_ON" not in AUTO_HIGHLIGHT_LONG_TAIL_TYPES


def test_the_per_type_tail_is_applied_end_to_end():
    from post_game.tv_view import AUTO_HIGHLIGHT_POST_OTHER_S
    (ga, gb), = _event_windows([_Ev(1, 600, "GOAL")], _clock_to_video,
                               10_000.0, AUTO_HIGHLIGHT_PRE_S)
    (sa, sb), = _event_windows([_Ev(1, 600, "SHOT_ON")], _clock_to_video,
                               10_000.0, AUTO_HIGHLIGHT_PRE_S)
    t = _clock_to_video(1, 600)
    assert abs((gb - t) - AUTO_HIGHLIGHT_POST_S) < 0.01
    assert abs((sb - t) - AUTO_HIGHLIGHT_POST_OTHER_S) < 0.01


def test_the_lead_is_long_enough_for_the_build_up():
    """15 s minus reaction time gave 2-3 s of actual build-up."""
    assert AUTO_HIGHLIGHT_PRE_S >= 20.0


def test_the_opponents_first_goal_gets_real_build_up():
    """The exact event the coach complained about: P1 300s OPP_GOAL."""
    (a, b), = _event_windows([_Ev(1, 300, "OPP_GOAL")], _clock_to_video,
                             10_000.0, AUTO_HIGHLIGHT_PRE_S)
    t = _clock_to_video(1, 300)
    assert t - a >= 20.0, f"only {t - a:.0f}s of lead-up"
    assert b - t >= 5.0, "no tail for the restart"


def test_the_window_keeps_the_build_up_and_the_aftermath():
    """The move that created the goal, plus enough after it to see it go in."""
    (a, b), = _event_windows([_Ev(1, 600, "GOAL")], _clock_to_video,
                             10_000.0, AUTO_HIGHLIGHT_PRE_S)
    t = _clock_to_video(1, 600)
    assert (t - a) >= 20.0, "lost the build-up"
    assert (b - t) >= 8.0, "no room for the ball to cross and the celebration"


def test_post_roll_is_overridable():
    (a, b), = _event_windows([_Ev(1, 600, "GOAL")], _clock_to_video,
                             10_000.0, 30.0, 2.0)
    t = _clock_to_video(1, 600)
    assert abs((t - a) - 30.0) < 0.01
    assert abs((b - t) - 2.0) < 0.01


def test_overlapping_windows_still_merge():
    """Two shots 5 s apart must produce ONE clip, not two overlapping ones."""
    got = _event_windows([_Ev(1, 600, "GOAL"), _Ev(1, 605, "SHOT_ON")],
                         _clock_to_video, 10_000.0, AUTO_HIGHLIGHT_PRE_S)
    assert len(got) == 1


def test_a_window_is_clamped_to_the_video():
    """An event near kickoff must not produce a negative start."""
    (a, _), = _event_windows([_Ev(1, 0, "GOAL")], _clock_to_video,
                             10_000.0, AUTO_HIGHLIGHT_PRE_S)
    assert a >= 0.0


# --------------------------------------------------------------------------
# The video-time -> game-clock inverse, which labels each clip
# --------------------------------------------------------------------------

def _game():
    from post_game.firestore_io import GameDoc  # noqa: F401  (shape only)
    class G:
        video_offset_h1_kickoff_s = 40.92
        video_offset_h2_kickoff_s = 1753.08
        half_length_min = 25
        pause_periods: list = []
        started_at = None
    return G()


def test_video_time_round_trips_to_the_game_clock():
    from post_game.identity import (period_clock_to_video_time_factory,
                                    video_time_to_period_clock_factory)
    g = _game()
    fwd = period_clock_to_video_time_factory(g)
    inv = video_time_to_period_clock_factory(g)
    for period, elapsed in [(1, 0), (1, 300), (1, 1275), (2, 0), (2, 662), (2, 1447)]:
        p, c = inv(fwd(period, elapsed))
        assert p == period, f"P{period} {elapsed}s came back as P{p}"
        assert abs(c - elapsed) < 0.01


def test_the_second_half_wins_at_its_kickoff_instant():
    """At exactly the H2 kickoff the clock reads 2nd half 0:00, not 1st half 25'."""
    from post_game.identity import video_time_to_period_clock_factory
    p, c = video_time_to_period_clock_factory(_game())(1753.08)
    assert p == 2 and abs(c) < 0.01


# --------------------------------------------------------------------------
# The PWA side
# --------------------------------------------------------------------------

def test_the_player_prefers_the_segment_index_over_latching_on_events():
    src = JSX.read_text()
    i = src.index("function BroadcastVideoPlayer(")
    body = src[i:i + 6000]
    assert "reel_start_s" in body, "the player ignores the segment clock index"
    assert "clock_s" in body
    # And it must interpolate, not freeze on the anchor event.
    assert re.search(r"Math\.max\(0, now - (seg\.reel_start_s|anchor\.t)\)", body)


def test_the_pipeline_emits_the_segment_clock_index():
    src = (REPO / "post_game" / "pipeline.py").read_text()
    assert "video_time_to_period_clock_factory" in src
    assert '"reel_start_s"' in src


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
