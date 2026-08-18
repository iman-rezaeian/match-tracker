"""Teams switch ends at half time, so H2 must be mirrored into H1's frame.

The report shipped a "+28.9 m drift" for a defender who had simply changed ends,
because `compute_click_stats`' `our_net_at_x0` parameter existed and was never
passed. The coach caught it immediately -- "no, they switched side" -- and every
drift number in that table was the end change rather than the player. These tests
exist so that cannot recur silently.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from post_game.click_orientation import our_net_at_x0_from_keeper
from post_game.click_samples import compute_click_stats


@dataclass
class _Cal:
    """Scaled homography: 1 px = 0.01 m, planar path (sphere=None)."""
    length_m: float = 50.0
    width_m: float = 30.0
    homography: list = None
    sphere: object = None
    video_frame_size: tuple = (7680, 3840)

    def __post_init__(self):
        if self.homography is None:
            self.homography = [[0.01, 0, 0], [0, 0.01, 0], [0, 0, 1]]


def _p(pid, t, x):
    return {"player_id": pid, "video_time_s": t, "x_m": x}


def _per_of(t):
    return 1 if t < 1000 else 2


def test_keeper_clicks_anchor_each_half_independently():
    pts = ([_p("gk", 10.0 + i, 3.0) for i in range(5)]         # H1 near x=0
           + [_p("gk", 2000.0 + i, 52.0) for i in range(5)])   # H2 near x=L
    assert our_net_at_x0_from_keeper(pts, "gk", 55.0, _per_of) == {1: True, 2: False}


def test_one_anchored_half_determines_the_other_by_alternation():
    pts = [_p("gk", 10.0 + i, 3.0) for i in range(5)]          # H1 only
    net = our_net_at_x0_from_keeper(pts, "gk", 55.0, _per_of)
    assert net[1] is True


def test_no_keeper_clicks_returns_none_so_drift_is_withheld():
    """Publishing an unflipped drift is worse than publishing none at all."""
    assert our_net_at_x0_from_keeper([_p("out", 10.0, 30.0)], "gk",
                                     55.0, _per_of) is None


def test_missing_keeper_id_returns_none():
    assert our_net_at_x0_from_keeper([_p("gk", 1.0, 3.0)], None,
                                     55.0, _per_of) is None


def test_midpitch_keeper_median_is_refused_not_guessed():
    """A wrong flip mirrors an entire half, so an ambiguous anchor must refuse."""
    pts = [_p("gk", 10.0 + i, 27.0) for i in range(5)]
    assert our_net_at_x0_from_keeper(pts, "gk", 55.0, _per_of) is None


def test_flipping_makes_a_stationary_player_show_zero_drift():
    """The end-to-end guarantee, and the exact case that was wrong: a player in
    the same physical spot in both halves must report no drift."""
    cal = _Cal()
    h1 = [{"player_id": "a", "video_time_s": 10.0 + i,
           "click_x_eq": 1000, "click_y_eq": 1500} for i in range(6)]   # x_m 10
    h2 = [{"player_id": "a", "video_time_s": 2000.0 + i,
           "click_x_eq": 4000, "click_y_eq": 1500} for i in range(6)]   # x_m 40
    stats, _ = compute_click_stats(h1 + h2, cal,
                                   periods=[(0.0, 1000.0), (1500.0, 1e9)],
                                   our_net_at_x0={1: True, 2: False},
                                   min_clicks=6)
    s = stats[0]
    assert s.by_half["1"]["avg_depth_m"] == pytest.approx(
        s.by_half["2"]["avg_depth_m"], abs=0.5)


def test_without_flipping_the_same_player_shows_a_false_drift():
    """Pins the magnitude of the bug: unflipped, the stationary player above
    reads as having crossed 30 m of pitch."""
    cal = _Cal()
    h1 = [{"player_id": "a", "video_time_s": 10.0 + i,
           "click_x_eq": 1000, "click_y_eq": 1500} for i in range(6)]
    h2 = [{"player_id": "a", "video_time_s": 2000.0 + i,
           "click_x_eq": 4000, "click_y_eq": 1500} for i in range(6)]
    stats, _ = compute_click_stats(h1 + h2, cal,
                                   periods=[(0.0, 1000.0), (1500.0, 1e9)],
                                   our_net_at_x0=None, min_clicks=6)
    s = stats[0]
    drift = s.by_half["2"]["avg_depth_m"] - s.by_half["1"]["avg_depth_m"]
    assert drift == pytest.approx(30.0, abs=1.0)
