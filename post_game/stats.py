"""Per-player physical + spatial stats (Tier A)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import config


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
    tracked_seconds: float = 0.0          # actual time with detections
    distance_est_m: float = 0.0           # (distance_m / tracked_min) × coach_min
    sprint_est_count: int = 0             # (sprint_count / tracked_min) × coach_min
    # Personalized sprint threshold actually used for THIS game (plan 4.5).
    sprint_threshold_ms: float = 0.0
    # Fraction of inter-detection steps that exceeded the physical speed cap —
    # i.e. tracking artifacts (swap teleports / projection jumps / concurrent-
    # tracklet ping-pong). A clean track is ~0; a swap-polluted one is high.
    # The UI uses this (not "top speed == cap") to flag unreliable movement.
    implausible_step_frac: float = 0.0


def _smooth(arr: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(arr) < window:
        return arr.astype(float)
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="same")


def _per_player_trajectory(
    tracks_field_df: pd.DataFrame, identity_by_track: dict[int, str]
) -> dict[str, pd.DataFrame]:
    df = tracks_field_df.copy()
    df["player_id"] = df["track_id"].map(identity_by_track)
    df = df[df["player_id"].notna()]
    out: dict[str, pd.DataFrame] = {}
    for pid, sub in df.groupby("player_id"):
        sub = sub.sort_values("time_s").reset_index(drop=True)
        out[str(pid)] = sub
    return out


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
) -> list[PlayerStats]:
    per_player = _per_player_trajectory(tracks_field_df, identity_by_track)
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

    our_net_at_x0: dict[int, bool] = {}
    gk_sub = per_player.get(str(gk_player_id)) if gk_player_id else None
    for pi, (a, b) in enumerate(periods or [(0.0, 1e12)], start=1):
        if gk_sub is not None:
            m = gk_sub[(gk_sub["time_s"] >= a) & (gk_sub["time_s"] <= b)]
            if len(m) >= 5:
                our_net_at_x0[pi] = float(m["x_m"].median()) < field_length_m / 2.0
                continue
        our_net_at_x0[pi] = bool(we_attack_right)  # attack +x ⇒ our net at x=0

    out: list[PlayerStats] = []
    for pid, sub in per_player.items():
        if len(sub) < 5:
            continue
        x = sub["x_m"].to_numpy()
        y = sub["y_m"].to_numpy()
        t = sub["time_s"].to_numpy()
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

        # Heatmap grid in canonical coords: row 0 = our-net end, last row =
        # opponent-net end; col 0 .. last = consistent left → right. The UI
        # renders row 0 at the BOTTOM (our net).
        gh, gw = heatmap_grid_shape
        grid = np.histogram2d(
            d, w, bins=[gh, gw],
            range=[[0, field_length_m], [0, field_width_m]],
        )[0].astype(int).tolist()

        minutes = int(np.ceil((t[-1] - t[0]) / 60.0)) if t[-1] > t[0] else 1
        rate = []
        for m in range(minutes):
            lo, hi = t[0] + m * 60, t[0] + (m + 1) * 60
            mask = (t[:-1] >= lo) & (t[:-1] < hi)
            rate.append(float(np.mean(speed_s[mask])) if mask.any() else 0.0)

        coach_min = float((played_minutes or {}).get(str(pid), (t[-1] - t[0]) / 60.0))
        dist_raw = float(seg_dist.sum())
        tracked_s = float(dt.sum())
        tracked_min = tracked_s / 60.0
        # Rate-based estimates (plan 4.4): scale per-tracked-minute rates to
        # coach-logged minutes. Below 3 tracked minutes a rate is a coin flip
        # off a sliver — keep the raw value and let the UI's low-tracking
        # warning carry the message.
        if tracked_min >= 3.0 and coach_min > 0:
            dist_est = dist_raw / tracked_min * coach_min
            sprint_est = int(round(sprint_count / tracked_min * coach_min))
        else:
            dist_est = dist_raw
            sprint_est = int(sprint_count)

        out.append(PlayerStats(
            player_id=str(pid),
            # Minutes from the coach log (ground truth) when available, else the
            # track time span. Track spans over-count when identity is imperfect.
            minutes_played=coach_min,
            distance_m=dist_raw,
            top_speed_ms=float(np.percentile(speed_s, 99)) if len(speed_s) else 0.0,
            avg_speed_ms=float(np.mean(speed_s)) if len(speed_s) else 0.0,
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
        ))
    return out
