#!/usr/bin/env python3
"""Report the size of each analytics doc, and of its largest keys.

The season view fetches analytics/v1 for EVERY finished game in one
`Promise.all`, so its memory cost is the sum of all docs. Firestore's 1 MiB
per-document limit bounds each one, but nothing bounds the sum -- and the PWA
then builds per-player aggregates over all of it on the main thread.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    from post_game import firestore_io

    games = firestore_io._team_doc().collection("games")
    total = 0
    for g in games.stream():
        snap = games.document(g.id).collection("analytics").document("v1").get()
        if not snap.exists:
            continue
        d = snap.to_dict() or {}
        blob = json.dumps(d, default=str)
        total += len(blob)
        parts = sorted(
            ((len(json.dumps(v, default=str)), k) for k, v in d.items()),
            reverse=True)[:5]
        print(f"{g.id}  {len(blob)/1024:>8.0f} KB   "
              + "  ".join(f"{k}={n/1024:.0f}KB" for n, k in parts))
    print(f"\nTOTAL fetched by the season view: {total/1024/1024:.2f} MB")


if __name__ == "__main__":
    main()
