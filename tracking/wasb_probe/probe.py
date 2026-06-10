"""WASB ball-in-crop probe.

Tests the user's idea: instead of detecting the ball on the full 5.7K equirect
(where it is ~3-6 px and generic YOLO managed only 18.7%), detect it INSIDE the
virtual broadcast crop the reel already renders — where a 70 deg FOV upscaled to
1920x1080 makes the ball ~15-30 px, the regime purpose-built detectors handle.

Detector: WASB (Widely Applicable Strong Baseline, BMVC 2023), the soccer
pretrained HRNet — 3 frames in -> 3 ball heatmaps out. MIT licensed.

This is a GATE, not a product: render N crop-triplets at the existing
player-centroid aim, run WASB, and report
  - detection rate (fraction of crops with a ball above threshold)
  - score distribution
  - detected-blob pixel size (is the ball big enough in the crop?)
If the rate clears ~40%, "track in the crop" is worth building into the reel
(the reel is already wired to swap ball position into the aim). If not, we spent
an afternoon, not a month.

Run:
    GOOGLE_APPLICATION_CREDENTIALS=~/.config/stompers/firebase-adminsdk.json \
    FIRESTORE_PROJECT_ID=lasalle-stompers \
    python -m tracking.wasb_probe.probe --game-id mpyo67cl4uflh \
        --start 1046 --end 1072 --every 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

HERE = Path(__file__).resolve().parent
WASB_SRC = HERE / "WASB-SBDT" / "src"
WEIGHTS = HERE / "weights" / "wasb_soccer_best.pth.tar"
sys.path.insert(0, str(WASB_SRC))

# ImageNet normalization used in WASB training (dataloaders/__init__.py).
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
INP_W, INP_H = 512, 288
SCORE_THRESHOLD = 0.5


class DotDict(dict):
    """dict that also supports attribute access, recursively — so WASB's HRNet
    config code (which mixes cfg['x'] and cfg.MODEL.EXTRA) works without pulling
    in omegaconf (whose wheel host is blocked on the corp VPN)."""

    def __getattr__(self, k):
        try:
            v = self[k]
        except KeyError as e:
            raise AttributeError(k) from e
        return DotDict(v) if isinstance(v, dict) else v


def _to_dotdict(obj):
    if isinstance(obj, dict):
        return DotDict({k: _to_dotdict(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_dotdict(v) for v in obj]
    return obj


def load_wasb(device: str):
    """Build the WASB HRNet and load the soccer weights. Returns (model, device)."""
    import yaml
    from models import build_model  # from WASB src

    with open(WASB_SRC / "configs" / "model" / "wasb.yaml") as f:
        mcfg = yaml.safe_load(f)
    mcfg["frames_in"] = 3
    mcfg["frames_out"] = 3
    model = build_model({"model": _to_dotdict(mcfg)})
    ckpt = torch.load(WEIGHTS, map_location="cpu")
    model.load_state_dict(ckpt["model_state_dict"], strict=True)
    model.eval().to(device)
    return model


def _prep(crop_bgr: np.ndarray) -> np.ndarray:
    """BGR HxWx3 -> normalized CHW float32 at WASB input size."""
    img = cv2.resize(crop_bgr, (INP_W, INP_H), interpolation=cv2.INTER_AREA)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    img = (img - _MEAN) / _STD
    return np.transpose(img, (2, 0, 1))  # CHW


def detect_ball(model, device, triplet_bgr: list[np.ndarray]) -> dict:
    """Run WASB on a 3-frame crop triplet. Returns the MIDDLE frame's best ball.

    Returns {found, score, xy (in 512x288 heatmap space), blob_px}.
    """
    chw = [_prep(c) for c in triplet_bgr]            # 3 x (3,H,W)
    inp = np.concatenate(chw, axis=0)[None]          # (1,9,H,W)
    x = torch.from_numpy(inp).to(device)
    with torch.no_grad():
        out = model(x)
    hm = out[0] if isinstance(out, dict) else out    # (1,3,H,W) logits
    hm = torch.sigmoid(hm)[0, 1].cpu().numpy()       # middle frame heatmap
    peak = float(hm.max())
    if peak <= SCORE_THRESHOLD:
        return {"found": False, "score": peak, "xy": None, "blob_px": 0}
    # Connected-component blob (same as WASB postprocessor, concomp + hm-weight).
    _, hm_th = cv2.threshold(hm, SCORE_THRESHOLD, 1, cv2.THRESH_BINARY)
    n, labels = cv2.connectedComponents(hm_th.astype(np.uint8))
    best = None
    for m in range(1, n):
        ys, xs = np.where(labels == m)
        w = hm[ys, xs]
        s = float(w.sum())
        if best is None or s > best[0]:
            cx = float((xs * w).sum() / w.sum())
            cy = float((ys * w).sum() / w.sum())
            best = (s, cx, cy, int(xs.size))
    return {"found": True, "score": peak, "xy": (best[1], best[2]), "blob_px": best[3]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--every", type=int, default=5, help="sample every Nth video frame")
    ap.add_argument("--fov", type=float, default=18.0, help="ball-cam crop FOV (deg). Tight: WASB resizes to 512px, so 18deg -> ~28 px/deg (ball ~8-12px, its trained regime). 70deg -> 7px/deg (ball vanishes).")
    ap.add_argument("--save-crops", type=str, default=None, help="dir to dump annotated crops")
    args = ap.parse_args()

    # Repo root on path for post_game.
    repo_root = HERE.parent.parent
    sys.path.insert(0, str(repo_root))
    from post_game import firestore_io, tv_view
    from post_game.calibration import FieldProjector
    from post_game.identity import period_clock_to_video_time_factory
    from post_game.video import render_perspective

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"device={device}  loading WASB soccer weights...")
    model = load_wasb(device)

    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    proj = FieldProjector(cal)
    L, W = cal.length_m, cal.width_m
    df = tv_view.load_tracks_field_df(args.game_id, proj, L, W)
    clk = period_clock_to_video_time_factory(game)
    video_path = game.video_url.replace("file://", "")

    # Aim stream at the existing player-centroid aim (fixed FOV for the probe).
    aim_times, lons, lats, _ = tv_view._build_aim_stream(
        df, proj, args.start, args.end, L, W,
        aim_cfg=tv_view._default_aim_cfg(), events=game.events, clock_to_video=clk,
    )

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    # Render the ball-cam crop directly at WASB's input size — rendering at 1920
    # then downscaling to 512 just wastes time (WASB resizes anyway) and the
    # equirect only has 16 px/deg of real detail. At a tight FOV this upsamples
    # the equirect so the ball lands at WASB's trained pixel scale.
    OUT_W, OUT_H = INP_W, INP_H

    def aim_at(t: float) -> tuple[float, float]:
        lon = float(np.interp(t, aim_times, np.degrees(np.unwrap(np.radians(lons)))))
        lon = ((lon + 180) % 360) - 180
        lat = float(np.interp(t, aim_times, lats))
        return lon, lat

    def triplet_at(t: float) -> list[np.ndarray] | None:
        """Read 3 CONSECUTIVE frames with ONE seek (random seeks on 5.7K H.265
        are the bottleneck), each reprojected to the ball-cam crop."""
        f0 = max(0, int(round((t - 1.0 / fps) * fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
        crops = []
        for k in range(3):
            ok, frame = cap.read()
            if not ok:
                return None
            lon, lat = aim_at((f0 + k) / fps)
            crops.append(render_perspective(frame, lon, lat, args.fov, OUT_W, OUT_H, interp=cv2.INTER_LANCZOS4))
        return crops

    save_dir = Path(args.save_crops) if args.save_crops else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    dt = 1.0 / fps
    n_total = n_found = 0
    scores, blobs = [], []
    t = args.start + 0.5
    while t < args.end - 0.5:
        trip = triplet_at(t)
        if trip is None:
            t += args.every / fps
            continue
        c_cur = trip[1]
        res = detect_ball(model, device, trip)
        n_total += 1
        if res["found"]:
            n_found += 1
            scores.append(res["score"])
            blobs.append(res["blob_px"])
            if save_dir and res["xy"]:
                vis = c_cur.copy()
                bx = res["xy"][0] * OUT_W / INP_W
                by = res["xy"][1] * OUT_H / INP_H
                cv2.circle(vis, (int(bx), int(by)), 10, (0, 255, 0), 2)
                cv2.putText(vis, f"{res['score']:.2f}", (int(bx) + 12, int(by)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                cv2.imwrite(str(save_dir / f"hit_{n_total:04d}.jpg"), vis)
        t += args.every / fps
    cap.release()

    rate = 100.0 * n_found / max(1, n_total)
    print("\n==== WASB ball-in-crop probe ====")
    print(f"crops tested      : {n_total}")
    print(f"ball detected     : {n_found}  ({rate:.1f}%)")
    if scores:
        print(f"score  (found)    : median {np.median(scores):.2f}  p10 {np.percentile(scores,10):.2f}  max {np.max(scores):.2f}")
        # blob px in 512x288 heatmap space -> approx ball px in the 1920x1080 crop
        blob_px = np.array(blobs)
        ball_d_crop = np.sqrt(np.array(blobs)) * (OUT_W / INP_W)  # rough diameter in crop px
        print(f"blob size (hm px) : median {np.median(blob_px):.0f}")
        print(f"~ball diam (crop) : median {np.median(ball_d_crop):.0f} px  (5.7K sphere was ~3-6 px)")
    gate = "PASS (build it)" if rate >= 40 else ("MARGINAL" if rate >= 25 else "FAIL")
    print(f"GATE (>=40%)      : {gate}")
    if save_dir:
        print(f"annotated hits    : {save_dir}")


if __name__ == "__main__":
    main()
