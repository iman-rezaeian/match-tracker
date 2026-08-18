#!/usr/bin/env python3
"""READ-ONLY: how many stitch JOINS could a jersey-number constraint actually gate?

Blind labels say 87% of the biggest tracklets chain more than one child, and the
join analysis says no geometric feature separates a good join from a bad one
(joins inside mixed chains are SHORTER, SLOWER and CLOSER than those inside clean
ones). That kills threshold tuning and leaves one candidate signal: the jersey
number, which was 2-for-2 on the tracklets ground truth says are clean.

`stitch_tracklets` already accepts `must_link` — a per-TRACK identity label that
both bridges gaps and forbids merging two fragments carrying different labels.
Feeding it VLM reads is therefore a small change. Whether it is WORTH making
depends entirely on a number nobody has measured: what fraction of individual
FRAGMENTS can be read at all.

The distinction matters. Draft coverage was measured per TRACKLET, after
stitching, on chains tens of seconds long with hundreds of frames to choose
from. A constraint has to act on the raw fragments BEFORE they are merged — and
the median fragment is a few seconds. A number that can be read on a 40 s
tracklet may be unreadable on every 3 s piece it was built from.

So: run the production legibility prescreen (`vlm_identity.legibility_prescreen`,
the same digit-size and away-facing gate the real reader uses) over every
fragment, then ask what share of JOINS have a readable fragment on BOTH sides —
the only joins a must-link/cannot-link rule can decide.

No video needed: the prescreen is pure geometry, and geometry is exactly what
survives in the cached tracks.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.fragment_read_coverage --game-id mri01pvelv46d
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np
import pandas as pd

from post_game import config
from post_game.reid_stitch import _track_summaries
from tracking.chain_drift_probe import joins_of_chain, rebuild
from tracking.vlm_identity import _readable_rows, legibility_prescreen


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--crops", type=int, default=8,
                    help="frames the reader would pick per fragment")
    args = ap.parse_args()

    from post_game import firestore_io
    from post_game.pipeline import _camera_ground_xy

    out_dir = config.OUTPUTS_DIR / args.game_id
    tracks, team, emb, mapping, votes = rebuild(args.game_id, out_dir)
    field_cal = firestore_io.get_game_calibration(args.game_id)
    cam_xy = _camera_ground_xy(field_cal)

    our = {int(t) for t, v in team.items() if v == 0}
    summ = _track_summaries(tracks, our)
    chains: dict[int, list[int]] = {}
    for t, root in mapping.items():
        if int(t) in our:
            chains.setdefault(int(root), []).append(int(t))

    # --- per-FRAGMENT readability -------------------------------------------
    readable: dict[int, bool] = {}
    why: dict[int, str] = {}
    by_track = {int(t): g for t, g in tracks[tracks.track_id.isin(our)].groupby("track_id")}
    for t, sub in by_track.items():
        rows = _readable_rows(sub, args.crops, cam_xy=cam_xy)
        ok, reason = legibility_prescreen(rows, config.VLM_MIN_DIGIT_PX,
                                          config.VLM_MIN_AWAY)
        readable[t] = bool(ok)
        why[t] = reason or "ok"

    n_frag = len(readable)
    n_ok = sum(readable.values())
    print(f"our-team FRAGMENTS: {n_frag}")
    print(f"  pass the legibility prescreen: {n_ok} = {100*n_ok/max(n_frag,1):.0f}%")
    from collections import Counter
    c = Counter(r.split("(")[0] for r in why.values())
    for k, v in c.most_common():
        print(f"    {k:<16}{v:>6}  ({100*v/max(n_frag,1):.0f}%)")

    # fragment duration vs readability -- the thing that decides feasibility
    dur = {t: float(summ[t]["t1"] - summ[t]["t0"]) for t in readable}
    df = pd.DataFrame({"t": list(readable), "ok": [readable[t] for t in readable],
                       "dur": [dur[t] for t in readable]})
    print(f"\nfragment duration: median {df.dur.median():.1f}s  "
          f"p90 {df.dur.quantile(.9):.1f}s")
    print(f"{'duration band':<16}{'frags':>8}{'readable':>10}")
    for lo, hi in [(0, 2), (2, 5), (5, 15), (15, 60), (60, 1e9)]:
        s = df[(df.dur >= lo) & (df.dur < hi)]
        if s.empty:
            continue
        lbl = f"{lo}-{hi if hi < 1e8 else '+'}s"
        print(f"{lbl:<16}{len(s):>8}{100*s.ok.mean():>9.0f}%")

    # --- per-JOIN reachability ----------------------------------------------
    tot = both = one = neither = 0
    for root, mem in chains.items():
        for a, b in joins_of_chain(mem, summ):
            tot += 1
            k = readable.get(a, False) + readable.get(b, False)
            both += (k == 2); one += (k == 1); neither += (k == 0)
    print(f"\nSTITCH JOINS in our-team chains: {tot}")
    print(f"  readable on BOTH sides   : {both:5d} = {100*both/max(tot,1):3.0f}%"
          f"   <- a number could decide this join")
    print(f"  readable on ONE side     : {one:5d} = {100*one/max(tot,1):3.0f}%")
    print(f"  readable on NEITHER side : {neither:5d} = {100*neither/max(tot,1):3.0f}%")
    print("\nA cannot-link constraint needs a number on BOTH sides to fire; that\n"
          "share is the ceiling on how much of the over-merging it can prevent.")


if __name__ == "__main__":
    main()
