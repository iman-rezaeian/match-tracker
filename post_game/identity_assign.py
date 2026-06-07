"""Coach-log global identity assignment (v2).

Assigns stitched tracklets to the players the coach logged as on the field,
using the coach tactical board (POSITION events) as a positional template plus
the lineup/sub timeline. Replaces the v0 per-fragment, centroid-matched vote
scheme that left ~46 low-confidence tracks.

Key ideas
---------
* **POSITION events** give each player a board spot (x,y∈[0,1]); board→field
  orientation is unknown (which way we attack + which sideline), so we try all
  4 depth/lateral flips per period and keep the one whose assignment is cheapest
  — the rigid board template only lines up with real play in the right
  orientation, so the data picks it for us (no fragile attack-direction geometry).
* **Per-window Hungarian**: in each short time window the on-field players are
  distinct, so we 1:1 match the tracklets present to the on-field players. Over
  the game a player owns many tracklets — each tracklet takes the player it was
  matched to most often.
* **On-field windows** (lineup + subs) gate candidates, with a generous
  tolerance because subs are logged late.
* **GK anchor**: the geometric GK track is nudged toward the GK player.
* **Two-tier confidence**: ≥AUTO confident, AUTO>c≥REVIEW best-guess (both used
  downstream), <REVIEW dropped.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import config
from .firestore_io import CoachEvent, RosterPlayer
from .identity import (
    IdentityAssignment,
    ONFIELD_TOLERANCE_S,
    _find_gk_track,
    _onfield_intervals,
    _is_onfield,
    _track_lifetimes,
)

log = logging.getLogger(__name__)

WINDOW_S = 5.0


def _board_to_field(bx: float, by: float, flip_d: bool, flip_l: bool,
                    L: float, W: float) -> tuple[float, float]:
    """Map board (bx left→right, by halfway→own-goal) to field meters under a
    chosen depth/lateral flip. by=1 (own goal) → one end; bx spans the width."""
    depth = by if flip_d else (1.0 - by)         # 0..1 along length
    lat = (1.0 - bx) if flip_l else bx           # 0..1 along width
    return (depth * L, lat * W)


def _player_board_positions(events, period: int) -> dict[str, tuple[float, float]]:
    """Latest board (x,y) per player within a period (subs inherit via the app)."""
    out: dict[str, tuple[int, float, float]] = {}
    for e in events or []:
        if (e.type or "").upper() != "POSITION" or int(e.period or 0) != period:
            continue
        pid = e.player_id
        ex = (e.extras or {})
        x, y = ex.get("x"), ex.get("y")
        if not pid or x is None or y is None:
            continue
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            continue
        if not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        at = int(e.at or 0)
        if pid not in out or at >= out[pid][0]:
            out[pid] = (at, x, y)
    return {p: (x, y) for p, (_, x, y) in out.items()}


def assign_identities_v2(
    tracks_df: pd.DataFrame,
    tracklet_of_track: dict[int, int],
    team_of_track: dict[int, int],
    events: list[CoachEvent],
    roster: list[RosterPlayer],
    starting_lineup: list[str],
    gk_player_id: Optional[str],
    period_clock_to_video_time: Callable[[int, int], float],
    periods_video: list[tuple[float, float]],
    field_length_m: float,
    field_width_m: float,
) -> list[IdentityAssignment]:
    """Return per-original-track IdentityAssignment. periods_video = [(t0,t1)]
    video-second spans per period (half_windows)."""
    valid_ids = {r.id for r in roster}
    has_xy = {"x_m", "y_m"}.issubset(tracks_df.columns)
    lifetimes = _track_lifetimes(tracks_df)
    onfield = _onfield_intervals(starting_lineup, events, period_clock_to_video_time)

    # our-team tracks → tracklet id
    our_tracks = {int(t) for t, tm in team_of_track.items() if tm == 0}
    tracklet_members: dict[int, list[int]] = {}
    for t in our_tracks:
        tl = tracklet_of_track.get(t, t)
        tracklet_members.setdefault(tl, []).append(t)

    gk_track = _find_gk_track(tracks_df, team_of_track, field_length_m, field_width_m)
    gk_tracklet = tracklet_of_track.get(gk_track, gk_track) if gk_track is not None else None

    # match_count[tracklet][player] accumulated over windows
    match_count: dict[int, dict[str, float]] = {}

    if has_xy:
        df = tracks_df[tracks_df["track_id"].isin(our_tracks)].copy()
        df["tracklet"] = df["track_id"].map(lambda t: tracklet_of_track.get(int(t), int(t)))
        from scipy.optimize import linear_sum_assignment

        for pi, (pstart, pend) in enumerate(periods_video, start=1):
            board = _player_board_positions(events, pi)
            if not board:
                continue
            pdf = df[(df["time_s"] >= pstart) & (df["time_s"] <= pend)]
            if pdf.empty:
                continue
            win_edges = np.arange(pstart, pend, WINDOW_S)

            # Try 4 board orientations; keep the cheapest total matched cost.
            best = None
            for flip_d in (False, True):
                for flip_l in (False, True):
                    exp = {p: _board_to_field(x, y, flip_d, flip_l, field_length_m, field_width_m)
                           for p, (x, y) in board.items() if p in valid_ids}
                    total_cost = 0.0
                    per_window: list[tuple[float, list[tuple[int, str]]]] = []
                    for w0 in win_edges:
                        w1 = w0 + WINDOW_S
                        wdf = pdf[(pdf["time_s"] >= w0) & (pdf["time_s"] < w1)]
                        if wdf.empty:
                            continue
                        # median pos per tracklet present
                        tl_pos = wdf.groupby("tracklet")[["x_m", "y_m"]].median()
                        tls = [int(t) for t in tl_pos.index]
                        # on-field players (tolerant) with a known board spot
                        wmid = 0.5 * (w0 + w1)
                        cand = [p for p in exp
                                if _is_onfield(onfield, p, wmid - ONFIELD_TOLERANCE_S,
                                               wmid + ONFIELD_TOLERANCE_S)]
                        if not tls or not cand:
                            continue
                        C = np.zeros((len(tls), len(cand)), dtype=np.float64)
                        for ai, tl in enumerate(tls):
                            px, py = float(tl_pos.loc[tl, "x_m"]), float(tl_pos.loc[tl, "y_m"])
                            for bj, p in enumerate(cand):
                                ex, ey = exp[p]
                                d2 = (px - ex) ** 2 + (py - ey) ** 2
                                score = config.ASSIGN_W_POSITION * np.exp(-d2 / (2 * config.ASSIGN_POS_SIGMA_M ** 2))
                                if gk_tracklet is not None and tl == gk_tracklet and p == gk_player_id:
                                    score += config.ASSIGN_GK_BONUS
                                C[ai, bj] = -score  # minimize → maximize score
                        ri, ci = linear_sum_assignment(C)
                        matched = [(tls[a], cand[b]) for a, b in zip(ri, ci)]
                        total_cost += float(C[ri, ci].sum())
                        per_window.append((wmid, matched))
                    if best is None or total_cost < best[0]:
                        best = (total_cost, flip_d, flip_l, per_window)

            if best is None:
                continue
            _, fd, fl, per_window = best
            log.info("  identity P%d: board orientation flip_depth=%s flip_lateral=%s "
                     "(%d windows)", pi, fd, fl, len(per_window))
            for _wmid, matched in per_window:
                for tl, p in matched:
                    match_count.setdefault(tl, {})[p] = match_count.setdefault(tl, {}).get(p, 0.0) + 1.0

    # --- aggregate per tracklet → player + confidence ---
    tracklet_assign: dict[int, tuple[Optional[str], float, str]] = {}
    for tl, members in tracklet_members.items():
        votes = match_count.get(tl, {})
        # restrict to roster + on-field over the tracklet lifetime (tolerant)
        span = (min(lifetimes.get(m, (0, 0))[0] for m in members),
                max(lifetimes.get(m, (0, 0))[1] for m in members))
        votes = {p: v for p, v in votes.items()
                 if p in valid_ids and _is_onfield(onfield, p, span[0] - ONFIELD_TOLERANCE_S,
                                                    span[1] + ONFIELD_TOLERANCE_S)}
        if not votes:
            tracklet_assign[tl] = (None, 0.0, "unknown")
            continue
        total = sum(votes.values())
        winner, count = max(votes.items(), key=lambda kv: kv[1])
        share = count / max(total, 1.0)
        # confidence: agreement share, lightly boosted when the winner clearly
        # leads the runner-up.
        ordered = sorted(votes.values(), reverse=True)
        margin = (ordered[0] - ordered[1]) / ordered[0] if len(ordered) > 1 and ordered[0] > 0 else 1.0
        conf = float(min(1.0, 0.6 * share + 0.4 * margin))
        status = ("auto" if conf >= config.ID_CONFIDENCE_AUTO
                  else "review" if conf >= config.ID_CONFIDENCE_REVIEW
                  else "unknown")
        tracklet_assign[tl] = (winner if status != "unknown" else None, conf, status)

    # --- emit per original track ---
    out: list[IdentityAssignment] = []
    for tid in sorted(team_of_track.keys()):
        team = team_of_track[tid]
        life = lifetimes.get(tid, (0.0, 0.0))
        minutes = max(0.0, (life[1] - life[0]) / 60.0)
        if team != 0:
            out.append(IdentityAssignment(
                track_id=tid, player_id=None, confidence=0.0,
                status="opponent" if team == 1 else "unknown",
                breakdown={}, minutes_on_field=minutes))
            continue
        tl = tracklet_of_track.get(tid, tid)
        pid, conf, status = tracklet_assign.get(tl, (None, 0.0, "unknown"))
        out.append(IdentityAssignment(
            track_id=tid, player_id=pid, confidence=conf, status=status,
            breakdown={"tracklet": tl, "coach_assign": True}, minutes_on_field=minutes))
    return out
