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
* **GK windows**: keeper tracklets are detected geometrically per GK window
  (starting GK + GK_CHANGE events) and assigned to whoever was in goal THEN —
  mid-game keeper rotations assign correctly, and part-time keepers can still
  own outfield tracklets outside their stint.
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

# Player-attributed real actions usable as identity anchors. Excludes
# bookkeeping (SUB, GK_CHANGE), silent POSITION, and OPP_GOAL (no playerId).
ACTION_EVENT_TYPES = frozenset({
    "GOAL", "ASSIST", "KEY_PASS", "SAVE", "SHOT_ON", "SHOT_OFF", "BLOCK",
    "BALL_WIN", "CLEAR", "KICK_OUT", "DUEL_WIN", "DUEL_LOSE", "GIVE_GO",
    "GATES", "TURNOVER", "HOLDS_BALL", "FOUL_BY", "FOUL_ON",
    "PEN_AWARDED", "PEN_CONCEDED",
})

# Zone tag (3×3 coach grid, coach POV): depth fraction measured from OUR goal,
# lateral fraction left→right — mapped to field meters through the same
# orientation flips the board search resolves.
_ZONE_DEPTH = {"D": 1.0 / 6, "M": 0.5, "A": 5.0 / 6}
_ZONE_LAT = {"L": 1.0 / 6, "C": 0.5, "R": 5.0 / 6}


def _zone_center(zone, flip_d: bool, flip_l: bool,
                 L: float, W: float) -> Optional[tuple[float, float]]:
    parts = str(zone or "").split("-")
    if len(parts) != 2:
        return None
    d_own = _ZONE_DEPTH.get(parts[0])
    lat = _ZONE_LAT.get(parts[1])
    if d_own is None or lat is None:
        return None
    # Board convention: by=1 = own goal → depth 1 under flip_d, depth 0 without.
    depth = (1.0 - d_own) if flip_d else d_own
    la = (1.0 - lat) if flip_l else lat
    return (depth * L, la * W)


def _event_votes(
    match_count: dict[int, dict[str, float]],
    pdf: pd.DataFrame,
    events,
    period: int,
    pstart: float,
    pend: float,
    clock_to_video: Callable[[int, int], float],
    valid_ids: set,
    keeper_tracklets: set[int],
    is_gk_at: Callable[[str, float], bool],
    flip_d: Optional[bool],
    flip_l: Optional[bool],
    L: float,
    W: float,
) -> int:
    """Coach action events as identity anchors (votes into match_count).

    Each logged action gives (player, ~time, ~place): the coach logs LATE, so
    the action sits in [-ASSIGN_EVENT_BEFORE_S, +ASSIGN_EVENT_AFTER_S] around
    the logged clock time. WHERE is proxied by the team centroid at each
    second (U10 swarm chases the ball, so the scrum ≈ the action), sharpened
    by the zone tag when present (and when board orientation is known). Each
    tracklet's vote is its best centroid-proximity moment in the window —
    board-independent, so periods without POSITION events still get votes.
    """
    n = 0
    cent = (pdf.assign(_sec=pdf["time_s"].astype(int))
            .groupby("_sec")[["x_m", "y_m"]].median())
    out = pdf[~pdf["tracklet"].isin(keeper_tracklets)]
    if out.empty:
        return 0
    two_sig_evt = 2.0 * config.ASSIGN_EVENT_SIGMA_M ** 2
    two_sig_zone = 2.0 * config.ASSIGN_ZONE_SIGMA_M ** 2
    for e in events or []:
        if (e.type or "").upper() not in ACTION_EVENT_TYPES:
            continue
        if int(e.period or 0) != period:
            continue
        pid = e.player_id
        if not pid or pid not in valid_ids:
            continue
        try:
            t = float(clock_to_video(e.period, e.elapsed))
        except Exception:
            continue
        if not (pstart - config.ASSIGN_EVENT_AFTER_S
                <= t <= pend + config.ASSIGN_EVENT_BEFORE_S):
            continue
        if is_gk_at(pid, t):
            continue  # keeper actions belong to keeper tracklets (separate path)
        w0 = t - config.ASSIGN_EVENT_BEFORE_S
        w1 = t + config.ASSIGN_EVENT_AFTER_S
        wdf = out[(out["time_s"] >= w0) & (out["time_s"] <= w1)]
        if wdf.empty:
            continue
        zc = None
        if flip_d is not None and flip_l is not None:
            zc = _zone_center((e.extras or {}).get("zone"), flip_d, flip_l, L, W)
        g = (wdf.assign(_sec=wdf["time_s"].astype(int))
             .groupby(["tracklet", "_sec"])[["x_m", "y_m"]].median())
        best_by_tl: dict[int, float] = {}
        for (tl, s), row in g.iterrows():
            if s not in cent.index:
                continue
            dx = float(row["x_m"]) - float(cent.loc[s, "x_m"])
            dy = float(row["y_m"]) - float(cent.loc[s, "y_m"])
            sc = float(np.exp(-(dx * dx + dy * dy) / two_sig_evt))
            if zc is not None:
                dzx = float(row["x_m"]) - zc[0]
                dzy = float(row["y_m"]) - zc[1]
                sc *= 0.25 + 0.75 * float(np.exp(-(dzx * dzx + dzy * dzy) / two_sig_zone))
            tl = int(tl)
            if sc > best_by_tl.get(tl, 0.0):
                best_by_tl[tl] = sc
        for tl, sc in best_by_tl.items():
            if sc <= 0.05:
                continue
            match_count.setdefault(tl, {})
            match_count[tl][pid] = match_count[tl].get(pid, 0.0) + config.ASSIGN_W_VOTES * sc
            n += 1
    return n


def _gk_windows(
    gk_player_id: Optional[str],
    events: list,
    clock_to_video: Callable[[int, int], float],
    periods_video: list[tuple[float, float]],
) -> list[tuple[float, float, str]]:
    """[(t0_video_s, t1_video_s, player_id)] goalkeeper segments over the game.

    Starting keeper = game.gkPlayerId; each GK_CHANGE coach event (game-clock
    period+elapsed, player_id = incoming keeper) closes the previous segment.
    The GK_CHANGE events are the reliable source: game.gkChanges entries carry
    wall-clock `at` ms (no direct video mapping) and are keyed `gkPlayerId`,
    which identity.py's legacy _gk_segments misreads as `playerId`.
    """
    end = max((b for _, b in (periods_video or [])), default=1e12)
    changes = [e for e in (events or [])
               if (e.type or "").upper() == "GK_CHANGE" and e.player_id]
    changes.sort(key=lambda e: (int(e.period or 0), float(e.elapsed or 0)))
    out: list[tuple[float, float, str]] = []
    cur = gk_player_id
    t = 0.0
    for e in changes:
        try:
            tv = float(clock_to_video(e.period, e.elapsed))
        except Exception:
            continue
        if cur and tv > t:
            out.append((t, tv, cur))
        cur = e.player_id
        t = max(t, tv)
    if cur:
        out.append((t, end, cur))
    return out


def _board_to_field(bx: float, by: float, flip_d: bool, flip_l: bool,
                    L: float, W: float) -> tuple[float, float]:
    """Map board (bx left→right, by halfway→own-goal) to field meters under a
    chosen depth/lateral flip. by=1 (own goal) → one end; bx spans the width."""
    depth = by if flip_d else (1.0 - by)         # 0..1 along length
    lat = (1.0 - bx) if flip_l else bx           # 0..1 along width
    return (depth * L, lat * W)


def _kickoff_depth_flip(tracks_df, team_of_track, kickoff_video_s: float,
                        field_length_m: float) -> Optional[bool]:
    """Determine the board DEPTH flip from the kickoff frame.

    At a kickoff each team is in its OWN half, so the team centroids' depth (x_m)
    cleanly give the attacking direction — far more robust than the cross-window
    cost search, which can lock onto the wrong flip (and mirror every player). We
    pin DEPTH from this and let the search resolve only LATERAL.

    Convention (see _board_to_field): flip_d=True ⇒ our goal at high-x (we defend
    high-x); False ⇒ our goal at low-x. At kickoff we sit in our defensive half, so
    our centroid's depth says which end is ours. Returns the flip_d, or None when
    the kickoff frame is too ambiguous (sparse/late-start) → caller falls back.
    """
    if "x_m" not in tracks_df.columns:
        return None
    w = tracks_df[(tracks_df["time_s"] >= kickoff_video_s - 6.0)
                  & (tracks_df["time_s"] <= kickoff_video_s + 2.0)]
    if w.empty:
        return None
    x = w["x_m"].to_numpy()
    keep = np.isfinite(x) & (x >= -2.0) & (x <= field_length_m + 2.0)
    w = w[keep]
    teams = w["track_id"].map(team_of_track)
    ours = w["x_m"][teams == 0].to_numpy()
    opp = w["x_m"][teams == 1].to_numpy()
    if len(ours) < 8:
        return None
    our_d = float(np.median(ours))
    if len(opp) >= 8:
        opp_d = float(np.median(opp))
        if abs(our_d - opp_d) < 6.0:
            return None                     # teams not cleanly separated → untrustworthy
        return our_d > opp_d                # we defend the end our centroid sits at
    center = field_length_m / 2.0           # opponent sparse: our centroid vs midfield
    if abs(our_d - center) < 5.0:
        return None
    return our_d > center


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
    overrides: Optional[dict] = None,
    squad: Optional[list[str]] = None,
    resolved_flips_out: Optional[dict] = None,
) -> list[IdentityAssignment]:
    """Return per-original-track IdentityAssignment. periods_video = [(t0,t1)]
    video-second spans per period (half_windows).

    `overrides` is the per-game coach correction map { "<tracklet_id>":
    "<player_id>" | None } from the PWA: a player_id force-assigns that stitched
    tracklet to that roster player (status="coach", confidence 1.0); None drops
    it (not our team). Coach overrides always win over the auto-assignment and
    consume that player's minute budget before the greedy pass runs.

    `resolved_flips_out`, when given, is filled with the board orientation the
    Hungarian search resolved per period: {period: (flip_depth, flip_lateral)}.
    (None, None) when the period had no POSITION board to search. Consumers
    (tag pre-fill, Phase 3.3) use it to map field meters back to the coach's
    zone vocabulary.
    """
    # Coach log is ground truth: only players who DRESSED for this game (the
    # logged squad) can be assigned — not the whole team roster. This is a hard
    # constraint on both auto-assignment AND coach overrides (an override onto a
    # non-squad player is rejected → that tracklet drops). Falls back to the full
    # roster only when no squad was logged.
    roster_ids = {r.id for r in roster}
    valid_ids = (set(squad) & roster_ids) if squad else roster_ids
    # Normalise overrides to int tracklet keys; tolerate str/int from Firestore.
    ov: dict[int, Optional[str]] = {}
    for k, v in (overrides or {}).items():
        try:
            ov[int(k)] = (str(v) if v else None)
        except (TypeError, ValueError):
            continue
    has_xy = {"x_m", "y_m"}.issubset(tracks_df.columns)
    lifetimes = _track_lifetimes(tracks_df)
    onfield = _onfield_intervals(starting_lineup, events, period_clock_to_video_time)

    # our-team tracks → tracklet id
    our_tracks = {int(t) for t, tm in team_of_track.items() if tm == 0}
    tracklet_members: dict[int, list[int]] = {}
    for t in our_tracks:
        tl = tracklet_of_track.get(t, t)
        tracklet_members.setdefault(tl, []).append(t)

    # --- Goalkeeper handled SEPARATELY ---------------------------------------
    # The keeper stands near our goal with little lengthwise movement. Forcing
    # the GK into the per-window outfield Hungarian smeared his identity across
    # the field (an outfield tracklet got labeled GK whenever the keeper wasn't
    # detected that window). Instead: identify keeper tracklet(s) geometrically,
    # assign each to WHOEVER WAS IN GOAL during its time window (mid-game GK
    # rotations are common — Leamington ran 5 keepers), and EXCLUDE keeper
    # tracklets + the on-duty keeper from the outfield matching.
    gk_windows = _gk_windows(gk_player_id, events, period_clock_to_video_time,
                             periods_video)
    keeper_votes: dict[int, dict[str, int]] = {}  # tracklet -> {gk_pid: n_samples}
    if has_xy and gk_windows:
        L = field_length_m
        _dft = tracks_df[tracks_df["track_id"].isin(our_tracks)].copy()
        _dft["tracklet"] = _dft["track_id"].map(lambda t: tracklet_of_track.get(int(t), int(t)))
        # Per-period: a deep, low-movement tracklet sits near a goal line — but
        # that's true at BOTH ends (our keeper vs a team-mate camped at the
        # opponent goal). Disambiguate by which end has the most deep+stationary
        # presence: the real keeper is there the whole half, so OUR goal = the
        # end with more keeper-candidate samples. Only flag tracklets on that end.
        for (pstart, pend) in (periods_video or [(0.0, 1e12)]):
            pdf = _dft[(_dft["time_s"] >= pstart) & (_dft["time_s"] <= pend)]
            cands = []  # (tracklet, median_x, n_samples)
            for tl, sub in pdf.groupby("tracklet"):
                if len(sub) < 10:
                    continue
                medx = float(sub["x_m"].median())
                xspread = float(sub["x_m"].quantile(0.9) - sub["x_m"].quantile(0.1))
                if min(medx, L - medx) < L * 0.12 and xspread < L * 0.25:
                    cands.append((int(tl), medx, len(sub)))
            if not cands:
                continue
            near0 = sum(n for _t, mx, n in cands if mx < L / 2)
            nearL = sum(n for _t, mx, n in cands if mx >= L / 2)
            our_end_is_0 = near0 >= nearL  # our goal = end with more keeper presence
            # Per GK window overlapping this period: there's exactly ONE keeper
            # at a time, so take the single most-present deep+stationary
            # tracklet on our end WITHIN the window and credit it to that
            # window's keeper. (Taking all candidates swept in deep defenders;
            # taking one per PERIOD broke mid-half rotations.)
            for (w0, w1, wpid) in gk_windows:
                lo, hi = max(w0, pstart), min(w1, pend)
                if hi - lo < 30.0:  # sub-30s sliver: no reliable geometry
                    continue
                wdf = pdf[(pdf["time_s"] >= lo) & (pdf["time_s"] <= hi)]
                wc = []  # (tracklet, n_samples)
                for tl, sub in wdf.groupby("tracklet"):
                    if len(sub) < 8:
                        continue
                    medx = float(sub["x_m"].median())
                    xspread = float(sub["x_m"].quantile(0.9) - sub["x_m"].quantile(0.1))
                    if (min(medx, L - medx) < L * 0.12 and xspread < L * 0.25
                            and (medx < L / 2) == our_end_is_0):
                        wc.append((int(tl), len(sub)))
                if wc:
                    tl_best, n = max(wc, key=lambda c: c[1])
                    keeper_votes.setdefault(tl_best, {})
                    keeper_votes[tl_best][wpid] = keeper_votes[tl_best].get(wpid, 0) + n
        # Fallback: if geometry found nothing, use the single closest-to-goal
        # track, credited to the starting keeper.
        if not keeper_votes and gk_player_id:
            gkt = _find_gk_track(tracks_df, team_of_track, field_length_m, field_width_m)
            if gkt is not None:
                keeper_votes[tracklet_of_track.get(gkt, gkt)] = {gk_player_id: 1}
    # A stitched tracklet that straddles a swap gets the keeper it overlaps most.
    keeper_assign: dict[int, str] = {tl: max(v, key=v.get) for tl, v in keeper_votes.items()}
    keeper_tracklets: set[int] = set(keeper_assign)
    if gk_windows and len({p for *_ , p in gk_windows}) > 1:
        log.info("  identity: %d GK window(s) across %d keeper(s); %d keeper tracklet(s) flagged",
                 len(gk_windows), len({p for *_, p in gk_windows}), len(keeper_tracklets))

    def _is_gk_at(pid: str, t: float) -> bool:
        return any(w0 <= t <= w1 and p == pid for (w0, w1, p) in gk_windows)

    def _gk_overlap_frac(pid: str, span: tuple[float, float]) -> float:
        s0, s1 = span
        tot = max(s1 - s0, 1e-9)
        ov = sum(max(0.0, min(s1, w1) - max(s0, w0))
                 for (w0, w1, p) in gk_windows if p == pid)
        return ov / tot

    # match_count[tracklet][player] accumulated over windows
    match_count: dict[int, dict[str, float]] = {}

    if has_xy:
        df = tracks_df[tracks_df["track_id"].isin(our_tracks)].copy()
        df["tracklet"] = df["track_id"].map(lambda t: tracklet_of_track.get(int(t), int(t)))
        from scipy.optimize import linear_sum_assignment

        for pi, (pstart, pend) in enumerate(periods_video, start=1):
            board = _player_board_positions(events, pi)
            pdf = df[(df["time_s"] >= pstart) & (df["time_s"] <= pend)]
            if pdf.empty:
                continue
            win_edges = np.arange(pstart, pend, WINDOW_S)

            # DEPTH from the kickoff frame (pstart == this half's kickoff video time):
            # each team is in its own half there, so team centroids give the attacking
            # direction directly. Pin flip_d to it (the axis the cost search gets wrong
            # on hard games, mirroring every player) and search only lateral. None =
            # ambiguous kickoff (e.g. late-start) → fall back to the full 4-way search.
            kf_depth = _kickoff_depth_flip(tracks_df, team_of_track, pstart, field_length_m)
            depth_opts = (kf_depth,) if kf_depth is not None else (False, True)

            # Try board orientations; keep the cheapest total matched cost.
            best = None
            if not board:
                win_edges = ()  # no template → skip Hungarian, keep event votes
            for flip_d in (depth_opts if board else ()):
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
                        # median pos per OUTFIELD tracklet present (keeper handled separately)
                        tl_pos = wdf.groupby("tracklet")[["x_m", "y_m"]].median()
                        tls = [int(t) for t in tl_pos.index if int(t) not in keeper_tracklets]
                        # on-field OUTFIELD players (exclude whoever is in
                        # goal RIGHT NOW — not the whole-game starting GK,
                        # who may be outfield after a swap), tolerant window
                        wmid = 0.5 * (w0 + w1)
                        cand = [p for p in exp
                                if not _is_gk_at(p, wmid)
                                and _is_onfield(onfield, p, wmid - ONFIELD_TOLERANCE_S,
                                                wmid + ONFIELD_TOLERANCE_S)]
                        if not tls or not cand:
                            continue
                        gate2 = (field_length_m * config.ASSIGN_MATCH_MAX_FRAC) ** 2
                        C = np.zeros((len(tls), len(cand)), dtype=np.float64)
                        for ai, tl in enumerate(tls):
                            px, py = float(tl_pos.loc[tl, "x_m"]), float(tl_pos.loc[tl, "y_m"])
                            for bj, p in enumerate(cand):
                                ex, ey = exp[p]
                                d2 = (px - ex) ** 2 + (py - ey) ** 2
                                score = config.ASSIGN_W_POSITION * np.exp(-d2 / (2 * config.ASSIGN_POS_SIGMA_M ** 2))
                                C[ai, bj] = -score  # minimize → maximize score
                        ri, ci = linear_sum_assignment(C)
                        matched = []
                        for a, b in zip(ri, ci):
                            tl, p = tls[a], cand[b]
                            ex, ey = exp[p]
                            d2 = (float(tl_pos.loc[tl, "x_m"]) - ex) ** 2 + (float(tl_pos.loc[tl, "y_m"]) - ey) ** 2
                            if d2 > gate2:
                                continue  # too far to be a real match — don't vote
                            matched.append((tl, p))
                            total_cost += float(C[a, b])
                        per_window.append((wmid, matched))
                    if best is None or total_cost < best[0]:
                        best = (total_cost, flip_d, flip_l, per_window)

            fd = fl = None
            if best is not None:
                _, fd, fl, per_window = best
                log.info("  identity P%d: board orientation flip_depth=%s flip_lateral=%s "
                         "(%d windows%s)", pi, fd, fl, len(per_window),
                         ", depth from kickoff" if kf_depth is not None else " [depth searched]")
            if resolved_flips_out is not None:
                resolved_flips_out[pi] = (fd, fl)
                for _wmid, matched in per_window:
                    for tl, p in matched:
                        match_count.setdefault(tl, {})[p] = match_count.setdefault(tl, {}).get(p, 0.0) + 1.0

            # --- coach ACTION-EVENT votes (sharper anchors than the board;
            # board-independent except for the optional zone term) ---------
            n_ev = _event_votes(
                match_count, pdf, events, pi, pstart, pend,
                period_clock_to_video_time, valid_ids, keeper_tracklets,
                _is_gk_at, fd, fl, field_length_m, field_width_m,
            )
            log.info("  identity P%d: action-event votes added for %d (tracklet, player) pairs", pi, n_ev)

        # --- SUB anchors (1.2b): a tracklet whose FIRST detection appears
        # near a touchline around a logged sub-on is very likely the incoming
        # player; symmetric for LAST detection ↔ the player going off. ------
        srt = df.sort_values("time_s")
        g = srt.groupby("tracklet")
        tl_first = g[["time_s", "y_m"]].first()
        tl_last = g[["time_s", "y_m"]].last()
        n_sub = 0
        for e in events or []:
            if (e.type or "").upper() != "SUB":
                continue
            try:
                t = float(period_clock_to_video_time(e.period, e.elapsed))
            except Exception:
                continue
            w0 = t - config.ASSIGN_SUB_BEFORE_S
            w1 = t + config.ASSIGN_SUB_AFTER_S
            off_pid = e.player_id
            on_pid = (e.extras or {}).get("subOnPlayerId")
            for tl in tl_first.index:
                tl = int(tl)
                if tl in keeper_tracklets:
                    continue
                if on_pid and on_pid in valid_ids:
                    ft, fy = float(tl_first.loc[tl, "time_s"]), float(tl_first.loc[tl, "y_m"])
                    if w0 <= ft <= w1 and min(fy, field_width_m - fy) <= config.ASSIGN_SUB_TOUCHLINE_M:
                        match_count.setdefault(tl, {})
                        match_count[tl][on_pid] = match_count[tl].get(on_pid, 0.0) + config.ASSIGN_SUB_W
                        n_sub += 1
                if off_pid and off_pid in valid_ids:
                    lt, ly = float(tl_last.loc[tl, "time_s"]), float(tl_last.loc[tl, "y_m"])
                    if w0 <= lt <= w1 and min(ly, field_width_m - ly) <= config.ASSIGN_SUB_TOUCHLINE_M:
                        match_count.setdefault(tl, {})
                        match_count[tl][off_pid] = match_count[tl].get(off_pid, 0.0) + config.ASSIGN_SUB_W
                        n_sub += 1
        if n_sub:
            log.info("  identity: SUB anchors added %d (tracklet, player) votes", n_sub)

    # --- per-player minute BUDGET from the coach log (ground truth) ----------
    # A player can't own more track-time than the coach logged them on the field
    # (±slack). This caps over-assignment that smears one player's data across
    # another's tracks (e.g. an 18-min sub holding 56 min of tracks).
    def _played_minutes(pid: str) -> float:
        tot = 0.0
        for (a, b) in onfield.get(pid, []):
            for (pa, pb) in (periods_video or []):
                lo, hi = max(a, pa), min(b, pb)
                if hi > lo:
                    tot += hi - lo
        return tot / 60.0
    budget = {p: _played_minutes(p) + config.ASSIGN_MINUTE_SLACK for p in valid_ids}

    # ACTUAL tracked coverage = detection count × sample interval. Both the
    # stitched span (end-start) and even per-fragment spans over-count badly:
    # BoT-SORT keeps a track id alive across ≤TRACK_BUFFER_S gaps, so one
    # "fragment" can span minutes while holding seconds of detections. Charging
    # span against the per-player minute budget exhausted every budget after a
    # couple of tracklets and dropped the rest to status=unknown (same trap
    # pipeline._build_tracklet_index documents). The budget still caps
    # pathological smear; it just charges real track-time now.
    _counts = tracks_df.groupby("track_id").size()
    _dts = (tracks_df.sort_values(["track_id", "time_s"])
            .groupby("track_id")["time_s"].diff().dropna())
    _dt_med = float(_dts[_dts > 0].median()) if len(_dts) else 0.1

    def _tl_minutes(members: list[int]) -> float:
        return sum(int(_counts.get(m, 0)) for m in members) * _dt_med / 60.0

    # Pre-compute each tracklet's candidate ranking + confidence.
    tl_rank: dict[int, dict] = {}
    for tl, members in tracklet_members.items():
        votes = match_count.get(tl, {})
        span = (min(lifetimes.get(m, (0, 0))[0] for m in members),
                max(lifetimes.get(m, (0, 0))[1] for m in members))
        votes = {p: v for p, v in votes.items()
                 if p in valid_ids and _is_onfield(onfield, p, span[0] - ONFIELD_TOLERANCE_S,
                                                    span[1] + ONFIELD_TOLERANCE_S)}
        if votes:
            ordered = sorted(votes.values(), reverse=True)
            total = sum(ordered)  # # of windows (≈WINDOW_S each) that matched this tracklet
            share = ordered[0] / max(total, 1.0)
            margin = (ordered[0] - ordered[1]) / ordered[0] if len(ordered) > 1 and ordered[0] > 0 else 1.0
            # Evidence floor: confidence must reflect HOW MUCH on-field positional
            # agreement accumulated, not just its cleanliness. A 1-window fluke
            # (share=margin=1) used to read 100%; now it saturates toward full
            # confidence only with sustained support across many windows. This kills
            # the "false 100%" on 1-second fragments and orders the greedy pass so
            # well-evidenced tracklets win budget slots first.
            evidence = 1.0 - np.exp(-total / float(config.ID_CONFIDENCE_EVIDENCE_VOTES))
            conf = float(min(1.0, evidence * (0.6 * share + 0.4 * margin)))
        else:
            conf = 0.0
        tl_rank[tl] = {
            "ranked": sorted(votes, key=votes.get, reverse=True),
            "conf": conf,
            "minutes": _tl_minutes(members),
            "span": span,
        }

    # --- greedy capacity assignment, highest-confidence tracklets first -------
    tracklet_assign: dict[int, tuple[Optional[str], float, str]] = {}
    assigned_min: dict[str, float] = {}

    # 0. Coach overrides win — force first so they consume budget before auto,
    #    and exclude them from the keeper + greedy passes.
    # Labelled non-player sentinels written by the PWA picker. All drop the
    # tracklet, but we keep the reason in the status so it can feed a future
    # "learn the referee/opponent appearance" step.
    _drop_status = {"__ref__": "coach_ref", "__opp__": "coach_opp", "__other__": "coach_other"}
    forced: set[int] = set()
    for tl, pid in ov.items():
        if tl not in tracklet_members:
            continue  # stale override (tracklet ids change on a full re-track)
        forced.add(tl)
        if pid and pid in valid_ids:
            tracklet_assign[tl] = (pid, 1.0, "coach")
            assigned_min[pid] = assigned_min.get(pid, 0.0) + tl_rank.get(tl, {}).get("minutes", 0.0)
        else:
            tracklet_assign[tl] = (None, 1.0, _drop_status.get(pid, "coach_drop"))
    if forced:
        log.info("  identity: applied %d coach override(s)", len(forced))

    # 1. Keeper tracklets → their window's keeper unconditionally (don't let
    #    the cap drop them). With mid-game GK rotations each keeper tracklet
    #    goes to whoever was actually in goal during its span.
    for tl in keeper_tracklets:
        if tl in tracklet_members and tl not in forced:
            kpid = keeper_assign.get(tl)
            if kpid and kpid in valid_ids:
                tracklet_assign[tl] = (kpid, 0.95, "auto")
                assigned_min[kpid] = assigned_min.get(kpid, 0.0) + tl_rank.get(tl, {}).get("minutes", 0.0)

    # 2. Everyone else by descending confidence, respecting per-player budgets.
    remaining = [tl for tl in tracklet_members
                 if tl not in keeper_tracklets and tl not in forced]
    for tl in sorted(remaining, key=lambda t: tl_rank[t]["conf"], reverse=True):
        info = tl_rank[tl]
        conf, tl_min = info["conf"], info["minutes"]
        chosen = None
        for p in info["ranked"]:
            # While p is in goal they only get keeper tracklets — but they CAN
            # own outfield tracklets outside their GK windows (part-time
            # keepers play the rest of the game outfield).
            if _gk_overlap_frac(p, info["span"]) > 0.5:
                continue
            if assigned_min.get(p, 0.0) + tl_min <= budget.get(p, 1e9):
                chosen = p
                break
        if chosen is None:
            tracklet_assign[tl] = (None, conf, "unknown")  # over budget / no candidate → drop
            continue
        assigned_min[chosen] = assigned_min.get(chosen, 0.0) + tl_min
        # RECALL: assign the best-guess player down to a low floor (not just the
        # REVIEW tier), so a player's distance/heatmap aren't starved when most of
        # their play is low-confidence. The minute budget still caps over-assignment,
        # and the confidence stays honest (shown low). Only truly weak (< STATS_MIN)
        # tracklets are dropped. 'lowconf' tracklets surface in FIX IDS "All segments".
        if conf >= config.ID_CONFIDENCE_AUTO:
            status = "auto"
        elif conf >= config.ID_CONFIDENCE_REVIEW:
            status = "review"
        elif conf >= config.ID_CONFIDENCE_STATS_MIN:
            status = "lowconf"
        else:
            status = "unknown"
        tracklet_assign[tl] = (chosen if status != "unknown" else None, conf, status)

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
