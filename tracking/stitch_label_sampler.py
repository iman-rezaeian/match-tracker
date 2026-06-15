#!/usr/bin/env python3
"""Phase 0 — sample tracklet pairs across stitch decision strata and render
side-by-side crops so a human can label "same player / different / can't tell".

The output CSV is the ground truth for `stitch_pr_eval.py`. Without it every
stitch change is a count illusion (count-down without a precision instrument).

Strata (50 pairs each by default = 200 total):
  - short_gap_intra : same team, gap < 3 s, geom-plausible — the easy positives
                      and the close-call decisions the current pipeline makes
                      hundreds of times per game.
  - med_gap_intra   : same team, 3-10 s gap, geom-plausible — where stitching
                      currently runs (STITCH_MAX_GAP_S=10) and most precision
                      questions live.
  - long_gap_intra  : same team, 10-30 s gap, geom-plausible — what we'd want
                      to enable if precision allowed (it currently doesn't).
  - cross_team      : different-team endpoints close in space/time — negative
                      controls. Anything labeled "same" here means our team
                      classifier is wrong, not a stitch failure.

Sampling preference (within each stratum) — borderline cases first:
  needed_speed in [3 .. 9] m/s — the zone where decisions actually matter.
  Skip trivially-easy (<2 m/s) and trivially-hard (>9 m/s) pairs.

Diversity: cap per source-track_id and per 30-second time bucket so a single
fragmented player doesn't dominate.

Run:
    python -m tracking.stitch_label_sampler \\
        --game-id mqcf9axlvtuyt --out tracking/labels/belle --n 100
    python -m tracking.stitch_label_sampler \\
        --game-id mqcjsjugchb2i --out tracking/labels/amher --n 100

Output:
    tracking/labels/<name>/pair_*.jpg          # one side-by-side crop per pair
    tracking/labels/<name>/pairs.csv           # to be filled in: label column

Then label the CSV (open in any editor / Numbers): label = 1 (same), 0 (different),
-1 (can't tell). Run `stitch_pr_eval.py` on the result.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pandas as pd

# Borderline-decision window. <2 m/s is "obviously same player walking"; >9 m/s is
# above the physical cap so it's an obvious reject. The interesting precision
# question lives in between — that's where the stitcher decides.
BORDERLINE_SPEED_MIN = 3.0
BORDERLINE_SPEED_MAX = 9.0


def _norm(vec: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(vec)
    return vec / n if n > 1e-9 else vec


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--out", required=True, help="output dir for crops + pairs.csv")
    ap.add_argument("--n", type=int, default=100, help="total pairs (split evenly across 4 strata)")
    ap.add_argument("--split-gap-s", type=float, default=1.0, help="gap-split pre-pass threshold")
    ap.add_argument("--max-pairs-per-track", type=int, default=3,
                    help="cap on how many pairs any single source track contributes (diversity)")
    ap.add_argument("--time-bucket-s", type=float, default=30.0,
                    help="cap one pair per (track, time-bucket) so a fragmented stretch doesn't dominate")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    rng = np.random.default_rng(args.seed)

    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector
    from post_game.gap_split import gap_split_tracks
    from post_game.pipeline import _our_color
    from post_game.team_classifier import classify_tracks
    from tracking.eval_stitch_assign import _load_jersey_medians

    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    if cal is None:
        raise SystemExit("No calibration on this game.")
    proj = FieldProjector(cal)
    L, W = cal.length_m, cal.width_m
    ckpt = config.OUTPUTS_DIR / args.game_id
    if not (ckpt / "tracks_raw.parquet").exists():
        raise SystemExit(f"No tracks checkpoint at {ckpt}.")
    video_path = (game.video_url or "").replace("file://", "")
    if not Path(video_path).exists():
        raise SystemExit(f"Source video missing: {video_path}. Phase 0 needs the source for crops.")

    # --- Reproduce the pipeline's stage-3 prep: project, filter to field, top-20/frame.
    df = pd.read_parquet(ckpt / "tracks_raw.parquet")
    xy = proj.pixel_to_field_batch(df[["foot_x_eq", "foot_y_eq"]].to_numpy())
    df["x_m"], df["y_m"] = xy[:, 0], xy[:, 1]
    on = ((df.x_m >= -1.5) & (df.x_m <= L + 1.5)
          & (df.y_m >= -1.5) & (df.y_m <= W + 1.5))
    df = df.loc[on].reset_index(drop=True)
    lifetime = df.groupby("track_id").size().rename("track_lifetime")
    df = df.merge(lifetime, on="track_id")
    score = df["track_lifetime"].astype(float)
    if "conf" in df.columns:
        score = score * df["conf"].astype(float).clip(lower=0.1)
    df["_rank_score"] = score
    df = (df.sort_values(["frame", "_rank_score"], ascending=[True, False])
            .groupby("frame", group_keys=False).head(20)
            .drop(columns=["_rank_score", "track_lifetime"]).reset_index(drop=True))

    # --- Gap-split (Phase 0 measures stitch decisions on CLEAN sub-tracks, the
    # same input the new stitcher will see — no point labeling teleport-zombie pairs).
    jersey = _load_jersey_medians(ckpt / "jersey_samples.npz")
    embeddings: dict[int, np.ndarray] = {}
    if (ckpt / "embeddings.npz").exists():
        with np.load(ckpt / "embeddings.npz", allow_pickle=True) as nz:
            embeddings = {int(k): np.asarray(nz[k], dtype=np.float32) for k in nz.files}
    df, jersey, embeddings, _ = gap_split_tracks(df, jersey, embeddings, split_gap_s=args.split_gap_s)
    print(f"after gap-split: {df['track_id'].nunique()} sub-tracks")

    # --- Team labels per sub-track (mirrors pipeline stage 4).
    team_of = classify_tracks(df, jersey,
                              our_home_color_hex=_our_color(game),
                              opp_color_hex=game.away_color,
                              ref_color_hex=game.ref_color)
    print(f"team breakdown: {pd.Series(list(team_of.values())).value_counts().to_dict()}")

    # --- Per-tracklet endpoints (start/end time + foot pos).
    eps: dict[int, dict] = {}
    last_box: dict[int, tuple[int, tuple[float, float, float, float]]] = {}
    first_box: dict[int, tuple[int, tuple[float, float, float, float]]] = {}
    for tid, sub in df.sort_values(["track_id", "time_s"]).groupby("track_id"):
        sub = sub.reset_index(drop=True)
        x = sub["x_m"].to_numpy(); y = sub["y_m"].to_numpy()
        t = sub["time_s"].to_numpy()
        eps[int(tid)] = dict(
            t0=float(t[0]), t1=float(t[-1]),
            x0=float(x[0]), y0=float(y[0]),
            x1=float(x[-1]), y1=float(y[-1]),
            n=int(len(sub)),
        )
        r0 = sub.iloc[0]; r1 = sub.iloc[-1]
        first_box[int(tid)] = (int(r0["frame"]), (float(r0["x1_eq"]), float(r0["y1_eq"]),
                                                  float(r0["x2_eq"]), float(r0["y2_eq"])))
        last_box[int(tid)]  = (int(r1["frame"]), (float(r1["x1_eq"]), float(r1["y1_eq"]),
                                                  float(r1["x2_eq"]), float(r1["y2_eq"])))

    # --- Candidate pairs by stratum. Walk every A and inspect Bs that start
    # shortly after A ends; bucket by gap range + team match; track diversity caps.
    n_per_stratum = max(10, args.n // 4)
    print(f"sampling target: {n_per_stratum} per stratum (4 strata, total ~{n_per_stratum*4})")

    # Index B's by start time for fast forward-scan.
    tids_by_start = sorted(eps.keys(), key=lambda i: eps[i]["t0"])

    strata: dict[str, list[dict]] = defaultdict(list)
    bucket_seen: set[tuple[str, int, int]] = set()  # (stratum, track_id, time_bucket)
    per_track_count: dict[int, int] = defaultdict(int)

    for a in tids_by_start:
        ea = eps[a]
        team_a = team_of.get(a, -1)
        if team_a not in (0, 1):           # skip ref/unknown as anchors
            continue
        # Forward scan a window of B candidates.
        for b in tids_by_start:
            eb = eps[b]
            gap = eb["t0"] - ea["t1"]
            if gap <= -0.5:
                continue
            if gap > 30.0:
                # tids_by_start is sorted by t0; once we've gone past 30s after
                # ea.t1 we can stop scanning Bs for this A.
                if eb["t0"] - ea["t1"] > 30.0:
                    break
                continue
            if a == b:
                continue
            team_b = team_of.get(b, -1)
            dist = float(np.hypot(eb["x0"] - ea["x1"], eb["y0"] - ea["y1"]))
            need_speed = dist / max(gap, 0.04)
            if need_speed > BORDERLINE_SPEED_MAX or need_speed < BORDERLINE_SPEED_MIN:
                continue

            # Stratum assignment
            if team_a == team_b == 0:
                if gap < 3.0: stratum = "short_gap_intra"
                elif gap < 10.0: stratum = "med_gap_intra"
                else: stratum = "long_gap_intra"
            elif team_a in (0, 1) and team_b in (0, 1) and team_a != team_b:
                stratum = "cross_team"
            else:
                continue  # opponent intra, or ref involvement — out of scope

            tb = int(ea["t1"] // args.time_bucket_s)
            if (stratum, a, tb) in bucket_seen:
                continue
            if per_track_count[a] >= args.max_pairs_per_track:
                continue
            if per_track_count[b] >= args.max_pairs_per_track:
                continue

            # Appearance cosine if both embeddings available (purely informational here).
            cos_app: Optional[float] = None
            if a in embeddings and b in embeddings:
                cos_app = _cos(embeddings[a], embeddings[b])

            strata[stratum].append(dict(
                a=a, b=b, gap=gap, dist=dist, need_speed=need_speed,
                team_a=team_a, team_b=team_b, cos_app=cos_app,
                t_a=ea["t1"], t_b=eb["t0"],
            ))
            bucket_seen.add((stratum, a, tb))
            per_track_count[a] += 1
            per_track_count[b] += 1

    print("candidate counts before sampling: " + ", ".join(
        f"{k}={len(v)}" for k, v in strata.items()))

    # --- Subsample per stratum, biasing toward medium need-speed (most informative).
    selected: list[dict] = []
    for stratum, cands in strata.items():
        if not cands:
            continue
        # weight = 1 - |need_speed - 6| / 6  (peak at 6 m/s, zero at endpoints)
        w = np.array([max(0.05, 1 - abs(c["need_speed"] - 6.0) / 6.0) for c in cands])
        w = w / w.sum()
        k = min(n_per_stratum, len(cands))
        idx = rng.choice(len(cands), size=k, replace=False, p=w)
        for i in idx:
            cands[i]["stratum"] = stratum
            selected.append(cands[i])

    print(f"sampled: {len(selected)} pairs total — " + ", ".join(
        f"{s}={sum(1 for x in selected if x['stratum']==s)}" for s in
        ("short_gap_intra", "med_gap_intra", "long_gap_intra", "cross_team")))

    # --- Render side-by-side crops at A.end-frame and B.start-frame.
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {video_path}")

    H = 380   # crop display height (each side)
    MIN_W = 320  # ensure each side is wide enough for the banner text to fit
    def grab(frame_idx: int, bbox_eq: tuple[float, float, float, float],
             label_char: str, label_color: tuple[int, int, int]) -> Optional[np.ndarray]:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
        ok, fr = cap.read()
        if not ok:
            return None
        x1, y1, x2, y2 = [int(v) for v in bbox_eq]
        ph = max(1, y2 - y1)
        # Pad generously so the coach has context, but use a SQUARE pad target so
        # both crops have a consistent aspect ratio. Min context = 2x player height.
        pad = max(40, int(1.2 * ph))
        cx1, cy1 = max(0, x1 - pad), max(0, y1 - pad)
        cx2, cy2 = min(fr.shape[1] - 1, x2 + pad), min(fr.shape[0] - 1, y2 + pad)
        c = fr[cy1:cy2, cx1:cx2].copy()
        if c.size == 0:
            return None
        # Mark the subject with a high-contrast box + label letter (A/B). Coords
        # transform from full-frame to the cropped image (subtract crop origin).
        bx1, by1 = x1 - cx1, y1 - cy1
        bx2, by2 = x2 - cx1, y2 - cy1
        cv2.rectangle(c, (bx1, by1), (bx2, by2), label_color, 3)
        # Label tag above the bbox
        tag_y = max(22, by1 - 8)
        cv2.rectangle(c, (bx1, tag_y - 22), (bx1 + 32, tag_y + 4), label_color, -1)
        cv2.putText(c, label_char, (bx1 + 8, tag_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
        # Resize so height==H, then pad-right to MIN_W if narrower (banner width).
        h, w = c.shape[:2]
        new_w = max(1, int(w * H / max(1, h)))
        c = cv2.resize(c, (new_w, H))
        if new_w < MIN_W:
            pad_w = MIN_W - new_w
            c = cv2.copyMakeBorder(c, 0, 0, 0, pad_w, cv2.BORDER_CONSTANT, value=(20, 20, 20))
        return c

    csv_rows: list[dict] = []
    for i, p in enumerate(sorted(selected, key=lambda x: (x["stratum"], x["a"]))):
        fa, ba = last_box[p["a"]]
        fb, bb = first_box[p["b"]]
        ca = grab(fa, ba, "A", (0, 255, 255))   # yellow box, "A" — END of tracklet A
        cb = grab(fb, bb, "B", (0, 200, 0))     # green  box, "B" — START of tracklet B
        if ca is None or cb is None:
            continue
        # Height-match the two sides (rare case: one near a frame edge).
        if ca.shape[0] != cb.shape[0]:
            target_h = min(ca.shape[0], cb.shape[0])
            ca = cv2.resize(ca, (int(ca.shape[1] * target_h / ca.shape[0]), target_h))
            cb = cv2.resize(cb, (int(cb.shape[1] * target_h / cb.shape[0]), target_h))
        sep = np.full((ca.shape[0], 8, 3), (0, 0, 220), np.uint8)  # red bar = the stitch decision
        canvas = np.hstack([ca, sep, cb])
        banner_h = 56
        banner = np.zeros((banner_h, canvas.shape[1], 3), np.uint8)
        cos_str = f"  cos={p['cos_app']:.2f}" if p["cos_app"] is not None else ""
        team_str = "SAME-TEAM" if p["team_a"] == p["team_b"] else "CROSS-TEAM"
        # Two-line banner — guaranteed to fit even on narrow crops.
        cv2.putText(banner,
                    f"[{p['stratum']}]  gap={p['gap']:.1f}s   dist={p['dist']:.1f}m   "
                    f"need={p['need_speed']:.1f} m/s",
                    (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(banner,
                    f"teams=({p['team_a']}->{p['team_b']}) {team_str}{cos_str}    "
                    f"A end @ t={p['t_a']:.0f}s   B start @ t={p['t_b']:.0f}s",
                    (8, 47), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
        img = np.vstack([banner, canvas])
        pair_id = f"{args.game_id}__{p['a']}__{p['b']}"
        fname = f"pair_{i:03d}__{p['stratum']}__{pair_id}.jpg"
        cv2.imwrite(str(out_dir / fname), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        csv_rows.append(dict(
            pair_id=pair_id, image=fname, game_id=args.game_id,
            stratum=p["stratum"], track_a=p["a"], track_b=p["b"],
            t_a_end=round(p["t_a"], 2), t_b_start=round(p["t_b"], 2),
            gap_s=round(p["gap"], 2), dist_m=round(p["dist"], 2),
            need_speed_ms=round(p["need_speed"], 2),
            team_a=p["team_a"], team_b=p["team_b"],
            cos_app=("" if p["cos_app"] is None else round(p["cos_app"], 3)),
            label="",  # human fills: 1=same, 0=different, -1=can't tell
            note="",
        ))
    cap.release()

    csv_path = out_dir / "pairs.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
        w.writeheader()
        w.writerows(csv_rows)
    print(f"\nwrote {len(csv_rows)} pair crops + {csv_path}")
    print("Fill the `label` column: 1 (same player) / 0 (different) / -1 (can't tell).")


if __name__ == "__main__":
    main()
