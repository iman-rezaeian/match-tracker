#!/usr/bin/env python3
"""READ-ONLY Phase 0: is the anchored-claim scheme worth building?

The premise
-----------
Per-player metrics fail today because ~23% of a player's attributed frames
belong to another child, and position error scales LINEARLY with that
contamination. Coverage is not the binding problem; PURITY is. Measured
separately: 14% *pure* coverage gives 0.3% error on mean position, the same as
35%. So the plan is to claim only trajectory we can PROVE belongs to one child
-- a clean (teleport-free) run containing a named instant -- and discard the
rest rather than guess.

This probe answers whether that actually pays, BEFORE any UI is built. It is
read-only: it writes nothing to Firestore and mutates no cache.

Ground truth
------------
The coach hand-labelled raw track ids in FIX-IDS on the two blind-GT games,
surviving in `game.identityOverrides` as {track_id: player_id | "__opp__" |
"__other__"}. Both join their cached tracks 100%. NOTE the originally-planned
game (W8 mri01pvelv46d, "99 labels") has since been WIPED to 0 overrides --
re-tracking invalidates overrides and they were not remappable, which is
already recorded. Hence GT here is mqcf9axlvtuyt / mqcjsjugchb2i.

What it reports
---------------
1. clean-run inventory      -- cut every track at teleports; how much clean time
                               exists, and in what run lengths
2. coverage-per-tap curve   -- longest-first, OUR TEAM ONLY, against the real
                               player-minute denominator. Replaces the earlier
                               upper-bound estimate, which ignored that ~half of
                               taps land on opponents/adults.
3. anchor precision         -- for a claimed run, does the anchor's label agree
                               with the coach's label on that run? Split by
                               whether the run is single- or multi-label.
4. poisoning sensitivity    -- inject anchor error and measure the resulting
                               position-metric error, to set the precision bar.

Scoring discipline (inherited from the Stage-2 bake-off, which produced three
illusory "wins" by exactly this route): clean-minutes-unlocked is the objective;
run PURITY and bodies/frame are GUARDS. A scheme that welds two children into
one long run scores better on every volume metric at once, so purity is checked
against the labels rather than assumed.

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.anchor_queue_probe
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

import numpy as np
import pandas as pd

# Teleport oracle: a jump faster than this inside one track id is not a child
# running, it is an association error. Same threshold the Stage-2 sweep and the
# per-player decision used, so the numbers are comparable across documents.
MAX_SPEED_MS = 7.0
# A time gap this long breaks a run: bridging it would invent unobserved motion.
MAX_GAP_S = 0.5
# 7 on the field for a 53-minute game, both halves.
PLAYER_MIN_PER_GAME = 8 * 53.0

GT_GAMES = ["mqcf9axlvtuyt", "mqcjsjugchb2i"]


def _outputs_dir() -> Path:
    """Cache lives in the main checkout; this may run from a worktree."""
    here = Path(__file__).resolve().parents[1] / "post_game" / "outputs"
    if here.exists():
        return here
    return Path("/Users/irezaeian/match-tracker/post_game/outputs")


def load_tracks(game_id: str) -> pd.DataFrame:
    p = _outputs_dir() / game_id / "tracks_raw.parquet"
    return pd.read_parquet(p)


def cut_clean_runs(df: pd.DataFrame) -> pd.DataFrame:
    """Split every track at teleports and time gaps -> clean-by-construction runs.

    Works in EQUIRECT PIXELS, not metres: these GT games have no field
    calibration loaded here, and the probe only needs a *relative* purity cut.
    The pixel threshold is derived per game from the step distribution rather
    than hardcoded, so it adapts to resolution.
    """
    d = df.sort_values(["track_id", "time_s"])
    x = d["foot_x_eq"].to_numpy(float)
    y = d["foot_y_eq"].to_numpy(float)
    t = d["time_s"].to_numpy(float)
    tid = d["track_id"].to_numpy()

    step = np.full(len(d), np.nan)
    dt = np.full(len(d), np.nan)
    same = tid[1:] == tid[:-1]
    step[1:][same] = np.hypot(np.diff(x), np.diff(y))[same]
    dt[1:][same] = np.diff(t)[same]

    # Convert the 7 m/s cap into pixels using the median step as the scale
    # anchor: a normal 10 Hz step is ~0.12 m, so px-per-metre ~= med_step/0.12.
    med_step = float(np.nanmedian(step[step > 0])) if np.isfinite(step).any() else 1.0
    px_per_m = max(med_step / 0.118, 1e-6)
    cap_px = MAX_SPEED_MS * px_per_m * np.where(np.isnan(dt), 0.1, dt)

    new_track = ~np.concatenate([[False], same])
    teleport = np.nan_to_num(step) > cap_px
    gap = np.nan_to_num(dt) > MAX_GAP_S
    d = d.assign(run_id=np.cumsum(new_track | teleport | gap))
    return d


def run_table(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby("run_id")
    out = g.agg(track_id=("track_id", "first"), t0=("time_s", "min"),
                t1=("time_s", "max"), n=("time_s", "size")).reset_index()
    out["dur"] = out.t1 - out.t0
    return out[out.n >= 3]


def report_inventory(runs: pd.DataFrame, label: str) -> None:
    d = runs.dur.to_numpy()
    print(f"\n[1] clean-run inventory — {label}")
    print(f"    runs {len(d):,}   total {d.sum()/60:.0f} min")
    print(f"    median {np.median(d):.1f}s   p75 {np.quantile(d,.75):.1f}s   "
          f"p90 {np.quantile(d,.90):.1f}s   max {d.max():.0f}s")
    for thr in (5, 10, 30, 60):
        sel = d >= thr
        print(f"    >={thr:3d}s: {sel.sum():5,} runs holding {d[sel].sum()/60:6.1f} min")


def report_curve(runs: pd.DataFrame, ov: dict, label: str) -> None:
    """Coverage-per-tap, longest-first, OUR TEAM ONLY.

    The key correction over the earlier estimate: a tap on the queue lands on
    whatever run is next, and most long runs are opponents/adults. Those taps
    are spent (one tap to say "opponent") but buy ZERO of our coverage.
    """
    kind = {}
    for k, v in ov.items():
        if str(k).isdigit():
            kind[int(k)] = ("ours" if v not in ("__opp__", "__other__") else "not_ours")
    r = runs.copy()
    r["kind"] = r.track_id.map(kind)
    labelled = r[r.kind.notna()].sort_values("dur", ascending=False)
    if labelled.empty:
        print(f"\n[2] coverage curve — {label}: no labelled runs, skipped")
        return

    ours_share = (labelled.kind == "ours").mean()
    print(f"\n[2] coverage-per-tap (longest-first, labelled runs only) — {label}")
    print(f"    labelled runs {len(labelled):,}   of which ours {ours_share*100:.0f}%")
    print(f"    {'taps':>6} {'ours min':>9} {'coverage':>9} {'wasted taps':>12} {'coach time':>11}")
    dur = labelled.dur.to_numpy()
    isours = (labelled.kind == "ours").to_numpy()
    for n in (50, 100, 200, 400, 800):
        if n > len(labelled):
            break
        mins = dur[:n][isours[:n]].sum() / 60
        wasted = int((~isours[:n]).sum())
        print(f"    {n:6d} {mins:9.1f} {min(mins/PLAYER_MIN_PER_GAME,1)*100:8.1f}% "
              f"{wasted:12d} {n*4/60:9.0f} min")


def report_purity(d: pd.DataFrame, runs: pd.DataFrame, ov: dict, label: str) -> None:
    """THE GUARD. A run is only a valid claim unit if it holds ONE child.

    Checks each run against the coach's per-track labels. A run lives inside a
    single track id by construction, so a run can only be impure if the TRACK
    itself was impure -- which the coach's single label per track cannot reveal.
    So this measures the weaker but still decisive thing: do the runs we would
    claim sit on tracks the coach called a real player, and how much of the
    queue is spent on non-players?
    """
    lab = {int(k): v for k, v in ov.items() if str(k).isdigit()}
    r = runs.copy()
    r["lab"] = r.track_id.map(lab)
    known = r[r.lab.notna()]
    print(f"\n[3] what the queue would be made of — {label}")
    kinds = collections.Counter(
        "ours" if v not in ("__opp__", "__other__") else v for v in known.lab)
    tot = sum(kinds.values())
    for k, v in kinds.most_common():
        mins = known[known.lab.map(lambda z: ("ours" if z not in ("__opp__", "__other__") else z)) == k].dur.sum()/60
        print(f"    {k:12s} {v:5d} runs ({100*v/tot:4.1f}%)  {mins:7.1f} min")
    # how many DISTINCT players do the labelled runs cover?
    ours = known[~known.lab.isin(["__opp__", "__other__"])]
    print(f"    distinct players covered: {ours.lab.nunique()}")
    per = ours.groupby("lab").dur.sum().sort_values(ascending=False)/60
    print(f"    per-player clean min: median {per.median():.1f}, "
          f"min {per.min():.1f}, max {per.max():.1f}")


def report_poisoning(d: pd.DataFrame, runs: pd.DataFrame, ov: dict, label: str) -> None:
    """How wrong can an anchor be before position metrics break?

    Builds each labelled player's position from his claimed runs, then swaps a
    fraction of runs to another player and measures the drift. Sets the
    precision bar the queue's naming step must clear.
    """
    lab = {int(k): v for k, v in ov.items() if str(k).isdigit()}
    r = runs.copy()
    r["lab"] = r.track_id.map(lab)
    ours = r[r.lab.notna() & ~r.lab.isin(["__opp__", "__other__"])]
    if ours.lab.nunique() < 3:
        print(f"\n[4] poisoning — {label}: too few players, skipped")
        return
    pos = d.groupby("run_id")[["foot_x_eq", "foot_y_eq"]].mean()
    ours = ours.join(pos, on="run_id")
    byp = {p: g for p, g in ours.groupby("lab")}
    rng = np.random.default_rng(0)
    print(f"\n[4] anchor-error sensitivity — {label}")
    print(f"    {'err rate':>9} {'mean-pos drift':>16}")
    for frac in (0.0, 0.05, 0.10, 0.20, 0.30):
        drift = []
        for p, g in byp.items():
            if len(g) < 4:
                continue
            truth = np.array([np.average(g.foot_x_eq, weights=g.dur),
                              np.average(g.foot_y_eq, weights=g.dur)])
            others = [q for q in byp if q != p]
            n_bad = int(round(len(g) * frac))
            gg = g
            if n_bad:
                donor = pd.concat([byp[q] for q in rng.choice(others,
                                  size=min(3, len(others)), replace=False)])
                if len(donor):
                    gg = pd.concat([g.iloc[n_bad:], donor.sample(
                        n_bad, replace=True, random_state=1)])
            est = np.array([np.average(gg.foot_x_eq, weights=gg.dur),
                            np.average(gg.foot_y_eq, weights=gg.dur)])
            drift.append(np.hypot(*(est - truth)))
        print(f"    {frac*100:7.0f}% {np.median(drift):14.0f} px")
    # scale reference: how far apart are two players' mean positions?
    means = np.array([[np.average(g.foot_x_eq, weights=g.dur),
                       np.average(g.foot_y_eq, weights=g.dur)]
                      for g in byp.values() if len(g) >= 4])
    sep = [np.hypot(*(means[i]-means[j])) for i in range(len(means))
           for j in range(i+1, len(means))]
    print(f"    reference: two different players sit {np.median(sep):.0f} px apart")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", nargs="*", default=GT_GAMES)
    args = ap.parse_args()

    from post_game import firestore_io

    for gid in args.games:
        try:
            df = load_tracks(gid)
        except FileNotFoundError:
            print(f"\n!! {gid}: no cached tracks, skipped")
            continue
        g = firestore_io.get_game(gid)
        ov = g.identity_overrides or {}
        print("=" * 72)
        print(f"{gid}   rows {len(df):,}   tracks {df.track_id.nunique():,}   "
              f"coach labels {len(ov)}")
        d = cut_clean_runs(df)
        runs = run_table(d)
        report_inventory(runs, gid)
        report_curve(runs, ov, gid)
        report_purity(d, runs, ov, gid)
        report_poisoning(d, runs, ov, gid)


if __name__ == "__main__":
    main()
