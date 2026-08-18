#!/usr/bin/env python3
"""Seed allowedUsers from the TeamSnap roster export (parent_contacts.local.json).

Reads the LIVE roster from teams/main (never SEED_ROSTER), matches players by
name, and upserts one allowedUsers/{email} doc per parent contact:

    { role: 'parent', name, relationship?, playerIds: [...],
      addedVia: 'roster-import', addedAt: <ms> }

Standing rules enforced here:
  * NO phone numbers, NO birthdays — the contacts file doesn't carry them and
    this script writes only name/relationship/playerIds/role.
  * Doc IDs are lowercased emails (rules compare token.email.lower()).
  * An existing role:'coach' doc is never downgraded — it only gains
    playerIds (the owner is both coach and a parent).

Default is a DRY RUN that prints the full plan. Pass --apply to write.

Run with the post_game venv (has google-cloud-firestore + ADC creds):
    .venv-post-game/bin/python scripts/seed_allowed_users.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from google.cloud import firestore

ROOT = Path(__file__).resolve().parent.parent
CONTACTS = ROOT / "parent_contacts.local.json"
PROJECT_ID = os.environ.get("FIRESTORE_PROJECT_ID", "lasalle-stompers")


def norm_name(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to Firestore (default: dry run)")
    args = ap.parse_args()

    data = json.loads(CONTACTS.read_text())
    db = firestore.Client(project=PROJECT_ID)

    team = db.collection("teams").document("main").get()
    if not team.exists:
        print("FATAL: teams/main not found"); return 1
    roster = team.to_dict().get("roster") or []
    by_name = {norm_name(p.get("name")): p for p in roster}
    print(f"Live roster: {len(roster)} players")

    plans, misses = [], []
    for entry in data["players"]:
        player = by_name.get(norm_name(entry["name"]))
        if not player:
            misses.append(entry["name"])
            continue
        for parent in entry["parents"]:
            email = parent["email"].strip().lower()
            plans.append({
                "email": email,
                "name": parent["name"].strip(),
                "relationship": (parent.get("relationship") or "").strip(),
                "player_id": player["id"],
                "player_name": player.get("name"),
            })

    # One parent may have several kids — merge playerIds per email.
    merged: dict[str, dict] = {}
    for p in plans:
        m = merged.setdefault(p["email"], {**p, "player_ids": []})
        if p["player_id"] not in m["player_ids"]:
            m["player_ids"].append(p["player_id"])

    print(f"\nPlan: {len(merged)} allowedUsers docs "
          f"({sum(len(m['player_ids']) for m in merged.values())} kid links)")
    if misses:
        print(f"UNMATCHED players (fix roster or contacts file first!): {misses}")

    now = int(time.time() * 1000)
    wrote_parent = wrote_coach = 0
    for email, m in sorted(merged.items()):
        ref = db.collection("allowedUsers").document(email)
        snap = ref.get()
        existing = snap.to_dict() if snap.exists else {}
        is_coach = existing.get("role") == "coach"
        pids = list(dict.fromkeys((existing.get("playerIds") or []) + m["player_ids"]))
        if is_coach:
            update = {"playerIds": pids}
            action = f"COACH  {email:44s} +playerIds={pids}"
        else:
            update = {
                "role": "parent",
                "name": m["name"],
                "playerIds": pids,
                "addedVia": existing.get("addedVia") or "roster-import",
                "addedAt": existing.get("addedAt") or now,
            }
            if m["relationship"]:
                update["relationship"] = m["relationship"]
            action = (f"{'UPDATE' if snap.exists else 'CREATE'} {email:44s} "
                      f"{m['name']} ({m['relationship'] or '?'}) -> {m['player_name']}")
        print(("[dry] " if not args.apply else "[set] ") + action)
        if args.apply:
            ref.set(update, merge=True)
            wrote_coach += 1 if is_coach else 0
            wrote_parent += 0 if is_coach else 1

    if args.apply:
        print(f"\nDone: {wrote_parent} parent docs upserted, {wrote_coach} coach docs given playerIds.")
    else:
        print("\nDry run only — re-run with --apply to write.")
    return 0 if not misses else 2


if __name__ == "__main__":
    sys.exit(main())
