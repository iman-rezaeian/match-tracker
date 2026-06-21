"""Precision check for switch-detection: render the BEFORE|AFTER frames at each
teleport cut, so a human can confirm each cut is a real body-swap (two different
people) and not a chopped good run.

Reproduces switch_split's teleport rule (speed > SWITCH_MAX_SPEED_MS AND jump >
SWITCH_MIN_JUMP_M, in field metres) on the gap-split team-0 runs, then for a
spread of cuts (by jump size) renders the detection just before the jump (red)
beside the one just after (green). Different people on the two sides = a correct
cut; same person = a false cut (over-split).

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.switch_cut_probe \
        --game-id mqcf9axlvtuyt \
        --video "/Users/irezaeian/Movies/stompers/Stompers-June13 Festival-Game 1.mp4"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from post_game import config, firestore_io
from post_game.calibration import FieldProjector
from post_game.gap_split import gap_split_tracks

S4_DIR = Path(__file__).resolve().parent / "outputs" / "identity_eval"
_BBOX = ["x1_eq", "y1_eq", "x2_eq", "y2_eq"]


def _crop(fr, bbox, color, h_out=320):
    x1, y1, x2, y2 = [int(v) for v in bbox]
    pad = max(40, int(1.2 * max(1, y2 - y1)))
    cx1, cy1 = max(0, x1 - pad), max(0, y1 - pad)
    cx2, cy2 = min(fr.shape[1] - 1, x2 + pad), min(fr.shape[0] - 1, y2 + pad)
    c = fr[cy1:cy2, cx1:cx2].copy()
    if c.size == 0:
        return None
    cv2.rectangle(c, (x1 - cx1, y1 - cy1), (x2 - cx1, y2 - cy1), color, 3)
    h, w = c.shape[:2]
    return cv2.resize(c, (max(1, int(w * h_out / max(1, h))), h_out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--n", type=int, default=12, help="Cuts to show (spread by jump size).")
    ap.add_argument("--out", default="/tmp/switch_cuts")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    team_of = {int(k): int(v) for k, v in
               json.loads((S4_DIR / f"{args.game_id}.stage4.json").read_text())["team_of_track"].items()}
    raw = pd.read_parquet(config.OUTPUTS_DIR / args.game_id / "tracks_raw.parquet",
                          columns=["frame", "time_s", "track_id", "foot_x_eq", "foot_y_eq"] + _BBOX)
    raw["track_id"] = raw["track_id"].astype(int)
    split, _, _, sub2par = gap_split_tracks(raw, split_gap_s=config.SPLIT_GAP_S)
    our = {r for r, p in sub2par.items() if team_of.get(p, -1) == 0}
    split = split[split["track_id"].isin(our)].copy()
    cal = firestore_io.get_game_calibration(args.game_id)
    xy = FieldProjector(cal).pixel_to_field_batch(split[["foot_x_eq", "foot_y_eq"]].to_numpy())
    split["x_m"], split["y_m"] = xy[:, 0], xy[:, 1]

    # find teleport cuts (mirror switch_split_tracks)
    cuts = []  # (jump_m, speed, row_before, row_after)
    for _rid, sub in split.sort_values(["track_id", "time_s"]).groupby("track_id"):
        if len(sub) < 2:
            continue
        a = sub.iloc[:-1].reset_index(drop=True)
        b = sub.iloc[1:].reset_index(drop=True)
        dist = np.hypot(b["x_m"].to_numpy() - a["x_m"].to_numpy(),
                        b["y_m"].to_numpy() - a["y_m"].to_numpy())
        dt = (b["time_s"].to_numpy() - a["time_s"].to_numpy())
        with np.errstate(divide="ignore", invalid="ignore"):
            spd = np.where(dt > 1e-6, dist / dt, np.inf)
        hit = np.where((spd > config.SWITCH_MAX_SPEED_MS) & (dist > config.SWITCH_MIN_JUMP_M))[0]
        for i in hit:
            cuts.append((float(dist[i]), float(spd[i]), a.iloc[i], b.iloc[i]))
    print(f"teleport cuts on team-0 gap-split runs: {len(cuts)}")
    if not cuts:
        return
    cuts.sort(key=lambda c: c[0], reverse=True)
    # spread across the jump-size distribution (biggest → near-threshold)
    pick = [cuts[i] for i in np.linspace(0, len(cuts) - 1, min(args.n, len(cuts))).astype(int)]

    cap = cv2.VideoCapture(args.video)
    panels = []
    for jump, spd, ra, rb in pick:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(ra["frame"])); ok1, f1 = cap.read()
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(rb["frame"])); ok2, f2 = cap.read()
        if not (ok1 and ok2):
            continue
        ca = _crop(f1, [ra[k] for k in _BBOX], (0, 0, 220))   # red = before jump
        cb = _crop(f2, [rb[k] for k in _BBOX], (0, 200, 0))   # green = after jump
        if ca is None or cb is None:
            continue
        h = min(ca.shape[0], cb.shape[0])
        ca = cv2.resize(ca, (int(ca.shape[1] * h / ca.shape[0]), h))
        cb = cv2.resize(cb, (int(cb.shape[1] * h / cb.shape[0]), h))
        sep = np.full((h, 10, 3), (40, 40, 40), np.uint8)
        strip = np.hstack([ca, sep, cb])
        bar = np.full((28, strip.shape[1], 3), (30, 30, 30), np.uint8)
        cv2.putText(bar, f"jump {jump:.1f} m @ {spd:.0f} m/s   (red=before  green=after)",
                    (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(np.vstack([bar, strip]))
    w = max(p.shape[1] for p in panels)
    panels = [cv2.copyMakeBorder(p, 6, 6, 0, w - p.shape[1], cv2.BORDER_CONSTANT, value=(0, 0, 0))
              for p in panels]
    for i in range(0, len(panels), 6):
        path = out / f"switch_cuts_{i // 6 + 1}.jpg"
        cv2.imwrite(str(path), np.vstack(panels[i:i + 6]))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
