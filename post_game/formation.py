"""Team formation, compactness, and width over time."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd


@dataclass
class FormationSnapshot:
    period: int
    label: str
    avg_positions: dict[str, tuple[float, float]]
    # Source of `label`: "coach" (reset boards / last dragged board, see
    # _coach_formation_reference), "coach-carryover" (no board activity this
    # period — inherited), or "tracks" (no coach board anywhere).
    label_source: str = "tracks"
    # Per-player coach-board positions in normalized half-field coords
    # (x∈[0,1] left→right from coach POV, y∈[0,1] halfway→own goal). Empty
    # dict when no coach POSITION events were available.
    coach_positions_norm: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass
class TeamTimeSeries:
    times_s: list[float]
    compactness_m: list[float]
    width_m: list[float]
    depth_m: list[float]
    centroid_x_m: list[float]
    # How many DISTINCT identified players the sample was computed from.
    # Load-bearing, not diagnostic: see the coverage-bias note in
    # compute_formation. Consumers MUST NOT plot width/depth without it.
    n_players: list[int] = field(default_factory=list)


# --- coverage bias on shape metrics --------------------------------------
#
# Measured on the 2026-08-09 game (428 windows with >=5 identified players,
# subsampled): with only 3 of 6 outfielders tracked, the max-min WIDTH reads
# 28% LOW and DEPTH 24% LOW; at 4 players, 13% and 11% low. Because coverage
# flickers second-to-second (that game: 1-2 players 36% of windows, 3-4 49%,
# 5+ 15%, six or more just 1%), the bias swings frame to frame — which is what
# made the PWA sparklines look like violent shape changes when the team was
# doing nothing unusual.
#
# So a plain "require >= 6 players" gate is NOT the fix here: it would delete
# 99% of the samples on real data. Instead:
#   * MIN_PLAYERS_SHAPE gates out the worst (n<4) samples;
#   * extents are reported as the EXPECTED-RANGE-corrected value, scaling the
#     observed max-min by the expected fraction of the full range that n of
#     N_OUTFIELD uniformly-drawn samples span ((n-1)/(n+1) vs (N-1)/(N+1));
#   * n_players rides along so consumers can weight, band, or grey out
#     low-coverage stretches rather than reading them as tactics.
MIN_PLAYERS_SHAPE = 4
N_OUTFIELD = 6           # 7v7 minus the keeper


def _extent_correction(n: int, n_full: int = N_OUTFIELD) -> float:
    """Multiplier putting an n-player max-min extent on an n_full-player scale.

    For n samples from a distribution, the expected span is a known fraction
    of the population range — (n-1)/(n+1) for the uniform case, which is the
    honest first-order model for players spread across a pitch axis. The ratio
    of the two expectations is the correction. Exactly 1.0 at n == n_full, so
    a fully-tracked window is untouched.
    """
    if n < 2:
        return 1.0
    obs = (n - 1) / (n + 1)
    full = (n_full - 1) / (n_full + 1)
    return float(full / obs) if obs > 0 else 1.0


# Outfield counts for which a FOUR-row shape is worth considering: EXACTLY 8,
# i.e. a real 9v9 board (9 on field, keeper excluded), where 3-2-2-1 and 2-3-2-1
# are ordinary shapes. 7v7's 6 outfielders are never four banks.
#
# Deliberately an exact match rather than ">= 8": a board carrying MORE than 8
# outfielders is not a bigger formation, it's a corrupt board — halftime-welded
# tracklets put 10 players on one board in the real data, and treating those as
# a four-row shape just relabels a known data bug. Measured: with ">= 8", two
# real 7v7 boards (8 and 10 outfielders) changed label.
FOUR_ROW_OUTFIELD = {8}
# A 4th row always fits at least as well as 3 (more free parameters), so it must
# pay for itself by a wide margin. Measured on synthetic boards: a GENUINE
# 3-2-2-1 cuts within-row variance to 0.026 of the 3-row fit, while a flat
# 8-player line reaches 0.44 and a true 3-3-2 reaches 0.67. Anything above this
# cut is a 3-row shape being over-fitted.
FOUR_ROW_MAX_COST_RATIO = 0.10


def _label_formation_outfield(xs: np.ndarray) -> str:
    """Row-count label ("2-3-1", or "3-2-2-1" at 9v9) from 1-D depth values,
    defense row first.

    Rows are contiguous in depth, so the optimal split for a given row count is
    found exactly by trying every combination of split points (n is tiny) and
    keeping the minimum within-row variance — deterministic, unlike the KMeans
    it replaced. Three rows is the default; four is considered only when there
    are enough outfielders for it to mean anything AND it fits materially
    better (see FOUR_ROW_MIN_GAIN), because more rows can never fit worse.
    """
    n = len(xs)
    if n == 0:
        return "?"
    if n < 4:
        return f"({n} outfield)"
    v = np.sort(np.asarray(xs, dtype=float))

    def _var_sum(a: np.ndarray) -> float:
        return float(((a - a.mean()) ** 2).sum()) if len(a) else 0.0

    best: tuple[float, tuple[int, int, int]] | None = None
    for i in range(1, n - 1):
        for j in range(i + 1, n):
            cost = _var_sum(v[:i]) + _var_sum(v[i:j]) + _var_sum(v[j:])
            if best is None or cost < best[0]:
                best = (cost, (i, j - i, n - j))

    if n in FOUR_ROW_OUTFIELD:
        best4: tuple[float, tuple[int, int, int, int]] | None = None
        for i in range(1, n - 2):
            for j in range(i + 1, n - 1):
                for k in range(j + 1, n):
                    cost = (_var_sum(v[:i]) + _var_sum(v[i:j])
                            + _var_sum(v[j:k]) + _var_sum(v[k:]))
                    if best4 is None or cost < best4[0]:
                        best4 = (cost, (i, j - i, k - j, n - k))
        if best4 is not None and best4[0] <= best[0] * FOUR_ROW_MAX_COST_RATIO:
            return "-".join(str(c) for c in best4[1])

    return "-".join(str(c) for c in best[1])


# Formation per period — COACH'S RULE (2026-06-11): RESET batches are THE
# reference. One reset → that board; several → every reset board votes
# (majority shape, earliest reset breaks ties); none → the dragged board at
# the period's last drag instant. Resets are slot-snapped board writes (≥
# FORMATION_MIN_BATCH near-simultaneous POSITION events), so raw drag coords
# never dilute them. Boards are always read at a SINGLE instant — mixing
# drags from different moments was the original mislabeling (2-3-1 all game
# read as 2-2-2 / 1-3-1). A period with no board activity inherits the
# previous period's label.
# MIRROR: coachKickoffFormation/boardLabelAt in soccer_team_app.jsx.
FORMATION_MIN_BATCH = 4          # near-simultaneous events = a board write
FORMATION_MIN_OUTFIELD = 4       # min on-field outfield positions to label


def _valid_position_events(coach_events: Iterable[Any]) -> list[tuple[int, int, str, float, float, float]]:
    """(at, period, pid, elapsed, x, y) for every well-formed POSITION event,
    sorted by wall-clock `at`."""
    out = []
    for e in coach_events or []:
        if getattr(e, "type", None) != "POSITION":
            continue
        pid = getattr(e, "player_id", None)
        if not pid:
            continue
        extras = getattr(e, "extras", {}) or {}
        x = extras.get("x")
        y = extras.get("y")
        if x is None or y is None:
            continue
        try:
            x = float(x); y = float(y)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            continue
        try:
            elapsed = float(getattr(e, "elapsed", 0) or 0)
        except (TypeError, ValueError):
            elapsed = 0.0
        out.append((int(getattr(e, "at", 0) or 0), int(getattr(e, "period", 0) or 0), pid, elapsed, x, y))
    out.sort(key=lambda r: r[0])
    return out


def _board_at(
    coach_events: Iterable[Any],
    t_wall_ms: int,
    starting_lineup: list[str],
    gk_player_id: Optional[str],
) -> dict[str, tuple[float, float]]:
    """Board state at wall-clock instant T: latest drag per player ≤ T (any
    period — the board persists), restricted to who was ON FIELD at T
    (lineup + subs ≤ T), GK at T excluded. Returns {pid: (x, y)} normalized."""
    on = set(starting_lineup or [])
    gk = gk_player_id
    rows = []
    for e in coach_events or []:
        try:
            at = int(getattr(e, "at", 0) or 0)
        except (TypeError, ValueError):
            at = 0
        if at <= t_wall_ms:
            rows.append((at, e))
    rows.sort(key=lambda r: r[0])
    pos: dict[str, tuple[float, float]] = {}
    for _at, e in rows:
        et = (getattr(e, "type", None) or "").upper()
        if et == "SUB":
            if getattr(e, "player_id", None):
                on.discard(e.player_id)
            son = (getattr(e, "extras", {}) or {}).get("subOnPlayerId")
            if son:
                on.add(son)
        elif et == "GK_CHANGE" and getattr(e, "player_id", None):
            gk = e.player_id
        elif et == "POSITION":
            pid = getattr(e, "player_id", None)
            extras = getattr(e, "extras", {}) or {}
            x, y = extras.get("x"), extras.get("y")
            if not pid or x is None or y is None:
                continue
            try:
                x = float(x); y = float(y)
            except (TypeError, ValueError):
                continue
            if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                pos[pid] = (x, y)
    return {pid: xy for pid, xy in pos.items() if pid in on and pid != gk}


def _coach_formation_reference(
    coach_events: Iterable[Any],
    period_index_1based: int,
    starting_lineup: list[str],
    gk_player_id: Optional[str],
) -> tuple[Optional[str], dict[str, tuple[float, float]]]:
    """(label, reference board positions) for one period under the coach's
    rule (see module comment above the FORMATION_* constants), or (None, {})
    when the period has no usable board."""
    evs = [r for r in _valid_position_events(coach_events) if r[1] == period_index_1based]
    # RESET/kickoff batches: runs of near-simultaneous POSITION events.
    batch_ends: list[int] = []
    run: list[tuple] = []
    for r in evs:
        if run and r[0] - run[-1][0] <= 2:
            run.append(r)
        else:
            run = [r]
        if len(run) == FORMATION_MIN_BATCH:
            batch_ends.append(run[-1][0])
        elif len(run) > FORMATION_MIN_BATCH:
            batch_ends[-1] = run[-1][0]
    if batch_ends:
        labeled = []
        for t in batch_ends:
            board = _board_at(coach_events, t, starting_lineup, gk_player_id)
            if len(board) >= FORMATION_MIN_OUTFIELD:
                depths = np.array([1.0 - xy[1] for xy in board.values()])
                labeled.append((_label_formation_outfield(depths), board))
        if labeled:
            counts: dict[str, int] = {}
            best = None
            for lbl, board in labeled:
                counts[lbl] = counts.get(lbl, 0) + 1
                if best is None or counts[lbl] > counts[best[0]]:
                    best = (lbl, board)  # ties keep the EARLIEST reset
            return best
    if evs:
        board = _board_at(coach_events, evs[-1][0], starting_lineup, gk_player_id)
        if len(board) >= FORMATION_MIN_OUTFIELD:
            depths = np.array([1.0 - xy[1] for xy in board.values()])
            return _label_formation_outfield(depths), board
    return None, {}


def compute_formation(
    tracks_field_df: pd.DataFrame,
    identity_by_track: dict[int, str],
    team_of_player: dict[str, int],
    periods: list[tuple[float, float]],
    gk_player_id: Optional[str] = None,
    coach_events: Optional[Iterable[Any]] = None,
    starting_lineup: Optional[list[str]] = None,
) -> tuple[list[FormationSnapshot], TeamTimeSeries]:
    df = tracks_field_df.copy()
    df["player_id"] = df["track_id"].map(identity_by_track)
    df = df[df["player_id"].notna()]
    df["team"] = df["player_id"].map(lambda p: team_of_player.get(p, -1))
    snaps: list[FormationSnapshot] = []

    for i, (start_s, end_s) in enumerate(periods):
        sub = df[(df["time_s"] >= start_s) & (df["time_s"] <= end_s) & (df["team"] == 0)]
        if sub.empty:
            positions: dict[str, tuple[float, float]] = {}
        else:
            avg = (
                sub.groupby("player_id")[["x_m", "y_m"]].median().to_dict(orient="index")
            )
            positions = {str(pid): (float(v["x_m"]), float(v["y_m"])) for pid, v in avg.items()}

        # Coach POSITION events (ground truth): label per the coach's rule —
        # reset boards vote, else the last dragged board, always read at a
        # single instant (see _coach_formation_reference).
        coach_label, coach_norm = _coach_formation_reference(
            coach_events or [], i + 1, starting_lineup or [], gk_player_id)
        prev_coach_label = next(
            (s.label for s in reversed(snaps) if s.label_source.startswith("coach")), None)
        if coach_label is not None:
            label = coach_label
            label_source = "coach"
        elif prev_coach_label is not None:
            # No board activity this period → the shape carried over.
            label = prev_coach_label
            label_source = "coach-carryover"
        else:
            outfield_xs = np.array([
                xy[0] for pid, xy in positions.items() if pid != gk_player_id
            ])
            label = _label_formation_outfield(outfield_xs)
            label_source = "tracks"

        if not positions and not coach_norm:
            continue

        snaps.append(FormationSnapshot(
            period=i + 1,
            label=label,
            avg_positions=positions,
            label_source=label_source,
            coach_positions_norm={pid: (float(x), float(y)) for pid, (x, y) in coach_norm.items()},
        ))

    our = df[df["team"] == 0]
    if our.empty:
        ts = TeamTimeSeries([], [], [], [], [], [])
    else:
        t0 = float(our["time_s"].min())
        t1 = float(our["time_s"].max())
        times, comp, width, depth, cx, npl = [], [], [], [], [], []
        cur = t0
        while cur <= t1:
            window = our[(our["time_s"] >= cur) & (our["time_s"] < cur + 1.0)]
            n = int(window["player_id"].nunique()) if not window.empty else 0
            if n >= MIN_PLAYERS_SHAPE:
                xs = window.groupby("player_id")["x_m"].mean().to_numpy()
                ys = window.groupby("player_id")["y_m"].mean().to_numpy()
                pairwise = np.sqrt(
                    (xs[:, None] - xs[None, :]) ** 2 + (ys[:, None] - ys[None, :]) ** 2
                )
                # Mean pairwise distance is an AVERAGE over pairs, so missing
                # players cost precision but not much bias — reported as-is.
                comp.append(float(pairwise[np.triu_indices_from(pairwise, k=1)].mean()))
                # Extents are max-min, which shrinks systematically with fewer
                # samples — corrected onto the full-team scale (see
                # _extent_correction) so a coverage dip is not read as the team
                # suddenly getting narrow.
                k = _extent_correction(n)
                width.append(float((ys.max() - ys.min()) * k))
                depth.append(float((xs.max() - xs.min()) * k))
                cx.append(float(xs.mean()))
                times.append(cur)
                npl.append(n)
            cur += 1.0
        ts = TeamTimeSeries(times, comp, width, depth, cx, npl)
    return snaps, ts
