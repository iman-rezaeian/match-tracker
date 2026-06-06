"""Itemized side-by-side performance-score breakdown for two players.

Shows every action, its point value, the raw pillar subtotal, the per-20-minute
normalization, and the final weighted overall — so you can see exactly where and
why one player scores higher. Uses the fixed scoring (real play events only).

Usage: python -m tracking.compare_players arian david
"""
import sys
from tracking.audit_player_score import roster, fin, player_seconds

A, B = (sys.argv[1] if len(sys.argv) > 1 else "arian"), (sys.argv[2] if len(sys.argv) > 2 else "david")

PLAY = {"GOAL", "ASSIST", "KEY_PASS", "SAVE", "SHOT_ON", "SHOT_OFF", "BLOCK", "BALL_WIN",
        "CLEAR", "KICK_OUT", "DUEL_WIN", "DUEL_LOSE", "GIVE_GO", "GATES", "TURNOVER",
        "HOLDS_BALL", "OPP_GOAL", "FOUL_BY", "FOUL_ON", "PEN_CONCEDED", "PEN_AWARDED"}

# pillar -> list of (action, point_value)
ATK = [("GOAL", 10), ("ASSIST", 8), ("KEY_PASS", 4), ("SHOT_ON", 3), ("SHOT_OFF", 1), ("FOUL_ON", 2), ("PEN_AWARDED", 6)]
DEF = [("SAVE", 7), ("BLOCK", 5), ("BALL_WIN", 5), ("CLEAR", 3), ("KICK_OUT", 1), ("DUEL_WIN", 2), ("DUEL_LOSE", -2), ("FOUL_BY", -2), ("PEN_CONCEDED", -8)]
DEC = [("GIVE_GO", 6), ("GATES", 4), ("KEY_PASS", 3), ("ASSIST", 3), ("HOLDS_BALL", -4), ("TURNOVER", -4)]
PILW = {"ATK": 30, "DEF": 25, "DEC": 30, "INV": 15}


def gather(name):
    p = [x for x in roster if name in str(x.get("name", "")).lower()][0]
    pid = p["id"]
    sec = 0
    counts = {}
    partner = 0  # give-and-go wall-pass credits (partnerId == pid)
    for g in fin:
        sec += player_seconds(pid, g)
        for e in (g.get("events") or []):
            if e.get("type") in PLAY and e.get("playerId") == pid:
                counts[e["type"]] = counts.get(e["type"], 0) + 1
            if e.get("type") == "GIVE_GO" and e.get("partnerId") == pid:
                partner += 1
    mn = round(sec / 60)
    return p, mn, counts, partner


def pillar_raw(counts, items):
    rows = []
    raw = 0
    for act, pv in items:
        n = counts.get(act, 0)
        if n:
            rows.append((act, n, pv, n * pv))
            raw += n * pv
    return rows, raw


pa = gather(A)
pb = gather(B)


def show(p, mn, counts, partner):
    ph = mn / 20 if mn else 1
    print(f"\n{'='*60}\n{p['name']} #{p.get('number')}  —  {mn} min  (perHalf divisor = {ph:.2f})\n{'='*60}")
    tot_actions = sum(counts.values()) + partner
    pill_vals = {}
    for label, items in [("ATK", ATK), ("DEF", DEF), ("DEC", DEC)]:
        rows, raw = pillar_raw(counts, items)
        if label == "DEC" and partner:           # wall-pass partner credit lands in Decisions
            rows.append(("GIVE_GO(wall)", partner, 3, partner * 3))
            raw += partner * 3
        val = raw / ph
        pill_vals[label] = val
        print(f"\n {label}  (raw {raw} ÷ {ph:.2f} = {val:.1f})")
        if rows:
            for act, n, pv, sub in rows:
                print(f"    {act:<12} {n} × {pv:>3}  = {sub:>4}")
        else:
            print("    (none)")
    inv = tot_actions / ph
    pill_vals["INV"] = inv
    print(f"\n INV  (total play-actions {tot_actions} ÷ {ph:.2f} = {inv:.1f})")
    print(f"\n OVERALL = "
          + " + ".join(f"{PILW[k]}%×{pill_vals[k]:.1f}" for k in ["ATK", "DEF", "DEC", "INV"]))
    overall = sum(PILW[k] * pill_vals[k] for k in pill_vals) / 100
    contribs = {k: PILW[k] * pill_vals[k] / 100 for k in pill_vals}
    print("         = " + " + ".join(f"{contribs[k]:+.2f}" for k in ["ATK", "DEF", "DEC", "INV"])
          + f"  =  {round(overall*10)/10}")
    return pill_vals, contribs, round(overall * 10) / 10


vA = show(*pa)
vB = show(*pb)

print(f"\n{'#'*60}\nSIDE BY SIDE — contribution to overall (pillar% × per-20 value)")
print(f"{'pillar':<8}{pa[0]['name'].split()[0]:>12}{pb[0]['name'].split()[0]:>12}   who & why")
why = {"ATK": "goals/shots/passes", "DEF": "blocks/duels/saves", "DEC": "smart plays − turnovers", "INV": "total activity rate"}
for k in ["ATK", "DEF", "DEC", "INV"]:
    a, b = vA[1][k], vB[1][k]
    lead = pa[0]['name'].split()[0] if a > b else pb[0]['name'].split()[0]
    print(f"{k:<8}{a:>+12.2f}{b:>+12.2f}   {lead} (+{abs(a-b):.2f}) · {why[k]}")
print(f"{'TOTAL':<8}{vA[2]:>12}{vB[2]:>12}")
