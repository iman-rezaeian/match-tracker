"""How much tracked time sits in tracklets that SPAN a coach sub boundary?

Assignment is tracklet-global: the greedy pass in identity_assign gives each
whole tracklet exactly one player. So a tracklet whose span crosses a logged SUB
is unassignable-by-construction — it is either two different children welded
together, or one child with a bench gap in the middle. Either way one player
label for the whole thing is wrong for part of it.

This probe quantifies the opportunity BEFORE any fix is built: if only a few
percent of track-time straddles, splitting cannot move the score and the idea
should be dropped rather than implemented.

Read-only. Reuses the stage-4 cache written by tracking/eval_identity.py and
tracking/half_split_r.py.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.sub_straddle_probe --game-id mri01pvelv46d
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from post_game import config, firestore_io
from post_game.identity import (
    half_windows,
    period_clock_to_video_time_factory,
    _onfield_intervals,
)

OUT_DIR = Path(__file__).resolve().parent / "outputs" / "identity_eval"


def _sub_times(game, clock_to_video) -> list[float]:
    """Video-second times of every logged SUB event."""
    out = []
    for e in game.events or []:
        if (e.type or "").upper() != "SUB":
            continue
        try:
            out.append(float(clock_to_video(e.period, e.elapsed)))
        except Exception:
            continue
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--guard-s", type=float, default=5.0,
                    help="ignore a boundary within this much of a tracklet's own edge "
                         "(splitting there would only shave a sliver)")
    args = ap.parse_args()

    game = firestore_io.get_game(args.game_id)
    s4_parquet = OUT_DIR / f"{args.game_id}.stage4.parquet"
    s4_maps = OUT_DIR / f"{args.game_id}.stage4.json"
    if not (s4_parquet.exists() and s4_maps.exists()):
        raise SystemExit(f"No stage-4 cache for {args.game_id}. "
                         "Run tracking.half_split_r first to build it.")
    tracks_df = pd.read_parquet(s4_parquet)
    maps = json.loads(s4_maps.read_text())
    team_of_track = {int(k): v for k, v in maps["team_of_track"].items()}
    tracklet_of_track = {int(k): v for k, v in maps["tracklet_of_track"].items()}

    clock_to_video = period_clock_to_video_time_factory(game)
    duration_s = float(tracks_df["time_s"].max()) + 1.0
    subs = _sub_times(game, clock_to_video)
    halves = half_windows(game, duration_s)
    onfield = _onfield_intervals(game.starting_lineup, game.events, clock_to_video,
                                 video_end_s=duration_s)

    # Our-team detections only — opponents are never named.
    ours = {t for t, tm in team_of_track.items() if tm == 0}
    df = tracks_df[tracks_df["track_id"].isin(ours)].copy()
    if df.empty:
        raise SystemExit("no our-team detections in the stage-4 cache")
    df["tracklet"] = df["track_id"].map(lambda t: tracklet_of_track.get(int(t), int(t)))

    # Per-tracklet span and real tracked time (detection count x median dt, the
    # same convention identity_assign._tl_minutes uses — a span would wildly
    # over-count because a track id survives gaps).
    _dts = df.sort_values(["track_id", "time_s"]).groupby("track_id")["time_s"].diff().dropna()
    dt_med = float(_dts[_dts > 0].median()) if len(_dts) else 0.1
    g = df.groupby("tracklet")["time_s"]
    span = pd.DataFrame({"t0": g.min(), "t1": g.max(), "n": g.size()})
    span["tracked_s"] = span["n"] * dt_med

    def _crossings(t0: float, t1: float) -> list[float]:
        return [s for s in subs
                if (t0 + args.guard_s) < s < (t1 - args.guard_s)]

    span["n_cross"] = [len(_crossings(a, b)) for a, b in zip(span["t0"], span["t1"])]
    straddling = span[span["n_cross"] > 0]

    tot_s = float(span["tracked_s"].sum())
    str_s = float(straddling["tracked_s"].sum())

    print(f"=== sub-boundary straddling — {args.game_id} ===")
    print(f"logged SUB events              : {len(subs)}")
    print(f"our-team tracklets             : {len(span)}")
    print(f"  ... that cross >=1 sub       : {len(straddling)} "
          f"({100.0 * len(straddling) / max(1, len(span)):.1f}%)")
    print(f"tracked time, all tracklets    : {tot_s / 60.0:.1f} min")
    print(f"tracked time, straddling ones  : {str_s / 60.0:.1f} min "
          f"({100.0 * str_s / max(1e-9, tot_s):.1f}%)   <-- the opportunity")
    print()

    # Substantial tracklets are the ones that actually carry the stats; a
    # straddling 2-second fragment is irrelevant either way.
    for thresh in (30.0, 60.0, 120.0):
        sub_s = span[span["tracked_s"] >= thresh]
        sub_str = sub_s[sub_s["n_cross"] > 0]
        if len(sub_s) == 0:
            continue
        print(f"tracklets >= {thresh:>5.0f}s tracked : {len(sub_s):>4}  "
              f"straddling {len(sub_str):>4} ({100.0 * len(sub_str) / len(sub_s):>5.1f}%)  "
              f"= {float(sub_str['tracked_s'].sum()) / 60.0:>6.1f} min "
              f"({100.0 * float(sub_str['tracked_s'].sum()) / max(1e-9, float(sub_s['tracked_s'].sum())):>5.1f}% of their time)")
    print()

    # How many pieces would splitting produce, and how big are they? A split
    # that mostly yields sub-30s crumbs trades one bad label for several
    # unusable ones.
    pieces = []
    for tl, row in straddling.iterrows():
        cuts = _crossings(row["t0"], row["t1"])
        edges = [row["t0"]] + cuts + [row["t1"]]
        sub = df[df["tracklet"] == tl]["time_s"].to_numpy()
        for a, b in zip(edges[:-1], edges[1:]):
            pieces.append(float(((sub >= a) & (sub < b)).sum()) * dt_med)
    if pieces:
        p = np.array(pieces)
        print(f"splitting would yield {len(p)} pieces from {len(straddling)} tracklets")
        print(f"  median piece {np.median(p):.1f}s   "
              f"<30s: {100.0 * (p < 30).mean():.0f}%   "
              f">=60s: {100.0 * (p >= 60).mean():.0f}%")
        print(f"  time landing in >=30s pieces: {p[p >= 30].sum() / 60.0:.1f} min "
              f"of {p.sum() / 60.0:.1f} min")

    # Cross-check against the halftime boundary: a tracklet spanning the break
    # is a different (known) pathology and shouldn't be credited to subs.
    if len(halves) >= 2:
        ht0, ht1 = halves[0][1], halves[1][0]
        ht = span[(span["t0"] < ht0) & (span["t1"] > ht1)]
        print(f"\ntracklets spanning HALFTIME ({ht0:.0f}-{ht1:.0f}s): {len(ht)} "
              f"({float(ht['tracked_s'].sum()) / 60.0:.1f} min)")

    out = {
        "game_id": args.game_id,
        "n_subs": len(subs),
        "n_tracklets": int(len(span)),
        "n_straddling": int(len(straddling)),
        "tracked_min_total": round(tot_s / 60.0, 2),
        "tracked_min_straddling": round(str_s / 60.0, 2),
        "straddling_time_frac": round(str_s / max(1e-9, tot_s), 4),
    }
    (OUT_DIR / f"{args.game_id}.straddle.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
