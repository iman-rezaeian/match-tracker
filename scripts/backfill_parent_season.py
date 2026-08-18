#!/usr/bin/env python3
"""Backfill teams/main/parentSeason/{playerId} for every finished game.

Uses the analytics docs already in Firestore — no pipeline re-runs. Safe to
re-run any time (each game's row is replaced by gameId, not appended).

Run with the post_game venv + pipeline creds:
    GOOGLE_APPLICATION_CREDENTIALS=~/.config/stompers/firebase-adminsdk.json \
    .venv-post-game/bin/python scripts/backfill_parent_season.py [--apply]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.cloud import firestore  # noqa: E402

from post_game import config, parent_season  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default: list what would run)")
    args = ap.parse_args()

    db = firestore.Client(project=config.FIRESTORE_PROJECT_ID)
    games = (db.collection("teams").document("main").collection("games")
             .where("status", "==", "finished").get())
    games = sorted(games, key=lambda s: (s.to_dict() or {}).get("date") or "")
    print(f"{len(games)} finished games")

    for snap in games:
        g = snap.to_dict() or {}
        label = f"{(g.get('date') or '?')[:10]} vs {g.get('opponent') or '?'} ({snap.id})"
        if not args.apply:
            print(f"[dry] would publish {label}")
            continue
        try:
            res = parent_season.publish_parent_season(snap.id, db=db)
            print(f"[ok]  {label} → {res}")
        except Exception as e:
            print(f"[ERR] {label}: {e}")
    if not args.apply:
        print("\nDry run only — re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
