"""Audit a player's performance-score calculation against the PWA formula.

Uses tracking/pwa_score.py — the exact Python replication of
soccer_team_app.jsx's playerSeconds() / gkExtrasForGame() /
computePerformanceScore(), INCLUDING the gkFraction outfield↔GK blend (the
old version of this script hardcoded outfield weights and diverged for any
part-time keeper). Pulls roster + finished games + scoring-weight overrides
from Firestore and decomposes per-game + season scores so we can see WHY a
number is what it is.

Usage:
    .venv-post-game/bin/python -m tracking.audit_player_score [name-substring]
"""

import sys

from post_game import firestore_io
from tracking import pwa_score

NEEDLE = (sys.argv[1] if len(sys.argv) > 1 else "david").lower()


def main() -> None:
    db = firestore_io._client()
    team = db.document("teams/main").get().to_dict() or {}
    roster = team.get("roster") or []
    weights = team.get("weights")  # None → defaults; merge happens inside
    games = [dict(g.to_dict(), id=g.id) for g in db.collection("teams/main/games").stream()]
    finished = sorted(
        [g for g in games if g.get("status") == "finished"],
        key=lambda x: x.get("date", ""),
    )

    players = [p for p in roster if NEEDLE in str(p.get("name", "")).lower()]
    if not players:
        print(f"No roster player matches {NEEDLE!r}")
        return

    for p in players:
        pid = p["id"]
        print(f"\n=== {p.get('name')} #{p.get('number')} (pos='{p.get('position')}') ===")
        print(f"{'game':<22}{'min':>5}{'gk%':>5}{'goals':>7}{'overall':>9}   ATK / DEF / DEC / INV")
        for g in finished:
            s = pwa_score.per_game_score(pid, g, weights, roster)
            nm = (g.get("opponent") or "")[:20]
            goals = sum(1 for e in (g.get("events") or [])
                        if e.get("type") == "GOAL" and e.get("playerId") == pid)
            if s is None:
                print(f"{nm:<22}{'0':>5}{'':>5}{goals:>7}      n/a   (0 sec on field)")
                continue
            gkpct = f"{round(s['_gk_fraction'] * 100)}%" if s["_was_gk"] else ""
            print(f"{nm:<22}{s['_minutes']:>5}{gkpct:>5}{goals:>7}{s['overall']:>9}   "
                  f"{s['attacking']} / {s['defending']} / {s['decisions']} / {s['involvement']}   "
                  f"{s['_counts']}")
        ss = pwa_score.season_score(pid, finished, p, weights, roster=roster)
        print("-" * 78)
        if ss is None:
            print("SEASON: no minutes recorded")
            continue
        goals = sum(
            1 for g in finished for e in (g.get("events") or [])
            if e.get("type") == "GOAL" and e.get("playerId") == pid
        )
        gkpct = f" gk%={round(ss['_gk_fraction'] * 100)}" if ss["_was_gk_any"] else ""
        print(f"SEASON (v{pwa_score.SCORING_VERSION}, type-weighted+shrunk)  "
              f"weighted-min={ss['_weighted_minutes']}{gkpct}  goals={goals}  OVERALL={ss['overall']}  "
              f"(ATK={ss['attacking']} DEF={ss['defending']} DEC={ss['decisions']} INV={ss['involvement']})")


if __name__ == "__main__":
    main()
