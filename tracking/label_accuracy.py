"""Score the auto-assigner against the coach's hand labels — CORRECTNESS, not
repeatability.

Why this exists alongside half_split_r
--------------------------------------
Half-to-half r asks "is the output self-consistent". That is a proxy, and a
leaky one: welded tracklets appear in both halves by construction, so they
correlate for free and inflate it. This asks the direct question instead —
**when the assigner names a tracklet, is it the child the coach says it is?**

The coach's `identityOverrides` are the only per-tracklet ground truth we have
(W8: 145 labels — 57 real players, 88 `__opp__`/`__other__` sentinels). They are
WITHHELD from the assignment (overrides=None), so this is a fair test set.

Reported per run, and comparable across runs because tracklet ids mostly survive
a re-stitch (143/145 on W8 with the halftime split on):

  precision   of the tracklets we NAMED and the coach labelled, how many match
  recall      of the coach's real-player labels, how many we named correctly
  team purity of tracklets the coach marked NOT-ours, how many we wrongly named

That last one matters most for stats: naming an opponent as one of our children
injects a stranger's running into a real player's numbers.

Usage:
    .venv-post-game/bin/python -m tracking.label_accuracy --game-id mri01pvelv46d --label baseline
    .venv-post-game/bin/python -m tracking.label_accuracy --game-id mri01pvelv46d --label hts --halftime-split
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from post_game import config, firestore_io
from post_game.identity import half_windows, period_clock_to_video_time_factory
from post_game.identity_assign import assign_identities_v2

OUT_DIR = Path(__file__).resolve().parent / "outputs" / "identity_eval"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--label", required=True, help="stage-4 cache variant to score")
    ap.add_argument("--halftime-split", action="store_true",
                    help="score the .hts stage-4 cache (must already exist)")
    ap.add_argument("--gap-split", type=float, default=None)
    args = ap.parse_args()

    sfx = "" if args.gap_split is None else f".gs{args.gap_split:g}"
    if args.halftime_split:
        sfx += ".hts"
    s4_parquet = OUT_DIR / f"{args.game_id}.stage4{sfx}.parquet"
    s4_maps = OUT_DIR / f"{args.game_id}.stage4{sfx}.json"
    if not (s4_parquet.exists() and s4_maps.exists()):
        raise SystemExit(f"missing stage-4 cache {s4_parquet.name} — run "
                         f"tracking.half_split_r with the same flags first")

    game = firestore_io.get_game(args.game_id)
    roster = firestore_io.get_roster()
    cal = firestore_io.get_game_calibration(args.game_id)
    if cal is None:
        raise SystemExit("no calibration on the game doc")
    name_of = {r.id: r.name for r in roster}

    tracks_df = pd.read_parquet(s4_parquet)
    maps = json.loads(s4_maps.read_text())
    team_of_track = {int(k): v for k, v in maps["team_of_track"].items()}
    tracklet_of_track = {int(k): v for k, v in maps["tracklet_of_track"].items()}

    duration_s = float(tracks_df["time_s"].max()) + 1.0
    assignments = assign_identities_v2(
        tracks_df=tracks_df,
        tracklet_of_track=tracklet_of_track,
        team_of_track=team_of_track,
        events=game.events,
        roster=roster,
        starting_lineup=game.starting_lineup,
        gk_player_id=game.gk_player_id,
        period_clock_to_video_time=period_clock_to_video_time_factory(game),
        periods_video=half_windows(game, duration_s),
        field_length_m=cal.length_m,
        field_width_m=cal.width_m,
        overrides=None,          # labels withheld → fair test
        squad=game.squad,
    )

    # tracklet -> prediction, and tracklet -> real tracked seconds (so we can
    # weight by how much data each decision actually governs)
    pred: dict[int, dict] = {}
    for a in assignments:
        tl = (a.breakdown or {}).get("tracklet")
        if tl is not None:
            pred[int(tl)] = {"pid": a.player_id, "status": a.status,
                             "conf": round(a.confidence, 3)}
    dts = (tracks_df.sort_values(["track_id", "time_s"])
           .groupby("track_id")["time_s"].diff().dropna())
    dt_med = float(dts[dts > 0].median()) if len(dts) else 0.1
    tl_of = {int(k): int(v) for k, v in tracklet_of_track.items()}
    secs = (tracks_df.assign(_tl=tracks_df["track_id"].map(lambda t: tl_of.get(int(t), int(t))))
            .groupby("_tl").size() * dt_med)

    labels = {}
    for k, v in (game.identity_overrides or {}).items():
        try:
            labels[int(k)] = v
        except (TypeError, ValueError):
            continue

    named_hit = named_miss = 0          # named + labelled real: right / wrong
    recall_hit = recall_miss = 0        # coach says real player: named right / not
    purity_ok = purity_bad = 0          # coach says NOT ours: dropped / wrongly named
    missing = 0
    bad_rows = []
    w_hit = w_miss = 0.0                # same, weighted by tracked seconds

    for tl, lab in sorted(labels.items()):
        p = pred.get(tl)
        if p is None:
            missing += 1
            continue
        lab_pid = lab if (lab and not str(lab).startswith("__")) else None
        got = p["pid"]
        s = float(secs.get(tl, 0.0))
        if lab_pid is None:
            # coach says this tracklet is NOT one of our players
            if got is None:
                purity_ok += 1
            else:
                purity_bad += 1
                bad_rows.append((tl, str(lab), name_of.get(got, got), p["status"], s))
        else:
            if got == lab_pid:
                recall_hit += 1
                named_hit += 1
                w_hit += s
            else:
                recall_miss += 1
                if got is not None:
                    named_miss += 1
                    w_miss += s
                    bad_rows.append((tl, name_of.get(lab_pid, lab_pid),
                                     name_of.get(got, got), p["status"], s))

    n_named = named_hit + named_miss
    prec = named_hit / n_named if n_named else float("nan")
    rec = recall_hit / (recall_hit + recall_miss) if (recall_hit + recall_miss) else float("nan")
    pur = purity_ok / (purity_ok + purity_bad) if (purity_ok + purity_bad) else float("nan")
    w_prec = w_hit / (w_hit + w_miss) if (w_hit + w_miss) > 0 else float("nan")

    print(f"=== label accuracy — {args.game_id} ({args.label}) ===")
    print(f"labels {len(labels)}  reproduced {len(labels) - missing}  missing {missing}")
    print()
    print(f"precision (named & labelled real) : {prec:.3f}   {named_hit}/{n_named}")
    print(f"  ... weighted by tracked seconds : {w_prec:.3f}   "
          f"{w_hit / 60:.1f} of {(w_hit + w_miss) / 60:.1f} min correct")
    print(f"recall    (coach's real players)  : {rec:.3f}   "
          f"{recall_hit}/{recall_hit + recall_miss}")
    print(f"purity    (coach says NOT ours)   : {pur:.3f}   "
          f"{purity_ok}/{purity_ok + purity_bad} correctly dropped")
    if purity_bad:
        print(f"    -> {purity_bad} non-player tracklet(s) named as one of ours")

    if bad_rows:
        print(f"\nworst errors by tracked time:")
        for tl, want, got, st, s in sorted(bad_rows, key=lambda r: -r[4])[:12]:
            print(f"  tl{tl:<6} coach={want:<18} got={str(got):<18} "
                  f"{st:<8} {s:>6.1f}s")

    res = {"game_id": args.game_id, "label": args.label,
           "precision": None if np.isnan(prec) else round(prec, 4),
           "precision_time_weighted": None if np.isnan(w_prec) else round(w_prec, 4),
           "recall": None if np.isnan(rec) else round(rec, 4),
           "purity": None if np.isnan(pur) else round(pur, 4),
           "named_correct": named_hit, "named_total": n_named,
           "nonplayer_named": purity_bad, "labels_missing": missing}
    (OUT_DIR / f"{args.game_id}.labelacc.{args.label}.json").write_text(json.dumps(res, indent=2))
    print(f"\nwrote {args.game_id}.labelacc.{args.label}.json")


if __name__ == "__main__":
    main()
