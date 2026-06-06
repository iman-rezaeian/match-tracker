"""One-off: reclassify David Shallvari's mislogged turnovers in Firestore.

Per the coach: of his 5 logged TURNOVERs, 2 were "kick out under pressure" and
2 were "clearance"; 1 was a real turnover. Score depends only on counts, so we
relabel his turnover events in chronological order: first 2 -> KICK_OUT,
next 2 -> CLEAR, remainder stay TURNOVER. Only David's events are touched.
"""
import sys
from post_game import firestore_io

PID = "p_shallvari"
PLAN = ["KICK_OUT", "KICK_OUT", "CLEAR", "CLEAR"]  # remaining stay TURNOVER
APPLY = "--apply" in sys.argv

db = firestore_io._client()
games = [(g.id, g.to_dict()) for g in db.collection("teams/main/games").stream()]
games = [(gid, g) for gid, g in games if g.get("status") == "finished"]

# Collect David's turnover events across games, in chronological order.
to_events = []  # (game_id, event_id, at)
for gid, g in games:
    for e in (g.get("events") or []):
        if e.get("type") == "TURNOVER" and e.get("playerId") == PID:
            to_events.append((gid, e.get("id"), e.get("at", 0)))
to_events.sort(key=lambda x: x[2])

print(f"David has {len(to_events)} TURNOVER events across {len({g for g,_,_ in to_events})} game(s).")
mapping = {}  # event_id -> new_type
for i, (gid, eid, _) in enumerate(to_events):
    new = PLAN[i] if i < len(PLAN) else "TURNOVER"
    mapping[eid] = new
    print(f"  event {eid}  (game {gid[:8]})  TURNOVER -> {new}")

if not APPLY:
    print("\nDRY RUN. Re-run with --apply to write to Firestore.")
    sys.exit(0)

# Apply per game.
changed_games = {gid for gid, eid, _ in to_events if mapping.get(eid) != "TURNOVER"}
for gid in changed_games:
    snap = db.collection("teams/main/games").document(gid).get()
    g = snap.to_dict()
    new_events = []
    n = 0
    for e in (g.get("events") or []):
        e = dict(e)
        if e.get("id") in mapping and mapping[e["id"]] != "TURNOVER":
            e["type"] = mapping[e["id"]]
            n += 1
        new_events.append(e)
    db.collection("teams/main/games").document(gid).update({"events": new_events})
    print(f"  ✓ game {gid[:8]}: updated {n} event(s)")
print("Done.")
