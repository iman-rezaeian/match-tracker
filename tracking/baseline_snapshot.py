"""Phase 0 regression baseline — freeze identity + stats + PWA scores to JSON.

Dumps, for every finished game (or --games subset):
  * per-player PWA performance scores (per-game path, exact replication via
    tracking/pwa_score.py),
  * the identity table from the analytics doc (per-tracklet records + per-track
    status breakdown),
  * player_stats from the analytics doc,
  * identityOverrides from the game doc (coach ground-truth labels),
plus season scores for the whole roster, and a golden-game candidate summary
(keeper swaps, override counts, analytics presence).

Re-run after every Phase 1/2 change and diff against the previous snapshot:

    .venv-post-game/bin/python -m tracking.baseline_snapshot --label pre-phase1
    # ... make changes, re-run pipeline ...
    .venv-post-game/bin/python -m tracking.baseline_snapshot --label post-gk-fix
    diff tracking/outputs/baseline/pre-phase1/baseline.json \
         tracking/outputs/baseline/post-gk-fix/baseline.json

Output is deterministically ordered (sorted keys, sorted lists) so diffs are
clean. A consolidated identity_labels.json is also written for Phase 1
acceptance measurement (0.3).
"""

from __future__ import annotations

import argparse
import json
import datetime
from pathlib import Path

from post_game import firestore_io
from tracking import pwa_score

OUT_ROOT = Path(__file__).resolve().parent / "outputs" / "baseline"


def _get_analytics(db, game_id: str) -> dict | None:
    snap = db.document(f"teams/main/games/{game_id}/analytics/v1").get()
    return snap.to_dict() if snap.exists else None


def _identity_summary(analytics: dict) -> dict:
    """Compact, diff-stable view of the identity assignment state."""
    status_counts: dict[str, int] = {}
    player_track_minutes: dict[str, float] = {}
    for a in analytics.get("identity_assignments") or []:
        st = a.get("status") or "unknown"
        status_counts[st] = status_counts.get(st, 0) + 1
        pid = a.get("player_id")
        if pid:
            player_track_minutes[pid] = round(
                player_track_minutes.get(pid, 0.0) + float(a.get("minutes_on_field") or 0.0), 2,
            )
    tracklets = sorted(
        (
            {
                "tracklet_id": t.get("tracklet_id"),
                "player_id": t.get("player_id"),
                "confidence": t.get("confidence"),
                "status": t.get("status"),
                "minutes": t.get("minutes"),
            }
            for t in (analytics.get("tracklets") or [])
        ),
        key=lambda t: (t["tracklet_id"] is None, t["tracklet_id"]),
    )
    return {
        "track_status_counts": dict(sorted(status_counts.items())),
        "player_track_minutes": dict(sorted(player_track_minutes.items())),
        "tracklets": tracklets,
    }


def _player_stats_slice(analytics: dict) -> list[dict]:
    keep = (
        "player_id", "minutes_played", "distance_m", "top_speed_ms", "avg_speed_ms",
        "sprint_count", "sprint_distance_m",
        "pct_attacking_third", "pct_middle_third", "pct_defensive_third",
    )
    out = []
    for s in analytics.get("player_stats") or []:
        row = {k: s.get(k) for k in keep}
        for k, v in row.items():
            if isinstance(v, float):
                row[k] = round(v, 2)
        out.append(row)
    return sorted(out, key=lambda r: str(r.get("player_id")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", nargs="*", default=None,
                    help="Game ids to snapshot (default: all finished games)")
    ap.add_argument("--label", default=None,
                    help="Snapshot folder name (default: baseline-<YYYYMMDD-HHMM>)")
    args = ap.parse_args()

    db = firestore_io._client()
    team = db.document("teams/main").get().to_dict() or {}
    roster = team.get("roster") or []
    weights = team.get("weights")
    roster_by_id = {p["id"]: p for p in roster}

    all_games = [dict(g.to_dict(), id=g.id) for g in db.collection("teams/main/games").stream()]
    finished = sorted(
        [g for g in all_games if g.get("status") == "finished"],
        key=lambda x: x.get("date", ""),
    )
    selected = finished if not args.games else [g for g in finished if g["id"] in set(args.games)]
    if args.games and len(selected) != len(args.games):
        missing = set(args.games) - {g["id"] for g in selected}
        raise SystemExit(f"Not found / not finished: {sorted(missing)}")

    label = args.label or ("baseline-" + datetime.datetime.now().strftime("%Y%m%d-%H%M"))
    out_dir = OUT_ROOT / label
    out_dir.mkdir(parents=True, exist_ok=True)

    games_out: dict[str, dict] = {}
    labels_out: dict[str, dict] = {}
    candidates: list[dict] = []
    for g in selected:
        gid = g["id"]
        analytics = _get_analytics(db, gid)
        overrides = g.get("identityOverrides") or {}
        scores = {}
        for p in roster:
            s = pwa_score.per_game_score(p["id"], g, weights)
            if s is not None:
                scores[p["id"]] = s
        entry = {
            "opponent": g.get("opponent"),
            "date": g.get("date"),
            "our_score": g.get("ourScore"),
            "opp_score": g.get("oppScore"),
            "n_events": len(g.get("events") or []),
            "gk_player_id": g.get("gkPlayerId"),
            "gk_changes": sorted(
                (dict(c) for c in (g.get("gkChanges") or [])),
                key=lambda c: c.get("at", 0),
            ),
            "identity_overrides": dict(sorted(overrides.items())),
            "pwa_scores": dict(sorted(scores.items())),
            "has_analytics": analytics is not None,
        }
        if analytics:
            entry["identity"] = _identity_summary(analytics)
            entry["player_stats"] = _player_stats_slice(analytics)
        games_out[gid] = entry
        if overrides:
            labels_out[gid] = dict(sorted(overrides.items()))
        candidates.append({
            "game_id": gid,
            "opponent": g.get("opponent"),
            "date": g.get("date"),
            "n_events": entry["n_events"],
            "n_gk_changes": len(entry["gk_changes"]),
            "n_overrides": len(overrides),
            "has_analytics": analytics is not None,
        })

    season = {}
    for p in roster:
        s = pwa_score.season_score(p["id"], finished, p, weights)
        if s is not None:
            season[p["id"]] = s

    snapshot = {
        "label": label,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "n_games": len(games_out),
        "weights_override_present": weights is not None,
        "roster": {p["id"]: {"name": p.get("name"), "number": p.get("number"),
                             "position": p.get("position")} for p in roster},
        "games": games_out,
        "season_scores": dict(sorted(season.items())),
    }
    snap_path = out_dir / "baseline.json"
    snap_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=str))

    labels_path = OUT_ROOT / "identity_labels.json"
    labels_path.write_text(json.dumps(labels_out, indent=2, sort_keys=True))

    # --- console summary -------------------------------------------------
    print(f"Snapshot: {snap_path}  ({len(games_out)} games, {len(season)} season scores)")
    print(f"Identity labels (coach overrides): {labels_path}")
    total_labels = sum(len(v) for v in labels_out.values())
    print(f"  {total_labels} labeled tracklets across {len(labels_out)} game(s)")
    print("\nGolden-game candidates:")
    print(f"{'game_id':<22}{'date':<12}{'opponent':<20}{'events':>7}{'gkChg':>6}{'labels':>7}{'analytics':>10}")
    for c in sorted(candidates, key=lambda c: str(c["date"])):
        print(f"{c['game_id']:<22}{str(c['date'])[:10]:<12}{str(c['opponent'] or '')[:18]:<20}"
              f"{c['n_events']:>7}{c['n_gk_changes']:>6}{c['n_overrides']:>7}"
              f"{'yes' if c['has_analytics'] else 'NO':>10}")
    swaps = [c for c in candidates if c["n_gk_changes"] > 0]
    messy = sorted(candidates, key=lambda c: -c["n_overrides"])
    print("\nSuggested golden games:")
    if swaps:
        print(f"  keeper-swap:    {swaps[0]['game_id']} ({swaps[0]['opponent']})")
    else:
        print("  keeper-swap:    NONE FOUND — no finished game has gkChanges")
    if messy and messy[0]["n_overrides"] > 0:
        print(f"  messy-identity: {messy[0]['game_id']} ({messy[0]['opponent']}, "
              f"{messy[0]['n_overrides']} coach labels)")
    else:
        print("  messy-identity: no game has coach identity labels yet — label ~30 "
              "tracklets in FIX IDS for one game (plan item 0.3)")


if __name__ == "__main__":
    main()
