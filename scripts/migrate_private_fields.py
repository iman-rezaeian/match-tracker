#!/usr/bin/env python3
"""One-time migration: move secrets + dugout audio off family-readable docs.

1. teams/main.teamLiveInput  → teams/main/private/liveInput   (field deleted)
2. games/<id>.liveInput      → keep ONLY {hlsUrl, createdAt} on the game doc;
   full creds (uid/rtmpsUrl/streamKey) → games/<id>/private/liveInput
3. games/<id>.voiceSegments  → games/<id>/voice/segments {list} (field deleted)

Safe to re-run: every step is idempotent (checks before writing/deleting).

Run with the post_game venv + pipeline creds:
    GOOGLE_APPLICATION_CREDENTIALS=~/.config/stompers/firebase-adminsdk.json \
    FIRESTORE_PROJECT_ID=lasalle-stompers \
    .venv-post-game/bin/python scripts/migrate_private_fields.py [--apply]
"""
from __future__ import annotations

import argparse
import os
import sys

from google.cloud import firestore

PROJECT_ID = os.environ.get("FIRESTORE_PROJECT_ID", "lasalle-stompers")
SECRET_KEYS = ("uid", "rtmpsUrl", "streamKey")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()
    tag = "[set]" if args.apply else "[dry]"

    db = firestore.Client(project=PROJECT_ID)
    team_ref = db.collection("teams").document("main")

    # 1. Team-level live input.
    team = team_ref.get().to_dict() or {}
    tli = team.get("teamLiveInput")
    if tli is not None:
        print(f"{tag} teams/main.teamLiveInput → private/liveInput "
              f"(uid={str((tli or {}).get('uid'))[:8]}…)" if isinstance(tli, dict) else f"{tag} clear teamLiveInput")
        if args.apply:
            if isinstance(tli, dict) and tli.get("uid"):
                team_ref.collection("private").document("liveInput").set(tli)
            team_ref.update({"teamLiveInput": firestore.DELETE_FIELD})
    else:
        print("     teams/main.teamLiveInput: already clean")

    # 2 + 3. Per-game fields.
    moved_live = moved_voice = 0
    for snap in team_ref.collection("games").get():
        g = snap.to_dict() or {}
        gref = team_ref.collection("games").document(snap.id)
        label = f"{(g.get('date') or '?')[:10]} vs {g.get('opponent') or '?'} ({snap.id})"

        li = g.get("liveInput")
        if isinstance(li, dict) and any(li.get(k) for k in SECRET_KEYS):
            moved_live += 1
            print(f"{tag} {label}: liveInput secrets → private/liveInput, doc keeps hlsUrl only")
            if args.apply:
                gref.collection("private").document("liveInput").set(li)
                gref.update({"liveInput": {
                    "hlsUrl": li.get("hlsUrl") or None,
                    "createdAt": li.get("createdAt") or None,
                }})

        segs = g.get("voiceSegments")
        if segs:
            moved_voice += 1
            print(f"{tag} {label}: {len(segs)} voiceSegments → voice/segments")
            if args.apply:
                vref = gref.collection("voice").document("segments")
                cur = (vref.get().to_dict() or {}).get("list") or []
                by_key = {(s.get("startedAt"), s.get("url")): s for s in cur}
                for s in segs:
                    by_key.setdefault((s.get("startedAt"), s.get("url")), s)
                merged = sorted(by_key.values(), key=lambda s: s.get("startedAt") or 0)
                vref.set({"list": merged})
                gref.update({"voiceSegments": firestore.DELETE_FIELD})

    print(f"\nGames with live-input secrets: {moved_live}; with voiceSegments: {moved_voice}")
    if not args.apply:
        print("Dry run only — re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
