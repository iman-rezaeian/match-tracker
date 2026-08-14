"""Tests for click-sampling: the canvas<->equirect transform and the stats.

The round-trip test is the important one. A click tool whose inverse transform
is subtly wrong produces plausible-looking positions that are all shifted, and
nothing downstream can detect it -- the numbers just quietly describe the wrong
part of the pitch.
"""

from __future__ import annotations

import numpy as np
import pytest

from tracking.click_sample_render import (canvas_to_equirect, pitch_bbox,
                                          render_frame, sample_times)


def _frame(w: int = 7680, h: int = 3840) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


@pytest.mark.parametrize("bands", [1, 2, 3])
def test_canvas_click_round_trips_to_equirect(bands):
    """A click at a known canvas point must map back to the pixel it came from."""
    box = (2000, 1900, 5900, 2600)
    _, geom = render_frame(_frame(), box, bands, 1900)
    for eq_x, eq_y in [(2100.0, 1950.0), (3900.0, 2100.0), (5800.0, 2550.0)]:
        # forward: equirect -> strip -> band -> canvas
        sx = eq_x - box[0]
        sy = eq_y - box[1]
        band = min(int(sx // geom["seg_w"]), bands - 1)
        cx = (sx - band * geom["seg_w"]) * geom["scale"]
        cy = band * geom["band_h"] + sy * geom["scale"]
        back = canvas_to_equirect(cx, cy, geom)
        assert back[0] == pytest.approx(eq_x, abs=1.5)
        assert back[1] == pytest.approx(eq_y, abs=1.5)


def test_bands_split_covers_the_whole_strip():
    """No horizontal gap between bands: every x must be reachable."""
    box = (2000, 1900, 5900, 2600)
    canvas, geom = render_frame(_frame(), box, 2, 1900)
    left = canvas_to_equirect(0, 0, geom)
    right = canvas_to_equirect(geom["band_w"] - 1, geom["band_h"] * 2 - 1, geom)
    assert left[0] == pytest.approx(box[0], abs=2)
    assert right[0] > box[0] + (box[2] - box[0]) * 0.9


def test_click_below_last_band_is_clamped_not_wrapped():
    """A stray click under the canvas must not wrap to band 0 and lie."""
    box = (2000, 1900, 5900, 2600)
    _, geom = render_frame(_frame(), box, 2, 1900)
    deep = canvas_to_equirect(100, geom["band_h"] * 5, geom)
    last = canvas_to_equirect(100, geom["band_h"] * 1.5, geom)
    assert deep[0] == pytest.approx(last[0], abs=1e-6)


def test_canvas_is_native_scale_when_bands_match_aspect():
    """2 bands at 1900px on a ~3936px strip should be near 1:1, not shrunken."""
    box = (1985, 1984, 5921, 2563)
    canvas, geom = render_frame(_frame(), box, 2, 1900)
    assert 0.9 < geom["scale"] < 1.1
    assert canvas.shape[0] == geom["band_h"] * 2


def test_player_render_height_is_usable_with_two_bands():
    """The whole design rests on this: a 77px player must stay >= 60px."""
    box = (1985, 1984, 5921, 2563)
    _, geom = render_frame(_frame(), box, 2, 1900)
    assert 77.0 * geom["scale"] >= 60.0


def test_sample_times_skips_halftime_and_spans_both_halves():
    ts = sample_times(40.0, 1750.0, 3200.0, interval=30.0, half_len_s=1500)
    assert min(ts) == 40.0
    h1 = [t for t in ts if t < 1600]
    h2 = [t for t in ts if t >= 1750]
    assert len(h1) > 10 and len(h2) > 10
    # nothing inside the halftime gap
    assert not [t for t in ts if 1545 < t < 1750]


def test_sample_times_respects_video_end():
    ts = sample_times(0.0, 1700.0, 1800.0, interval=30.0, half_len_s=1500)
    assert max(ts) <= 1800.0


def test_pitch_bbox_follows_the_bodies():
    import pandas as pd
    df = pd.DataFrame({
        "foot_x_eq": np.concatenate([np.full(500, 3000.0), np.full(500, 5000.0)]),
        "foot_y_eq": np.full(1000, 2100.0),
    })
    x0, y0, x1, y1 = pitch_bbox(df, pad=50)
    assert x0 < 3000 and x1 > 5000
    assert y0 < 2100 < y1
