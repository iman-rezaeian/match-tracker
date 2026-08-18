"""The absolute distance cap on a stitch join.

The speed term alone (`MAX_PLAUSIBLE_SPEED_MS * gap + slack`) assumes a child
sprints dead-straight for the entire unobserved gap, so at the 10 s maximum it
sanctions a 93 m move on a 55x30 m pitch. Measured on mri01pvelv46d, the median
in-tracklet join already spans 5.4 m, the p90 spans 24 m, and the longest is
64 m — further than the pitch is long. Those are how one tracklet ends up
holding two different children.

`STITCH_DIST_CAP_M` bounds the move outright. Pure; no I/O.
"""

from __future__ import annotations

import numpy as np
import pytest

from post_game import config


def _envelope(gap_s: float, cap_m: float,
              slack_m: float = None, speed: float = None) -> float:
    """The move the stitcher will allow across `gap_s`, mirroring reid_stitch."""
    slack_m = config.STITCH_SLACK_M if slack_m is None else slack_m
    speed = config.MAX_PLAUSIBLE_SPEED_MS if speed is None else speed
    return min(speed * max(gap_s, 0.0) + slack_m, cap_m)


def test_the_shipped_cap_is_finite():
    """inf was the old default and it is what let 64 m joins through."""
    assert np.isfinite(config.STITCH_DIST_CAP_M)
    assert config.STITCH_DIST_CAP_M == pytest.approx(12.0)


def test_cap_binds_before_the_pitch_is_crossable():
    """At the 10 s max gap the speed term alone allows more than the pitch."""
    speed_only = _envelope(config.STITCH_MAX_GAP_S, cap_m=float("inf"))
    assert speed_only > 55.0, "speed term should be the permissive one"
    capped = _envelope(config.STITCH_MAX_GAP_S, cap_m=config.STITCH_DIST_CAP_M)
    assert capped == pytest.approx(12.0)


def test_short_gaps_are_unaffected_by_the_cap():
    """The cap must not touch ordinary frame-to-frame continuation.

    It starts binding at gap = 1.0 s, where 9 m/s x 1 s + 3 m slack already
    reaches 12 m — so only sub-second gaps are strictly below it.
    """
    for gap in (0.1, 0.2, 0.5):
        assert _envelope(gap, config.STITCH_DIST_CAP_M) < config.STITCH_DIST_CAP_M


def test_the_cap_takes_over_once_the_gap_is_long():
    # speed*gap + slack exceeds 12 m from about gap = 1.0 s
    assert _envelope(1.0, 12.0) == pytest.approx(12.0)
    assert _envelope(5.0, 12.0) == pytest.approx(12.0)


def test_a_realistic_jog_still_links():
    """A child jogging 1 m/s through a 4 s occlusion covers 4 m — keep it."""
    assert 4.0 <= _envelope(4.0, config.STITCH_DIST_CAP_M)


def test_a_cross_pitch_jump_is_rejected():
    """The measured 24 m p90 and 64 m max joins must no longer fit."""
    for dist in (24.0, 64.0):
        assert dist > _envelope(config.STITCH_MAX_GAP_S, config.STITCH_DIST_CAP_M)


def test_cap_is_env_overridable():
    """Operators must be able to A/B it without editing code."""
    import importlib
    import os
    old = os.environ.get("STITCH_DIST_CAP_M")
    try:
        os.environ["STITCH_DIST_CAP_M"] = "8"
        importlib.reload(config)
        assert config.STITCH_DIST_CAP_M == pytest.approx(8.0)
    finally:
        if old is None:
            os.environ.pop("STITCH_DIST_CAP_M", None)
        else:
            os.environ["STITCH_DIST_CAP_M"] = old
        importlib.reload(config)
    assert config.STITCH_DIST_CAP_M == pytest.approx(12.0)
