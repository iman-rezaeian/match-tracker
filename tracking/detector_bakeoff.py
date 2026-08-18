#!/usr/bin/env python3
"""READ-ONLY: what recovers the bodies the tracker loses — a bigger model, or
more pixels?

Stage-2 failure 2 is "the detector finds nothing where a track just died": 44%
of mid-field track deaths. It is NOT occlusion — at the moment a track dies the
nearest other body is over 4 m away 42.6% of the time and never within 1 m. The
lost player is usually alone in open space, 62-71 px tall, ~25 m out.

So the question is whether the production detector is simply under-powered, and
if so whether the cheaper fix is a larger network or a less aggressive
downscale. Both cost roughly 2x compute, and nobody had measured either, so this
replays the same death frames through every candidate — same tiles, same
projection, same dedupe, only the arm under test changes.

SAMPLE SIZE MATTERS MORE THAN THE RANKING. An earlier run of this at n=40
produced a confident-looking ordering that was entirely noise: the 95% CI for
the current model was [50%, 78%] and for the largest model [71%, 93%] — heavily
overlapping, so not even "biggest beats current" was supportable. Separating a
20-point gap needs ~73 samples per arm; a 10-point gap needs ~329. The tool now
prints a Wilson interval next to every rate and refuses to imply an ordering the
data cannot carry.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.detector_bakeoff \\
        --game-id mrhvbvwi1gjpn --n 150
"""
from __future__ import annotations

import argparse
import time
import warnings
from math import sqrt

import numpy as np
import pandas as pd

# (label, weights, classes, n_tiles, crop_w). n_tiles/crop_w None = production
# values. Overriding them is the RESOLUTION arm: a 75 deg tile spans 1600 source
# pixels of an 8K equirect but is rendered at 1280, a 1.25x downscale. Narrower
# tiles cover fewer source pixels each, so the same 1280 render is closer to
# native — at the cost of more detector calls per frame.
MODELS = [
    ("yolo11s (current)", "yolo11s.pt", None, None, None),
    # --- model arm: same tiles, bigger network
    ("yolo11m", "yolo11m.pt", None, None, None),
    ("yolo11x", "yolo11x.pt", None, None, None),
    # --- resolution arm: same network, more pixels on the player
    ("11s @1600px", "yolo11s.pt", None, None, 1600),
    ("11s 5 tiles", "yolo11s.pt", None, 5, None),
    ("11s 5t @1600", "yolo11s.pt", None, 5, 1600),
    # --- both, the expensive corner
    ("11m 5t @1600", "yolo11m.pt", None, 5, 1600),
]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% CI for a proportion — the honest width of a recovery rate."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return centre - half, centre + half


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--radius-m", type=float, default=1.5)
    ap.add_argument("--min-bbox-h", type=float, default=40.0)
    args = ap.parse_args()

    from ultralytics import RTDETR, YOLO
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector, compute_tile_aims
    from post_game.video import iter_frames, render_perspective, crop_bbox_to_equirect

    game = firestore_io.get_game(args.game_id)
    fc = firestore_io.get_game_calibration(args.game_id)
    L, W = fc.length_m, fc.width_m
    proj = FieldProjector(fc)
    eq_w, eq_h = fc.video_frame_size

    tr = pd.read_parquet(config.OUTPUTS_DIR / args.game_id / "tracks_raw.parquet")
    xy = proj.pixel_to_field_batch(tr[["foot_x_eq", "foot_y_eq"]].to_numpy())
    tr["x_m"], tr["y_m"] = xy[:, 0], xy[:, 1]
    on = tr[(tr.x_m >= -1.5) & (tr.x_m <= L + 1.5)
            & (tr.y_m >= -1.5) & (tr.y_m <= W + 1.5)].copy()
    frames = np.sort(tr.frame.unique())
    nxt = dict(zip(frames[:-1], frames[1:]))
    t_of = tr.groupby("frame")["time_s"].first()

    last = on.sort_values("time_s").groupby("track_id").tail(1).copy()
    last["h"] = last.y2_eq - last.y1_eq
    e = 2.0
    deaths = last[(last.x_m >= e) & (last.x_m <= L - e)
                  & (last.y_m >= e) & (last.y_m <= W - e)
                  & (last.h >= args.min_bbox_h)].sort_values("time_s")
    pick = deaths.iloc[:: max(1, len(deaths) // args.n)].head(args.n)

    want: dict[float, tuple[float, float]] = {}
    for _, r in pick.iterrows():
        nf = nxt.get(r.frame)
        if nf is not None and nf in t_of.index:
            want[float(t_of.loc[nf])] = (float(r.x_m), float(r.y_m))
    if not want:
        raise SystemExit("no successor frames to replay")
    targets = sorted(want)
    print(f"replaying {len(targets)} death frames of {len(deaths)} candidates\n")

    # Decode ONCE at full equirect; every arm scores the same pixels, and tiles
    # are rendered per-arm so the resolution arm is a fair comparison.
    eq_frames: list[tuple[float, np.ndarray]] = []
    for s in iter_frames(video := str(game.video_url or "").replace("file://", ""),
                         sample_rate=1,
                         windows=[(t - 0.05, t + 0.05) for t in targets],
                         render_crop=False):
        j = int(np.argmin([abs(s.time_s - t) for t in targets]))
        if abs(s.time_s - targets[j]) > 0.08:
            continue
        eq_frames.append((targets[j], s.eq_frame.copy()))
        if len(eq_frames) >= len(targets):
            break
    if not eq_frames:
        raise SystemExit("decoded NOTHING — probe bug, not a result")
    print(f"decoded {len(eq_frames)} frames\n")

    hdr = (f"{'arm':<20}{'recovered':>12}{'rate':>7}{'95% CI':>16}"
           f"{'ms/frame':>10}")
    print(hdr)
    print("-" * len(hdr))
    results = []
    for label, weights, classes, n_tiles, crop_w in MODELS:
        path = config.MODELS_DIR / weights
        try:
            loader = RTDETR if weights.startswith("rtdetr") else YOLO
            model = loader(str(path) if path.exists() else weights)
        except Exception as ex:
            print(f"{label:<20}  load failed: {str(ex)[:40]}")
            continue
        nt = n_tiles or config.DETECT_N_TILES
        cw = crop_w or config.CROP_W
        ch = int(round(cw * config.CROP_H / config.CROP_W))
        aims = compute_tile_aims(proj, L, W, nt, config.DETECT_TILE_FOV_DEG)

        found = 0
        t0 = time.time()
        for tgt, eq in eq_frames:
            tx, ty = want[tgt]
            crops = [render_perspective(eq, lon, lat, fov, cw, ch)
                     for (lon, lat, fov) in aims]
            res = model.predict(crops, classes=classes or [0],
                                conf=config.DETECT_CONFIDENCE, imgsz=cw,
                                device=config.DEVICE, verbose=False)
            pts = []
            for ci, r in enumerate(res):
                lon, lat, fov = aims[ci]
                for b in r.boxes.xyxy.cpu().numpy():
                    x1, _, x2, y2 = crop_bbox_to_equirect(
                        tuple(b), lon, lat, fov, eq_w, eq_h, cw, ch)
                    pts.append([(x1 + x2) / 2.0, y2])
            if pts:
                g = proj.pixel_to_field_batch(np.array(pts))
                if np.min(np.hypot(g[:, 0] - tx, g[:, 1] - ty)) <= args.radius_m:
                    found += 1
        ms = 1000.0 * (time.time() - t0) / len(eq_frames)
        lo, hi = wilson(found, len(eq_frames))
        results.append((label, found, len(eq_frames), lo, hi, ms))
        print(f"{label:<20}{found:>7}/{len(eq_frames):<4}{100*found/len(eq_frames):>6.0f}%"
              f"   [{100*lo:>3.0f}%,{100*hi:>4.0f}%]{ms:>10.0f}")

    if results:
        base = results[0]
        print(f"\nvs {base[0]} — is the difference real, or inside the noise?")
        for label, k, n, lo, hi, _ in results[1:]:
            overlap = lo < base[4]          # this arm's low end below baseline's high end
            verdict = ("INSIDE noise — cannot claim a difference" if overlap
                       else "SEPARATED — real at 95%")
            print(f"  {label:<20}{100*k/n:>5.0f}% vs {100*base[1]/base[2]:.0f}%   {verdict}")
    print("\nOverlapping intervals mean the ranking between those arms is not\n"
          "supported. Raise --n before acting on a gap you cannot separate.")


if __name__ == "__main__":
    main()
