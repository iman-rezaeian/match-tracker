#!/usr/bin/env python3
"""Anchor-and-propagate identity probe (METRICS_RELEVANCE_PLAN.md step 2, iter 2).

coach_assign_probe showed template-nearest lifts named fragment-time to 70% but 46%
is zone-ambiguous. This iteration: build spatio-temporal continuity CHAINS over clean
ours-fragments, find HIGH-confidence anchors (uniquely-closest board template + GK
geometry + action-event timing), PROPAGATE each anchor's identity across its whole
chain, resolve with on-field + no-overlap. Goal: cut the 46% ambiguity by letting
continuity carry confident IDs through stretches the static template can't resolve.

Read-only on cached tracks; no GPU; no pipeline rerun.
Run: python -m tracking.coach_assign_anchor_probe --game-id mqcf9axlvtuyt
"""
from __future__ import annotations
import argparse, os
from collections import defaultdict
import numpy as np, pandas as pd

HIGH_MARGIN = 6.0   # board-template margin (m) above which an assignment is a confident ANCHOR
AMBIG = 3.0         # margin below which a template assignment is "ambiguous"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--game-id", required=True)
    ap.add_argument("--gap", type=float, default=5.0); ap.add_argument("--cap", type=float, default=12.0)
    a = ap.parse_args()
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from post_game import firestore_io, config
    from post_game.calibration import FieldProjector
    from post_game.team_classifier import classify_tracks
    from post_game.identity import period_clock_to_video_time_factory, _onfield_intervals, _is_onfield
    from post_game.identity_assign import _player_board_positions, _board_to_field
    from tracking.st_stitch_probe import tracklet_endpoints, greedy_st_stitch

    g = firestore_io.get_game(a.game_id); roster = {r.id: r for r in firestore_io.get_roster()}
    cal = firestore_io.get_game_calibration(a.game_id); proj = FieldProjector(cal); L, W = cal.length_m, cal.width_m
    clk = period_clock_to_video_time_factory(g)
    HALF = {1: (0.0, 1515.9, True, True), 2: (1877.8, 3367.2, True, False)}
    onfield = _onfield_intervals(g.starting_lineup, g.events, clk, video_end_s=3367.2)
    squad = set(g.squad or g.starting_lineup or [])
    gk = getattr(g, "gk_player_id", None) or "p_garland"
    tmpl = {p: {pid: _board_to_field(bx, by, fd, fl, L, W)
                for pid, (bx, by) in _player_board_positions(g.events, p).items()}
            for p, (s, e, fd, fl) in HALF.items()}

    df = pd.read_parquet(config.OUTPUTS_DIR / a.game_id / "tracks_raw.parquet").sort_values(["track_id", "time_s"])
    z = np.load(config.OUTPUTS_DIR / a.game_id / "jersey_samples.npz", allow_pickle=True)
    team = classify_tracks(df, {int(k): list(z[k]) for k in z.keys()}, our_home_color_hex="#0a0a0a")
    xy = proj.pixel_to_field_batch(df[["foot_x_eq", "foot_y_eq"]].to_numpy()); df["x_m"], df["y_m"] = xy[:, 0], xy[:, 1]
    df = df.loc[(df.x_m >= -3) & (df.x_m <= L + 3) & (df.y_m >= -3) & (df.y_m <= W + 3)].copy()
    new = np.zeros(len(df), dtype=np.int64); nid = 0; ours = set()
    for tid, idx in df.groupby("track_id").indices.items():
        t = df["time_s"].to_numpy()[idx]; brk = np.concatenate([[0], (np.diff(t) > 1.0).cumsum()]); sub = nid + brk
        if team.get(int(tid)) == 0: ours.update(int(s) for s in np.unique(sub))
        new[idx] = sub; nid = int(sub.max()) + 1
    df["track_id"] = new; eps = tracklet_endpoints(df)

    def contig(t): t = np.sort(t); return float(np.diff(t)[np.diff(t) < 0.3].sum()) if len(t) > 1 else 0.0
    frag = {}
    for tid, gg in df.groupby("track_id"):
        if tid not in ours or tid not in eps: continue
        t = gg["time_s"].to_numpy(); mid = 0.5 * (t[0] + t[-1])
        per = 1 if HALF[1][0] <= mid <= HALF[1][1] else (2 if HALF[2][0] <= mid <= HALF[2][1] else 0)
        if per: frag[tid] = dict(t0=float(t[0]), t1=float(t[-1]), c=contig(t), per=per,
                                 x=float(np.nanmedian(gg["x_m"])), y=float(np.nanmedian(gg["y_m"])))

    # continuity chains over ours fragments (safe params)
    chain_of = greedy_st_stitch(eps, max_gap_s=a.gap, speed_ms=config.MAX_PLAUSIBLE_SPEED_MS,
                                slack_m=config.STITCH_SLACK_M, gap_weight=config.STITCH_GAP_WEIGHT,
                                dist_cap_m=a.cap, allowed=set(frag))
    chain = defaultdict(list)
    for f, r in chain_of.items():
        if f in frag: chain[r].append(f)

    # per-fragment template ranking (on-field-gated)
    def ranked(f):
        out = []
        for pid in squad:
            tp = tmpl[frag[f]["per"]].get(pid)
            if tp is None or not _is_onfield(onfield, pid, frag[f]["t0"], frag[f]["t1"]): continue
            out.append((float(np.hypot(frag[f]["x"] - tp[0], frag[f]["y"] - tp[1])), pid))
        out.sort(); return out
    rank = {f: ranked(f) for f in frag}
    margin = {f: (rank[f][1][0] - rank[f][0][0]) if len(rank[f]) > 1 else 99.0 for f in frag}
    best = {f: (rank[f][0][1] if rank[f] else None) for f in frag}

    # CHAIN identity: weighted vote of high-margin anchor members (by contig); GK chains by deep-zone
    chain_player = {}
    for r, members in chain.items():
        votes = defaultdict(float)
        for f in members:
            if margin[f] >= HIGH_MARGIN and best[f]:
                votes[best[f]] += frag[f]["c"]
        if votes:
            win = max(votes, key=votes.get)
            if votes[win] >= 0.6 * sum(votes.values()):
                chain_player[r] = win

    # ASSIGN: anchored chains first (by total contig), then template-fill remaining; no-overlap per player
    pl_iv = defaultdict(list); named = {}  # frag -> (pid, source)
    def free(pid, f): return all(frag[f]["t1"] < s or frag[f]["t0"] > e for s, e in pl_iv[pid])
    anchored = sorted([r for r in chain if r in chain_player],
                      key=lambda r: -sum(frag[f]["c"] for f in chain[r]))
    for r in anchored:
        pid = chain_player[r]
        for f in sorted(chain[r], key=lambda f: frag[f]["t0"]):
            if _is_onfield(onfield, pid, frag[f]["t0"], frag[f]["t1"]) and free(pid, f):
                pl_iv[pid].append((frag[f]["t0"], frag[f]["t1"])); named[f] = (pid, "anchor")
    for f in sorted(frag, key=lambda f: -frag[f]["c"]):  # template-fill
        if f in named or not best[f]: continue
        for d, pid in rank[f]:
            if free(pid, f): pl_iv[pid].append((frag[f]["t0"], frag[f]["t1"])); named[f] = (pid, "fill"); break

    # REPORT
    tot = sum(f["c"] for f in frag.values())
    nm = sum(frag[f]["c"] for f in named)
    anc = sum(frag[f]["c"] for f in named if named[f][1] == "anchor")
    ambig_named = sum(frag[f]["c"] for f in named if margin[f] < AMBIG)
    ambig_fill = sum(frag[f]["c"] for f in named if named[f][1] == "fill" and margin[f] < AMBIG)
    print(f"chains={len(chain)}  anchored chains={len(anchored)}")
    print(f"named fragment-time: {nm:.0f}/{tot:.0f}s ({100*nm/tot:.0f}%)  |  ANCHOR-backed: {100*anc/nm:.0f}%  fill: {100*(nm-anc)/nm:.0f}%")
    print(f"AMBIGUOUS (margin<{AMBIG}m) share of named time: {100*ambig_named/nm:.0f}%  "
          f"(baseline template-only probe was 46%)  -- of which still-unbacked fill: {100*ambig_fill/nm:.0f}%")
    print(f"\n{'player':<14}{'onfield_min':>12}{'named_min':>10}{'cover%':>8}{'anchor%':>9}")
    for pid in sorted(squad):
        of_s = sum(e - s for s, e in onfield.get(pid, []))
        fs = [f for f in named if named[f][0] == pid]
        nmm = sum(frag[f]["c"] for f in fs); ancm = sum(frag[f]["c"] for f in fs if named[f][1] == "anchor")
        r = roster.get(pid); nme = (getattr(r, "name", pid) if r else pid)[:14]
        tag = " (GK)" if pid == gk else ""
        print(f"{nme:<14}{of_s/60:>12.1f}{nmm/60:>10.1f}{(100*nmm/of_s if of_s else 0):>7.0f}%{(100*ancm/nmm if nmm else 0):>8.0f}%{tag}")


if __name__ == "__main__":
    main()
