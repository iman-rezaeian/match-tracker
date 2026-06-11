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


def _label_formation_outfield(xs: np.ndarray) -> str:
    """Row-count label ("2-3-1") from 1-D depth values, defense row first.

    Rows are contiguous in depth, so the optimal 3-way split is found exactly
    by trying every pair of split points (n is tiny) and keeping the minimum
    within-row variance — deterministic, unlike the KMeans it replaced.
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
        ts = TeamTimeSeries([], [], [], [], [])
    else:
        t0 = float(our["time_s"].min())
        t1 = float(our["time_s"].max())
        times, comp, width, depth, cx = [], [], [], [], []
        cur = t0
        while cur <= t1:
            window = our[(our["time_s"] >= cur) & (our["time_s"] < cur + 1.0)]
            if not window.empty and window["player_id"].nunique() >= 3:
                xs = window.groupby("player_id")["x_m"].mean().to_numpy()
                ys = window.groupby("player_id")["y_m"].mean().to_numpy()
                pairwise = np.sqrt(
                    (xs[:, None] - xs[None, :]) ** 2 + (ys[:, None] - ys[None, :]) ** 2
                )
                comp.append(float(pairwise[np.triu_indices_from(pairwise, k=1)].mean()))
                width.append(float(ys.max() - ys.min()))
                depth.append(float(xs.max() - xs.min()))
                cx.append(float(xs.mean()))
                times.append(cur)
            cur += 1.0
        ts = TeamTimeSeries(times, comp, width, depth, cx)
    return snaps, ts
