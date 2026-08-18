#!/usr/bin/env python3
"""Score identity quality against the coach's hand-labels — the ground-truth
before/after gauge for the team-color / VLM fixes.

The coach labels tracklets in FIX-IDS; those land in `game.identityOverrides`
(a real player id, or a non-player sentinel __opp__/__ref__/__other__, or a
drop). This harness treats those labels as GROUND TRUTH and reports two things
straight off the LIVE analytics doc + drafts (no re-derivation — exactly what
the coach saw):

  (A) REVIEW-LIST PURITY — of the tracklets the coach marked opponent/coach/drop
      (i.e. NOT our player), how many did the pipeline wrongly put in the
      our-team review list `analytics.tracklets[]`? (lower = cleaner list)

  (B) VLM DRAFT ACCURACY — of `game.identityDrafts` on labeled tracklets:
      correct (suggested == coach's player), wrong-player, and — the key defect —
      how many landed on a NON-player (opponent/coach). (target: non-player → 0)

Read-only; zero writes. Run BEFORE a change, then AFTER (re-run the pipeline
with the fix), and compare.
  python -m tracking.eval_identity_vs_labels --game-id mri01pvelv46d
"""
from __future__ import annotations

import argparse
from collections import Counter


def _gt_kind(v) -> str:
    """Coach label → kind: 'player' | 'opp' | 'ref' | 'other' | 'drop'."""
    if not v:
        return "drop"
    s = str(v)
    if s == "__opp__":
        return "opp"
    if s == "__ref__":
        return "ref"
    if s == "__other__":
        return "other"
    if s.startswith("__"):
        return "other"
    return "player"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    args = ap.parse_args()

    from post_game import firestore_io

    snap = firestore_io._team_doc().collection("games").document(args.game_id).get()
    doc = snap.to_dict() or {}
    gt = doc.get("identityOverrides") or {}          # {tracklet_id(str): label}
    drafts = {int(x["trackletId"]): x for x in (doc.get("identityDrafts") or [])}
    analytics = firestore_io.read_analytics(args.game_id) or {}
    review = {int(t["tracklet_id"]): t for t in (analytics.get("tracklets") or [])}

    if not gt:
        raise SystemExit("No coach labels (game.identityOverrides) — nothing to score against.")

    kinds = Counter(_gt_kind(v) for v in gt.values())
    n = len(gt)
    non_player = kinds["opp"] + kinds["ref"] + kinds["other"] + kinds["drop"]

    # (A) review-list purity: of the coach's NON-player labels, how many are in
    # the our-team review list?
    in_list_nonplayer = sum(1 for k, v in gt.items()
                            if _gt_kind(v) != "player" and int(k) in review)
    players_gt = {int(k) for k, v in gt.items() if _gt_kind(v) == "player"}
    players_in_list = sum(1 for tl in players_gt if tl in review)

    # auto-assignment accuracy on GT players (from analytics tracklet records)
    auto_assigned = auto_correct = 0
    for tl in players_gt:
        r = review.get(tl)
        if r and r.get("player_id"):
            auto_assigned += 1
            if str(r["player_id"]) == str(gt[str(tl)]):
                auto_correct += 1

    # (B) VLM draft accuracy on labeled tracklets
    d_labeled = d_correct = d_wrong_player = d_nonplayer = 0
    for tl, dr in drafts.items():
        if str(tl) not in gt:
            continue
        d_labeled += 1
        k = _gt_kind(gt[str(tl)])
        if k == "player":
            if str(dr.get("suggestedPlayerId")) == str(gt[str(tl)]):
                d_correct += 1
            else:
                d_wrong_player += 1
        else:
            d_nonplayer += 1   # drafted a suggestion on a coach-marked non-player

    print(f"\n==== identity vs coach labels · {args.game_id} ====")
    print(f"GT labels: {n}  (player {kinds['player']}, opponent {kinds['opp']}, "
          f"ref {kinds['ref']}, coach/other {kinds['other']}, drop {kinds['drop']})")
    print(f"\n(A) REVIEW-LIST PURITY:")
    print(f"  coach non-player labels IN the our-team review list: "
          f"{in_list_nonplayer}/{non_player} "
          f"({100*in_list_nonplayer/max(1,non_player):.0f}% of non-players leaked in)")
    print(f"  coach real-players in the review list: {players_in_list}/{len(players_gt)}")
    print(f"  auto-assignment on GT players: {auto_correct}/{auto_assigned} correct "
          f"(of {len(players_gt)} labeled players)")
    print(f"\n(B) VLM DRAFT ACCURACY (drafts on labeled tracklets = {d_labeled}):")
    print(f"  correct player:      {d_correct}")
    print(f"  wrong player:        {d_wrong_player}")
    print(f"  on a NON-player:     {d_nonplayer}   <-- target 0 (opponent/coach)")


if __name__ == "__main__":
    main()
