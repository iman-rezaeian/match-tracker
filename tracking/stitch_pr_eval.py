#!/usr/bin/env python3
"""Phase 0 — precision/recall of the stitch decision on the labeled pair set.

The stitcher's job, reduced to a binary call: "should A and B be in the same
chain?" Sweep the cost threshold, score against the human labels. Every
algorithm change in Phase 1+ ships only if PR-AUC improves with no precision
regression at the operating point.

Reads tracking/labels/<game>/pairs.csv (the user-filled `label` column) and the
SAME features the sampler recorded (gap_s, dist_m, need_speed_ms, cos_app). The
production stitch cost is recomputed exactly here so the gate matches what the
pipeline actually does — no proxies.

Run:
    python -m tracking.stitch_pr_eval                          # baseline (prod cost)
    python -m tracking.stitch_pr_eval --app-weight 0           # ablate appearance
    python -m tracking.stitch_pr_eval --dist-cap-m 12          # try the abs cap

Reports:
  - PR-AUC overall
  - per-stratum precision / recall at the OPERATING POINT (= current production
    accept threshold, derived from the cost function)
  - false-positive / false-negative counts so we can re-open those crops
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np


HERE = Path(__file__).resolve().parent
LABELS_ROOT = HERE / "labels"


def _stitch_cost_prod(gap: float, dist: float, cos_app: Optional[float],
                      team_a: int, team_b: int,
                      *, app_weight: float, gap_weight: float,
                      max_gap_s: float, dist_cap_m: float,
                      slack_m: float, speed_cap_ms: float,
                      enforce_team_gate: bool = True) -> float:
    """Reproduce reid_stitch.stitch_tracklets' cost function decision.

    Returns +inf if the pair would be REJECTED by a hard gate (the prod
    stitcher's `continue` paths); else the cost. Lower cost = more confident
    merge. The PR sweep then accepts/rejects by thresholding this cost.

    Team gate: production's stitch loop is scoped to target_team only — a
    cross-team pair is never even considered. Modeled here as +inf when
    team_a != team_b (both must be valid team labels 0/1).
    """
    if enforce_team_gate and team_a in (0, 1) and team_b in (0, 1) and team_a != team_b:
        return float("inf")
    if gap < -0.5:
        return float("inf")
    if gap > max_gap_s:
        return float("inf")
    max_move = min(speed_cap_ms * max(gap, 0.0) + slack_m, dist_cap_m)
    if dist > max_move:
        return float("inf")
    # Appearance: prod gate is cosine >= STITCH_APPEARANCE_COS (0.55) when
    # embeddings exist. When the embedding is missing, prod falls back to HSV /
    # geometry-only; we don't have HSV in the labeled CSV, so treat absent
    # cosine as a no-op (don't reject, don't subsidize).
    if cos_app is not None and cos_app < 0.55:
        # Note: this gate is empirically nearly-always-pass at our OSNet
        # cross-track distribution (median 0.62-0.75). Keep it for fidelity
        # so the baseline matches prod, but expect very few rejects here.
        return float("inf")
    app_term = app_weight * (1.0 - (cos_app if cos_app is not None else 0.0))
    return float(dist + gap_weight * gap + app_term)


def _pr_curve(scores: np.ndarray, labels: np.ndarray) -> dict:
    """labels in {0,1}; lower score = more likely positive (merge).

    Sweep all unique cost thresholds (accept if cost <= thr). Return per-thr
    precision/recall and the area under the resulting PR curve (trapezoid).
    """
    # Sort by cost ascending — accept top-k as merges.
    order = np.argsort(scores, kind="stable")
    y = labels[order]
    pos_total = int(y.sum())
    if pos_total == 0:
        return {"auc": 0.0, "precision": [], "recall": [], "thr": []}
    tp = np.cumsum(y == 1)
    fp = np.cumsum(y == 0)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / pos_total
    # Prepend (recall=0, precision=1) to anchor the curve.
    recall_full = np.concatenate([[0.0], recall])
    precision_full = np.concatenate([[1.0], precision])
    # Monotonize precision (standard PR-AUC convention: max-to-the-right).
    for i in range(len(precision_full) - 2, -1, -1):
        precision_full[i] = max(precision_full[i], precision_full[i + 1])
    auc = float(np.trapz(precision_full, recall_full))
    return {
        "auc": auc,
        "precision": precision_full.tolist(),
        "recall": recall_full.tolist(),
        "thr": scores[order].tolist(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels-glob", default="*/pairs.csv",
                    help="glob under tracking/labels/ for label CSVs (default: all)")
    ap.add_argument("--app-weight", type=float, default=5.0,
                    help="STITCH_APP_WEIGHT (prod default 5.0; try 0 to ablate)")
    ap.add_argument("--gap-weight", type=float, default=0.5)
    ap.add_argument("--max-gap-s", type=float, default=10.0,
                    help="STITCH_MAX_GAP_S (prod default 10.0)")
    ap.add_argument("--dist-cap-m", type=float, default=float("inf"),
                    help="STITCH_DIST_CAP_M (prod default inf — set 12 to test the abs cap)")
    ap.add_argument("--slack-m", type=float, default=3.0)
    ap.add_argument("--speed-cap-ms", type=float, default=9.0)
    ap.add_argument("--include-uncertain", action="store_true",
                    help="include label=-1 pairs (treats them as negatives — conservative)")
    ap.add_argument("--no-team-gate", action="store_true",
                    help="disable the team-gate (cross-team pairs no longer auto-rejected). "
                         "Use to evaluate the cost function in isolation from team classification.")
    args = ap.parse_args()
    sys.path.insert(0, str(HERE.parent))

    # --- Load labels.
    rows: list[dict] = []
    n_junk = 0
    for csv_path in sorted(LABELS_ROOT.glob(args.labels_glob)):
        with csv_path.open() as f:
            for r in csv.DictReader(f):
                lab = (r.get("label") or "").strip()
                if not lab:
                    continue
                # "junk" = a box is on a non-player (coach/spectator). Not a valid
                # stitch decision — excluded from PR, but counted: it measures how
                # much non-player noise survives into the candidate pool.
                if lab == "junk":
                    n_junk += 1
                    continue
                try:
                    li = int(lab)
                except ValueError:
                    continue
                if li not in (0, 1, -1):
                    continue
                if li == -1 and not args.include_uncertain:
                    continue
                r["_y"] = 1 if li == 1 else 0
                r["_src"] = csv_path.parent.name
                rows.append(r)
    if n_junk:
        print(f"  (excluded {n_junk} 'not a player' pairs — non-player noise in the candidate pool)")
    if not rows:
        raise SystemExit(
            f"No labeled rows found under {LABELS_ROOT}/{args.labels_glob}. "
            "Fill the `label` column in pairs.csv first.")

    # --- Recompute the stitch cost for each labeled pair under THIS config.
    costs = np.full(len(rows), np.inf)
    for i, r in enumerate(rows):
        gap = float(r["gap_s"])
        dist = float(r["dist_m"])
        cos_app = float(r["cos_app"]) if r.get("cos_app") not in ("", None) else None
        team_a = int(r.get("team_a", -1) or -1)
        team_b = int(r.get("team_b", -1) or -1)
        costs[i] = _stitch_cost_prod(
            gap, dist, cos_app, team_a, team_b,
            app_weight=args.app_weight, gap_weight=args.gap_weight,
            max_gap_s=args.max_gap_s, dist_cap_m=args.dist_cap_m,
            slack_m=args.slack_m, speed_cap_ms=args.speed_cap_ms,
            enforce_team_gate=not args.no_team_gate,
        )
    labels = np.array([r["_y"] for r in rows], dtype=int)

    # --- Overall PR-AUC (only over pairs the gates didn't reject — those are
    # auto-rejects, which are the right call when the label is 0 but a FN when
    # the label is 1. We score them as cost=+inf so they sit at the recall-0
    # end of the sweep, which is correct.)
    pr = _pr_curve(costs, labels)
    print(f"\n==== Stitch decision PR ({len(rows)} labeled pairs) ====")
    print(f"  config: app_weight={args.app_weight} gap_weight={args.gap_weight} "
          f"max_gap={args.max_gap_s}s dist_cap={args.dist_cap_m}m slack={args.slack_m}m "
          f"speed_cap={args.speed_cap_ms}m/s")
    print(f"  positives (same-player): {int(labels.sum())}  "
          f"negatives: {int((labels==0).sum())}")
    print(f"  PR-AUC: {pr['auc']:.3f}")

    # --- Operating-point precision/recall = "what prod would actually accept".
    # Prod accepts ALL pairs that pass the gates (cost is finite). Recall at
    # this operating point is the fraction of true positives that pass, and
    # precision is TP/(TP+FP) among the accepted set.
    accepted = np.isfinite(costs)
    if accepted.any():
        tp = int(((labels == 1) & accepted).sum())
        fp = int(((labels == 0) & accepted).sum())
        fn = int(((labels == 1) & ~accepted).sum())
        tn = int(((labels == 0) & ~accepted).sum())
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        print(f"\n  Operating point (prod gate accept = cost < inf):")
        print(f"    accepted: {int(accepted.sum())} / {len(rows)}")
        print(f"    TP={tp}  FP={fp}  FN={fn}  TN={tn}")
        print(f"    precision={prec:.3f}  recall={rec:.3f}  F1={f1:.3f}")
    else:
        print("  Operating point: nothing accepted (gates are too tight on this set).")

    # --- Per-stratum breakdown — where in decision space the stitcher fails.
    print("\n  Per-stratum @ operating point:")
    print(f"    {'stratum':<18} {'n':>4} {'pos':>4} {'acc':>4} {'TP':>3} {'FP':>3} {'FN':>3} {'prec':>5} {'rec':>5}")
    by = defaultdict(list)
    for i, r in enumerate(rows):
        by[r["stratum"]].append(i)
    for stratum, idxs in sorted(by.items()):
        ys = labels[idxs]; acs = np.isfinite(costs[idxs])
        n = len(idxs); pos = int(ys.sum())
        acc = int(acs.sum())
        tp = int(((ys == 1) & acs).sum())
        fp = int(((ys == 0) & acs).sum())
        fn = int(((ys == 1) & ~acs).sum())
        prec = tp / max(tp + fp, 1) if (tp + fp) else 0.0
        rec = tp / max(tp + fn, 1) if pos else 0.0
        print(f"    {stratum:<18} {n:>4} {pos:>4} {acc:>4} {tp:>3} {fp:>3} {fn:>3} {prec:>5.2f} {rec:>5.2f}")

    # --- Per-source (per-game) PR-AUC.
    by_src = defaultdict(list)
    for i, r in enumerate(rows):
        by_src[r["_src"]].append(i)
    if len(by_src) > 1:
        print("\n  Per-source PR-AUC:")
        for src, idxs in sorted(by_src.items()):
            sub = _pr_curve(costs[idxs], labels[idxs])
            print(f"    {src:<10} n={len(idxs):>4} pos={int(labels[idxs].sum()):>3}  AUC={sub['auc']:.3f}")

    # --- Surface FP and FN pair_ids so the user can re-open the crops.
    fps = [rows[i] for i in range(len(rows)) if (labels[i] == 0 and np.isfinite(costs[i]))]
    fns = [rows[i] for i in range(len(rows)) if (labels[i] == 1 and not np.isfinite(costs[i]))]
    if fps:
        print(f"\n  False positives (accepted but labeled different) — top 10:")
        for r in fps[:10]:
            print(f"    {r['stratum']:<18} gap={r['gap_s']}s dist={r['dist_m']}m  {r['_src']}/{r['image']}")
    if fns:
        print(f"\n  False negatives (rejected but labeled same) — top 10:")
        for r in fns[:10]:
            print(f"    {r['stratum']:<18} gap={r['gap_s']}s dist={r['dist_m']}m  {r['_src']}/{r['image']}")
    print()


if __name__ == "__main__":
    main()
