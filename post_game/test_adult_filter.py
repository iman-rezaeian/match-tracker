"""Tests for the box-height band that keeps non-players out of team shape."""

from __future__ import annotations

import numpy as np
import pandas as pd

from post_game.adult_filter import (MIN_ROWS_TO_JUDGE, PLAYER_BOX_H_MAX_PX,
                                    PLAYER_BOX_H_MIN_PX, adult_track_ids,
                                    drop_sideline_adults)


def _track(tid: int, h: float, n: int = 20) -> pd.DataFrame:
    return pd.DataFrame({
        "track_id": [tid] * n,
        "time_s": np.arange(n) * 0.1,
        "bbox_h_crop": [h] * n,
        "foot_x_eq": np.arange(n) * 1.0,
        "foot_y_eq": np.arange(n) * 1.0,
    })


def test_tall_track_is_flagged_player_sized_is_kept():
    df = pd.concat([_track(1, 200.0), _track(2, 70.0)], ignore_index=True)
    assert adult_track_ids(df) == {1}


def test_tiny_track_is_flagged_too():
    """The small tail is the PURER pollutant (4% ours) and must not be ignored.

    The original one-sided `h >= 120` filter cut only the tall tail, on an
    inverted premise about where our players sit in the distribution.
    """
    df = pd.concat([_track(1, 30.0), _track(2, 77.0)], ignore_index=True)
    assert adult_track_ids(df) == {1}


def test_band_edges_are_inclusive_so_a_boundary_track_is_kept():
    """Judged by `< min` / `> max`, so a track exactly on an edge survives."""
    assert adult_track_ids(_track(1, PLAYER_BOX_H_MAX_PX)) == set()
    assert adult_track_ids(_track(2, PLAYER_BOX_H_MAX_PX + 0.1)) == {2}
    assert adult_track_ids(_track(3, PLAYER_BOX_H_MIN_PX)) == set()
    assert adult_track_ids(_track(4, PLAYER_BOX_H_MIN_PX - 0.1)) == {4}


def test_the_measured_player_band_is_kept_end_to_end():
    """Our clicked players run p10 53 -> p90 127 px; none of that may be cut."""
    df = pd.concat([_track(i, h) for i, h in enumerate([53.0, 77.0, 127.0], start=1)],
                   ignore_index=True)
    assert adult_track_ids(df) == set()


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
    df = pd.concat([_track(1, 200.0), _track(2, 70.0)], ignore_index=True)
    out = drop_sideline_adults(df)
    assert set(out.track_id.unique()) == {2}
    assert len(out) == 20


def test_report_records_what_was_deleted():
    """A filter must return its deletions, not just log them."""
    df = pd.concat([_track(1, 140.0), _track(2, 70.0)], ignore_index=True)
    rep: dict = {}
    drop_sideline_adults(df, report=rep)
    assert rep["dropped_tracks"] == 0
    df2 = pd.concat([_track(1, 200.0), _track(2, 70.0)], ignore_index=True)
    rep2: dict = {}
    drop_sideline_adults(df2, report=rep2)
    assert rep2["dropped_tracks"] == 1
    assert rep2["dropped_rows"] == 20
    assert rep2["kept_rows"] == 20
    assert rep2["dropped_track_ids"] == [1]
    assert rep2["band_px"] == [PLAYER_BOX_H_MIN_PX, PLAYER_BOX_H_MAX_PX]


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
    adult = _track(99, 200.0)
    adult["foot_x_eq"] = 3000.0
    truth = players.foot_x_eq.mean()
    dirty = pd.concat([players, adult], ignore_index=True)
    filtered = drop_sideline_adults(dirty)
    assert abs(filtered.foot_x_eq.mean() - truth) < abs(dirty.foot_x_eq.mean() - truth)
    assert filtered.foot_x_eq.mean() == truth


def test_the_filter_is_actually_wired_into_the_pipeline():
    """Guards against going inert.

    This filter sat written, tested and UNCALLED for a week while the team-shape
    metrics it exists to clean shipped to the coach polluted. Tests passing is
    not the same as a filter running.
    """
    import inspect

    from . import pipeline
    src = inspect.getsource(pipeline)
    assert "drop_sideline_adults" in src, "filter is not called by the pipeline"
    assert "config.TEAM_SHAPE_SIZE_FILTER" in src, "not behind its config flag"
    # It must feed the TEAM aggregate, not the per-player stats.
    assert "compute_formation(\n        _shape_df" in src, \
        "filtered frame is not the one team shape is computed from"


def test_the_pipeline_reports_its_deletions():
    """The removal must reach the analytics doc, not just a log line."""
    import inspect

    from . import pipeline
    assert '"team_shape_filter"' in inspect.getsource(pipeline)


def test_a_player_sized_body_survives_the_filter():
    """The documented residual, pinned as a test rather than left as a caveat.

    An OPPONENT is exactly player-sized and player-placed, and a far-side coach
    projects at player height, so box height cannot exclude either. Narrowing the
    band to catch them costs our own players fast (measured: `outside 55-150`
    drops 17.6% of clicked players to gain 1.5 points of purity) — so this stays
    a known limitation, not a tuning target. Purity after filtering is 34%, i.e.
    team shape remains directional.
    """
    opponent = _track(99, 80.0)
    assert adult_track_ids(opponent) == set()
