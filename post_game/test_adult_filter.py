"""Tests for the near-camera sideline-adult filter."""

from __future__ import annotations

import numpy as np
import pandas as pd

from post_game.adult_filter import (ADULT_BOX_H_PX, MIN_ROWS_TO_JUDGE,
                                    adult_track_ids, drop_sideline_adults)


def _track(tid: int, h: float, n: int = 20) -> pd.DataFrame:
    return pd.DataFrame({
        "track_id": [tid] * n,
        "time_s": np.arange(n) * 0.1,
        "bbox_h_crop": [h] * n,
        "foot_x_eq": np.arange(n) * 1.0,
        "foot_y_eq": np.arange(n) * 1.0,
    })


def test_tall_track_is_flagged_short_is_kept():
    df = pd.concat([_track(1, 140.0), _track(2, 70.0)], ignore_index=True)
    assert adult_track_ids(df) == {1}


def test_threshold_is_inclusive_at_the_boundary():
    """A track exactly at the threshold counts as an adult, matching >=."""
    df = _track(1, ADULT_BOX_H_PX)
    assert adult_track_ids(df) == {1}
    df2 = _track(2, ADULT_BOX_H_PX - 0.1)
    assert adult_track_ids(df2) == set()


def test_short_tracks_are_kept_even_when_tall():
    """Too few detections to judge -> keep. Never drop a player to tidy a metric."""
    df = _track(1, 200.0, n=MIN_ROWS_TO_JUDGE - 1)
    assert adult_track_ids(df) == set()


def test_median_not_mean_so_a_few_big_frames_do_not_convict():
    """A player who passes close to camera for a moment must survive."""
    n = 40
    h = [70.0] * n
    h[:5] = [250.0] * 5           # brief near-camera pass
    df = pd.DataFrame({
        "track_id": [1] * n,
        "time_s": np.arange(n) * 0.1,
        "bbox_h_crop": h,
        "foot_x_eq": np.zeros(n),
        "foot_y_eq": np.zeros(n),
    })
    assert adult_track_ids(df) == set(), "median must resist a short spike"


def test_drop_removes_all_rows_of_a_flagged_track():
    df = pd.concat([_track(1, 140.0), _track(2, 70.0)], ignore_index=True)
    out = drop_sideline_adults(df)
    assert set(out.track_id.unique()) == {2}
    assert len(out) == 20


def test_report_records_what_was_deleted():
    """A filter must return its deletions, not just log them."""
    df = pd.concat([_track(1, 140.0), _track(2, 70.0)], ignore_index=True)
    rep: dict = {}
    drop_sideline_adults(df, report=rep)
    assert rep["dropped_tracks"] == 1
    assert rep["dropped_rows"] == 20
    assert rep["kept_rows"] == 20
    assert rep["dropped_track_ids"] == [1]


def test_missing_height_column_is_inactive_not_destructive():
    """An older cache must degrade to current behaviour, not lose every track."""
    df = pd.DataFrame({"track_id": [1, 1], "time_s": [0.0, 0.1],
                       "foot_x_eq": [0.0, 1.0], "foot_y_eq": [0.0, 1.0]})
    assert adult_track_ids(df) == set()
    rep: dict = {}
    out = drop_sideline_adults(df, report=rep)
    assert len(out) == 2
    assert rep["dropped_rows"] == 0


def test_empty_frame_is_safe():
    df = pd.DataFrame(columns=["track_id", "time_s", "bbox_h_crop"])
    assert adult_track_ids(df) == set()
    assert drop_sideline_adults(df).empty


def test_team_metrics_move_toward_truth_on_a_synthetic_scene():
    """The point of the filter: a touchline adult must stop dragging the centroid.

    Players sit around x=1000; one big adult sits far away at x=3000. Filtering
    must pull the measured centroid back toward the players' own value.
    """
    players = pd.concat([_track(i, 70.0) for i in range(1, 8)], ignore_index=True)
    players["foot_x_eq"] = 1000.0
    adult = _track(99, 140.0)
    adult["foot_x_eq"] = 3000.0
    truth = players.foot_x_eq.mean()
    dirty = pd.concat([players, adult], ignore_index=True)
    filtered = drop_sideline_adults(dirty)
    assert abs(filtered.foot_x_eq.mean() - truth) < abs(dirty.foot_x_eq.mean() - truth)
    assert filtered.foot_x_eq.mean() == truth


def test_a_player_sized_far_adult_survives_the_filter():
    """The documented residual, pinned as a test rather than left as a caveat.

    A far-side coach projects at player height, so height cannot exclude him.
    If someone later 'fixes' this by raising the threshold, the our-players-lost
    cost rises steeply (measured: 43% lost at h<90) — so this must stay a known
    limitation, not become a tuning target.
    """
    far_adult = _track(99, 80.0)          # player-sized because he is distant
    assert adult_track_ids(far_adult) == set()
