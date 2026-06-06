"""Per-player physical + spatial stats (Tier A)."""

from __future__ import annotations

from dataclasses import dataclass

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
) -> list[PlayerStats]:
    per_player = _per_player_trajectory(tracks_field_df, identity_by_track)
    third_low, third_high = config.THIRDS_FRACTIONS
    boundaries_x = (field_length_m * third_low, field_length_m * third_high)

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
        seg_dist = np.sqrt(dx * dx + dy * dy)
        # Clamp each step to a physically plausible move (MAX_PLAUSIBLE_SPEED_MS
        # * dt). Kills identity-swap teleports that otherwise produce absurd top
        # speeds (6000+ km/h) and inflate total distance.
        seg_dist = np.minimum(seg_dist, config.MAX_PLAUSIBLE_SPEED_MS * dt)
        speed = seg_dist / dt  # inherently <= MAX_PLAUSIBLE_SPEED_MS now
        speed_s = _smooth(speed, config.SPEED_SMOOTH_WINDOW)

        # Sprints: continuous run above threshold for >= 0.5s
        is_sprint = speed_s >= config.SPRINT_THRESHOLD_MS
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

        # Field thirds along attack axis
        if we_attack_right:
            att = x >= boundaries_x[1]
            mid = (x >= boundaries_x[0]) & (x < boundaries_x[1])
            dfn = x < boundaries_x[0]
        else:
            dfn = x >= boundaries_x[1]
            mid = (x >= boundaries_x[0]) & (x < boundaries_x[1])
            att = x < boundaries_x[0]

        gh, gw = heatmap_grid_shape
        grid = np.histogram2d(
            x, y, bins=[gh, gw],
            range=[[0, field_length_m], [0, field_width_m]],
        )[0].astype(int).tolist()

        minutes = int(np.ceil((t[-1] - t[0]) / 60.0)) if t[-1] > t[0] else 1
        rate = []
        for m in range(minutes):
            lo, hi = t[0] + m * 60, t[0] + (m + 1) * 60
            mask = (t[:-1] >= lo) & (t[:-1] < hi)
            rate.append(float(np.mean(speed_s[mask])) if mask.any() else 0.0)

        out.append(PlayerStats(
            player_id=str(pid),
            minutes_played=float((t[-1] - t[0]) / 60.0),
            distance_m=float(seg_dist.sum()),
            top_speed_ms=float(np.percentile(speed_s, 99)) if len(speed_s) else 0.0,
            avg_speed_ms=float(np.mean(speed_s)) if len(speed_s) else 0.0,
            sprint_count=int(sprint_count),
            sprint_distance_m=float(sprint_dist),
            pct_attacking_third=float(att.mean() * 100),
            pct_middle_third=float(mid.mean() * 100),
            pct_defensive_third=float(dfn.mean() * 100),
            heatmap_grid=grid,
            work_rate_timeline=rate,
        ))
    return out
