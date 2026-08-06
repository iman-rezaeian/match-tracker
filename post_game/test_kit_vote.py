"""Kit discrimination must work for BOTH kits the team owns.

Green (#16a34a, S221) is chromatic — hue separates it. Black (#0a0a0a, S0) is
not, and neither are the white/grey kits it has been played against; for those
hue is noise and brightness is the entire signal. Applying one axis to both is
the bug that has bitten this pipeline from each side already: the grass drop
deletes the green kit, and a low-saturation drop deletes the black one.
"""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from post_game.kit_vote import (  # noqa: E402
    OPP, OURS, UNKNOWN, circ_dist, hex_to_hsv, pick_axis, vote_detection,
)

GREEN, BLACK = "#16a34a", "#0a0a0a"
BLUE, WHITE, GREY = "#2563eb", "#f5f5f4", "#d4d4d4"


def _frame_of(hex_color: str, w: int = 40, h: int = 120):
    """A frame filled with one kit colour, so a bbox over it is that kit."""
    s = hex_color.lstrip("#")
    bgr = (int(s[4:6], 16), int(s[2:4], 16), int(s[0:2], 16))
    return np.full((h, w, 3), bgr, dtype=np.uint8)


BBOX = (0, 0, 40, 120)


# --- axis selection ---------------------------------------------------------

def test_two_chromatic_kits_use_hue():
    assert pick_axis(GREEN, BLUE) == "hue"


def test_black_kit_forces_brightness():
    assert pick_axis(BLACK, WHITE) == "value"
    assert pick_axis(BLACK, GREY) == "value"


def test_one_achromatic_kit_is_enough_to_disqualify_hue():
    """Black vs blue: the black side has no hue to compare, so hue is unusable."""
    assert pick_axis(BLACK, BLUE) == "value"


# --- the real game configurations -------------------------------------------

def test_green_vs_blue_separates():
    assert vote_detection(_frame_of(GREEN), BBOX, GREEN, BLUE) == OURS
    assert vote_detection(_frame_of(BLUE), BBOX, GREEN, BLUE) == OPP


def test_black_vs_white_separates():
    """The June games. Hue would abstain on every detection here."""
    assert vote_detection(_frame_of(BLACK), BBOX, BLACK, WHITE) == OURS
    assert vote_detection(_frame_of(WHITE), BBOX, BLACK, WHITE) == OPP


def test_black_vs_grey_separates():
    assert vote_detection(_frame_of(BLACK), BBOX, BLACK, GREY) == OURS
    assert vote_detection(_frame_of(GREY), BBOX, BLACK, GREY) == OPP


def test_hue_axis_would_have_failed_the_black_games():
    """Regression guard: forcing hue on an achromatic pair must NOT be confident."""
    got = [vote_detection(_frame_of(k), BBOX, BLACK, WHITE, axis="hue")
           for k in (BLACK, WHITE)]
    assert all(v == UNKNOWN for v in got)


# --- abstention -------------------------------------------------------------

def test_ambiguous_midtone_abstains_rather_than_guessing():
    """Mid-grey sits between black and white — no confident call."""
    mid = _frame_of("#808080")
    assert vote_detection(mid, BBOX, BLACK, WHITE, value_margin=60.0) == UNKNOWN


def test_tiny_bbox_abstains():
    assert vote_detection(_frame_of(GREEN), (0, 0, 40, 8), GREEN, BLUE) == UNKNOWN


def test_degenerate_bbox_abstains():
    assert vote_detection(_frame_of(GREEN), (0, 0, 0, 0), GREEN, BLUE) == UNKNOWN


# --- helpers ----------------------------------------------------------------

def test_hue_distance_wraps():
    assert circ_dist(1.0, 179.0) == pytest.approx(2.0)
    assert circ_dist(71.0, 111.0) == pytest.approx(40.0)


def test_known_kit_hsv_values():
    h, s, v = hex_to_hsv(GREEN)
    assert (round(h), round(s)) == (71, 221)
    _, s_black, v_black = hex_to_hsv(BLACK)
    assert s_black == 0 and v_black < 20


def test_bad_hex_is_harmless():
    assert hex_to_hsv("") == (0.0, 0.0, 0.0)
    assert hex_to_hsv("nope") == (0.0, 0.0, 0.0)
