"""Time-weighted per-player precision/recall against UNBIASED ground truth
(Tier 1 #1). The honest instrument: coach overrides are adversarial corrections
(relative deltas only); this scores the auto-assignment against a blind GT label
set (from tracking.player_gt_app) for ABSOLUTE numbers and the Tier-1 baseline.

Runs assign_identities_v2 with coach overrides WITHHELD (the fair test), then for
each player X over the LABELED tracklet universe (time-weighted):
  precision = time(pred=X & true=X) / time(pred=X, among labelled)
  recall    = time(pred=X & true=X) / time(true=X)
Also reports per-STATUS precision vs GT — the real cap check: of tracklets the
pipeline shows CONFIDENT (auto/review), what fraction are truly right. (The cap
changes status/confidence, not which player is chosen, so per-player P/R is
cap-invariant; per-status precision is where the cap shows.)

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.player_gt_eval --game-id mqcf9axlvtuyt
    # cap effect on confident-tier precision:
    ID_ANCHOR_CAP_ENABLED=0 .venv-post-game/bin/python -m tracking.player_gt_eval --game-id mqcf9axlvtuyt
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from post_game import config, firestore_io
from post_game.identity import half_windows, period_clock_to_video_time_factory
from post_game.identity_assign import assign_identities_v2

S4_DIR = Path(__file__).resolve().parent / "outputs" / "identity_eval"
LABELS_ROOT = Path(__file__).resolve().parent / "labels"
NOT_PLAYER, CANT_TELL = "__not_player__", "__cant_tell__"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-id", required=True)
    args = ap.parse_args()

    gt_csv = LABELS_ROOT / f"{args.game_id}_player_gt" / "gt.csv"
    if not gt_csv.exists():
        raise SystemExit(f"No GT labels: {gt_csv} (run player_gt_sampler + player_gt_app).")
    s4p, s4j = S4_DIR / f"{args.game_id}.stage4.parquet", S4_DIR / f"{args.game_id}.stage4.json"
    if not (s4p.exists() and s4j.exists()):
        raise SystemExit(f"No stage-4 cache for {args.game_id} (run eval_identity once).")

    game = firestore_io.get_game(args.game_id)
    roster = firestore_io.get_roster()
    name_of = {r.id: (f"#{r.jersey_number or '?'} {r.name}") for r in roster}
    field_cal = firestore_io.get_game_calibration(args.game_id)

    tracks_df = pd.read_parquet(s4p)
    maps = json.loads(s4j.read_text())
    team_of = {int(k): int(v) for k, v in maps["team_of_track"].items()}
    tl_of = {int(k): int(v) for k, v in maps["tracklet_of_track"].items()}

    duration_s = float(tracks_df["time_s"].max()) + 1.0
    assignments = assign_identities_v2(
        tracks_df=tracks_df, tracklet_of_track=tl_of, team_of_track=team_of,
        events=game.events, roster=roster, starting_lineup=game.starting_lineup,
        gk_player_id=game.gk_player_id,
        period_clock_to_video_time=period_clock_to_video_time_factory(game),
        periods_video=half_windows(game, duration_s),
        field_length_m=field_cal.length_m, field_width_m=field_cal.width_m,
        overrides=None, squad=game.squad,  # WITHHELD — fair test
    )
    pred = {}  # tracklet -> (player_id, status)
    for a in assignments:
        tl = (a.breakdown or {}).get("tracklet")
        if tl is not None:
            pred[int(tl)] = (a.player_id, a.status)

    # tracked minutes per tracklet (detection-count × median dt) for time weights
    counts = tracks_df.groupby("track_id").size()
    dts = tracks_df.sort_values(["track_id", "time_s"]).groupby("track_id")["time_s"].diff().dropna()
    dt_med = float(dts[dts > 0].median()) if len(dts) else 0.1
    members = defaultdict(list)
    for trk, tl in tl_of.items():
        if team_of.get(trk) == 0:
            members[tl].append(trk)
    tl_min = {tl: sum(int(counts.get(m, 0)) for m in mem) * dt_med / 60.0
              for tl, mem in members.items()}

    # ground-truth labels
    gt = {}  # tracklet -> true_player_id (only label == "player")
    n_lab = {"player": 0, NOT_PLAYER: 0, CANT_TELL: 0, "": 0}
    with open(gt_csv) as f:
        for r in csv.DictReader(f):
            lab = (r.get("label") or "").strip()
            n_lab[lab] = n_lab.get(lab, 0) + 1
            if lab == "player" and r.get("true_player_id"):
                gt[int(r["tracklet_id"])] = r["true_player_id"]
    labeled = {int(r) for r in gt}  # tracklets with a true player
    judgeable = labeled  # cant-tell excluded; not-player handled in precision below

    # --- per-player time-weighted precision/recall over the labeled universe ---
    pred_t = defaultdict(float)   # time pipeline assigned to X (among labeled, non-canttell)
    true_t = defaultdict(float)   # true time of X (labeled player)
    hit_t = defaultdict(float)    # time pred==X==true
    conf_pairs = defaultdict(float)  # (true, pred) mismatch time
    for tl, true_pid in gt.items():
        m = tl_min.get(tl, 0.0)
        pp = pred.get(tl, (None, "unknown"))[0]
        true_t[true_pid] += m
        if pp is not None:
            pred_t[pp] += m
            if pp == true_pid:
                hit_t[pp] += m
            else:
                conf_pairs[(true_pid, pp)] += m

    players = sorted(set(true_t) | set(pred_t), key=lambda p: -true_t.get(p, 0))
    rows = []
    for p in players:
        prec = hit_t[p] / pred_t[p] if pred_t[p] else None
        rec = hit_t[p] / true_t[p] if true_t[p] else None
        f1 = (2 * prec * rec / (prec + rec)) if (prec and rec) else None
        rows.append({"player": name_of.get(p, p),
                     "true_min": round(true_t[p], 1), "pred_min": round(pred_t[p], 1),
                     "precision": round(prec, 3) if prec is not None else None,
                     "recall": round(rec, 3) if rec is not None else None,
                     "f1": round(f1, 3) if f1 is not None else None})
    micro_p = sum(hit_t.values()) / sum(pred_t.values()) if sum(pred_t.values()) else None
    micro_r = sum(hit_t.values()) / sum(true_t.values()) if sum(true_t.values()) else None

    # --- per-status precision vs GT (the cap check) ---
    # Over judgeable tracklets (true player known): of tracklets in status S that
    # the pipeline assigned to SOME player, what fraction (time-weighted) is right.
    st_t, st_hit = defaultdict(float), defaultdict(float)
    for tl in judgeable:
        pp, status = pred.get(tl, (None, "unknown"))
        if pp is None:
            continue
        m = tl_min.get(tl, 0.0)
        st_t[status] += m
        if pp == gt[tl]:
            st_hit[status] += m
    status_prec = {s: {"precision": round(st_hit[s] / st_t[s], 3) if st_t[s] else None,
                       "min": round(st_t[s], 1)} for s in sorted(st_t)}

    # getattr-guarded so the instrument runs on a branch without the Tier-0 cap;
    # the cap-check (per-status precision delta) activates once that lands.
    cap = "ON" if getattr(config, "ID_ANCHOR_CAP_ENABLED", False) else "OFF"
    print(f"\n=== Player GT eval · {args.game_id} · cap {cap} ===")
    print(f"labels: player={n_lab.get('player',0)} not-player={n_lab.get(NOT_PLAYER,0)} "
          f"cant-tell={n_lab.get(CANT_TELL,0)} unlabeled={n_lab.get('',0)}")
    print(f"labeled-universe tracked time: {sum(true_t.values()):.1f} min across {len(labeled)} tracklets\n")
    print(f"{'player':<22}{'true':>6}{'pred':>6}{'prec':>7}{'recall':>8}{'f1':>7}")
    for r in rows:
        print(f"{r['player']:<22}{r['true_min']:>6}{r['pred_min']:>6}"
              f"{str(r['precision']):>7}{str(r['recall']):>8}{str(r['f1']):>7}")
    print(f"\nMICRO precision={round(micro_p,3) if micro_p is not None else None}  "
          f"recall={round(micro_r,3) if micro_r is not None else None}")
    print(f"per-status precision vs GT: " + "  ".join(
        f"{s}={v['precision']} ({v['min']}min)" for s, v in status_prec.items()))
    top_conf = sorted(conf_pairs.items(), key=lambda kv: -kv[1])[:6]
    if top_conf:
        print("top confusions (true → pred, min):")
        for (tp, pp), m in top_conf:
            print(f"  {name_of.get(tp, tp)} → {name_of.get(pp, pp)}: {m:.1f}")

    out = {"game_id": args.game_id, "cap": cap, "label_counts": n_lab,
           "labeled_tracklets": len(labeled), "labeled_min": round(sum(true_t.values()), 1),
           "per_player": rows, "micro_precision": micro_p, "micro_recall": micro_r,
           "status_precision_vs_gt": status_prec,
           "top_confusions": [{"true": name_of.get(t, t), "pred": name_of.get(p, p),
                               "min": round(m, 1)} for (t, p), m in top_conf]}
    out_path = LABELS_ROOT / f"{args.game_id}_player_gt" / f"eval_cap_{cap.lower()}.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    main()
