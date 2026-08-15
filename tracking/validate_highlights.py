#!/usr/bin/env python3
"""Validate a rendered highlights reel against the two fixes it should carry.

1. Clip windows are ASYMMETRIC: ~25 s of lead before the coach's tap, ~10 s after.
   The old symmetric +-15 s window left "only 2-3 seconds before they score".
2. Every rendered segment carries `reel_start_s` + `period` + `clock_s`, which is
   what lets the PWA scorebug show the right minute anywhere in the reel rather
   than latching on the last event before the playhead.

Then it simulates the scorebug across the whole reel and prints the minute at each
clip boundary, so the jumps can be eyeballed against the events.

Run:
    GOOGLE_APPLICATION_CREDENTIALS=... FIRESTORE_PROJECT_ID=lasalle-stompers \
    PYTHONPATH=. .venv-post-game/bin/python -m tracking.validate_highlights \
        --game-id mri01pvelv46d
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = "PASS", "FAIL"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    args = ap.parse_args()

    from post_game import firestore_io
    from post_game.identity import period_clock_to_video_time_factory
    from post_game.tv_view import (AUTO_HIGHLIGHT_EVENT_TYPES,
                                   AUTO_HIGHLIGHT_POST_S, AUTO_HIGHLIGHT_PRE_S)

    game = firestore_io.get_game(args.game_id)
    doc = firestore_io.read_analytics(args.game_id) or {}
    to_video = period_clock_to_video_time_factory(game)
    results: list[tuple[str, str, str]] = []

    # ---- (1) segment count + duration -----------------------------------
    hl = doc.get("auto_highlights") or {}
    segs = hl.get("segments") or []
    dur = hl.get("duration_s")
    print(f"auto_highlights: {len(segs)} segments, duration "
          f"{dur if dur is None else f'{dur:.1f}s ({dur/60:.1f} min)'}")
    print(f"url: {doc.get('auto_highlights_url')}\n")
    results.append(("1. reel rendered with segments",
                    PASS if segs and dur else FAIL,
                    f"{len(segs)} segments, {dur}s"))

    # ---- (2) every segment carries the clock index ----------------------
    missing = [i for i, s in enumerate(segs)
               if s.get("reel_start_s") is None or s.get("clock_s") is None
               or s.get("period") is None]
    results.append(("2. every segment has reel_start_s/period/clock_s",
                    PASS if segs and not missing else FAIL,
                    "all present" if not missing else f"missing on {missing}"))

    print(f"{'#':>3} {'reel@':>8} {'src start':>10} {'src end':>9} {'len':>6} "
          f"{'per':>4} {'clock':>8}  game min")
    for i, s in enumerate(segs):
        a, b = s.get("start_s"), s.get("end_s")
        rs, per, ck = s.get("reel_start_s"), s.get("period"), s.get("clock_s")
        mins = (f"{int((ck or 0)//60)+1 + (game.half_length_min if per == 2 else 0)}'"
                if ck is not None else "?")
        print(f"{i:>3} {rs:>8.1f} {a:>10.1f} {b:>9.1f} {b-a:>6.1f} "
              f"{per:>4} {ck:>8.1f}  {mins}")

    # ---- (3) lead-up / tail per highlight event -------------------------
    evs = [e for e in sorted(game.events, key=lambda e: (e.period, e.elapsed))
           if e.type in AUTO_HIGHLIGHT_EVENT_TYPES]
    print(f"\n{len(evs)} highlight events — lead-up actually rendered:")
    print(f"{'event':<10} {'per':>4} {'clock':>7} {'video t':>9} {'lead':>7} "
          f"{'tail':>7}  covered")
    bad_lead = []
    goals_checked = 0
    for e in evs:
        t = to_video(e.period, e.elapsed)
        seg = next((s for s in segs
                    if s["start_s"] <= t <= s["end_s"]), None)
        if seg is None:
            print(f"{e.type:<10} {e.period:>4} {e.elapsed:>7.0f} {t:>9.1f} "
                  f"{'—':>7} {'—':>7}  NOT IN REEL")
            bad_lead.append((e.type, "not in reel"))
            continue
        lead, tail = t - seg["start_s"], seg["end_s"] - t
        # A merged window can give a LONGER lead than the nominal pre-roll; only
        # a SHORTER one is a defect.
        ok = lead >= AUTO_HIGHLIGHT_PRE_S - 1.0
        if e.type == "GOAL":
            goals_checked += 1
        if not ok:
            bad_lead.append((e.type, f"lead {lead:.1f}s"))
        print(f"{e.type:<10} {e.period:>4} {e.elapsed:>7.0f} {t:>9.1f} "
              f"{lead:>6.1f}s {tail:>6.1f}s  {'ok' if ok else 'SHORT'}")

    results.append((f"3. every event has >={AUTO_HIGHLIGHT_PRE_S:.0f}s lead-up "
                    f"({goals_checked} goals)",
                    PASS if not bad_lead else FAIL,
                    "all ok" if not bad_lead else str(bad_lead[:4])))

    # ---- (4) scorebug minute across the reel ----------------------------
    half = game.half_length_min

    def scorebug(now: float):
        found = None
        for s in segs:
            ln = max(0.0, s["end_s"] - s["start_s"])
            if now + 0.01 >= s["reel_start_s"] and now <= s["reel_start_s"] + ln:
                found = s
                break
            if now > s["reel_start_s"]:
                found = s
        if not found:
            return None
        el = (found.get("clock_s") or 0) + max(0.0, now - found["reel_start_s"])
        per = found.get("period") or 1
        return per, max(1, int(el // 60) + 1) + (half if per == 2 else 0)

    print("\nscorebug minute across the reel (probing each clip):")
    prev = None
    monotonic_within = True
    for s in segs:
        ln = max(0.0, s["end_s"] - s["start_s"])
        for frac, lbl in ((0.02, "start"), (0.98, "end")):
            now = s["reel_start_s"] + ln * frac
            got = scorebug(now)
            if got is None:
                continue
            per, mn = got
            flag = ""
            if prev and lbl == "start":
                # A cut may jump either way in game time; only a WITHIN-clip
                # regression would be wrong.
                flag = "  <- cut"
            print(f"  reel {now:>7.1f}s  {lbl:<5}  "
                  f"{'2ND' if per == 2 else '1ST'} {mn}'{flag}")
            if lbl == "end" and prev is not None and per == prev[0] and mn < prev[1]:
                monotonic_within = False
            prev = (per, mn)
    results.append(("4. scorebug advances within each clip",
                    PASS if monotonic_within else FAIL,
                    "no within-clip regression"))

    print("\n" + "=" * 62)
    for name, verdict, detail in results:
        print(f"{verdict:<5} {name}\n      {detail}")
    print("=" * 62)
    raise SystemExit(0 if all(v == PASS for _, v, _ in results) else 1)


if __name__ == "__main__":
    main()
