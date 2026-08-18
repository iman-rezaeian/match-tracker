"""Unit tests for the on-field-window attribution filter (stats._drop_offwindow).

The coach's SUB taps are independent evidence the software never produced. On W8,
30.0% of attributed detections (40,032 of 133,637) fell in minutes the log says
the player was on the BENCH — Qian 50.6%, Rezaeian 44.0%, Hahn 43.7% — i.e. that
"running" belonged to another child. The control that validates the whole test:
Garland never subs off, so he cannot leak, and he measured exactly 0.0%.

These tests lock the filter's contract, including that control case.

Run: python -m post_game.test_offwindow_filter
"""
from __future__ import annotations

import pandas as pd

from post_game.stats import _drop_offwindow


def _df(rows):
    return pd.DataFrame(rows, columns=["player_id", "time_s", "track_id"])


def test_drops_only_outside_the_window():
    df = _df([("p1", 5.0, 1), ("p1", 50.0, 2), ("p1", 95.0, 3)])
    out = _drop_offwindow(df, {"p1": [(10.0, 90.0)]}, {})
    assert list(out["time_s"]) == [50.0]


def test_never_subbed_off_player_loses_nothing():
    # THE CONTROL: a player on for the whole game cannot leak. If this fails, the
    # filter is wrong (that is exactly how the W8 measurement was validated).
    df = _df([("gk", t, 1) for t in (0.0, 100.0, 1500.0, 3100.0)])
    rep = {}
    out = _drop_offwindow(df, {"gk": [(0.0, 3194.0)]}, rep)
    assert len(out) == len(df)
    assert "gk" not in rep          # nothing reported when nothing leaked


def test_multiple_stints_are_all_kept():
    df = _df([("p1", 5.0, 1), ("p1", 15.0, 1), ("p1", 45.0, 2), ("p1", 75.0, 3)])
    out = _drop_offwindow(df, {"p1": [(10.0, 20.0), (70.0, 80.0)]}, {})
    assert sorted(out["time_s"]) == [15.0, 75.0]


def test_player_with_no_logged_window_is_kept():
    # The coach never logged him — we must not delete him on an absent log.
    df = _df([("p9", 5.0, 1), ("p9", 500.0, 2)])
    out = _drop_offwindow(df, {"p1": [(0.0, 10.0)]}, {})
    assert len(out) == 2


def test_report_records_fraction_per_player():
    df = _df([("p1", 1.0, 1), ("p1", 2.0, 1), ("p1", 50.0, 2), ("p1", 51.0, 2)])
    rep = {}
    _drop_offwindow(df, {"p1": [(0.0, 10.0)]}, rep)
    assert rep["p1"]["offwindow_detections"] == 2
    assert abs(rep["p1"]["offwindow_frac"] - 0.5) < 1e-9


def test_players_are_filtered_independently():
    df = _df([("p1", 5.0, 1), ("p2", 5.0, 2)])
    # p1 is on at t=5, p2 is not
    out = _drop_offwindow(df, {"p1": [(0.0, 10.0)], "p2": [(100.0, 200.0)]}, {})
    assert list(out["player_id"]) == ["p1"]


def test_window_edges_are_inclusive():
    df = _df([("p1", 10.0, 1), ("p1", 90.0, 2)])
    out = _drop_offwindow(df, {"p1": [(10.0, 90.0)]}, {})
    assert len(out) == 2, "a detection exactly at the sub moment counts as on-field"


def test_empty_onfield_is_a_noop():
    df = _df([("p1", 5.0, 1)])
    assert len(_drop_offwindow(df, {}, {})) == 1


def test_all_outside_drops_everything_for_that_player():
    df = _df([("p1", 500.0, 1), ("p2", 5.0, 2)])
    out = _drop_offwindow(df, {"p1": [(0.0, 10.0)], "p2": [(0.0, 10.0)]}, {})
    assert list(out["player_id"]) == ["p2"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} off-window filter tests passed.")
