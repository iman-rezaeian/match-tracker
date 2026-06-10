#!/usr/bin/env python3
"""Player-profile separability probe — is appearance-based cross-game ID viable?

Answers, with real numbers, whether a per-player appearance "profile" (gallery)
could identify players — the question raised when jersey-OCR proved orientation-
limited. Uses the cached OSNet embeddings (per track) + the coach-corrected labels
(analytics identity_assignments, which fold in FIX-IDS overrides).

Reports three gates:
  1. WITHIN-game: can it separate same-kit teammates inside one game?
     (nearest-centroid leave-one-out accuracy + the same-vs-different cosine gap)
  2. SAME-KIT cross-game: does a player's signature persist across games where the
     team wore the SAME kit?  → decides whether PER-KIT galleries work.
  3. CROSS-KIT cross-game: does it persist across DIFFERENT kits? → decides whether
     a single KIT-INVARIANT profile is achievable (needs 8K + a part-based model).

Kit is taken from each game's home_color (the coach-set "our jersey" color).

Baseline (2026-06, 5.7K, raw OSNet): within-game ~25-32% (3-4x chance but a tiny
~0.02-0.03 cosine margin — kit dominates); cross-kit ~chance. Re-run on 8K games:
if within-game jumps >~60% and same-kit cross-game is strong, build per-kit
galleries; if cross-KIT also rises, a single kit-invariant profile is in reach.

Usage:
  python tracking/player_profile_probe.py --games mpyo67cl4uflh mq01kuce2i81r [...]
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from itertools import combinations
import numpy as np


def _load_game(gid, db, ros):
    import os
    path = f"post_game/outputs/{gid}/embeddings.npz"
    if not os.path.exists(path):
        print(f"  [{gid}] no embeddings.npz — skip (needs a processed run with the Stage-2 cache)")
        return None
    z = np.load(path)
    from post_game import firestore_io
    g = firestore_io.get_game(gid)
    doc = (db.document("teams/main").collection("games").document(gid)
           .collection("analytics").document("v1").get().to_dict())
    if not doc:
        print(f"  [{gid}] no analytics doc — skip")
        return None
    lab = {a["track_id"]: a["player_id"] for a in doc.get("identity_assignments") or [] if a.get("player_id")}
    byp = defaultdict(list)
    for tid, pid in lab.items():
        k = str(tid)
        if k in z:
            v = z[k].astype(np.float64); n = np.linalg.norm(v)
            if n > 0:
                byp[pid].append(v / n)
    byp = {p: np.stack(v) for p, v in byp.items() if len(v) >= 3}
    cents = {}
    for p, M in byp.items():
        c = M.mean(0); cents[p] = c / (np.linalg.norm(c) + 1e-9)
    return {"gid": gid, "kit": (g.home_color or "?"), "opp": g.opponent,
            "byp": byp, "cents": cents}


def within_game(d):
    byp, cents = d["byp"], d["cents"]
    players = list(byp)
    if len(players) < 2:
        return
    correct = tot = 0; wsum = wn = bsum = bn = 0
    for p in players:
        for v in byp[p]:
            sims = {q: float(v @ cents[q]) for q in players}
            correct += (max(sims, key=sims.get) == p); tot += 1
            wsum += sims[p]; wn += 1
            for q in players:
                if q != p:
                    bsum += sims[q]; bn += 1
    print(f"  [{d['gid']} vs {d['opp']}, kit {d['kit']}] players={len(players)} tracks={tot}")
    print(f"     nearest-centroid acc: {100*correct/tot:4.0f}%  (chance {100/len(players):.0f}%)"
          f"   cosine same {wsum/wn:.3f} vs diff {bsum/bn:.3f}  (gap {wsum/wn-bsum/bn:+.3f})")


def cross_game(da, db_):
    common = [p for p in da["cents"] if p in db_["cents"]]
    if not common:
        return None
    hit = sum(1 for p in common
              if max(db_["cents"], key=lambda q: da["cents"][p] @ db_["cents"][q]) == p)
    return hit, len(common), 100 * hit / len(common), 100 / len(db_["cents"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", nargs="+", required=True)
    args = ap.parse_args()
    from post_game import firestore_io
    db = firestore_io._client()
    ros = {r.id: r.name.split()[0] for r in firestore_io.get_roster()}

    games = [d for gid in args.games if (d := _load_game(gid, db, ros))]
    if not games:
        raise SystemExit("no usable games")

    print("\n=== 1) WITHIN-GAME separability (can it split same-kit teammates?) ===")
    for d in games:
        within_game(d)

    if len(games) >= 2:
        print("\n=== 2/3) CROSS-GAME (does a player's signature persist?) ===")
        for da, db_ in combinations(games, 2):
            r = cross_game(da, db_)
            if not r:
                continue
            hit, n, acc, chance = r
            same = "SAME-KIT" if da["kit"] == db_["kit"] else "CROSS-KIT"
            print(f"  [{same}] {da['gid']}→{db_['gid']}: {acc:4.0f}% of {n} shared players matched"
                  f"  (chance {chance:.0f}%)")
        print("\n  Gate: SAME-KIT high → per-kit galleries work. CROSS-KIT high → a single")
        print("  kit-invariant profile is achievable (expect this only at 8K + a part-based model).")
    print("\nNote: body-size/height is a separate cue but needs field-distance normalization")
    print("to reflect stature (raw bbox px is dominated by distance-to-camera).")


if __name__ == "__main__":
    main()
