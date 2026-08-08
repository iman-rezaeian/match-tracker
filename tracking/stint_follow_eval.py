#!/usr/bin/env python3
"""READ-ONLY: run the stint follower over a game's REAL coach-log stints.

Why this exists separately from `stint_follow_probe`
----------------------------------------------------
The probe measures whether following survives, using the tracker's own clean
segments as stand-ins for a player. Useful for a go/no-go, but those segments
are an artefact of the tracker: they start and end wherever fragmentation
happened to allow, and their median length (48 s) is nothing like a real stint
(8 minutes on both Jul-12 games).

This tool runs the same follower over the stints the coach actually made —
reconstructed from `starting_lineup` + timed SUB events by the existing
`identity._onfield_intervals`. That exposes three things the probe cannot:

  * **Real stint lengths.** 35 stints per game at a median of 8 minutes, not 48
    seconds. Following for 8 minutes is the job; 48 s was never the test.
  * **The drift check.** A stint still attached past its logged sub-out is
    wrong by arithmetic — 35 free correctness checks per game, no labels.
  * **Realistic concurrency.** At most 7 outfield targets plus a keeper, versus
    the probe's 53 overlapping pseudo-truth segments.

What it does NOT do
-------------------
Seed correctly. There is no per-frame ground truth for "which body is
Perrotta at kickoff", so seeds here are placed by a stated heuristic (see
`--seed-mode`) and the identity of each seed is UNVERIFIED. Coverage, gap rate,
and drift are therefore meaningful; per-player identity accuracy is NOT, and
this tool does not print any. Real seeding is Phase 2 (the coach's UI), and
that is also what will produce the first true labels.

Never writes: no Firestore, no checkpoints. Reads cached Stage-2 parquets.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.stint_follow_eval \\
        --game-id mrhvbvwi1gjpn --tag Q_base
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np


def build_stints(game_id: str):
    """Coach log -> [(player_id, t_start_video_s, t_end_video_s)].

    Reuses `identity._onfield_intervals`, which is already the stint model:
    starting lineup on at kickoff, SUB events opening and closing appearances.
    """
    from post_game import firestore_io
    from post_game.identity import (
        _onfield_intervals, period_clock_to_video_time_factory,
    )

    game = firestore_io.get_game(game_id)
    clock = period_clock_to_video_time_factory(game)
    iv = _onfield_intervals(game.starting_lineup, game.events, clock)
    out = []
    for pid, spans in iv.items():
        for (a, b) in spans:
            out.append((pid, float(a), float(b)))
    out.sort(key=lambda r: r[1])
    return out, game


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--tag", default="Q_base")
    ap.add_argument("--seed-mode", default="distinct",
                    choices=["distinct", "longest"],
                    help="how each stint's first body is chosen. 'distinct' "
                         "gives concurrent stints DIFFERENT bodies (correct); "
                         "'longest' reproduces the original bug where every "
                         "concurrent stint seeded the same track. Identity is "
                         "UNVERIFIED either way — this is a stand-in for the "
                         "coach's tap, not a claim about who the player is.")
    ap.add_argument("--reacquire-radius-m", type=float, default=None)
    args = ap.parse_args()

    from post_game.stint_follow import (
        Seed, coverage, distance_m, drift_check, follow_stints,
    )
    from tracking.stint_follow_probe import build_frame_index, load_frames

    on, L, W = load_frames(args.game_id, args.tag)
    times, byt = build_frame_index(on)
    t_lo, t_hi = float(times.min()), float(times.max())
    stints, game = build_stints(args.game_id)

    # Only stints that overlap the cached window can be followed at all. A
    # 15-minute smoke window holds a slice of one half, so most of a game's 35
    # stints are simply not covered by the parquet — say so rather than let the
    # denominator quietly shrink.
    usable = [(p, max(a, t_lo), min(b, t_hi)) for (p, a, b) in stints
              if b > t_lo and a < t_hi]
    usable = [(p, a, b) for (p, a, b) in usable if b - a >= 5.0]

    print(f"game {args.game_id}  vs {game.opponent}  tag {args.tag}")
    print(f"cached window {t_lo:.0f}-{t_hi:.0f}s  "
          f"({(t_hi - t_lo) / 60:.1f} min of {game.half_length_min * 2} min game)")
    print(f"coach-log stints: {len(stints)} total, "
          f"{len(usable)} overlap the cached window\n")
    if not usable:
        raise SystemExit("no stint overlaps this window; re-track a wider one.")

    # Seed each stint on a body. This stands in for the coach's tap.
    #
    # Concurrent stints MUST get different bodies. The first version took the
    # longest-lived track alive at each stint start, which is deterministic —
    # so all seven stints beginning at kickoff seeded the SAME track, followed
    # one child in lockstep, and died together when that track ended. It read
    # as a "3.3-minute wall" in the follower and was entirely an artefact of
    # the harness: seven duplicate measurements of one track's lifetime.
    lifetimes = (on.groupby("track_id").time_s.agg(["min", "max", "size"]))
    seeds, seeded = [], []
    claimed_at: dict[float, set[int]] = {}
    for pid, a, b in usable:
        i = int(np.searchsorted(times, a))
        if i >= len(times):
            continue
        tkey = float(times[i])
        taken = claimed_at.setdefault(tkey, set())
        fr = byt[tkey]
        alive = [r for r in fr if int(r[3]) in lifetimes.index
                 and (args.seed_mode == "longest" or int(r[3]) not in taken)]
        if not alive:
            continue
        best = max(alive, key=lambda r: lifetimes.loc[int(r[3]), "size"])
        taken.add(int(best[3]))
        seeds.append(Seed(player_id=f"{pid}@{a:.0f}", t0=tkey,
                          xy=(float(best[0]), float(best[1])), t_end=b))
        seeded.append((pid, a, b))

    kw = {}
    if args.reacquire_radius_m is not None:
        kw["reacquire_radius_m"] = args.reacquire_radius_m
    frames = [(float(t), byt[t][:, :3]) for t in times]
    out = follow_stints(frames, seeds, **kw)

    covs = np.array([coverage(t) for t in out])
    durs = np.array([b - a for (_, a, b) in seeded])
    drift = [t for t in out if drift_check(t)]

    print(f"{'stints followed':<28}{len(out)}")
    print(f"{'stint length (window-clipped)':<28}median {np.median(durs)/60:.1f} min"
          f"  max {durs.max()/60:.1f} min")
    print(f"{'median coverage':<28}{100*np.median(covs):.0f}%")
    print(f"{'mean coverage':<28}{100*covs.mean():.0f}%")
    print(f"{'stints under 50% coverage':<28}{int((covs < 0.5).sum())}")
    print(f"{'observed distance':<28}{sum(distance_m(t) for t in out)/1000:.2f} km")

    print(f"\n{'DRIFT: attached past sub-out':<28}{len(drift)}  "
          f"of {len(out)}  <- free correctness check, no labels needed")
    for t in drift[:5]:
        over = t.samples[-1][0] - t.t_end
        print(f"     {t.player_id:<22} {over:.0f}s past its logged end")

    # Coverage is the honest headline here: identity is unverified, so a high
    # coverage number means "we followed SOMETHING for most of the stint", which
    # is necessary but not sufficient.
    print("\nCoverage says how much of each stint was followed, NOT whether the "
          "right\nchild was followed — seeds are a heuristic stand-in and "
          "identity is unmeasured.\nPhase 2 (the coach's seeding UI) is what "
          "makes identity real.")


if __name__ == "__main__":
    main()
