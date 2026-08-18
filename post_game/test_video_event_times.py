"""Exact per-event video times, for when no offset can be right.

The coach reported that the scorebug popup and the goal-roar audio were both
"inconsistent — sometimes before the goal, sometimes long after". Both fire at
`period_clock_to_video_time_factory(...)`, so they are always wrong together.

With Caboto's kickoff offset already corrected from a wrongly-stored 0.0 to 22.0,
the six goals were STILL out by -15.0, +4.0, +11.0, +2.1, -7.9 and -9.9 s. The error
changes sign, so there is no single offset that fixes it — only the measured instant
per event. A wider clip window (the earlier fix) makes the goal appear in the clip
but does nothing for when an overlay or a sound effect fires.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from post_game.identity import period_clock_to_video_time_factory

# elapsed -> (period, coach-read video second), measured off the source file.
CABOTO_GOALS = {
    (1, 148): 155.0, (1, 598): 624.0, (1, 1467): 1500.0,
    (2, 213): 1904.0, (2, 877): 2558.0, (2, 935): 2614.0,
}


def _game(**kw):
    base = dict(
        video_offset_h1_kickoff_s=22.0, video_offset_h2_kickoff_s=1688.94,
        half_length_min=25, pause_periods=[], started_at=0,
        video_event_times={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_an_exact_time_wins_over_the_offset_arithmetic():
    g = _game(video_event_times={"1:148": 155.0})
    assert period_clock_to_video_time_factory(g)(1, 148) == 155.0


def test_events_without_an_exact_time_are_unaffected():
    """Only the listed events move; everything else keeps the old mapping."""
    g = _game(video_event_times={"1:148": 155.0})
    f = period_clock_to_video_time_factory(g)
    assert f(1, 300) == 322.0            # 22.0 + 300, the fallback
    assert abs(f(2, 100) - (1688.94 + 100)) < 0.01


def test_every_caboto_goal_maps_to_the_measured_second():
    g = _game(video_event_times={f"{p}:{e}": v
                                 for (p, e), v in CABOTO_GOALS.items()})
    f = period_clock_to_video_time_factory(g)
    for (p, e), v in CABOTO_GOALS.items():
        assert abs(f(p, e) - v) < 0.01


def test_the_residual_error_changes_sign_so_no_offset_could_work():
    """The load-bearing fact. If this ever became one-signed, a plain offset would
    be the simpler fix and this whole table could go."""
    errs = []
    for (p, e), v in CABOTO_GOALS.items():
        mapped = (22.0 + e) if p == 1 else (1688.94 + e)
        errs.append(v - mapped)
    assert min(errs) < -5.0 and max(errs) > +5.0, (
        f"errors no longer straddle zero: {errs}")


def test_a_missing_table_is_the_old_behaviour_exactly():
    g = _game()
    assert period_clock_to_video_time_factory(g)(1, 148) == 170.0


def test_the_map_never_returns_negative():
    g = _game(video_event_times={"1:10": -5.0})
    assert period_clock_to_video_time_factory(g)(1, 10) == 0.0


def test_keys_are_period_scoped():
    """Same elapsed in both halves must not collide."""
    g = _game(video_event_times={"1:213": 240.0, "2:213": 1904.0})
    f = period_clock_to_video_time_factory(g)
    assert f(1, 213) == 240.0
    assert f(2, 213) == 1904.0


def test_the_goal_roar_and_the_scorebug_share_this_map():
    """Why one fix covers both symptoms: the roar times come from the same factory.

    If goal_video_times ever stopped using clock_to_video, the audio would drift
    away from the overlay again without any test noticing.
    """
    import inspect

    from post_game import public_audio
    src = inspect.getsource(public_audio.goal_video_times)
    assert "clock_to_video" in src


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
