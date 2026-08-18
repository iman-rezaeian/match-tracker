#!/usr/bin/env python3
"""READ-ONLY: find WHERE inside a stitched chain the identity drifts.

Blind ground truth (tracking/labels/<game>_player_gt/gt.csv) now says 26 of the
30 biggest tracklets in mri01pvelv46d hold more than one child. That is a
verdict on the whole chain; it does not say which link crossed. Without that,
any merge guard is tuned against a proxy — which is exactly how the last three
knobs got their thresholds.

So: rebuild the chain the stitcher built, walk its joins in time order, and
record what each join looked like at the moment it was made (gap, metre
distance, implied speed, appearance cosine, kit votes on either side). Then
contrast the joins inside GT-clean chains against those inside GT-mixed ones.
If a feature separates them, that feature is the guard. If none does, the guard
has to come from somewhere other than geometry, and we should stop tuning.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.chain_drift_probe --game-id mri01pvelv46d
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

from post_game import config
from post_game.reid_stitch import _track_summaries, _overlaps_in_time, _cosine


def load_gt(game_id: str) -> dict[str, dict]:
    p = Path("tracking/labels") / f"{game_id}_player_gt" / "gt.csv"
    if not p.exists():
        raise SystemExit(f"no ground truth at {p}")
    return {str(r["tracklet_id"]): r for r in csv.DictReader(p.open())}


def rebuild(game_id: str, out_dir: Path):
    """Replay stages 3 -> 4b on the cached tracks, exactly as the pipeline does.

    The cached parquet is the RAW stage-2 output: pixel coords only, no metres,
    no off-field filter, no halftime split. The stitcher keys entirely off
    ``x_m``/``y_m`` and silently returns an identity mapping without them, which
    is why a naive replay recovers no chains at all. So the projection and the
    three filters have to be reproduced here or the chain ids won't match the
    published ones.
    """
    from post_game import firestore_io
    from post_game.calibration import FieldProjector
    from post_game.team_classifier import classify_from_kit_votes
    from post_game.reid_stitch import stitch_tracklets
    from post_game.pipeline import _our_color

    tracks = pd.read_parquet(out_dir / "tracks_raw.parquet")
    game = firestore_io.get_game(game_id)
    field_cal = firestore_io.get_game_calibration(game_id)
    if field_cal is None:
        raise SystemExit(f"no per-game calibration for {game_id}")

    kv = np.load(out_dir / "kit_votes.npz", allow_pickle=True)
    kit_votes = {int(k): np.asarray(kv[k]) for k in kv.files}
    emb = np.load(out_dir / "embeddings.npz", allow_pickle=True)
    track_embeddings = {int(k): emb[k] for k in emb.files}

    # --- stage 3: pixel -> metres, then the off-field + top-20 filters --------
    projector = FieldProjector(field_cal)
    foot = tracks[["foot_x_eq", "foot_y_eq"]].to_numpy()
    xy = projector.pixel_to_field_batch(foot)
    tracks["x_m"], tracks["y_m"] = xy[:, 0], xy[:, 1]
    L, W = field_cal.length_m, field_cal.width_m
    tracks = tracks.loc[
        (tracks["x_m"] >= -1.5) & (tracks["x_m"] <= L + 1.5)
        & (tracks["y_m"] >= -1.5) & (tracks["y_m"] <= W + 1.5)
    ].reset_index(drop=True)
    lifetime = tracks.groupby("track_id").size().rename("track_lifetime")
    tracks = tracks.merge(lifetime, on="track_id")
    score = tracks["track_lifetime"].astype(float) * tracks["conf"].astype(float).clip(lower=0.1)
    tracks["_rank_score"] = score
    tracks = (tracks.sort_values(["frame", "_rank_score"], ascending=[True, False])
              .groupby("frame", group_keys=False).head(20)
              .drop(columns=["_rank_score", "track_lifetime"]).reset_index(drop=True))

    # --- stage 3.4: halftime split (renumbers ids; must run before classify) --
    if config.HALFTIME_SPLIT_ENABLED:
        from post_game.halftime_split import detect_halftime_break, split_tracks_at_halftime
        bw = detect_halftime_break(tracks)
        if bw is not None:
            tracks, _, track_embeddings, _ = split_tracks_at_halftime(
                tracks, bw, {}, track_embeddings)

    team_of_track = classify_from_kit_votes(tracks, kit_votes)
    mapping = stitch_tracklets(
        tracks, team_of_track, track_embeddings=track_embeddings,
        our_color_hex=_our_color(game), opp_color_hex=game.away_color,
    )
    return tracks, team_of_track, track_embeddings, mapping, kit_votes


def joins_of_chain(members: list[int], summ: dict) -> list[tuple[int, int]]:
    """Consecutive (prev, cur) pairs in time order — the chain as it reads."""
    ms = sorted(members, key=lambda t: summ[t]["t0"])
    return list(zip(ms[:-1], ms[1:]))


def describe_join(a: int, b: int, summ: dict, emb: dict, votes: dict) -> dict:
    sa, sb = summ[a], summ[b]
    gap = float(sb["t0"] - sa["t1"])
    dist = float(np.hypot(sb["p0"][0] - sa["p1"][0], sb["p0"][1] - sa["p1"][1]))
    ea, eb = emb.get(a), emb.get(b)
    cos = float(_cosine(ea, eb)) if ea is not None and eb is not None else float("nan")

    def kit(t):
        v = votes.get(t)
        if v is None:
            return (0, 0)
        v = np.asarray(v).ravel()
        return (int(v[0]), int(v[1])) if v.size >= 2 else (0, 0)

    ka, kb = kit(a), kit(b)
    return {
        "a": a, "b": b, "gap_s": gap, "dist_m": dist,
        "speed_ms": dist / gap if gap > 0.05 else float("inf"),
        "cos": cos,
        "overlap": bool(_overlaps_in_time(sa, sb)),
        "kit_a_ours": ka[0], "kit_a_opp": ka[1],
        "kit_b_ours": kb[0], "kit_b_opp": kb[1],
        "dur_a": float(sa["t1"] - sa["t0"]), "dur_b": float(sb["t1"] - sb["t0"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    out_dir = config.OUTPUTS_DIR / args.game_id
    gt = load_gt(args.game_id)
    tracks, team, emb, mapping, votes = rebuild(args.game_id, out_dir)

    our = {int(t) for t, tm in team.items() if tm == 0}
    summ = _track_summaries(tracks, our)
    chains: dict[int, list[int]] = {}
    for t, root in mapping.items():
        if int(t) in our:
            chains.setdefault(int(root), []).append(int(t))

    rows = []
    for tid, g in gt.items():
        members = chains.get(int(tid))
        if not members:
            continue
        clean = bool(g["true_player_id"])
        for j in joins_of_chain(members, summ):
            d = describe_join(j[0], j[1], summ, emb, votes)
            d.update(tracklet=tid, clean=clean, n_frag=len(members),
                     minutes=float(g["minutes"]))
            rows.append(d)

    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("no joins recovered — chain membership did not reproduce")

    print(f"joins recovered: {len(df)}  across {df['tracklet'].nunique()} labelled tracklets")
    print(f"  in GT-CLEAN chains: {int((df['clean']).sum())}")
    print(f"  in GT-MIXED chains: {int((~df['clean']).sum())}\n")

    feats = ["gap_s", "dist_m", "speed_ms", "cos", "dur_a", "dur_b", "n_frag"]
    print(f"{'feature':<10}{'clean median':>14}{'mixed median':>14}{'clean p90':>12}{'mixed p90':>12}")
    print("-" * 62)
    for f in feats:
        c = df.loc[df["clean"], f].replace([np.inf], np.nan).dropna()
        m = df.loc[~df["clean"], f].replace([np.inf], np.nan).dropna()
        if c.empty or m.empty:
            continue
        print(f"{f:<10}{c.median():>14.2f}{m.median():>14.2f}"
              f"{c.quantile(.9):>12.2f}{m.quantile(.9):>12.2f}")

    print("\nfragment count per chain:")
    per = df.groupby(["tracklet", "clean"])["n_frag"].first().reset_index()
    for cl in (True, False):
        s = per.loc[per["clean"] == cl, "n_frag"]
        if len(s):
            print(f"  {'CLEAN' if cl else 'MIXED'}: n={len(s)} median={s.median():.0f} "
                  f"min={s.min()} max={s.max()}")

    if args.json_out:
        Path(args.json_out).write_text(df.to_json(orient="records"))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
