#!/usr/bin/env python3
"""Coach-log-anchored fragment assignment probe (METRICS_RELEVANCE_PLAN.md step 2).

Tests the core lever: instead of per-window 1:1 Hungarian (strands ~29/30 fragments
per player as 'unknown' -> only 344 named), let each player ACCUMULATE many
non-overlapping clean fragments across the game, gated by on-field windows
(lineup+subs) + board-zone template, with a one-place-at-a-time constraint.

Reuses production helpers so it stays faithful. Read-only on cached tracks; no GPU.
Reports per-player NAMED COVERAGE (optimistic) AND an assignment-MARGIN diagnostic
(coverage is trivially gameable by assigning every fragment to someone; margin =
2nd-best minus best template distance flags ambiguous/likely-wrong assignments).

Run: python -m tracking.coach_assign_probe --game-id mqcf9axlvtuyt
"""
from __future__ import annotations
import argparse, os
import numpy as np, pandas as pd


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--game-id", required=True); a = ap.parse_args()
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from post_game import firestore_io, config
    from post_game.calibration import FieldProjector
    from post_game.team_classifier import classify_tracks
    from post_game.identity import period_clock_to_video_time_factory, _onfield_intervals, _is_onfield
    from post_game.identity_assign import _player_board_positions, _board_to_field
    from tracking.st_stitch_probe import tracklet_endpoints, greedy_st_stitch

    g = firestore_io.get_game(a.game_id)
    roster = {r.id: r for r in firestore_io.get_roster()}
    cal = firestore_io.get_game_calibration(a.game_id); proj = FieldProjector(cal); L, W = cal.length_m, cal.width_m
    clk = period_clock_to_video_time_factory(g)
    # play windows + resolved orientation (from the run log)
    HALF = {1: (0.0, 1515.9, True, True), 2: (1877.8, 3367.2, True, False)}  # start,end,flip_d,flip_l
    onfield = _onfield_intervals(g.starting_lineup, g.events, clk, video_end_s=3367.2)
    squad = set(g.squad or g.starting_lineup or [])
    board = {1: _player_board_positions(g.events, 1), 2: _player_board_positions(g.events, 2)}
    # per-player field template per period
    tmpl = {}
    for p, (s, e, fd, fl) in HALF.items():
        tmpl[p] = {pid: _board_to_field(bx, by, fd, fl, L, W) for pid, (bx, by) in board[p].items()}

    # tracks -> team -> ours clean fragments
    df = pd.read_parquet(config.OUTPUTS_DIR / a.game_id / "tracks_raw.parquet").sort_values(["track_id", "time_s"])
    z = np.load(config.OUTPUTS_DIR / a.game_id / "jersey_samples.npz", allow_pickle=True)
    team = classify_tracks(df, {int(k): list(z[k]) for k in z.keys()}, our_home_color_hex="#0a0a0a")
    xy = proj.pixel_to_field_batch(df[["foot_x_eq", "foot_y_eq"]].to_numpy()); df["x_m"], df["y_m"] = xy[:, 0], xy[:, 1]
    df = df.loc[(df.x_m >= -3) & (df.x_m <= L + 3) & (df.y_m >= -3) & (df.y_m <= W + 3)].copy()
    new = np.zeros(len(df), dtype=np.int64); nid = 0; ours_frag = set()
    for tid, idx in df.groupby("track_id").indices.items():
        t = df["time_s"].to_numpy()[idx]; brk = np.concatenate([[0], (np.diff(t) > 1.0).cumsum()]); sub = nid + brk
        if team.get(int(tid)) == 0:
            ours_frag.update(int(s) for s in np.unique(sub))
        new[idx] = sub; nid = int(sub.max()) + 1
    df["track_id"] = new
    eps = tracklet_endpoints(df)

    # per-fragment summary
    def contig(t): t = np.sort(t); return float(np.diff(t)[np.diff(t) < 0.3].sum()) if len(t) > 1 else 0.0
    frag = {}
    for tid, gg in df.groupby("track_id"):
        if tid not in ours_frag or tid not in eps:
            continue
        t = gg["time_s"].to_numpy(); mid = 0.5 * (t[0] + t[-1])
        per = 1 if HALF[1][0] <= mid <= HALF[1][1] else (2 if HALF[2][0] <= mid <= HALF[2][1] else 0)
        if per == 0:
            continue
        frag[tid] = dict(t0=float(t[0]), t1=float(t[-1]), c=contig(t), per=per,
                         x=float(np.nanmedian(gg["x_m"])), y=float(np.nanmedian(gg["y_m"])))

    # greedy assign: strongest fragments first; nearest on-field player template; no temporal overlap
    assigned_iv = {pid: [] for pid in squad}     # pid -> list of (t0,t1)
    named = {}                                    # frag -> (pid, margin)
    for tid in sorted(frag, key=lambda k: -frag[k]["c"]):
        f = frag[tid]; cands = []
        for pid in squad:
            tp = tmpl[f["per"]].get(pid)
            if tp is None or not _is_onfield(onfield, pid, f["t0"], f["t1"]):
                continue
            cands.append((np.hypot(f["x"] - tp[0], f["y"] - tp[1]), pid))
        cands.sort()
        for rank, (d, pid) in enumerate(cands):
            if any(not (f["t1"] < s or f["t0"] > e) for s, e in assigned_iv[pid]):
                continue  # temporal overlap with this player's existing frags
            margin = (cands[rank + 1][0] - d) if rank + 1 < len(cands) else 99.0
            assigned_iv[pid].append((f["t0"], f["t1"])); named[tid] = (pid, d, margin); break

    # report
    tot_c = sum(f["c"] for f in frag.values()); named_c = sum(frag[t]["c"] for t in named)
    print(f"ours clean fragments: {len(frag)}  | named: {len(named)} ({100*len(named)/max(1,len(frag)):.0f}%)  "
          f"| fragment-time named: {named_c:.0f}/{tot_c:.0f}s ({100*named_c/max(1,tot_c):.0f}%)")
    print(f"(baseline: pipeline per-window Hungarian named 344 tracks)\n")
    print(f"{'player':<14}{'#':>3}{'onfield_min':>12}{'named_min':>10}{'cover%':>8}{'lowmargin%':>11}")
    for pid in sorted(squad):
        of_s = sum(e - s for s, e in onfield.get(pid, []))
        ts = [t for t in named if named[t][0] == pid]
        nm = sum(frag[t]["c"] for t in ts)
        lowm = sum(frag[t]["c"] for t in ts if named[t][2] < 3.0)  # <3m margin = ambiguous
        cov = 100 * nm / of_s if of_s > 0 else 0
        nm_pct = 100 * lowm / nm if nm > 0 else 0
        r = roster.get(pid); nme = getattr(r, "name", pid) if r else pid
        print(f"{nme[:14]:<14}{str(getattr(r,'number','?'))[:3]:>3}{of_s/60:>12.1f}{nm/60:>10.1f}{cov:>7.0f}%{nm_pct:>10.0f}%")
    # global margin diagnostic
    margins = np.array([named[t][2] for t in named])
    print(f"\nassignment margin (2nd-best - best template dist): median={np.median(margins):.1f}m  "
          f"<3m (ambiguous)={100*(margins<3).mean():.0f}%  (low = template can't disambiguate same-zone players)")


if __name__ == "__main__":
    main()
