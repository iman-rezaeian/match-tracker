"""Eyeball test: does gap-split turn zombie tracklets into coherent single-player
runs? Applies post_game.gap_split to the cached raw tracks, then renders 4-frame
strips for the longest CONTIGUOUS runs into montages. If a run's 4 frames are the
same kid, gap-split fixed the chimera; if still mixed, the swap is mid-run (needs
switch detection).

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.gapsplit_coherence_probe \
        --game-id mqcf9axlvtuyt \
        --video "/Users/irezaeian/Movies/stompers/Stompers-June13 Festival-Game 1.mp4"
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from post_game import config, firestore_io
from post_game.calibration import FieldProjector
from post_game.gap_split import gap_split_tracks

_BBOX = ["x1_eq", "y1_eq", "x2_eq", "y2_eq"]


def _crop(fr, bbox, h_out=300):
    x1, y1, x2, y2 = [int(v) for v in bbox]
    pad = max(40, int(1.2 * max(1, y2 - y1)))
    cx1, cy1 = max(0, x1 - pad), max(0, y1 - pad)
    cx2, cy2 = min(fr.shape[1] - 1, x2 + pad), min(fr.shape[0] - 1, y2 + pad)
    c = fr[cy1:cy2, cx1:cx2].copy()
    if c.size == 0:
        return None
    cv2.rectangle(c, (x1 - cx1, y1 - cy1), (x2 - cx1, y2 - cy1), (0, 200, 0), 3)
    h, w = c.shape[:2]
    return cv2.resize(c, (max(1, int(w * h_out / max(1, h))), h_out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--split-gap-s", type=float, default=config.SPLIT_GAP_S)
    ap.add_argument("--n", type=int, default=12, help="Runs to show.")
    ap.add_argument("--edge-buffer", type=float, default=2.5,
                    help="Drop runs whose MEDIAN position is within this many metres "
                         "of a field edge (sideline coaches/spectators). 0 = off.")
    ap.add_argument("--switch", action="store_true",
                    help="Also apply switch-detection (split mid-run teleport swaps) "
                         "after projection — to eyeball whether crossing-swaps split out.")
    ap.add_argument("--out", default="/tmp/gapsplit_coherence")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(config.OUTPUTS_DIR / args.game_id / "tracks_raw.parquet",
                         columns=["frame", "time_s", "track_id", "foot_x_eq", "foot_y_eq"] + _BBOX)

    n_raw = df["track_id"].nunique()
    split, _, _, _ = gap_split_tracks(df, split_gap_s=args.split_gap_s)
    n_split = split["track_id"].nunique()
    print(f"raw tracks: {n_raw}  →  gap-split runs: {n_split} (gap >{args.split_gap_s}s)")

    # Field gate: project foot positions to metres, keep runs whose MEDIAN
    # position sits ≥ edge-buffer inside the field. A coach lives at the line;
    # a player is only briefly there → median inset separates them.
    if args.edge_buffer > 0:
        cal = firestore_io.get_game_calibration(args.game_id)
        L, Wd = cal.length_m, cal.width_m
        xy = FieldProjector(cal).pixel_to_field_batch(
            split[["foot_x_eq", "foot_y_eq"]].to_numpy())
        split = split.assign(x_m=xy[:, 0], y_m=xy[:, 1])
        if args.switch:
            from post_game.gap_split import switch_split_tracks
            _b = split["track_id"].nunique()
            split, _, _, _ = switch_split_tracks(split)
            print(f"switch-split: {_b} → {split['track_id'].nunique()} runs "
                  f"(cut mid-run teleport swaps)")
        inset = split.groupby("track_id").apply(
            lambda s: float(np.median(np.minimum(
                np.minimum(s["x_m"], L - s["x_m"]), np.minimum(s["y_m"], Wd - s["y_m"])))),
            include_groups=False)
        keep = set(inset[inset >= args.edge_buffer].index)
        before = split["track_id"].nunique()
        split = split[split["track_id"].isin(keep)]
        print(f"field gate (median ≥{args.edge_buffer}m inside): kept "
              f"{len(keep)}/{before} runs; dropped {before - len(keep)} sideline/edge")

    # span/density per contiguous run; pick the longest by detection count.
    g = split.groupby("track_id")
    stat = pd.DataFrame({"n": g.size(), "t0": g["time_s"].min(), "t1": g["time_s"].max()})
    stat["span"] = stat["t1"] - stat["t0"]
    stat["density"] = stat["n"] / stat["span"].clip(lower=1.0)
    top = stat.sort_values("n", ascending=False).head(args.n)
    print(f"longest contiguous runs (contrast: zombie 993 was 33min span / 3.7% density):")
    for rid, r in top.iterrows():
        print(f"  run {rid}: {int(r['n'])} det · span {r['span']:.0f}s · density {r['density']:.2f}/s")

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {args.video}")
    panels = []
    for rid, r in top.iterrows():
        sub = split[split["track_id"] == rid].sort_values("time_s").assign(
            _h=lambda d: d["y2_eq"] - d["y1_eq"])
        picks = []
        for bidx in np.array_split(np.arange(len(sub)), min(4, len(sub))):
            if len(bidx):
                b = sub.iloc[bidx]
                picks.append(b.loc[b["_h"].idxmax()])
        crops = []
        for rr in picks:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(rr["frame"]))
            ok, fr = cap.read()
            if ok:
                c = _crop(fr, [rr[k] for k in _BBOX])
                if c is not None:
                    crops.append(c)
        if not crops:
            continue
        h = min(c.shape[0] for c in crops)
        strip = np.hstack([cv2.resize(c, (int(c.shape[1] * h / c.shape[0]), h)) for c in crops])
        bar = np.full((28, strip.shape[1], 3), (30, 30, 30), np.uint8)
        cv2.putText(bar, f"run {rid} | {int(r['n'])} det | span {r['span']:.0f}s | "
                    f"density {r['density']:.2f}/s", (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(np.vstack([bar, strip]))

    # stack panels into montages of 6
    w = max(p.shape[1] for p in panels)
    panels = [cv2.copyMakeBorder(p, 6, 6, 0, w - p.shape[1], cv2.BORDER_CONSTANT,
                                 value=(0, 0, 0)) for p in panels]
    for i in range(0, len(panels), 6):
        montage = np.vstack(panels[i:i + 6])
        path = out / f"gapsplit_montage_{i // 6 + 1}.jpg"
        cv2.imwrite(str(path), montage)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
