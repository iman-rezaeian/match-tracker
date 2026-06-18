"""Sample meaningful tracklets and render crop strips for UNBIASED per-player
ground-truth labeling (Tier 1 #1).

Coach overrides are CORRECTIONS (an adversarial hard-case set) — they only give
relative deltas, never absolute precision/recall. This produces an unbiased GT:
the labeler is shown each meaningful our-team tracklet's crops, BLIND to the
pipeline's guess, and records the TRUE player. One label per tracklet → ground
truth for every player in a single pass.

Selects team-0 tracklets by tracked-minutes (detection-count × median dt — the
same measure assign_identities_v2 budgets on), down to a threshold / cap that
covers ≥90% of total our-team tracked time, and PRINTS that coverage so the
labeled universe is known (measured recall is relative to it).

Reads cached artifacts only (no re-track): stage-4 maps for the team/tracklet
partition + tracks_raw.parquet for equirect boxes + the video for crops.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.player_gt_sampler \
        --game-id mqcf9axlvtuyt \
        --video "/Users/irezaeian/Movies/stompers/Stompers-June13 Festival-Game 1.mp4"
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from post_game import config

OUT_ROOT = Path(__file__).resolve().parent / "labels"
S4_DIR = Path(__file__).resolve().parent / "outputs" / "identity_eval"
_BBOX = ["x1_eq", "y1_eq", "x2_eq", "y2_eq"]


def _crop(fr: np.ndarray, bbox, h_out: int = 300) -> np.ndarray | None:
    """One padded, box-marked crop (adapted from stitch_label_sampler.grab)."""
    x1, y1, x2, y2 = [int(v) for v in bbox]
    ph = max(1, y2 - y1)
    pad = max(40, int(1.2 * ph))
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
    ap.add_argument("--video", required=True, help="SAME equirect video the run used.")
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-minutes", type=float, default=0.25,
                    help="Skip tracklets below this tracked-time (default 0.25).")
    ap.add_argument("--coverage", type=float, default=0.90,
                    help="Stop adding tracklets once this fraction of our-team "
                         "tracked time is covered (default 0.90).")
    ap.add_argument("--max-n", type=int, default=200, help="Hard cap on tracklets.")
    ap.add_argument("--strip", type=int, default=4, help="Crops per tracklet strip.")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else (OUT_ROOT / f"{args.game_id}_player_gt")
    out_dir.mkdir(parents=True, exist_ok=True)

    maps = json.loads((S4_DIR / f"{args.game_id}.stage4.json").read_text())
    team_of = {int(k): int(v) for k, v in maps["team_of_track"].items()}
    tl_of = {int(k): int(v) for k, v in maps["tracklet_of_track"].items()}

    df = pd.read_parquet(config.OUTPUTS_DIR / args.game_id / "tracks_raw.parquet",
                         columns=["frame", "time_s", "track_id"] + _BBOX)
    df["track_id"] = df["track_id"].astype(int)

    # tracked-minutes per tracklet = sum(member detection counts) × median dt / 60
    counts = df.groupby("track_id").size()
    dts = df.sort_values(["track_id", "time_s"]).groupby("track_id")["time_s"].diff().dropna()
    dt_med = float(dts[dts > 0].median()) if len(dts) else 0.1

    members: dict[int, list[int]] = {}
    for trk in (t for t, tm in team_of.items() if tm == 0):
        members.setdefault(tl_of.get(trk, trk), []).append(trk)
    tl_minutes = {tl: sum(int(counts.get(m, 0)) for m in mem) * dt_med / 60.0
                  for tl, mem in members.items()}
    total_min = sum(tl_minutes.values())

    # Rank by minutes; keep down to coverage target / cap / floor.
    ranked = sorted(tl_minutes.items(), key=lambda kv: kv[1], reverse=True)
    selected, cum = [], 0.0
    for tl, mins in ranked:
        if mins < args.min_minutes or len(selected) >= args.max_n:
            break
        selected.append(tl)
        cum += mins
        if total_min and cum / total_min >= args.coverage:
            break
    cov = (cum / total_min) if total_min else 0.0
    print(f"our-team tracklets: {len(members)} ({total_min:.1f} tracked-min total)")
    print(f"selected {len(selected)} tracklets covering {cov:.1%} of our-team "
          f"tracked time (min-minutes={args.min_minutes}, cap={args.max_n})")
    print(f"  → measured recall will be RELATIVE to this {cov:.0%} labeled universe.")

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {args.video}")

    rows = []
    for n, tl in enumerate(selected):
        sub = df[df["track_id"].isin(members[tl])].sort_values("time_s")
        if sub.empty:
            continue
        # Pick `strip` detections spread across the tracklet's span, each the
        # largest (closest/sharpest) bbox in its time-bin → best recognition.
        sub = sub.assign(_h=(sub["y2_eq"] - sub["y1_eq"])).reset_index(drop=True)
        picks = []
        for bidx in np.array_split(np.arange(len(sub)), min(args.strip, len(sub))):
            if len(bidx):
                bin_df = sub.iloc[bidx]
                picks.append(bin_df.loc[bin_df["_h"].idxmax()])
        crops = []
        for r in picks:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(r["frame"]))
            ok, fr = cap.read()
            if not ok:
                continue
            c = _crop(fr, [r[k] for k in _BBOX])
            if c is not None:
                crops.append(c)
        if not crops:
            continue
        h = min(c.shape[0] for c in crops)
        strip = np.hstack([cv2.resize(c, (int(c.shape[1] * h / c.shape[0]), h)) for c in crops])
        img = f"tl_{tl}.jpg"
        cv2.imwrite(str(out_dir / img), strip)
        rows.append({
            "tracklet_id": tl, "image": img, "game_id": args.game_id,
            "minutes": round(tl_minutes[tl], 2),
            "t_start_s": round(float(sub["time_s"].min()), 1),
            "t_end_s": round(float(sub["time_s"].max()), 1),
            "n_det": int(len(sub)),
            "true_player_id": "", "label": "", "note": "",  # blank — labeled blind
        })
        if (n + 1) % 25 == 0:
            print(f"  ...rendered {n + 1}/{len(selected)}")

    gt_csv = out_dir / "gt.csv"
    with open(gt_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {len(rows)} tracklet rows → {gt_csv}")
    print("Next: streamlit run tracking/player_gt_app.py")


if __name__ == "__main__":
    main()
