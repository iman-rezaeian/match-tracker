"""Goalkeeper positioning at each SHOT_ON / SAVE / GOAL.

Cheap and high-value: GK identity is known (`gkPlayerId` + `gkChanges`), shot
timestamps are known (coach event log), GK field position is a single lookup
on the trajectory already computed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import config
from .firestore_io import CoachEvent
from .identity import _gk_segments


@dataclass
class GKShotPosition:
    event_id: str
    event_type: str
    period: int
    elapsed: int
    gk_player_id: str
    gk_pos_m: tuple[float, float]
    distance_from_goal_line_m: float
    lateral_offset_from_goal_center_m: float
    shooter_pos_m: Optional[tuple[float, float]]
    on_correct_angle: Optional[bool]


def _gk_at_time(gk_segments: list[dict], at_ms: int) -> Optional[str]:
    for seg in gk_segments:
        if seg["from"] <= at_ms and (seg["to"] is None or at_ms < seg["to"]):
            return seg["playerId"]
    return None


def _track_position_at(
    tracks_field_df: pd.DataFrame, track_id: int, t_video: float
) -> Optional[tuple[float, float]]:
    sub = tracks_field_df[tracks_field_df["track_id"] == track_id]
    if sub.empty:
        return None
    idx = (sub["time_s"] - t_video).abs().idxmin()
    row = sub.loc[idx]
    return (float(row["x_m"]), float(row["y_m"]))


def compute_gk_positions(
    events: list[CoachEvent],
    tracks_field_df: pd.DataFrame,
    identity_by_track: dict[int, str],
    gk_player_id: Optional[str],
    gk_changes: list[dict],
    field_length_m: float,
    field_width_m: float,
    we_attack_right_in_period: dict[int, bool],
    period_clock_to_video_time: Callable[[int, int], float],
) -> list[GKShotPosition]:
    if not gk_player_id and not gk_changes:
        return []

    player_to_track = {pid: tid for tid, pid in identity_by_track.items()}
    gk_segs = _gk_segments(gk_player_id, gk_changes)
    out: list[GKShotPosition] = []
    goal_y = field_width_m / 2.0

    for ev in events:
        if ev.type not in config.GK_EVENT_TYPES:
            continue
        gk_pid = _gk_at_time(gk_segs, ev.at) or gk_player_id
        if not gk_pid:
            continue
        gk_track = player_to_track.get(gk_pid)
        if gk_track is None:
            continue

        t_video = period_clock_to_video_time(ev.period, ev.elapsed) - config.GK_SHOT_LOOKBACK_S
        gk_xy = _track_position_at(tracks_field_df, gk_track, t_video)
        if gk_xy is None:
            continue

        attacking_right = we_attack_right_in_period.get(ev.period, True)
        goal_x = 0.0 if attacking_right else field_length_m
        dist_line = abs(gk_xy[0] - goal_x)
        lateral = gk_xy[1] - goal_y

        shooter_pos = None
        on_angle: Optional[bool] = None
        if ev.player_id and ev.player_id != gk_pid:
            shooter_track = player_to_track.get(ev.player_id)
            if shooter_track is not None:
                shooter_pos = _track_position_at(tracks_field_df, shooter_track, t_video)
                if shooter_pos is not None:
                    sx, sy = shooter_pos
                    vx, vy = goal_x - sx, goal_y - sy
                    L = vx * vx + vy * vy
                    if L > 1e-6:
                        tparam = ((gk_xy[0] - sx) * vx + (gk_xy[1] - sy) * vy) / L
                        cx, cy = sx + tparam * vx, sy + tparam * vy
                        perp = float(np.hypot(gk_xy[0] - cx, gk_xy[1] - cy))
                        on_angle = bool(perp <= 1.5 and 0.0 <= tparam <= 1.0)

        out.append(GKShotPosition(
            event_id=ev.id,
            event_type=ev.type,
            period=ev.period,
            elapsed=ev.elapsed,
            gk_player_id=gk_pid,
            gk_pos_m=gk_xy,
            distance_from_goal_line_m=float(dist_line),
            lateral_offset_from_goal_center_m=float(lateral),
            shooter_pos_m=shooter_pos,
            on_correct_angle=on_angle,
        ))
    return out
