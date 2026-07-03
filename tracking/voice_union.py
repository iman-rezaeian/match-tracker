#!/usr/bin/env python3
"""Phase 3.6 union — merge voice-extracted draft events with the coach's live log.

Voice narration and the live scorebug log are COMPLEMENTARY (measured across 3
games): the coach taps structured events (goals/shots/subs) into the log but
NARRATES possession, so voice mostly ADDS granular events (ball-wins, corners,
fouls) the log lacks — and occasionally re-states a logged one. This step unions
the two so the confirm queue shows the coach what voice ADDS, without duplicating
what he already logged.

Per the capture plan: "merge voice drafts into matching live events (same type
±30s), not duplicate." So each voice draft is classified:
  * MATCHED  — a live event of the same type within ±window in the same period
               (dedup; if the live event lacks a player and voice has one, the
               voice player is offered as an enrichment)
  * NEW      — no matching live event → surface as a draft for the coach to confirm

ALIGNMENT: the auto-start recorder writes one audio segment per half, so the
concat timeline maps to (period, game-clock elapsed) at the segment boundaries —
and those boundaries match the live clock to the second (Win City: seg1=1439s,
live P1 max elapsed=1439s). Boundaries auto-derive from the R2 segments' durations
(cumulative), or pass --boundaries. Phone-memo (single segment) games need a
different anchor (kickoff / live-event cross-correlation) — not handled here.

Read-only; emits a union JSON. Firestore-write + PWA confirm-queue surfacing are
the follow-on. Run:
    .venv-post-game/bin/python -m tracking.voice_union \
        --events tracking/outputs/voice_clean/game_wincity.events.json \
        --game-id mqnxdtyven0g2
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def _segment_boundaries(game_id: str) -> list[float]:
    """Cumulative concat-time (s) at which each half's audio ENDS, from the R2
    per-half segments' durations. [end_of_h1, end_of_h1+end_of_h2, ...]."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from post_game import firestore_io
    s3 = firestore_io._r2_client()
    bucket = os.environ["R2_BUCKET"]
    keys = sorted(o["Key"] for o in
                  s3.list_objects_v2(Bucket=bucket, Prefix=f"voice_{game_id}_").get("Contents", []))
    tmp = Path(tempfile.mkdtemp())
    cum, acc = [], 0.0
    for k in keys:
        d = tmp / Path(k).name
        s3.download_file(bucket, k, str(d))
        dur = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(d)], text=True).strip())
        acc += dur
        cum.append(acc)
    return cum


def _concat_to_clock(t: float, boundaries: list[float]) -> tuple[int, float]:
    """Map a concat-audio second to (period, elapsed-in-period seconds)."""
    prev = 0.0
    for i, b in enumerate(boundaries):
        if t <= b:
            return i + 1, t - prev
        prev = b
    # past the last boundary → last period (shouldn't happen if boundaries cover it)
    return len(boundaries), t - (boundaries[-2] if len(boundaries) > 1 else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events", required=True, help="voice_extract .events.json")
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--boundaries", help="comma-sep cumulative half-end concat-seconds "
                                         "(default: auto from R2 segment durations)")
    ap.add_argument("--window-s", type=float, default=30.0, help="same-type match window")
    ap.add_argument("--write", action="store_true",
                    help="write the drafts to the game doc's voiceDrafts field "
                         "(PWA confirm queue reads them). Additive + reversible.")
    args = ap.parse_args()
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from post_game import firestore_io

    voice = json.loads(Path(args.events).read_text())["events"]
    g = firestore_io.get_game(args.game_id)
    roster = {r.id: f"#{getattr(r,'jersey_number','?')} {r.name}" for r in firestore_io.get_roster()}
    live = [{"type": (e.type or "").upper(), "period": int(e.period or 1),
             "elapsed": float(e.elapsed or 0), "player_id": e.player_id, "matched": False}
            for e in (g.events or [])]

    boundaries = ([float(x) for x in args.boundaries.split(",")] if args.boundaries
                  else _segment_boundaries(args.game_id))
    print(f"segment boundaries (concat s): {[round(b) for b in boundaries]}")

    new_drafts, enrichments, dups = [], [], []
    for v in voice:
        period, elapsed = _concat_to_clock(float(v["videoTimeS"]), boundaries)
        vtype = (v.get("type") or "").upper()
        # find an unmatched live event, same type + period, within the window
        cand = [e for e in live if not e["matched"] and e["type"] == vtype
                and e["period"] == period and abs(e["elapsed"] - elapsed) <= args.window_s]
        rec = {"type": vtype, "period": period, "elapsed": round(elapsed),
               "player_id": v.get("player_id"), "player_first_name": v.get("player_first_name"),
               "confidence": v.get("confidence"), "quote": v.get("quote"),
               "source": "voice_draft"}
        if cand:
            e = min(cand, key=lambda e: abs(e["elapsed"] - elapsed))
            e["matched"] = True
            if not e["player_id"] and rec["player_id"]:
                enrichments.append({**rec, "enriches_live_at": round(e["elapsed"])})
            else:
                dups.append(rec)
        else:
            new_drafts.append(rec)

    def _fmt(r):
        who = roster.get(r["player_id"]) or (r["player_first_name"] or "—")
        return f"  P{r['period']} {r['elapsed']:>4}s  {r['type']:<12} {who:<20} c={r['confidence']}  “{(r['quote'] or '')[:44]}”"

    from collections import Counter
    print(f"\nvoice events: {len(voice)}  |  live events: {len(live)}")
    print(f"→ NEW drafts (voice adds, not in live log): {len(new_drafts)}  {dict(Counter(r['type'] for r in new_drafts))}")
    for r in sorted(new_drafts, key=lambda r: (r["period"], r["elapsed"])):
        print(_fmt(r))
    print(f"\n→ ENRICH live events lacking a player: {len(enrichments)}")
    for r in enrichments:
        print(_fmt(r))
    print(f"\n→ DUP of an already-complete live event (dropped): {len(dups)}  {dict(Counter(r['type'] for r in dups))}")

    out = Path(args.events).with_suffix("").as_posix() + ".union.json"
    Path(out).write_text(json.dumps({
        "game_id": args.game_id, "boundaries_s": boundaries, "window_s": args.window_s,
        "new_drafts": new_drafts, "enrichments": enrichments, "dups": dups,
    }, indent=2))
    print(f"\nwritten: {out}\n(new_drafts + enrichments are the confirm-queue candidates)")

    if args.write:
        # Shape for the PWA: camelCase (matches event/voiceSegments fields) +
        # deterministic id per (period,elapsed,type) so re-runs replace, not dup.
        def _shape(r, kind):
            return {
                "id": f"vd_{r['period']}_{r['elapsed']}_{r['type']}",
                "type": r["type"], "period": r["period"], "elapsed": r["elapsed"],
                "playerId": r.get("player_id"),
                "playerName": r.get("player_first_name") or "",
                "confidence": r.get("confidence"), "quote": r.get("quote"),
                "kind": kind, "source": "voice_draft",
            }
        drafts = [_shape(r, "new") for r in new_drafts] + [_shape(r, "enrich") for r in enrichments]
        firestore_io.write_voice_drafts(args.game_id, drafts)
        print(f"→ wrote {len(drafts)} voiceDrafts to game {args.game_id} "
              f"(PWA confirm queue). Reversible: field can be cleared.")


if __name__ == "__main__":
    main()
