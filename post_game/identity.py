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


def _halftime_seconds(game: GameDoc) -> float:
    """Duration of the (longest) pause period — assumed to be halftime."""
    gap = 0.0
    for p in game.pause_periods:
        if p.ended_at and p.started_at:
            gap = max(gap, (p.ended_at - p.started_at) / 1000.0)
    return gap


def half_windows(game: GameDoc, video_duration_s: float) -> list[tuple[float, float]]:
    """Return [(t1_start, t1_end), (t2_start, t2_end)] in video seconds.

    Boundaries are derived from `video_offset_h1_kickoff_s` (set in the UI)
    plus wallclock deltas from Firestore (`started_at`, `pause_periods`,
    `ended_at`). Falls back to half-length when wallclock data is missing.

    If `video_offset_h2_kickoff_s` is set (> 0), it overrides the
    wallclock-derived H2 start — use when the "start 2nd half" button was
    pressed late.
    """
    offset = max(0.0, float(game.video_offset_h1_kickoff_s))
    half_len_s = game.half_length_min * 60

    halftime_gap_s = _halftime_seconds(game)

    # 1st half end (video s) — prefer wallclock when halftime whistle present
    pp = game.pause_periods[0] if game.pause_periods else None
    if pp and pp.started_at and game.started_at:
        h1_play_s = (pp.started_at - game.started_at) / 1000.0
    else:
        h1_play_s = float(half_len_s)
    h1_end = offset + max(0.0, h1_play_s)

    # H2 start: manual override wins; else derived from halftime gap.
    h2_override = float(getattr(game, "video_offset_h2_kickoff_s", 0.0) or 0.0)
    if h2_override > 0:
        h2_start = h2_override
    else:
        h2_start = h1_end + halftime_gap_s

    # 2nd half end — prefer wallclock final whistle if present
    if game.ended_at and game.started_at:
        total_wall_s = (game.ended_at - game.started_at) / 1000.0
        h2_play_s = max(0.0, total_wall_s - h1_play_s - halftime_gap_s)
    else:
        h2_play_s = float(half_len_s)
    h2_end = h2_start + h2_play_s

    # Clamp to actual video length
    h1_end = min(h1_end, video_duration_s)
    h2_start = min(h2_start, video_duration_s)
    h2_end = min(h2_end, video_duration_s)

    return [(offset, h1_end), (h2_start, h2_end)]


def period_clock_to_video_time_factory(game: GameDoc) -> Callable[[int, int], float]:
    """Returns f(period, elapsed_s) -> seconds into the source video.

    Uses `video_offset_h1_kickoff_s` (1st-half kickoff position in video) plus
    the wallclock-derived halftime gap from `pause_periods`. If no offset was
    set, assumes the video starts at kickoff (legacy behaviour).
    """
    offset = max(0.0, float(game.video_offset_h1_kickoff_s))
    half_len_s = game.half_length_min * 60
    halftime_gap_s = _halftime_seconds(game)
    # End of 1st half in video (wallclock-derived if present, else half_len)
    pp = game.pause_periods[0] if game.pause_periods else None
    if pp and pp.started_at and game.started_at:
        h1_play_s = (pp.started_at - game.started_at) / 1000.0
    else:
        h1_play_s = float(half_len_s)

    # Manual H2 override: when set, period-2 timestamps are offset from this
    # instead of from (h1_end + halftime_gap). Keeps clip alignment correct
    # even when the "start 2nd half" button was pressed late.
    h2_override = float(getattr(game, "video_offset_h2_kickoff_s", 0.0) or 0.0)
    h2_kickoff_in_video = h2_override if h2_override > 0 else (offset + h1_play_s + halftime_gap_s)

    def f(period: int, elapsed_s: int) -> float:
        if period == 1:
            return offset + float(elapsed_s)
        return h2_kickoff_in_video + float(elapsed_s)

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
