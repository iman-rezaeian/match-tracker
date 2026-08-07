#!/usr/bin/env python3
"""READ-ONLY: score the VLM jersey-number drafts against blind ground truth.

Everything measured so far about draft reliability is run-to-run CONSISTENCY:
re-reading the same game gives a different player on 46% of span-matched
tracklets, several at IoU > 0.94 with both reads at 0.97 confidence. That bounds
reliability but cannot say which read was right, or whether either was. Only a
label made by eye, blind to the pipeline, can.

`tracking/player_gt_app.py` already collects exactly that — it shows each
tracklet's crop strip WITHOUT the pipeline's guess and writes
`tracking/labels/<game>_player_gt/gt.csv`. `player_gt_eval.py` then scores the
ASSIGNER against it. Nothing scored the DRAFTS, which is what the coach actually
taps, so this fills that gap using the same labels.

The number that matters is precision BY CONFIDENCE BAND. A draft chip says 0.97;
the coach needs to know whether 0.97 means 97% right, or something far worse. If
high-confidence drafts are reliable and low ones are not, the fix is a threshold.
If confidence carries no signal at all, the chips must be presented as guesses to
check rather than answers to accept.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.vlm_draft_eval --game-id mri01pvelv46d
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

# Sentinels the labelling app writes in `label` when the tracklet is not one of
# our named children. A draft on any of these is wrong by definition.
NON_PLAYER = {"__referee__", "__opponent__", "__opp__", "__other__",
              "__not_a_player__", "__cant_tell__", "__unknown__"}


def load_gt(game_id: str, labels_dir: Path) -> dict[str, dict]:
    """{tracklet_id: {"true": player_id|None, "label": str, "minutes": float}}."""
    path = labels_dir / f"{game_id}_player_gt" / "gt.csv"
    if not path.exists():
        raise SystemExit(
            f"No ground truth at {path}.\n"
            f"Label some tracklets first:\n"
            f"    set -a; source .env; set +a\n"
            f"    streamlit run tracking/player_gt_app.py\n"
            f"It shows each tracklet BLIND to the pipeline's guess, which is the\n"
            f"whole point — a label made while looking at the draft is not evidence.")
    out: dict[str, dict] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            if row.get("game_id") and row["game_id"] != game_id:
                continue
            out[str(row["tracklet_id"])] = {
                "true": (row.get("true_player_id") or "").strip() or None,
                "label": (row.get("label") or "").strip(),
                "minutes": float(row.get("minutes") or 0.0),
            }
    return out


def score(drafts: list[dict], gt: dict[str, dict]) -> dict:
    """Precision overall, by confidence band, and time-weighted."""
    bands = [(0.9, 1.01), (0.8, 0.9), (0.6, 0.8), (0.0, 0.6)]
    per_band: dict[tuple, list] = defaultdict(list)
    rows = []
    for d in drafts:
        tid = str(d.get("trackletId"))
        g = gt.get(tid)
        if not g:
            continue                      # unlabelled: no opinion
        conf = float(d.get("confidence") or 0.0)
        pred = d.get("suggestedPlayerId")
        if g["label"] in NON_PLAYER or g["true"] is None:
            correct = False               # drafted a non-player / unlabelled child
        else:
            correct = (pred == g["true"])
        rows.append((tid, pred, g["true"], g["label"], conf, correct,
                     float(d.get("minutes") or g["minutes"])))
        for lo, hi in bands:
            if lo <= conf < hi:
                per_band[(lo, hi)].append(correct)
                break
    return {"rows": rows, "bands": per_band, "order": bands}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--labels-dir", default="tracking/labels")
    ap.add_argument("--drafts-json", help="score a saved draft set instead of the live doc")
    args = ap.parse_args()

    gt = load_gt(args.game_id, Path(args.labels_dir))
    if args.drafts_json:
        import json
        drafts = json.loads(Path(args.drafts_json).read_text())
    else:
        from post_game import firestore_io
        snap = (firestore_io._team_doc().collection("games")
                .document(args.game_id).get().to_dict() or {})
        drafts = snap.get("identityDrafts") or []

    res = score(drafts, gt)
    rows = res["rows"]
    if not rows:
        raise SystemExit(
            f"{len(drafts)} drafts and {len(gt)} labels, but none of the drafted "
            f"tracklets are labelled. Label the DRAFTED ones — the app sorts by "
            f"minutes, so work down from the top.")

    n = len(rows)
    ok = sum(1 for r in rows if r[5])
    tmin = sum(r[6] for r in rows)
    tok = sum(r[6] for r in rows if r[5])
    print(f"drafts scored against blind GT: {n} of {len(drafts)} "
          f"({len(gt)} tracklets labelled)\n")
    print(f"  precision (by tracklet): {ok}/{n} = {100*ok/n:.0f}%")
    print(f"  precision (by minutes) : {tok:.0f}/{tmin:.0f} = {100*tok/max(tmin,1e-9):.0f}%")
    print()
    print("  confidence band   n   correct   precision")
    for lo, hi in res["order"]:
        v = res["bands"].get((lo, hi))
        if not v:
            continue
        c = sum(v)
        print(f"   {lo:.1f}-{hi if hi<=1 else 1.0:.1f}        {len(v):3d}   {c:5d}     {100*c/len(v):5.0f}%")
    bad = [r for r in rows if not r[5]]
    if bad:
        print(f"\n  wrong drafts ({len(bad)}):")
        for tid, pred, true, label, conf, _, mins in sorted(bad, key=lambda r: -r[4])[:15]:
            truth = true or label or "?"
            print(f"    tl{tid:<7} said {str(pred):<13} truth {truth:<13} "
                  f"c={conf:.2f} {mins:.1f}min")


if __name__ == "__main__":
    main()
