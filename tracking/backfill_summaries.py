#!/usr/bin/env python3
"""Backfill the season-view summary doc for games analysed before it existed.

The pipeline now writes `analytics/summary` alongside `analytics/v1`, but the
seven already-analysed games have no summary, and the season view needs one for
every game or it falls back to the ~1 MB full doc it was choking on.

Read-only with respect to `v1`: it reads each full doc and writes a projection to
a NEW document id, so a bad run costs nothing but a rewrite.

    PYTHONPATH=. .venv-post-game/bin/python -m tracking.backfill_summaries [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from post_game import config, firestore_io

    games = firestore_io._team_doc().collection("games")
    before = after = 0
    for g in games.stream():
        full = (games.document(g.id).collection("analytics")
                .document(config.ANALYTICS_DOC_VERSION).get())
        if not full.exists:
            continue
        d = full.to_dict() or {}
        nb = len(json.dumps(d, default=str))
        before += nb
        if args.dry_run:
            # Mirror write_analytics_summary's projection without writing.
            proj = {k: d[k] for k in firestore_io._SUMMARY_KEYS if k in d}
            if isinstance(d.get("player_stats"), list):
                proj["player_stats"] = [
                    {k: s[k] for k in firestore_io._SUMMARY_PLAYER_KEYS if k in s}
                    for s in d["player_stats"] if isinstance(s, dict)]
            na = len(json.dumps(proj, default=str))
        else:
            firestore_io.write_analytics_summary(g.id, d)
            na = len(json.dumps(
                (games.document(g.id).collection("analytics")
                 .document(config.ANALYTICS_SUMMARY_DOC).get().to_dict() or {}),
                default=str))
        after += na
        print(f"{g.id}  {nb/1024:>7.0f} KB -> {na/1024:>6.1f} KB")

    print(f"\nseason view fetch: {before/1024/1024:.2f} MB -> "
          f"{after/1024/1024:.3f} MB"
          + ("  (--dry-run: nothing written)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
