"""Exact Python replication of the PWA performance-score path.

Mirrors soccer_team_app.jsx: playerSeconds(), gkExtrasForGame(),
mergeWeights(), computePerformanceScore(), the per-game score path
(GameDetail tally → score rows) and the season path (StatsView
seasonScores). Used by audit_player_score.py and baseline_snapshot.py so
both tools agree with the app to the displayed decimal.

Quirks replicated FAITHFULLY (do not "fix" here — the point is parity):

* JS Math.round is half-up; Python round() is banker's. js_round_*
  reproduce JS behavior (Math.round(-2.5) === -2).
* SEASON aggregation collects only the player's own events + GIVE_GO
  partner events, so OPP_GOAL.ownGoalById own goals never reach the season
  score even though they DO count per-game (full event list is passed
  there). Known PWA inconsistency — tracked in ANALYTICS_IMPROVEMENT_PLAN.md.
* Per-game gkExtras only when the player served as GK in THAT game; season
  gkExtras when GK in any finished game OR roster position == 'GK'.
* playerSeconds returns 0 when the game has no startingLineup.

Games/roster are plain dicts as returned by Firestore (same field names the
PWA reads), NOT post_game.firestore_io dataclasses.
"""

from __future__ import annotations

import math
import time
from typing import Any, Optional

# --- JS-compatible rounding ------------------------------------------------

def js_round_int(n: float) -> int:
    """JS Math.round: half-up toward +infinity."""
    return int(math.floor(n + 0.5))


def js_round1(n: float) -> float:
    """JS Math.round(n * 10) / 10."""
    return math.floor(n * 10 + 0.5) / 10


def _now_ms() -> int:
    return int(time.time() * 1000)


# --- EVENT_TYPES (non-silent ids only) — mirrors the jsx map ---------------
# POSITION is silent; SUB / GK_CHANGE are not in EVENT_TYPES at all. Only
# these types feed computePerformanceScore.
KNOWN_NONSILENT_EVENTS = frozenset({
    "GOAL", "ASSIST", "KEY_PASS", "SAVE", "SHOT_ON", "SHOT_OFF", "BLOCK",
    "BALL_WIN", "CLEAR", "KICK_OUT", "DUEL_WIN", "DUEL_LOSE", "GIVE_GO",
    "GATES", "TURNOVER", "HOLDS_BALL", "OPP_GOAL",
    "FOUL_BY", "FOUL_ON", "PEN_CONCEDED", "PEN_AWARDED",
})

# --- DEFAULT_WEIGHTS — mirrors the jsx constant ----------------------------

DEFAULT_WEIGHTS: dict = {
    "points": {
        "GOAL_atk": 10, "ASSIST_atk": 8, "KEY_PASS_atk": 4, "SHOT_ON_atk": 3, "SHOT_OFF_atk": 1,
        "SAVE_def": 7, "BLOCK_def": 5, "BALL_WIN_def": 5, "DUEL_WIN_def": 2, "DUEL_LOSE_def": -2,
        "CLEAR_def": 3, "KICK_OUT_def": 1,
        "GIVE_GO_dec": 6, "GIVE_GO_PARTNER_dec": 3, "GATES_dec": 4, "KEY_PASS_dec": 3, "ASSIST_dec": 3,
        "HOLDS_BALL_dec": -4, "TURNOVER_dec": -4, "CLEAN_SHEET_def": 8,
        "FOUL_ON_atk": 2, "FOUL_BY_def": -2,
        "PEN_AWARDED_atk": 6, "PEN_CONCEDED_def": -8,
        "OWN_GOAL_def": -10,
    },
    "gkPoints": {
        "GOAL_atk": 10, "ASSIST_atk": 8, "KEY_PASS_atk": 10, "SHOT_ON_atk": 3, "SHOT_OFF_atk": 1,
        "SAVE_def": 10, "BLOCK_def": 5, "BALL_WIN_def": 5, "DUEL_WIN_def": 2, "DUEL_LOSE_def": -2,
        "CLEAR_def": 3, "KICK_OUT_def": 1,
        "GIVE_GO_dec": 6, "GIVE_GO_PARTNER_dec": 3, "GATES_dec": 4, "KEY_PASS_dec": 6, "ASSIST_dec": 3,
        "HOLDS_BALL_dec": -4, "TURNOVER_dec": -4, "CLEAN_SHEET_def": 8,
        "FOUL_ON_atk": 2, "FOUL_BY_def": -2,
        "PEN_AWARDED_atk": 6, "PEN_CONCEDED_def": -8,
        "OWN_GOAL_def": -10,
    },
    "pillars": {
        "outfield": {"atk": 30, "def": 25, "dec": 30, "inv": 15},
        "gk":       {"atk": 10, "def": 55, "dec": 25, "inv": 10},
    },
}


def merge_weights(w: Optional[dict]) -> dict:
    w = w or {}
    return {
        "points":   {**DEFAULT_WEIGHTS["points"],   **(w.get("points") or {})},
        "gkPoints": {**DEFAULT_WEIGHTS["gkPoints"], **(w.get("gkPoints") or {})},
        "pillars": {
            "outfield": {**DEFAULT_WEIGHTS["pillars"]["outfield"], **((w.get("pillars") or {}).get("outfield") or {})},
            "gk":       {**DEFAULT_WEIGHTS["pillars"]["gk"],       **((w.get("pillars") or {}).get("gk") or {})},
        },
    }


# --- playerSeconds ----------------------------------------------------------

def player_seconds(player_id: str, game: dict) -> int:
    if not game.get("startingLineup"):
        return 0
    starting = player_id in game["startingLineup"]
    subs = sorted(
        [e for e in (game.get("events") or []) if e.get("type") == "SUB"],
        key=lambda e: e.get("at", 0),
    )
    intervals: list[list] = []
    on_since = game.get("startedAt") if starting else None
    for sub in subs:
        if sub.get("playerId") == player_id and on_since is not None:
            intervals.append([on_since, sub.get("at")])
            on_since = None
        if sub.get("subOnPlayerId") == player_id and on_since is None:
            on_since = sub.get("at")
    if on_since is not None:
        end_ts = (game.get("endedAt")
                  if game.get("status") == "finished" and game.get("endedAt")
                  else _now_ms())
        intervals.append([on_since, end_ts])
    pauses = [[p.get("startedAt"), p.get("endedAt") or _now_ms()]
              for p in (game.get("pausePeriods") or [])]
    total = 0.0
    for s, e in intervals:
        if s is None or e is None:
            continue
        secs = (e - s) / 1000
        for ps, pe in pauses:
            if ps is None:
                continue
            o0, o1 = max(s, ps), min(e, pe)
            if o1 > o0:
                secs -= (o1 - o0) / 1000
        total += max(0.0, secs)
    return int(math.floor(total))


# --- gkExtrasForGame ---------------------------------------------------------

def gk_extras_for_game(player_id: str, game: dict) -> dict:
    start_ts = game.get("startedAt")
    end_ts = (game.get("endedAt")
              if game.get("status") == "finished" and game.get("endedAt")
              else _now_ms())
    gk_changes = game.get("gkChanges") or []
    if game.get("gkPlayerId") or gk_changes:
        segments = []
        current = game.get("gkPlayerId") or None
        seg_start = start_ts
        for c in sorted(gk_changes, key=lambda c: c.get("at", 0)):
            segments.append({"from": seg_start, "to": c.get("at"), "gkPlayerId": current})
            current = c.get("gkPlayerId") or None
            seg_start = c.get("at")
        segments.append({"from": seg_start, "to": end_ts, "gkPlayerId": current})
        gk_timeline = [s for s in segments if s["gkPlayerId"] == player_id]
    else:
        # Legacy fallback: all on-field time counts as GK time.
        subs = sorted(
            [e for e in (game.get("events") or []) if e.get("type") == "SUB"],
            key=lambda e: e.get("at", 0),
        )
        intervals = []
        starting = player_id in (game.get("startingLineup") or [])
        on_since = start_ts if starting else None
        for sub in subs:
            if sub.get("playerId") == player_id and on_since is not None:
                intervals.append({"from": on_since, "to": sub.get("at")})
                on_since = None
            if sub.get("subOnPlayerId") == player_id and on_since is None:
                on_since = sub.get("at")
        if on_since is not None:
            intervals.append({"from": on_since, "to": end_ts})
        gk_timeline = intervals
    if not gk_timeline:
        return {"oppGoalsConceded": 0, "concededPenalty": 0, "cleanSheets": 0, "secondsAsGK": 0}
    conceded = 0
    conceded_penalty = 0
    for e in (game.get("events") or []):
        if e.get("type") != "OPP_GOAL":
            continue
        at = e.get("at")
        if at is None:
            continue
        if any(seg["from"] is not None and seg["to"] is not None
               and seg["from"] <= at <= seg["to"] for seg in gk_timeline):
            conceded += 1
            fault = e.get("gkFault")
            if fault == "gk":
                conceded_penalty += 6
            elif fault == "unstoppable":
                conceded_penalty += 0
            else:
                conceded_penalty += 3
    seconds_as_gk = sum(
        max(0.0, (seg["to"] - seg["from"]) / 1000)
        for seg in gk_timeline
        if seg["from"] is not None and seg["to"] is not None
    )
    clean_sheets = 1 if (conceded == 0 and seconds_as_gk >= 60 and game.get("status") == "finished") else 0
    return {
        "oppGoalsConceded": conceded,
        "concededPenalty": conceded_penalty,
        "cleanSheets": clean_sheets,
        "secondsAsGK": seconds_as_gk,
    }


# --- computePerformanceScore -------------------------------------------------

def compute_performance_score(
    player_id: str,
    events: list[dict],
    minutes_played: float,
    gk_fraction: float = 0.0,
    gk_extras: Optional[dict] = None,
    weights: Optional[dict] = None,
) -> dict:
    """Returns {overall, attacking, defending, decisions, involvement} rounded
    to 1 decimal like the PWA, plus _counts/_partner/_own_goals diagnostics."""
    if minutes_played <= 0:
        return {"overall": 0, "attacking": 0, "defending": 0, "decisions": 0,
                "involvement": 0, "_counts": {}, "_partner": 0, "_own_goals": 0}
    gk_extras = gk_extras or {}
    W = merge_weights(weights)
    try:
        f = max(0.0, min(1.0, float(gk_fraction or 0)))
    except (TypeError, ValueError):
        f = 0.0
    per_half = minutes_played / 20
    c: dict[str, int] = {}
    partner_count = 0
    own_goals = 0
    for e in events:
        if e.get("type") not in KNOWN_NONSILENT_EVENTS:
            continue  # excludes SUB/GK_CHANGE (not in EVENT_TYPES) + silent POSITION
        if e.get("playerId") == player_id:
            c[e["type"]] = c.get(e["type"], 0) + 1
        if e.get("type") == "GIVE_GO" and e.get("partnerId") == player_id:
            partner_count += 1
        if e.get("type") == "OPP_GOAL" and e.get("ownGoalById") == player_id:
            own_goals += 1

    po, pg = W["points"], W["gkPoints"]

    def pt(k: str) -> float:
        a = po.get(k, 0)
        return a + f * (pg.get(k, 0) - a)

    g = lambda k: c.get(k, 0)
    attacking = (
        g("GOAL") * pt("GOAL_atk") + g("ASSIST") * pt("ASSIST_atk")
        + g("KEY_PASS") * pt("KEY_PASS_atk") + g("SHOT_ON") * pt("SHOT_ON_atk")
        + g("SHOT_OFF") * pt("SHOT_OFF_atk") + g("FOUL_ON") * pt("FOUL_ON_atk")
        + g("PEN_AWARDED") * pt("PEN_AWARDED_atk")
    ) / per_half
    conceded_penalty = (gk_extras.get("concededPenalty") or 0) if f > 0 else 0
    clean_sheets = (gk_extras.get("cleanSheets") or 0) if f > 0 else 0
    defending = (
        g("SAVE") * pt("SAVE_def") + g("BLOCK") * pt("BLOCK_def")
        + g("BALL_WIN") * pt("BALL_WIN_def") + g("CLEAR") * pt("CLEAR_def")
        + g("KICK_OUT") * pt("KICK_OUT_def") + g("DUEL_WIN") * pt("DUEL_WIN_def")
        + g("DUEL_LOSE") * pt("DUEL_LOSE_def") + g("FOUL_BY") * pt("FOUL_BY_def")
        + g("PEN_CONCEDED") * pt("PEN_CONCEDED_def") + own_goals * pt("OWN_GOAL_def")
        + ((-conceded_penalty + clean_sheets * pt("CLEAN_SHEET_def")) if f > 0 else 0)
    ) / per_half
    decisions = (
        g("GIVE_GO") * pt("GIVE_GO_dec") + partner_count * pt("GIVE_GO_PARTNER_dec")
        + g("GATES") * pt("GATES_dec") + g("KEY_PASS") * pt("KEY_PASS_dec")
        + g("ASSIST") * pt("ASSIST_dec") + g("HOLDS_BALL") * pt("HOLDS_BALL_dec")
        + g("TURNOVER") * pt("TURNOVER_dec")
    ) / per_half
    total_events = sum(c.values()) + partner_count + own_goals
    involvement = total_events / per_half
    PO, PG = W["pillars"]["outfield"], W["pillars"]["gk"]
    pil = {k: PO[k] + f * (PG[k] - PO[k]) for k in ("atk", "def", "dec", "inv")}
    overall = (pil["atk"] * attacking + pil["def"] * defending
               + pil["dec"] * decisions + pil["inv"] * involvement) / 100
    return {
        "overall": js_round1(overall),
        "attacking": js_round1(attacking),
        "defending": js_round1(defending),
        "decisions": js_round1(decisions),
        "involvement": js_round1(involvement),
        "_counts": c,
        "_partner": partner_count,
        "_own_goals": own_goals,
    }


# --- Per-game path (GameDetail) ----------------------------------------------

def served_as_gk(player_id: str, game: dict) -> bool:
    return (game.get("gkPlayerId") == player_id
            or any(ch.get("gkPlayerId") == player_id for ch in (game.get("gkChanges") or [])))


def per_game_score(player_id: str, game: dict, weights: Optional[dict] = None) -> Optional[dict]:
    """GameDetail's PERFORMANCE SCORES row for one player. None if 0 seconds."""
    sec = player_seconds(player_id, game)
    if sec <= 0:
        return None
    minutes = js_round_int(sec / 60)
    was_gk = served_as_gk(player_id, game)
    gk_extras = gk_extras_for_game(player_id, game) if was_gk else None
    gk_fraction = ((gk_extras.get("secondsAsGK") or 0) / sec) if (was_gk and sec > 0) else 0
    score = compute_performance_score(
        player_id, game.get("events") or [], minutes, gk_fraction, gk_extras, weights,
    )
    score["_minutes"] = minutes
    score["_seconds"] = sec
    score["_gk_fraction"] = round(gk_fraction, 4)
    score["_was_gk"] = was_gk
    if gk_extras:
        score["_gk_extras"] = gk_extras
    return score


# --- Season path (StatsView) --------------------------------------------------

def season_score(player_id: str, finished_games: list[dict],
                 roster_player: Optional[dict] = None,
                 weights: Optional[dict] = None) -> Optional[dict]:
    """StatsView seasonScores entry for one player over finished games."""
    roster_player = roster_player or {}
    total_seconds = 0
    gk_seconds = 0.0
    was_gk_any = roster_player.get("position") == "GK"
    gx_agg = {"oppGoalsConceded": 0, "concededPenalty": 0, "cleanSheets": 0}
    all_events: list[dict] = []
    for g in finished_games:
        for e in (g.get("events") or []):
            if e.get("type") == "SUB":
                continue
            if e.get("playerId") == player_id:
                all_events.append(e)
            elif e.get("type") == "GIVE_GO" and e.get("partnerId") == player_id:
                all_events.append(e)
            # NOTE: OPP_GOAL.ownGoalById events do NOT make it in — faithful
            # replication of the PWA's season filter (own goals only count
            # per-game). See module docstring.
        sec = player_seconds(player_id, g)
        if sec > 0:
            total_seconds += sec
        if served_as_gk(player_id, g):
            was_gk_any = True
            gx = gk_extras_for_game(player_id, g)
            gk_seconds += gx["secondsAsGK"] or 0
            gx_agg["oppGoalsConceded"] += gx["oppGoalsConceded"]
            gx_agg["concededPenalty"] += gx["concededPenalty"]
            gx_agg["cleanSheets"] += gx["cleanSheets"]
    if total_seconds <= 0:
        return None
    minutes = js_round_int(total_seconds / 60)
    gk_fraction = (gk_seconds / total_seconds if total_seconds > 0
                   else (1 if roster_player.get("position") == "GK" else 0))
    score = compute_performance_score(
        player_id, all_events, minutes, gk_fraction,
        gx_agg if was_gk_any else None, weights,
    )
    score["_minutes"] = minutes
    score["_seconds"] = total_seconds
    score["_gk_fraction"] = round(gk_fraction, 4)
    score["_was_gk_any"] = was_gk_any
    if was_gk_any:
        score["_gk_extras"] = gx_agg
    return score
