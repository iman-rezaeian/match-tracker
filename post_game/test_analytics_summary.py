"""Tests for the season-view summary projection.

The season view fans out over every finished game at once. Against the full
analytics docs that was ~5 MB for seven games (~3.4 MB of it
`identity_assignments`, which the view never reads) and it opened to a black
screen on a phone. These tests pin the projection that fixed it: small, and
carrying exactly the fields the view consumes.
"""

from __future__ import annotations

from post_game.firestore_io import (_SUMMARY_KEYS, _SUMMARY_PLAYER_KEYS,
                                    write_analytics_summary)

# The five per-player fields the season view actually reads, measured off the
# source. If someone adds a column to that table they must widen the projection
# too, or the column silently renders undefined.
VIEW_READS = {"player_id", "minutes_played", "pct_defensive_third",
              "pct_middle_third", "pct_attacking_third"}


class _FakeDoc:
    def __init__(self, sink):
        self._sink = sink

    def collection(self, _name):
        return self

    def document(self, name):
        self._sink["doc_id"] = name
        return self

    def set(self, payload):
        self._sink["payload"] = payload


def _write(analytics: dict) -> dict:
    sink: dict = {}
    import post_game.firestore_io as fio
    orig = fio._team_doc
    fio._team_doc = lambda: _FakeDoc(sink)
    try:
        write_analytics_summary("g1", analytics)
    finally:
        fio._team_doc = orig
    return sink


def _full_doc() -> dict:
    return {
        "player_stats": [{
            "player_id": "p_a", "minutes_played": 27.6,
            "pct_defensive_third": 50.6, "pct_middle_third": 33.2,
            "pct_attacking_third": 16.2,
            # Fields the view does NOT read, and which dominate the size.
            "heatmap_grid": [0.0] * 96, "distance_m": 1234.5,
            "sprint_count": 7, "tracked_seconds": 400.0,
        }],
        "field_tilt": {"def_pct": 40.0, "mid_pct": 35.0, "att_pct": 25.0},
        "generated_at_ms": 1_700_000_000_000,
        # The bulk of a real doc, none of which the season view touches.
        "identity_assignments": [{"track_id": i} for i in range(4000)],
        "tracklets": [{"tracklet_id": i} for i in range(129)],
        "team_time_series": {"times_s": list(range(5000))},
        "broadcast_events": [{"t": i} for i in range(300)],
    }


def test_the_projection_carries_every_field_the_view_reads():
    p = _write(_full_doc())["payload"]
    assert set(p["player_stats"][0]) >= VIEW_READS


def test_the_projection_drops_the_bulk_keys():
    """identity_assignments alone was ~3.4 MB across the season."""
    p = _write(_full_doc())["payload"]
    for k in ("identity_assignments", "tracklets", "team_time_series",
              "broadcast_events"):
        assert k not in p


def test_per_player_heatmap_and_movement_fields_are_dropped():
    """96 floats per player per game, for a view that draws no heatmap."""
    s = _write(_full_doc())["payload"]["player_stats"][0]
    for k in ("heatmap_grid", "distance_m", "sprint_count", "tracked_seconds"):
        assert k not in s


def test_it_is_dramatically_smaller():
    import json
    full = _full_doc()
    p = _write(full)["payload"]
    assert len(json.dumps(p)) < len(json.dumps(full)) / 20


def test_click_stats_are_summarised_not_dropped():
    """The squad table marks which games are tagged, so the marker must survive —
    but the 96-float per-player heatmap must not."""
    full = _full_doc()
    full["click_stats"] = {
        "n_clicks": 649, "n_frames": 97, "median_pos_err_m": 1.7,
        "players": [{"player_id": "p_a", "n_clicks": 90, "avg_depth_m": 2.4,
                     "pct_defensive_third": 98.9, "pct_middle_third": 1.1,
                     "pct_attacking_third": 0.0,
                     "heatmap": [0.0] * 96}],
    }
    cs = _write(full)["payload"]["click_stats"]
    assert cs["n_clicks"] == 649
    assert cs["players"][0]["n_clicks"] == 90
    assert "heatmap" not in cs["players"][0]


def test_the_oriented_flag_survives_the_projection():
    """Load-bearing for cross-game pooling.

    When the keeper's median sits mid-pitch the orientation resolver refuses
    rather than guess, and that game's depth figures are in an undefined frame.
    A season average that silently includes it mirrors half its contribution, so
    the flag must reach the client that does the pooling.
    """
    full = _full_doc()
    full["click_stats"] = {"n_clicks": 10, "oriented": False, "players": []}
    assert _write(full)["payload"]["click_stats"]["oriented"] is False


def test_a_doc_without_optional_keys_is_still_written():
    """An older or partial doc must summarise, not raise."""
    p = _write({"player_stats": []})["payload"]
    assert p["player_stats"] == []
    assert "click_stats" not in p


def test_it_writes_to_the_summary_doc_id_not_over_v1():
    """A projection that overwrote v1 would destroy the film room."""
    assert _write(_full_doc())["doc_id"] == "summary"


def test_the_declared_key_lists_stay_in_sync_with_the_view():
    assert set(_SUMMARY_PLAYER_KEYS) == VIEW_READS
    assert "player_stats" in _SUMMARY_KEYS


if __name__ == "__main__":
    import traceback
    bad = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            try:
                v()
                print(f"ok   {k}")
            except Exception:
                bad += 1
                print(f"FAIL {k}")
                traceback.print_exc()
    raise SystemExit(1 if bad else 0)
