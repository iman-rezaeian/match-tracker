"""Audit a player's performance-score calculation against the PWA formula.

Replicates soccer_team_app.jsx's playerSeconds() + computePerformanceScore()
exactly, pulls roster + finished games from Firestore, and decomposes the score
so we can see WHY a number is what it is (e.g. David #18 = 1.5).
"""
import math
import sys
from post_game import firestore_io

NEEDLE = sys.argv[1] if len(sys.argv) > 1 else "david"

db = firestore_io._client()
team = db.document("teams/main").get().to_dict()
roster = team.get("roster") or []
games = [g.to_dict() for g in db.collection("teams/main/games").stream()]
fin = [g for g in games if g.get("status") == "finished"]
fin.sort(key=lambda x: x.get("date", ""))


def player_seconds(pid, g):
    if not g.get("startingLineup"):
        return 0
    starting = pid in g["startingLineup"]
    subs = sorted([e for e in (g.get("events") or []) if e.get("type") == "SUB"],
                  key=lambda e: e.get("at", 0))
    intervals = []
    on_since = g.get("startedAt") if starting else None
    for s in subs:
        if s.get("playerId") == pid and on_since is not None:
            intervals.append([on_since, s.get("at")]); on_since = None
        if s.get("subOnPlayerId") == pid and on_since is None:
            on_since = s.get("at")
    if on_since is not None:
        end = g.get("endedAt") if (g.get("status") == "finished" and g.get("endedAt")) else None
        intervals.append([on_since, end])
    pauses = [[p.get("startedAt"), p.get("endedAt")] for p in (g.get("pausePeriods") or [])]
    tot = 0.0
    for s, e in intervals:
        if s is None or e is None:
            continue
        secs = (e - s) / 1000
        for ps, pe in pauses:
            if ps is None or pe is None:
                continue
            o0, o1 = max(s, ps), min(e, pe)
            if o1 > o0:
                secs -= (o1 - o0) / 1000
        tot += max(0, secs)
    return math.floor(tot)


PTS = dict(GOAL_atk=10, ASSIST_atk=8, KEY_PASS_atk=5, SHOT_ON_atk=3, SHOT_OFF_atk=1,
           SAVE_def=7, BLOCK_def=5, BALL_WIN_def=5, DUEL_WIN_def=4, DUEL_LOSE_def=-1,
           GIVE_GO_dec=6, GIVE_GO_PARTNER_dec=3, GATES_dec=4, KEY_PASS_dec=3, ASSIST_dec=3,
           HOLDS_BALL_dec=-4, TURNOVER_dec=-4, CLEAN_SHEET_def=8, FOUL_ON_atk=2, FOUL_BY_def=-2,
           PEN_AWARDED_atk=6, PEN_CONCEDED_def=-8, OWN_GOAL_def=-10)
PIL = dict(atk=30, dfn=25, dec=30, inv=15)  # outfield


def score(pid, events, minutes):
    if minutes <= 0:
        return None
    ph = minutes / 20
    c = {}; partner = 0; own = 0
    for e in events:
        if e.get("type") == "SUB":
            continue
        if e.get("playerId") == pid:
            c[e["type"]] = c.get(e["type"], 0) + 1
        if e.get("type") == "GIVE_GO" and e.get("partnerId") == pid:
            partner += 1
        if e.get("type") == "OPP_GOAL" and e.get("ownGoalById") == pid:
            own += 1
    g = lambda k: c.get(k, 0)
    atk = (g("GOAL")*PTS["GOAL_atk"] + g("ASSIST")*PTS["ASSIST_atk"] + g("KEY_PASS")*PTS["KEY_PASS_atk"]
           + g("SHOT_ON")*PTS["SHOT_ON_atk"] + g("SHOT_OFF")*PTS["SHOT_OFF_atk"] + g("FOUL_ON")*PTS["FOUL_ON_atk"]
           + g("PEN_AWARDED")*PTS["PEN_AWARDED_atk"]) / ph
    dfn = (g("SAVE")*PTS["SAVE_def"] + g("BLOCK")*PTS["BLOCK_def"] + g("BALL_WIN")*PTS["BALL_WIN_def"]
           + g("DUEL_WIN")*PTS["DUEL_WIN_def"] + g("DUEL_LOSE")*PTS["DUEL_LOSE_def"] + g("FOUL_BY")*PTS["FOUL_BY_def"]
           + g("PEN_CONCEDED")*PTS["PEN_CONCEDED_def"] + own*PTS["OWN_GOAL_def"]) / ph
    dec = (g("GIVE_GO")*PTS["GIVE_GO_dec"] + partner*PTS["GIVE_GO_PARTNER_dec"] + g("GATES")*PTS["GATES_dec"]
           + g("KEY_PASS")*PTS["KEY_PASS_dec"] + g("ASSIST")*PTS["ASSIST_dec"] + g("HOLDS_BALL")*PTS["HOLDS_BALL_dec"]
           + g("TURNOVER")*PTS["TURNOVER_dec"]) / ph
    tot = sum(c.values()) + partner + own
    inv = tot / ph
    overall = (PIL["atk"]*atk + PIL["dfn"]*dfn + PIL["dec"]*dec + PIL["inv"]*inv) / 100
    r = lambda n: round(n * 10) / 10
    return dict(overall=r(overall), atk=r(atk), dfn=r(dfn), dec=r(dec), inv=r(inv), counts=c, partner=partner)


players = [p for p in roster if NEEDLE in str(p.get("name", "")).lower()]
for p in players:
    pid = p["id"]
    print(f"\n=== {p.get('name')} #{p.get('number')} (pos='{p.get('position')}') ===")
    print(f"{'game':<22}{'min':>5}{'goals':>7}{'overall':>9}   ATK / DEF / DEC / INV")
    all_ev = []; total_sec = 0
    for g in fin:
        sec = player_seconds(pid, g); total_sec += sec
        mn = round(sec / 60)
        evs = list(g.get("events") or [])
        all_ev.extend(evs)
        goals = sum(1 for e in evs if e.get("type") == "GOAL" and e.get("playerId") == pid)
        s = score(pid, evs, mn)
        nm = (g.get("opponent") or "")[:20]
        if s:
            print(f"{nm:<22}{mn:>5}{goals:>7}{s['overall']:>9}   {s['atk']} / {s['dfn']} / {s['dec']} / {s['inv']}   {s['counts']}")
        else:
            print(f"{nm:<22}{mn:>5}{goals:>7}      n/a   (0 min on field)")
    mn = round(total_sec / 60)
    s = score(pid, all_ev, mn)
    goals = sum(1 for e in all_ev if e.get("type") == "GOAL" and e.get("playerId") == pid)
    print("-" * 72)
    if s:
        print(f"SEASON  min={mn}  goals={goals}  OVERALL={s['overall']}  "
              f"(ATK={s['atk']} DEF={s['dfn']} DEC={s['dec']} INV={s['inv']})")
        print(f"  counts: {s['counts']}  give&go-wall-credits={s['partner']}")
