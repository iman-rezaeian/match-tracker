"""Unit tests for halftime detection + the no-tracklet-spans-halftime split."""

from __future__ import annotations

import numpy as np
import pandas as pd

from post_game.halftime_split import (
    detect_halftime_break,
    split_tracks_at_halftime,
)


def _game(break_start: float, break_end: float, *, n_players: int = 14,
          duration: float = 3200.0, hz: float = 2.0) -> pd.DataFrame:
    """Synthetic game: n_players on the pitch except during the break."""
    rows = []
    for t in np.arange(0.0, duration, 1.0 / hz):
        if break_start <= t < break_end:
            continue
        for p in range(n_players):
            rows.append({"track_id": p, "time_s": float(t), "x_m": 10.0, "y_m": 10.0})
    return pd.DataFrame(rows)


def test_detects_the_break_when_no_logged_time_is_given():
    df = _game(1500.0, 1800.0)
    got = detect_halftime_break(df)
    assert got is not None
    assert abs(got[0] - 1500.0) <= 12.0
    assert abs(got[1] - 1800.0) <= 12.0


def test_detected_break_overrides_a_late_coach_tap():
    """The real case: coach taps 47 s after the whistle (mqcf9axlvtuyt)."""
    df = _game(1516.0, 1877.0)
    got = detect_halftime_break(df, logged_break=(1563.0, 1878.0))
    assert got is not None
    assert got[0] < 1550.0  # follows the footage, not the tap


def test_far_away_empty_window_is_rejected():
    """An empty stretch nowhere near the logged break is not halftime."""
    df = _game(400.0, 800.0)
    assert detect_halftime_break(df, logged_break=(2000.0, 2300.0),
                                 max_shift_s=120.0) is None


def test_short_stoppage_is_not_a_break():
    df = _game(1500.0, 1520.0)  # 20 s < MIN_BREAK_S
    assert detect_halftime_break(df) is None


def test_no_break_at_all_returns_none():
    assert detect_halftime_break(_game(0.0, 0.0)) is None


def test_empty_input_returns_none():
    assert detect_halftime_break(pd.DataFrame()) is None


def test_split_separates_the_two_halves():
    df = pd.DataFrame({
        "track_id": [1, 1, 1, 1],
        "time_s": [100.0, 200.0, 2000.0, 2100.0],
    })
    out, _, _, parent = split_tracks_at_halftime(df, (1500.0, 1800.0))
    first = set(out.loc[out.time_s < 1500, "track_id"])
    second = set(out.loc[out.time_s > 1800, "track_id"])
    assert first != second
    assert not (first & second)
    assert len(parent) == 1
    assert list(parent.values()) == [1]


def test_track_wholly_inside_one_half_is_untouched():
    df = pd.DataFrame({"track_id": [7, 7], "time_s": [100.0, 200.0]})
    out, _, _, parent = split_tracks_at_halftime(df, (1500.0, 1800.0))
    assert list(out["track_id"]) == [7, 7]
    assert parent == {}


def test_no_straddling_tracks_is_a_no_op():
    df = pd.DataFrame({
        "track_id": [1, 1, 2, 2],
        "time_s": [10.0, 20.0, 2000.0, 2010.0],
    })
    out, _, _, parent = split_tracks_at_halftime(df, (1500.0, 1800.0))
    pd.testing.assert_frame_equal(out, df)
    assert parent == {}


def test_new_ids_do_not_collide_with_existing_ones():
    df = pd.DataFrame({
        "track_id": [1, 1, 5, 5],
        "time_s": [100.0, 2000.0, 100.0, 2000.0],
    })
    out, _, _, _ = split_tracks_at_halftime(df, (1500.0, 1800.0))
    # 2 straddling tracks -> 4 distinct ids, none reused
    assert out["track_id"].nunique() == 4
    assert out.groupby("track_id")["time_s"].apply(
        lambda s: (s < 1500).all() or (s > 1800).all()).all()


def test_aux_dicts_are_rekeyed_for_the_new_ids():
    df = pd.DataFrame({"track_id": [3, 3], "time_s": [100.0, 2000.0]})
    jersey = {3: ["hsv"]}
    emb = {3: np.zeros(4, dtype=np.float32)}
    out, j2, e2, parent = split_tracks_at_halftime(df, (1500.0, 1800.0), jersey, emb)
    new_id = next(iter(parent))
    assert j2[new_id] == ["hsv"]
    assert new_id in e2
    assert j2[3] == ["hsv"]  # parent keeps its entry (first half)


def test_split_output_has_no_track_spanning_the_break():
    """The invariant, stated directly."""
    rng = np.random.default_rng(0)
    rows = []
    for t in range(0, 3000, 5):
        if 1500 <= t < 1800:
            continue
        for p in range(8):
            rows.append({"track_id": int(rng.integers(0, 6)), "time_s": float(t)})
    df = pd.DataFrame(rows)
    out, _, _, _ = split_tracks_at_halftime(df, (1500.0, 1800.0))
    # The cut lands at the break midpoint, so every surviving track sits wholly
    # on one side of it.
    mid = 0.5 * (1500.0 + 1800.0)
    for _tid, g in out.groupby("track_id"):
        assert (g["time_s"] < mid).all() or (g["time_s"] >= mid).all()
