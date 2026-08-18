"""Per-kid season rollup → teams/main/parentSeason/{playerId}.

The family app's HEATMAPS and MATCH STATS tiles read ONLY these docs; the
Firestore rules let a parent fetch just the playerIds on their own
allowedUsers row. Content policy (coach's standing rules):

  * Outcome stats only — goals, assists, GK saves, minutes. No INV/mistake
    counts, no performance score, no distance/speed (retired family).
  * The 12x8 heatmap ships per game only when tracking coverage clears
    COVERAGE_HEATMAP_MIN — a family should see "–" rather than a
    confidently-wrong map (coverage_frac trust dial: ≳0.5 solid, <0.25 sliver).
  * Every roster player gets a row per finished game, so absences render
    as the explicit "–" row the coach asked for (attended: false).

One code path serves both writers: the pipeline calls publish_parent_season()
at the end of a run, and scripts/backfill_parent_season.py loops it over all
finished games using the analytics docs already in Firestore.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from google.cloud import firestore

from . import config

log = logging.getLogger(__name__)

# Below this tracked-coverage fraction the per-game heatmap is withheld.
COVERAGE_HEATMAP_MIN = 0.30


def _first_name(name: Optional[str]) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else ""


def publish_parent_season(game_id: str, db: Optional[firestore.Client] = None) -> dict[str, Any]:
    """Upsert this game's row into every roster player's season doc."""
    db = db or firestore.Client(project=config.FIRESTORE_PROJECT_ID)
    team = db.collection("teams").document("main")

    game_snap = team.collection("games").document(game_id).get()
    if not game_snap.exists:
        return {"skipped": "game not found"}
    g = game_snap.to_dict() or {}
    if g.get("status") != "finished":
        return {"skipped": "game not finished"}

    roster = (team.get().to_dict() or {}).get("roster") or []
    an_snap = (team.collection("games").document(game_id)
               .collection("analytics").document(config.ANALYTICS_DOC_VERSION).get())
    an = an_snap.to_dict() if an_snap.exists else {}
    pstats = {p.get("player_id"): p for p in (an.get("player_stats") or [])}

    goals: dict[str, int] = {}
    assists: dict[str, int] = {}
    saves: dict[str, int] = {}
    for e in (g.get("events") or []):
        pid, etype = e.get("playerId"), e.get("type")
        if not pid:
            continue
        if etype == "GOAL":
            goals[pid] = goals.get(pid, 0) + 1
        elif etype == "ASSIST":
            assists[pid] = assists.get(pid, 0) + 1
        elif etype == "SAVE":
            saves[pid] = saves.get(pid, 0) + 1

    squad = set(g.get("squad") or g.get("startingLineup") or [])
    date = (g.get("date") or "")[:10]
    updated = 0
    for p in roster:
        pid = p.get("id")
        if not pid:
            continue
        st = pstats.get(pid) or {}
        minutes = float(st.get("minutes_played") or 0.0)
        attended = bool(pid in squad or minutes > 0
                        or pid in goals or pid in assists or pid in saves)
        coverage = float(st.get("coverage_frac") or 0.0)
        row: dict[str, Any] = {
            "gameId": game_id,
            "date": date,
            "opponent": g.get("opponent") or "Opponent",
            "tournament": g.get("tournament") or None,
            "ourScore": g.get("ourScore", 0),
            "oppScore": g.get("oppScore", 0),
            "attended": attended,
            # None (not 0) when the sub log gave us nothing — the UI renders
            # "–"; a literal 0 would read as "played zero minutes" to a family
            # that watched their kid play half the game.
            "minutes": round(minutes, 1) if (attended and minutes > 0) else None,
            "goals": goals.get(pid, 0),
            "assists": assists.get(pid, 0),
            "saves": saves.get(pid, 0),
            "coverage": round(coverage, 3),
        }
        grid = st.get("heatmap_grid")
        rows_n, cols_n = st.get("heatmap_grid_rows"), st.get("heatmap_grid_cols")
        if attended and grid and rows_n and cols_n and coverage >= COVERAGE_HEATMAP_MIN:
            row["heatmap"] = list(grid)
            row["heatmapRows"] = int(rows_n)
            row["heatmapCols"] = int(cols_n)

        ref = team.collection("parentSeason").document(pid)
        snap = ref.get()
        doc = snap.to_dict() if snap.exists else {}
        rows = [r for r in (doc.get("games") or []) if r.get("gameId") != game_id]
        rows.append(row)
        rows.sort(key=lambda r: ((r.get("date") or ""), (r.get("gameId") or "")))
        ref.set({
            "playerId": pid,
            "playerFirstName": _first_name(p.get("name")),
            "playerNumber": p.get("number") or "",
            "games": rows,
            "updatedAt": int(time.time() * 1000),
        })
        updated += 1

    log.info("parentSeason: %s → %d player docs", game_id, updated)
    return {"players": updated}
