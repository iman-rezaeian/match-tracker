#!/usr/bin/env python3
"""Are the BENCH-TIME leaks a SUB-tap timing problem, or an identity problem?

The pipeline reported, on the Caboto game:

    BENCH-TIME leak: p_duncan 43%, p_rezaeian 21%, p_hassoun 20%
    identity CONFLICTS: p_bowser 454.6s of provably-impossible time

Those have very different causes and very different fixes:

  * SUB-TAP TIMING — the player really was on the pitch, but the coach's SUB tap
    landed late/early, so his on-field window is wrong and genuine detections get
    thrown away as "off-window". Fixable from the data (sub_correct.py already
    exists for exactly this).
  * IDENTITY — the tracks credited to him belong to another child, so the
    detections are correctly rejected and the window is fine.

The coach's CLICKS settle it. Each click is him naming a player at a known second,
so for any click we know that player WAS on the pitch then. So:

    click inside his SUB window   -> consistent
    click OUTSIDE his SUB window  -> the WINDOW is wrong (he was playing, the tap
                                     says he wasn't) => SUB-TAP TIMING

⚠ This only tests instants the coach clicked (~40 frames), and only for players he
clicked. It cannot prove identity is fine; it can only show whether the windows
disagree with ground truth.

RESULT (Caboto game, 2026-08-15): only 6% of 275 clicks fall outside the clicked
player's window, so THE WINDOWS ARE ESSENTIALLY RIGHT and the pipeline's
"BENCH-TIME leak: p_duncan=43%" is NOT a tap-timing problem. The two numbers count
different things:

    43%  of TRACKED DETECTIONS credited to Duncan fall outside his window
          -> the tracker attached his name to another child's track  => IDENTITY
     6%  of the coach's CLICKS fall outside a window
          -> the windows themselves are close to correct

Corroborating: Duncan's window is 34 min but only 214 s are tracked (10%
coverage), so his attributed detections are dominated by whatever the stitcher
merged in. The `offwindow` filter is therefore doing its job -- it is REJECTING
another child's data, not discarding Duncan's.

⚠ One genuine window defect did show up: at video t=2664 s the coach tagged a full,
legitimate seven, but four of them differ from the seven the SUB log has on the
pitch. That instant sits 5 s after a SUB tap in a rotation whose taps span 80-117 s
(the run logged "sub-slack: spread median 80s max 117s"), so a multi-player
rotation logged over ~2 minutes leaves the window set briefly wrong. That is what
`post_game/sub_correct.py` exists to fix.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", default="mri01pvelv46d")
    ap.add_argument("--clicks", default=None)
    args = ap.parse_args()

    from post_game import firestore_io
    from post_game.identity import (_onfield_intervals,
                                    period_clock_to_video_time_factory)

    game = firestore_io.get_game(args.game_id)
    clock_to_video = period_clock_to_video_time_factory(game)
    roster = {p.id: (p.name or p.id) for p in firestore_io.get_roster()}

    # The player windows the pipeline used, in VIDEO seconds.
    windows = _onfield_intervals(
        game.starting_lineup or [], game.events, clock_to_video)

    root = Path(args.clicks or f"tracking/outputs/click_samples/{args.game_id}")
    clicks = [json.loads(l) for l in (root / "clicks.jsonl").open() if l.strip()]
    print(f"{len(clicks)} clicks over "
          f"{len({round(float(c['video_time_s']), 2) for c in clicks})} frames\n")

    by_player: dict[str, list[float]] = defaultdict(list)
    for c in clicks:
        by_player[str(c["player_id"])].append(float(c["video_time_s"]))

    print(f"{'player':<18}{'clicks':>7}{'in win':>8}{'OUTSIDE':>9}{'out%':>7}   "
          f"window (video s)")
    rows = []
    for pid, times in sorted(by_player.items()):
        iv = windows.get(pid) or windows.get(str(pid)) or []
        inside = 0
        outside_times = []
        for t in times:
            if any(a <= t <= b for a, b in iv):
                inside += 1
            else:
                outside_times.append(t)
        pct = 100.0 * len(outside_times) / max(1, len(times))
        span = ", ".join(f"{a:.0f}-{b:.0f}" for a, b in iv[:3]) or "(none)"
        rows.append((pid, len(times), inside, len(outside_times), pct,
                     span, outside_times))
        name = roster.get(pid, pid).split()[0]
        print(f"{name:<18}{len(times):>7}{inside:>8}{len(outside_times):>9}"
              f"{pct:>6.0f}%   {span}")

    worst = sorted(rows, key=lambda r: -r[4])[:4]
    print("\n--- players whose clicks most disagree with their SUB window ---")
    for pid, n, ins, out, pct, span, outs in worst:
        if not out:
            continue
        name = roster.get(pid, pid)
        print(f"\n{name}  ({out}/{n} clicks outside his window, {pct:.0f}%)")
        print(f"  window: {span}")
        print(f"  clicked at: {', '.join(f'{t:.0f}s' for t in sorted(outs)[:12])}")
        # How far outside? A small gap = a late/early tap. A huge gap = identity.
        gaps = []
        iv = windows.get(pid) or []
        for t in outs:
            if not iv:
                continue
            gaps.append(min(min(abs(t - a), abs(t - b)) for a, b in iv))
        if gaps:
            gaps.sort()
            print(f"  distance to the nearest window edge: "
                  f"min {gaps[0]:.0f}s  median {gaps[len(gaps)//2]:.0f}s  "
                  f"max {gaps[-1]:.0f}s")
            print("  => LATE/EARLY SUB TAP" if gaps[len(gaps) // 2] < 180
                  else "  => too far out to be a tap-timing slip")

    tot = sum(r[1] for r in rows)
    tot_out = sum(r[3] for r in rows)
    print(f"\nOVERALL: {tot_out}/{tot} clicks ({100*tot_out/max(1,tot):.0f}%) fall "
          f"outside the clicked player's own on-field window.")
    print("Every one of those is a moment the coach SAW the player on the pitch "
          "while the SUB log said he was on the bench.")


if __name__ == "__main__":
    main()
