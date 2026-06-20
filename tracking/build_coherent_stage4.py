"""Build a COHERENT stage-4 cache: gap-split the raw tracks into contiguous
single-body runs, keep only on-field our-team runs, and write a drop-in cache the
GT sampler + eval consume. Fixes the root problem GT labeling exposed — the
stitched tracklets were "zombie" chimeras (one id hopping between players/bench/
opponents across the whole game), so no per-tracklet label could be right.

Cheap: works off the cached `<game>.stage4.{parquet,json}` (team labels) +
`tracks_raw.parquet` (bboxes/foot) — NO jersey stream, NO re-classify. Each
contiguous run inherits its parent track's team; opponents (team-1) and sideline
subjects (median position hugging the field edge) are dropped. The referee wears
our colour so survives here — the coach marks it during labeling.

Output `<game>.stage4.coherent.{parquet,json}`:
  parquet: track_id (run id) · frame · time_s · x1..y2_eq (render) · x_m/y_m · conf
  json:    team_of_track={run:0} · tracklet_of_track={run:run}  (each run = 1 tracklet)

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.build_coherent_stage4 --game-id mqcf9axlvtuyt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from post_game import config, firestore_io
from post_game.calibration import FieldProjector
from post_game.gap_split import gap_split_tracks

S4_DIR = Path(__file__).resolve().parent / "outputs" / "identity_eval"
_BBOX = ["x1_eq", "y1_eq", "x2_eq", "y2_eq"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--split-gap-s", type=float, default=config.SPLIT_GAP_S)
    ap.add_argument("--edge-buffer", type=float, default=2.5,
                    help="Drop runs whose median position is within this many metres "
                         "of a field edge (sideline coaches/spectators).")
    ap.add_argument("--switch", action="store_true",
                    help="Also apply switch-detection: split runs at mid-run identity "
                         "swaps (teleport jumps the team-blind tracker makes during "
                         "crossings). Recommended for the cleanest coherent cache.")
    args = ap.parse_args()

    s4_json = S4_DIR / f"{args.game_id}.stage4.json"
    if not s4_json.exists():
        raise SystemExit(f"No stage-4 cache for {args.game_id} — run eval_identity once "
                         "(needs jersey_samples.npz) to build it first.")
    team_of = {int(k): int(v) for k, v in json.loads(s4_json.read_text())["team_of_track"].items()}

    raw = pd.read_parquet(config.OUTPUTS_DIR / args.game_id / "tracks_raw.parquet",
                          columns=["frame", "time_s", "track_id", "conf",
                                   "foot_x_eq", "foot_y_eq"] + _BBOX)
    raw["track_id"] = raw["track_id"].astype(int)
    n_raw = raw["track_id"].nunique()

    # 1. gap-split into contiguous runs; sub_to_parent maps run id -> original id
    split, _, _, sub2parent = gap_split_tracks(raw, split_gap_s=args.split_gap_s)
    n_runs = split["track_id"].nunique()

    # 2. inherit team from the cached classification; keep our-team (0) runs
    run_team = {run: team_of.get(parent, -1) for run, parent in sub2parent.items()}
    our_runs = {r for r, t in run_team.items() if t == 0}
    split = split[split["track_id"].isin(our_runs)].copy()

    # 3. project foot -> field metres (for the gate + downstream assign)
    cal = firestore_io.get_game_calibration(args.game_id)
    L, Wd = cal.length_m, cal.width_m
    xy = FieldProjector(cal).pixel_to_field_batch(split[["foot_x_eq", "foot_y_eq"]].to_numpy())
    split["x_m"], split["y_m"] = xy[:, 0], xy[:, 1]

    # 3b. switch-split: cut runs at mid-run teleport swaps (needs field coords).
    # Capture pre-switch run id per detection so we can migrate existing GT labels
    # (keyed to the gap-split id) onto the dominant post-switch sub-run.
    if args.switch:
        from post_game.gap_split import switch_split_tracks
        _before_sw = split["track_id"].nunique()
        split["_gap_id"] = split["track_id"]   # carried through for label migration
        split, _, _, _ = switch_split_tracks(split)
        print(f"switch-split: {_before_sw} → {split['track_id'].nunique()} runs "
              f"(cut mid-run teleport swaps)")

    # 4. field-gate: drop runs whose MEDIAN position hugs an edge (sideline adults)
    inset = (np.minimum(np.minimum(split["x_m"], L - split["x_m"]),
                        np.minimum(split["y_m"], Wd - split["y_m"])))
    med_inset = inset.groupby(split["track_id"]).median()
    keep = set(med_inset[med_inset >= args.edge_buffer].index)
    before = split["track_id"].nunique()
    split = split[split["track_id"].isin(keep)]
    print(f"{args.game_id}: raw {n_raw} → gap-split {n_runs} runs → our-team {len(our_runs)} "
          f"→ on-field {len(keep)} (dropped {before - len(keep)} sideline/edge, gate {args.edge_buffer}m)")

    # 5. write the coherent cache (drop-in for sampler + eval)
    out_par = S4_DIR / f"{args.game_id}.stage4.coherent.parquet"
    out_json = S4_DIR / f"{args.game_id}.stage4.coherent.json"
    keep_cols = ["track_id", "frame", "time_s"] + _BBOX + ["x_m", "y_m", "conf"]
    split[keep_cols].reset_index(drop=True).to_parquet(out_par)
    runs = sorted(int(r) for r in keep)
    out_json.write_text(json.dumps({
        "team_of_track": {str(r): 0 for r in runs},        # all our-team
        "tracklet_of_track": {str(r): r for r in runs},     # each run = its own tracklet
    }))
    print(f"wrote {out_par.name} ({len(split)} det, {len(runs)} runs) + {out_json.name}")

    # 6. label-migration map (gap-split run id -> dominant surviving switch run id),
    # so GT labels made on the pre-switch cache transfer to the right sub-run.
    if args.switch and "_gap_id" in split.columns:
        mig: dict[str, int] = {}
        for gap_id, sub in split.groupby("_gap_id"):
            dom = sub["track_id"].value_counts().idxmax()  # sw run with most detections
            mig[str(int(gap_id))] = int(dom)
        mig_path = S4_DIR / f"{args.game_id}.stage4.coherent.migration.json"
        mig_path.write_text(json.dumps(mig))
        print(f"wrote {mig_path.name} ({len(mig)} gap→switch label mappings)")


if __name__ == "__main__":
    main()
