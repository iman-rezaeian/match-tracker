#!/usr/bin/env python3
"""Score coach labels into the answer: does a follow survive a full-half stint?

This is the tool every earlier measurement should have been. Previous survival
numbers were scored against the tracker's own ~90-second clean segments, so
they measured how long the ANSWER KEY lasted, not how long the follower did.
Labels from `stint_label_app` have no such horizon: a checkpoint at minute 20
is just as valid as one at minute 1.

What it reports, and why each one
---------------------------------
  * **survival curve** — fraction of follows still on the right child at each
    elapsed minute. Directly answers the 25-minute question, since the coach
    can and should keep the team on for a whole half in an important game.
  * **hazard per minute band** — whether the risk of losing a player is flat
    (design: periodic re-confirmation) or front-loaded (design: get the seed
    right and it holds). An earlier attempt at this was confounded by the
    answer key expiring; labels remove that confound.
  * **seed accuracy** — how often the automatic seed was on the right child at
    t=0. Measured separately because a wrong seed is not a tracking failure and
    must not be counted as one: the stand-in heuristic put the KEEPER's stint
    on a body at midfield, and every downstream number from that follow was
    meaningless.
  * **certain vs judged** — labels where the coach could read a name or number
    are definitive; continuity judgements are not. Reported apart so a
    confident conclusion is never resting on the softer half.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.stint_label_score --game-id mrhvbvwi1gjpn
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np

LABELS_ROOT = Path(__file__).resolve().parent / "labels"
SAME, WRONG, UNSURE = "same", "wrong", "unsure"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - m), min(1.0, c + m))


def load(game_id: str) -> list[dict]:
    p = LABELS_ROOT / f"{game_id}_stint_labels" / "labels.csv"
    if not p.exists():
        raise SystemExit(f"no labels at {p}. Label some clips first:\n"
                         f"  streamlit run tracking/stint_label_app.py")
    with open(p) as f:
        rows = [dict(r) for r in csv.DictReader(f)]
    for r in rows:
        r["elapsed_in_follow_s"] = float(r["elapsed_in_follow_s"])
        r["t_checkpoint_s"] = float(r["t_checkpoint_s"])
        r["certain"] = str(r.get("certain", "")) in ("1", "True", "true")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--certain-only", action="store_true",
                    help="use only labels where a name/number was readable")
    args = ap.parse_args()

    rows = load(args.game_id)
    if args.certain_only:
        rows = [r for r in rows if r["certain"]]
        if not rows:
            raise SystemExit("no 'certain' labels yet.")

    by_stint: dict[str, list[dict]] = {}
    for r in rows:
        by_stint.setdefault(r["stint_key"], []).append(r)
    for v in by_stint.values():
        v.sort(key=lambda r: r["elapsed_in_follow_s"])

    n_unsure = sum(1 for r in rows if r["verdict"] == UNSURE)
    print(f"game {args.game_id}   {len(rows)} labels over {len(by_stint)} stints"
          f"   ({sum(1 for r in rows if r['certain'])} certain, "
          f"{n_unsure} can't-tell)")

    # --- seed accuracy, kept separate from following quality ----------------
    seeds = [v[0] for v in by_stint.values() if v[0]["elapsed_in_follow_s"] < 1.0]
    if seeds:
        ok = sum(1 for s in seeds if s["verdict"] == SAME)
        lo, hi = wilson(ok, len(seeds))
        print(f"\nSEED accuracy (automatic stand-in): {ok}/{len(seeds)} "
              f"= {100*ok/max(len(seeds),1):.0f}%  CI [{100*lo:.0f}, {100*hi:.0f}]")
        print("  A wrong seed is NOT a tracking failure — those stints are")
        print("  excluded from the survival curve below.")

    # --- survival, over stints whose seed was right -------------------------
    good = {k: v for k, v in by_stint.items()
            if not (v[0]["elapsed_in_follow_s"] < 1.0 and v[0]["verdict"] == WRONG)}
    print(f"\nSURVIVAL over {len(good)} correctly-seeded stints")
    first_wrong = {}
    for k, v in good.items():
        w = [r for r in v if r["verdict"] == WRONG]
        first_wrong[k] = w[0]["elapsed_in_follow_s"] if w else None

    horizon = max((r["elapsed_in_follow_s"] for r in rows), default=0)
    print(f"{'minute':>8}{'at risk':>9}{'still right':>13}{'survival':>10}")
    for m in range(0, int(horizon / 60) + 1, 2):
        t = m * 60
        at_risk = [k for k, v in good.items()
                   if max(r["elapsed_in_follow_s"] for r in v) >= t]
        if not at_risk:
            continue
        alive = [k for k in at_risk
                 if first_wrong[k] is None or first_wrong[k] > t]
        s = len(alive) / len(at_risk)
        print(f"{m:>8}{len(at_risk):>9}{len(alive):>13}{100*s:>9.0f}%")

    swapped = [v for v in first_wrong.values() if v is not None]
    if swapped:
        print(f"\ntime-to-swap: median {np.median(swapped)/60:.1f} min "
              f"(n={len(swapped)} of {len(good)})")
    else:
        print(f"\nNo swap observed in {len(good)} stints "
              f"(all right-censored at {horizon/60:.0f} min).")

    # --- hazard, the design-deciding question -------------------------------
    print(f"\n{'band':>10}{'at risk':>9}{'swaps':>7}{'hazard/min':>12}")
    for m0 in range(0, int(horizon / 60) + 1, 5):
        t0, t1 = m0 * 60, (m0 + 5) * 60
        at_risk = sum(1 for k, v in good.items()
                      if max(r["elapsed_in_follow_s"] for r in v) >= t0
                      and (first_wrong[k] is None or first_wrong[k] >= t0))
        sw = sum(1 for w in first_wrong.values() if w is not None and t0 <= w < t1)
        if at_risk:
            print(f"{m0:>4}-{m0+5:<5}{at_risk:>9}{sw:>7}{sw/at_risk/5:>12.3f}")
    print("\nFLAT hazard  => loss is constant risk; design for periodic re-confirm.")
    print("FRONT-LOADED => get the seed right and it holds; design for good seeds.")

    if n_unsure:
        print(f"\n{n_unsure} can't-tell labels are excluded from every number "
              f"above.\nIf that fraction is large the clips need to be longer "
              f"or tighter, not the\nfollower changed.")


if __name__ == "__main__":
    main()
