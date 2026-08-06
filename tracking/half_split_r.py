"""Per-player half-to-half repeatability (r) — THE score for identity changes.

Why this exists
---------------
`NAMING_BOTTLENECK.md` establishes that per-player accuracy is limited by
ATTRIBUTION, not by detection, geometry or metric math:

    same minutes, two independent samplings (odd vs even frames)  r = 0.984
    per-player, first half vs second half                         r = 0.004

Same footage, same math. The only difference is whether identity is involved.
So r = 0.98 is the achievable ceiling and per-player half-to-half r is the
score. **Coverage is NOT a valid score** — the VLM run raised named coverage
while r went 0.004 -> -0.027. Judging by coverage is why previous work went in
circles, and any change that "names more players" can hit 7/7 by construction.

The metric
----------
Per player, WORK RATE (m per tracked minute) in H1 vs H2, Pearson r across
players. A rate, not a sum, so a player tracked for 3 minutes of one half and
9 of the other is still comparable — otherwise r would mostly measure coverage.

The control
-----------
Every run also prints the odd/even-frame control on the SAME seconds: split
each half's detections into odd and even frames and correlate those two
independent samplings. That is measurement noise with identity held constant,
and it must reproduce ~0.98. If it does not, the instrument is wrong and the
headline r means nothing — the run says so and exits non-zero.

Read-only: re-runs stages 3-5 from cached checkpoints exactly like
tracking/eval_identity.py and never touches Firestore analytics.

Usage:
    set -a; source .env; set +a
    .venv-post-game/bin/python -m tracking.half_split_r --game-id mri01pvelv46d
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from post_game import config, firestore_io
from post_game.calibration import FieldProjector
from post_game.identity import (
    half_windows,
    period_clock_to_video_time_factory,
    _onfield_intervals,
)
from post_game.identity_assign import assign_identities_v2
from post_game.pipeline import _our_color
from post_game.reid_stitch import stitch_tracklets
from post_game.stats import compute_player_stats
from post_game.team_classifier import classify_tracks

OUT_DIR = Path(__file__).resolve().parent / "outputs" / "identity_eval"

# Below this many paired players an r is not reportable — with n=4 a single
# outlier swings it from -0.5 to +0.9. The doc's numbers are on ~9-12 players.
MIN_PAIRED_PLAYERS = 6
# The measured precision ceiling the odd/even control must reproduce.
CONTROL_R_EXPECTED = 0.984
CONTROL_R_TOLERANCE = 0.15


def _load_jersey_medians(npz_path: Path) -> dict[int, list]:
    """{track_id: [median_hsv]} — same reduction classify_tracks applies."""
    out: dict[int, list] = {}
    with np.load(npz_path, allow_pickle=True) as nz:
        for k in nz.files:
            samples = nz[k]
            if len(samples) == 0:
                continue
            stacked = np.vstack([np.asarray(s, dtype=np.float32) for s in samples])
            out[int(k)] = [np.median(stacked, axis=0)]
    return out


def _pearson(a: list[float], b: list[float]) -> float | None:
    """Pearson r, or None when undefined (n < 2 or a constant input)."""
    if len(a) < 2:
        return None
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        return None
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _work_rates(
    tracks_df: pd.DataFrame,
    identity_by_track: dict[int, str],
    window: tuple[float, float],
    *,
    L: float,
    W: float,
    fps: float,
    gk_player_id: str | None,
    onfield: dict[str, list[tuple[float, float]]],
    tracklet_of_track: dict[int, int],
    min_tracked_s: float,
) -> dict[str, float]:
    """{player_id: metres per tracked minute} within one time window.

    Restricting BOTH the frames and `periods` to a single half keeps
    compute_player_stats' per-half orientation logic self-consistent.
    """
    t0, t1 = window
    sub = tracks_df[(tracks_df["time_s"] >= t0) & (tracks_df["time_s"] <= t1)]
    if sub.empty:
        return {}
    stats = compute_player_stats(
        sub,
        identity_by_track,
        field_length_m=L,
        field_width_m=W,
        fps_after_sample=fps,
        periods=[window],
        gk_player_id=gk_player_id,
        onfield_intervals=onfield,
        tracklet_of_track=tracklet_of_track,
    )
    out: dict[str, float] = {}
    for s in stats:
        # Thin slivers produce wild rates off a couple of noisy steps; they are
        # not evidence either way and would dominate an r over ~10 players.
        if s.tracked_seconds < min_tracked_s:
            continue
        out[str(s.player_id)] = float(s.distance_m) / (s.tracked_seconds / 60.0)
    return out


def _paired(a: dict[str, float], b: dict[str, float]) -> tuple[list[str], list[float], list[float]]:
    ids = sorted(set(a) & set(b))
    return ids, [a[i] for i in ids], [b[i] for i in ids]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-id", required=True)
    ap.add_argument("--label", default="baseline", help="output filename stem")
    ap.add_argument("--min-tracked-s", type=float, default=60.0,
                    help="ignore a player in a half with less tracked time than this")
    ap.add_argument("--onfield-tolerance-s", type=float, default=None,
                    help="override ID_ONFIELD_TOLERANCE_S for this run (sweep knob)")
    ap.add_argument("--halftime-split", action="store_true",
                    help="enforce that no track spans halftime, cutting at the "
                         "break DETECTED in the footage (falls back to the coach's "
                         "logged break if detection is inconclusive)")
    ap.add_argument("--gap-split", type=float, default=None, metavar="SECONDS",
                    help="split each track at internal gaps longer than this before "
                         "classify+stitch (pipeline stage 3.5, normally off). Uses a "
                         "SEPARATE stage-4 cache so the baseline cache is untouched.")
    args = ap.parse_args()

    if args.onfield_tolerance_s is not None:
        # Set before assignment so identity_assign reads the swept value.
        config.ID_ONFIELD_TOLERANCE_S = float(args.onfield_tolerance_s)

    game = firestore_io.get_game(args.game_id)
    roster = firestore_io.get_roster()
    field_cal = firestore_io.get_game_calibration(args.game_id)
    if field_cal is None:
        raise SystemExit("No calibration on game doc — can't project to field.")
    name_of = {r.id: r.name for r in roster}
    L, W = field_cal.length_m, field_cal.width_m

    # --- stages 3-4, sharing eval_identity's cache (identical construction) ---
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # A gap-split run reclassifies and re-stitches a DIFFERENT track universe, so
    # it must not read or overwrite the baseline stage-4 cache.
    _sfx = "" if args.gap_split is None else f".gs{args.gap_split:g}"
    if args.halftime_split:
        _sfx += ".hts"
    s4_parquet = OUT_DIR / f"{args.game_id}.stage4{_sfx}.parquet"
    s4_maps = OUT_DIR / f"{args.game_id}.stage4{_sfx}.json"
    if s4_parquet.exists() and s4_maps.exists():
        tracks_df = pd.read_parquet(s4_parquet)
        maps = json.loads(s4_maps.read_text())
        team_of_track = {int(k): v for k, v in maps["team_of_track"].items()}
        tracklet_of_track = {int(k): v for k, v in maps["tracklet_of_track"].items()}
        print(f"stage-4 cache: {len(tracks_df)} detections, "
              f"{tracks_df['track_id'].nunique()} tracks")
    else:
        ckpt = config.OUTPUTS_DIR / args.game_id
        tracks_df = pd.read_parquet(ckpt / "tracks_raw.parquet")
        jersey = _load_jersey_medians(ckpt / "jersey_samples.npz")
        embeddings = {}
        if (ckpt / "embeddings.npz").exists():
            with np.load(ckpt / "embeddings.npz", allow_pickle=True) as nz:
                embeddings = {int(k): np.asarray(nz[k], dtype=np.float32) for k in nz.files}
        projector = FieldProjector(field_cal)
        xy = projector.pixel_to_field_batch(tracks_df[["foot_x_eq", "foot_y_eq"]].to_numpy())
        tracks_df["x_m"], tracks_df["y_m"] = xy[:, 0], xy[:, 1]
        on_field = ((tracks_df["x_m"] >= -1.5) & (tracks_df["x_m"] <= L + 1.5)
                    & (tracks_df["y_m"] >= -1.5) & (tracks_df["y_m"] <= W + 1.5))
        tracks_df = tracks_df.loc[on_field].reset_index(drop=True)
        lifetime = tracks_df.groupby("track_id").size().rename("track_lifetime")
        tracks_df = tracks_df.merge(lifetime, on="track_id")
        score = tracks_df["track_lifetime"].astype(float)
        if "conf" in tracks_df.columns:
            score = score * tracks_df["conf"].astype(float).clip(lower=0.1)
        tracks_df["_rank_score"] = score
        ranked = tracks_df.sort_values(["frame", "_rank_score"], ascending=[True, False])
        tracks_df = (ranked.groupby("frame", group_keys=False).head(20)
                     .drop(columns=["_rank_score", "track_lifetime"]).reset_index(drop=True))

        # Stage 3.5 (pipeline.py:490-499), normally OFF. BoT-SORT keeps a track id
        # alive across long gaps, so a single track_id can span halftime and weld
        # two different children together — measured at 71-92% of our tracked time.
        # Splitting at internal gaps rebuilds a clean track universe before
        # classify+stitch, exactly as the pipeline would.
        if args.gap_split is not None:
            from post_game.gap_split import gap_split_tracks
            _n0 = tracks_df["track_id"].nunique()
            tracks_df, jersey, embeddings, _ = gap_split_tracks(
                tracks_df, jersey, embeddings, split_gap_s=float(args.gap_split))
            print(f"gap-split @ {args.gap_split:g}s: {_n0} tracks -> "
                  f"{tracks_df['track_id'].nunique()} sub-tracks")

        # A player cannot be one continuous body across the break, so any track
        # that survives it welds two children together. Cut at the break found in
        # the FOOTAGE — the coach's tap rides on the game clock, which is offset
        # from video time by the kickoff-sync error.
        if args.halftime_split:
            from post_game.halftime_split import (detect_halftime_break,
                                                  split_tracks_at_halftime)
            _hw = half_windows(game, float(tracks_df["time_s"].max()) + 1.0)
            _logged = (_hw[0][1], _hw[1][0]) if len(_hw) >= 2 else None
            _brk = detect_halftime_break(tracks_df, logged_break=_logged)
            if _brk is None and _logged is not None:
                print(f"halftime: detection inconclusive — using logged "
                      f"{_logged[0]:.0f}..{_logged[1]:.0f}s")
                _brk = _logged
            if _brk is not None:
                if _logged is not None:
                    print(f"halftime: logged {_logged[0]:.0f}..{_logged[1]:.0f}s "
                          f"-> using {_brk[0]:.0f}..{_brk[1]:.0f}s "
                          f"({_brk[0] - _logged[0]:+.0f}s start)")
                _n0 = tracks_df["track_id"].nunique()
                tracks_df, jersey, embeddings, _p = split_tracks_at_halftime(
                    tracks_df, _brk, jersey, embeddings)
                print(f"halftime split: {_n0} tracks -> "
                      f"{tracks_df['track_id'].nunique()} ({len(_p)} cut)")

        team_of_track = classify_tracks(
            tracks_df, jersey,
            our_home_color_hex=_our_color(game),
            opp_color_hex=game.away_color,
            ref_color_hex=game.ref_color,
        )
        tracklet_of_track = stitch_tracklets(
            tracks_df, team_of_track,
            track_embeddings=embeddings, track_jersey_samples=jersey,
        )
        keep_cols = [c for c in ("track_id", "frame", "time_s", "x_m", "y_m", "conf")
                     if c in tracks_df.columns]
        tracks_df = tracks_df[keep_cols]
        tracks_df.to_parquet(s4_parquet)
        s4_maps.write_text(json.dumps({
            "team_of_track": {str(k): int(v) for k, v in team_of_track.items()},
            "tracklet_of_track": {str(k): int(v) for k, v in tracklet_of_track.items()},
        }))
        print(f"stage-4 cache written: {s4_parquet.name}")

    duration_s = float(tracks_df["time_s"].max()) + 1.0
    play_windows = half_windows(game, duration_s)
    clock_to_video = period_clock_to_video_time_factory(game)
    onfield = _onfield_intervals(game.starting_lineup, game.events, clock_to_video,
                                 video_end_s=duration_s)

    # --- stage 5: AUTO assignment, coach overrides WITHHELD -------------------
    assignments = assign_identities_v2(
        tracks_df=tracks_df,
        tracklet_of_track=tracklet_of_track,
        team_of_track=team_of_track,
        events=game.events,
        roster=roster,
        starting_lineup=game.starting_lineup,
        gk_player_id=game.gk_player_id,
        period_clock_to_video_time=clock_to_video,
        periods_video=play_windows,
        field_length_m=L,
        field_width_m=W,
        overrides=None,
        squad=game.squad,
    )
    identity_by_track = {a.track_id: a.player_id for a in assignments if a.player_id}

    # Detection cadence -> the fps compute_player_stats should assume.
    _dts = (tracks_df.sort_values(["track_id", "time_s"])
            .groupby("track_id")["time_s"].diff().dropna())
    dt_med = float(_dts[_dts > 0].median()) if len(_dts) else 0.1
    fps = 1.0 / dt_med if dt_med > 0 else 10.0

    if len(play_windows) < 2:
        raise SystemExit("Need two halves to compute half-to-half r.")

    common = dict(L=L, W=W, fps=fps, gk_player_id=game.gk_player_id,
                  onfield=onfield, tracklet_of_track=tracklet_of_track,
                  min_tracked_s=args.min_tracked_s)

    # --- headline: per-player work rate, H1 vs H2 -----------------------------
    h1 = _work_rates(tracks_df, identity_by_track, play_windows[0], **common)
    h2 = _work_rates(tracks_df, identity_by_track, play_windows[1], **common)
    ids, a, b = _paired(h1, h2)
    r_half = _pearson(a, b)

    # --- control: odd vs even frames on the SAME seconds ----------------------
    # Identity held constant; only the sampling differs. This is measurement
    # noise, and it is the ceiling the headline is chasing.
    if "frame" in tracks_df.columns:
        parity = tracks_df["frame"].astype(int) % 2
    else:  # fall back to sample index within each track
        parity = tracks_df.groupby("track_id").cumcount() % 2
    ctrl_pairs: list[tuple[float, float]] = []
    for win in play_windows[:2]:
        odd = _work_rates(tracks_df[parity == 1], identity_by_track, win, **common)
        even = _work_rates(tracks_df[parity == 0], identity_by_track, win, **common)
        _cids, ca, cb = _paired(odd, even)
        ctrl_pairs.extend(zip(ca, cb))
    r_ctrl = _pearson([p[0] for p in ctrl_pairs], [p[1] for p in ctrl_pairs])

    # --- report ---------------------------------------------------------------
    def _fmt(r):
        return "n/a" if r is None else f"{r:+.3f}"

    print()
    print(f"=== half-to-half repeatability — {args.game_id} ({args.label}) ===")
    tol = getattr(config, "ID_ONFIELD_TOLERANCE_S", None)
    if tol is not None:
        print(f"on-field tolerance: {float(tol):.0f} s")
    print(f"control  odd/even, same minutes : r = {_fmt(r_ctrl)}  (n={len(ctrl_pairs)} player-halves)")
    print(f"HEADLINE per-player H1 vs H2    : r = {_fmt(r_half)}  (n={len(ids)} players)")
    print()
    print(f"{'player':<22}{'H1 m/min':>10}{'H2 m/min':>10}{'delta':>9}")
    for pid, x, y in sorted(zip(ids, a, b), key=lambda t: -abs(t[2] - t[1])):
        print(f"{(name_of.get(pid) or pid)[:21]:<22}{x:>10.1f}{y:>10.1f}{y - x:>+9.1f}")

    # `players` is what makes an honest before/after possible: two runs can only
    # be compared on the players BOTH named. A change that drops the hard-to-name
    # children raises r purely by composition — measured on gap-split, which
    # looked like +0.396 -> +0.572 until the common-player subset showed
    # +0.733 -> +0.370. Always diff on this list, never on the headline alone.
    result = {
        "game_id": args.game_id,
        "label": args.label,
        "players": ids,
        "onfield_tolerance_s": (float(tol) if tol is not None else None),
        "min_tracked_s": args.min_tracked_s,
        "r_half_to_half": r_half,
        "n_players": len(ids),
        "r_control_odd_even": r_ctrl,
        "n_control_pairs": len(ctrl_pairs),
        "work_rate_h1": {p: round(v, 2) for p, v in sorted(h1.items())},
        "work_rate_h2": {p: round(v, 2) for p, v in sorted(h2.items())},
    }
    out_path = OUT_DIR / f"{args.game_id}.halfr.{args.label}.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(f"\nwrote {out_path}")

    # --- instrument self-check ------------------------------------------------
    # A headline r is only meaningful if the control reproduces the known
    # ceiling. Fail loudly rather than let a broken harness look like a result.
    problems = []
    if len(ids) < MIN_PAIRED_PLAYERS:
        problems.append(f"only {len(ids)} paired players (need >= {MIN_PAIRED_PLAYERS})")
    if r_ctrl is None:
        problems.append("control r undefined")
    elif abs(r_ctrl - CONTROL_R_EXPECTED) > CONTROL_R_TOLERANCE:
        problems.append(f"control r {r_ctrl:+.3f} is far from the measured "
                        f"ceiling {CONTROL_R_EXPECTED:+.3f} — instrument suspect")
    if problems:
        print("\nINSTRUMENT CHECK FAILED:")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(2)
    print("\ninstrument check OK — control reproduces the precision ceiling.")


if __name__ == "__main__":
    main()
