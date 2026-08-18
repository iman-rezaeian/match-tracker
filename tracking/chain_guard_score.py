#!/usr/bin/env python3
"""READ-ONLY: score candidate chain-merge guards against the blind GT labels.

The earlier guard measurements ("chain <=8 splits 42% of tracked minutes") only
counted how much a threshold CUTS. That says nothing about whether the cut lands
where the identity actually drifts — a guard that shatters a clean chain and
leaves a mixed one intact is strictly worse than no guard, and the minutes number
looks identical either way.

With 30 blind labels we can finally ask the real question. For each candidate
guard, replay the chain's joins in time order, break the chain wherever the guard
fires, and report:

  * mixed chains BROKEN UP   — how many labelled-mixed chains the guard splits
  * clean chains DAMAGED     — how many labelled-clean chains it also splits
                               (pure cost; the chain was already right)
  * minutes moved            — how much tracked time changes hands

A guard is only worth shipping if it breaks mixed chains substantially more often
than it damages clean ones. Ratio, not raw count.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.chain_guard_score --game-id mri01pvelv46d
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import pandas as pd

from post_game import config
from post_game.reid_stitch import _track_summaries
from tracking.chain_drift_probe import describe_join, joins_of_chain, rebuild


GUARDS: dict[str, callable] = {
    "gap > 4s":            lambda j: j["gap_s"] > 4.0,
    "gap > 6s":            lambda j: j["gap_s"] > 6.0,
    "dist > 5m":           lambda j: j["dist_m"] > 5.0,
    "dist > 8m":           lambda j: j["dist_m"] > 8.0,
    "speed > 3 m/s":       lambda j: j["speed_ms"] > 3.0,
    "speed > 5 m/s":       lambda j: j["speed_ms"] > 5.0,
    "cos < 0.85":          lambda j: j["cos"] < 0.85,
    "cos < 0.90":          lambda j: j["cos"] < 0.90,
    "gap>4 AND dist>5":    lambda j: j["gap_s"] > 4.0 and j["dist_m"] > 5.0,
    "gap>3 OR cos<0.85":   lambda j: j["gap_s"] > 3.0 or j["cos"] < 0.85,
}


def split_count(joins: list[dict], fires) -> int:
    """How many pieces the chain falls into once every firing join is cut."""
    return 1 + sum(1 for j in joins if fires(j))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--max-frag", type=int, nargs="*", default=[6, 8, 12],
                    help="also score a plain cap on fragments per chain")
    args = ap.parse_args()

    out_dir = config.OUTPUTS_DIR / args.game_id
    gt_path = Path("tracking/labels") / f"{args.game_id}_player_gt" / "gt.csv"
    gt = {str(r["tracklet_id"]): r for r in csv.DictReader(gt_path.open())}

    tracks, team, emb, mapping, votes = rebuild(args.game_id, out_dir)
    our = {int(t) for t, v in team.items() if v == 0}
    summ = _track_summaries(tracks, our)
    chains: dict[int, list[int]] = {}
    for t, root in mapping.items():
        if int(t) in our:
            chains.setdefault(int(root), []).append(int(t))

    per_chain = []
    for tid, g in gt.items():
        mem = chains.get(int(tid))
        if not mem:
            continue
        js = [describe_join(a, b, summ, emb, votes)
              for a, b in joins_of_chain(mem, summ)]
        per_chain.append({"tracklet": tid, "clean": bool(g["true_player_id"]),
                          "minutes": float(g["minutes"]), "joins": js,
                          "n_frag": len(mem)})

    n_clean = sum(1 for c in per_chain if c["clean"])
    n_mixed = len(per_chain) - n_clean
    print(f"scoring on {len(per_chain)} reproduced chains "
          f"({n_clean} GT-clean, {n_mixed} GT-mixed)\n")

    hdr = (f"{'guard':<22}{'mixed split':>13}{'clean damaged':>15}"
           f"{'ratio':>8}{'min moved':>11}")
    print(hdr); print("-" * len(hdr))

    def report(name, fires):
        ms = sum(1 for c in per_chain if not c["clean"]
                 and split_count(c["joins"], fires) > 1)
        cd = sum(1 for c in per_chain if c["clean"]
                 and split_count(c["joins"], fires) > 1)
        mv = sum(c["minutes"] for c in per_chain
                 if split_count(c["joins"], fires) > 1)
        ratio = (ms / n_mixed) / max(cd / max(n_clean, 1), 1e-9) if cd else float("inf")
        rs = "inf" if ratio == float("inf") else f"{ratio:.1f}"
        print(f"{name:<22}{ms:>5}/{n_mixed:<7}{cd:>7}/{n_clean:<7}{rs:>8}{mv:>10.1f}")

    for name, fires in GUARDS.items():
        report(name, fires)

    for k in args.max_frag:
        # A fragment cap is not a per-join test — it fires on the whole chain.
        ms = sum(1 for c in per_chain if not c["clean"] and c["n_frag"] > k)
        cd = sum(1 for c in per_chain if c["clean"] and c["n_frag"] > k)
        mv = sum(c["minutes"] for c in per_chain if c["n_frag"] > k)
        ratio = (ms / n_mixed) / max(cd / max(n_clean, 1), 1e-9) if cd else float("inf")
        rs = "inf" if ratio == float("inf") else f"{ratio:.1f}"
        print(f"{'frags > ' + str(k):<22}{ms:>5}/{n_mixed:<7}{cd:>7}/{n_clean:<7}"
              f"{rs:>8}{mv:>10.1f}")

    print("\nreference: doing nothing leaves all "
          f"{n_mixed} mixed chains intact and damages 0 clean ones.")
    print("A guard earns its place only if it splits most of the mixed set while\n"
          "leaving the clean set alone. Ratio is (mixed hit rate)/(clean hit rate).")


if __name__ == "__main__":
    main()
