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


# --------------------------------------------------------------------------
# click -> position stats
# --------------------------------------------------------------------------

from dataclasses import dataclass as _dc

from post_game.click_samples import (MIN_CLICKS, ClickPlayerStats,
                                     compute_click_stats, spread_score,
                                     to_field)
from tracking.click_sample_app import snap


@_dc
class _Cal:
    """Minimal stand-in with a scaled homography (1 px = 0.01 m).

    `sphere=None` forces FieldProjector down its planar-homography path, which
    is what makes the arithmetic here predictable enough to assert on.
    """
    length_m: float = 50.0
    width_m: float = 30.0
    homography: list = None
    sphere: object = None
    video_frame_size: tuple = (7680, 3840)

    def __post_init__(self):
        if self.homography is None:
            self.homography = [[0.01, 0, 0], [0, 0.01, 0], [0, 0, 1]]


def _clicks(pid, n, x_eq, y_eq, t0=0.0, dt=30.0):
    return [{"player_id": pid, "video_time_s": t0 + i * dt,
             "click_x_eq": x_eq, "click_y_eq": y_eq} for i in range(n)]


def test_under_sampled_players_are_refused_not_published():
    """A number from 10 clicks is 20% wrong; report it as missing instead."""
    cl = _clicks("a", MIN_CLICKS - 1, 2500, 1500)
    stats, rep = compute_click_stats(cl, _Cal())
    assert stats == []
    assert rep["under_sampled"][0]["player_id"] == "a"


def test_enough_clicks_produces_stats():
    stats, rep = compute_click_stats(_clicks("a", MIN_CLICKS, 2500, 1500), _Cal())
    assert len(stats) == 1 and stats[0].n_clicks == MIN_CLICKS
    assert rep["under_sampled"] == []


def test_no_distance_or_speed_field_is_ever_emitted():
    """The module must not expose an integral metric, even caveated."""
    banned = {"distance", "dist", "speed", "sprint", "km", "velocity", "work_rate"}
    fields = set(ClickPlayerStats.__dataclass_fields__)
    for f in fields:
        assert not any(b in f.lower() for b in banned), f"forbidden metric: {f}"


def test_not_ours_clicks_are_excluded():
    cl = _clicks("a", MIN_CLICKS, 2500, 1500) + _clicks("__not_ours__", 50, 100, 100)
    pts = to_field(cl, _Cal())
    assert all(p["player_id"] == "a" for p in pts)


def test_halves_are_flipped_into_one_canonical_frame():
    """Same physical spot in both halves must read as the same depth."""
    cal = _Cal()
    near = _clicks("a", 15, 500, 1500, t0=0.0, dt=10.0)      # x_m = 5
    far = _clicks("a", 15, 4500, 1500, t0=2000.0, dt=10.0)   # x_m = 45
    periods = [(0.0, 1000.0), (2000.0, 3000.0)]
    stats, _ = compute_click_stats(
        near + far, cal, periods=periods, our_net_at_x0={1: True, 2: False})
    s = stats[0]
    # H1 depth 5 (net at x0); H2 x=45 flips to depth 50-45=5 -> same
    assert s.by_half["1"]["avg_depth_m"] == pytest.approx(5.0, abs=0.5)
    assert s.by_half["2"]["avg_depth_m"] == pytest.approx(5.0, abs=0.5)


def test_thirds_sum_to_one_hundred():
    cl = (_clicks("a", 10, 500, 1500) + _clicks("a", 10, 2500, 1500, t0=500)
          + _clicks("a", 10, 4500, 1500, t0=1000))
    stats, _ = compute_click_stats(cl, _Cal())
    s = stats[0]
    assert s.pct_def_third + s.pct_mid_third + s.pct_att_third == pytest.approx(100.0, abs=0.2)


def test_spread_score_separates_clustered_from_spread():
    """Clustered clicks plateau the error, so the caller must be able to see it."""
    spread = np.linspace(0, 3000, 40)
    clustered = np.linspace(0, 60, 40)
    assert spread_score(spread, 0, 3000) > 0.9
    assert spread_score(clustered, 0, 3000) < 0.3


def test_snap_rejects_an_ambiguous_pair():
    """Two bodies equally near => keep the raw click rather than guess a child."""
    dets = [{"track_id": 1, "foot_x_eq": 1000, "foot_y_eq": 1000, "bbox_h": 70},
            {"track_id": 2, "foot_x_eq": 1030, "foot_y_eq": 1000, "bbox_h": 70}]
    x, y, tid = snap(1010, 1000, dets)
    assert tid is None and (x, y) == (1010, 1000)


def test_snap_accepts_a_clear_single_body():
    dets = [{"track_id": 7, "foot_x_eq": 1000, "foot_y_eq": 1000, "bbox_h": 70},
            {"track_id": 8, "foot_x_eq": 1400, "foot_y_eq": 1000, "bbox_h": 70}]
    x, y, tid = snap(1010, 1005, dets)
    assert tid == 7 and (x, y) == (1000, 1000)


def test_snap_ignores_adults():
    """A near-camera adult must never capture a click meant for a child."""
    dets = [{"track_id": 9, "foot_x_eq": 1000, "foot_y_eq": 1000, "bbox_h": 200}]
    x, y, tid = snap(1005, 1000, dets)
    assert tid is None


# --------------------------------------------------------------------------
# on-field filtering of the name buttons
# --------------------------------------------------------------------------

from tracking.click_sample_app import onfield_at


def test_onfield_at_returns_only_players_in_their_window():
    """7 on the pitch, not the 12-strong squad and not the 16-name roster."""
    iv = {
        "a": [(0.0, 600.0)], "b": [(0.0, 600.0)], "c": [(0.0, 600.0)],
        "d": [(0.0, 600.0)], "e": [(0.0, 600.0)], "f": [(0.0, 600.0)],
        "gk": [(0.0, 3000.0)],
        "sub1": [(600.0, 1200.0)], "sub2": [(600.0, 1200.0)],
    }
    assert onfield_at(iv, 300.0) == ["a", "b", "c", "d", "e", "f", "gk"]
    assert len(onfield_at(iv, 300.0)) == 7
    assert "sub1" not in onfield_at(iv, 300.0)


def test_onfield_at_handles_a_substitution():
    iv = {"starter": [(0.0, 600.0)], "sub": [(600.0, 1200.0)]}
    assert onfield_at(iv, 100.0) == ["starter"]
    assert onfield_at(iv, 900.0) == ["sub"]


def test_onfield_at_slack_covers_the_kickoff_boundary():
    """A frame rendered exactly at kickoff must not return an empty list.

    Observed live: Game 1's first sampled frame sits at t=40.9s, the H1 kickoff
    offset, and a strict test returned nobody on the pitch.
    """
    iv = {"a": [(40.92, 1500.0)]}
    assert onfield_at(iv, 40.9) == ["a"]
    assert onfield_at(iv, 40.9, slack_s=0.0) == []


def test_onfield_at_is_empty_well_outside_every_window():
    iv = {"a": [(0.0, 100.0)]}
    assert onfield_at(iv, 5000.0) == []


# --------------------------------------------------------------------------
# panel ranking near the far touchline
#
# This is the bug that shipped three times: the far touchline is the horizon in
# this camera geometry, so spectators behind it project onto y~0 and read as
# on-pitch. Measured on Game 1: the line sits at pixel row 2028, and 10 px above
# it projects to y=-3.2 m. Neither a polygon test nor apparent size can separate
# a child at the far side from a seated adult just beyond it.
# --------------------------------------------------------------------------

from tracking.click_sample_app import (FAR_TOUCHLINE_BAND_M, PANEL_H,
                                       occupied_panels,
                                       panel_label)


class _Proj:
    """Projector stub: pixel y maps linearly to field width, x to length."""
    def pixel_to_field(self, px, py):
        return (px - 2000) / 100.0, (py - 2000) / 100.0


def _det(px, py, h=70, tid=1):
    return {"track_id": tid, "foot_x_eq": px, "foot_y_eq": py, "bbox_h": h}


def _frm(dets):
    return {"video_time_s": 100.0, "detections": dets}


def test_far_band_bodies_do_not_inflate_the_confident_count():
    """A spectator projecting just inside the far line must not count.

    Both detections are placed in the SAME grid cell (within one PANEL_H of each
    other) so the assertion is about the tally rather than about how the pixel
    grid happens to split them.
    """
    box = [2000, 2000, 2750, 2600]
    band_px = 2000 + int(FAR_TOUCHLINE_BAND_M * 100)
    near_line = _det(2100, band_px - 50)        # inside the uncertain band
    real = _det(2200, band_px + 200)           # comfortably on the pitch
    assert (near_line["foot_y_eq"] - box[1]) // PANEL_H \
        == (real["foot_y_eq"] - box[1]) // PANEL_H, "fixture must share one cell"
    cells = occupied_panels(_frm([near_line, real]), box, _Proj(), (50.0, 30.0))
    assert len(cells) == 1
    assert cells[0]["confident"] == 1
    assert cells[0]["uncertain"] == 1


def test_far_band_bodies_still_make_a_panel_visible():
    """A player really can be at the far side, so the panel must stay clickable."""
    box = [2000, 2000, 2750, 2600]
    only_far = _det(2100, 2000 + 50)
    cells = occupied_panels(_frm([only_far]), box, _Proj(), (50.0, 30.0))
    assert len(cells) == 1 and cells[0]["confident"] == 0
    assert cells[0]["uncertain"] == 1


def test_ranking_prefers_confident_play_over_far_clutter():
    box = [2000, 2000, 3500, 2600]
    far_heavy = [_det(2100 + i * 5, 2050) for i in range(6)]      # far band
    real_play = [_det(2900 + i * 5, 2900) for i in range(3)]      # comfortably in
    cells = occupied_panels(_frm(far_heavy + real_play), box, _Proj(), (50.0, 30.0))
    assert cells[0]["confident"] == 3, "real play must rank above far-band clutter"


def test_adults_are_counted_separately_from_players():
    box = [2000, 2000, 2750, 2600]
    cells = occupied_panels(
        _frm([_det(2100, 2900, h=200), _det(2150, 2900, h=70)]),
        box, _Proj(), (50.0, 30.0))
    assert cells[0]["adults"] == 1 and cells[0]["confident"] == 1


def test_off_pitch_bodies_are_counted_separately():
    box = [2000, 2000, 2750, 2600]
    # y far beyond the pitch width -> outside the polygon entirely
    cells = occupied_panels(_frm([_det(2100, 2000 + 5000)]), box,
                            _Proj(), (50.0, 30.0))
    assert cells == [] or cells[0]["off"] == 1


def test_panel_label_uses_field_position_not_grid_arithmetic():
    """Labels must name a real pitch area; the grid-derived version produced
    'left, far side' three times in one frame."""
    L, W = 60.0, 30.0
    assert panel_label({"fx": [5.0], "fy": [15.0]}, (L, W)) == "defensive third, centre"
    assert panel_label({"fx": [55.0], "fy": [2.0]}, (L, W)) == "attacking third, left"
    assert panel_label({"fx": [30.0], "fy": [28.0]}, (L, W)) == "middle third, right"


def test_panel_label_degrades_without_field_coords():
    assert panel_label({"fx": [], "fy": []}, (50.0, 30.0)) == "pitch area"
    assert panel_label({"fx": [1.0], "fy": [1.0]}, None) == "pitch area"


# --------------------------------------------------------------------------
# roster button labels
# --------------------------------------------------------------------------

from tracking.click_sample_app import _num, button_label


def test_button_label_leads_with_the_shirt_number():
    """The number is what the coach reads off the kit, so it comes first."""
    assert button_label({"id": "a", "name": "Liam Gibala", "number": "7"}) \
        == "#7 Liam Gibala"


def test_button_label_marks_the_keeper_after_the_name():
    """Guards operator precedence: `x + y if c else x` would misplace the suffix."""
    assert button_label({"id": "gk", "name": "Liam Garland", "number": "14"},
                        "gk") == "#14 Liam Garland (GK)"
    assert button_label({"id": "out", "name": "Ben Hahn", "number": "8"},
                        "gk") == "#8 Ben Hahn"


def test_button_label_survives_a_missing_number():
    """A roster row without a shirt number must not render '#None'."""
    assert button_label({"id": "a", "name": "No Number", "number": None}) == "No Number"
    assert button_label({"id": "gk", "name": "Keeper", "number": None}, "gk") \
        == "Keeper (GK)"


def test_number_sort_is_numeric_not_lexical():
    """'21' must not sort before '3'."""
    ps = [{"number": "21"}, {"number": "3"}, {"number": "10"}]
    assert [p["number"] for p in sorted(ps, key=_num)] == ["3", "10", "21"]


def test_unnumbered_players_sort_last():
    ps = [{"number": None}, {"number": "9"}, {"number": "bad"}]
    assert _num(ps[0]) == 999 and _num(ps[2]) == 999
    assert sorted(ps, key=_num)[0]["number"] == "9"
