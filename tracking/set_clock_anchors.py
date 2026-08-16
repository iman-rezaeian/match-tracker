#!/usr/bin/env python3
"""Write measured clock->video anchors for a game, and show what they change.

An anchor is a time the coach read off the SOURCE FILE for an event whose game
clock we already know. Two per period pin both the offset and the rate.

Why this is needed at all — Caboto (mri01pvelv46d), 2026-08-16. Its stored
`videoOffsetH1KickoffS` was 0.0 while its same-day sibling had 40.9, because
"Confirm 1st-half kickoff" with an empty box wrote 0.0 as a deliberate confirmation.
Every highlight clip was then cut around a time 7-33 s before the goal it existed to
show, and the scorebug credited each goal early. The error GREW through the half, so
no single offset repairs it: the coach's three anchors fit
`video = 4.1 + 1.0197 * elapsed`.

⚠ Neither clock is broken. `elapsed` tracks wallclock-since-startedAt within 1 s
across all 55 first-half events, and the video is continuous 29.97 fps with zero
packet gaps. The phone's clock and the camera's simply disagree by ~2%, and only
measured anchors resolve that.

Usage (dry run prints the effect and writes nothing):
    ... python -m tracking.set_clock_anchors --game-id mri01pvelv46d \
        --anchor 1:148:155 --anchor 1:598:624 --anchor 1:1467:1500
    ... --write        to persist
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--anchor", action="append", default=[],
                    metavar="PERIOD:ELAPSED_S:VIDEO_S",
                    help="repeatable, e.g. 1:598:624")
    ap.add_argument("--write", action="store_true", help="persist (default: dry run)")
    args = ap.parse_args()

    from post_game import firestore_io
    from post_game.identity import (_fit_anchors,
                                    period_clock_to_video_time_factory)

    anchors: dict[int, list[tuple[float, float]]] = {}
    for spec in args.anchor:
        per, el, vid = spec.split(":")
        anchors.setdefault(int(per), []).append((float(el), float(vid)))
    if not anchors:
        raise SystemExit("no --anchor given")

    game = firestore_io.get_game(args.game_id)
    before = period_clock_to_video_time_factory(game)

    print(f"{args.game_id}  vs {game.opponent}")
    print(f"stored H1 offset: {game.video_offset_h1_kickoff_s:.1f} s "
          f"(confirmed={game.video_offset_h1_confirmed})\n")

    for per, pts in sorted(anchors.items()):
        off, rate = _fit_anchors(sorted(pts))
        print(f"period {per}: {len(pts)} anchors -> "
              f"video = {off:.2f} + {rate:.5f} * elapsed  "
              f"({100*(rate-1):+.2f}% rate)")
        for e, v in sorted(pts):
            pred = off + rate * e
            print(f"    elapsed {e:>6.0f}  coach read {v:>7.1f}  "
                  f"fit {pred:>7.1f}  residual {v-pred:+.1f}")

    print("\neffect on this game's GOAL events:")
    goals = [e for e in sorted(game.events, key=lambda x: (x.period, x.elapsed))
             if e.type in ("GOAL", "OPP_GOAL")]
    fits = {p: _fit_anchors(sorted(v)) for p, v in anchors.items()}
    print(f"{'type':<10}{'per':>4}{'elapsed':>9}{'was':>9}{'now':>9}{'shift':>8}")
    for e in goals:
        was = before(e.period, e.elapsed)
        fit = fits.get(e.period)
        now = (fit[0] + fit[1] * e.elapsed) if fit else was
        print(f"{e.type:<10}{e.period:>4}{e.elapsed:>9}{was:>9.1f}{now:>9.1f}"
              f"{now-was:>+8.1f}")

    if args.write:
        firestore_io.set_video_clock_anchors(args.game_id, anchors)
        print("\nWRITTEN to videoClockAnchors (game.events untouched)")
    else:
        print("\ndry run — pass --write to persist")


if __name__ == "__main__":
    main()
