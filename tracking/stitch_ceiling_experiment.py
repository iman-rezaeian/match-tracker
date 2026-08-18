#!/usr/bin/env python3
"""Stitch-ceiling experiment: can better OFFLINE STITCH on the (clean) equirect
fragments beat the honest baseline — WITHOUT a new tracker or a re-track?

The accuracy investigation (2026-08-04, 5 probes + adversary) concluded:
  * detections are complete (~16 on-field/frame ≈ headcount) — the loss is
    ASSOCIATION, not optics;
  * the equirect tracker fragments but SWAPS ~7x less than field-space, so its
    fragments are a CLEANER substrate for stitching;
  * the "52.1%" baseline is CORRUPTED — ~35% of tracks weld a 1st-half player to
    a different 2nd-half player across halftime (fixed in the live pipeline via
    the _next_id carry, but the cached baseline predates it);
  * jersey NUMBER is the only per-player signal that survives same-kit U10s
    (OSNet teammate-vs-teammate AUC 0.49 = coin flip).

So the decisive, cheap experiment (no re-track): on the cached EQUIRECT tracks,
(1) gap-split the halftime welds, (2) run the production stitcher in several
configs (greedy vs global min-cost-flow, ± tuned weights), (3) optionally anchor
with VLM jersey-number reads as must-link, and score BOTH:
  * NAMED-COVERAGE (our tracked-seconds that get a name) — the coverage metric;
  * GT-ACCURACY vs the coach's 145 hand-labels (game.identityOverrides) — did the
    names land on the RIGHT player? Coverage is worthless if it's wrong.

Everything reuses the PRODUCTION functions (gap_split_tracks, classify_tracks,
stitch_tracklets, assign_identities_v2) so the numbers mean what the pipeline
means. Read-only: ZERO Firestore writes, no analytics doc, operates on the cached
equirect parquet. Prints a comparison table.

  python -m tracking.stitch_ceiling_experiment --game-id mri01pvelv46d \
      --ckpt-suffix equirect_forrestore --with-numbers

The --with-numbers arm renders tracklet crops and reads jersey numbers with Opus
(slow, costs tokens) then re-stitches with confident reads as must-link anchors.
Omit it for the fast (cached-only) stitch sweep.
"""
from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional


def _gt_kind(v) -> str:
    """Coach label → 'player' | non-player. Mirrors eval_identity_vs_labels."""
    if not v:
        return "drop"
    s = str(v)
    if s.startswith("__"):
        return "nonplayer"
    return "player"


def _track_level_gt(game_id, log) -> tuple[dict[int, str], dict[int, str]]:
    """Translate the coach's TRACKLET-level labels into stable TRACK-level GT.

    The coach labels tracklets in FIX-IDS, but a tracklet id is a union-find root
    of the PRODUCTION stitch — under a different stitch config that id means
    nothing. The live analytics doc's `identity_assignments` records carry
    (track_id, breakdown.tracklet), i.e. the production track→tracklet mapping.
    Expanding each label onto its member TRACKS yields ground truth that any
    stitch config can be scored against.

    Returns ({track_id: player_id} for real players, {track_id: sentinel} for
    coach-marked non-players (opponent/coach/other)).
    """
    from post_game import firestore_io
    snap = firestore_io._team_doc().collection("games").document(game_id).get()
    gt_tl = (snap.to_dict() or {}).get("identityOverrides") or {}
    a = firestore_io.read_analytics(game_id) or {}
    members: dict[int, list[int]] = defaultdict(list)
    for r in (a.get("identity_assignments") or []):
        tl = (r.get("breakdown") or {}).get("tracklet")
        if tl is not None:
            members[int(tl)].append(int(r["track_id"]))
    players: dict[int, str] = {}
    nonplayers: dict[int, str] = {}
    for tl_s, lbl in gt_tl.items():
        try:
            tl = int(tl_s)
        except (TypeError, ValueError):
            continue
        tgt = players if _gt_kind(lbl) == "player" else nonplayers
        for t in members.get(tl, []):
            tgt[t] = str(lbl)
    log(f"track-level GT: {sum(1 for v in gt_tl.values() if _gt_kind(v)=='player')} "
        f"player-tracklets -> {len(players)} tracks "
        f"({len(set(players.values()))} distinct players); "
        f"{len(nonplayers)} non-player tracks")
    return players, nonplayers


def _score_run(label, tracks_df, jersey, embeddings, game, roster, name_of, L, W, cal,
               *, gap_split, split_gap, stitch_mode, must_link, gt, log):
    """Run stage 3→5 (filter→[gap-split]→classify→stitch→assign) and score both
    named-coverage and GT-accuracy vs the coach labels. Returns a result dict."""
    import pandas as pd
    from post_game import config
    from post_game.calibration import FieldProjector
    from post_game.identity import half_windows, period_clock_to_video_time_factory
    from post_game.identity_assign import assign_identities_v2
    from post_game.pipeline import _our_color
    from post_game.reid_stitch import stitch_tracklets, stitch_stats
    from post_game.team_classifier import classify_tracks
    from post_game.gap_split import gap_split_tracks

    df = tracks_df.copy()
    j = dict(jersey)
    emb = dict(embeddings)

    # stage 3 pre-filter (MIRRORS eval_stitch_assign / pipeline): project foot
    # points, drop off-field detections (spectators/coaches/adjacent field), then
    # keep the top-20 longest-lived*conf detections per frame. Without this the
    # stitcher is fed ~27 boxes/frame incl. ~41% off-field, which shatters everything.
    proj = FieldProjector(cal)
    import numpy as np
    xy = proj.pixel_to_field_batch(df[["foot_x_eq", "foot_y_eq"]].to_numpy())
    df["x_m"], df["y_m"] = xy[:, 0], xy[:, 1]
    on = ((df["x_m"] >= -1.5) & (df["x_m"] <= L + 1.5)
          & (df["y_m"] >= -1.5) & (df["y_m"] <= W + 1.5))
    df = df.loc[on].reset_index(drop=True)
    lifetime = df.groupby("track_id").size().rename("track_lifetime")
    df = df.merge(lifetime, on="track_id")
    score = df["track_lifetime"].astype(float)
    if "conf" in df.columns:
        score = score * df["conf"].astype(float).clip(lower=0.1)
    df["_rank_score"] = score
    ranked = df.sort_values(["frame", "_rank_score"], ascending=[True, False])
    df = (ranked.groupby("frame", group_keys=False).head(20)
          .drop(columns=["_rank_score", "track_lifetime"]).reset_index(drop=True))

    n_raw = df["track_id"].nunique()
    if gap_split:
        df, j, emb, _ = gap_split_tracks(df, j, emb, split_gap_s=split_gap)
        n_split = df["track_id"].nunique()
    else:
        n_split = n_raw

    team_of_track = classify_tracks(
        df, j, our_home_color_hex=_our_color(game),
        opp_color_hex=game.away_color, ref_color_hex=game.ref_color)
    tracklet_of_track = stitch_tracklets(
        df, team_of_track, track_embeddings=emb, track_jersey_samples=j,
        mode=stitch_mode, must_link=must_link,
        our_color_hex=_our_color(game), opp_color_hex=game.away_color)
    ss = stitch_stats(tracklet_of_track, team_of_track)

    play_windows = half_windows(game, float(df["time_s"].max()) + 1.0)
    clock_to_video = period_clock_to_video_time_factory(game)
    assignments = assign_identities_v2(
        tracks_df=df, tracklet_of_track=tracklet_of_track, team_of_track=team_of_track,
        events=game.events, roster=roster, starting_lineup=game.starting_lineup,
        gk_player_id=game.gk_player_id, period_clock_to_video_time=clock_to_video,
        periods_video=play_windows, field_length_m=L, field_width_m=W,
        overrides=None, squad=game.squad)

    # named-coverage (== eval_stitch_assign / pipeline _tl_minutes)
    dts = df.sort_values(["track_id", "time_s"]).groupby("track_id")["time_s"].diff().dropna()
    dt_med = float(dts[dts > 0].median()) if len(dts) else 0.1
    counts = df.groupby("track_id").size()
    our = {int(t) for t, tm in team_of_track.items() if tm == 0}
    id_by_track = {a.track_id: a.player_id for a in assignments if a.player_id}
    total_our_s = sum(int(counts.get(t, 0)) for t in our) * dt_med
    named_our_s = sum(int(counts.get(t, 0)) for t in our if t in id_by_track) * dt_med
    cov = 100 * named_our_s / max(1, total_our_s)

    # GT-accuracy at TRACK level (config-independent). `gt` here is already
    # expanded to {track_id: player_id} by _track_level_gt(): the coach labels
    # TRACKLETS, whose ids are union-find roots of the PRODUCTION stitch, so they
    # are meaningless under a different stitch config. Expanding the label down to
    # the production tracklet's MEMBER TRACKS gives a stable ground truth that any
    # stitch config can be scored against.
    # Weight by detections so a long track counts more than a 1-frame sliver.
    gt_total = gt_named = gt_correct = 0
    gt_s_total = gt_s_named = gt_s_correct = 0.0
    for t, lbl in gt.items():
        if t not in counts.index:
            continue
        gt_total += 1
        sec = int(counts.get(t, 0)) * dt_med
        gt_s_total += sec
        pid = id_by_track.get(t)
        if pid:
            gt_named += 1
            gt_s_named += sec
            if str(pid) == str(lbl):
                gt_correct += 1
                gt_s_correct += sec

    per_player = defaultdict(float)
    for t in our:
        pid = id_by_track.get(t)
        if pid:
            per_player[pid] += int(counts.get(t, 0)) * dt_med

    gt_prec = 100 * gt_correct / max(1, gt_named)      # of the ones we named, % right
    gt_rec = 100 * gt_correct / max(1, gt_total)       # of all GT tracks, % named right
    gt_s_rec = 100 * gt_s_correct / max(1, gt_s_total)  # same, weighted by seconds
    res = dict(label=label, n_raw=n_raw, n_split=n_split,
               our_frags=ss["our_fragments"], our_tracklets=ss["our_tracklets"],
               named_tracks=sum(1 for t in our if t in id_by_track), our_tracks=len(our),
               cov=cov, named_s=named_our_s, total_s=total_our_s,
               gt_total=gt_total, gt_named=gt_named, gt_correct=gt_correct,
               gt_prec=gt_prec, gt_rec=gt_rec, gt_s_rec=gt_s_rec,
               per_player={name_of.get(p, p): round(m / 60, 1) for p, m in
                           sorted(per_player.items(), key=lambda kv: -kv[1])})
    log(f"\n==== {label} ====")
    log(f"  raw tracks {n_raw} -> gap-split {n_split} | our frags {res['our_frags']} "
        f"-> tracklets {res['our_tracklets']}")
    log(f"  NAMED-COVERAGE: {named_our_s:.0f}/{total_our_s:.0f}s = {cov:.1f}%  "
        f"(named {res['named_tracks']}/{res['our_tracks']} our-tracks)")
    log(f"  GT (track-level, {gt_total} coach-labeled tracks): named {gt_named}, "
        f"CORRECT {gt_correct} | precision {gt_prec:.0f}% (of named) | "
        f"recall {gt_rec:.0f}% (of all GT) | sec-weighted recall {gt_s_rec:.0f}%")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--ckpt-suffix", default="equirect_forrestore",
                    help="cached track set to score (default the good equirect baseline)")
    ap.add_argument("--with-numbers", action="store_true",
                    help="also run the jersey-number-anchored arm (slow; Opus VLM + tokens)")
    ap.add_argument("--vlm-model", default="claude-opus-4-8")
    ap.add_argument("--vlm-min-conf", type=float, default=0.6)
    ap.add_argument("--vlm-limit", type=int, default=0,
                    help="read at most N tracklets (0 = all). Use a small N to smoke-test "
                         "the VLM arm before committing to a long unattended run.")
    ap.add_argument("--arm", choices=["A", "B", "AB"], default="AB",
                    help="which arms to run (A = stitch sweep, B = number-anchored)")
    args = ap.parse_args()
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

    t0 = time.time()
    logf = open(f"/tmp/{args.game_id}.stitch_ceiling.log", "a", buffering=1)

    def log(m=""):
        line = str(m)
        print(line, flush=True)
        logf.write(line + "\n")

    import pandas as pd
    from post_game import config, firestore_io

    log(f"\n########## STITCH-CEILING EXPERIMENT {args.game_id} "
        f"(ckpt={args.ckpt_suffix}) started ##########")

    ckpt = config.OUTPUTS_DIR / args.game_id
    sfx = f".{args.ckpt_suffix}" if args.ckpt_suffix else ""
    tp = ckpt / f"tracks_raw{sfx}.parquet"
    jp = ckpt / f"jersey_samples{sfx}.npz"
    ep = ckpt / f"embeddings{sfx}.npz"
    if not tp.exists():
        raise SystemExit(f"no tracks at {tp}")
    import numpy as np
    tracks_df = pd.read_parquet(tp)
    # Reduce each track's per-frame HSV pixel arrays to ONE median vector — the
    # same reduction classify_tracks applies, and exactly what eval_stitch_assign
    # does (_load_jersey_medians). Passing the raw per-frame lists instead makes
    # classify_tracks team-split completely differently (measured: 1955 vs 775
    # our-fragments), which silently invalidates every downstream number.
    jersey = {}
    if jp.exists():
        with np.load(jp, allow_pickle=True) as nz:
            for k in nz.files:
                samples = list(nz[k])
                if not samples:
                    continue
                stacked = np.vstack([np.asarray(s, dtype=np.float32) for s in samples])
                jersey[int(k)] = [np.median(stacked, axis=0).astype(np.float32)]
    embeddings = {}
    if ep.exists():
        with np.load(ep, allow_pickle=True) as nz:
            embeddings = {int(k): np.asarray(nz[k], dtype=np.float32) for k in nz.files}

    game = firestore_io.get_game(args.game_id)
    roster = firestore_io.get_roster()
    name_of = {r.id: r.name for r in roster}
    cal = firestore_io.get_game_calibration(args.game_id)
    if cal is None:
        raise SystemExit("No calibration.")
    L, W = cal.length_m, cal.width_m

    log(f"loaded {tracks_df['track_id'].nunique()} tracks, {len(jersey)} jersey, "
        f"{len(embeddings)} embeddings")
    gt, gt_nonplayer = _track_level_gt(args.game_id, log)
    if not gt:
        raise SystemExit("no track-level GT could be built — is the analytics doc present?")

    results = []
    # Arm A — the stitch sweep on cached equirect fragments (no VLM).
    # A0 = the PRODUCTION-equivalent baseline (no gap-split — that's what the cached
    #      equirect run + the 52.1% figure used). This must reproduce ~52% or the
    #      harness is not faithful and nothing below it means anything.
    # A1 = the surgical halftime-weld fix: split ONLY at >300s gaps. Measured on this
    #      cache, exactly 1257 tracks carry a >300s internal gap (identical count at
    #      10s..300s), i.e. the halftime welds are cleanly separable there. The shipped
    #      SPLIT_GAP_S=1.0 instead shatters tracks at 6004 harmless micro-misses.
    SWEEP = [
        ("A0_prod_greedy_nosplit",     dict(gap_split=False, stitch_mode="greedy")),
        ("A1_split300_greedy",         dict(gap_split=True,  stitch_mode="greedy", split_gap=300.0)),
        ("A2_split300_global",         dict(gap_split=True,  stitch_mode="global", split_gap=300.0)),
        ("A3_split300_global_appw0.5", dict(gap_split=True,  stitch_mode="global", split_gap=300.0, app_weight=0.5)),
        ("A4_split300_global_dcap12",  dict(gap_split=True,  stitch_mode="global", split_gap=300.0, dist_cap=12.0)),
        ("A5_split1_greedy_shipped",   dict(gap_split=True,  stitch_mode="greedy", split_gap=1.0)),
    ]
    for label, cfg in (SWEEP if args.arm in ("A", "AB") else []):
        # apply per-config overrides read at call-time inside stitch
        if "app_weight" in cfg:
            config.STITCH_APP_WEIGHT = cfg["app_weight"]
        else:
            config.STITCH_APP_WEIGHT = float(os.environ.get("STITCH_APP_WEIGHT", "5.0"))
        if "dist_cap" in cfg:
            config.STITCH_DIST_CAP_M = cfg["dist_cap"]
        else:
            config.STITCH_DIST_CAP_M = float(os.environ.get("STITCH_DIST_CAP_M", "inf"))
        try:
            r = _score_run(label, tracks_df, jersey, embeddings, game, roster, name_of, L, W, cal,
                           gap_split=cfg["gap_split"],
                           split_gap=cfg.get("split_gap", config.SPLIT_GAP_S),
                           stitch_mode=cfg["stitch_mode"], must_link=None, gt=gt, log=log)
            results.append(r)
        except Exception as e:
            log(f"  {label} FAILED: {type(e).__name__}: {e}")

    # Arm B — jersey-number-anchored (best stitch config + VLM must-link).
    if args.with_numbers and args.arm in ("B", "AB"):
        log("\n---- Arm B: reading jersey numbers with the VLM (slow) ----")
        try:
            must_link = _build_number_anchors(
                args, tracks_df, jersey, embeddings, game, roster, L, W, log)
            log(f"  number anchors (must-link tracks): {len(must_link)}")
            config.STITCH_APP_WEIGHT = 0.5
            config.STITCH_DIST_CAP_M = 12.0
            r = _score_run("B_global_split_numAnchored", tracks_df, jersey, embeddings,
                           game, roster, name_of, L, W, cal,
                           gap_split=True, split_gap=config.SPLIT_GAP_S,
                           stitch_mode="global", must_link=must_link, gt=gt, log=log)
            results.append(r)
        except Exception as e:
            import traceback
            log(f"  Arm B FAILED: {type(e).__name__}: {e}\n{traceback.format_exc()}")

    # Final comparison table
    log("\n\n===================== COMPARISON =====================")
    log(f"{'config':<30} {'cov%':>6} {'tracklets':>9} | "
        f"{'GTcorrect':>10} {'prec%':>6} {'rec%':>6} {'secRec%':>8}")
    for r in results:
        log(f"{r['label']:<30} {r['cov']:>6.1f} {r['our_tracklets']:>9} | "
            f"{r['gt_correct']:>4}/{r['gt_total']:<5} {r['gt_prec']:>6.0f} "
            f"{r['gt_rec']:>6.0f} {r['gt_s_rec']:>8.0f}")
    log("\nHOW TO READ:")
    log("  cov%     = NAMED-COVERAGE (our tracked-seconds that got a name). The old")
    log("             52.1% figure is this metric on the un-split cache => compare to A0.")
    log("  prec%    = of the coach-labeled tracks we DID name, % named the RIGHT player.")
    log("  rec%     = of ALL coach-labeled tracks, % named right (the real accuracy).")
    log("  secRec%  = same as rec% but weighted by tracked seconds (what stats actually use).")
    log("  A0 is the production-equivalent baseline: if it does NOT land near 52% the")
    log("  harness is unfaithful and every other row is meaningless.")
    if results:
        best = max(results, key=lambda r: r['gt_s_rec'])
        log(f"\nBEST by second-weighted GT recall: {best['label']} "
            f"(secRec {best['gt_s_rec']:.0f}%, cov {best['cov']:.1f}%)")
        a0 = next((r for r in results if r['label'].startswith('A0')), None)
        if a0:
            log(f"vs A0 baseline: secRec {a0['gt_s_rec']:.0f}% -> {best['gt_s_rec']:.0f}% "
                f"({best['gt_s_rec']-a0['gt_s_rec']:+.0f} pts), "
                f"cov {a0['cov']:.1f}% -> {best['cov']:.1f}% ({best['cov']-a0['cov']:+.1f} pts)")
    log(f"elapsed {time.time()-t0:.0f}s")
    logf.close()


def _build_number_anchors(args, tracks_df, jersey, embeddings, game, roster, L, W, log):
    """Read jersey numbers for each stitched tracklet with the VLM and return a
    must_link map {track_id -> player_id} for tracklets with a confident number
    that maps to a rostered player. Reuses vlm_identity's production machinery."""
    from post_game import config, firestore_io
    from post_game.identity import half_windows, period_clock_to_video_time_factory
    from post_game.pipeline import _our_color
    from post_game.reid_stitch import stitch_tracklets
    from post_game.team_classifier import classify_tracks
    from post_game.gap_split import gap_split_tracks
    import tracking.vlm_identity as vid

    # Reconstruct the (gap-split, global) tracklets the anchors will be keyed to.
    import numpy as np
    df = tracks_df.copy()
    j, emb = dict(jersey), dict(embeddings)
    df, j, emb, _ = gap_split_tracks(df, j, emb, split_gap_s=config.SPLIT_GAP_S)
    team_of_track = classify_tracks(df, j, our_home_color_hex=_our_color(game),
                                    opp_color_hex=game.away_color, ref_color_hex=game.ref_color)
    tracklet_of_track = stitch_tracklets(df, team_of_track, track_embeddings=emb,
                                         track_jersey_samples=j, mode="global",
                                         our_color_hex=_our_color(game), opp_color_hex=game.away_color)
    video = firestore_io_local_video(game, args.game_id, log)

    _, num_to_pid, dup_numbers = vid.build_number_map(roster)
    # roster numbers for the prompt (all rostered numbers, incl. dups for the read)
    roster_numbers = sorted(set(num_to_pid.keys()) | set(dup_numbers))
    our_roots = {r for t, r in tracklet_of_track.items() if team_of_track.get(t) == 0}
    log(f"  {len(our_roots)} our-team tracklets to read numbers for")

    from pathlib import Path
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="numanchor_"))
    # group rows by tracklet root
    df["_root"] = df["track_id"].map(tracklet_of_track)
    must_link: dict[int, str] = {}
    read_ct = 0
    err_ct = 0
    # Read the LONGEST tracklets first: they carry the most seconds, so a legible
    # number there anchors the most tracked time per VLM call (and a --vlm-limit
    # smoke test then exercises the cases that actually matter).
    roots_by_size = sorted(our_roots, key=lambda r: -int((df["_root"] == r).sum()))
    for root in roots_by_size:
        if args.vlm_limit and read_ct >= args.vlm_limit:
            log(f"  --vlm-limit {args.vlm_limit} reached; stopping number reads")
            break
        sub = df[df["_root"] == root]
        if len(sub) < 5:
            continue
        try:
            number, conf, nreads, _reason, team = vid.read_tracklet_number(
                video, sub, tmp, int(root), roster_numbers,
                model=args.vlm_model, crops=6, min_conf=args.vlm_min_conf, batches=2,
                our_color=_our_color(game), opp_color=game.away_color)
        except Exception as e:
            read_ct += 1          # count attempts, so --vlm-limit always terminates
            err_ct += 1
            log(f"    tracklet {root}: read error {type(e).__name__}: {str(e)[:160]}")
            # Fail fast on a systemic outage (corp-VPN TLS interception, bad auth):
            # without this the loop burns thousands of tracklets on the same error.
            if err_ct >= 5 and err_ct == read_ct:
                raise RuntimeError(
                    f"aborting number-reads: first {err_ct} calls ALL failed "
                    f"({type(e).__name__}). Likely corp-VPN TLS interception "
                    f"(issuer 'focsecurefw-ssl-decrypt' lacks an Authority Key "
                    f"Identifier, which OpenSSL rejects) or expired auth. "
                    f"Disconnect the VPN or fix the cert path, then re-run.") from e
            continue
        read_ct += 1
        if number is not None and conf >= args.vlm_min_conf and team == "ours":
            pid = num_to_pid.get(int(number))
            if pid:
                # anchor EVERY member track of this tracklet to the player
                for t, r in tracklet_of_track.items():
                    if r == root:
                        must_link[t] = pid
        if read_ct % 20 == 0:
            log(f"    ...read {read_ct} tracklets, {len(set(must_link.values()))} players anchored")
    log(f"  read {read_ct} tracklets; anchored {len(must_link)} tracks to "
        f"{len(set(must_link.values()))} players")
    return must_link


def firestore_io_local_video(game, game_id, log):
    """Resolve the local raw video path (mirror of retrack_smoke's resolver)."""
    from post_game import config
    cand = [
        Path("/Users/irezaeian/Movies/stompers/VID_20260712_Game2.mp4"),
        config.OUTPUTS_DIR / game_id / "source.mp4",
    ]
    for p in cand:
        if p.exists():
            log(f"  video: {p}")
            return str(p)
    raise SystemExit("raw video not found for number-reading arm")


if __name__ == "__main__":
    main()
