#!/usr/bin/env python3
"""READ-ONLY: score tagged Stage-2 checkpoints against each other.

Compares tracker variants produced by `tracking/retrack_smoke.py --tag <name>`
on the same window, so a threshold/rescue change can be judged before spending
two hours on a full re-track.

The metrics, and why each one is here:

  * **tracks** and **median lifespan** — the thing being fixed. Baseline on
    mrhvbvwi1gjpn was 4269 ids for ~15 players at 6.0 s median.

  * **same-person pairs left** — consecutive track pairs that are obviously one
    child (gap <= 2 s, reachable at 3 m/s, non-overlapping). 1339 of them on the
    baseline, i.e. a perfect short-gap re-association would cut fragmentation
    ~31%. This counts how much of that headroom a variant actually took.

  * **id-switch proxy** — mid-field deaths with a DIFFERENT id within 1.5 m on
    the next frame. Baseline: of the deaths that had any neighbour that close,
    100% carried a different id.

  * **bodies/frame** and **median step** — the guard rails, and the reason this
    tool exists rather than eyeballing the track count. Lowering the detection
    thresholds will always reduce fragmentation *somewhat* by admitting more
    boxes; if it also pushes bodies/frame past the ~15-17 a 7v7 can physically
    hold, or inflates the per-frame step above the measured 0.08 m of a real
    player, the variant has invented players rather than tracked them. A win
    must improve fragmentation while leaving both of these alone.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.sweep_score \\
        --game-id mrhvbvwi1gjpn --tags A_base B_align C_both D_rescue
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np
import pandas as pd


def load(game_id: str, tag: str):
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector

    path = config.OUTPUTS_DIR / game_id / f"tracks_raw.{tag}.parquet"
    if not path.exists():
        return None
    fc = firestore_io.get_game_calibration(game_id)
    L, W = fc.length_m, fc.width_m
    tr = pd.read_parquet(path)
    xy = FieldProjector(fc).pixel_to_field_batch(
        tr[["foot_x_eq", "foot_y_eq"]].to_numpy())
    tr["x_m"], tr["y_m"] = xy[:, 0], xy[:, 1]
    on = tr[(tr.x_m >= -1.5) & (tr.x_m <= L + 1.5)
            & (tr.y_m >= -1.5) & (tr.y_m <= W + 1.5)].copy()
    return on, L, W


def same_person_pairs(on: pd.DataFrame, max_gap_s=2.0, speed=3.0) -> int:
    """Consecutive track pairs that are obviously one child, still unmerged."""
    s = (on.sort_values("time_s").groupby("track_id")
         .agg(t0=("time_s", "first"), t1=("time_s", "last"),
              x1=("x_m", "last"), y1=("y_m", "last"),
              x0=("x_m", "first"), y0=("y_m", "first"))
         .sort_values("t0"))
    starts = s[["t0", "x0", "y0"]].to_numpy()
    ends = s[["t1", "x1", "y1"]].to_numpy()
    ids = s.index.to_numpy()
    used, pairs = set(), 0
    for i in range(len(s)):
        te, xe, ye = ends[i]
        j0 = np.searchsorted(starts[:, 0], te - 1e-3)
        for j in range(j0, min(j0 + 40, len(s))):
            if ids[j] == ids[i] or ids[j] in used:
                continue
            ts, xs, ys = starts[j]
            gap = ts - te
            if gap < 0 or gap > max_gap_s:
                continue
            if np.hypot(xs - xe, ys - ye) <= max(1.0, speed * gap):
                pairs += 1
                used.add(ids[j])
                break
    return pairs


def teleports(on: pd.DataFrame, max_speed=7.0) -> tuple[int, float]:
    """Jumps inside ONE track id faster than a child can run => the id moved bodies.

    This replaces an "id switch rate" that was tautological: it asked whether a
    track's LAST frame had a successor carrying the same id, which by definition
    it never does — if it did, the track would not have ended. That metric read
    100% under every configuration and measured nothing, yet it was cited as
    evidence that no change affected identity confusion.

    A teleport is label-free and genuinely varies between configurations. 7 m/s
    is a generous U10 sprint, well above the measured 0.08 m per 0.1 s step, so
    anything past it is not a child running.
    """
    s = on.sort_values(["track_id", "time_s"])
    d = s.groupby("track_id")[["x_m", "y_m"]].diff()
    dt = s.groupby("track_id")["time_s"].diff()
    tele = (np.hypot(d.x_m, d.y_m) > max_speed * dt) & dt.between(0.05, 2.0)
    return int(tele.sum()), float(dt[tele].sum())


def id_switch_proxy(on: pd.DataFrame, L: float, W: float, radius=1.5) -> tuple:
    frames = np.sort(on.frame.unique())
    nxt = dict(zip(frames[:-1], frames[1:]))
    byf = {f: g[["x_m", "y_m", "track_id"]].to_numpy()
           for f, g in on.groupby("frame")}
    last = on.sort_values("time_s").groupby("track_id").tail(1)
    edge = 2.0
    inter = last[(last.x_m >= edge) & (last.x_m <= L - edge)
                 & (last.y_m >= edge) & (last.y_m <= W - edge)]
    near = switched = 0
    for _, r in inter.iterrows():
        a = byf.get(nxt.get(r.frame, -1))
        if a is None or not len(a):
            continue
        d = np.hypot(a[:, 0] - r.x_m, a[:, 1] - r.y_m)
        k = int(np.argmin(d))
        if d[k] <= radius:
            near += 1
            switched += int(a[k, 2]) != int(r.track_id)
    return len(inter), near, switched


def score(game_id: str, tag: str) -> dict | None:
    got = load(game_id, tag)
    if got is None:
        return None
    on, L, W = got
    life = (on.groupby("track_id").time_s.max()
            - on.groupby("track_id").time_s.min())
    per_frame = on.groupby("frame").size()
    s = on.sort_values(["track_id", "time_s"])
    d = s.groupby("track_id")[["x_m", "y_m"]].diff()
    dt = s.groupby("track_id")["time_s"].diff()
    step = np.hypot(d.x_m, d.y_m)[dt.between(0.05, 0.15)].dropna()
    n_tele, _ = teleports(on)
    n_tracks = int(on.track_id.nunique())
    return {
        "tag": tag,
        "tracks": n_tracks,
        "median_life_s": float(life.median()),
        "pairs_left": same_person_pairs(on),
        "tele_per_track": n_tele / max(n_tracks, 1),
        "bodies_per_frame": float(per_frame.median()),
        "median_step_m": float(step.median()) if len(step) else float("nan"),
    }


def main() -> None:
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--tags", nargs="+", required=True)
    args = ap.parse_args()

    rows = [r for r in (score(args.game_id, t) for t in args.tags) if r]
    if not rows:
        raise SystemExit(
            f"no checkpoints found for {args.game_id} with tags {args.tags}. "
            f"Run tracking.retrack_smoke --tag <name> first.")
    missing = set(args.tags) - {r["tag"] for r in rows}
    if missing:
        print(f"!! no checkpoint for: {sorted(missing)}\n")

    hdr = (f"{'tag':<10}{'tracks':>8}{'med life':>10}{'pairs left':>12}"
           f"{'tele/trk':>10}{'bodies/fr':>11}{'step m':>9}")
    print(hdr)
    print("-" * len(hdr))
    base = rows[0]
    for r in rows:
        print(f"{r['tag']:<10}{r['tracks']:>8}{r['median_life_s']:>9.1f}s"
              f"{r['pairs_left']:>12}{r['tele_per_track']:>10.2f}"
              f"{r['bodies_per_frame']:>11.0f}{r['median_step_m']:>9.3f}")
    print(f"\nvs {base['tag']}:")
    for r in rows[1:]:
        dt = 100.0 * (r["tracks"] - base["tracks"]) / max(base["tracks"], 1)
        dp = 100.0 * (r["pairs_left"] - base["pairs_left"]) / max(base["pairs_left"], 1)
        db = r["bodies_per_frame"] - base["bodies_per_frame"]
        dtel = 100.0 * (r["tele_per_track"] - base["tele_per_track"]) / max(base["tele_per_track"], 1e-9)
        flag = ""
        if db > 2:
            flag = "  <-- REJECT: invented bodies"
        elif dtel > 8:
            flag = "  <-- REJECT: buys fragmentation by merging DIFFERENT children"
        elif r["median_step_m"] > 1.6 * base["median_step_m"]:
            flag = "  <-- REJECT: step inflated"
        print(f"  {r['tag']:<10} tracks {dt:+6.1f}%   pairs {dp:+6.1f}%   "
              f"teleports {dtel:+6.1f}%   bodies/fr {db:+.1f}{flag}")
    print("\nA win = fewer tracks AND fewer same-person pairs, with teleports and\n"
          "bodies/frame flat. Fewer tracks bought by merging different children\n"
          "(teleports up) is worse than the fragmentation it replaced.")


if __name__ == "__main__":
    main()
