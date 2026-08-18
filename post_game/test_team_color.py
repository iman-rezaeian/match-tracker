"""Unit tests for post_game/team_color — coarse color-family bucketing.
Pure; no I/O. Run: python -m post_game.test_team_color
"""
from __future__ import annotations

import numpy as np

from post_game.team_color import color_family, tracklet_family


# ---- color_family (hex) --------------------------------------------------
def test_kit_hexes_bucket_coarsely():
    assert color_family(hex="#16a34a") == "green"   # our kit
    assert color_family(hex="#2563eb") == "blue"    # opponent kit
    # rough coach picks near the same families still bucket the same
    assert color_family(hex="#3aa856") == "green"
    assert color_family(hex="#1e40af") == "blue"
    assert color_family(hex="#dc2626") == "red"
    assert color_family(hex="#facc15") == "yellow"


def test_achromatic_hexes():
    assert color_family(hex="#0a0a0a") == "black"
    assert color_family(hex="#f5f5f5") == "white"
    assert color_family(hex="#808080") == "gray"
    assert color_family(hex="bad") == "gray"        # malformed → safe default


def test_color_family_hsv_triples():
    # OpenCV HSV (h 0-179): green ~60, blue ~115
    assert color_family(np.array([60, 200, 150])) == "green"
    assert color_family(np.array([115, 200, 150])) == "blue"
    assert color_family(np.array([115, 200, 20])) == "black"    # dark → black
    assert color_family(np.array([115, 10, 200])) == "white"    # desat bright → white


# ---- tracklet_family (pixel pooling) -------------------------------------
def _px(h, s, v, n):
    return np.tile(np.array([h, s, v], np.float32), (n, 1))


def test_confident_green_tracklet():
    samples = [_px(62, 180, 150, 100)]
    assert tracklet_family(samples) == "green"


def test_confident_blue_tracklet():
    samples = [_px(115, 180, 150, 100)]
    assert tracklet_family(samples) == "blue"


def test_washed_tracklet_is_none():
    # all low-saturation (washed / dark) → no chromatic mass → None (don't act)
    samples = [_px(115, 20, 60, 200)]
    assert tracklet_family(samples) is None


def test_too_few_chromatic_pixels_none():
    # only a handful of saturated pixels → below min_pixels → None
    samples = [_px(62, 200, 150, 10), _px(115, 10, 60, 500)]
    assert tracklet_family(samples) is None


def test_mixed_no_dominant_family_none():
    # half green, half blue chromatic — neither clears the dominance bar → None
    samples = [_px(62, 200, 150, 100), _px(115, 200, 150, 100)]
    assert tracklet_family(samples, dominance=0.6) is None


def test_dominant_family_wins_over_minority_noise():
    # mostly green with a little blue noise → green
    samples = [_px(62, 200, 150, 200), _px(115, 200, 150, 30)]
    assert tracklet_family(samples) == "green"


def test_achromatic_never_returned_as_confident_color():
    # a dark kit: chromatic-gated pixels are few/none → None, never "black"
    samples = [_px(115, 200, 30, 300)]   # saturated in H but dark → black family, dropped
    assert tracklet_family(samples) is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} team_color tests passed.")
