"""Full-roster BEFORE/AFTER performance scores, replicating the deployed app.

BEFORE = session start: POSITION/GK_CHANGE counted; key pass 5; duels 4/-1; no
CLEAR/KICK_OUT; David's turnovers; ANY GK stint -> full GK pillars (outfield
points, since gkPoints was dead code then).

AFTER = current deployed: POSITION/GK_CHANGE excluded; key pass 4; duels 2/-2;
CLEAR +3 / KICK_OUT +1; David's data reclassified; part-time keepers scored by
a time-weighted blend of BOTH point table (points<->gkPoints) and pillar weights
(outfield<->gk) using gkFraction = gkSeconds/totalSeconds.
"""
import tracking.audit_player_score as A
roster, fin, player_seconds = A.roster, A.fin, A.player_seconds

PLAY = {'GOAL','ASSIST','KEY_PASS','SAVE','SHOT_ON','SHOT_OFF','BLOCK','BALL_WIN','CLEAR','KICK_OUT',
        'DUEL_WIN','DUEL_LOSE','GIVE_GO','GATES','TURNOVER','HOLDS_BALL','OPP_GOAL','FOUL_BY','FOUL_ON',
        'PEN_CONCEDED','PEN_AWARDED'}

OLD_P = dict(GOAL_atk=10,ASSIST_atk=8,KEY_PASS_atk=5,SHOT_ON_atk=3,SHOT_OFF_atk=1,
             SAVE_def=7,BLOCK_def=5,BALL_WIN_def=5,DUEL_WIN_def=4,DUEL_LOSE_def=-1,
             GIVE_GO_dec=6,GIVE_GO_PARTNER_dec=3,GATES_dec=4,KEY_PASS_dec=3,ASSIST_dec=3,
             HOLDS_BALL_dec=-4,TURNOVER_dec=-4,CLEAN_SHEET_def=8,FOUL_ON_atk=2,FOUL_BY_def=-2,
             PEN_AWARDED_atk=6,PEN_CONCEDED_def=-8,OWN_GOAL_def=-10)
NEW_P = dict(OLD_P); NEW_P.update(KEY_PASS_atk=4,DUEL_WIN_def=2,DUEL_LOSE_def=-2,CLEAR_def=3,KICK_OUT_def=1)
NEW_GK = dict(NEW_P); NEW_GK.update(KEY_PASS_atk=10,SAVE_def=10,KEY_PASS_dec=6)  # W.gkPoints (now live)
PIL_OUT = dict(atk=30, dfn=25, dec=30, inv=15)
PIL_GK  = dict(atk=10, dfn=55, dec=25, inv=10)


def gk_extras(pid, game):
    start = game.get('startedAt'); end = game.get('endedAt') if game.get('status') == 'finished' else None
    if game.get('gkPlayerId') or game.get('gkChanges'):
        segs = []; cur = game.get('gkPlayerId'); s = start
        for c in sorted(game.get('gkChanges') or [], key=lambda c: c.get('at', 0)):
            segs.append((s, c.get('at'), cur)); cur = c.get('gkPlayerId'); s = c.get('at')
        segs.append((s, end, cur))
        tl = [(a, b) for a, b, who in segs if who == pid]
    else:
        return dict(concededPenalty=0, cleanSheets=0, secs=0)
    conceded = 0; pen = 0
    for e in (game.get('events') or []):
        if e.get('type') != 'OPP_GOAL':
            continue
        if any(a is not None and b is not None and a <= e.get('at', 0) <= b for a, b in tl):
            conceded += 1
            pen += 6 if e.get('gkFault') == 'gk' else (0 if e.get('gkFault') == 'unstoppable' else 3)
    secs = sum(max(0, (b - a) / 1000) for a, b in tl if a is not None and b is not None)
    cs = 1 if (conceded == 0 and secs >= 60 and game.get('status') == 'finished') else 0
    return dict(concededPenalty=pen, cleanSheets=cs, secs=secs)


def blend(a, b, f):
    return {k: (1 - f) * a.get(k, 0) + f * b.get(k, 0) for k in set(a) | set(b)}


def score(pid, events, mn, pts, pil, gkx, apply_bonus, onlyplay):
    if mn <= 0:
        return None
    ph = mn / 20; c = {}; partner = 0; own = 0
    for e in events:
        t = e.get('type')
        if t == 'SUB' or (onlyplay and t not in PLAY):
            continue
        if e.get('playerId') == pid:
            c[t] = c.get(t, 0) + 1
        if t == 'GIVE_GO' and e.get('partnerId') == pid:
            partner += 1
        if t == 'OPP_GOAL' and e.get('ownGoalById') == pid:
            own += 1
    g = lambda k: c.get(k, 0); p = lambda k: pts.get(k, 0)
    atk = (g('GOAL')*p('GOAL_atk')+g('ASSIST')*p('ASSIST_atk')+g('KEY_PASS')*p('KEY_PASS_atk')+g('SHOT_ON')*p('SHOT_ON_atk')+g('SHOT_OFF')*p('SHOT_OFF_atk')+g('FOUL_ON')*p('FOUL_ON_atk')+g('PEN_AWARDED')*p('PEN_AWARDED_atk'))/ph
    bonus = (-gkx['concededPenalty'] + gkx['cleanSheets']*p('CLEAN_SHEET_def')) if apply_bonus else 0
    dfn = (g('SAVE')*p('SAVE_def')+g('BLOCK')*p('BLOCK_def')+g('BALL_WIN')*p('BALL_WIN_def')+g('CLEAR')*p('CLEAR_def')+g('KICK_OUT')*p('KICK_OUT_def')+g('DUEL_WIN')*p('DUEL_WIN_def')+g('DUEL_LOSE')*p('DUEL_LOSE_def')+g('FOUL_BY')*p('FOUL_BY_def')+g('PEN_CONCEDED')*p('PEN_CONCEDED_def')+own*p('OWN_GOAL_def')+bonus)/ph
    dec = (g('GIVE_GO')*p('GIVE_GO_dec')+partner*p('GIVE_GO_PARTNER_dec')+g('GATES')*p('GATES_dec')+g('KEY_PASS')*p('KEY_PASS_dec')+g('ASSIST')*p('ASSIST_dec')+g('HOLDS_BALL')*p('HOLDS_BALL_dec')+g('TURNOVER')*p('TURNOVER_dec'))/ph
    inv = (sum(c.values())+partner+own)/ph
    return round((pil['atk']*atk+pil['dfn']*dfn+pil['dec']*dec+pil['inv']*inv)/100*10)/10


if __name__ == '__main__':
    rows = []
    for pl in roster:
        pid = pl['id']; sec = 0; gks = 0; ev = []
        gkx = dict(concededPenalty=0, cleanSheets=0, secs=0); served = (pl.get('position') or '') == 'GK'
        for gm in fin:
            sec += player_seconds(pid, gm); ev.extend(gm.get('events') or [])
            if (gm.get('gkPlayerId') == pid) or any(c.get('gkPlayerId') == pid for c in (gm.get('gkChanges') or [])):
                served = True; gx = gk_extras(pid, gm)
                gks += gx['secs']; gkx['concededPenalty'] += gx['concededPenalty']; gkx['cleanSheets'] += gx['cleanSheets']
        mn = round(sec / 60)
        if mn <= 0:
            continue
        frac = min(1, gks / sec) if sec else (1 if (pl.get('position') or '') == 'GK' else 0)
        ev_old = [{**e, 'type': ('TURNOVER' if e.get('type') in ('CLEAR', 'KICK_OUT') else e.get('type'))} for e in ev]
        before = score(pid, ev_old, mn, OLD_P, (PIL_GK if served else PIL_OUT), gkx, served, onlyplay=False)
        after = score(pid, ev, mn, blend(NEW_P, NEW_GK, frac), blend(PIL_OUT, PIL_GK, frac), gkx, frac > 0, onlyplay=True)
        goals = sum(1 for e in ev if e.get('type') == 'GOAL' and e.get('playerId') == pid)
        rows.append((pl['name'], 'GK' if served else '', goals, mn, round(frac*100), before, after, round((after-before)*10)/10))

    rows.sort(key=lambda r: -r[6])
    print(f"{'player':<20}{'pos':>4}{'G':>3}{'min':>5}{'gk%':>5}{'BEFORE':>8}{'AFTER':>7}{'Δ':>7}")
    print('-' * 59)
    for r in rows:
        print(f"{r[0]:<20}{r[1]:>4}{r[2]:>3}{r[3]:>5}{r[4]:>4}%{r[5]:>8}{r[6]:>7}{r[7]:>+7}")
