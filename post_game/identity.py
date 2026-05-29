"""Identity assignment — coach-log-anchored.

The big idea: rather than naming 14 anonymous tracks via face/gait/cleat, we
*anchor* identity using the coach event log (player_id + timestamp) and let
that propagate along each track. OCR + face are tiebreakers.

This v0 is intentionally conservative — only the coach_log signal is wired up.
OCR/face will be added in a follow-up. Where uncertainty is high we leave a
track as `status="review"` for the coach to confirm in the Analytics tab.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import config
from .firestore_io import CoachEvent, GameDoc, RosterPlayer

log = logging.getLogger(__name__)


@dataclass
class IdentityAssignment:
    track_id: int
    player_id: Optional[str]
    confidence: float
    status: str
    breakdown: dict
    minutes_on_field: float


def period_clock_to_video_time_factory(game: GameDoc) -> Callable[[int, int], float]:
    """Returns f(period, elapsed_s) -> seconds into the source video.

    Assumes the video began rolling at `game.started_at` (i.e. coach uploaded
    a video that starts at kickoff). Halftime gap inferred from pausePeriods.
    """
    half_len_s = game.half_length_min * 60
    halftime_gap_s = 0.0
    for p in game.pause_periods:
        if p.ended_at and p.started_at:
            halftime_gap_s = max(halftime_gap_s, (p.ended_at - p.started_at) / 1000.0)

    def f(period: int, elapsed_s: int) -> float:
        if period == 1:
            return float(elapsed_s)
        return float(half_len_s + halftime_gap_s + elapsed_s)

    return f


def _track_lifetimes(tracks_df: pd.DataFrame) -> dict[int, tuple[float, float]]:
    g = tracks_df.groupby("track_id")["time_s"]
    return {int(tid): (float(t.min()), float(t.max())) for tid, t in g}


def _nearest_track(
    tracks_df: pd.DataFrame,
    team_of_track: dict[int, int],
    target_team: int,
    t_video: float,
    field_xy: Optional[tuple[float, float]] = None,
    window_s: float = 1.5,
) -> Optional[int]:
    mask = (tracks_df["time_s"] >= t_video - window_s) & (tracks_df["time_s"] <= t_video + window_s)
    snap = tracks_df[mask]
    if snap.empty:
        return None
    snap = snap[snap["track_id"].map(lambda x: team_of_track.get(int(x), -1) == target_team)]
    if snap.empty:
        return None
    if field_xy is not None and {"x_m", "y_m"}.issubset(snap.columns):
        d = np.hypot(snap["x_m"].to_numpy() - field_xy[0], snap["y_m"].to_numpy() - field_xy[1])
        return int(snap.iloc[int(np.argmin(d))]["track_id"])
    counts = snap.groupby("track_id").size()
    return int(counts.idxmax())


def _gk_segments(gk_player_id: Optional[str], gk_changes: list[dict]) -> list[dict]:
    segs: list[dict] = []
    if gk_player_id:
        segs.append({"playerId": gk_player_id, "from": 0, "to": None})
    for ch in gk_changes:
        at = int(ch.get("at", 0))
        pid = ch.get("playerId")
        if not pid:
            continue
        if segs:
            segs[-1]["to"] = at
        segs.append({"playerId": pid, "from": at, "to": None})
    return segs


def _find_gk_track(
    tracks_df: pd.DataFrame, team_of_track: dict[int, int],
    field_length_m: float, field_width_m: float,
) -> Optional[int]:
    if "x_m" not in tracks_df.columns or tracks_df.empty:
        return None
    best_tid, best_score = None, float("inf")
    for tid, sub in tracks_df.groupby("track_id"):
        if team_of_track.get(int(tid), -1) != 0:
            continue
        if len(sub) < 30:
            continue
        x = float(np.median(sub["x_m"]))
        d = min(x, field_length_m - x)
        if d < best_score:
            best_score = d
            best_tid = int(tid)
    return best_tid if best_score < 10.0 else None


def assign_identities(
    tracks_df: pd.DataFrame,
    team_of_track: dict[int, int],
    events: list[CoachEvent],
    roster: list[RosterPlayer],
    starting_lineup: list[str],
    gk_player_id: Optional[str],
    gk_changes: list[dict],
    period_clock_to_video_time: Callable[[int, int], float],
    field_length_m: float,
    field_width_m: float,
) -> list[IdentityAssignment]:
    votes: dict[int, dict[str, int]] = {}
    has_xy = {"x_m", "y_m"}.issubset(tracks_df.columns)

    for ev in events:
        if not ev.player_id:
            continue
        t_video = period_clock_to_video_time(ev.period, ev.elapsed)
        target_xy: Optional[tuple[float, float]] = None
        if has_xy:
            mask = (
                (tracks_df["time_s"] >= t_video - 1.0)
                & (tracks_df["time_s"] <= t_video + 1.0)
                & tracks_df["track_id"].map(lambda x: team_of_track.get(int(x), -1) == 0)
            )
            snap = tracks_df[mask]
            if not snap.empty:
                target_xy = (float(snap["x_m"].mean()), float(snap["y_m"].mean()))
        tid = _nearest_track(tracks_df, team_of_track, 0, t_video, target_xy)
        if tid is None:
            continue
        votes.setdefault(tid, {})
        votes[tid][ev.player_id] = votes[tid].get(ev.player_id, 0) + 1

    # GK constraint
    gk_segs = _gk_segments(gk_player_id, gk_changes)
    gk_track = _find_gk_track(tracks_df, team_of_track, field_length_m, field_width_m)
    if gk_track is not None and gk_segs:
        for seg in gk_segs:
            votes.setdefault(gk_track, {})
            votes[gk_track][seg["playerId"]] = votes[gk_track].get(seg["playerId"], 0) + 5

    lifetimes = _track_lifetimes(tracks_df)
    valid_roster_ids = {r.id for r in roster}
    assignments: list[IdentityAssignment] = []
    for tid in sorted(team_of_track.keys()):
        team = team_of_track[tid]
        life = lifetimes.get(tid, (0.0, 0.0))
        minutes = max(0.0, (life[1] - life[0]) / 60.0)
        if team != 0:
            assignments.append(IdentityAssignment(
                track_id=tid, player_id=None, confidence=0.0,
                status="opponent" if team == 1 else "unknown",
                breakdown={}, minutes_on_field=minutes,
            ))
            continue
        track_votes = votes.get(tid, {})
        track_votes = {pid: v for pid, v in track_votes.items() if pid in valid_roster_ids}
        if not track_votes:
            assignments.append(IdentityAssignment(
                track_id=tid, player_id=None, confidence=0.0, status="unknown",
                breakdown={"coach_log": {}}, minutes_on_field=minutes,
            ))
            continue
        winner, count = max(track_votes.items(), key=lambda kv: kv[1])
        total = sum(track_votes.values())
        share = count / max(total, 1)
        coach_score = min(1.0, count / 3.0)
        fused = coach_score * config.ID_WEIGHTS["coach_log"] + share * (1 - config.ID_WEIGHTS["coach_log"])
        status = (
            "auto" if fused >= config.ID_CONFIDENCE_AUTO
            else "review" if fused >= config.ID_CONFIDENCE_REVIEW
            else "unknown"
        )
        assignments.append(IdentityAssignment(
            track_id=tid, player_id=winner if status != "unknown" else None,
            confidence=float(fused), status=status,
            breakdown={"coach_log": track_votes}, minutes_on_field=minutes,
        ))
    return assignments
