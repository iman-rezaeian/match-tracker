"""Offline identity-assignment evaluation from cached pipeline checkpoints.

Re-runs stages 3-5 (field projection → team classification → stitching →
assign_identities_v2) from a game's cached tracks_raw.parquet /
jersey_samples.npz / embeddings.npz, WITHOUT touching Firestore analytics
(pipeline.run would overwrite production docs). Scores the AUTO assignment
against the coach's identityOverrides labels (predictions are made with
overrides withheld), and reports GK-window keeper attribution.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.eval_identity --game-id mq01kuce2i81r --label gk-fix

Re-run with the same --game-id before/after identity changes and diff the
JSON in tracking/outputs/identity_eval/.

Memory note: jersey_samples.npz can be multi-GB; we stream it key-by-key and
keep only each track's median HSV (exactly what classify_tracks reduces to).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from post_game import config, firestore_io
from post_game.calibration import FieldProjector
from post_game.identity import half_windows, period_clock_to_video_time_factory, _onfield_intervals
try:
    from post_game.identity_assign import assign_identities_v2, _gk_windows
except ImportError:  # pre-gk-fix code (no _gk_windows) — for before/after evals
    from post_game.identity_assign import assign_identities_v2
    _gk_windows = None
from post_game.pipeline import _our_color
from post_game.reid_stitch import stitch_tracklets, stitch_stats
from post_game.team_classifier import classify_tracks

OUT_DIR = Path(__file__).resolve().parent / "outputs" / "identity_eval"


def _load_jersey_medians(npz_path: Path) -> dict[int, list]:
    """{track_id: [median_hsv_3vec]} — same reduction classify_tracks applies."""
    out: dict[int, list] = {}
    with np.load(npz_path, allow_pickle=True) as nz:
        for k in nz.files:
            samples = nz[k]
            if len(samples) == 0:
                continue
            stacked = np.vstack([np.asarray(s, dtype=np.float32) for s in samples])
            out[int(k)] = [np.median(stacked, axis=0)]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--label", required=True, help="output filename stem")
    args = ap.parse_args()

    import pandas as pd

    game = firestore_io.get_game(args.game_id)
    roster = firestore_io.get_roster()
    field_cal = firestore_io.get_game_calibration(args.game_id)
    if field_cal is None:
        raise SystemExit("No calibration on game doc — can't project to field.")
    name_of = {r.id: r.name for r in roster}
    L, W = field_cal.length_m, field_cal.width_m

    # Stage 3+4 cache: classify+stitch are deterministic per checkpoint, and
    # the jersey npz takes minutes to stream — cache the slim result so
    # identity_assign iterations take seconds.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    s4_parquet = OUT_DIR / f"{args.game_id}.stage4.parquet"
    s4_maps = OUT_DIR / f"{args.game_id}.stage4.json"
    if s4_parquet.exists() and s4_maps.exists():
        tracks_df = pd.read_parquet(s4_parquet)
        maps = json.loads(s4_maps.read_text())
        team_of_track = {int(k): v for k, v in maps["team_of_track"].items()}
        tracklet_of_track = {int(k): v for k, v in maps["tracklet_of_track"].items()}
        print(f"stage-4 cache: {len(tracks_df)} detections, "
              f"{tracks_df['track_id'].nunique()} tracks")
    else:
        ckpt = config.OUTPUTS_DIR / args.game_id
        tracks_df = pd.read_parquet(ckpt / "tracks_raw.parquet")
        print(f"checkpoint: {len(tracks_df)} detections, {tracks_df['track_id'].nunique()} tracks")
        jersey = _load_jersey_medians(ckpt / "jersey_samples.npz")
        embeddings = {}
        if (ckpt / "embeddings.npz").exists():
            with np.load(ckpt / "embeddings.npz", allow_pickle=True) as nz:
                embeddings = {int(k): np.asarray(nz[k], dtype=np.float32) for k in nz.files}
        print(f"jersey medians for {len(jersey)} tracks, embeddings for {len(embeddings)}")

        # --- stage 3: pixel -> field + off-field filter + top-20 (mirrors pipeline.py)
        projector = FieldProjector(field_cal)
        xy = projector.pixel_to_field_batch(tracks_df[["foot_x_eq", "foot_y_eq"]].to_numpy())
        tracks_df["x_m"], tracks_df["y_m"] = xy[:, 0], xy[:, 1]
        on_field = ((tracks_df["x_m"] >= -1.5) & (tracks_df["x_m"] <= L + 1.5)
                    & (tracks_df["y_m"] >= -1.5) & (tracks_df["y_m"] <= W + 1.5))
        tracks_df = tracks_df.loc[on_field].reset_index(drop=True)
        lifetime = tracks_df.groupby("track_id").size().rename("track_lifetime")
        tracks_df = tracks_df.merge(lifetime, on="track_id")
        score = tracks_df["track_lifetime"].astype(float)
        if "conf" in tracks_df.columns:
            score = score * tracks_df["conf"].astype(float).clip(lower=0.1)
        tracks_df["_rank_score"] = score
        ranked = tracks_df.sort_values(["frame", "_rank_score"], ascending=[True, False])
        tracks_df = (ranked.groupby("frame", group_keys=False).head(20)
                     .drop(columns=["_rank_score", "track_lifetime"]).reset_index(drop=True))

        # --- stage 4 + 4b
        team_of_track = classify_tracks(
            tracks_df, jersey,
            our_home_color_hex=_our_color(game),
            opp_color_hex=game.away_color,
            ref_color_hex=game.ref_color,
        )
        tracklet_of_track = stitch_tracklets(
            tracks_df, team_of_track,
            track_embeddings=embeddings, track_jersey_samples=jersey,
        )
        print("stitch:", stitch_stats(tracklet_of_track, team_of_track))
        keep_cols = [c for c in ("track_id", "frame", "time_s", "x_m", "y_m", "conf")
                     if c in tracks_df.columns]
        tracks_df = tracks_df[keep_cols]
        tracks_df.to_parquet(s4_parquet)
        s4_maps.write_text(json.dumps({
            "team_of_track": {str(k): int(v) for k, v in team_of_track.items()},
            "tracklet_of_track": {str(k): int(v) for k, v in tracklet_of_track.items()},
        }))
        print(f"stage-4 cache written: {s4_parquet.name}")

    # --- stage 5: AUTO assignment with coach overrides WITHHELD
    duration_s = float(tracks_df["time_s"].max()) + 1.0
    play_windows = half_windows(game, duration_s)
    clock_to_video = period_clock_to_video_time_factory(game)
    assignments = assign_identities_v2(
        tracks_df=tracks_df,
        tracklet_of_track=tracklet_of_track,
        team_of_track=team_of_track,
        events=game.events,
        roster=roster,
        starting_lineup=game.starting_lineup,
        gk_player_id=game.gk_player_id,
        period_clock_to_video_time=clock_to_video,
        periods_video=play_windows,
        field_length_m=L,
        field_width_m=W,
        overrides=None,  # withheld → labels stay a fair test set
        squad=game.squad,
    )

    # Per-tracklet prediction (members share the tracklet-level assignment).
    pred: dict[int, dict] = {}
    for a in assignments:
        tl = (a.breakdown or {}).get("tracklet")
        if tl is not None:
            pred[int(tl)] = {"player_id": a.player_id, "confidence": round(a.confidence, 3),
                             "status": a.status}

    # --- score vs coach labels (identityOverrides on the game doc)
    labels = {}
    for k, v in (game.identity_overrides or {}).items():
        try:
            labels[int(k)] = v  # player_id, or sentinel/None => "not our player"
        except (TypeError, ValueError):
            continue
    n_match = n_wrong = n_missed = n_label_hit = 0
    wrong_rows = []
    labeled_predictions = []  # every labeled tracklet's prediction, for threshold sweeps
    for tl, lab in sorted(labels.items()):
        p = pred.get(tl)
        if p is None:
            n_missed += 1  # tracklet id not reproduced by this run's stitch
            continue
        n_label_hit += 1
        lab_pid = lab if (lab and not str(lab).startswith("__")) else None
        labeled_predictions.append({
            "tracklet": tl, "label": lab, "label_pid": lab_pid,
            "pred": p["player_id"], "confidence": p["confidence"], "status": p["status"],
        })
        if p["player_id"] == lab_pid:
            n_match += 1
        else:
            n_wrong += 1
            wrong_rows.append({
                "tracklet": tl, "label": lab, "label_name": name_of.get(lab_pid),
                "pred": p["player_id"], "pred_name": name_of.get(p["player_id"]),
                "confidence": p["confidence"], "status": p["status"],
            })

    gkw = (_gk_windows(game.gk_player_id, game.events, clock_to_video, play_windows)
           if _gk_windows is not None else [])
    keeper_report = [
        {"t0": round(a, 1), "t1": round(b, 1), "player_id": p, "name": name_of.get(p)}
        for (a, b, p) in gkw
    ]
    keeper_assigned = sorted(
        {(p["player_id"], name_of.get(p["player_id"])) for p in pred.values()
         if p["status"] == "auto" and p["confidence"] == 0.95 and p["player_id"]},
    )

    status_counts: dict[str, int] = {}
    for p in pred.values():
        status_counts[p["status"]] = status_counts.get(p["status"], 0) + 1

    # Per-status precision on labeled tracklets: of tracklets this status tier
    # predicted, what fraction matched the coach label. The calibration guardrail
    # — precision-among-'auto' should RISE once template-only guesses are demoted.
    _sp: dict[str, dict] = {}
    for r in labeled_predictions:
        d = _sp.setdefault(r["status"], {"correct": 0, "total": 0})
        d["total"] += 1
        if r["pred"] == r["label_pid"]:
            d["correct"] += 1
    status_precision = {
        s: {"precision": round(d["correct"] / d["total"], 3) if d["total"] else None,
            "correct": d["correct"], "total": d["total"]}
        for s, d in sorted(_sp.items())
    }

    result = {
        "game_id": args.game_id,
        "label": args.label,
        "n_labels": len(labels),
        "labels_reproduced": n_label_hit,
        "labels_missed_by_stitch": n_missed,
        "correct": n_match,
        "wrong": n_wrong,
        "accuracy_on_reproduced": round(n_match / n_label_hit, 3) if n_label_hit else None,
        "tracklet_status_counts": dict(sorted(status_counts.items())),
        "status_precision": status_precision,
        "gk_windows": keeper_report,
        "keeper_assigned_players": [{"player_id": a, "name": b} for a, b in keeper_assigned],
        "wrong_rows": wrong_rows,
        "labeled_predictions": labeled_predictions,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.game_id}.{args.label}.json"
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True))

    print(f"\nGK windows ({len(gkw)}):")
    for r in keeper_report:
        print(f"  {r['t0']:>7.1f}s – {r['t1']:>7.1f}s  {r['name']}")
    print(f"keeper tracklets went to: {[b for _, b in keeper_assigned]}")
    print(f"\nlabels: {len(labels)}  reproduced: {n_label_hit}  missed-by-stitch: {n_missed}")
    print(f"ACCURACY on reproduced labels: {result['accuracy_on_reproduced']}  "
          f"({n_match} correct / {n_wrong} wrong)")
    print(f"status counts: {result['tracklet_status_counts']}")
    print("per-status precision (labeled): " + "  ".join(
        f"{s}={v['precision']} ({v['correct']}/{v['total']})"
        for s, v in status_precision.items()))
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    main()
