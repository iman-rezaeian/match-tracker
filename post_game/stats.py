"""Per-player physical + spatial stats (Tier A)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import config

log = logging.getLogger(__name__)


@dataclass
class PlayerStats:
    player_id: str
    minutes_played: float
    distance_m: float
    top_speed_ms: float
    avg_speed_ms: float
    sprint_count: int
    sprint_distance_m: float
    pct_attacking_third: float
    pct_middle_third: float
    pct_defensive_third: float
    heatmap_grid: list[list[int]]
    work_rate_timeline: list[float]
    # --- Rate-based estimates (plan 4.4) -------------------------------
    # Tracked coverage is systematically UNEQUAL across players, so the raw
    # sums above are biased between players, not just scaled down. Headline
    # numbers are therefore rate × coach-logged minutes; the raw sums stay
    # for the 8K before/after comparison. Estimates fall back to the raw
    # value when tracked time is too thin to trust a rate (< 3 tracked min).
    tracked_seconds: float = 0.0          # actual time with detections (real steps)
    distance_est_m: float = 0.0           # (distance_m / tracked_min) × coach_min, mult-capped
    sprint_est_count: int = 0             # (sprint_count / tracked_min) × coach_min, mult-capped
    # Fraction of coach-logged minutes we actually tracked (tracked_min/coach_min).
    # THE trust dial for distance_est_m: ≳0.5 is solid, <0.25 is a sliver.
    coverage_frac: float = 0.0
    # True when the coverage-fraction cap held distance_est_m below the naive
    # coach_min/tracked_min extrapolation — i.e. an indicative, not measured, number.
    dist_est_capped: bool = False
    # Personalized sprint threshold actually used for THIS game (plan 4.5).
    sprint_threshold_ms: float = 0.0
    # Fraction of inter-detection steps that exceeded the physical speed cap —
    # i.e. tracking artifacts (swap teleports / projection jumps / concurrent-
    # tracklet ping-pong). A clean track is ~0; a swap-polluted one is high.
    # The UI uses this (not "top speed == cap") to flag unreliable movement.
    implausible_step_frac: float = 0.0
    # --- MEASUREMENT ONLY (2026-08-18): statue-aware coverage --------------
    # coverage_frac counts statue time (standers welded into the identity) as
    # tracked time. These two fields measure what coverage WOULD be with
    # statue steps excluded, so the semantics change can be decided on data.
    # NOTHING consumes them yet — the parentSeason gate and the dist_est
    # rate math still run on coverage_frac.
    coverage_frac_statue_aware: float = 0.0
    statue_frac_of_tracked: float = 0.0


def _smooth(arr: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(arr) < window:
        return arr.astype(float)
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="same")


def _smooth_edge_safe(arr: np.ndarray, window: int) -> np.ndarray:
    """Boxcar that does NOT distort the ends — required for POSITION.

    `_smooth` uses np.convolve(mode="same"), which ZERO-PADS: a player standing
    still at x=10 m is smoothed to 6 m at the first sample and 8 m at the second,
    i.e. the pad invents a 4 m displacement at every run boundary. Harmless-ish on
    a speed series (it dips toward 0), catastrophic on a coordinate that is then
    differenced into distance — it made total distance rise from 86 km to 249 km.
    Here the signal is edge-replicated before convolving, so a constant input is
    returned unchanged.
    """
    if window <= 1 or len(arr) < 3:
        return arr.astype(float)
    w = min(int(window), len(arr))
    if w % 2 == 0:                      # need an odd window for a centred mean
        w -= 1
    if w <= 1:
        return arr.astype(float)
    pad = w // 2
    padded = np.concatenate([np.full(pad, arr[0], dtype=float),
                             arr.astype(float),
                             np.full(pad, arr[-1], dtype=float)])
    return np.convolve(padded, np.ones(w) / w, mode="valid")


def _drop_offwindow(df: pd.DataFrame,
                    onfield: dict[str, list[tuple[float, float]]],
                    report: dict[str, dict]) -> pd.DataFrame:
    """Drop detections credited to a player OUTSIDE his own on-field window.

    The coach's SUB taps are independent evidence the software never produced: a
    human recorded when each child came on and off. Measured on W8, **30.0% of all
    attributed detections (40,032 of 133,637) fall in minutes that log says the
    player was on the BENCH** — Qian 50.6%, Rezaeian 44.0%, Hahn 43.7% — so his
    "running" included another child's. The control validates the test: Garland,
    who never subs off and therefore cannot leak, measures exactly 0.0%.

    This is a pure attribution error, independent of the physics conflict check
    (which only catches a player in two places SIMULTANEOUSLY; a track that is
    simply the wrong child at a time nobody else was named is invisible to it).

    Note the asymmetry that makes this safe: `coach_min` — the coverage
    denominator and extrapolation base — comes from the SAME SUB taps and is NOT
    touched here. Only the numerator shrinks, so coverage correctly falls: that
    time was never really tracked for this player.
    """
    if not onfield:
        return df
    keep = np.zeros(len(df), dtype=bool)
    pid_col = df["player_id"].astype(str).to_numpy()
    t_col = df["time_s"].astype(float).to_numpy()
    for pid in np.unique(pid_col):
        ivs = onfield.get(str(pid))
        sel = pid_col == pid
        if not ivs:
            keep |= sel          # no logged window (coach never logged him) → keep
            continue
        inside = np.zeros(len(df), dtype=bool)
        for (lo, hi) in ivs:
            inside |= sel & (t_col >= lo) & (t_col <= hi)
        n_out = int((sel & ~inside).sum())
        if n_out:
            n_tot = int(sel.sum())
            r = report.setdefault(str(pid), {})
            r["offwindow_detections"] = n_out
            r["offwindow_frac"] = round(n_out / max(1, n_tot), 3)
        keep |= inside
    n_drop = len(df) - int(keep.sum())
    if n_drop:
        log.warning(
            "  stats: dropped %d of %d attributed detections credited OUTSIDE the "
            "player's own on-field window (coach SUB taps say he was on the bench) "
            "— %.1f%% of attributed time was another child's",
            n_drop, len(df), 100.0 * n_drop / max(1, len(df)))
    return df[keep]


def _per_player_trajectory(
    tracks_field_df: pd.DataFrame, identity_by_track: dict[int, str],
    tracklet_of_track: Optional[dict[int, int]] = None,
    onfield_intervals: Optional[dict[str, list[tuple[float, float]]]] = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    """Per-player trajectory rows, with physically impossible instants REMOVED.

    A merge in the tracker/stitcher can put two different children under one
    identity. Then the same player appears in two places at the same instant —
    measured at 12.2% of player-frames on W8, a median 15.2 m apart — and every
    stat downstream silently averages a stranger's running into his. Nothing in
    the data can say WHICH of the two is really him (appearance is a coin flip on
    same-kit teammates), so guessing would just relabel the corruption; instead
    those instants are dropped and reported. Returns (trajectories, conflict
    report keyed by player).
    """
    from .player_conflicts import (find_conflicts, conflict_summary,
                                   conflicted_times, blame_tracks)

    df = tracks_field_df.copy()
    df["player_id"] = df["track_id"].map(identity_by_track)
    df = df[df["player_id"].notna()]

    # Attribution first: a detection credited to a player while the coach's SUB
    # taps say he was on the bench is simply the wrong child, whatever else is
    # true of it. Removing those BEFORE the physics check keeps the conflict
    # numbers about genuine simultaneous-presence merges rather than bench rows.
    report: dict[str, dict] = {}
    if onfield_intervals:
        df = _drop_offwindow(df, onfield_intervals, report)

    samples = list(zip(df["player_id"].astype(str), df["time_s"].astype(float),
                       df["track_id"].astype(int),
                       df["x_m"].astype(float), df["y_m"].astype(float)))
    conflicts = find_conflicts(samples)
    if conflicts:
        summ = conflict_summary(conflicts)
        blame = blame_tracks(samples, conflicts)
        for pid, s in summ.items():
            s["worst_tracks"] = [{"track_id": t, "instants": n}
                                 for t, n in blame.get(pid, [])]
            # Also blame at TRACKLET level: the coach reviews stitched tracklets in
            # FIX-IDS, not raw tracks, so a track_id alone can't be surfaced there.
            if tracklet_of_track:
                per_tl: dict[int, int] = {}
                for t, n in blame.get(pid, []):
                    r = tracklet_of_track.get(int(t))
                    if r is not None:
                        per_tl[int(r)] = per_tl.get(int(r), 0) + n
                s["worst_tracklets"] = [
                    {"tracklet_id": r, "instants": n}
                    for r, n in sorted(per_tl.items(), key=lambda kv: -kv[1])]
            report.setdefault(pid, {}).update(s)   # keep any offwindow_* keys
        # Vectorized drop: build the (player, time) pairs to remove once and use
        # a MultiIndex membership test — a row-wise apply over ~500k rows is slow.
        bad = conflicted_times(conflicts)
        bad_pairs = {(pid, t) for pid, ts in bad.items() for t in ts}
        n_before = len(df)
        if bad_pairs:
            keys = pd.MultiIndex.from_arrays(
                [df["player_id"].astype(str), df["time_s"].astype(float)])
            df = df[~keys.isin(bad_pairs)]
        log.warning(
            "  stats: dropped %d of %d player-detections as physically impossible "
            "(same player in 2+ places >1.5m apart) across %d player(s) — these "
            "are tracker/stitch merges; excluded from distance/speed/heatmap",
            n_before - len(df), n_before, len(report))

    out: dict[str, pd.DataFrame] = {}
    for pid, sub in df.groupby("player_id"):
        sub = sub.sort_values("time_s").reset_index(drop=True)
        out[str(pid)] = sub
    return out, report


def compute_player_stats(
    tracks_field_df: pd.DataFrame,
    identity_by_track: dict[int, str],
    field_length_m: float,
    field_width_m: float,
    fps_after_sample: float,
    we_attack_right: bool = True,
    heatmap_grid_shape: tuple[int, int] = (12, 8),
    periods: Optional[list[tuple[float, float]]] = None,
    gk_player_id: Optional[str] = None,
    played_minutes: Optional[dict[str, float]] = None,
    sprint_thresholds: Optional[dict[str, float]] = None,
    conflicts_out: Optional[dict[str, dict]] = None,
    tracklet_of_track: Optional[dict[int, int]] = None,
    onfield_intervals: Optional[dict[str, list[tuple[float, float]]]] = None,
) -> list[PlayerStats]:
    """`conflicts_out`: if given, filled with the per-player physically-impossible
    (same player in 2+ places) report — see player_conflicts. Those instants are
    excluded from every stat below rather than silently averaged in."""
    per_player, conflict_report = _per_player_trajectory(
        tracks_field_df, identity_by_track, tracklet_of_track, onfield_intervals)
    if conflicts_out is not None:
        conflicts_out.update(conflict_report)
    third_low, third_high = config.THIRDS_FRACTIONS
    boundaries_x = (field_length_m * third_low, field_length_m * third_high)

    # Canonical orientation per half so the heatmap + thirds always read
    # "our net at the bottom, opponent net at the top", consistent across halves
    # (teams switch ends at the break = a 180° rotation, so we flip BOTH the
    # depth and width axes). The anchor is the GK: our net is whichever end the
    # keeper guards that half. Falls back to `we_attack_right` if no GK data.
    def _period_of(ts: float) -> int:
        for i, (a, b) in enumerate(periods or [], start=1):
            if a <= ts <= b:
                return i
        return 1

    # The old anchor was the NAMED keeper's median x, which is circular (it depends
    # on the identity we're trying to trust) and was measurably broken: on W8 the
    # keeper's named track spans x = 1.6 -> 31.7 m within one half, which no keeper
    # does, so the median landed mid-pitch and the sign was a coin flip. A wrong
    # sign MIRRORS every heatmap and swaps attacking/defensive third.
    #
    # Replaced by an IDENTITY-FREE anchor: someone stands in front of a goal for
    # essentially the whole half, and that is our keeper. Count, per half and per
    # end, the number of distinct seconds with a body parked in the central
    # goal-mouth band. Measured on W8: H1 end0 1461 s vs endL 1231 s (weak, 1.19x);
    # H2 end0 737 s vs endL 1474/1505 s = 98% of the half (decisive, 2.00x). Take
    # the most confident half and ALTERNATE (teams switch at the break), which
    # agrees with an independent derivation from team positional mass.
    our_net_at_x0: dict[int, bool] = {}
    orientation_confidence: dict[int, float] = {}
    _per = list(periods or [(0.0, 1e12)])
    _all = tracks_field_df
    _band = ((_all["y_m"] > field_width_m * 0.25) & (_all["y_m"] < field_width_m * 0.75))
    _votes: dict[int, tuple[int, int]] = {}
    for pi, (a, b) in enumerate(_per, start=1):
        _s = _all[(_all["time_s"] >= a) & (_all["time_s"] <= b) & _band]
        if _s.empty:
            continue
        s0 = _s.loc[_s["x_m"] < 6.0, "time_s"].round(0).nunique()
        sL = _s.loc[_s["x_m"] > field_length_m - 6.0, "time_s"].round(0).nunique()
        if s0 or sL:
            _votes[pi] = (int(s0), int(sL))
    if _votes:
        def _strength(v):
            return max(v) / max(1, min(v))
        _best = max(_votes, key=lambda k: _strength(_votes[k]))
        _s0, _sL = _votes[_best]
        _anchor = _s0 > _sL          # our net at x=0 in the anchor period
        for pi in range(1, len(_per) + 1):
            our_net_at_x0[pi] = _anchor if ((pi - _best) % 2 == 0) else (not _anchor)
            orientation_confidence[pi] = round(_strength(_votes.get(pi, (1, 1))), 2)
        if _strength(_votes[_best]) < config.ORIENT_MIN_CONFIDENCE:
            log.warning("  stats: pitch orientation is AMBIGUOUS (best half only "
                        "%.2fx goal-occupancy) — heatmaps/thirds may be mirrored",
                        _strength(_votes[_best]))
        else:
            log.info("  stats: pitch orientation anchored on period %d (%.2fx "
                     "goal-occupancy), halves alternate", _best, _strength(_votes[_best]))
    else:
        for pi in range(1, len(_per) + 1):
            our_net_at_x0[pi] = bool(we_attack_right)   # last-resort fallback
            orientation_confidence[pi] = 0.0

    out: list[PlayerStats] = []
    for pid, sub in per_player.items():
        if len(sub) < 5:
            continue
        x = sub["x_m"].to_numpy()
        y = sub["y_m"].to_numpy()
        t = sub["time_s"].to_numpy()
        # Smooth POSITION before integrating distance. Distance was a raw sum of
        # per-frame steps, and each step carries projection jitter that never
        # cancels — it always adds length. Measured on W8 across 7,121 windows
        # where a player barely moved (net 0.18 m over 2 s), the summed path was
        # 1.27 m: a 7.2x over-count, i.e. a STANDING player is credited ~1.3 m of
        # running every 2 seconds. The existing 5-tap boxcar was applied to SPEED
        # only, which fixes the reported rate but not the integral.
        # Smoothing runs WITHIN contiguous runs only: bridging a multi-second gap
        # would invent a straight-line path across unobserved time.
        if config.DIST_POS_SMOOTH_WINDOW > 1 and len(t) >= 3:
            _gapped = np.diff(t) > config.DIST_POS_SMOOTH_MAX_GAP_S
            _run = np.concatenate([[0], np.cumsum(_gapped)])
            xs, ys = x.astype(float).copy(), y.astype(float).copy()
            for _r in np.unique(_run):
                _m = _run == _r
                if int(_m.sum()) >= 3:
                    xs[_m] = _smooth_edge_safe(x[_m], config.DIST_POS_SMOOTH_WINDOW)
                    ys[_m] = _smooth_edge_safe(y[_m], config.DIST_POS_SMOOTH_WINDOW)
            x, y = xs, ys
        dt = np.diff(t)
        # Floor dt at a realistic frame interval (not 1ms) so a near-zero gap
        # can't manufacture an enormous speed; cap large gaps at 2s.
        med_dt = float(np.median(dt)) if len(dt) else 0.2
        dt = np.clip(dt, max(0.04, 0.5 * med_dt), 2.0)
        dx = np.diff(x)
        dy = np.diff(y)
        raw_seg = np.sqrt(dx * dx + dy * dy)
        # A step above the physical cap (MAX_PLAUSIBLE_SPEED_MS) is NOT real
        # motion — it's a tracking artifact: an identity-swap teleport, a
        # far-side projection jump, or a ping-pong between two concurrent
        # tracklets assigned to the same player. The OLD code CLAMPED these to
        # the cap, which (a) inflated distance by adding cap×dt of fake travel
        # and (b) pinned top speed at exactly the cap for ANY player with >1%
        # artifact steps — which then tripped the UI's "inflated" gate and HID
        # otherwise-good stats. Treat an artifact step as a gap instead: zero
        # distance, zero speed, and it breaks sprint runs. `implausible_frac`
        # (how polluted the track is) is what the UI gates on now, not the cap.
        cap_dist = config.MAX_PLAUSIBLE_SPEED_MS * dt
        teleport = raw_seg > cap_dist
        implausible_frac = float(teleport.mean()) if len(teleport) else 0.0
        seg_dist = np.where(teleport, 0.0, raw_seg)
        speed = np.where(teleport, 0.0, raw_seg / dt)  # real speeds, all <= cap
        speed_s = _smooth(speed, config.SPEED_SMOOTH_WINDOW)
        # A teleport step is a GAP, not standstill: the player wasn't observed
        # moving slowly, they weren't cleanly observed at all. So its dt must not
        # count as "tracked time", and its zeroed speed must not drag down the
        # average. Keeping them (the old behaviour) deflated tracked_s, avg_speed,
        # work-rate, and the dist_est rate by up to ~implausible_frac (e.g. ~37%
        # for a heavily swap-polluted track). `real` masks the true-motion steps.
        real = ~teleport
        real_speed = speed[real]        # unsmoothed real-step speeds (for the mean)
        real_speed_s = speed_s[real]    # smoothed, real steps only (for the p99)

        # Sprints: continuous run above threshold for >= 0.5s. The threshold
        # is personalized when season history exists (plan 4.5) — a fixed bar
        # over-counts the fastest kids and ignores max-effort runs by slower
        # ones. Falls back to the fixed config value for new players.
        sprint_thr = float((sprint_thresholds or {}).get(str(pid), config.SPRINT_THRESHOLD_MS))
        is_sprint = speed_s >= sprint_thr
        sprint_count = 0
        sprint_dist = 0.0
        in_run = False
        run_dist = 0.0
        run_duration = 0.0
        for i, flag in enumerate(is_sprint):
            if flag:
                in_run = True
                run_dist += seg_dist[i]
                run_duration += dt[i]
            else:
                if in_run and run_duration >= 0.5:
                    sprint_count += 1
                    sprint_dist += run_dist
                in_run = False
                run_dist = 0.0
                run_duration = 0.0
        if in_run and run_duration >= 0.5:
            sprint_count += 1
            sprint_dist += run_dist

        # Canonical per-half coords: d = depth from OUR net (0 = our net,
        # L = opponent net), w = consistent left/right. Flip both axes (180°)
        # for halves where our net is at x=L, so a left-back reads bottom-left
        # in both halves.
        net_x0 = np.array([our_net_at_x0.get(_period_of(tt), True) for tt in t])
        d = np.where(net_x0, x, field_length_m - x)
        w = np.where(net_x0, y, field_width_m - y)

        # Thirds along the canonical depth axis (attacking = toward opponent net).
        att = d >= boundaries_x[1]
        mid = (d >= boundaries_x[0]) & (d < boundaries_x[1])
        dfn = d < boundaries_x[0]

        # Statue guard: a track that spends 3+ minutes inside a ~3 m circle is
        # a stander (waiting sub, coach, touchline figure) mis-attributed to
        # this player, not the player himself — on-field windows can't catch
        # it because it happens DURING his play time. Excluded from the grid
        # only; a genuinely parked cell from real play survives because real
        # kids' tracks never hold a 3 m circle that long.
        _tids = sub["track_id"].to_numpy()
        _statue = np.zeros(len(sub), dtype=bool)
        for _tid in np.unique(_tids):
            _m = _tids == _tid
            if int(_m.sum()) < 10:
                continue
            _dur = float(t[_m].max() - t[_m].min())
            if _dur < config.STATUE_MIN_DURATION_S:
                continue
            _cx, _cy = float(np.median(x[_m])), float(np.median(y[_m]))
            _rad = float(np.percentile(np.hypot(x[_m] - _cx, y[_m] - _cy), 95))
            if _rad <= config.STATUE_MAX_RADIUS_M:
                _statue |= _m
        # Spot-dwell pass: fragmentation shatters a long stander into many
        # short tracks the loop above can't see, so also flag any fine spatial
        # cell holding an implausible cumulative dwell for THIS player.
        _cell = config.STATUE_SPOT_CELL_M
        _key = (np.clip((x / _cell).astype(int), 0, 10_000) * 100_000
                + np.clip((y / _cell).astype(int), 0, 10_000))
        _dts = np.concatenate([[float(np.median(dt)) if len(dt) else 0.2], dt])
        _uniq, _inv = np.unique(_key, return_inverse=True)
        _dwell = np.bincount(_inv, weights=_dts)
        _hot = _dwell[_inv] >= config.STATUE_SPOT_DWELL_S
        _statue |= _hot
        if _statue.any():
            log.warning(
                "  stats: %s heatmap drops %d statue samples (%.0f%%) — "
                "stationary track(s) parked %.1f+ min inside %.1f m",
                pid, int(_statue.sum()), 100.0 * _statue.mean(),
                config.STATUE_MIN_DURATION_S / 60.0, config.STATUE_MAX_RADIUS_M)

        # Heatmap grid in canonical coords: row 0 = our-net end, last row =
        # opponent-net end; col 0 .. last = consistent left → right. The UI
        # renders row 0 at the BOTTOM (our net).
        gh, gw = heatmap_grid_shape
        _live = ~_statue
        grid = np.histogram2d(
            d[_live], w[_live], bins=[gh, gw],
            range=[[0, field_length_m], [0, field_width_m]],
        )[0].astype(int).tolist()

        minutes = int(np.ceil((t[-1] - t[0]) / 60.0)) if t[-1] > t[0] else 1
        rate = []
        for m in range(minutes):
            lo, hi = t[0] + m * 60, t[0] + (m + 1) * 60
            # Restrict each minute's work-rate to real-motion steps so a minute
            # polluted by teleport gaps isn't reported as low effort.
            mask = (t[:-1] >= lo) & (t[:-1] < hi) & real
            rate.append(float(np.mean(speed_s[mask])) if mask.any() else 0.0)

        coach_min = float((played_minutes or {}).get(str(pid), (t[-1] - t[0]) / 60.0))
        dist_raw = float(seg_dist.sum())
        # tracked_s counts only REAL-motion steps (teleport gaps excluded) so the
        # per-tracked-minute rates below aren't diluted by unobserved time.
        tracked_s = float(dt[real].sum())
        tracked_min = tracked_s / 60.0
        # Coverage = fraction of the coach-logged minutes we actually tracked.
        # This is THE trust dial for the rate-based estimates below.
        coverage_frac = (tracked_min / coach_min) if coach_min > 0 else 0.0
        # Measurement-only twin: coverage with statue steps excluded (a step
        # counts as statue when BOTH endpoints are statue samples). See the
        # PlayerStats field comment — nothing consumes these yet.
        _step_statue = _statue[1:] & _statue[:-1]
        _tracked_s_sa = float(dt[real & ~_step_statue].sum())
        statue_frac = ((tracked_s - _tracked_s_sa) / tracked_s) if tracked_s > 0 else 0.0
        coverage_statue_aware = ((_tracked_s_sa / 60.0) / coach_min) if coach_min > 0 else 0.0
        # Rate-based estimates (plan 4.4): scale per-tracked-minute rates to
        # coach-logged minutes. Two guards make the estimate honest:
        #   1. Absolute floor (>= 3 tracked min): below a sliver the rate itself
        #      is a coin flip, so keep the raw sum.
        #   2. Coverage-FRACTION cap: the naive multiplier is coach_min/tracked_min,
        #      which at low coverage explodes (Zaidan: 19% coverage ⇒ ×5.3, 528 m →
        #      2794 m — a number the data cannot support and which reads as fact in
        #      the UI). Cap the extrapolation multiplier so we never claim more than
        #      DIST_EST_MAX_MULT× the tracked distance. At healthy coverage (≳50%)
        #      the cap never binds; only thin-coverage players are held back to a
        #      conservative estimate. `dist_est_capped` flags when the cap bit so
        #      the PWA can mark it indicative rather than measured.
        #   3. Coverage-FRACTION floor: the cap bounds how far we stretch, but a
        #      rate measured on a thin sliver is unreliable at ANY multiplier —
        #      we'd be projecting a whole game from a few unrepresentative
        #      minutes (and the tracker preferentially keeps a player while
        #      they're MOVING, so the sliver is biased fast). Below
        #      DIST_EST_MIN_COVERAGE, don't extrapolate at all: report the real
        #      tracked distance and flag it, rather than publish a projection the
        #      data can't support.
        dist_est_capped = False
        if coverage_frac < config.DIST_EST_MIN_COVERAGE:
            dist_est = dist_raw
            sprint_est = int(sprint_count)
            dist_est_capped = True      # UI: indicative / under-reported, not measured
        elif tracked_min >= 3.0 and coach_min > 0:
            mult = coach_min / tracked_min
            capped_mult = min(mult, config.DIST_EST_MAX_MULT)
            dist_est_capped = capped_mult < mult
            dist_est = dist_raw * capped_mult
            sprint_est = int(round(sprint_count * capped_mult))
        else:
            dist_est = dist_raw
            sprint_est = int(sprint_count)

        out.append(PlayerStats(
            player_id=str(pid),
            # Minutes from the coach log (ground truth) when available, else the
            # track time span. Track spans over-count when identity is imperfect.
            minutes_played=coach_min,
            distance_m=dist_raw,
            # p99 of the SMOOTHED speed (unchanged semantics), but over real
            # steps only so teleport zeros can't shift the percentile.
            top_speed_ms=float(np.percentile(real_speed_s, 99)) if len(real_speed_s) else 0.0,
            # Average over REAL-motion steps only — the teleport zeros are gaps,
            # not slow play, and including them deflated avg_speed by up to
            # ~implausible_step_frac.
            avg_speed_ms=float(np.mean(real_speed)) if len(real_speed) else 0.0,
            sprint_count=int(sprint_count),
            sprint_distance_m=float(sprint_dist),
            implausible_step_frac=implausible_frac,
            pct_attacking_third=float(att.mean() * 100),
            pct_middle_third=float(mid.mean() * 100),
            pct_defensive_third=float(dfn.mean() * 100),
            heatmap_grid=grid,
            work_rate_timeline=rate,
            tracked_seconds=tracked_s,
            distance_est_m=float(dist_est),
            sprint_est_count=sprint_est,
            sprint_threshold_ms=sprint_thr,
            coverage_frac=float(coverage_frac),
            dist_est_capped=bool(dist_est_capped),
            coverage_frac_statue_aware=float(coverage_statue_aware),
            statue_frac_of_tracked=float(statue_frac),
        ))
    return out
