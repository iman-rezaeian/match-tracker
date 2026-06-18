"""Surgical, override-preserving confidence recalibration of an already-labelled
game — WITHOUT re-stitching (which would reshuffle tracklet ids and mis-apply the
coach's tracklet-keyed overrides).

Recovers the ORIGINAL team/tracklet partition from the live analytics doc
(our-team tracks carry breakdown.tracklet; opponents have empty breakdown), then
re-runs pipeline.run(stats_only=True, pin_partition=...) so:
  - tracklet ids stay byte-stable → all coach overrides map 1:1,
  - assignment re-runs with the current confidence cap (ID_ANCHOR_CAP_ENABLED),
  - only the identity-dependent doc fields are recomputed + merged (reel/audio/
    broadcast index untouched).

WRITES THE LIVE DOC. Prints a before/after status diff so the effect is visible.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.recap_pinned --game-id mqcf9axlvtuyt
    # dry-run (verify override mapping + show current status, write nothing):
    .venv-post-game/bin/python -m tracking.recap_pinned --game-id mqcf9axlvtuyt --dry-run
"""

from __future__ import annotations

import argparse
from collections import Counter

from post_game import firestore_io, pipeline


def _recover_partition(doc: dict) -> tuple[dict[int, int], dict[int, int]]:
    """team_of_track, tracklet_of_track from the doc's identity_assignments.
    A 'tracklet' key in breakdown marks an our-team track (team 0); everything
    else is non-ours (team 1) and maps to itself."""
    team: dict[int, int] = {}
    tl: dict[int, int] = {}
    for a in (doc.get("identity_assignments") or []):
        trk = int(a["track_id"])
        bt = (a.get("breakdown") or {}).get("tracklet")
        if bt is not None:
            team[trk], tl[trk] = 0, int(bt)
        else:
            team[trk], tl[trk] = 1, trk
    return team, tl


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="Verify override mapping + print current status; write nothing.")
    args = ap.parse_args()

    game = firestore_io.get_game(args.game_id)
    overrides = {int(k): v for k, v in (game.identity_overrides or {}).items()}
    doc = firestore_io.read_analytics(args.game_id) or {}
    if not doc.get("identity_assignments"):
        raise SystemExit("Live doc has no identity_assignments — nothing to pin.")

    team, tl = _recover_partition(doc)
    our_tracklets = {t for trk, t in tl.items() if team.get(trk) == 0}
    mapped = sum(1 for T in overrides if T in our_tracklets)
    print(f"recovered partition: {len(team)} tracks, {len(our_tracklets)} our-team tracklets")
    print(f"coach overrides: {len(overrides)}  →  map onto pinned tracklets: "
          f"{mapped}/{len(overrides)}")
    if overrides and mapped < len(overrides):
        # Should never happen: overrides were authored against THIS doc's ids.
        miss = [T for T in overrides if T not in our_tracklets]
        print(f"  ⚠ {len(miss)} override(s) not in our-team partition: {miss[:10]}")

    before = Counter(a.get("status") for a in doc["identity_assignments"])
    print(f"\nBEFORE status counts: {dict(sorted((str(k), v) for k, v in before.items()))}")

    if args.dry_run:
        print("\n[dry-run] no write performed.")
        return

    out = pipeline.run(game_id=args.game_id, stats_only=True, pin_partition=(team, tl))
    after = Counter(a.get("status") for a in (out.get("identity_assignments") or []))
    print(f"AFTER  status counts: {dict(sorted((str(k), v) for k, v in after.items()))}")
    print(f"\nplayers analysed: {len(out.get('player_stats') or [])}  — live doc updated.")


if __name__ == "__main__":
    main()
