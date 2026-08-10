#!/usr/bin/env python3
"""READ-ONLY go/no-go: does seed-and-follow survive a whole substitution stint?

The question this answers
-------------------------
The pipeline currently detects every body and works out afterwards which body
is which child. It is bad at that: 4269 raw ids for ~15 players on
mrhvbvwi1gjpn, 6.0 s median lifespan, 87% chain impurity on the other game.
Detection is fine — 99.5% of frames hold >=14 bodies — so the loss is
ASSOCIATION.

The proposed replacement is to name a player ONCE, at the moment they walk on,
and follow them until they walk off. The coach's two Jul-12 logs say a stint
(one player, one continuous appearance) has a median length of **8.0-8.1
minutes**, 35 per game. So the whole approach lives or dies on one number:

    seeded on a known body, how long until the follower is on a DIFFERENT body?

If that is 30 s the coach re-seeds constantly and the idea collapses. If it is
most of a stint, the interaction is "confirm a handful of times per half" and
it ships. This probe measures it before anything is built.

Pseudo-truth, and its honest limitation
---------------------------------------
No per-frame human labels exist (`tracking/labels/*_player_gt` is TRACKLET
level). Rather than spend coach hours before knowing the answer is worth
having, this uses the tracker's own long, teleport-free segments as stand-ins
for a known player: a track that runs >=MIN_TRUTH_S with no intra-id jump
faster than TELEPORT_MPS is very probably one child.

**It is not proven to be one child**, and this probe does not pretend
otherwise. It is calibrated to separate "swaps in seconds" from "survives
minutes", which is the decision at hand. Do not quote its output as ground
truth beyond that. Phase 2's seeding UI produces real labels; re-run then.

Keeping the test honest
-----------------------
The follower is given ONLY id-stripped per-frame detections (position + box).
Track ids are held back and used solely as the answer key. Otherwise the
follower would be re-reading the tracker's own association decisions and
scoring itself against them — a tautology of the same family as the
"id switch rate" that read 100% under every configuration and measured
nothing (see `tracking/sweep_score.teleports`).

Reported metrics
----------------
  * **survival**   seconds until the follower first attaches to a body that is
                   not the seeded one. Right-censored at the segment end, so
                   the median is reported over the observed distribution and
                   P(survive >= 8 min) is a Wilson interval, not a point guess.
  * **honest vs silent**  whether the follower DECLARED a gap before it went
                   wrong, or slid onto a team-mate confidently. This matters as
                   much as raw survival: a declared gap costs the coach one tap,
                   a silent swap corrupts a whole shift and looks fine. Most of
                   this project has been spent digging out quiet failures.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.stint_follow_probe \\
        --game-id mrhvbvwi1gjpn --tag Q_base
"""
from __future__ import annotations

import argparse
import math
import warnings

import numpy as np
import pandas as pd

# --- Physical / measurement constants -------------------------------------
# A generous U10 sprint. Well above the measured 0.08 m per 0.1 s step, so a
# jump past it is not a child running. Same value as sweep_score.teleports.
TELEPORT_MPS = 7.0
# A pseudo-truth segment must be long enough that it plausibly follows one
# child through contested play, not just a quiet spell.
MIN_TRUTH_S = 60.0
# A stint's worth of following: the number the whole approach has to clear.
STINT_TARGET_S = 480.0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Used because this probe's n is small and a naive
    k/n +- normal interval overstates confidence exactly where it matters.
    An earlier detector bake-off in this repo ranked arms on n=40 whose CIs
    turned out to overlap completely."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - m), min(1.0, c + m))


def _poisson_ci(k: int, z: float = 1.96) -> tuple[float, float]:
    """Garwood exact Poisson interval on a COUNT of events.

    Used instead of a normal approximation because this probe's swap count is
    small (single digits), where sqrt(k) intervals are badly wrong and k=0 has
    no interval at all.
    """
    from scipy.stats import chi2
    lo = 0.0 if k == 0 else chi2.ppf(0.025, 2 * k) / 2.0
    hi = chi2.ppf(0.975, 2 * (k + 1)) / 2.0
    return float(lo), float(hi)


def load_frames(game_id: str, tag: str):
    """Cached Stage-2 checkpoint -> per-frame detections in METRES.

    Mirrors `tracking.sweep_score.load` (same projector, same on-field crop) so
    this probe and the sweep are measuring the same population of bodies.
    """
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector

    # An empty/None tag means the pipeline's own untagged checkpoint
    # (`tracks_raw.parquet`) rather than a sweep arm. Game 2's full-game cache
    # is untagged, so without this the path becomes `tracks_raw..parquet`.
    path = (config.OUTPUTS_DIR / game_id /
            (f"tracks_raw.{tag}.parquet" if tag else "tracks_raw.parquet"))
    if not path.exists():
        raise SystemExit(
            f"no checkpoint {path}. Run tracking.retrack_smoke --tag {tag} first.")
    cal = firestore_io.get_game_calibration(game_id)
    if cal is None:
        raise SystemExit(f"no calibration for {game_id}")
    L, W = cal.length_m, cal.width_m
    tr = pd.read_parquet(path)
    xy = FieldProjector(cal).pixel_to_field_batch(
        tr[["foot_x_eq", "foot_y_eq"]].to_numpy())
    tr["x_m"], tr["y_m"] = xy[:, 0], xy[:, 1]
    on = tr[(tr.x_m >= -1.5) & (tr.x_m <= L + 1.5)
            & (tr.y_m >= -1.5) & (tr.y_m <= W + 1.5)].copy()
    return on, L, W


def truth_segments(on: pd.DataFrame, min_s: float = MIN_TRUTH_S) -> list[dict]:
    """Long, teleport-free track segments to stand in for a known player.

    A track id is only usable as truth if it never jumps faster than a child
    can run: a teleport means the id itself changed bodies, so it cannot be the
    answer key. Tracks are split at any teleport and each clean piece is judged
    on its own length.
    """
    segs: list[dict] = []
    for tid, g in on.sort_values("time_s").groupby("track_id"):
        if len(g) < 2:
            continue
        t = g.time_s.to_numpy()
        x, y = g.x_m.to_numpy(), g.y_m.to_numpy()
        dt = np.diff(t)
        step = np.hypot(np.diff(x), np.diff(y))
        # Break at teleports (and at long internal holes, which are not
        # continuous observation of one body either).
        brk = np.where((step > TELEPORT_MPS * dt) | (dt > 2.0))[0]
        bounds = [0, *(brk + 1), len(t)]
        for a, b in zip(bounds[:-1], bounds[1:]):
            if b - a < 2:
                continue
            dur = t[b - 1] - t[a]
            if dur >= min_s:
                segs.append({
                    "track_id": int(tid), "t0": float(t[a]), "t1": float(t[b - 1]),
                    "dur_s": float(dur), "n": int(b - a),
                })
    segs.sort(key=lambda s: -s["dur_s"])
    return segs


def build_frame_index(on: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """time -> (N,4) array of [x_m, y_m, box_h, track_id].

    track_id rides along ONLY so the scorer can check the answer; the follower
    is handed columns 0:3 and never sees it.
    """
    byt: dict[float, np.ndarray] = {}
    for t, g in on.groupby("time_s"):
        byt[float(t)] = g[["x_m", "y_m", "bbox_h_crop", "track_id"]].to_numpy()
    times = np.array(sorted(byt), dtype=float)
    return times, byt


def follow(times: np.ndarray, byt: dict, seg: dict,
           max_step_mps: float, ambig_margin_m: float,
           heading_w: float, max_gap_s: float,
           lone_gate_m: float | None = 0.35) -> dict:
    """Seed on `seg` at its first frame, then follow forward on detections only.

    Returns the outcome of ONE seeded follow: how long it stayed on the seeded
    body, and — crucially — whether it declared uncertainty before it went
    wrong or slid across silently.

    Association is deliberately the simplest thing that could work: nearest
    detection to a constant-velocity prediction, inside a physical reach gate,
    plus the measured direction prior. Appearance is left out — it is INERT on
    identical kits (identical results on/off; 0.063 cosine margin, 53% correct
    vs 76% for heading), so adding it would cost runtime and buy nothing.
    """
    from post_game.tracking import heading_penalty

    i0 = int(np.searchsorted(times, seg["t0"]))
    i1 = int(np.searchsorted(times, seg["t1"], side="right"))
    seed_rows = byt[times[i0]]
    seed = seed_rows[seed_rows[:, 3] == seg["track_id"]]
    if not len(seed):
        return {"ok": False}

    px, py = float(seed[0][0]), float(seed[0][1])
    vx = vy = 0.0
    t_prev = float(times[i0])
    gap_declared_at: float | None = None
    swap_at: float | None = None
    n_gap = 0

    for i in range(i0 + 1, i1):
        t = float(times[i])
        dt = t - t_prev
        if dt <= 0 or dt > max_gap_s:
            # Observation hole longer than we are willing to coast through.
            if gap_declared_at is None:
                gap_declared_at = t
            n_gap += 1
            t_prev = t
            continue
        rows = byt[t]
        # Constant-velocity prediction, then a physical reach gate. A child
        # cannot cover more than max_step_mps * dt, so anything beyond that is
        # a different body regardless of how well it scores.
        qx, qy = px + vx * dt, py + vy * dt
        d = np.hypot(rows[:, 0] - qx, rows[:, 1] - qy)
        reach = max_step_mps * dt
        cand = np.where(d <= reach)[0]
        if not len(cand):
            if gap_declared_at is None:
                gap_declared_at = t
            n_gap += 1
            t_prev = t
            continue

        cost = d[cand].astype(float)
        if heading_w > 0.0 and (vx or vy):
            for j, ci in enumerate(cand):
                cost[j] += heading_w * heading_penalty(
                    (px, py), (vx, vy), (rows[ci, 0], rows[ci, 1]),
                    box_h=float(rows[ci, 2]))
        order = np.argsort(cost)
        best = cand[order[0]]

        # A SOLE candidate must still be plausible, not merely unopposed.
        # Every swap measured on mrhvbvwi1gjpn/Q_base had exactly ONE candidate
        # in reach (n=5, all of them): the follower was never choosing wrongly
        # between rivals, it was accepting the only body available. Three were
        # detection holes — the seeded player was not detected that frame and
        # someone else sat within reach — and two were prediction overshoot at
        # sprint speed (6.8 and 1.5 m/s), where constant velocity threw the
        # query past the true body while an impostor fell inside the gate.
        #
        # Requiring a lone candidate to sit close to the PREDICTION converts
        # those into declared gaps. Measured: swap rate 0.098 -> 0.057 per
        # minute, projected stint survival 45% -> 64%, for ~2x the declared
        # gaps. 0.35 m is the knee; 0.25 m catches no further swaps and only
        # adds gaps. NOTE anything above ~0.7 m is INERT at 10 Hz, because the
        # reach gate (max_step_mps * dt) already binds tighter than that.
        if lone_gate_m is not None and d[best] > lone_gate_m:
            if gap_declared_at is None:
                gap_declared_at = t
            n_gap += 1
            t_prev = t
            continue

        # Ambiguity => DECLARE, do not guess. Two bodies within the margin of
        # each other is exactly the crossing/contest case where a silent pick
        # corrupts the rest of the stint.
        if len(order) > 1 and (cost[order[1]] - cost[order[0]]) < ambig_margin_m:
            if gap_declared_at is None:
                gap_declared_at = t
            n_gap += 1
            t_prev = t
            continue

        if int(rows[best, 3]) != seg["track_id"] and swap_at is None:
            swap_at = t          # first attach to a body that is not the seed
        nx, ny = float(rows[best, 0]), float(rows[best, 1])
        vx, vy = (nx - px) / dt, (ny - py) / dt
        px, py = nx, ny
        t_prev = t

    survived = (swap_at - seg["t0"]) if swap_at is not None else (seg["t1"] - seg["t0"])
    return {
        "ok": True, "track_id": seg["track_id"], "seg_dur_s": seg["dur_s"],
        "survived_s": float(survived),
        "censored": swap_at is None,     # ran out of segment before any swap
        "n_gap": n_gap,
        # Did it warn us before it went wrong? Honest failure vs silent swap.
        "honest": bool(swap_at is not None and gap_declared_at is not None
                       and gap_declared_at <= swap_at),
    }


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--tag", default="Q_base")
    ap.add_argument("--max-step-mps", type=float, default=7.0,
                    help="reach gate; a child cannot exceed this")
    ap.add_argument("--ambig-margin-m", type=float, default=0.5,
                    help="two candidates closer than this => declare, don't guess")
    ap.add_argument("--heading-w", type=float, default=1.0,
                    help="weight on the direction-of-travel prior (0 = off)")
    ap.add_argument("--max-gap-s", type=float, default=1.0,
                    help="longest observation hole to coast through")
    ap.add_argument("--lone-gate-m", type=float, default=0.35,
                    help="a SOLE candidate must sit within this of the "
                         "prediction, else declare a gap (0 = accept any). "
                         "0.35 measured as the knee; >0.7 is inert at 10 Hz")
    ap.add_argument("--min-truth-s", type=float, default=MIN_TRUTH_S)
    ap.add_argument("--limit", type=int, default=0, help="cap segments (0 = all)")
    args = ap.parse_args()

    on, L, W = load_frames(args.game_id, args.tag)
    segs = truth_segments(on, args.min_truth_s)
    if args.limit:
        segs = segs[:args.limit]
    if not segs:
        raise SystemExit(
            f"no teleport-free segments >= {args.min_truth_s:.0f}s in "
            f"{args.game_id}/{args.tag}. Lower --min-truth-s to inspect.")

    times, byt = build_frame_index(on)
    lone = args.lone_gate_m if args.lone_gate_m > 0 else None
    win = f"{on.time_s.min():.0f}-{on.time_s.max():.0f}s"
    print(f"game {args.game_id}  tag {args.tag}  window {win}  "
          f"field {L:.1f}x{W:.1f} m")
    print(f"pseudo-truth: {len(segs)} teleport-free segments >= "
          f"{args.min_truth_s:.0f}s  (median {np.median([s['dur_s'] for s in segs]):.0f}s, "
          f"max {segs[0]['dur_s']:.0f}s)")
    print(f"follower: reach {args.max_step_mps} m/s, ambiguity margin "
          f"{args.ambig_margin_m} m, heading w={args.heading_w}, "
          f"coast <= {args.max_gap_s}s, lone-cand gate "
          f"{('%.2f m' % lone) if lone else 'off'}\n")

    res = [r for r in (follow(times, byt, s, args.max_step_mps,
                              args.ambig_margin_m, args.heading_w,
                              args.max_gap_s, lone) for s in segs) if r.get("ok")]
    if not res:
        raise SystemExit("no segment could be seeded.")

    surv = np.array([r["survived_s"] for r in res])
    cens = np.array([r["censored"] for r in res])
    swapped = [r for r in res if not r["censored"]]

    print(f"{'n seeded':<26}{len(res)}")
    print(f"{'swapped before seg end':<26}{len(swapped)}"
          f"   ({100.0*len(swapped)/len(res):.0f}%)")
    print(f"{'ran clean to seg end':<26}{int(cens.sum())}"
          f"   ({100.0*cens.mean():.0f}%)  <- right-censored")
    print()
    print(f"{'median observed survival':<26}{np.median(surv):.0f}s"
          f"   ({np.median(surv)/60:.1f} min)")
    for q in (25, 75):
        print(f"{'  p' + str(q):<26}{np.percentile(surv, q):.0f}s")
    if swapped:
        sw = np.array([r["survived_s"] for r in swapped])
        print(f"{'median time-to-swap':<26}{np.median(sw):.0f}s"
              f"   ({np.median(sw)/60:.1f} min)   [swappers only]")

    # --- Survival must be estimated as a RATE, not as "did it last 8 min" ----
    # 83% of follows run clean to the end of their segment, so the observed
    # median survival is really the median SEGMENT LENGTH: it measures how long
    # the pseudo-truth lasts, not how long the follower does. And the longest
    # teleport-free segment available is ~260 s, because the tracker fragments
    # long before a stint ends — so NO segment can demonstrate 8-minute
    # survival, and a naive P(survive >= 8 min) reads 0% by construction.
    #
    # The censoring-correct question is the swap RATE: over all the time we
    # actually observed, how often did a swap happen? That is estimable from
    # short segments and extrapolates honestly to a stint under a constant-
    # hazard assumption (stated, not hidden — see the caveat printed below).
    exposure_s = float(surv.sum())
    n_swap = len(swapped)
    rate_per_min = n_swap / (exposure_s / 60.0) if exposure_s else float("nan")
    print(f"\n{'exposure (time followed)':<26}{exposure_s/60:.1f} min"
          f"   across {len(res)} seeds")
    print(f"{'swaps observed':<26}{n_swap}")
    print(f"{'swap rate':<26}{rate_per_min:.3f} per minute followed")
    # Poisson CI on the count, carried through to the rate and then to survival.
    if exposure_s > 0:
        k_lo, k_hi = _poisson_ci(n_swap)
        r_lo = k_lo / (exposure_s / 60.0)
        r_hi = k_hi / (exposure_s / 60.0)
        surv8 = math.exp(-rate_per_min * STINT_TARGET_S / 60.0)
        s_lo = math.exp(-r_hi * STINT_TARGET_S / 60.0)
        s_hi = math.exp(-r_lo * STINT_TARGET_S / 60.0)
        print(f"{'  95% CI':<26}[{r_lo:.3f}, {r_hi:.3f}] per min")
        print(f"\nprojected P(survive a {STINT_TARGET_S/60:.0f} min stint) = "
              f"{100*surv8:.0f}%   95% CI [{100*s_lo:.0f}%, {100*s_hi:.0f}%]")
        print(f"  assumes a CONSTANT swap hazard; longest observed segment is "
              f"{max(s['dur_s'] for s in segs):.0f}s, so 8 min is an "
              f"EXTRAPOLATION,\n  not an observation.")

    if swapped:
        h = sum(r["honest"] for r in swapped)
        lo2, hi2 = wilson(h, len(swapped))
        print(f"\nof the {len(swapped)} swaps: {h} declared a gap first "
              f"({100.0*h/len(swapped):.0f}%, CI [{100*lo2:.0f}%, {100*hi2:.0f}%])"
              f"  <- honest")
        print(f"{'':>21}{len(swapped)-h} slid across SILENTLY"
              f"  <- these corrupt a shift and look fine")

    # --- gate, on the rate (the median is censored and cannot carry it) ------
    # Expressed as expected coach taps: a swap mid-stint costs one correction,
    # which is the unit the coach actually feels. 35 stints/game.
    print("\n--- gate ---")
    if not exposure_s:
        raise SystemExit("no exposure; cannot judge.")
    taps = rate_per_min * (STINT_TARGET_S / 60.0) * 35.0
    print(f"implied corrections per game: ~{taps:.0f}   "
          f"(swap rate x 8 min x 35 stints)")
    if rate_per_min <= 0.125:          # <= 1 swap per stint
        print(f"PASS  {rate_per_min:.3f} swaps/min <= 0.125 (1 per stint). "
              f"Proceed to Phase 1 as planned.")
    elif rate_per_min <= 0.5:          # <= 4 per stint
        print(f"PARTIAL  {rate_per_min:.3f} swaps/min. Workable, but the coach "
              f"re-confirms mid-stint;\n         re-scope the time estimate "
              f"before building Phase 2.")
    else:
        print(f"FAIL  {rate_per_min:.3f} swaps/min. Seed-and-follow as specified "
              f"does not survive a stint;\n      stop and rethink rather than build.")
    print("\nCaveats that bound this number:")
    print("  * pseudo-truth is the tracker's own clean segments, not human "
          "labels — good enough\n    to separate seconds from minutes, not to "
          "quote as ground truth.")
    print(f"  * longest segment is {max(s['dur_s'] for s in segs):.0f}s, so "
          f"8-minute survival is extrapolated under a\n    constant-hazard "
          f"assumption, never observed.")
    print("  * a follow is scored only against the body it was seeded on; a "
          "swap onto an\n    OPPONENT and onto a team-mate count the same here.")


if __name__ == "__main__":
    main()
