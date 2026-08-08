"""A coach on the touchline is a whole track, not a stray detection.

The 1.5 m buffer is a per-detection test, so it cannot tell a coach standing
half a metre outside the line from a player taking a throw-in from the same
spot — both are "just outside". The distinguishing fact is only visible over a
whole track: the player crosses the line and comes back, the coach never does.

Measured on both July 12 games, fraction-of-life-outside is sharply bimodal (a
mass at 0.0-0.1, a spike at 0.9-1.0, a thin valley between), so requiring EVERY
sample to be outside is a safe cut rather than a tuned threshold.

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


def _apply(df, min_dets=10):
    """The stage-3b2 rule, mirrored (pipeline needs a video to run end-to-end)."""
    outside = ((df["x_m"] < 0) | (df["x_m"] > L)
               | (df["y_m"] < 0) | (df["y_m"] > W))
    per = df.assign(_o=outside).groupby("track_id")["_o"].agg(["mean", "size"])
    never = per.index[(per["mean"] >= 1.0) & (per["size"] >= min_dets)]
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


def test_one_frame_inside_is_enough_to_survive():
    """The rule is >=1.0, i.e. EVERY sample outside. Deliberately strict.

    The valley in the measured distribution is thin but real; anything less than
    total keeps the cut safe against a player who is briefly mis-projected.
    """
    pts = [(20.0, W + 0.5)] * 29 + [(20.0, W - 0.2)]
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
