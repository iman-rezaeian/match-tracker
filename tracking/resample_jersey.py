"""Regenerate a game's jersey_samples.npz from cached tracks WITHOUT re-tracking.

The multi-GB jersey npz is only produced inside the stage-2 detection+tracking
pass (pipeline.py:275, `sample_jersey_hsv(eq_frame, t.bbox_eq)`), so games whose
npz was cleaned up can't run eval_identity / eval_stitch_assign. But the cached
`tracks_raw.parquet` already stores the per-detection EQUIRECT bboxes
(`x1_eq..y2_eq`), and production samples jersey straight from the equirect frame
+ equirect bbox. So we can reproduce the npz FAITHFULLY by re-decoding the video
and calling the SAME `sample_jersey_hsv` on the cached boxes — no YOLO, no
perspective tiling, no re-detection. Decode-bound (tens of minutes) vs the
multi-hour full re-track.

Faithfulness rests on two things, both guaranteed here:
  1. Per-detection HSV is identical by construction — same function, same boxes,
     same (deterministically-decoded) frame pixels.
  2. Frame alignment — we reuse the SAME play_windows (half_windows) and
     SAMPLE_RATE production used, so iter_frames yields the identical absolute
     frame_index set. A coverage report at the end flags any misalignment.

WARNING: point `--video` at the SAME file the original run used. A different
file of similar length would sample wrong pixels and the coverage check would
NOT catch it (frames still "exist").

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.resample_jersey \
        --game-id mqcf9axlvtuyt \
        --video "/Users/irezaeian/Movies/stompers/Stompers-June13 Festival-Game 1.mp4"
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from post_game import config, firestore_io
from post_game.identity import half_windows
from post_game.team_classifier import sample_jersey_hsv
from post_game.video import iter_frames

_BBOX_COLS = ["x1_eq", "y1_eq", "x2_eq", "y2_eq"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--video", required=True,
                    help="Path to the SAME equirect video the original run used.")
    ap.add_argument("--out", default=None,
                    help="Output npz path (default: post_game/outputs/<game>/jersey_samples.npz)")
    ap.add_argument("--every-frame", action="store_true",
                    help="Fallback: iterate at sample_rate=1 with no windows (bulletproof "
                         "frame alignment, ~3x slower). Use only if the coverage check is low.")
    args = ap.parse_args()

    ckpt_dir = config.OUTPUTS_DIR / args.game_id
    raw_path = ckpt_dir / "tracks_raw.parquet"
    if not raw_path.exists():
        raise SystemExit(f"No cached tracks: {raw_path}")
    out_path = Path(args.out) if args.out else (ckpt_dir / "jersey_samples.npz")

    df = pd.read_parquet(raw_path, columns=["frame", "time_s", "track_id"] + _BBOX_COLS)
    n_rows = len(df)
    n_tracks = df["track_id"].nunique()
    print(f"cached: {n_rows} detections, {n_tracks} tracks "
          f"(frames {int(df['frame'].min())}..{int(df['frame'].max())})")

    # Boxes grouped by absolute source frame index (== iter_frames' frame_index).
    boxes_by_frame: dict[int, list[tuple[int, tuple]]] = defaultdict(list)
    for frame, tid, x1, y1, x2, y2 in df[["frame", "track_id"] + _BBOX_COLS].itertuples(index=False):
        boxes_by_frame[int(frame)].append((int(tid), (float(x1), float(y1), float(x2), float(y2))))
    cached_frames = set(boxes_by_frame)

    # Reproduce the production sampling cadence: same windows (so the
    # (idx - span_start) % SAMPLE_RATE phase matches) and same SAMPLE_RATE.
    if args.every_frame:
        windows, sample_rate = None, 1
        print("iterating EVERY frame (sample_rate=1, no windows) — bulletproof alignment")
    else:
        game = firestore_io.get_game(args.game_id)
        duration_s = float(df["time_s"].max()) + 1.0
        windows = half_windows(game, duration_s)
        sample_rate = config.SAMPLE_RATE
        print(f"play_windows={[(round(a, 1), round(b, 1)) for a, b in windows]}  "
              f"sample_rate={sample_rate}")

    samples: dict[int, list[np.ndarray]] = defaultdict(list)
    frames_hit = 0
    dets_sampled = 0
    dets_seen = 0
    t0 = time.time()
    for i, sample in enumerate(iter_frames(args.video, sample_rate=sample_rate,
                                           windows=windows, render_crop=False)):
        rows = boxes_by_frame.get(sample.frame_index)
        if not rows:
            continue
        frames_hit += 1
        for tid, bbox in rows:
            dets_seen += 1
            hsv = sample_jersey_hsv(sample.eq_frame, bbox)
            if len(hsv) > 0:
                samples[tid].append(hsv)
                dets_sampled += 1
        if frames_hit % 2000 == 0:
            rate = frames_hit / max(time.time() - t0, 1e-9)
            print(f"  ...{frames_hit}/{len(cached_frames)} cached frames "
                  f"({rate:.0f} hit/s)")

    # --- coverage report: did we reach (nearly) every cached detection? ---
    frame_cov = frames_hit / max(len(cached_frames), 1)
    det_cov = dets_seen / max(n_rows, 1)
    tracks_cov = len(samples) / max(n_tracks, 1)
    print(f"\ncoverage: cached-frames {frames_hit}/{len(cached_frames)} ({frame_cov:.1%})  "
          f"detections {dets_seen}/{n_rows} ({det_cov:.1%})  "
          f"tracks-with-samples {len(samples)}/{n_tracks} ({tracks_cov:.1%})")
    print(f"jersey-yield: {dets_sampled}/{dets_seen} detections gave HSV "
          f"({dets_sampled / max(dets_seen, 1):.1%}; rest too small/grass-only)")
    if frame_cov < 0.99:
        print("\n⚠ FRAME COVERAGE LOW — windows/cadence likely misaligned. "
              "Re-run with --every-frame before trusting the output.")

    # Production format (pipeline.py:340): one object array of per-detection
    # HSV arrays per str(track_id). eval_identity / pipeline load via list(nz[k]).
    np.savez(out_path,
             **{str(k): np.array(v, dtype=object) for k, v in samples.items()})
    print(f"\nwritten: {out_path}  ({out_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
