#!/usr/bin/env python3
"""Read-only A/B harness for the gap-split + stitch-tuning change.

Runs stage 3->5 (filter -> [gap-split] -> classify -> stitch -> assign) on a game's
CACHED tracks_raw.parquet and reports NAMED-COVERAGE (our detection-seconds mapped to
a player / total our detection-seconds) plus per-player minutes + status counts.
ZERO Firestore writes — safe to run while a live game's analytics doc is being written.

Compare flags-off vs flags-on, e.g.:
  python -m tracking.eval_stitch_assign --game-id mqcf9axlvtuyt --label baseline
  python -m tracking.eval_stitch_assign --game-id mqcf9axlvtuyt --label split \
      --gap-split --app-weight 0.5 --dist-cap-m 12
"""
from __future__ import annotations
import argparse, os
from collections import defaultdict
from pathlib import Path

import numpy as np


def _load_jersey_medians(npz_path: Path) -> dict[int, list]:
    """{track_id: [median_hsv_3vec]} — same reduction classify_tracks applies."""
    out: dict[int, list] = {}
    with np.load(npz_path, allow_pickle=True) as nz:
        for k in nz.files:
            samples = list(nz[k])
            if not samples:
                continue
            stacked = np.vstack([np.asarray(s, dtype=np.float32) for s in samples])
            out[int(k)] = [np.median(stacked, axis=0).astype(np.float32)]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--label", default="run")
    ap.add_argument("--gap-split", action="store_true", help="enable the gap-split pre-pass")
    ap.add_argument("--split-gap-s", type=float, default=None)
    ap.add_argument("--app-weight", type=float, default=None, help="override STITCH_APP_WEIGHT")
    ap.add_argument("--dist-cap-m", type=float, default=None, help="override STITCH_DIST_CAP_M")
    args = ap.parse_args()
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

    import pandas as pd
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector
    from post_game.identity import half_windows, period_clock_to_video_time_factory
    from post_game.identity_assign import assign_identities_v2
    from post_game.pipeline import _our_color
    from post_game.reid_stitch import stitch_tracklets, stitch_stats
    from post_game.team_classifier import classify_tracks
    from post_game.gap_split import gap_split_tracks

    # runtime config overrides (read at call time inside stitch/assign)
    if args.app_weight is not None:
        config.STITCH_APP_WEIGHT = args.app_weight
    if args.dist_cap_m is not None:
        config.STITCH_DIST_CAP_M = args.dist_cap_m
    split_gap = args.split_gap_s if args.split_gap_s is not None else config.SPLIT_GAP_S

    game = firestore_io.get_game(args.game_id)
    roster = firestore_io.get_roster()
    name_of = {r.id: r.name for r in roster}
    cal = firestore_io.get_game_calibration(args.game_id)
    if cal is None:
        raise SystemExit("No calibration — can't project to field.")
    L, W = cal.length_m, cal.width_m
    ckpt = config.OUTPUTS_DIR / args.game_id

    tracks_df = pd.read_parquet(ckpt / "tracks_raw.parquet")
    jersey = _load_jersey_medians(ckpt / "jersey_samples.npz")
    embeddings = {}
    if (ckpt / "embeddings.npz").exists():
        with np.load(ckpt / "embeddings.npz", allow_pickle=True) as nz:
            embeddings = {int(k): np.asarray(nz[k], dtype=np.float32) for k in nz.files}

    # stage 3: project + off-field + top-20/frame (mirrors pipeline.py)
    proj = FieldProjector(cal)
    xy = proj.pixel_to_field_batch(tracks_df[["foot_x_eq", "foot_y_eq"]].to_numpy())
    tracks_df["x_m"], tracks_df["y_m"] = xy[:, 0], xy[:, 1]
    on = ((tracks_df["x_m"] >= -1.5) & (tracks_df["x_m"] <= L + 1.5)
          & (tracks_df["y_m"] >= -1.5) & (tracks_df["y_m"] <= W + 1.5))
    tracks_df = tracks_df.loc[on].reset_index(drop=True)
    lifetime = tracks_df.groupby("track_id").size().rename("track_lifetime")
    tracks_df = tracks_df.merge(lifetime, on="track_id")
    score = tracks_df["track_lifetime"].astype(float)
    if "conf" in tracks_df.columns:
        score = score * tracks_df["conf"].astype(float).clip(lower=0.1)
    tracks_df["_rank_score"] = score
    ranked = tracks_df.sort_values(["frame", "_rank_score"], ascending=[True, False])
    tracks_df = (ranked.groupby("frame", group_keys=False).head(20)
                 .drop(columns=["_rank_score", "track_lifetime"]).reset_index(drop=True))
    n_raw = tracks_df["track_id"].nunique()

    if args.gap_split:
        tracks_df, jersey, embeddings, _ = gap_split_tracks(
            tracks_df, jersey, embeddings, split_gap_s=split_gap)
        print(f"gap-split: {n_raw} -> {tracks_df['track_id'].nunique()} sub-tracks (gap>{split_gap}s)")

    team_of_track = classify_tracks(
        tracks_df, jersey, our_home_color_hex=_our_color(game),
        opp_color_hex=game.away_color, ref_color_hex=game.ref_color)
    tracklet_of_track = stitch_tracklets(
        tracks_df, team_of_track, track_embeddings=embeddings, track_jersey_samples=jersey)
    ss = stitch_stats(tracklet_of_track, team_of_track)

    play_windows = half_windows(game, float(tracks_df["time_s"].max()) + 1.0)
    clock_to_video = period_clock_to_video_time_factory(game)
    assignments = assign_identities_v2(
        tracks_df=tracks_df, tracklet_of_track=tracklet_of_track, team_of_track=team_of_track,
        events=game.events, roster=roster, starting_lineup=game.starting_lineup,
        gk_player_id=game.gk_player_id, period_clock_to_video_time=clock_to_video,
        periods_video=play_windows, field_length_m=L, field_width_m=W,
        overrides=None, squad=game.squad)

    # named-coverage: charge each track detection_count * median_dt (== pipeline _tl_minutes)
    dts = tracks_df.sort_values(["track_id", "time_s"]).groupby("track_id")["time_s"].diff().dropna()
    dt_med = float(dts[dts > 0].median()) if len(dts) else 0.1
    counts = tracks_df.groupby("track_id").size()
    our = {int(t) for t, tm in team_of_track.items() if tm == 0}
    id_by_track = {a.track_id: a.player_id for a in assignments if a.player_id}
    total_our_s = sum(int(counts.get(t, 0)) for t in our) * dt_med
    named_our_s = sum(int(counts.get(t, 0)) for t in our if t in id_by_track) * dt_med
    per_player = defaultdict(float)
    for t in our:
        pid = id_by_track.get(t)
        if pid:
            per_player[pid] += int(counts.get(t, 0)) * dt_med
    status = defaultdict(int)
    for a in assignments:
        status[a.status] += 1

    print(f"\n==== {args.label} (gap_split={args.gap_split}, app_weight={config.STITCH_APP_WEIGHT}, "
          f"dist_cap={config.STITCH_DIST_CAP_M}) ====")
    print(f"our fragments={ss['our_fragments']} -> tracklets={ss['our_tracklets']} "
          f"(merged={ss['merged_tracklets']}, largest={ss['largest_tracklet_fragments']})")
    print(f"status: {dict(sorted(status.items()))}")
    print(f"named tracks: {sum(1 for t in our if t in id_by_track)}/{len(our)} "
          f"| NAMED-COVERAGE: {named_our_s:.0f}/{total_our_s:.0f}s = {100*named_our_s/max(1,total_our_s):.1f}%")
    print("per-player named minutes:")
    for pid in sorted(per_player, key=lambda p: -per_player[p]):
        print(f"  {name_of.get(pid, pid)[:16]:<16} {per_player[pid]/60:>6.1f} min")


if __name__ == "__main__":
    main()
