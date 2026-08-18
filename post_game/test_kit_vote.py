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
    OPP, OURS, UNKNOWN, _otsu, bimodality, circ_dist, fit_value_anchors,
    hex_to_hsv, pick_axis, vote_detection, vote_value,
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


# --- data-fitted value anchors ----------------------------------------------
# A kit hex is the fabric in a swatch, not the fabric in sunlight. On the value
# axis that gap decides everything, so the anchors are fitted to the footage.

def _two_clusters(dark_at, bright_at, n=200, spread=12.0, seed=0):
    r = np.random.default_rng(seed)
    return np.concatenate([r.normal(dark_at, spread, n),
                           r.normal(bright_at, spread, n)])


def test_fitted_anchors_land_on_the_two_clusters():
    vals = _two_clusters(55, 170)
    our_v, opp_v, note = fit_value_anchors(vals, BLACK, "#28bb40")
    assert our_v == pytest.approx(55, abs=12), note
    assert opp_v == pytest.approx(170, abs=12), note


def test_polarity_comes_from_the_hex_not_the_data():
    """Which cluster is ours is a fact about the kits; lighting cannot flip it."""
    vals = _two_clusters(55, 170)
    dark_kit_ours, _, _ = fit_value_anchors(vals, BLACK, "#f5f5f4")
    _, dark_kit_theirs, _ = fit_value_anchors(vals, "#f5f5f4", BLACK)
    assert dark_kit_ours == pytest.approx(dark_kit_theirs)


def test_unimodal_distribution_abstains():
    """One blob must NOT be bisected — that mislabels half of one team."""
    r = np.random.default_rng(1)
    our_v, opp_v, note = fit_value_anchors(r.normal(120, 15, 400), BLACK, "#28bb40")
    assert our_v is None and opp_v is None
    assert "not bimodal" in note


def test_too_few_tracks_abstains():
    our_v, opp_v, note = fit_value_anchors(_two_clusters(55, 170, n=10),
                                           BLACK, "#28bb40")
    assert our_v is None and opp_v is None and "too few" in note


def test_abstention_leaves_the_caller_on_the_hex_values():
    """None is the signal to keep the hex anchors, not a value to use."""
    our_v, opp_v, _ = fit_value_anchors([], BLACK, "#28bb40")
    assert (our_v, opp_v) == (None, None)


def test_fitted_anchors_fix_the_game1_split():
    """The regression this exists for.

    mrhvbvwi1gjpn: black kit, observed torso V median 126 (p10-p90 46-211) while
    the hex says V10. Voting on the hex midpoint (V98) split the teams 0.69:1;
    a 7v7 game must be ~1:1. Fitted anchors put the boundary at ~125.

    The clusters must sit where the real ones did — BOTH above the hex midpoint
    of 98, since a sunlit black kit photographs at V150-200, not V10. Clusters
    straddling 98 would let the broken anchors score 1:1 by luck and the test
    would pass on a bug.
    """
    vals = _two_clusters(105, 190, n=1500, spread=22)
    our_v, opp_v, _ = fit_value_anchors(vals, BLACK, "#28bb40")
    hex_mid = (hex_to_hsv(BLACK)[2] + hex_to_hsv("#28bb40")[2]) / 2.0
    fitted_mid = (our_v + opp_v) / 2.0
    ours_hex = int((vals < hex_mid).sum())
    ours_fit = int((vals < fitted_mid).sum())
    opp_hex, opp_fit = len(vals) - ours_hex, len(vals) - ours_fit
    assert abs(np.log2(ours_fit / opp_fit)) < abs(np.log2(ours_hex / opp_hex))
    assert 0.8 < ours_fit / opp_fit < 1.25


def test_bimodality_score_separates_one_blob_from_two():
    """eta^2 must put one blob and two clusters on opposite sides of the gate.

    The metric this replaced could not: bisecting a Gaussian scored 2.54 pooled
    SDs against 8.55 for real clusters — same order of magnitude, no usable
    threshold. See `bimodality`.
    """
    one = np.random.default_rng(2).normal(120, 15, 400)
    two = _two_clusters(55, 170)
    assert bimodality(one, _otsu(one)) < 0.75
    assert bimodality(two, _otsu(two)) > 0.75


def test_gate_clears_real_footage_with_headroom():
    """The gate must sit clear of BOTH measured extremes, not just above noise.

    Real games score lower than clean synthetic clusters (0.762 and 0.755 on the
    two July 12 games) because refs, spectators and unclassifiable tracks widen
    the distribution. The highest unimodal case is 0.66. A gate must fall in
    that empty band with room on both sides — at 0.75 a valid game cleared by
    0.005, and any extra noise would have dropped it silently back onto the
    broken hex anchors.
    """
    from post_game.kit_vote import fit_value_anchors as _f
    import inspect
    gate = inspect.signature(_f).parameters["min_separation"].default
    assert 0.66 < gate < 0.755, f"gate {gate} not in the empty band"
    assert gate - 0.66 > 0.03 and 0.755 - gate > 0.03, "insufficient headroom"


def test_overlapping_kits_abstain_rather_than_being_forced_apart():
    """Two kits too close in brightness to tell apart must not be split."""
    our_v, opp_v, note = fit_value_anchors(_two_clusters(100, 140, spread=25),
                                           BLACK, "#28bb40")
    assert our_v is None and "not bimodal" in note


def test_vote_value_uses_whatever_anchors_it_is_given():
    """The fitted anchors must actually change the verdict at the boundary.

    A V100 torso is 90 from the hex 'ours' anchor (V10) but only 87 from the
    opponent's (V187) — so on the hex anchors it lands inside the margin and
    abstains. Against fitted anchors (60/180) it is decisively ours. That flip
    is the whole fix: real black-kit torsos sit at V150-200, where the hex
    anchors have nothing useful to say.
    """
    roi = np.full((40, 3), 100.0, dtype=np.float32)
    assert vote_value(roi, our_v=10.0, opp_v=187.0, min_px=10, margin=12) == UNKNOWN
    assert vote_value(roi, our_v=60.0, opp_v=180.0, min_px=10, margin=12) == OURS
