"""Crop choice and prescreen for jersey-number reads.

The squad number is on the back, so a frame only carries one while the player is
running away from the camera. Choosing crops by bbox height alone optimises for
closeness, which is uncorrelated with facing — measured on mri01pvelv46d, 74 of
105 tracklets came back with no number and every successful read described the
number as being on the back.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tracking.vlm_identity import (
    _readable_rows, _tallest_rows, legibility_prescreen,
)

CAM = (27.8, 34.6)      # W8: beyond the far touchline of a 30 m-wide pitch


def _rows(pts, heights, t0=0.0):
    """pts: [(x_m, y_m), ...] in order; heights: bbox height per point."""
    n = len(pts)
    return pd.DataFrame({
        "time_s": [t0 + 0.1 * i for i in range(n)],
        "x_m": [p[0] for p in pts],
        "y_m": [p[1] for p in pts],
        "x1_eq": [0.0] * n, "x2_eq": [30.0] * n,
        "y1_eq": [0.0] * n, "y2_eq": list(heights),
    })


# --- the core behaviour -----------------------------------------------------

def test_prefers_the_away_facing_frame_over_the_merely_closest():
    """A big front-on crop has no number in it; a smaller back-on crop does.

    The path must be CONTINUOUS — the player jogs toward the camera (getting
    bigger), turns, then runs away (getting smaller). A discontinuous path would
    make np.gradient read the jump as motion.
    """
    rows = _rows([(27, 14), (27, 16), (27, 18), (27, 20), (27, 22), (27, 24),
                  (27, 22), (27, 20), (27, 18), (27, 16)],
                 [120, 140, 160, 180, 200, 220, 200, 180, 160, 140])
    got = _readable_rows(rows, 3, cam_xy=CAM)
    # Every pick must be genuinely back-turned, even though the single biggest
    # frame (220) is at the turn where facing is ambiguous.
    assert (got["_away"] > 0.9).all(), f"picked front-on frames: {list(got._away)}"
    assert 220 not in list(got["y2_eq"]), "picked the biggest frame regardless of facing"


def test_without_a_camera_position_it_falls_back_to_height():
    rows = _rows([(27, 20), (27, 18)], [100, 250])
    got = _readable_rows(rows, 1, cam_xy=None)
    assert float(got["y2_eq"].iloc[0]) == 250


def test_matches_tallest_when_facing_is_uninformative():
    """A stationary player tells us nothing, so size decides."""
    rows = _rows([(27, 20)] * 4, [100, 300, 150, 120])
    got = _readable_rows(rows, 1, cam_xy=CAM)
    assert float(got["y2_eq"].iloc[0]) == 300


def test_camera_side_is_respected_not_assumed():
    """The same motion reads as toward or away depending on where the rig is.

    On W8 the camera sits BEYOND the far touchline (y=34.6 on a 30 m pitch), so
    assuming a y=0 sideline would invert the facing test on every tracklet.
    """
    rows = _rows([(27, 10), (27, 12), (27, 14), (27, 16)], [150] * 4)
    at_zero = _readable_rows(rows, 4, cam_xy=(27.8, 0.0))
    at_far = _readable_rows(rows, 4, cam_xy=(27.8, 34.6))
    # Rising y runs AWAY from a y=0 camera and TOWARD a y=34.6 one, so the
    # per-frame facing scores must be opposite in sign.
    a0 = at_zero.sort_values("time_s")["_away"].to_numpy()
    a1 = at_far.sort_values("time_s")["_away"].to_numpy()
    assert (a0 * a1 < 0).all(), f"facing not inverted: {a0} vs {a1}"


def test_returns_at_most_k():
    rows = _rows([(27, 20 - i) for i in range(10)], [100] * 10)
    assert len(_readable_rows(rows, 3, cam_xy=CAM)) == 3


def test_empty_input_is_safe():
    assert _readable_rows(_rows([], []), 3, cam_xy=CAM).empty


def test_zero_height_rows_are_dropped():
    rows = _rows([(27, 20), (27, 18)], [0, 150])
    got = _readable_rows(rows, 5, cam_xy=CAM)
    assert (got["y2_eq"] > 0).all()


# --- prescreen --------------------------------------------------------------

def test_rejects_digits_too_small_to_read():
    rows = _rows([(27, 20)], [70])           # 70px body -> ~12px digits
    ok, why = legibility_prescreen(rows, min_digit_px=14, min_away=-1.0)
    assert not ok and "too-small" in why


def test_accepts_a_big_enough_body():
    rows = _rows([(27, 20)], [200])          # ~34px digits
    ok, _ = legibility_prescreen(rows, min_digit_px=14, min_away=-1.0)
    assert ok


def test_rejects_a_tracklet_that_never_turns_its_back():
    rows = _rows([(27, 20), (27, 24), (27, 28)], [250] * 3)   # straight at cam
    scored = _readable_rows(rows, 3, cam_xy=CAM)
    ok, why = legibility_prescreen(scored, min_digit_px=14, min_away=-0.30)
    assert not ok and "front-on" in why


def test_accepts_a_tracklet_that_does_turn_its_back():
    rows = _rows([(27, 28), (27, 24), (27, 20)], [250] * 3)   # running away
    scored = _readable_rows(rows, 3, cam_xy=CAM)
    ok, _ = legibility_prescreen(scored, min_digit_px=14, min_away=-0.30)
    assert ok


def test_prescreen_is_off_by_default():
    """Defaults must reproduce the old always-read behaviour."""
    rows = _rows([(27, 20)], [40])
    ok, _ = legibility_prescreen(rows, min_digit_px=0.0, min_away=-1.0)
    assert ok


def test_no_frames_is_rejected():
    ok, why = legibility_prescreen(_rows([], []), 14, -0.3)
    assert not ok and why == "no-frames"


def test_tallest_rows_still_works_unchanged():
    """The old selector is kept for callers that want pure size."""
    rows = _rows([(27, 20)] * 3, [100, 300, 200])
    assert float(_tallest_rows(rows, 1)["y2_eq"].iloc[0]) == 300
