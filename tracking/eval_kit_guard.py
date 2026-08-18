#!/usr/bin/env python3
"""Decisive A/B for the cross-team STITCH KIT GUARD, scored against BLIND GT.

Finding (2026-08-05): `reid_stitch.py` already refuses to chain two fragments whose
kit colours are confidently OPPOSITE (green vs blue) — a CANNOT-LINK that directly
prevents welding one of our players onto an opponent. But the guard is gated on
`TRACK_PITCH AND PITCH_COLOR_GATE`, and TRACK_PITCH defaults OFF since the
field-space tracker was abandoned. **So the guard never runs in production.**
On W8 it looked worth +13 points of precision; 19% of stitched tracklets contain
both a confident-green and a confident-blue track.

This scores guard-ON vs guard-OFF on the two BLIND-GT games (labels from
tracking.player_gt_app — unbiased, unlike the coach's adversarial FIX-IDS
corrections), reporting what actually matters:

    CORRECT / WRONG-PLAYER / NOT-OURS minutes, and PRECISION

...NOT coverage. Two guardrails learned the hard way:
  * the denominator is held over the whole LABELED universe, never only the
    tracklets that happened to get named (that bug made a 38% result read 44%);
  * time is charged as detection-count x median dt, and we also print distinct
    observed frames, so `dt`-clip inflation can't masquerade as a win.

Read-only: no Firestore writes, no re-tracks. Both games' jersey_samples.npz are
multi-GB, so each game takes a few minutes to load.

  python -m tracking.eval_kit_guard --game-id mqcf9axlvtuyt
  python -m tracking.eval_kit_guard --game-id mqcjsjugchb2i
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

LABELS_ROOT = Path(__file__).resolve().parent / "labels"
NON_PLAYER = {"__not_player__", "__cant_tell__", "__referee__", "__opponent__"}


def _load_gt(game_id: str) -> dict[int, str]:
    """{tracklet_id: true_player_id or sentinel} from the blind GT csv."""
    p = LABELS_ROOT / f"{game_id}_player_gt" / "gt.csv"
    if not p.exists():
        raise SystemExit(f"no GT labels at {p}")
    out: dict[int, str] = {}
    with p.open() as f:
        for row in csv.DictReader(f):
            tl = row.get("tracklet_id")
            if not tl:
                continue
            lbl = (row.get("true_player_id") or "").strip() or (row.get("label") or "").strip()
            if lbl:
                out[int(tl)] = lbl
    return out


def _score(game_id: str, guard_on: bool, gt: dict[int, str], log) -> dict:
    import pandas as pd
    from post_game import config, firestore_io
    from post_game.calibration import FieldProjector
    from post_game.identity import half_windows, period_clock_to_video_time_factory
    from post_game.identity_assign import assign_identities_v2
    from post_game.pipeline import _our_color
    from post_game.reid_stitch import stitch_tracklets
    from post_game.team_classifier import classify_tracks

    # Flip the guard. It is read at call time inside stitch_tracklets, so setting
    # these here is sufficient — and PITCH_COLOR_GATE already defaults on.
    config.TRACK_PITCH = bool(guard_on)
    config.PITCH_COLOR_GATE = True

    ckpt = config.OUTPUTS_DIR / game_id
    tracks_df = pd.read_parquet(ckpt / "tracks_raw.parquet")
    # Per-track MEDIAN hsv — the reduction classify_tracks applies. Passing raw
    # per-frame lists makes classify team-split completely differently.
    jersey: dict[int, list] = {}
    with np.load(ckpt / "jersey_samples.npz", allow_pickle=True) as nz:
        for k in nz.files:
            s = list(nz[k])
            if not s:
                continue
            jersey[int(k)] = [np.median(
                np.vstack([np.asarray(a, dtype=np.float32) for a in s]), axis=0
            ).astype(np.float32)]
    embeddings: dict[int, np.ndarray] = {}
    ep = ckpt / "embeddings.npz"
    if ep.exists():
        with np.load(ep, allow_pickle=True) as nz:
            embeddings = {int(k): np.asarray(nz[k], dtype=np.float32) for k in nz.files}

    game = firestore_io.get_game(game_id)
    roster = firestore_io.get_roster()
    cal = firestore_io.get_game_calibration(game_id)
    L, W = cal.length_m, cal.width_m

    # stage 3: project, drop off-field, top-20/frame (mirrors pipeline)
    proj = FieldProjector(cal)
    xy = proj.pixel_to_field_batch(tracks_df[["foot_x_eq", "foot_y_eq"]].to_numpy()) \
        if "foot_x_eq" in tracks_df.columns else None
    if xy is None:      # coherent-style frames already carry x_m/y_m
        pass
    else:
        tracks_df["x_m"], tracks_df["y_m"] = xy[:, 0], xy[:, 1]
    on = ((tracks_df["x_m"] >= -1.5) & (tracks_df["x_m"] <= L + 1.5)
          & (tracks_df["y_m"] >= -1.5) & (tracks_df["y_m"] <= W + 1.5))
    tracks_df = tracks_df.loc[on].reset_index(drop=True)
    lt = tracks_df.groupby("track_id").size().rename("lt")
    tracks_df = tracks_df.merge(lt, on="track_id")
    tracks_df["_s"] = tracks_df["lt"].astype(float) * tracks_df["conf"].astype(float).clip(lower=0.1)
    tracks_df = (tracks_df.sort_values(["frame", "_s"], ascending=[True, False])
                 .groupby("frame", group_keys=False).head(20)
                 .drop(columns=["_s", "lt"]).reset_index(drop=True))

    team_of = classify_tracks(tracks_df, jersey, our_home_color_hex=_our_color(game),
                              opp_color_hex=game.away_color, ref_color_hex=game.ref_color)
    tl_of = stitch_tracklets(tracks_df, team_of, track_embeddings=embeddings,
                             track_jersey_samples=jersey,
                             our_color_hex=_our_color(game), opp_color_hex=game.away_color)
    pw = half_windows(game, float(tracks_df["time_s"].max()) + 1.0)
    assigns = assign_identities_v2(
        tracks_df=tracks_df, tracklet_of_track=tl_of, team_of_track=team_of,
        events=game.events, roster=roster, starting_lineup=game.starting_lineup,
        gk_player_id=game.gk_player_id,
        period_clock_to_video_time=period_clock_to_video_time_factory(game),
        periods_video=pw, field_length_m=L, field_width_m=W,
        overrides=None, squad=game.squad)      # overrides WITHHELD = the fair test

    dts = tracks_df.sort_values(["track_id", "time_s"]).groupby("track_id")["time_s"].diff().dropna()
    dt_med = float(dts[dts > 0].median()) if len(dts) else 0.1
    counts = tracks_df.groupby("track_id").size()
    pred = {a.track_id: a.player_id for a in assigns if a.player_id}

    # GT is labeled per TRACKLET; charge each labeled tracklet's member tracks.
    # DENOMINATOR = the whole labeled universe (never only what got named).
    correct = wrong = notours = unnamed = 0.0
    frames_correct = frames_total = 0
    for tl, truth in gt.items():
        members = [t for t, r in tl_of.items() if r == tl]
        if not members:
            continue
        sec = sum(int(counts.get(t, 0)) for t in members) * dt_med
        frames_total += sum(int(counts.get(t, 0)) for t in members)
        if sec <= 0:
            continue
        # majority predicted player across the tracklet's member tracks
        votes: dict[str, int] = defaultdict(int)
        for t in members:
            p = pred.get(t)
            if p:
                votes[p] += int(counts.get(t, 0))
        got = max(votes, key=votes.get) if votes else None
        if got is None:
            unnamed += sec
        elif truth in NON_PLAYER:
            notours += sec           # named one of OUR players over a non-player
        elif str(got) == str(truth):
            correct += sec
            frames_correct += sum(int(counts.get(t, 0)) for t in members)
        else:
            wrong += sec

    named = correct + wrong + notours
    prec = 100 * correct / named if named else 0.0
    tag = "GUARD ON " if guard_on else "GUARD OFF"
    log(f"  [{tag}] labeled universe {(correct+wrong+notours+unnamed)/60:.1f} min | "
        f"CORRECT {correct/60:.1f} | WRONG-PLAYER {wrong/60:.1f} | "
        f"NOT-OURS {notours/60:.1f} | unnamed {unnamed/60:.1f}")
    log(f"  [{tag}] PRECISION (correct / named) = {prec:.1f}%   "
        f"| observed frames correct {frames_correct}/{frames_total}")
    return dict(guard=guard_on, correct_min=correct / 60, wrong_min=wrong / 60,
                notours_min=notours / 60, unnamed_min=unnamed / 60, precision=prec,
                tracklets=len(set(tl_of.values())))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    args = ap.parse_args()

    out = Path(f"/tmp/{args.game_id}.kit_guard.log")

    def log(m=""):
        print(m, flush=True)
        with out.open("a") as f:
            f.write(str(m) + "\n")

    gt = _load_gt(args.game_id)
    n_player = sum(1 for v in gt.values() if v not in NON_PLAYER)
    log(f"\n===== KIT-GUARD A/B · {args.game_id} =====")
    log(f"blind GT: {len(gt)} labeled tracklets ({n_player} real players, "
        f"{len(gt)-n_player} non-player)")

    res = []
    for guard in (False, True):
        try:
            res.append(_score(args.game_id, guard, gt, log))
        except Exception as e:
            import traceback
            log(f"  FAILED (guard={guard}): {type(e).__name__}: {e}")
            log(traceback.format_exc()[:1500])
    if len(res) == 2:
        off, on = res
        log(f"\n  DELTA (on - off): precision {off['precision']:.1f}% -> {on['precision']:.1f}% "
            f"({on['precision']-off['precision']:+.1f} pts) | "
            f"correct {off['correct_min']:.1f} -> {on['correct_min']:.1f} min | "
            f"not-ours {off['notours_min']:.1f} -> {on['notours_min']:.1f} min")
        log("  SHIP IF precision rises on BOTH GT games (else the W8 result was label bias).")


if __name__ == "__main__":
    main()
