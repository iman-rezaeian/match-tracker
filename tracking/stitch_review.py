#!/usr/bin/env python3
"""Render side-by-side crops at the RISKIEST spatio-temporal stitch joins so a human
can rule 'same player / not' fast — the only real precision check on this footage
(appearance embeddings are too kit-dominated to validate; see METRICS_RELEVANCE_PLAN.md).

For each cross-parent join (A's last detection | B's first detection), grab the
equirect frame at each side, crop the player's bbox, stack side by side, annotate
gap/dist/cos. Worst joins first (largest time gap). Read-only; no GPU.

Run: python -m tracking.stitch_review --game-id mqcf9axlvtuyt --n 30 --gap 20 --out /tmp/stitch_review
"""
from __future__ import annotations
import argparse, os
from collections import defaultdict
from pathlib import Path
import numpy as np, pandas as pd, cv2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--gap", type=float, default=20.0)
    ap.add_argument("--out", default="/tmp/stitch_review")
    args = ap.parse_args()
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    import sys; sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from post_game import firestore_io, config
    from post_game.calibration import FieldProjector
    from tracking.st_stitch_probe import tracklet_endpoints, greedy_st_stitch

    df = pd.read_parquet(config.OUTPUTS_DIR / args.game_id / "tracks_raw.parquet").sort_values(["track_id", "time_s"])
    cal = firestore_io.get_game_calibration(args.game_id); proj = FieldProjector(cal)
    xy = proj.pixel_to_field_batch(df[["foot_x_eq", "foot_y_eq"]].to_numpy()); df["x_m"], df["y_m"] = xy[:, 0], xy[:, 1]
    L, W = cal.length_m, cal.width_m
    df = df.loc[(df.x_m >= -3) & (df.x_m <= L + 3) & (df.y_m >= -3) & (df.y_m <= W + 3)].copy()
    # gap-split, track parent
    new = np.zeros(len(df), dtype=np.int64); nid = 0; par = {}
    for tid, idx in df.groupby("track_id").indices.items():
        t = df["time_s"].to_numpy()[idx]; brk = np.concatenate([[0], (np.diff(t) > 1.0).cumsum()]); sub = nid + brk
        for s in np.unique(sub): par[int(s)] = int(tid)
        new[idx] = sub; nid = int(sub.max()) + 1
    df["track_id"] = new
    eps = tracklet_endpoints(df)
    emb_z = np.load(config.OUTPUTS_DIR / args.game_id / "embeddings.npz", allow_pickle=True)
    emb = {int(k): emb_z[k] / (np.linalg.norm(emb_z[k]) + 1e-9) for k in emb_z.keys()}

    m = greedy_st_stitch(eps, max_gap_s=args.gap, speed_ms=config.MAX_PLAUSIBLE_SPEED_MS,
                         slack_m=config.STITCH_SLACK_M, gap_weight=config.STITCH_GAP_WEIGHT)
    mem = defaultdict(list)
    for t, r in m.items(): mem[r].append(t)
    joins = []
    for r, xs in mem.items():
        xs = sorted(xs, key=lambda f: eps[f]["t0"])
        for a, b in zip(xs, xs[1:]):
            if par[a] == par[b]:
                continue
            ea, eb = eps[a], eps[b]
            gap = eb["t0"] - ea["t1"]; dist = float(np.hypot(eb["x0"] - ea["x1"], eb["y0"] - ea["y1"]))
            c = float(np.dot(emb[par[a]], emb[par[b]])) if par[a] in emb and par[b] in emb else float("nan")
            joins.append((gap, dist, c, a, b))
    joins.sort(reverse=True)  # largest gap first = riskiest
    joins = joins[:args.n]

    # index detections for fast lookup of last(A)/first(B) bbox+frame
    g = df.groupby("track_id")
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    video = firestore_io.get_game(args.game_id).video_url.replace("file://", "")
    cap = cv2.VideoCapture(video); fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    def crop_at(frame_idx, box):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx)); ok, fr = cap.read()
        if not ok: return None
        x1, y1, x2, y2 = [int(v) for v in box]
        pad = int(0.4 * (y2 - y1))
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad); x2, y2 = x2 + pad, y2 + pad
        c = fr[y1:y2, x1:x2]
        if c.size == 0: return None
        h = 240; w = int(c.shape[1] * h / c.shape[0]); return cv2.resize(c, (max(1, w), h))

    for i, (gap, dist, c, a, b) in enumerate(joins):
        ra = g.get_group(a).iloc[-1]; rb = g.get_group(b).iloc[0]
        ca = crop_at(ra["frame"], (ra.x1_eq, ra.y1_eq, ra.x2_eq, ra.y2_eq))
        cb = crop_at(rb["frame"], (rb.x1_eq, rb.y1_eq, rb.x2_eq, rb.y2_eq))
        if ca is None or cb is None: continue
        H = 240; sep = np.full((H, 6, 3), (0, 0, 255), np.uint8)
        canvas = np.hstack([ca, sep, cb])
        banner = np.zeros((28, canvas.shape[1], 3), np.uint8)
        cv2.putText(banner, f"gap={gap:.1f}s dist={dist:.1f}m cos={c:.2f}  A->B same player?",
                    (4, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.imwrite(str(out / f"join_{i:03d}_gap{gap:.0f}s.jpg"), np.vstack([banner, canvas]))
    cap.release()
    print(f"wrote {min(len(joins), args.n)} join crops -> {out}  (left=A end, right=B start; red bar=join)")


if __name__ == "__main__":
    main()
