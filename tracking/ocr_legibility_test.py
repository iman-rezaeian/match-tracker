#!/usr/bin/env python3
"""Jersey-number OCR legibility probe.

Measures whether jersey numbers are big/sharp enough for OCR in a given game's
footage — so we can decide if a jersey-OCR identity pass is worth building
*before* committing to it. Re-run this on the first 8K game to compare vs the
5.7K baseline (see JERSEY_OCR_FEASIBILITY.md).

What it does:
  1. Reads the cached per-detection boxes (tracks_raw.parquet) and reports the
     player bbox-height distribution + the % of detections whose estimated jersey
     digit height clears common OCR thresholds (digit ≈ 22% of body height).
  2. Extracts a spread of upscaled player crops (small/median/large buckets) from
     the source video so you can eyeball actual legibility + orientation.
     NOTE: the largest boxes near a touchline 360 cam are often sideline adults,
     not numbered players — judge the MID/large *field* players.

Usage:
  python tracking/ocr_legibility_test.py \
      --tracks post_game/outputs/<game_id>/tracks_raw.parquet \
      --video  /path/to/source.mp4 \
      --out    /tmp/ocr_test [--n 12]
"""
from __future__ import annotations
import argparse, subprocess, shutil
from pathlib import Path
import numpy as np
import pandas as pd

DIGIT_FRAC = 0.22   # jersey digit height ≈ this fraction of full body bbox height
THRESHOLDS = (12, 16, 20, 25, 30)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default="/tmp/ocr_test")
    ap.add_argument("--n", type=int, default=12, help="total crops to extract")
    ap.add_argument("--upscale", type=int, default=5)
    args = ap.parse_args()

    ff = shutil.which("ffmpeg")
    if not ff:
        raise SystemExit("ffmpeg not on PATH")
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(args.tracks)
    df["h"] = df["y2_eq"] - df["y1_eq"]
    df = df[df["h"] > 5]
    print(f"detections: {len(df)}")
    p = np.percentile(df["h"], [10, 50, 75, 90, 95, 99])
    print("bbox height px  p10/50/75/90/95/99 =", [round(float(x)) for x in p], "max", round(float(df['h'].max())))
    dig = df["h"].to_numpy() * DIGIT_FRAC
    print(f"est. digit height (≈{DIGIT_FRAC:.0%} of body):")
    for t in THRESHOLDS:
        print(f"  ≥{t:>2}px : {100*np.mean(dig >= t):5.1f}% of detections")

    # Sample across size buckets so we see near + far players, not just the
    # biggest (which skew to sideline adults). Spread over match time too.
    df = df[(df["time_s"] > 120)]
    buckets = {
        "large": df.nlargest(max(50, args.n * 8), "h"),
        "medium": df[(df["h"] > 90) & (df["h"] < 130)],
        "small": df[(df["h"] > 55) & (df["h"] < 80)],
    }
    per = max(1, args.n // 3)
    rows = pd.concat([b.sample(min(per, len(b)), random_state=3) for b in buckets.values() if len(b)])
    print(f"\nextracting {len(rows)} crops to {out} (x{args.upscale}) …")
    for i, (_, r) in enumerate(rows.iterrows()):
        h = int(r["h"]); pad = h // 3
        cx, cy = max(0, int(r.x1_eq) - pad), max(0, int(r.y1_eq) - pad)
        cw, ch = int(r.x2_eq - r.x1_eq) + 2 * pad, h + 2 * pad
        dst = out / f"p{i:02d}_h{h}_t{r.time_s:.0f}.png"
        subprocess.run([ff, "-nostdin", "-loglevel", "error", "-ss", f"{r.time_s}", "-i", args.video,
                        "-vf", f"crop={cw}:{ch}:{cx}:{cy},scale=iw*{args.upscale}:ih*{args.upscale}:flags=lanczos",
                        "-frames:v", "1", str(dst)], check=False)
        print("  ", dst.name)
    print("\nNow open the crops and judge: can you read a jersey number on a FIELD")
    print("player (ignore sideline adults)? If yes for a decent fraction → OCR is")
    print("worth building as a tiebreaker. See JERSEY_OCR_FEASIBILITY.md.")


if __name__ == "__main__":
    main()
