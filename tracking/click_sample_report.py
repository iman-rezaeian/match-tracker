#!/usr/bin/env python3
"""Report per-player POSITION metrics from the coach's clicks.

Run:
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.click_sample_report \
        --game-id mrhvbvwi1gjpn

Reads `clicks.jsonl` from the render directory, projects every click through the
game's homography, and prints the per-player table plus the half-to-half drift.
Emits POSITION metrics only -- never distance, speed or sprints, which a sample
of clicks cannot support (see post_game/click_samples.py).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--min-clicks", type=int, default=8)
    args = ap.parse_args()

    from post_game import firestore_io
    from post_game.click_samples import compute_click_stats, load_clicks

    root = Path(args.dir or f"tracking/outputs/click_samples/{args.game_id}")
    clicks = load_clicks(root / "clicks.jsonl")
    if not clicks:
        raise SystemExit(f"no clicks in {root}")

    idx = json.loads((root / "index.json").read_text())
    game = firestore_io.get_game(args.game_id)
    cal = firestore_io.get_game_calibration(args.game_id)
    roster = {p.id: (p.jersey_number, p.name) for p in firestore_io.get_roster()}

    h1 = float(getattr(game, "video_offset_h1_kickoff_s", 0.0) or 0.0)
    h2 = float(getattr(game, "video_offset_h2_kickoff_s", 0.0) or 0.0)

    # --- coverage ---------------------------------------------------------
    seen = {round(float(c["video_time_s"]), 2) for c in clicks}
    h1_frames = [f for f in idx["frames"] if f["video_time_s"] < h2]
    h2_frames = [f for f in idx["frames"] if f["video_time_s"] >= h2]
    done = lambda fs: sum(  # noqa: E731
        1 for f in fs if round(float(f["video_time_s"]), 2) in seen)
    n_h1 = sum(1 for c in clicks if c["video_time_s"] < h2)
    print(f"clicks {len(clicks)}   frames {len(seen)}/{len(idx['frames'])}")
    print(f"  H1: {n_h1:4d} clicks over {done(h1_frames)}/{len(h1_frames)} frames")
    print(f"  H2: {len(clicks)-n_h1:4d} clicks over {done(h2_frames)}/{len(h2_frames)} frames")

    periods = [(h1, h2 - 1), (h2, 1e9)]

    def period_of(t: float) -> int:
        for i, (a, b) in enumerate(periods, start=1):
            if a <= t <= b:
                return i
        return 1

    # Teams switch ends at half time, so H2 must be mirrored into H1's frame.
    # Without this a player who never moved reads as having crossed the pitch --
    # the first version of this report claimed a +28.9 m drift for a defender who
    # had simply changed ends.
    from post_game.click_orientation import our_net_at_x0_from_keeper
    from post_game.click_samples import to_field
    net = our_net_at_x0_from_keeper(
        to_field(clicks, cal), game.gk_player_id, float(cal.length_m), period_of)
    if net:
        print(f"  orientation: our net at x=0 by half -> {net}")
    else:
        print("  orientation: UNKNOWN (keeper not sampled in a half) — "
              "half-to-half drift withheld")

    stats, rep = compute_click_stats(clicks, cal, periods=periods,
                                     our_net_at_x0=net,
                                     min_clicks=args.min_clicks)
    extra = {k: v for k, v in rep.items()
             if k in ("clamped_far_touchline", "dropped_off_pitch")}
    print(f"  projection: {extra}")
    if rep["under_sampled"]:
        u = ", ".join(f"{roster.get(x['player_id'], ('?', '?'))[1]}({x['n_clicks']})"
                      for x in rep["under_sampled"])
        print(f"  under {args.min_clicks} clicks, withheld: {u}")

    # --- per-player -------------------------------------------------------
    print(f"\n{'#':>3} {'name':17s} {'n':>3} {'depth':>6} {'width':>6} "
          f"{'p10-p90 depth':>13} {'def/mid/att':>12} {'spr':>5}")
    print("-" * 76)
    for s in sorted(stats, key=lambda s: s.avg_depth_m):
        num, nm = roster.get(s.player_id, ("?", s.player_id))
        print(f"{num:>3} {nm:17s} {s.n_clicks:>3} {s.avg_depth_m:6.1f} "
              f"{s.avg_width_m:6.1f} {s.p10_depth_m:6.1f}-{s.p90_depth_m:5.1f} "
              f"{s.pct_def_third:4.0f}/{s.pct_mid_third:3.0f}/{s.pct_att_third:3.0f} "
              f"{s.spread_score:5.2f}")

    # --- half-to-half drift ----------------------------------------------
    both = [s for s in stats if "1" in s.by_half and "2" in s.by_half]
    print(f"\nHALF-TO-HALF DRIFT ({len(both)} player(s) with both halves)")
    if not net:
        print("  WITHHELD — the halves are not oriented, so any drift here would "
              "be the end change rather than the player moving.")
    elif both:
        print(f"{'#':>3} {'name':17s} {'H1':>7} {'H2':>7} {'drift':>7}  "
              f"{'H1 w':>6} {'H2 w':>6}")
        for s in sorted(both, key=lambda s: s.avg_depth_m):
            num, nm = roster.get(s.player_id, ("?", s.player_id))
            a, b = s.by_half["1"], s.by_half["2"]
            print(f"{num:>3} {nm:17s} {a['avg_depth_m']:7.1f} {b['avg_depth_m']:7.1f} "
                  f"{b['avg_depth_m']-a['avg_depth_m']:+7.1f}  "
                  f"{a['avg_width_m']:6.1f} {b['avg_width_m']:6.1f}")
        print("\n  positive drift = further up the pitch in H2")
    else:
        print("  (needs enough clicks in BOTH halves per player)")


if __name__ == "__main__":
    main()
