#!/usr/bin/env python3
"""Jersey-OCR YIELD probe — per PLAYER, on their best crops.

The earlier ocr_legibility_test.py sampled RANDOM detections. This one answers the
real question the coach asked: for each *assigned player*, how many readable
jersey-number crops do we actually get on their BEST frames (largest, sharpest,
person-shaped)? That's the ceiling for a best-frame jersey-OCR tiebreaker.

It needs no OCR model (which the corp VPN blocks) — it reports the digit-size +
sharpness distribution on the best crops and dumps an upscaled jersey-region
gallery per player so a human (or a later OCR pass on an off-VPN box) can judge.

Usage:
  python tracking/jersey_ocr_probe.py --game mpyo67cl4uflh \
      --video /Users/irezaeian/Movies/stompers/stompers-20260603.mp4 \
      --out /tmp/jersey_probe [--per-player 6]
"""
from __future__ import annotations
import argparse, subprocess, shutil
from pathlib import Path
import numpy as np
import pandas as pd

DIGIT_FRAC = 0.22       # jersey digit height ≈ this fraction of body bbox height
READABLE_DIGIT_PX = 20  # rough floor where OCR starts having a chance


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default="/tmp/jersey_probe")
    ap.add_argument("--per-player", type=int, default=6)
    ap.add_argument("--upscale", type=int, default=5)
    args = ap.parse_args()

    ff = shutil.which("ffmpeg")
    if not ff:
        raise SystemExit("ffmpeg not on PATH")
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    from post_game import firestore_io
    db = firestore_io._client()
    doc = (db.document("teams/main").collection("games").document(args.game)
           .collection("analytics").document("v1").get().to_dict()) or {}
    id_by_track = {a["track_id"]: a["player_id"] for a in doc.get("identity_assignments") or [] if a.get("player_id")}
    ros = {r.id: r for r in firestore_io.get_roster()}

    tracks = Path("post_game/outputs") / args.game / "tracks_raw.parquet"
    df = pd.read_parquet(tracks)
    df["h"] = df["y2_eq"] - df["y1_eq"]
    df["w"] = (df["x2_eq"] - df["x1_eq"]).clip(lower=1)
    df["aspect"] = df["h"] / df["w"]
    df["pid"] = df["track_id"].map(id_by_track)
    df = df[df["pid"].notna()]
    # person-shaped, reasonably big, confident
    df = df[(df["aspect"] > 1.4) & (df["aspect"] < 4.5) & (df["h"] > 40) & (df["conf"] > 0.5)]

    print(f"{'player':<16}{'#':>3}  {'bestH':>6}{'digit~':>7}{'>=20px crops':>14}")
    rows_for_extract = []
    for pid, sub in df.groupby("pid"):
        p = ros.get(pid)
        num = f"#{p.jersey_number}" if p and p.jersey_number is not None else "?"
        name = (p.name.split()[0] if p else pid)
        top = sub.nlargest(args.per_player, "h")
        best_h = float(top["h"].max())
        best_digit = best_h * DIGIT_FRAC
        n_readable = int((sub["h"] * DIGIT_FRAC >= READABLE_DIGIT_PX).sum())
        frac_readable = 100 * n_readable / max(len(sub), 1)
        print(f"{name:<16}{num:>3}  {best_h:>6.0f}{best_digit:>7.0f}{frac_readable:>12.0f}%")
        for i, (_, r) in enumerate(top.iterrows()):
            rows_for_extract.append((name, num.replace('#',''), i, r))

    print(f"\nextracting {len(rows_for_extract)} jersey crops → {out} (x{args.upscale}) …")
    for name, num, i, r in rows_for_extract:
        h = float(r["h"])
        # jersey-number region: upper-back ~ top 15%..55% of the body, full width
        cy = int(r["y1_eq"] + 0.12 * h); ch = int(0.45 * h)
        pad = int(0.15 * (r["x2_eq"] - r["x1_eq"]))
        cx = max(0, int(r["x1_eq"]) - pad); cw = int(r["x2_eq"] - r["x1_eq"]) + 2 * pad
        dst = out / f"{name}_{num}_{i}_h{int(h)}_t{r.time_s:.0f}.png"
        subprocess.run([ff, "-nostdin", "-loglevel", "error", "-ss", f"{float(r['time_s'])}",
                        "-i", args.video,
                        "-vf", f"crop={cw}:{ch}:{cx}:{max(0,cy)},scale=iw*{args.upscale}:ih*{args.upscale}:flags=lanczos",
                        "-frames:v", "1", str(dst)], check=False, timeout=60)
    print("\nOpen the crops: for each PLAYER, can you read the number on their best")
    print("frames? Count players with >=1 clearly-readable crop → that's the OCR yield")
    print("ceiling. Run the actual OCR (tracking/jersey_ocr_run.py) on an off-VPN box.")


if __name__ == "__main__":
    main()
