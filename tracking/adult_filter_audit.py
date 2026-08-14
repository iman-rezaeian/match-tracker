#!/usr/bin/env python3
"""Score the sideline-adult filter against the coach's CLICKS.

Why re-score a filter that already has numbers
----------------------------------------------
`post_game/adult_filter.py` was measured on the two blind-GT games, where the
truth signal was a hand-label per tracklet. Those games are not the ones the
team-shape metrics are actually published for, and a per-tracklet label says
nothing about whether the body was on the pitch at a given instant.

The click corpus is a better instrument, and it did not exist when the filter
was written. Each click is the coach asserting "one of MY players was here, at
this second". So for any frame the coach worked we know a lower bound on the
real team, and we can ask the only question that matters for team shape:

    does the filter remove bodies that are NOT near a clicked player, while
    keeping the ones that ARE?

A filter that drops adults keeps its click-matched rows and loses unmatched
ones. A filter that eats our own team does the opposite, and the earlier
opponent-filter disaster is exactly what that looks like when nobody checks.

⚠ This measures the filter on CLICKED FRAMES ONLY (~97 frames of one game).
That is the sample where truth exists; it is not the whole game.

Needs the track cache at `post_game/outputs/<game>/tracks_raw.parquet`. That path
is gitignored, so in a worktree symlink it in first:
    ln -s /path/to/main/checkout/post_game/outputs post_game/outputs

Matching is done in EQUIRECT PIXEL space, by asking whether the click falls
inside the tracked box -- deliberately not in field metres. A click lands on the
torso while a track is keyed on the feet, and that ~45 px offset has already
produced one fake accuracy figure in this project. Box containment has no such
offset: a torso click is inside its own player's box by construction, so no
radius needs choosing and no projection error enters.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Boxes are grown by this many pixels before testing containment, so a click a
# few pixels outside a tight box still matches. Generous on purpose: counting
# MORE rows as ours can only make the filter look worse, never better.
BOX_PAD_PX = 15.0
# Tracked rows within this many seconds of a click's timestamp.
TIME_TOL_S = 0.2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--thresholds", default="100,110,120,130,150")
    args = ap.parse_args()

    from post_game.adult_filter import adult_track_ids
    from post_game.click_samples import load_clicks

    root = Path(f"post_game/outputs/{args.game_id}")
    df = pd.read_parquet(root / "tracks_raw.parquet")
    print(f"{args.game_id}: {len(df):,} rows, {df.track_id.nunique():,} tracks")

    h = df.groupby("track_id").bbox_h_crop.median()
    print("per-track median box height: "
          f"p10={h.quantile(.1):.0f} p50={h.median():.0f} p90={h.quantile(.9):.0f}")

    clicks = load_clicks(Path(f"tracking/outputs/click_samples/{args.game_id}/clicks.jsonl"))
    print(f"{len(clicks)} clicks over "
          f"{len({round(float(c['video_time_s']), 2) for c in clicks})} frames\n")

    # Tag every tracked row on a clicked frame as matched / unmatched.
    by_t: dict[float, list[tuple[float, float]]] = {}
    for c in clicks:
        by_t.setdefault(round(float(c["video_time_s"]), 2), []).append(
            (float(c.get("raw_x_eq", c["click_x_eq"])),
             float(c.get("raw_y_eq", c["click_y_eq"]))))

    rows = []
    for t, cs in by_t.items():
        near = df[(df.time_s >= t - TIME_TOL_S) & (df.time_s <= t + TIME_TOL_S)]
        if near.empty:
            continue
        cx = np.array([c[0] for c in cs])
        cy = np.array([c[1] for c in cs])
        inside = (
            (cx[None, :] >= near.x1_eq.to_numpy()[:, None] - BOX_PAD_PX)
            & (cx[None, :] <= near.x2_eq.to_numpy()[:, None] + BOX_PAD_PX)
            & (cy[None, :] >= near.y1_eq.to_numpy()[:, None] - BOX_PAD_PX)
            & (cy[None, :] <= near.y2_eq.to_numpy()[:, None] + BOX_PAD_PX)
        )
        rows.append(pd.DataFrame({
            "track_id": near.track_id.to_numpy(),
            "bbox_h_crop": near.bbox_h_crop.to_numpy(),
            "matched": inside.any(axis=1),
        }))
    ev = pd.concat(rows, ignore_index=True)
    n_ours, n_other = int(ev.matched.sum()), int((~ev.matched).sum())
    print(f"on clicked frames: {len(ev):,} tracked rows — {n_ours:,} match a click "
          f"(OURS), {n_other:,} do not (opponents + adults + phantoms)")
    print(f"  median height  ours {ev[ev.matched].bbox_h_crop.median():.0f} px | "
          f"unmatched {ev[~ev.matched].bbox_h_crop.median():.0f} px\n")

    def score(label: str, ids: set[int]) -> None:
        cut = ev.track_id.isin(ids)
        kept = ev[~cut]
        keep_ours = 100.0 * kept.matched.sum() / max(1, n_ours)
        cut_other = 100.0 * (cut & ~ev.matched).sum() / max(1, n_other)
        purity = 100.0 * kept.matched.sum() / max(1, len(kept))
        game_cut = 100.0 * df.track_id.isin(ids).mean()
        print(f"{label:>13} {keep_ours:>10.1f}% {cut_other:>13.1f}% "
              f"{purity:>7.1f}% {game_cut:>18.1f}%")

    print(f"{'filter':>13} {'ours kept':>11} {'unmatched cut':>14} {'purity':>8} "
          f"{'full-game rows cut':>19}")
    score("none", set())
    # The shipped one-sided cut, for comparison.
    for th in [float(x) for x in args.thresholds.split(",")]:
        score(f"h>={th:.0f}", adult_track_ids(df, th))

    # Two-sided band. Our players are the MIDDLE of the height distribution, so
    # a cut that only trims the tall tail leaves a small-box population that is
    # a purer pollutant than the tall one.
    med = df.groupby("track_id").bbox_h_crop.median()
    cnt = df.groupby("track_id").size()
    from post_game.adult_filter import MIN_ROWS_TO_JUDGE
    for lo, hi in [(50, 160), (50, 200), (45, 180), (55, 150)]:
        judged = med[(cnt >= MIN_ROWS_TO_JUDGE) & ((med < lo) | (med > hi))]
        score(f"outside {lo}-{hi}", {int(t) for t in judged.index})


if __name__ == "__main__":
    main()
