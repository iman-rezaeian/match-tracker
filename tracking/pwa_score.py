"""Exact Python replication of the PWA performance-score path (scoring v2).

Mirrors soccer_team_app.jsx: playerSeconds(), gkExtrasForGame(),
mergeWeights(), pillarPoints(), computePerformanceScore(), the per-game score
path (GameDetail) and the season path (StatsView seasonScores). Used by
audit_player_score.py and baseline_snapshot.py so both tools agree with the
app to the displayed decimal.

v2 (2026-06) scoring semantics replicated here:
* Empirical-Bayes SHRINKAGE: rates blended with `shrinkMinutes` virtual
  minutes of squad-average production (squad prior computed with outfield
  values for everyone).
* INV cleanup: TURNOVER / DUEL_LOSE / FOUL_BY and own goals no longer count
  toward Involvement.
* Clean sheet PRO-RATED by secondsAsGK / gameSeconds (60 s floor removed).
* SEASON score: per-game pillar POINTS weighted by game type
  (weights.gameTypes keyed by lowercased game.tournament; scrimmage 0.5 by
  default), summed over games, divided by weighted minutes, shrunk toward
  the squad season rate. Built from per-game points, so per-game gk blending
  and own goals are handled exactly like the game view (the v1 pooled filter
  silently dropped OPP_GOAL.ownGoalById from season scores — fixed).

Quirks still replicated FAITHFULLY:
* JS Math.round is half-up; js_round_* reproduce it.
* playerSeconds returns 0 when the game has no startingLineup.

Games/roster are plain dicts as returned by Firestore (same field names the
PWA reads), NOT post_game.firestore_io dataclasses.
"""

from __future__ import annotations

import math
import time
from typing import Optional

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
KNOWN_NONSILENT_EVENTS = frozenset({
    "GOAL", "ASSIST", "KEY_PASS", "SAVE", "SHOT_ON", "SHOT_OFF", "BLOCK",
    "BALL_WIN", "CLEAR", "KICK_OUT", "DUEL_WIN", "DUEL_LOSE", "GIVE_GO",
    "GATES", "TURNOVER", "HOLDS_BALL", "OPP_GOAL",
    "FOUL_BY", "FOUL_ON", "PEN_CONCEDED", "PEN_AWARDED",
})

# v2: mistake events already priced in DEF/DEC earn no Involvement credit.
INV_EXCLUDED = frozenset({"TURNOVER", "DUEL_LOSE", "FOUL_BY"})

# Pressure multiplier (plan 4.3): positive DEC-pillar actions under pressure are
# scaled by PRESSURE_DEC_MULT. Mirrors soccer_team_app.jsx exactly.
PRESSURE_DEC_MULT = 1.5

SCORING_VERSION = 2

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
    "shrinkMinutes": 12,
    "gameTypes": {"scrimmage": 0.5, "festival": 0.75, "default": 1.0},
}


def merge_weights(w: Optional[dict]) -> dict:
    w = w or {}
    try:
        shrink = float(w.get("shrinkMinutes"))
        if math.isnan(shrink):
            raise ValueError
    except (TypeError, ValueError):
        shrink = DEFAULT_WEIGHTS["shrinkMinutes"]
    return {
        "points":   {**DEFAULT_WEIGHTS["points"],   **(w.get("points") or {})},
        "gkPoints": {**DEFAULT_WEIGHTS["gkPoints"], **(w.get("gkPoints") or {})},
        "pillars": {
            "outfield": {**DEFAULT_WEIGHTS["pillars"]["outfield"], **((w.get("pillars") or {}).get("outfield") or {})},
            "gk":       {**DEFAULT_WEIGHTS["pillars"]["gk"],       **((w.get("pillars") or {}).get("gk") or {})},
        },
        "shrinkMinutes": shrink,
        "gameTypes": {**DEFAULT_WEIGHTS["gameTypes"], **(w.get("gameTypes") or {})},
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
    # v2: pro-rated clean sheet (60s floor removed).
    game_seconds = max(1.0, ((end_ts or 0) - (start_ts or 0)) / 1000)
    clean_sheets = (min(1.0, seconds_as_gk / game_seconds)
                    if (conceded == 0 and game.get("status") == "finished") else 0)
    return {
        "oppGoalsConceded": conceded,
        "concededPenalty": conceded_penalty,
        "cleanSheets": clean_sheets,
        "secondsAsGK": seconds_as_gk,
    }


# --- pillarPoints (raw points, no rate) --------------------------------------

def pillar_points(player_id: str, events: list[dict], f: float,
                  gk_extras: Optional[dict], W: dict) -> dict:
    c: dict[str, int] = {}
    c_pressure: dict[str, int] = {}
    partner_count = 0
    partner_pressure_count = 0
    own_goals = 0
    inv_count = 0
    for e in events or []:
        if e.get("type") not in KNOWN_NONSILENT_EVENTS:
            continue
        under_pressure = e.get("pressure") == "pressure"
        if e.get("playerId") == player_id:
            c[e["type"]] = c.get(e["type"], 0) + 1
            if under_pressure:
                c_pressure[e["type"]] = c_pressure.get(e["type"], 0) + 1
            if e["type"] not in INV_EXCLUDED:
                inv_count += 1
        if e.get("type") == "GIVE_GO" and e.get("partnerId") == player_id:
            partner_count += 1
            if under_pressure:
                partner_pressure_count += 1
            inv_count += 1
        if e.get("type") == "OPP_GOAL" and e.get("ownGoalById") == player_id:
            own_goals += 1  # costs DEF points; NOT involvement (v2)
    po, pg = W["points"], W["gkPoints"]

    def pt(k: str) -> float:
        a = po.get(k, 0)
        return a + f * (pg.get(k, 0) - a)

    g = lambda k: c.get(k, 0)
    atk = (g("GOAL") * pt("GOAL_atk") + g("ASSIST") * pt("ASSIST_atk")
           + g("KEY_PASS") * pt("KEY_PASS_atk") + g("SHOT_ON") * pt("SHOT_ON_atk")
           + g("SHOT_OFF") * pt("SHOT_OFF_atk") + g("FOUL_ON") * pt("FOUL_ON_atk")
           + g("PEN_AWARDED") * pt("PEN_AWARDED_atk"))
    conceded_penalty = ((gk_extras or {}).get("concededPenalty") or 0) if f > 0 else 0
    clean_sheets = ((gk_extras or {}).get("cleanSheets") or 0) if f > 0 else 0
    dfn = (g("SAVE") * pt("SAVE_def") + g("BLOCK") * pt("BLOCK_def")
           + g("BALL_WIN") * pt("BALL_WIN_def") + g("CLEAR") * pt("CLEAR_def")
           + g("KICK_OUT") * pt("KICK_OUT_def") + g("DUEL_WIN") * pt("DUEL_WIN_def")
           + g("DUEL_LOSE") * pt("DUEL_LOSE_def") + g("FOUL_BY") * pt("FOUL_BY_def")
           + g("PEN_CONCEDED") * pt("PEN_CONCEDED_def") + own_goals * pt("OWN_GOAL_def")
           + ((-conceded_penalty + clean_sheets * pt("CLEAN_SHEET_def")) if f > 0 else 0))
    dec = (g("GIVE_GO") * pt("GIVE_GO_dec") + partner_count * pt("GIVE_GO_PARTNER_dec")
           + g("GATES") * pt("GATES_dec") + g("KEY_PASS") * pt("KEY_PASS_dec")
           + g("ASSIST") * pt("ASSIST_dec") + g("HOLDS_BALL") * pt("HOLDS_BALL_dec")
           + g("TURNOVER") * pt("TURNOVER_dec"))
    # Pressure bonus: extra (mult-1) DEC points for positive DEC actions under
    # pressure; DEC mistakes (HOLDS_BALL/TURNOVER) left at base cost.
    gp = lambda k: c_pressure.get(k, 0)
    dec_pressure_bonus = (PRESSURE_DEC_MULT - 1) * (
        gp("GIVE_GO") * pt("GIVE_GO_dec") + partner_pressure_count * pt("GIVE_GO_PARTNER_dec")
        + gp("GATES") * pt("GATES_dec") + gp("KEY_PASS") * pt("KEY_PASS_dec")
        + gp("ASSIST") * pt("ASSIST_dec"))
    return {"atk": atk, "def": dfn, "dec": dec + dec_pressure_bonus, "inv": inv_count,
            "_counts": c, "_partner": partner_count, "_own_goals": own_goals}


def compute_squad_rates(per_player: list[dict], events: list[dict], W: dict) -> dict:
    """Squad-average per-20-min pillar rates (the shrinkage prior). Outfield
    values for everyone. per_player = [{playerId, minutes}]."""
    tot = {"atk": 0.0, "def": 0.0, "dec": 0.0, "inv": 0.0}
    tot_min = 0.0
    for row in per_player:
        minutes = row.get("minutes") or 0
        if minutes <= 0:
            continue
        p = pillar_points(row["playerId"], events, 0, None, W)
        for k in tot:
            tot[k] += p[k]
        tot_min += minutes
    ph = max(tot_min, 1.0) / 20
    return {k: v / ph for k, v in tot.items()}


# --- computePerformanceScore -------------------------------------------------

def compute_performance_score(
    player_id: str,
    events: list[dict],
    minutes_played: float,
    gk_fraction: float = 0.0,
    gk_extras: Optional[dict] = None,
    weights: Optional[dict] = None,
    squad_rates: Optional[dict] = None,
) -> dict:
    if minutes_played <= 0:
        return {"overall": 0, "attacking": 0, "defending": 0, "decisions": 0,
                "involvement": 0, "_counts": {}, "_partner": 0, "_own_goals": 0}
    W = merge_weights(weights)
    try:
        f = max(0.0, min(1.0, float(gk_fraction or 0)))
    except (TypeError, ValueError):
        f = 0.0
    pts = pillar_points(player_id, events, f, gk_extras, W)
    M = max(0.0, float(W["shrinkMinutes"])) if squad_rates else 0.0

    def rate(p: float, sq: Optional[float]) -> float:
        if M > 0:
            return (p + (M / 20) * (sq or 0)) / ((minutes_played + M) / 20)
        return p / (minutes_played / 20)

    attacking = rate(pts["atk"], (squad_rates or {}).get("atk"))
    defending = rate(pts["def"], (squad_rates or {}).get("def"))
    decisions = rate(pts["dec"], (squad_rates or {}).get("dec"))
    involvement = rate(pts["inv"], (squad_rates or {}).get("inv"))
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
        "_counts": pts["_counts"],
        "_partner": pts["_partner"],
        "_own_goals": pts["_own_goals"],
    }


# --- Per-game path (GameDetail) ----------------------------------------------

def served_as_gk(player_id: str, game: dict) -> bool:
    return (game.get("gkPlayerId") == player_id
            or any(ch.get("gkPlayerId") == player_id for ch in (game.get("gkChanges") or [])))


def game_squad_rates(game: dict, roster: list[dict], weights: Optional[dict] = None) -> dict:
    """Shrinkage prior for one game — mirrors GameDetail's computeSquadRates call."""
    W = merge_weights(weights)
    per_player = []
    for p in roster:
        sec = player_seconds(p["id"], game)
        if sec > 0:
            per_player.append({"playerId": p["id"], "minutes": sec / 60})
    return compute_squad_rates(per_player, game.get("events") or [], W)


def per_game_score(player_id: str, game: dict, weights: Optional[dict] = None,
                   roster: Optional[list[dict]] = None) -> Optional[dict]:
    """GameDetail's PERFORMANCE SCORES row. Pass `roster` to enable v2
    shrinkage (the app always has it; omitting disables the prior)."""
    sec = player_seconds(player_id, game)
    if sec <= 0:
        return None
    minutes = js_round_int(sec / 60)
    was_gk = served_as_gk(player_id, game)
    gk_extras = gk_extras_for_game(player_id, game) if was_gk else None
    gk_fraction = ((gk_extras.get("secondsAsGK") or 0) / sec) if (was_gk and sec > 0) else 0
    squad_rates = game_squad_rates(game, roster, weights) if roster else None
    score = compute_performance_score(
        player_id, game.get("events") or [], minutes, gk_fraction, gk_extras,
        weights, squad_rates,
    )
    score["_minutes"] = minutes
    score["_seconds"] = sec
    score["_gk_fraction"] = round(gk_fraction, 4)
    score["_was_gk"] = was_gk
    if gk_extras:
        score["_gk_extras"] = gk_extras
    return score


# --- Season path (StatsView, v2) ----------------------------------------------

def _type_weight(game: dict, W: dict) -> float:
    t = str(game.get("tournament") or "").lower()
    gt = W["gameTypes"]
    return float(gt[t]) if t in gt else float(gt["default"])


def season_score(player_id: str, finished_games: list[dict],
                 roster_player: Optional[dict] = None,
                 weights: Optional[dict] = None,
                 roster: Optional[list[dict]] = None) -> Optional[dict]:
    """StatsView seasonScores entry (v2). The squad prior needs the full
    roster; pass it for exact parity (falls back to just this player)."""
    W = merge_weights(weights)
    M = max(0.0, float(W["shrinkMinutes"]))
    squad = roster if roster else ([roster_player] if roster_player else [{"id": player_id}])
    sums: dict[str, dict] = {}
    squad_tot = {"atk": 0.0, "def": 0.0, "dec": 0.0, "inv": 0.0}
    squad_min = 0.0
    for g in finished_games:
        w = _type_weight(g, W)
        if not w > 0:
            continue
        ev = g.get("events") or []
        for p in squad:
            pid = p["id"]
            sec = player_seconds(pid, g)
            if sec <= 0:
                continue
            minutes = sec / 60
            sgk = served_as_gk(pid, g)
            gx = gk_extras_for_game(pid, g) if sgk else None
            f = min(1.0, ((gx or {}).get("secondsAsGK") or 0) / sec) if (sgk and sec > 0) else 0
            pts = pillar_points(pid, ev, f, gx, W)
            row = sums.setdefault(pid, {"atk": 0.0, "def": 0.0, "dec": 0.0, "inv": 0.0,
                                        "wmin": 0.0, "wgkmin": 0.0})
            for k in ("atk", "def", "dec", "inv"):
                row[k] += w * pts[k]
            row["wmin"] += w * minutes
            row["wgkmin"] += w * minutes * f
            pop = pillar_points(pid, ev, 0, None, W)
            for k in ("atk", "def", "dec", "inv"):
                squad_tot[k] += w * pop[k]
            squad_min += w * minutes
    row = sums.get(player_id)
    if not row or row["wmin"] <= 0:
        return None
    sq_ph = max(squad_min, 1.0) / 20
    squad_rates = {k: v / sq_ph for k, v in squad_tot.items()}
    rate = lambda p, sq: (p + (M / 20) * sq) / ((row["wmin"] + M) / 20)
    attacking = rate(row["atk"], squad_rates["atk"])
    defending = rate(row["def"], squad_rates["def"])
    decisions = rate(row["dec"], squad_rates["dec"])
    involvement = rate(row["inv"], squad_rates["inv"])
    f = min(1.0, row["wgkmin"] / row["wmin"])
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
        "_minutes": js_round_int(row["wmin"]),
        "_weighted_minutes": round(row["wmin"], 1),
        "_gk_fraction": round(f, 4),
        "_was_gk_any": f > 0,
    }
