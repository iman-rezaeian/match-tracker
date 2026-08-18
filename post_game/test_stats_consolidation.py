"""Guards the consolidated STATS destination in soccer_team_app.jsx.

Stats used to live in four places on three routes, including two season-level
per-player tables that both showed GP and MIN without acknowledging each other.
They now live in one STATS view with SEASON / GAMES / TEAM tabs (see
STATS_CONSOLIDATION_PLAN.md).

These are structural assertions against the JSX source, not a render test — there
is no JS test harness in this repo (React loads from a CDN). They catch the
regressions that actually happened here: a second stats surface reappearing, a
retired movement metric coming back, and a per-player number falling back to the
tracked source when tags are absent.

The pooling arithmetic is mirrored in Python and checked numerically, the same
approach test_score_mirror.py takes for the scorer.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JSX = REPO / "soccer_team_app.jsx"


def _src() -> str:
    return JSX.read_text()


def _component(name: str) -> str:
    """Source text of one top-level `function name(...)` declaration."""
    src = _src()
    start = src.index(f"function {name}(")
    nxt = src.find("\nfunction ", start + 1)
    return src[start:nxt if nxt != -1 else len(src)]


def _strip_comments(src: str) -> str:
    """Drop /* */ and // comments.

    Needed because the retired-metric names are DOCUMENTED at their removal sites
    — a test that forbids the string outright would forbid explaining the
    decision, which is worse than the regression it guards.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


# --------------------------------------------------------------------------
# One destination
# --------------------------------------------------------------------------

def test_the_old_season_modal_is_gone():
    """SeasonAnalyticsView was a second season screen, opened from FILM ROOM."""
    src = _src()
    assert "function SeasonAnalyticsView" not in src
    # A comment may mention it as history; a JSX element may not.
    assert "<SeasonAnalyticsView" not in src


def test_film_room_no_longer_hosts_a_season_rollup():
    fr = _strip_comments(_component("FilmRoomView"))
    assert "showSeason" not in fr
    assert "SEASON ANALYTICS" not in fr


def test_stats_view_has_the_three_tabs():
    sv = _component("StatsView")
    for tab in ("'season'", "'games'", "'team'"):
        assert tab in sv, f"missing tab {tab}"
    assert "SeasonPlayersTab" in sv
    assert "SeasonGamesTab" in sv
    assert "SeasonTeamTab" in sv


def test_the_per_game_panel_is_reachable_from_stats():
    """The per-game deck used to be reachable only through FILM ROOM."""
    assert "AnalyticsPanel" in _component("StatsView")


# --------------------------------------------------------------------------
# The source boundary — the point of the merge
# --------------------------------------------------------------------------

def test_the_merged_table_labels_both_sources():
    """The coach must see which columns are taps and which are tags."""
    t = _component("SeasonPlayersTab")
    assert "FROM YOUR TAPS" in t
    assert "FROM YOUR TAGS" in t


def test_an_untagged_player_shows_no_position_not_a_tracked_one():
    """The whole consolidation rule: no silent fallback to the bad source."""
    t = _strip_comments(_component("SeasonPlayersTab"))
    # The "not tagged" branch must be guarded by the ABSENCE of tagged data, not
    # by some other condition — a mutation to `false ? ...` makes the placeholder
    # unreachable and silently shows a bar built from whatever is in scope.
    assert re.search(r"r\.avgDefPct == null \?", t), \
        "the untagged placeholder is not gated on missing tag data"
    assert "not tagged" in t
    # A tracked per-player positional field must not appear in this table.
    for bad in ("heatmap_grid", "distance_m", "sprint_count", "tracked_seconds"):
        assert bad not in t, f"tracked field {bad} leaked into the merged table"


def test_retired_movement_metrics_stay_retired_across_the_whole_app():
    """These were removed for carrying ~23% wrong-child contamination.

    Checked against comment-stripped source: the names appear in the comments that
    record WHY they were removed, and those should stay.
    """
    src = _strip_comments(_src())
    for label in ("AVG km/h", "M/MIN", "Dist/g", "Spr/g", "KM TOTAL"):
        assert label not in src, f"retired metric {label!r} is back on a screen"


def test_tag_columns_average_over_tagged_games_only():
    hook = _component("useSeasonAnalytics")
    # The separate denominator is what keeps a tagged game's thirds from being
    # diluted by untagged games that contribute nothing.
    assert "r.tagged ? r.attPct / r.tagged" in hook


# --------------------------------------------------------------------------
# Orientation — a wrong flip mirrors half a game
# --------------------------------------------------------------------------

def test_unoriented_games_are_excluded_from_pooling():
    """Teams switch ends; the pipeline refuses to guess when the keeper is
    mid-pitch, and pooling such a game would mirror half its contribution."""
    assert "cs.oriented === false" in _component("useSeasonAnalytics")
    assert "cs.oriented === false" in _component("usePlayerSeasonHeatmap")


# --------------------------------------------------------------------------
# The season heatmap must not refetch the whole season
# --------------------------------------------------------------------------

def test_the_detail_sheet_fetches_only_the_players_tagged_games():
    """Fanning out over every full doc is what caused the black screen."""
    sv = _component("StatsView")
    assert "taggedGames={finished.filter(" in sv
    assert "season.clickByGame[g.id]" in sv


def test_the_heatmap_hook_only_runs_when_opened():
    h = _component("usePlayerSeasonHeatmap")
    assert "if (!open || !playerId" in h


# --------------------------------------------------------------------------
# Pooling arithmetic, mirrored
# --------------------------------------------------------------------------

def _pool(grids: list[tuple[list[float], int]]) -> list[float]:
    """Python mirror of usePlayerSeasonHeatmap's weighted pool.

    Each per-game grid is already normalised to sum 1, so an unweighted mean
    would give a 12-click game the same say as a 90-click one. Weight by click
    count, then renormalise.
    """
    acc = [0.0] * len(grids[0][0])
    for grid, w in grids:
        for i, v in enumerate(grid):
            acc[i] += v * w
    total = sum(acc) or 1.0
    return [v / total for v in acc]


def test_pooled_grid_is_normalised():
    a = [0.5, 0.5, 0.0, 0.0]
    b = [0.0, 0.0, 0.25, 0.75]
    out = _pool([(a, 10), (b, 30)])
    assert abs(sum(out) - 1.0) < 1e-9


def test_pooling_is_weighted_by_click_count():
    """A 90-click game must move the map more than a 10-click game."""
    deep = [1.0, 0.0]
    high = [0.0, 1.0]
    out = _pool([(deep, 10), (high, 90)])
    assert out[1] > out[0]
    assert abs(out[1] - 0.9) < 1e-9


def test_a_single_game_pools_to_itself():
    g = [0.1, 0.2, 0.3, 0.4]
    assert _pool([(g, 42)]) == g


def test_the_jsx_weighting_matches_this_mirror():
    """If the JSX drops the weight, the mirror above stops describing it."""
    h = _component("usePlayerSeasonHeatmap")
    assert "me.n_clicks || 1" in h
    assert re.search(r"acc\[i\] \+= \(me\.heatmap\[i\] \|\| 0\) \* w", h)
    assert "acc.map(v => v / total)" in h


def test_mismatched_grid_shapes_are_skipped_not_resampled():
    """Summing cell-wise across different shapes would silently smear the map."""
    h = _component("usePlayerSeasonHeatmap")
    assert "shape[0] !== rows || shape[1] !== cols" in h


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
