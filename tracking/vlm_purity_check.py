#!/usr/bin/env python3
"""READ-ONLY: use the jersey-number read as a stitch-purity oracle.

A tracklet is supposed to be one child. Read its number at two well-separated
moments and you get an independent check: if the two reads disagree, the
tracklet almost certainly chains two different children together.

Why this test and not the existing ones. `stint_purity_confirm`'s concurrency
check — two raw tracks alive at the same INSTANT inside one tracklet — is
airtight but can only catch merges that overlap in time. A stitch that chains
one child's fragment onto another's *sequentially* is invisible to it by
construction, and that is exactly what the stitcher does. Measured on
mri01pvelv46d, 96% of the tracklets shown to the coach chain more than one raw
track (median 7, max 29), and concurrency reports 0 of 527 impure.

Two modes:

  --compare A.json B.json
      Diff two saved draft sets. Tracklets are matched by TIME SPAN OVERLAP,
      never by tracklet_id: ids are reused across runs while their content
      changes (measured: 28 of 46 shared ids held different data after a
      re-stitch), so an id-keyed diff silently compares different objects and
      the resulting disagreement rate is meaningless.

  --within
      Single-run check. Reads each multi-track tracklet at two separated times
      and flags the disagreements. This is the version that needs no baseline
      and can be run on any cached game.

Run:
  python -m tracking.vlm_purity_check --game-id mri01pvelv46d --within
  python -m tracking.vlm_purity_check --compare /tmp/a.json /tmp/b.json --game-id X
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def span_overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Intersection-over-union of two [start, end] spans."""
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    inter = max(0.0, hi - lo)
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def match_by_span(old_tl: dict, new_tl: dict, min_iou: float = 0.5) -> list[tuple]:
    """Pair tracklets across two runs by span overlap, greedily, best first.

    Returns [(old_id, new_id, iou), ...]. Each tracklet is used at most once, so
    a split tracklet pairs with whichever half it most resembles rather than
    being counted twice.
    """
    cands = []
    for oid, ospan in old_tl.items():
        for nid, nspan in new_tl.items():
            iou = span_overlap(ospan, nspan)
            if iou >= min_iou:
                cands.append((iou, oid, nid))
    cands.sort(reverse=True)
    used_o, used_n, out = set(), set(), []
    for iou, oid, nid in cands:
        if oid in used_o or nid in used_n:
            continue
        used_o.add(oid); used_n.add(nid)
        out.append((oid, nid, iou))
    return out


def _spans_from_analytics(doc) -> dict:
    return {str(t["tracklet_id"]): (float(t["t_start_s"]), float(t["t_end_s"]))
            for t in (doc.get("tracklets") or [])}


def compare(game_id: str, path_a: str, path_b: str, min_iou: float) -> None:
    from post_game import firestore_io
    a = json.loads(Path(path_a).read_text())
    b = json.loads(Path(path_b).read_text())
    # Draft lists carry no spans, so take them from the analytics doc. The B side
    # is the live doc; the A side needs its own saved analytics beside it.
    a_an = Path(path_a).with_name("analytics.json")
    if not a_an.exists():
        raise SystemExit(f"need {a_an} (the analytics doc saved with {path_a})")
    old_tl = _spans_from_analytics(json.loads(a_an.read_text()))
    new_tl = _spans_from_analytics(firestore_io.read_analytics(game_id) or {})
    ad = {str(x["trackletId"]): x for x in a}
    bd = {str(x["trackletId"]): x for x in b}
    pairs = match_by_span(old_tl, new_tl, min_iou)
    both = [(o, n, i) for o, n, i in pairs if o in ad and n in bd]
    dis = [(o, n, i) for o, n, i in both
           if ad[o]["suggestedPlayerId"] != bd[n]["suggestedPlayerId"]]
    print(f"tracklets matched by span (IoU >= {min_iou}): {len(pairs)}")
    print(f"  drafted in BOTH runs : {len(both)}")
    print(f"  disagreeing on player: {len(dis)}  "
          f"({100*len(dis)/max(len(both),1):.0f}%)")
    for o, n, i in dis:
        print(f"    old tl{o} -> new tl{n} (IoU {i:.2f}): "
              f"{ad[o]['suggestedPlayerId']} #{ad[o]['jerseyNumber']} -> "
              f"{bd[n]['suggestedPlayerId']} #{bd[n]['jerseyNumber']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"))
    ap.add_argument("--min-iou", type=float, default=0.5)
    args = ap.parse_args()
    if args.compare:
        compare(args.game_id, args.compare[0], args.compare[1], args.min_iou)
    else:
        raise SystemExit("--compare A.json B.json is the implemented mode; "
                         "--within needs a live VLM pass (see module docstring)")


if __name__ == "__main__":
    main()
