"""A coach on the touchline is a whole track, not a stray detection.

The 1.5 m buffer is a per-detection test, so it cannot tell a coach standing
half a metre outside the line from a player taking a throw-in from the same
spot — both are "just outside". The distinguishing fact is only visible over a
whole track: the player crosses the line and comes back, the coach never does.

The threshold is TUNED, not safe-by-construction. An earlier version of this
docstring claimed the fraction-of-life-outside distribution is "sharply bimodal
with a thin valley", making `>= 1.0` self-evidently safe. Measurement on both
games refuted that: the middle band holds 591 (g1) / 482 (g2) substantial
tracks, 16% / 13% of the population. At 1.0 a single frame of projection noise
saved a track and ~50k touchline detections per game escaped, so the cut now
sits at DROP_NEVER_OUTSIDE_FRAC = 0.95.

Run: `.venv-post-game/bin/python -m post_game.test_never_onfield`
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

L, W = 54.0, 30.0


def _track(tid, pts, t0=0.0):
    return pd.DataFrame({
        "track_id": tid,
        "time_s": [t0 + 0.1 * i for i in range(len(pts))],
        "x_m": [p[0] for p in pts],
        "y_m": [p[1] for p in pts],
    })


def _apply(df, min_dets=10, frac=None):
    """The stage-3b2 rule, mirrored (pipeline needs a video to run end-to-end).

    `frac` defaults to the CONFIG value rather than a literal, so a change to
    DROP_NEVER_OUTSIDE_FRAC is exercised here instead of passing regardless.
    """
    if frac is None:
        frac = config.DROP_NEVER_OUTSIDE_FRAC
    outside = ((df["x_m"] < 0) | (df["x_m"] > L)
               | (df["y_m"] < 0) | (df["y_m"] > W))
    per = df.assign(_o=outside).groupby("track_id")["_o"].agg(["mean", "size"])
    never = per.index[(per["mean"] >= frac) & (per["size"] >= min_dets)]
    return df[~df["track_id"].isin(never)], set(never)


def test_a_coach_pacing_outside_the_line_is_dropped():
    coach = _track(1, [(20 + 0.1 * i, W + 0.5) for i in range(30)])
    _, dropped = _apply(coach)
    assert dropped == {1}


def test_a_throw_in_is_kept():
    """Steps outside, throws, comes back — the case a tighter buffer would cut."""
    pts = ([(30.0, W + 0.4)] * 12) + [(30.0, W - 1.0 - i) for i in range(12)]
    _, dropped = _apply(_track(2, pts))
    assert not dropped, "a player who returns to the pitch must never be dropped"


def test_a_keeper_behind_the_goal_line_is_kept():
    pts = ([(-0.6, 15.0)] * 15) + [(1.0 + i, 15.0) for i in range(10)]
    _, dropped = _apply(_track(3, pts))
    assert not dropped


def test_a_player_who_spends_real_time_inside_survives():
    """Replaces `test_one_frame_inside_is_enough_to_survive` (2026-08-08).

    That test asserted a single inside frame out of 30 should save a track,
    justified by a "thin but real" valley in the distribution. Measurement
    refuted the valley (591/482 substantial tracks sit in the middle), and the
    behaviour it protected was the leak itself: ~50k touchline detections per
    game survived on one frame of projection noise.

    What it was TRYING to protect is a real player briefly mis-projected — so
    that is what is asserted now. Two of 30 frames inside (0.933) is below the
    0.95 bar and kept; a lone blip is not.
    """
    pts = [(20.0, W + 0.5)] * 28 + [(20.0, W - 0.2)] * 2
    _, dropped = _apply(_track(4, pts))
    assert not dropped


def test_a_brief_blip_outside_is_not_treated_as_a_coach():
    """Below min_dets the rule abstains — noise, not a person on the line."""
    _, dropped = _apply(_track(5, [(20.0, W + 0.5)] * 4))
    assert not dropped


def test_players_and_coaches_together():
    df = pd.concat([
        _track(1, [(20.0, W + 0.5)] * 30),                       # coach, near side
        _track(2, [(20.0, -0.5)] * 30),                          # spectator, far side
        _track(3, [(25.0, 15.0 + 0.1 * i) for i in range(30)]),  # player, midfield
        _track(4, ([(30.0, W + 0.4)] * 12
                   + [(30.0, W - 2.0)] * 12)),                   # throw-in
    ])
    kept, dropped = _apply(df)
    assert dropped == {1, 2}, f"expected both touchline adults, got {dropped}"
    assert set(kept.track_id) == {3, 4}


def test_far_sideline_is_caught_too():
    """NOT a near-side-only rule. The far sideline holds MORE never-enter tracks
    in both games (245 vs 112, 188 vs 111); near-side coaches are merely larger
    on camera and so more noticeable."""
    _, dropped = _apply(_track(9, [(20.0, -0.8)] * 30))
    assert dropped == {9}


def test_default_is_on_and_overridable():
    assert config.DROP_NEVER_ONFIELD is True
    assert config.DROP_NEVER_MIN_DETS == 10
    assert config.DROP_NEVER_OUTSIDE_FRAC == 0.95


def test_one_frame_inside_no_longer_saves_a_coach():
    """The leak the 1.0 threshold had: 1 frame of noise kept a whole track.

    A coach with 39 samples outside and a single projection blip inside scores
    0.975 — under the old `>= 1.0` rule that track survived, and ~50k such
    detections per game reached identity. At 0.95 it is cut.
    """
    pts = [(-2.0, 15.0)] * 39 + [(1.0, 15.0)]
    _, dropped = _apply(_track(9, pts))
    assert 9 in dropped
    # ...and the old threshold demonstrably did NOT cut it
    _, old = _apply(_track(9, pts), frac=1.0)
    assert 9 not in old


def test_substitute_who_comes_on_is_kept():
    """The population a naive tightening would eat.

    A sub warms up on the touchline then plays. Most of the track is outside,
    but once they step on the fraction falls below the bar — kept by
    construction, which is why 0.95 is safe and 0.75 is not.
    """
    pts = [(-2.0, 16.0)] * 30 + [(20.0, 15.0)] * 10   # 0.75 outside
    kept, dropped = _apply(_track(11, pts))
    assert 11 not in dropped
    assert not kept.empty


def test_threshold_is_not_hardcoded_in_the_pipeline():
    """Guards the wiring: the rule must read config, not a literal 1.0."""
    import inspect
    from . import pipeline
    src = inspect.getsource(pipeline)
    assert "config.DROP_NEVER_OUTSIDE_FRAC" in src
    assert '_per["mean"] >= 1.0' not in src


def test_empty_input_is_safe():
    df = pd.DataFrame({"track_id": [], "time_s": [], "x_m": [], "y_m": []})
    kept, dropped = _apply(df)
    assert kept.empty and not dropped


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
