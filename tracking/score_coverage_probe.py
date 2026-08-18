#!/usr/bin/env python3
"""Compare the old season score against the logging-weighted one, on real games.

Why this exists
---------------
The coach's taps are not uniform. Over 12 games the total action events per game
swing 95 -> 18, and the DEF share of them falls 60% -> 3% as he increasingly taps
only goals and shots. The old season rate divided each pillar's points by ALL
minutes played, so a pillar's rate was (points from the few well-logged games) /
(minutes from every game) -- diluted toward zero by the games where he was busy
coaching, and dominated by whoever happened to play in the two June games.

This probe mirrors both formulas in Python so the change can be scored before it
ships. It deliberately does NOT re-derive the pillar POINTS (that lives in the JSX
`pillarPoints` and is unchanged) -- it uses a simple proxy: each pillar's event
count for the player, at unit weight. The proxy is enough to show which players
move and in which direction, which is the decision being made here.

⚠ The absolute numbers below are NOT the app's scores. Only the RANKING SHIFT is
meaningful, because the proxy skips the per-event point weights and the GK blend.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PILLAR_TYPES = {
    "atk": ["GOAL", "ASSIST", "KEY_PASS", "SHOT_ON", "SHOT_OFF", "FOUL_ON", "PEN_AWARDED"],
    "def": ["SAVE", "BLOCK", "BALL_WIN", "CLEAR", "KICK_OUT", "DUEL_WIN",
            "DUEL_LOSE", "FOUL_BY", "PEN_CONCEDED"],
    "dec": ["GIVE_GO", "GATES", "KEY_PASS", "ASSIST", "HOLDS_BALL", "TURNOVER"],
}
LOG_FLOOR = 0.15


def player_seconds(pid: str, g: dict) -> float:
    """Mirror of the JSX playerSeconds: starting lineup + SUB taps, minus pauses."""
    if not g.get("startingLineup"):
        return 0.0
    subs = sorted((e for e in (g.get("events") or []) if e.get("type") == "SUB"),
                  key=lambda e: e.get("at") or 0)
    on_since = g.get("startedAt") if pid in g["startingLineup"] else None
    intervals = []
    for s in subs:
        if s.get("playerId") == pid and on_since is not None:
            intervals.append((on_since, s.get("at")))
            on_since = None
        if s.get("subOnPlayerId") == pid and on_since is None:
            on_since = s.get("at")
    if on_since is not None:
        intervals.append((on_since, g.get("endedAt") or on_since))
    pauses = [(p.get("startedAt"), p.get("endedAt"))
              for p in (g.get("pausePeriods") or []) if p.get("startedAt")]
    total = 0.0
    for a, b in intervals:
        if a is None or b is None:
            continue
        secs = (b - a) / 1000.0
        for ps, pe in pauses:
            if ps is None or pe is None:
                continue
            lo, hi = max(a, ps), min(b, pe)
            if hi > lo:
                secs -= (hi - lo) / 1000.0
        total += max(0.0, secs)
    return total


def main() -> None:
    from post_game import firestore_io

    team = firestore_io._team_doc()
    roster = {p["id"]: p.get("name", p["id"])
              for p in (team.get().to_dict() or {}).get("roster", [])}
    games = [s.to_dict() | {"id": s.id} for s in team.collection("games").stream()]
    games = [g for g in games if g.get("status") == "finished"]
    games.sort(key=lambda g: g.get("date") or "")

    # Per-game logging intensity per pillar (events per team-minute).
    log = {}
    for g in games:
        c = Counter(e.get("type") for e in (g.get("events") or []))
        team_min = sum(player_seconds(p, g) for p in roster) / 60.0
        base = max(team_min, 1.0)
        log[g["id"]] = {k: sum(c.get(t, 0) for t in ts) / base
                       for k, ts in PILLAR_TYPES.items()}
    peak = {k: max((v[k] for v in log.values()), default=0.0) for k in PILLAR_TYPES}

    def lw(gid: str, k: str) -> float:
        if not peak[k]:
            return 1.0
        return max(LOG_FLOOR, min(1.0, log[gid][k] / peak[k]))

    print("per-game logging weight (1.0 = best-logged game of the season)")
    print(f"{'date':<12}{'opp':<16}{'atk':>7}{'def':>7}{'dec':>7}")
    for g in games:
        print(f"{g.get('date',''):<12}{(g.get('opponent') or '?')[:15]:<16}"
              + "".join(f"{lw(g['id'], k):>7.2f}" for k in ("atk", "def", "dec")))

    # Accumulate the proxy: event counts as points, minutes as exposure.
    pts = defaultdict(lambda: dict(atk=0.0, def_=0.0, dec=0.0))
    raw_min = defaultdict(float)
    wmin = defaultdict(lambda: dict(atk=0.0, def_=0.0, dec=0.0))
    for g in games:
        per = defaultdict(Counter)
        for e in (g.get("events") or []):
            if e.get("playerId"):
                per[e["playerId"]][e.get("type")] += 1
        for pid in roster:
            sec = player_seconds(pid, g)
            if sec <= 0:
                continue
            mins = sec / 60.0
            raw_min[pid] += mins
            for k, key in (("atk", "atk"), ("def", "def_"), ("dec", "dec")):
                pts[pid][key] += sum(per[pid].get(t, 0) for t in PILLAR_TYPES[k])
                wmin[pid][key] += mins * lw(g["id"], k)

    rows = []
    for pid in roster:
        if raw_min[pid] <= 0:
            continue
        old = sum(pts[pid].values()) / (raw_min[pid] / 20.0)
        new = sum(pts[pid][key] / max(wmin[pid][key] / 20.0, 1e-9)
                  for key in ("atk", "def_", "dec"))
        rows.append((roster[pid].split()[0], raw_min[pid], old, new,
                     100 * wmin[pid]["def_"] / raw_min[pid]))

    print(f"\n{'player':<12}{'min':>7}{'OLD/20m':>9}{'NEW/20m':>9}{'DEF cov%':>10}")
    for nm, mn, old, new, cov in sorted(rows, key=lambda r: -r[3]):
        print(f"{nm:<12}{mn:>7.0f}{old:>9.2f}{new:>9.2f}{cov:>10.0f}")

    old_rank = [r[0] for r in sorted(rows, key=lambda r: -r[2])]
    new_rank = [r[0] for r in sorted(rows, key=lambda r: -r[3])]
    print("\nranking, OLD:", " > ".join(old_rank))
    print("ranking, NEW:", " > ".join(new_rank))
    moved = sum(1 for i, n in enumerate(new_rank) if old_rank.index(n) != i)
    print(f"\n{moved}/{len(rows)} players change position")
    print("\n⚠ absolute values are a PROXY (event counts, not weighted points) —")
    print("  only the ranking shift is meaningful here.")


if __name__ == "__main__":
    main()
